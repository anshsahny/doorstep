"""Every 10 minutes: ask the NWS which alerts cover the organisation's location.

Each alert is recorded once (claimed by id). In `observe` mode — the default — that is all: a
real alert must not start an incident about a fictional roster and page a real phone. In `drill`
mode a new alert is forwarded to a fresh coordinator session, whose hazard profile decides,
before any model is asked, whether the alert is one it responds to.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from datetime import UTC, datetime
from typing import Any

from . import common
from .common import DEPS, log

NWS = "https://api.weather.gov/alerts/active?point={lat},{lng}"


def fetch_alerts(user_agent: str, lat: str, lng: str) -> list[dict[str, Any]]:
    request = urllib.request.Request(
        NWS.format(lat=lat, lng=lng),
        headers={"User-Agent": user_agent, "Accept": "application/geo+json"},
    )
    with urllib.request.urlopen(request, timeout=10) as reply:  # noqa: S310 - fixed https host
        return list(json.loads(reply.read()).get("features") or [])


def handler(
    event: dict[str, Any] | None = None,
    context: Any = None,
    deps: common.Deps | None = None,
    fetch=fetch_alerts,
) -> dict[str, Any]:
    deps = deps or DEPS
    if deps.kill_switch():
        log(msg="poll skipped: kill switch on")
        return {"skipped": "kill switch"}
    user_agent = deps.param("nws_user_agent")
    if not user_agent:
        log(level="warning", msg="poll skipped: no NWS user agent in SSM")
        return {"skipped": "no user agent"}
    mode = (deps.param("poller_mode") or "observe").strip().lower()
    features = fetch(user_agent, os.environ["ORG_LAT"], os.environ["ORG_LNG"])

    new, forwarded = 0, 0
    for feature in features:
        props = feature.get("properties") or {}
        alert_id = str(props.get("id") or feature.get("id") or "")
        if not alert_id:
            continue
        digest = hashlib.sha256(alert_id.encode()).hexdigest()[:24]
        if not deps.claim(f"ALERT#{digest}"):
            continue
        new += 1
        log(msg="new alert", event=props.get("event"), severity=props.get("severity"), mode=mode)
        if mode == "drill":
            incident_id = f"alert-{datetime.now(UTC):%Y%m%d-%H%M%S}-{digest[:4]}"
            deps.invoke(incident_id, {"type": "alert", "feature": feature})
            forwarded += 1
    summary = {"alerts": len(features), "new": new, "forwarded": forwarded, "mode": mode}
    log(msg="poll done", **summary)
    return summary
