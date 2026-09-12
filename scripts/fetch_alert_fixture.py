"""Build a replay fixture from a real archived NWS product in the IEM VTEC archive.

Usage:
  uv run python scripts/fetch_alert_fixture.py               # fetch June 2021 PQR EH.W.0001
  uv run python scripts/fetch_alert_fixture.py --from-file x # convert a downloaded IEM JSON

The fixture mimics the NWS alerts API shape (`features[0].properties`) so the same code path can
consume live `/alerts/active` results in Phase 3. The product text is public-domain NWS output;
the CAP severity/urgency/certainty values are the standard ones NWS uses for this product type
because the IEM archive stores VTEC products, not CAP.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "alerts" / "2021-06-pqr-excessive-heat-warning.json"
IEM_JSON = (
    "https://mesonet.agron.iastate.edu/json/vtec_event.py"
    "?wfo=PQR&year=2021&phenomena=EH&significance=W&etn=1"
)
IEM_PAGE = "https://mesonet.agron.iastate.edu/vtec/?year=2021&wfo=KPQR&phenomena=EH&significance=W&eventid=0001"
USER_AGENT = "(doorstep-demo, fixture builder)"

# Standard NWS CAP values for an Excessive Heat Warning (pre-2025 name; VTEC EH.W).
CAP = {
    "event": "Excessive Heat Warning",
    "severity": "Severe",
    "urgency": "Expected",
    "certainty": "Likely",
}


def _vtec_time(s: str) -> str:
    """'210626T1700Z' -> '2021-06-26T17:00:00Z'."""
    return datetime.strptime(s, "%y%m%dT%H%MZ").strftime("%Y-%m-%dT%H:%M:%SZ")


def convert(iem: dict) -> dict:
    text: str = iem["report"]["text"]
    vtec_match = re.search(r"/O\.NEW\.(K\w{3})\.(\w{2})\.(\w)\.(\d{4})\.(\w+)-(\w+)/", text)
    if not vtec_match:
        raise SystemExit("no O.NEW VTEC line found in the product text")
    office, phen, sig, etn, start, end = vtec_match.groups()
    vtec = vtec_match.group(0)

    headline_match = re.search(r"\.\.\.(EXCESSIVE HEAT WARNING[^.]*?)\.\.\.", text, re.S)
    headline = " ".join(headline_match.group(1).split()) if headline_match else CAP["event"]

    # The bullet body from "* WHAT..." to the end of the segment ("$$").
    body_match = re.search(r"(\* WHAT\.\.\..*?)(?:\n\$\$|\Z)", text, re.S)
    description = body_match.group(1).strip() if body_match else text.strip()

    ugcs = iem.get("ugcs") or []
    ugc_codes = sorted({u["ugc"] for u in ugcs if u.get("ugc")})
    area_desc = "; ".join(sorted({u["name"] for u in ugcs if u.get("name")}))

    product_issue = (iem.get("ugcs") or [{}])[0].get("utc_product_issue") or ""

    return {
        "_note": (
            "REAL archived NWS product text (public domain) used as a replay fixture. "
            "Nothing in this file is fictional except the Doorstep org that replays it."
        ),
        "source": {
            "provider": "Iowa Environmental Mesonet (IEM) NWS VTEC archive",
            "json_url": IEM_JSON,
            "archive_page": IEM_PAGE,
            "product_id": iem["report"].get("product_id"),
            "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cap_fields_note": (
                "severity/urgency/certainty are the standard NWS CAP values for this product type; "
                "the IEM archive stores the VTEC product, not the CAP message"
            ),
        },
        "vtec": vtec,
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": f"IEM-{start[:2]}-{office}-{phen}-{sig}-{etn}",
                    "event": CAP["event"],
                    "severity": CAP["severity"],
                    "urgency": CAP["urgency"],
                    "certainty": CAP["certainty"],
                    "status": "Actual",
                    "messageType": "Alert",
                    "category": "Met",
                    "senderName": "NWS Portland OR",
                    "sent": product_issue,
                    "effective": product_issue,
                    "onset": _vtec_time(start),
                    "expires": _vtec_time(end),
                    "ends": _vtec_time(end),
                    "headline": headline,
                    "description": description,
                    "areaDesc": area_desc,
                    "geocode": {"UGC": ugc_codes},
                    "parameters": {"VTEC": [vtec], "NWSheadline": [headline]},
                    "rawProductText": text,
                },
            }
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--from-file", help="convert a previously downloaded IEM JSON file instead of fetching"
    )
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    if args.from_file:
        iem = json.loads(Path(args.from_file).read_text())
    else:
        req = Request(IEM_JSON, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed https URL
            iem = json.load(resp)
    if not iem.get("event_exists"):
        sys.exit("IEM says the event does not exist")

    fixture = convert(iem)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, indent=2) + "\n")
    props = fixture["features"][0]["properties"]
    print(
        f"wrote {out.relative_to(ROOT)}: {props['event']} | {fixture['vtec']} | "
        f"onset {props['onset']}"
    )
    print(f"headline: {props['headline']}")


if __name__ == "__main__":
    main()
