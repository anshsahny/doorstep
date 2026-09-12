"""Shared agent plumbing: the Bedrock model factory and prompt helpers."""

from __future__ import annotations

from strands.models import BedrockModel

from ..runtime import RunContext


def make_model(ctx: RunContext, *, temperature: float = 0.2, max_tokens: int = 900) -> BedrockModel:
    """Nova 2 Lite on Bedrock (SPEC §6). Region and profile come from the environment."""
    return BedrockModel(
        model_id=ctx.settings.model_agent,
        region_name=ctx.settings.aws_region,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)
