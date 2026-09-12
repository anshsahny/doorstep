# Doorstep — cost tally

AWS budget alarms: $5 / $15 / $30. Twilio usage trigger: $10.
Update this file whenever something that costs money is created or torn down.

## Running total

| Provider | Spent so far | Notes |
|---|---|---|
| AWS | under $0.60 | Bedrock verification cleared 2026-09-11 ~12:00. Smoke runs: a few Nova 2 Lite calls (~2.8k tokens each), about eight Nova 2 Sonic sessions of 10–45 s, one AgentCore Runtime deployed for ~10 min then removed. Phase 1: three Bedrock spikes plus about eight local drill runs (each ≈150 Nova 2 Lite / Nova Micro calls, ≈300k input tokens, roughly $0.03–0.05 per drill). Kept: CDK bootstrap (S3 bucket, ECR repo, IAM roles; ~$0/month while empty) and CloudWatch Transaction Search (enabled by the CLI; small per-span cost once traces flow). |
| Twilio (`doorstep` subaccount) | about $1.18 | one US local number (about $1.15/month) + two 12 s smoke calls (about $0.014 each, billed per minute) |
| ngrok | $0.00 | free plan |

## Ledger (newest first)

| Date | Item | Est. | Actual | Status |
|---|---|---|---|---|
| 2026-09-11 | Bedrock: Phase 1 spikes (persona on Nova Micro, Graph, Cedar denial) and about eight local drills on Nova 2 Lite + Nova Micro; no cloud resources created | < $0.40 | | done |
| 2026-09-11 | AgentCore: CDK bootstrap stack `CDKToolkit` (kept for Phase 3) | ~$0/month | | active |
| 2026-09-11 | AgentCore: hello runtime stack `AgentCore-DoorstepHello-default`, two invocations, then removed | cents | | removed |
| 2026-09-11 | Bedrock: smoke 01 (Nova 2 Lite) and smoke 02 (Nova 2 Sonic) runs, including diagnostics | < $0.10 | | done |
| 2026-09-11 | Twilio: two smoke-04 calls, 12 s each | $0.03 | | done |
| 2026-09-11 | Twilio: US local number on the `doorstep` subaccount | $1.15/month | | active (keep through judging, Oct 8) |
| 2026-09-11 | Phase 0 scaffold; no cloud resources created | $0 | $0 | done |

## Planned Phase 0 spend

- Twilio US local number: about $1.15/month. One smoke call of under a minute: about $0.02.
- Nova 2 Lite and Nova 2 Sonic smoke calls: cents.
- AgentCore Runtime "hello": cents while active; torn down right after the test.
- CDK bootstrap (S3 bucket, ECR repo, IAM roles): about $0; kept for Phase 3.

## Teardown checklist

- [x] AgentCore hello runtime removed after smoke 03 (`agentcore remove all -y` + `deploy -y`, 2026-09-11 12:35; `agentcore status` shows no resources)
- [x] Leftover empty log group `/aws/bedrock-agentcore/runtimes/DoorstepHello_hello-…-DEFAULT` deleted (2026-09-11 12:48); no agentcore log groups remain
