"""Smoke test 01: a Strands Agent on Nova 2 Lite calls a toy tool and returns structured output.

Pass criteria:
  - the `heat_index` tool ran at least once (read from the agent's tool metrics)
  - the result carries a validated `HeatIndexReport` (Strands structured output)
  - the reported heat index matches what the tool computed
"""

from __future__ import annotations

from typing import Literal

import boto3
from pydantic import BaseModel, Field
from strands import Agent, tool
from strands.models import BedrockModel

from _common import AWS_PROFILE, MODEL_AGENT, REGION, fail, info, ok, passed, step

_tool_calls: list[dict[str, float]] = []


@tool
def heat_index(temp_f: float, humidity_pct: float) -> dict[str, float]:
    """Compute the NWS heat index ("feels like" temperature) in degrees Fahrenheit.

    Args:
        temp_f: Air temperature in degrees Fahrenheit.
        humidity_pct: Relative humidity as a percentage from 0 to 100.
    """
    t, r = temp_f, humidity_pct
    # Rothfusz regression used by the National Weather Service.
    hi = (
        -42.379
        + 2.04901523 * t
        + 10.14333127 * r
        - 0.22475541 * t * r
        - 6.83783e-3 * t * t
        - 5.481717e-2 * r * r
        + 1.22874e-3 * t * t * r
        + 8.5282e-4 * t * r * r
        - 1.99e-6 * t * t * r * r
    )
    result = {"heat_index_f": round(hi, 1), "temp_f": t, "humidity_pct": r}
    _tool_calls.append(result)
    return result


class HeatIndexReport(BaseModel):
    """Structured summary of a heat-index check."""

    temp_f: float = Field(description="Air temperature that was used, in Fahrenheit")
    humidity_pct: float = Field(description="Relative humidity that was used, in percent")
    heat_index_f: float = Field(description="Heat index returned by the heat_index tool")
    danger_level: Literal["caution", "extreme_caution", "danger", "extreme_danger"] = Field(
        description="NWS heat index category for that heat index"
    )
    advice: str = Field(description="One plain-language sentence of advice for a neighbour")


SYSTEM_PROMPT = (
    "You are Doorstep, a neighbour check-in assistant. When asked about heat, always call the "
    "heat_index tool rather than estimating, then summarise the result."
)

PROMPT = (
    "It is 98 F with 60 percent humidity at Juniper Court this afternoon. "
    "Use the heat_index tool to compute the heat index, then report it."
)


def main() -> None:
    step(f"Creating Strands Agent on {MODEL_AGENT} (profile {AWS_PROFILE}, region {REGION})")
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=REGION)
    model = BedrockModel(
        model_id=MODEL_AGENT, boto_session=session, temperature=0.2, max_tokens=600
    )
    agent = Agent(
        model=model,
        tools=[heat_index],
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,  # quiet: no streaming print output
    )

    step("Invoking the agent with a prompt that requires the tool")
    try:
        result = agent(PROMPT, structured_output_model=HeatIndexReport)
    except Exception as exc:  # noqa: BLE001 - a smoke test reports any failure
        fail(f"Agent invocation failed: {type(exc).__name__}: {exc}")

    tool_metrics = result.metrics.tool_metrics
    # Structured output is itself a tool call in Strands, so count only our tool.
    calls = tool_metrics["heat_index"].call_count if "heat_index" in tool_metrics else 0
    info(f"stop_reason={result.stop_reason} heat_index_calls={calls} tools={sorted(tool_metrics)}")
    info(f"usage={result.metrics.accumulated_usage}")
    info(f"final text: {str(result).strip()[:300]}")

    if calls < 1 or not _tool_calls:
        fail("the heat_index tool was never called")
    computed = _tool_calls[-1]["heat_index_f"]
    ok(f"tool called {calls}x; tool computed heat index {computed} F")

    report = result.structured_output
    if not isinstance(report, HeatIndexReport):
        fail(f"structured_output is {type(report).__name__}, expected HeatIndexReport")
    ok(f"structured output: {report.model_dump_json()}")

    if abs(report.heat_index_f - computed) > 1.0:
        fail(f"report says {report.heat_index_f} F but the tool computed {computed} F")

    passed(f"Nova 2 Lite Strands agent on {MODEL_AGENT}: tool call + structured output")


if __name__ == "__main__":
    main()
