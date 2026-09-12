"""The deterministic parts of the incident graph, with no model calls.

The model proposes; these gates decide: the profile's alert list gates activation, the wave rules
gate the call plan, and the outreach node creates the cases.
"""

import asyncio

from strands.multiagent import Status
from strands.multiagent.graph import GraphState

from doorstep_agent.graph import OutreachNode, _gate_assessment, _gate_call_plan, activated
from doorstep_agent.models import AlertAssessment, CallPlan, CaseState
from doorstep_agent.risk import default_call_plan, score_all
from doorstep_agent.runtime import RunContext


def _alert(ctx: RunContext, **update):
    return ctx.store.incident(ctx.incident_id).alert.model_copy(update=update)


def test_profile_gate_overrides_a_model_that_activates_on_an_unlisted_event(
    ctx: RunContext,
) -> None:
    ctx.extras["alert"] = _alert(ctx, event="Dense Fog Advisory", vtec=None)
    _gate_assessment(ctx, AlertAssessment(activate=True, severity="Minor", rationale="model: yes"))
    assessment = ctx.extras["assessment"]
    assert assessment.activate is False
    assert "[gate]" in assessment.rationale
    assert activated(GraphState(), invocation_state={"ctx": ctx}) is False


def test_listed_warning_activates_and_is_stamped_with_the_profile(ctx: RunContext) -> None:
    ctx.extras["alert"] = _alert(ctx)  # the 2021 Excessive Heat Warning fixture
    raw = AlertAssessment(activate=True, severity="severe", hazards="heat", rationale="matches")
    _gate_assessment(ctx, raw)
    assessment = ctx.extras["assessment"]
    assert assessment.activate is True and assessment.ask_captain is False
    assert assessment.hazards == [ctx.profile.id] and assessment.severity == "Severe"
    assert activated(GraphState(), invocation_state={"ctx": ctx}) is True
    assert [e.type for e in ctx.audit.events()] == ["assessment"]


def test_advisory_asks_the_captain(ctx_factory) -> None:
    advisory = "/O.NEW.KPQR.HT.Y.0001.210626T1700Z-210629T0600Z/"
    waiting = ctx_factory(auto_approve=False)
    waiting.extras["alert"] = _alert(waiting, event="Heat Advisory", vtec=advisory)
    _gate_assessment(waiting, AlertAssessment(activate=True, severity="Minor", rationale="x"))
    assert waiting.extras["assessment"].activate is False
    assert waiting.extras["awaiting_captain"] is True
    pending = waiting.store.decisions(waiting.incident_id, status="pending")
    assert [d.name for d in pending] == ["doorstep-borderline-activation"]

    auto = ctx_factory(auto_approve=True)
    auto.extras["alert"] = _alert(auto, event="Heat Advisory", vtec=advisory)
    _gate_assessment(auto, AlertAssessment(activate=True, severity="Minor", rationale="x"))
    assert auto.extras["assessment"].activate is True
    assert auto.extras["assessment"].ask_captain is True
    answered = auto.store.decisions(auto.incident_id, status="answered")
    assert answered and answered[0].response == "activate"


def test_plan_gate_corrects_the_model_and_records_it(ctx: RunContext) -> None:
    # r07 (wave 3) jumps to wave 1 with no reason; r02 and r03 (wave 1) are moved down.
    raw = CallPlan(waves=[["r07", "r01", "r05"], ["r02"], ["r03"]], notes="model notes")
    _gate_call_plan(ctx, raw)
    plan = ctx.extras["call_plan"]
    assert sorted(plan.ordered_ids()) == sorted(r.id for r in ctx.store.residents())
    by_wave = {w.wave: w.resident_ids for w in plan.waves}
    assert "r07" in by_wave[3] and "r02" in by_wave[1] and "r03" in by_wave[1]
    corrections = "\n".join(ctx.extras["plan_corrections"])
    assert "r07: plan jumped" in corrections and "r02: plan moved them down" in corrections
    event = ctx.audit.events()[-1]
    assert event.type == "triage" and event.rationale == "model notes"


def test_outreach_node_creates_the_cases_in_plan_order(ctx: RunContext) -> None:
    plan = default_call_plan(score_all(ctx.store.residents(), ctx.profile))
    ctx.extras["call_plan"] = plan
    result = asyncio.run(OutreachNode().invoke_async("start", {"ctx": ctx}))
    assert result.status == Status.COMPLETED
    assert ctx.extras["queued"] == plan.ordered_ids()
    assert ctx.extras["queued"][0] == "r05"  # highest score first
    cases = ctx.store.cases(ctx.incident_id)
    assert len(cases) == 12 and all(c.state == CaseState.QUEUED for c in cases)
    assert ctx.audit.events()[-1].reason == "queued 12 check-ins in 3 waves"
