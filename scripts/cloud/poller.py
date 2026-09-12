"""Turn the 10-minute NWS alert schedule on or off without a deploy (make poller ARGS=on|off)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.cloud.stack import session  # noqa: E402

NAME = "doorstep-alert-poller"
KEEP = ("Name", "GroupName", "ScheduleExpression", "FlexibleTimeWindow", "Target", "Description")


def main(action: str) -> int:
    scheduler = session().client("scheduler")
    current = scheduler.get_schedule(Name=NAME)
    if action in ("on", "off"):
        wanted = "ENABLED" if action == "on" else "DISABLED"
        if current["State"] != wanted:
            scheduler.update_schedule(**{k: current[k] for k in KEEP if k in current}, State=wanted)
        current = scheduler.get_schedule(Name=NAME)
    print(f"{NAME}: {current['State']} ({current['ScheduleExpression']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "status"))
