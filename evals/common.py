"""Shared eval plumbing: persona sets, a context per case, and a token meter for the cost line.

Every suite runs the production agents, tools, Cedar policies and hooks in this process against
an in-memory store. Nothing leaves the process: no calls, no Telegram (the notifier records).
"""

from __future__ import annotations

import json
import tempfile
import threading
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

from doorstep_agent.audit import AuditLog
from doorstep_agent.config import settings
from doorstep_agent.models import Alert, Incident, Mode, ResidentCase
from doorstep_agent.profiles import load_profile
from doorstep_agent.risk import score_all
from doorstep_agent.runtime import RunContext, StoreOutbox
from doorstep_agent.state_machine import CasePolicy, Clock
from doorstep_agent.store import InMemoryStore

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PERSONAS = ROOT / "evals" / "personas"
RESULTS = ROOT / "evals" / "results"
CACHE = ROOT / "evals" / ".cache"
ALERT_FIXTURE = DATA / "alerts" / "2021-06-pqr-excessive-heat-warning.json"

# The drill set (12, also used by every drill and the sandbox), the Phase 6 additions for the
# classification suite (29), and the rest of the 48-resident roster for the backtest (7).
DRILL_SET = PERSONAS / "heat"
EVAL_SET = PERSONAS / "heat-eval"
BACKTEST_SET = PERSONAS / "heat-backtest"

# USD per 1M tokens, us-east-1 (docs/COST.md: Cost Explorer and the Price List API).
PRICES = {
    "nova-2-lite": (0.33, 2.75),
    "nova-micro": (0.035, 0.14),
}


def roster_ids() -> list[str]:
    return [r["id"] for r in json.loads((DATA / "roster.json").read_text())["residents"]]


def eval_context(
    incident_id: str,
    resident_ids: list[str] | None = None,
    *,
    mode: Mode = "drill",
    auto_approve: bool = False,
    store: InMemoryStore | None = None,
) -> RunContext:
    """A fresh incident over `resident_ids` (default: the whole roster), cases created."""
    ids = resident_ids or roster_ids()
    store = store or InMemoryStore.from_data_dir(DATA, resident_ids=ids)
    profile = load_profile("heat")
    alert = Alert.from_fixture(json.loads(ALERT_FIXTURE.read_text()))
    store.save_incident(
        Incident(
            id=incident_id,
            org_id=store.org().id,
            mode=mode,
            profile_id=profile.id,
            alert=alert,
            resident_ids=ids,
        )
    )
    for rid, score in score_all(store.residents(), profile).items():
        store.save_case(ResidentCase(incident_id=incident_id, resident_id=rid, risk=score))
    clock = Clock(compression=30)
    # A fresh sessions directory per context: case ids repeat between a before and an after run,
    # and a dispatcher that restores the earlier run's finished session has nothing left to do.
    cfg = replace(settings(), sessions_dir=Path(tempfile.mkdtemp(prefix="doorstep-evals-")))
    return RunContext(
        store=store,
        incident_id=incident_id,
        profile=profile,
        org=store.org(),
        mode=mode,
        clock=clock,
        policy=CasePolicy(
            clock, max_attempts=cfg.max_attempts, retry_interval_minutes=cfg.retry_interval_minutes
        ),
        audit=AuditLog(store, incident_id),
        settings=cfg,
        alert_severity=alert.severity,
        auto_approve=auto_approve,
        outbox=StoreOutbox(store, incident_id),
    )


class UsageMeter:
    """Counts Bedrock tokens per model by wrapping `BedrockModel.stream` for this process.

    Strands reports usage in the stream's `metadata` event; the personas (ActorSimulator) and
    every Doorstep agent go through the same class, so one wrapper sees all of it.
    """

    def __init__(self) -> None:
        self.tokens: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        self._lock = threading.Lock()
        self._original: Any = None

    def install(self) -> UsageMeter:
        from strands.models.bedrock import BedrockModel

        if self._original is not None:
            return self
        original = BedrockModel.stream
        meter = self

        async def stream(model_self: Any, *args: Any, **kwargs: Any):  # noqa: ANN202
            model_id = str(model_self.config.get("model_id", "?"))
            async for event in original(model_self, *args, **kwargs):
                usage = event.get("metadata", {}).get("usage") if isinstance(event, dict) else None
                if usage:
                    with meter._lock:
                        meter.tokens[model_id][0] += int(usage.get("inputTokens", 0))
                        meter.tokens[model_id][1] += int(usage.get("outputTokens", 0))
                yield event

        BedrockModel.stream = stream  # type: ignore[method-assign]
        self._original = original
        return self

    def snapshot(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        with self._lock:
            items = {k: list(v) for k, v in self.tokens.items()}
        for model_id, (tin, tout) in items.items():
            price = next((p for key, p in PRICES.items() if key in model_id), (0.0, 0.0))
            out[model_id] = {
                "input_tokens": tin,
                "output_tokens": tout,
                "usd": round(tin / 1e6 * price[0] + tout / 1e6 * price[1], 4),
            }
        return out

    def usd(self) -> float:
        return round(sum(v["usd"] for v in self.snapshot().values()), 4)


METER = UsageMeter()


def write_result(name: str, payload: dict[str, Any]) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, default=str, ensure_ascii=False) + "\n")
    return path


def read_result(name: str) -> dict[str, Any] | None:
    path = RESULTS / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None
