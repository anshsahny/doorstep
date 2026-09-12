"""Spike S1 (Phase 3): the AgentCore Runtime entrypoint contract, locally.

Proves: an async entrypoint can start background work and return at once; /ping reports
HealthyBusy while that work runs and Healthy after; a second invocation is served while the
first task is still running; the session id arrives from the runtime header.

    uv run python scripts/spikes/p3_s1_async_entrypoint.py
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.request

from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()
PORT = 8765
state: dict[str, object] = {"done": False}


@app.entrypoint
async def invoke(payload, context):
    if payload.get("kind") == "long":
        task_id = app.add_async_task("spike-long")

        async def work() -> None:
            try:
                await asyncio.sleep(3)
                state["done"] = True
            finally:
                app.complete_async_task(task_id)

        asyncio.get_running_loop().create_task(work())
        return {"accepted": True, "session": context.session_id}
    return {"quick": True, "session": context.session_id, "long_done": state["done"]}


def call(path: str, body: dict | None = None, session: str = "s" * 40) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session,
        },
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def main() -> int:
    threading.Thread(target=lambda: app.run(port=PORT), daemon=True).start()
    time.sleep(2)
    t0 = time.monotonic()
    first = call("/invocations", {"kind": "long"})
    returned_in = time.monotonic() - t0
    busy = call("/ping")["status"]
    second = call("/invocations", {"kind": "quick"})
    time.sleep(3.5)
    idle = call("/ping")["status"]
    print(f"long accepted in {returned_in:.2f}s: {first}")
    print(f"ping while running: {busy}; quick call mid-task: {second}; ping after: {idle}")
    ok = (
        returned_in < 1.5
        and busy == "HealthyBusy"
        and second["long_done"] is False
        and idle == "Healthy"
        and first["session"] == "s" * 40
        and state["done"] is True
    )
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
