"""The Doorstep stack (PLAN Phase 3): AWS CDK in Python, one `make deploy`.

What is here, and the one rule each part follows:

* DynamoDB `doorstep` and an S3 data bucket: private, encrypted, TLS only. Data is synthetic
  and re-seedable, so both are destroyed with the stack.
* The coordinator on Amazon Bedrock AgentCore Runtime (Strands Agents, arm64 container). Its
  role is written out by hand: two models, one table, one S3 prefix, five named parameters, and
  the logging/tracing actions the AgentCore docs list. No `*` where a resource can be named.
* Voice on a second runtime from the same image (`doorstep_voice`, Strands BidiAgent on Nova 2
  Sonic), reached by browsers over its `/ws` WebSocket with a 60 s presigned URL. Its role can
  stream one model, read incidents, claim voice tokens and send events to the coordinator; it
  holds no Telegram or Twilio secret.
* Lambdas behind an HTTP API and a schedule. Each has its own role, its own log group, and
  DynamoDB access limited to the key prefixes it owns (`dynamodb:LeadingKeys`).
* No secret appears anywhere in this file, the template, or the outputs. Parameters are
  referenced by name; their values are written by `make secrets-push`.
"""

from __future__ import annotations

import json
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as event_sources
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_sqs as sqs
from constructs import Construct

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_NAME = "doorstep_coordinator"
VOICE_RUNTIME_NAME = "doorstep_voice"
SONIC_MODEL = "amazon.nova-2-sonic-v1:0"
VOICE_TOKEN_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Voice-Token"
# Browser pages that may ask for a voice link (Phase 5 adds the dashboard's CloudFront origin).
VOICE_ORIGINS = ["http://localhost:5174"]
SSM_PREFIX = "/doorstep"
MODELS = {
    # inference profile id -> the foundation model it routes to (us-east-1/us-east-2/us-west-2)
    "us.amazon.nova-2-lite-v1:0": "amazon.nova-2-lite-v1:0",
    "us.amazon.nova-micro-v1:0": "amazon.nova-micro-v1:0",
}
LOG_RETENTION = logs.RetentionDays.TWO_WEEKS


class DoorstepStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        runtime_context: Path,
        lambda_bundle: Path,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        org = json.loads((ROOT / "data" / "org.json").read_text(encoding="utf-8"))

        # --- data ---
        table = dynamodb.Table(
            self,
            "Table",
            table_name="doorstep",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY,
        )
        table.add_global_secondary_index(
            index_name="GSI1",
            partition_key=dynamodb.Attribute(name="GSI1PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="GSI1SK", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.KEYS_ONLY,
        )
        bucket = s3.Bucket(
            self,
            "Data",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[s3.LifecycleRule(prefix="sessions/", expiration=Duration.days(30))],
        )

        def param_arn(name: str) -> str:
            return f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_PREFIX}/{name}"

        # --- real check-in calls: permitted by Cedar in the coordinator, dialled by a worker ---
        dead_calls = sqs.Queue(
            self,
            "CheckinJobsDlq",
            queue_name="doorstep-checkin-jobs-dlq",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            retention_period=Duration.days(14),
        )
        checkin_jobs = sqs.Queue(
            self,
            "CheckinJobs",
            queue_name="doorstep-checkin-jobs",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            visibility_timeout=Duration.seconds(90),
            retention_period=Duration.hours(1),
            # A real call is never retried by the queue: one receive, then the dead-letter queue.
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=1, queue=dead_calls),
        )

        # --- the coordinator on AgentCore Runtime ---
        image = ecr_assets.DockerImageAsset(
            self,
            "RuntimeImage",
            directory=str(runtime_context),
            platform=ecr_assets.Platform.LINUX_ARM64,
        )
        runtime_role = iam.Role(
            self,
            "RuntimeRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:*"
                    },
                },
            ),
            description="Doorstep coordinator on AgentCore Runtime (least privilege)",
        )
        runtime_logs = (
            f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/"
        )
        runtime_policy = iam.Policy(
            self,
            "RuntimePolicy",
            statements=[
                iam.PolicyStatement(
                    sid="PullOwnImage",
                    actions=["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                    resources=[image.repository.repository_arn],
                ),
                iam.PolicyStatement(
                    sid="EcrTokenHasNoResource",
                    actions=["ecr:GetAuthorizationToken"],
                    resources=["*"],
                ),
                iam.PolicyStatement(
                    sid="RuntimeLogs",
                    actions=["logs:CreateLogGroup", "logs:DescribeLogStreams"],
                    resources=[f"{runtime_logs}{RUNTIME_NAME}-*"],
                ),
                iam.PolicyStatement(
                    sid="RuntimeLogEvents",
                    actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                    resources=[f"{runtime_logs}{RUNTIME_NAME}-*:log-stream:*"],
                ),
                iam.PolicyStatement(
                    sid="SpansIntoOwnLogGroup",
                    actions=["logs:PutResourcePolicy"],
                    resources=[f"{runtime_logs}{RUNTIME_NAME}-*"],
                ),
                iam.PolicyStatement(
                    sid="DescribeLogGroups",
                    actions=["logs:DescribeLogGroups"],
                    resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:*"],
                ),
                iam.PolicyStatement(
                    sid="TracesHaveNoResource",
                    actions=[
                        "xray:PutTraceSegments",
                        "xray:PutTelemetryRecords",
                        "xray:GetSamplingRules",
                        "xray:GetSamplingTargets",
                    ],
                    resources=["*"],
                ),
                iam.PolicyStatement(
                    sid="MetricsInOwnNamespace",
                    actions=["cloudwatch:PutMetricData"],
                    resources=["*"],
                    conditions={"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
                ),
                iam.PolicyStatement(
                    sid="TwoModels",
                    actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                    resources=[
                        *(
                            f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/{p}"
                            for p in MODELS
                        ),
                        *(f"arn:aws:bedrock:*::foundation-model/{m}" for m in MODELS.values()),
                    ],
                ),
                iam.PolicyStatement(
                    sid="OneTable",
                    actions=[
                        "dynamodb:GetItem",
                        "dynamodb:PutItem",
                        "dynamodb:UpdateItem",
                        "dynamodb:Query",
                        "dynamodb:BatchWriteItem",
                        "dynamodb:ConditionCheckItem",
                    ],
                    resources=[table.table_arn, f"{table.table_arn}/index/*"],
                ),
                iam.PolicyStatement(
                    sid="SessionObjects",
                    actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    resources=[bucket.arn_for_objects("sessions/*")],
                ),
                iam.PolicyStatement(
                    sid="ListSessionsOnly",
                    actions=["s3:ListBucket"],
                    resources=[bucket.bucket_arn],
                    conditions={"StringLike": {"s3:prefix": ["sessions/*"]}},
                ),
                iam.PolicyStatement(
                    sid="NamedParameters",
                    actions=["ssm:GetParameters", "ssm:GetParameter"],
                    resources=[
                        param_arn(n)
                        for n in (
                            "telegram/bot_token",
                            "telegram/captain_chat_id",
                            "telegram/volunteer_chat_ids",
                            "call_allowlist",
                            "operator_test_number",
                            "kill_switch",
                        )
                    ],
                ),
                iam.PolicyStatement(
                    sid="QueuePermittedCalls",
                    actions=["sqs:SendMessage"],
                    resources=[checkin_jobs.queue_arn],
                ),
            ],
        )
        runtime_policy.attach_to_role(runtime_role)

        runtime = agentcore.CfnRuntime(
            self,
            "Coordinator",
            agent_runtime_name=RUNTIME_NAME,
            description="Doorstep coordinator: Strands Agents on Amazon Bedrock AgentCore",
            role_arn=runtime_role.role_arn,
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=agentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=image.image_uri
                )
            ),
            network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode="PUBLIC"
            ),
            protocol_configuration="HTTP",
            lifecycle_configuration=agentcore.CfnRuntime.LifecycleConfigurationProperty(
                idle_runtime_session_timeout=300, max_lifetime=7200
            ),
            environment_variables={
                "DOORSTEP_TABLE": table.table_name,
                "DOORSTEP_SESSIONS_BUCKET": bucket.bucket_name,
                "DOORSTEP_ORG_ID": org["id"],
                "DOORSTEP_SSM_PREFIX": SSM_PREFIX,
                "DOORSTEP_MODEL_AGENT": "us.amazon.nova-2-lite-v1:0",
                "DOORSTEP_MODEL_PERSONA": "us.amazon.nova-micro-v1:0",
                "DOORSTEP_CHECKIN_QUEUE_URL": checkin_jobs.queue_url,
            },
        )
        runtime.node.add_dependency(runtime_policy)
        invoke_runtime = iam.PolicyStatement(
            sid="InvokeCoordinator",
            actions=["bedrock-agentcore:InvokeAgentRuntime"],
            resources=[runtime.attr_agent_runtime_arn, f"{runtime.attr_agent_runtime_arn}/*"],
        )

        # --- voice on AgentCore Runtime ---
        voice_role = iam.Role(
            self,
            "VoiceRuntimeRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:*"
                    },
                },
            ),
            description="Doorstep voice on AgentCore Runtime (least privilege)",
        )
        voice_policy = iam.Policy(
            self,
            "VoicePolicy",
            statements=[
                iam.PolicyStatement(
                    sid="PullOwnImage",
                    actions=["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                    resources=[image.repository.repository_arn],
                ),
                iam.PolicyStatement(
                    sid="EcrTokenHasNoResource",
                    actions=["ecr:GetAuthorizationToken"],
                    resources=["*"],
                ),
                iam.PolicyStatement(
                    sid="RuntimeLogs",
                    actions=["logs:CreateLogGroup", "logs:DescribeLogStreams"],
                    resources=[f"{runtime_logs}{VOICE_RUNTIME_NAME}-*"],
                ),
                iam.PolicyStatement(
                    sid="RuntimeLogEvents",
                    actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                    resources=[f"{runtime_logs}{VOICE_RUNTIME_NAME}-*:log-stream:*"],
                ),
                iam.PolicyStatement(
                    sid="SpansIntoOwnLogGroup",
                    actions=["logs:PutResourcePolicy"],
                    resources=[f"{runtime_logs}{VOICE_RUNTIME_NAME}-*"],
                ),
                iam.PolicyStatement(
                    sid="DescribeLogGroups",
                    actions=["logs:DescribeLogGroups"],
                    resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:*"],
                ),
                iam.PolicyStatement(
                    sid="TracesHaveNoResource",
                    actions=[
                        "xray:PutTraceSegments",
                        "xray:PutTelemetryRecords",
                        "xray:GetSamplingRules",
                        "xray:GetSamplingTargets",
                    ],
                    resources=["*"],
                ),
                iam.PolicyStatement(
                    sid="MetricsInOwnNamespace",
                    actions=["cloudwatch:PutMetricData"],
                    resources=["*"],
                    conditions={"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
                ),
                # InvokeModelWithBidirectionalStream is authorized by bedrock:InvokeModel
                # (Bedrock API reference).
                iam.PolicyStatement(
                    sid="OneSpeechModel",
                    actions=["bedrock:InvokeModel"],
                    resources=[f"arn:aws:bedrock:{self.region}::foundation-model/{SONIC_MODEL}"],
                ),
                iam.PolicyStatement(
                    sid="ReadIncidentsAndRoster",
                    actions=["dynamodb:GetItem", "dynamodb:Query"],
                    resources=[table.table_arn],
                    conditions={
                        "ForAllValues:StringLike": {"dynamodb:LeadingKeys": ["INC#*", "ORG#*"]}
                    },
                ),
                iam.PolicyStatement(
                    sid="ClaimVoiceTokens",
                    actions=["dynamodb:PutItem"],
                    resources=[table.table_arn],
                    conditions={
                        "ForAllValues:StringLike": {"dynamodb:LeadingKeys": ["CLAIM#VOICE#*"]}
                    },
                ),
                iam.PolicyStatement(
                    sid="EventsToTheCoordinator",
                    actions=["bedrock-agentcore:InvokeAgentRuntime"],
                    resources=[
                        runtime.attr_agent_runtime_arn,
                        f"{runtime.attr_agent_runtime_arn}/*",
                    ],
                ),
                iam.PolicyStatement(
                    sid="NamedParameters",
                    actions=["ssm:GetParameters", "ssm:GetParameter"],
                    resources=[param_arn("internal_hmac_secret"), param_arn("kill_switch")],
                ),
            ],
        )
        voice_policy.attach_to_role(voice_role)
        voice_runtime = agentcore.CfnRuntime(
            self,
            "Voice",
            agent_runtime_name=VOICE_RUNTIME_NAME,
            description="Doorstep voice: Strands Agents BidiAgent on Nova 2 Sonic",
            role_arn=voice_role.role_arn,
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=agentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=image.image_uri
                )
            ),
            network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode="PUBLIC"
            ),
            protocol_configuration="HTTP",
            request_header_configuration=agentcore.CfnRuntime.RequestHeaderConfigurationProperty(
                request_header_allowlist=[VOICE_TOKEN_HEADER]
            ),
            lifecycle_configuration=agentcore.CfnRuntime.LifecycleConfigurationProperty(
                idle_runtime_session_timeout=120, max_lifetime=900
            ),
            environment_variables={
                "DOORSTEP_SERVICE": "voice",
                "DOORSTEP_TABLE": table.table_name,
                "DOORSTEP_ORG_ID": org["id"],
                "DOORSTEP_SSM_PREFIX": SSM_PREFIX,
                "DOORSTEP_COORDINATOR_ARN": runtime.attr_agent_runtime_arn,
            },
        )
        voice_runtime.node.add_dependency(voice_policy)

        # --- Lambdas ---
        code = lambda_.Code.from_asset(str(lambda_bundle))

        def function(
            name: str,
            handler: str,
            *,
            params: list[str],
            key_prefixes: list[str],
            item_actions: list[str],
            timeout: int,
            extra_env: dict[str, str] | None = None,
            invoke: bool = True,
        ) -> lambda_.Function:
            log_group = logs.LogGroup(
                self,
                f"{name}Logs",
                log_group_name=f"/aws/lambda/doorstep-{name}",
                retention=LOG_RETENTION,
                removal_policy=RemovalPolicy.DESTROY,
            )
            role = iam.Role(
                self, f"{name}Role", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com")
            )
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="OwnLogGroup",
                    actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                    resources=[f"{log_group.log_group_arn}"],
                )
            )
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="OwnParameters",
                    actions=["ssm:GetParameter"],
                    resources=[param_arn(p) for p in params],
                )
            )
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="OwnKeysOnly",
                    actions=item_actions,
                    resources=[table.table_arn],
                    conditions={"ForAllValues:StringLike": {"dynamodb:LeadingKeys": key_prefixes}},
                )
            )
            if invoke:
                role.add_to_policy(invoke_runtime)
            fn = lambda_.Function(
                self,
                name,
                function_name=f"doorstep-{name}",
                runtime=lambda_.Runtime.PYTHON_3_12,
                architecture=lambda_.Architecture.ARM_64,
                handler=handler,
                code=code,
                role=role,
                log_group=log_group,
                memory_size=256,
                timeout=Duration.seconds(timeout),
                # Active tracing makes the Lambda's trace header `Sampled=1`. Without it botocore
                # forwards `Sampled=0` to the runtime, whose ADOT sampler follows the parent, and
                # a drill started through the API records no spans at all (found 2026-09-12).
                tracing=lambda_.Tracing.ACTIVE,
                environment={
                    "DOORSTEP_TABLE": table.table_name,
                    "DOORSTEP_RUNTIME_ARN": runtime.attr_agent_runtime_arn,
                    "DOORSTEP_SSM_PREFIX": SSM_PREFIX,
                    **(extra_env or {}),
                },
            )
            fn.node.add_dependency(role)
            return fn

        claim_actions = ["dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:GetItem"]
        webhook = function(
            "telegram-webhook",
            "doorstep_api.telegram_webhook.handler",
            params=["telegram/webhook_secret", "kill_switch"],
            key_prefixes=["CLAIM#TGU#*"],
            item_actions=claim_actions,
            timeout=25,
        )
        replay = function(
            "admin-replay",
            "doorstep_api.admin_replay.handler",
            params=["captain_passcode", "kill_switch", "caps"],
            key_prefixes=["CLAIM#IDEM#*", "RATE#*", "CAP#*"],
            item_actions=[*claim_actions, "dynamodb:UpdateItem"],
            timeout=25,
        )
        poller = function(
            "alert-poller",
            "doorstep_api.alert_poller.handler",
            params=["nws_user_agent", "poller_mode", "kill_switch"],
            key_prefixes=["CLAIM#ALERT#*"],
            item_actions=["dynamodb:PutItem"],
            timeout=30,
            extra_env={
                "ORG_LAT": str(org["location"]["lat"]),
                "ORG_LNG": str(org["location"]["lng"]),
            },
        )

        voice_link = function(
            "voice-session",
            "doorstep_api.voice_session.handler",
            params=["internal_hmac_secret", "kill_switch", "caps"],
            key_prefixes=["RATE#voice#*", "CAP#voice#*"],
            item_actions=["dynamodb:UpdateItem"],
            timeout=10,
            extra_env={"DOORSTEP_VOICE_RUNTIME_ARN": voice_runtime.attr_agent_runtime_arn},
            invoke=False,
        )
        voice_link.add_to_role_policy(
            iam.PolicyStatement(
                sid="ReadIncidentOnly",
                actions=["dynamodb:GetItem"],
                resources=[table.table_arn],
                conditions={"ForAllValues:StringLike": {"dynamodb:LeadingKeys": ["INC#*"]}},
            )
        )
        # A presigned URL carries this function's signature: it can open voice sessions only.
        voice_link.add_to_role_policy(
            iam.PolicyStatement(
                sid="PresignVoiceSessions",
                actions=["bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStream"],
                resources=[
                    voice_runtime.attr_agent_runtime_arn,
                    f"{voice_runtime.attr_agent_runtime_arn}/*",
                ],
            )
        )

        dialer = function(
            "checkin-worker",
            "doorstep_api.checkin_worker.handler",
            params=[
                "kill_switch",
                "call_allowlist",
                "twilio/subaccount_sid",
                "twilio/subaccount_token",
                "twilio/from_number",
                "voice_bridge_url",
                "internal_hmac_secret",
            ],
            key_prefixes=["CLAIM#DIALED#*"],
            item_actions=["dynamodb:PutItem"],
            timeout=30,
            invoke=False,
        )
        dialer.add_to_role_policy(
            iam.PolicyStatement(
                sid="ReadIncidentAndRoster",
                actions=["dynamodb:GetItem"],
                resources=[table.table_arn],
                conditions={
                    "ForAllValues:StringLike": {"dynamodb:LeadingKeys": ["INC#*", "ORG#*"]}
                },
            )
        )
        dialer.add_event_source(
            event_sources.SqsEventSource(
                checkin_jobs, batch_size=1, report_batch_item_failures=True
            )
        )

        # --- HTTP API ---
        api = apigw.HttpApi(
            self,
            "Api",
            api_name="doorstep",
            create_default_stage=False,
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=VOICE_ORIGINS,
                allow_methods=[apigw.CorsHttpMethod.POST],
                allow_headers=["content-type"],
                max_age=Duration.hours(1),
            ),
        )
        stage = apigw.HttpStage(
            self,
            "DefaultStage",
            http_api=api,
            stage_name="$default",
            auto_deploy=True,
            throttle=apigw.ThrottleSettings(rate_limit=5, burst_limit=10),
        )
        routes = [
            *api.add_routes(
                path="/telegram/webhook",
                methods=[apigw.HttpMethod.POST],
                integration=integrations.HttpLambdaIntegration("Webhook", webhook),
            ),
            *api.add_routes(
                path="/admin/replay",
                methods=[apigw.HttpMethod.POST],
                integration=integrations.HttpLambdaIntegration("Replay", replay),
            ),
            *api.add_routes(
                path="/voice/session",
                methods=[apigw.HttpMethod.POST],
                integration=integrations.HttpLambdaIntegration("VoiceSession", voice_link),
            ),
        ]
        # Route-level throttling names the routes, so they must exist before the stage.
        for route in routes:
            stage.node.add_dependency(route)
        cfn_stage = stage.node.default_child
        cfn_stage.route_settings = {  # type: ignore[union-attr]
            "POST /admin/replay": {"ThrottlingRateLimit": 1, "ThrottlingBurstLimit": 2},
            "POST /telegram/webhook": {"ThrottlingRateLimit": 10, "ThrottlingBurstLimit": 20},
            "POST /voice/session": {"ThrottlingRateLimit": 1, "ThrottlingBurstLimit": 3},
        }

        # --- schedule ---
        schedule_role = iam.Role(
            self, "PollerScheduleRole", assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com")
        )
        schedule_role.add_to_policy(
            iam.PolicyStatement(actions=["lambda:InvokeFunction"], resources=[poller.function_arn])
        )
        scheduler.CfnSchedule(
            self,
            "PollerSchedule",
            name="doorstep-alert-poller",
            schedule_expression="rate(10 minutes)",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            target=scheduler.CfnSchedule.TargetProperty(
                arn=poller.function_arn,
                role_arn=schedule_role.role_arn,
                retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(maximum_retry_attempts=0),
            ),
            state="ENABLED",
        )

        CfnOutput(self, "ApiUrl", value=api.api_endpoint)
        CfnOutput(self, "RuntimeArn", value=runtime.attr_agent_runtime_arn)
        CfnOutput(self, "RuntimeId", value=runtime.attr_agent_runtime_id)
        CfnOutput(self, "VoiceRuntimeArn", value=voice_runtime.attr_agent_runtime_arn)
        CfnOutput(self, "TableName", value=table.table_name)
        CfnOutput(self, "DataBucket", value=bucket.bucket_name)
