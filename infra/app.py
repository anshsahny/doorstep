"""CDK app entry point: stage the allowlisted build inputs, then define the Doorstep stack."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import aws_cdk as cdk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from infra.doorstep_stack import DoorstepStack  # noqa: E402
from infra.stage import stage_lambdas, stage_runtime  # noqa: E402

app = cdk.App()
DoorstepStack(
    app,
    "Doorstep",
    runtime_context=stage_runtime(),
    lambda_bundle=stage_lambdas(),
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION", "us-east-1"),
    ),
    description="Doorstep: neighbour check-ins on Amazon Bedrock AgentCore (Strands Agents)",
)
app.synth()
