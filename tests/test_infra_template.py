"""Least privilege and no secrets, checked on the synthesized CloudFormation template.

Needs aws-cdk-lib (the `infra` group) and Node for jsii; skipped where they are missing.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

cdk = pytest.importorskip("aws_cdk")
if shutil.which("node") is None:  # pragma: no cover
    pytest.skip("jsii needs node", allow_module_level=True)

from aws_cdk.assertions import Template  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

from infra.doorstep_stack import DoorstepStack  # noqa: E402
from infra.stage import INCLUDE, stage_runtime  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# Only these actions may use Resource "*": AWS documents no resource ARN for them.
NO_RESOURCE_ACTIONS = {
    "ecr:GetAuthorizationToken",
    "xray:PutTraceSegments",
    "xray:PutTelemetryRecords",
    "xray:GetSamplingRules",
    "xray:GetSamplingTargets",
    "cloudwatch:PutMetricData",
}
ALLOWED_ENV = {
    "DOORSTEP_TABLE",
    "DOORSTEP_SESSIONS_BUCKET",
    "DOORSTEP_ORG_ID",
    "DOORSTEP_SSM_PREFIX",
    "DOORSTEP_MODEL_AGENT",
    "DOORSTEP_MODEL_PERSONA",
    "DOORSTEP_RUNTIME_ARN",
    "DOORSTEP_SERVICE",
    "DOORSTEP_COORDINATOR_ARN",
    "DOORSTEP_VOICE_RUNTIME_ARN",
    "DOORSTEP_CHECKIN_QUEUE_URL",
    "ORG_LAT",
    "ORG_LNG",
}


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> dict:
    runtime = stage_runtime(tmp_path_factory.mktemp("runtime"))
    bundle = tmp_path_factory.mktemp("lambda")
    (bundle / "doorstep_api").mkdir()
    app = cdk.App()
    stack = DoorstepStack(
        app,
        "Doorstep",
        runtime_context=runtime,
        lambda_bundle=bundle,
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    return Template.from_stack(stack).to_json()


def resources(template: dict, kind: str) -> dict[str, dict]:
    return {k: v for k, v in template["Resources"].items() if v["Type"] == kind}


def statements(template: dict):
    for lid, r in resources(template, "AWS::IAM::Policy").items():
        for st in r["Properties"]["PolicyDocument"]["Statement"]:
            yield lid, st


def as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def test_no_action_wildcards_and_star_resources_only_where_aws_allows_nothing_else(
    template,
) -> None:
    for lid, st in statements(template):
        for action in as_list(st["Action"]):
            assert "*" not in action, f"{lid}: wildcard action {action}"
        if "*" in as_list(st["Resource"]):
            assert set(as_list(st["Action"])) <= NO_RESOURCE_ACTIONS, f"{lid}: {st}"


def test_our_roles_use_no_managed_policies(template) -> None:
    for lid, role in resources(template, "AWS::IAM::Role").items():
        if lid.startswith("CustomS3AutoDeleteObjects"):
            continue  # CDK's own provider, runs only when the stack is deleted
        assert not role["Properties"].get("ManagedPolicyArns"), lid


def test_the_webhook_can_only_touch_telegram_update_claims(template) -> None:
    webhook = [st for lid, st in statements(template) if lid.startswith("telegramwebhookRole")]
    dynamo = [st for st in webhook if any(a.startswith("dynamodb:") for a in as_list(st["Action"]))]
    assert dynamo and all(
        as_list(st["Condition"]["ForAllValues:StringLike"]["dynamodb:LeadingKeys"])
        == ["CLAIM#TGU#*"]
        for st in dynamo
    )
    ssm = [st for st in webhook if "ssm:GetParameter" in as_list(st["Action"])]
    text = json.dumps(ssm)
    assert "webhook_secret" in text and "bot_token" not in text


def test_the_runtime_role_names_its_models_table_prefix_and_parameters(template) -> None:
    runtime = [st for lid, st in statements(template) if lid.startswith("RuntimePolicy")]
    text = json.dumps(runtime)
    assert "foundation-model/*" not in text
    assert "amazon.nova-2-lite-v1:0" in text and "amazon.nova-micro-v1:0" in text
    assert "sessions/*" in text
    assert "GetWorkloadAccessToken" not in text


def test_environment_variables_are_names_not_secrets(template) -> None:
    envs = [
        r["Properties"].get("Environment", {}).get("Variables", {})
        for r in resources(template, "AWS::Lambda::Function").values()
    ] + [
        r["Properties"].get("EnvironmentVariables", {})
        for r in resources(template, "AWS::BedrockAgentCore::Runtime").values()
    ]
    names = {k for env in envs for k in env}
    assert names <= ALLOWED_ENV, names - ALLOWED_ENV


def test_no_local_secret_value_appears_in_the_template(template) -> None:
    text = json.dumps(template)
    local = dotenv_values(ROOT / ".env") if (ROOT / ".env").exists() else {}
    for key, value in local.items():
        if value and len(value) >= 6 and not key.startswith(("AWS_", "DOORSTEP_MODEL")):
            assert value not in text, f"the value of {key} is in the template"


def test_bucket_is_private_and_tls_only(template) -> None:
    bucket = next(iter(resources(template, "AWS::S3::Bucket").values()))["Properties"]
    assert all(bucket["PublicAccessBlockConfiguration"].values())
    policy = json.dumps(resources(template, "AWS::S3::BucketPolicy"))
    assert "aws:SecureTransport" in policy


def test_lambdas_are_traced_so_runtime_spans_are_sampled(template) -> None:
    for fn in resources(template, "AWS::Lambda::Function").values():
        if "doorstep_api" in fn["Properties"].get("Handler", ""):
            assert fn["Properties"]["TracingConfig"]["Mode"] == "Active"


def test_logs_expire_and_the_api_is_throttled(template) -> None:
    for group in resources(template, "AWS::Logs::LogGroup").values():
        assert group["Properties"]["RetentionInDays"] == 14
    stage = next(iter(resources(template, "AWS::ApiGatewayV2::Stage").values()))["Properties"]
    assert stage["DefaultRouteSettings"]["ThrottlingRateLimit"] == 2
    assert stage["RouteSettings"]["POST /admin/replay"]["ThrottlingRateLimit"] == 1


def test_the_runtime_build_context_is_an_allowlist(tmp_path: Path) -> None:
    staged = stage_runtime(tmp_path / "ctx")
    assert not any(".env" in p.name for p in staged.rglob("*"))
    assert not any(part.startswith(".env") for part in INCLUDE)
    assert not (staged / ".sessions").exists() and not (staged / "node_modules").exists()


def test_the_voice_runtime_streams_one_model_and_holds_no_channel_secret(template) -> None:
    voice = [st for lid, st in statements(template) if lid.startswith("VoicePolicy")]
    text = json.dumps(voice)
    assert "foundation-model/amazon.nova-2-sonic-v1:0" in text and "nova-2-lite" not in text
    assert "telegram" not in text and "twilio" not in text and "captain_passcode" not in text
    assert "sessions/*" not in text, "no access to the coordinator's paused sessions"
    writes = [st for st in voice if "dynamodb:PutItem" in as_list(st["Action"])]
    assert writes and all(
        st["Condition"]["ForAllValues:StringLike"]["dynamodb:LeadingKeys"] == ["CLAIM#VOICE#*"]
        for st in writes
    )
    runtimes = resources(template, "AWS::BedrockAgentCore::Runtime")
    voice_rt = next(
        r for r in runtimes.values() if r["Properties"]["AgentRuntimeName"] == "doorstep_voice"
    )
    assert voice_rt["Properties"]["RequestHeaderConfiguration"]["RequestHeaderAllowlist"] == [
        "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Voice-Token"
    ]


def test_the_voice_link_function_can_presign_voice_sessions_and_nothing_else(template) -> None:
    link = [st for lid, st in statements(template) if lid.startswith("voicesessionRole")]
    actions = {a for st in link for a in as_list(st["Action"])}
    assert "bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStream" in actions
    assert "bedrock-agentcore:InvokeAgentRuntime" not in actions
    assert "dynamodb:PutItem" not in actions


def test_only_the_dialer_holds_twilio_credentials_and_it_cannot_reach_the_runtime(template) -> None:
    by_role: dict[str, str] = {}
    for lid, st in statements(template):
        by_role[lid] = by_role.get(lid, "") + json.dumps(st)
    holders = sorted(lid for lid, text in by_role.items() if "twilio/subaccount_token" in text)
    assert holders and all(lid.startswith("checkinworkerRole") for lid in holders), holders
    dialer = "".join(text for lid, text in by_role.items() if lid.startswith("checkinworkerRole"))
    assert "bedrock-agentcore" not in dialer and "bedrock:" not in dialer
    assert "CLAIM#DIALED#*" in dialer


def test_the_call_queue_never_retries_a_real_call(template) -> None:
    queues = resources(template, "AWS::SQS::Queue")
    jobs = next(
        q for q in queues.values() if q["Properties"].get("QueueName") == "doorstep-checkin-jobs"
    )
    assert jobs["Properties"]["RedrivePolicy"]["maxReceiveCount"] == 1
    runtime = [st for lid, st in statements(template) if lid.startswith("RuntimePolicy")]
    assert any("sqs:SendMessage" in as_list(st["Action"]) for st in runtime)


# --- Phase 5: the dashboard -----------------------------------------------------------------


def test_every_bucket_is_private_and_the_site_is_served_only_through_cloudfront(template) -> None:
    for bucket in resources(template, "AWS::S3::Bucket").values():
        assert all(bucket["Properties"]["PublicAccessBlockConfiguration"].values())
    (dist,) = resources(template, "AWS::CloudFront::Distribution").values()
    config = dist["Properties"]["DistributionConfig"]
    assert config["DefaultCacheBehavior"]["ViewerProtocolPolicy"] == "redirect-to-https"
    assert config["PriceClass"] == "PriceClass_100"
    assert resources(template, "AWS::CloudFront::OriginAccessControl")
    policies = json.dumps(resources(template, "AWS::S3::BucketPolicy"))
    assert "cloudfront.amazonaws.com" in policies


def test_the_dashboard_function_reads_incidents_and_counts_its_own_caps_only(template) -> None:
    board = [st for lid, st in statements(template) if lid.startswith("dashboardRole")]
    text = json.dumps(board)
    for secret in ("telegram", "twilio", "call_allowlist", "operator_test_number"):
        assert secret not in text, secret
    writes = [
        st
        for st in board
        if {"dynamodb:PutItem", "dynamodb:UpdateItem"} & set(as_list(st["Action"]))
    ]
    prefixes = {
        p
        for st in writes
        for p in st["Condition"]["ForAllValues:StringLike"]["dynamodb:LeadingKeys"]
    }
    assert prefixes == {"RATE#sandbox#*", "CAP#sandbox#*", "RATE#passfail#*", "CLAIM#IDEM#drills#*"}
    assert not any(p.startswith(("INC#", "ORG#")) for p in prefixes), "reads only, never writes"


def test_every_route_is_throttled_and_cors_names_the_site_only(template) -> None:
    routes = {
        r["Properties"]["RouteKey"]
        for r in resources(template, "AWS::ApiGatewayV2::Route").values()
    }
    stage = next(iter(resources(template, "AWS::ApiGatewayV2::Stage").values()))["Properties"]
    assert routes <= set(stage["RouteSettings"]), routes - set(stage["RouteSettings"])
    assert stage["RouteSettings"]["GET /incidents/{incident_id}"]["ThrottlingRateLimit"] == 3
    assert stage["RouteSettings"]["POST /drills"]["ThrottlingRateLimit"] == 1
    (api,) = resources(template, "AWS::ApiGatewayV2::Api").values()
    origins = api["Properties"]["CorsConfiguration"]["AllowOrigins"]
    assert "*" not in json.dumps(origins)
    assert [o for o in origins if isinstance(o, str)] == [
        "http://localhost:5174",
        "http://localhost:5173",
    ]
