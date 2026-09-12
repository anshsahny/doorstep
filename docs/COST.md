# Doorstep — cost tally

AWS budget alarms: $5 / $15 / $30. Twilio usage trigger: $10.
Update this file whenever something that costs money is created or torn down.

## AWS credits (the number that actually matters)

| | |
|---|---|
| Granted | **$140.00**, expiring **Sep 2027** (expiry is not a constraint) |
| Estimated remaining | **$135.51** as of 2026-09-12 |
| Estimated used | **$4.49** |
| Out-of-pocket so far | **$0.00** — credits cover 100% of usage |

The `doorstep-monthly` budget has no cost-type filters, so it tracks **gross usage before
credits**: it is a credit-burn meter, not a bill. Its $4.25 alert fired on 2026-09-12 at $0.00
actual cost. A budget on **net** cost is the one that would mean real money.

## Running total

| Provider | Spent so far | Notes |
|---|---|---|
| AWS | **$4.49 gross, $0.00 net** | Entirely Bedrock inference from drill runs; nothing is running continuously. Measured from Cost Explorer 2026-09-12 (see the measured rates below), not estimated. Kept: CDK bootstrap (`CDKToolkit`; ~$0/month while empty) and CloudWatch Transaction Search (enabled in Phase 0; no cost until traces flow in Phase 3). No AgentCore runtimes, no EC2. |
| Twilio (`doorstep` subaccount) | about $1.18 | one US local number (about $1.15/month) + two 12 s smoke calls (about $0.014 each, billed per minute) |
| ngrok | $0.00 | free plan |

## Measured rates (Cost Explorer, us-east-1, 2026-09-12)

Cost Explorer reports Bedrock `UsageQuantity` in **thousands of tokens** — Nova Micro's implied
rate matches its published price exactly, which is how the unit was confirmed.

| Model | Rate | Notes |
|---|---|---|
| Nova 2 Lite input | **$0.33 / 1M tokens** | 5.5x Nova Lite 1.0; this is 90% of all spend |
| Nova 2 Lite output | **$2.75 / 1M tokens** | |
| Nova Micro input / output | $0.035 / $0.14 per 1M | personas; negligible |
| **Nova 2 Sonic** | **unknown — no billable line item has appeared** | Eight Phase 0 voice sessions produced no usage record at all. Phase 4 runs entirely on this model, so measure it on the first real call before sizing any cap. |

**One 12-resident local drill ≈ 1M input tokens ≈ $0.37.** (12.3M input tokens over roughly a
dozen drills on 2026-09-12.) An earlier estimate of $0.03–0.05 per drill was wrong by about 8x:
it assumed Nova Lite 1.0 pricing and undercounted tokens about 3x.

## Forward exposure

Phases 2–7 are comfortable: a few gate drills plus roughly $3–5 per full `make evals` pass.

**The judging period is the real risk.** SPEC §14 caps the public sandbox at 30 drills/day, and
each visitor gets their own 12-resident drill:

    30 drills/day x $0.37 = $11.10/day x 24 days (Sep 14 - Oct 8) = ~$266

That exceeds the $135.51 remaining: at the cap, credits run out around **day 12 of a 24-day
judging period**, on a live public link, and spend then lands on a real card. A daily cap alone
does not bound the total. Before Phase 5 ships, add a **cumulative** sandbox cap (about 300
drills covers the whole period inside credits) alongside the daily cap and the kill switch.

## Ledger (newest first)

| Date | Item | Est. | Actual | Status |
|---|---|---|---|---|
| 2026-09-12 | Bedrock: Phase 2 spikes (offline, $0) and two live verification drills | $0.75 | included below | done |
| 2026-09-12 | Bedrock: all drill runs attributed to 2026-09-12 UTC (Phase 1 Friday evening + Saturday Gate 1 re-verification + Phase 2), 12.3M Nova 2 Lite input tokens | — | **$4.47 gross, $0.00 net** | measured |
| 2026-09-11 | Bedrock: Phase 1 spikes (persona on Nova Micro, Graph, Cedar denial) and about eight local drills on Nova 2 Lite + Nova Micro; no cloud resources created | < $0.40 | posted under 2026-09-12 UTC | done |
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
