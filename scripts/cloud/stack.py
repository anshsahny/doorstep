"""Read the deployed Doorstep stack's outputs (names and ARNs only, never secrets)."""

from __future__ import annotations

import boto3

STACK = "Doorstep"


def outputs(session: boto3.Session | None = None) -> dict[str, str]:
    session = session or boto3.Session(profile_name="doorstep", region_name="us-east-1")
    stacks = session.client("cloudformation").describe_stacks(StackName=STACK)["Stacks"]
    return {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}


def session() -> boto3.Session:
    return boto3.Session(profile_name="doorstep", region_name="us-east-1")
