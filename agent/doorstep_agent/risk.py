"""Deterministic risk scoring and call waves (SPEC §6.2).

Weights and wave thresholds come from the active hazard profile. The triage agent may move a
resident up one wave with a reason; `validate_call_plan` enforces that nobody moves down and
nobody is dropped or duplicated.
"""

from __future__ import annotations

from .models import CallPlan, CallWave, RiskScore, WaveAdjustment
from .models import Resident as Resident
from .profiles import HazardProfile


def score_resident(resident: Resident, profile: HazardProfile) -> RiskScore:
    points = 0
    applied: list[str] = []
    for factor in profile.risk_factors:
        if resident.field_value(factor.field) == factor.equals:
            points += factor.points
            applied.append(factor.id)
    return RiskScore(
        resident_id=resident.id, points=points, factors=applied, wave=wave_for(points, profile)
    )


def wave_for(points: int, profile: HazardProfile) -> int:
    t = profile.wave_thresholds
    if points >= t.wave1_min:
        return 1
    if points >= t.wave2_min:
        return 2
    return 3


def score_all(residents: list[Resident], profile: HazardProfile) -> dict[str, RiskScore]:
    return {r.id: score_resident(r, profile) for r in residents}


def default_call_plan(scores: dict[str, RiskScore]) -> CallPlan:
    """The plan the deterministic scores imply, highest score first inside each wave."""
    waves: dict[int, list[tuple[int, str]]] = {1: [], 2: [], 3: []}
    for rid, s in scores.items():
        waves[s.wave].append((-s.points, rid))
    return CallPlan(
        waves=[
            CallWave(wave=w, resident_ids=[rid for _, rid in sorted(items)])
            for w, items in sorted(waves.items())
        ],
        notes="Deterministic plan from risk scores.",
    )


def validate_call_plan(
    proposed: CallPlan, scores: dict[str, RiskScore]
) -> tuple[CallPlan, list[str]]:
    """Return a corrected plan and the list of corrections applied.

    Rules: every scored resident appears exactly once; a resident may move up by one wave with a
    stated reason; nobody may move down or jump two waves.
    """
    corrections: list[str] = []
    proposed_wave: dict[str, int] = {}
    for wave in proposed.waves:
        for rid in wave.resident_ids:
            if rid in proposed_wave:
                corrections.append(f"{rid}: listed twice, kept first placement")
                continue
            proposed_wave[rid] = wave.wave

    reasons = {a.resident_id: a.reason for a in proposed.adjustments}
    final: dict[str, int] = {}
    adjustments: list[WaveAdjustment] = []
    for rid, score in scores.items():
        computed = score.wave
        wanted = proposed_wave.get(rid)
        if wanted is None:
            corrections.append(f"{rid}: missing from the plan, placed in wave {computed}")
            final[rid] = computed
        elif wanted == computed:
            final[rid] = computed
        elif wanted == computed - 1 and reasons.get(rid):
            final[rid] = wanted
            adjustments.append(
                WaveAdjustment(
                    resident_id=rid, from_wave=computed, to_wave=wanted, reason=reasons[rid]
                )
            )
        elif wanted == computed - 1:
            corrections.append(f"{rid}: moved up without a reason, kept in wave {computed}")
            final[rid] = computed
        elif wanted > computed:
            corrections.append(
                f"{rid}: plan moved them down to wave {wanted}, kept in wave {computed}"
            )
            final[rid] = computed
        elif reasons.get(rid):  # jumped two waves with a reason: allow a single step
            final[rid] = computed - 1
            adjustments.append(
                WaveAdjustment(
                    resident_id=rid, from_wave=computed, to_wave=computed - 1, reason=reasons[rid]
                )
            )
            corrections.append(
                f"{rid}: plan jumped from wave {computed} to {wanted}, allowed one step to "
                f"wave {computed - 1}"
            )
        else:
            final[rid] = computed
            corrections.append(
                f"{rid}: plan jumped from wave {computed} to {wanted} without a reason, "
                f"kept in wave {computed}"
            )

    unknown = set(proposed_wave) - set(scores)
    for rid in sorted(unknown):
        corrections.append(f"{rid}: not on the roster, dropped")

    waves: dict[int, list[tuple[int, str]]] = {1: [], 2: [], 3: []}
    for rid, w in final.items():
        waves[w].append((-scores[rid].points, rid))
    plan = CallPlan(
        waves=[
            CallWave(wave=w, resident_ids=[rid for _, rid in sorted(items)])
            for w, items in sorted(waves.items())
        ],
        adjustments=adjustments,
        notes=proposed.notes,
    )
    return plan, corrections
