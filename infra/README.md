# infra/

One AWS CDK (Python) stack, `Doorstep` (`doorstep_stack.py`): the coordinator and voice
runtimes on AgentCore Runtime (arm64 image from `runtime/Dockerfile`), DynamoDB, S3, SQS with a
dead-letter queue and alarms, Lambdas behind an HTTP API, EventBridge Scheduler, CloudFront for
the dashboard, and least-privilege IAM (checked by `tests/test_infra_template.py`). No EC2.
`make deploy` / `make destroy`.
