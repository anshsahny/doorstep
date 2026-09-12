"""Incident start as a Strands Graph: alert_assessor -> triage -> outreach (SPEC §5, §6).

Two Strands Agent nodes with structured output and one deterministic node. The model proposes;
deterministic code decides:
- the profile's `alert_events` gate activation even if the assessor says otherwise;
- `validate_call_plan` enforces the wave rules on the triage output;
- the outreach node creates the resident cases in wave order without any model call.
"""

from __future__ import annotations

import asyncio
from typing import Any

from strands.agent.agent_result import AgentResult
from strands.hooks import AfterInvocationEvent, HookProvider, HookRegistry
from strands.multiagent import GraphBuilder, Status
from strands.multiagent.base import MultiAgentBase, MultiAgentResult, NodeResult
from strands.multiagent.graph import Graph, GraphState
from strands.telemetry.metrics import EventLoopMetrics

from .agents.alert_assessor import AGENT_NAME as ASSESSOR
from .agents.alert_assessor import alert_task_text, build_alert_assessor
from .agents.triage import AGENT_NAME as TRIAGE
from .agents.triage import build_triage_agent
from .decisions import expires_at
from .models import (
    Alert,
    AlertAssessment,
    CallPlan,
    Decision,
    DecisionOption,
    Incident,
    ResidentCase,
)
from .risk import default_call_plan, score_all, validate_call_plan
from .runtime import RunContext

OUTREACH = "outreach"


class CaptureStructuredOutput(HookProvider):
    """Store each agent's structured output in the shared run context, keyed by agent name."""

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(AfterInvocationEvent, self.after)

    def after(self, event: AfterInvocationEvent) -> None:
        ctx: RunContext | None = event.invocation_state.get("ctx")
        if ctx is None or event.result is None or event.result.structured_output is None:
            return
        if event.agent.name == ASSESSOR:
            _gate_assessment(ctx, event.result.structured_output)
        elif event.agent.name == TRIAGE:
            _gate_call_plan(ctx, event.result.structured_output)


def _gate_assessment(ctx: RunContext, raw: AlertAssessment) -> None:
    """Apply the profile's activation rule on top of the model's judgement."""
    alert: Alert = ctx.extras["alert"]
    rule = ctx.profile.activation_for(alert.event, alert.vtec_code())
    assessment = raw.model_copy(deep=True)
    if rule is None:
        if assessment.activate:
            assessment.rationale += (
                f" [gate] The {ctx.profile.id} profile does not list '{alert.event}'; "
                "not activated."
            )
        assessment.activate = False
        assessment.ask_captain = False
    elif rule == "ask":
        assessment.ask_captain = True
    else:
        assessment.ask_captain = False
    if rule is not None and assessment.activate:
        assessment.hazards = [ctx.profile.id]
    if assessment.activate and assessment.ask_captain:
        decision = Decision(
            id="dec-activation",
            incident_id=ctx.incident_id,
            resident_id=None,
            name="doorstep-borderline-activation",
            reason=(
                f"{alert.event}: the profile asks the captain before activating. "
                f"{assessment.rationale}"
            ),
            options=[
                DecisionOption(id="activate", label="Activate the check-in", action="activate"),
                DecisionOption(id="stand_down", label="Stand down", action="stand_down"),
            ],
            # Raised by the graph rather than by a paused tool, so it is answerable immediately
            # and `respond_to_decision` applies it directly instead of resuming an agent.
            status="pending",
            audience=ctx.org.captain_id,
            expires_at=expires_at(ctx),
        )
        if ctx.auto_approve:
            decision.status, decision.responder, decision.response = (
                "answered",
                "captain:auto-approve",
                "activate",
            )
            decision.responded_at = ctx.clock.now()
        ctx.store.save_decision(decision)
        ctx.audit.record(
            actor="system:decisions",
            type="decision",
            reason=f"borderline activation: {decision.status} ({decision.response or 'pending'})",
        )
        if not ctx.auto_approve:
            assessment.activate = False
            ctx.extras["awaiting_captain"] = True
    ctx.extras["assessment"] = assessment
    ctx.audit.record(
        actor=f"agent:{ASSESSOR}",
        type="assessment",
        reason=(
            f"activate={assessment.activate} severity={assessment.severity} "
            f"hazards={assessment.hazards}"
        ),
        rationale=assessment.rationale,
    )


