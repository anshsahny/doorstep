"""Lighthouse accessibility scores for the deployed dashboard, one line per page (Gate 5 >= 90).

Runs the project's own Lighthouse (web/node_modules, Node 22) against headless Chrome, desktop
and mobile form factors. Pages with drill data use the recorded drill, so this costs $0.

    make lighthouse                 # the deployed site
    make lighthouse ARGS="--url http://localhost:4173"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = [
    "/",
    "/board?recorded=1",
    "/decisions?recorded=1",
    "/policies?recorded=1",
    "/report?recorded=1",
    "/evidence",
    "/captain",
]
NODE = "/opt/homebrew/opt/node@22/bin"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url")
    parser.add_argument("--min", type=int, default=90)
    args = parser.parse_args()
    base = (
        args.url or json.loads((ROOT / "cdk.out/outputs.json").read_text())["Doorstep"]["SiteUrl"]
    )
    env = {**os.environ, "PATH": f"{NODE}:{os.environ['PATH']}"}
    lighthouse = ROOT / "web" / "node_modules" / ".bin" / "lighthouse"
    worst = 100
    with tempfile.TemporaryDirectory() as tmp:
        for form in ("desktop", "mobile"):
            for page in PAGES:
                out = Path(tmp) / "report.json"
                cmd = [
                    str(lighthouse), base.rstrip("/") + page,
                    "--only-categories=accessibility", "--output=json", f"--output-path={out}",
                    "--quiet", "--chrome-flags=--headless=new --no-sandbox",
                ]  # fmt: skip
                if form == "desktop":
                    cmd.append("--preset=desktop")
                subprocess.run(cmd, env=env, check=True, capture_output=True)
                report = json.loads(out.read_text())
                score = round(report["categories"]["accessibility"]["score"] * 100)
                worst = min(worst, score)
                failed = [
                    a["id"]
                    for a in report["audits"].values()
                    if a.get("score") == 0 and a.get("scoreDisplayMode") == "binary"
                ]
                note = f"failing: {', '.join(failed)}" if failed else ""
                print(f"{form:7} {score:3}  {page}  {note}")
    print(f"\nlowest accessibility score: {worst} (gate: >= {args.min})")
    return 0 if worst >= args.min else 1


if __name__ == "__main__":
    sys.exit(main())
