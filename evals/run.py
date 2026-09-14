"""Run the Doorstep eval suites and write the report (`make evals`).

    uv run python -m evals.run --suite all --label after
    uv run python -m evals.run --suite checkin --label before --only p02_walter_urgent_hidden
    uv run python -m evals.run --report          # rebuild REPORT.md from saved results, $0

Every suite runs against Bedrock (Nova 2 Lite agents, Nova Micro personas) from this machine, in
process, with an in-memory store: no call, message or deployed resource is touched. Task results
are cached under `evals/.cache/<suite>-<label>/`, so re-running a label re-scores for $0; delete
that directory to run the models again. Exit code 1 when a Gate 6 target is missed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time

from . import report
from .common import METER, write_result

SUITES = ("checkin", "dispatcher", "redteam", "backtest")


async def _run(suite: str, args: argparse.Namespace) -> dict:
    if suite == "checkin":
        from . import suite_checkin

        return await suite_checkin.run(
            args.label, urgent_trials=args.urgent_trials, only=args.only, workers=args.workers
        )
    if suite == "dispatcher":
        from . import suite_dispatcher

        return await suite_dispatcher.run(args.label, only=args.only)
    if suite == "redteam":
        from . import suite_redteam

        return await suite_redteam.run(args.label, only=args.only)
    from . import suite_backtest

    return await suite_backtest.run(args.label)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--suite", choices=(*SUITES, "all"), default="all")
    parser.add_argument("--label", default="after", help="results name, e.g. before / after")
    parser.add_argument("--only", nargs="*", help="persona or scenario ids to run")
    parser.add_argument("--urgent-trials", type=int, default=3)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--report", action="store_true", help="only rebuild REPORT.md")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    for noisy in ("strands", "botocore", "strands_evals", "httpx"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    if not args.report:
        METER.install()
        for suite in SUITES if args.suite == "all" else (args.suite,):
            before = METER.usd()
            t0 = time.monotonic()
            print(f"== {suite} ({args.label}) ...", flush=True)
            result = asyncio.run(_run(suite, args))
            result["wall_seconds"] = round(time.monotonic() - t0, 1)
            result["cost_usd"] = round(METER.usd() - before, 4)
            result["tokens"] = METER.snapshot()
            name = f"{suite}-{args.label}" + ("-partial" if args.only else "")
            path = write_result(name, result)
            print(f"   wrote {path} in {result['wall_seconds']} s, ~${result['cost_usd']}")
            print(report.summary_line(result))
    return report.build()


if __name__ == "__main__":
    raise SystemExit(main())
