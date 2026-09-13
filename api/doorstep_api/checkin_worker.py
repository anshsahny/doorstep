"""checkin_worker (SQS `checkin-jobs`): dial one permitted check-in call on the Twilio subaccount.

The coordinator's `place_checkin_call` has already been allowed by Cedar and by its own code
check. This function trusts none of that, because it is the last step before a real phone rings.
For every job, in order:

1. kill switch;
2. the incident is **live** (read from DynamoDB, never from the message) and covers the resident;
3. the resident consents to calls and has a phone reference of the one supported kind;
4. the number, resolved from SSM and normalized to E.164, is **exactly** on the SSM allowlist;
5. the credentials belong to a Twilio **subaccount**, never the parent account;
6. the job is claimed once, so a redelivered message never rings a phone twice;

then a 2-minute single-use voice token is minted and the call is created with inline TwiML that
streams the audio to the voice bridge. A job that fails any check is logged and dropped: SQS
never retries a real call (the queue's redrive goes straight to the dead-letter queue).
"""

from __future__ import annotations

import json
import re
from typing import Any
from xml.sax.saxutils import quoteattr

from doorstep_voice.tokens import mint

from . import common
from .common import DEPS, INCIDENT_ID, log, normalize_number
from .twilio_rest import TwilioError, TwilioRest

RESIDENT_ID = re.compile(r"^r[0-9]{2}$")
PHONE_REF = re.compile(r"^env:CALL_ALLOWLIST\[(\d+)\]$")
TOKEN_TTL_SECONDS = 120
CALL_TIME_LIMIT_SECONDS = 300
RING_TIMEOUT_SECONDS = 30


class Refused(Exception):
    """A job this function will not dial. The message is safe to log (no numbers)."""


def _doc(deps: common.Deps, pk: str, sk: str) -> dict[str, Any] | None:
    item = deps.dynamodb.get_item(
        TableName=deps.table, Key={"PK": {"S": pk}, "SK": {"S": sk}}, ConsistentRead=True
    ).get("Item")
    return json.loads(item["doc"]["S"]) if item and "doc" in item else None


def twiml_for(bridge_url: str, token: str) -> str:
    """Connect the call's audio to the bridge; when the bridge closes the stream, hang up."""
    return (
        "<Response><Connect>"
        f"<Stream url={quoteattr(bridge_url)}>"
        f'<Parameter name="token" value={quoteattr(token)} />'
        "</Stream></Connect><Hangup/></Response>"
    )


def check_job(deps: common.Deps, job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The number to dial and the incident, or `Refused`."""
    if deps.kill_switch():
        raise Refused("kill switch is on")
    incident_id = str(job.get("incident_id") or "")
    resident_id = str(job.get("resident_id") or "")
    if not INCIDENT_ID.match(incident_id) or not RESIDENT_ID.match(resident_id):
        raise Refused("malformed job")
    incident = _doc(deps, f"INC#{incident_id}", "META")
    if incident is None:
        raise Refused("no such incident")
    if incident.get("mode") != "live":
        raise Refused(f"incident mode is {incident.get('mode')!r}; real calls are live only")
    if resident_id not in (incident.get("resident_ids") or []):
        raise Refused("resident is not in this incident")
    resident = _doc(deps, f"ORG#{incident.get('org_id')}", f"RES#{resident_id}")
    if resident is None:
        raise Refused("unknown resident")
    if not (resident.get("consent") or {}).get("calls"):
        raise Refused("resident has not consented to calls")
    ref = PHONE_REF.match(str(resident.get("phone_ref") or ""))
    if ref is None:
        raise Refused("resident has no supported phone reference")
    listed = [
        n for n in (normalize_number(x) for x in (deps.param("call_allowlist") or "").split(","))
    ]
    index = int(ref.group(1))
    number = listed[index] if index < len(listed) else None
    allowlist = {n for n in listed if n}
    if not number or number not in allowlist:
        raise Refused("callee is not on the call allowlist")
    return number, incident


def dial(
    deps: common.Deps, job: dict[str, Any], twilio: TwilioRest | None = None
) -> dict[str, Any]:
    try:
        number, incident = check_job(deps, job)
        sid = deps.param("twilio/subaccount_sid") or ""
        token = deps.param("twilio/subaccount_token") or ""
        from_number = normalize_number(deps.param("twilio/from_number"))
        bridge = deps.param("voice_bridge_url") or ""
        secret = deps.param("internal_hmac_secret") or ""
        if not (sid and token and from_number and secret):
            raise Refused("Twilio or token configuration is missing")
        if not bridge.startswith("wss://") or "?" in bridge:
            raise Refused("voice bridge URL must be wss:// with no query string")
        client = twilio or TwilioRest(sid, token)
        account = client.account()
        owner = account.get("owner_account_sid")
        if not owner or owner == account.get("sid") or account.get("sid") != sid:
            raise Refused("credentials are not a Twilio subaccount; refusing to dial")
        key = f"DIALED#{job['incident_id']}#{job['resident_id']}#{int(job.get('attempt') or 0)}"
        if not deps.claim(key):
            raise Refused("this call was already dialled")
        voice_token, claims = mint(
            secret,
            incident_id=job["incident_id"],
            resident_id=job["resident_id"],
            channel="phone",
            mode="live",
            ttl_seconds=TOKEN_TTL_SECONDS,
        )
        call = client.create_call(
            to=number,
            from_=from_number,
            twiml=twiml_for(bridge, voice_token),
            time_limit=CALL_TIME_LIMIT_SECONDS,
            ring_timeout=RING_TIMEOUT_SECONDS,
        )
        log(
            msg="call placed",
            incident=job["incident_id"],
            resident=job["resident_id"],
            call_sid=call.get("sid"),
            token_id=claims["jti"][:6],
        )
        return {"dialled": True, "call_sid": call.get("sid")}
    except Refused as exc:
        log(msg="call refused", incident=job.get("incident_id"), reason=str(exc))
        return {"dialled": False, "reason": str(exc)}
    except TwilioError as exc:
        log(msg="call failed", incident=job.get("incident_id"), error=str(exc))
        return {"dialled": False, "reason": str(exc)}


def handler(
    event: dict[str, Any], context: Any = None, deps: common.Deps | None = None, twilio: Any = None
) -> dict[str, Any]:
    deps = deps or DEPS
    results = []
    for record in event.get("Records") or []:
        try:
            job = json.loads(record.get("body") or "{}")
        except ValueError:
            job = {}
        results.append(dial(deps, job if isinstance(job, dict) else {}, twilio))
    # Never ask SQS to retry: a failed real call is logged and left for a human to re-issue.
    return {"batchItemFailures": [], "results": results}