def _gate_call_plan(ctx: RunContext, raw: CallPlan) -> None:
    scores = score_all(ctx.store.residents(), ctx.profile)
    plan, corrections = validate_call_plan(raw, scores)
    ctx.extras["call_plan"] = plan
    ctx.extras["plan_corrections"] = corrections
    ctx.audit.record(
        actor=f"agent:{TRIAGE}",
        type="triage",
        reason=f"waves: {[len(w.resident_ids) for w in plan.waves]}; adjustments: "
        f"{[(a.resident_id, a.from_wave, a.to_wave) for a in plan.adjustments]}; "
        f"corrections: {len(corrections)}",
        rationale=plan.notes,
        data={"corrections": corrections},
    )


def activated(state: GraphState, *, invocation_state: dict[str, Any], **_: Any) -> bool:
    ctx: RunContext = invocation_state["ctx"]
    assessment = ctx.extras.get("assessment")
    return bool(assessment and assessment.activate)


class OutreachNode(MultiAgentBase):
    """Deterministic node: create the resident cases in wave order. No model."""

    def __init__(self, node_id: str = OUTREACH) -> None:
        super().__init__()
        self.id = node_id

    async def invoke_async(self, task, invocation_state=None, **kwargs) -> MultiAgentResult:
        ctx: RunContext = (invocation_state or {})["ctx"]
        scores = score_all(ctx.store.residents(), ctx.profile)
        plan: CallPlan = ctx.extras.get("call_plan") or default_call_plan(scores)
        created = 0
        for rid in plan.ordered_ids():
            ctx.store.save_case(
                ResidentCase(incident_id=ctx.incident_id, resident_id=rid, risk=scores[rid])
            )
            created += 1
        ctx.extras["queued"] = plan.ordered_ids()
        text = f"queued {created} check-ins in {len(plan.waves)} waves"
        ctx.audit.record(actor="system:outreach", type="incident", reason=text)
        agent_result = AgentResult(
            stop_reason="end_turn",
            message={"role": "assistant", "content": [{"text": text}]},
            metrics=EventLoopMetrics(),
            state={},
        )
        return MultiAgentResult(
            status=Status.COMPLETED,
            results={self.id: NodeResult(result=agent_result, status=Status.COMPLETED)},
        )


def build_incident_graph(ctx: RunContext) -> Graph:
    assessor = build_alert_assessor(ctx)
    assessor.hooks.add_hook(CaptureStructuredOutput())
    triage = build_triage_agent(ctx)
    triage.hooks.add_hook(CaptureStructuredOutput())
    builder = GraphBuilder()
    builder.add_node(assessor, ASSESSOR)
    builder.add_node(triage, TRIAGE)
    builder.add_node(OutreachNode(), OUTREACH)
    builder.add_edge(ASSESSOR, TRIAGE, condition=activated)
    builder.add_edge(TRIAGE, OUTREACH)
    builder.set_entry_point(ASSESSOR)
    builder.set_max_node_executions(3)
    builder.set_node_timeout(ctx.settings.step_timeout_seconds * 1.5)
    builder.set_execution_timeout(ctx.settings.step_timeout_seconds * 3)
    return builder.build()


async def start_incident(ctx: RunContext, alert: Alert) -> Incident:
    """Run the incident-start graph and record the outcome on the Incident."""
    incident = ctx.store.incident(ctx.incident_id)
    ctx.extras["alert"] = alert
    ctx.alert_severity = alert.severity
    ctx.audit.record(
        actor="system:incident",
        type="incident",
        reason=f"alert received: {alert.event} ({alert.severity}) {alert.vtec or ''}".strip(),
    )
    graph = build_incident_graph(ctx)
    try:
        await asyncio.wait_for(
            graph.invoke_async(
                alert_task_text(alert),
                invocation_state=ctx.invocation_state(actor="graph:incident"),
            ),
            ctx.settings.step_timeout_seconds * 3 + 5,
        )
    except Exception as exc:  # noqa: BLE001 - a failed start is reported, never hangs
        ctx.audit.record(
            actor="system:incident",
            type="note",
            reason=f"incident-start graph failed: {type(exc).__name__}: {exc}",
        )
    assessment: AlertAssessment | None = ctx.extras.get("assessment")
    incident.assessment = assessment
    if assessment is None:
        incident.status = "not_activated"
    elif ctx.extras.get("awaiting_captain"):
        incident.status = "awaiting_captain"
    elif not assessment.activate:
        incident.status = "not_activated"
    else:
        incident.status = "active"
        incident.call_plan = ctx.extras.get("call_plan")
    ctx.store.save_incident(incident)
    return incident
