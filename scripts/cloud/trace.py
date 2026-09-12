"""Print the AgentCore Observability spans for one incident (make trace ARGS=<incident id>).

Spans reach CloudWatch through Transaction Search, in the runtime's own log group (`spans`
stream). This reads that stream, keeps the spans whose `session.id` is the incident's runtime
session, and prints each trace as an indented tree of span
names and durations — the text copy of what the CloudWatch GenAI Observability console shows.
Spans take a few minutes to be indexed after a run.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from doorstep_agent.cloud.coordinator import runtime_session_id  # noqa: E402
from scripts.cloud.stack import outputs, session  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("incident_id")
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--limit", type=int, default=400)
    args = ap.parse_args()

    aws = session()
    out = outputs(aws)
    logs = aws.client("logs")
    sid = runtime_session_id(args.incident_id)
    group = f"/aws/bedrock-agentcore/runtimes/{out['RuntimeId']}-DEFAULT"
    start = int((time.time() - args.hours * 3600) * 1000)
    spans = []
    # Read the `spans` stream and filter on the span's own `session.id` attribute locally.
    for page in logs.get_paginator("filter_log_events").paginate(
        logGroupName=group, logStreamNames=["spans"], startTime=start
    ):
        for event in page["events"]:
            try:
                span = json.loads(event["message"])
            except ValueError:
                continue
            if (span.get("attributes") or {}).get("session.id") == sid:
                spans.append(span)
    groups = [group]
    if not spans:
        print(f"no spans yet for {sid} in {groups} (indexing takes a few minutes)")
        return 1

    by_trace: dict[str, list[dict]] = defaultdict(list)
    for s in spans:
        by_trace[s["traceId"]].append(s)
    print(f"incident {args.incident_id} | {sid} | {len(spans)} spans, {len(by_trace)} traces")
    for trace_id, items in by_trace.items():
        children: dict[str | None, list[dict]] = defaultdict(list)
        ids = {s["spanId"] for s in items}
        for s in items:
            parent = s.get("parentSpanId")
            children[parent if parent in ids else None].append(s)

        def show(span: dict, depth: int, children=children) -> None:
            ms = (span.get("durationNano") or 0) / 1e6
            attrs = span.get("attributes") or {}
            detail = attrs.get("gen_ai.tool.name") or attrs.get("gen_ai.request.model") or ""
            print(f"  {'  ' * depth}{span.get('name')} {detail} ({ms:.0f} ms)")
            for child in sorted(
                children[span["spanId"]], key=lambda c: c.get("startTimeUnixNano", 0)
            ):
                show(child, depth + 1, children)

        print(f"trace {trace_id}")
        for root in sorted(children[None], key=lambda c: c.get("startTimeUnixNano", 0)):
            show(root, 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
