"""The only code in Doorstep that can make a phone ring: two Twilio REST calls, standard library.

`account` reads an account record (to prove the credentials are a subaccount's). `create_call`
places a call. Nothing else in the repository creates calls (a test searches for it), so the
checks in `checkin_worker` in front of `create_call` are the last word on who gets rung.

No Twilio SDK: the Lambda stays small, and there is exactly one request shape to audit.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://api.twilio.com/2010-04-01"


class TwilioError(RuntimeError):
    """A Twilio request failed. The message never contains credentials or phone numbers."""


class TwilioRest:
    def __init__(self, account_sid: str, auth_token: str, *, timeout: float = 10.0) -> None:
        self.account_sid = account_sid
        self._auth = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
        self.timeout = timeout

    def _request(self, method: str, path: str, form: dict[str, str] | None = None) -> Any:
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        request = urllib.request.Request(f"{API}{path}", data=data, method=method)
        request.add_header("Authorization", f"Basic {self._auth}")
        if data is not None:
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raise TwilioError(
                f"Twilio {method} {path.split('/')[-1]} failed: HTTP {exc.code}"
            ) from None
        except OSError as exc:
            raise TwilioError(f"Twilio {method} failed: {type(exc).__name__}") from None

    def account(self) -> dict[str, Any]:
        return self._request("GET", f"/Accounts/{self.account_sid}.json")

    def create_call(
        self, *, to: str, from_: str, twiml: str, time_limit: int, ring_timeout: int
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/Accounts/{self.account_sid}/Calls.json",
            {
                "To": to,
                "From": from_,
                "Twiml": twiml,
                "TimeLimit": str(time_limit),
                "Timeout": str(ring_timeout),
            },
        )
