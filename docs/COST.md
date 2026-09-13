# Doorstep — cost tally

AWS budget alarms: $5 / $15 / $30. Twilio usage trigger: $10.
Update this file whenever something that costs money is created or torn down.

## AWS credits (the number that actually matters)

| | |
|---|---|
| Granted | **$140.00**, expiring **Sep 2027** (expiry is not a constraint) |
| Estimated remaining | **about $134.5** as of 2026-09-12 evening |
| Estimated used | **about $5.5** (Cost Explorer $5.31 + unposted) |
| Out-of-pocket so far | **$0.00** — credits cover 100% of usage |

The `doorstep-monthly` budget has no cost-type filters, so it tracks **gross usage before
credits**: it is a credit-burn meter, not a bill. Its $4.25 alert fired on 2026-09-12 at $0.00
actual cost. A budget on **net** cost is the one that would mean real money.

## Running total

| Provider | Spent so far | Notes |
|---|---|---|
| AWS | **$5.31 gross, $0.00 net** (Cost Explorer, Sep 12; the last drills may not be posted yet) | Entirely Bedrock inference from drill runs; nothing is running continuously. Measured from Cost Explorer 2026-09-12 (see the measured rates below), not estimated. Kept: CDK bootstrap (`CDKToolkit`; ~$0/month while empty) and CloudWatch Transaction Search (enabled in Phase 0; no cost until traces flow in Phase 3). No AgentCore runtimes, no EC2. |
| Twilio (`doorstep` subaccount) | about $1.29 | one US local number (about $1.15/month) + two 12 s smoke calls (about $0.014 each, billed per minute) |
| ngrok | $0.00 | free plan |

## Measured rates (Cost Explorer, us-east-1, 2026-09-12)

Cost Explorer reports Bedrock `UsageQuantity` in **thousands of tokens** — Nova Micro's implied
rate matches its published price exactly, which is how the unit was confirmed.

| Model | Rate | Notes |
|---|---|---|
| Nova 2 Lite input | **$0.33 / 1M tokens** | 5.5x Nova Lite 1.0; this is 90% of all spend |
| Nova 2 Lite output | **$2.75 / 1M tokens** | |
| Nova Micro input / output | $0.035 / $0.14 per 1M | personas; negligible |
| **Nova 2 Sonic** speech in / out | **$3.00 / $12.00 per 1M tokens** | AWS Price List API, us-east-1, `USE1-NovaSonic2.0-speech-{input,output}-tokens` (2026-09-12). Text in/out $0.33 / $2.75 per 1M. No Cost Explorer line yet (usage posts a day late). |

**One 12-resident local drill ≈ 1M input tokens ≈ $0.37.** (12.3M input tokens over roughly a
dozen drills on 2026-09-12.) An earlier estimate of $0.03–0.05 per drill was wrong by about 8x:
it assumed Nova Lite 1.0 pricing and undercounted tokens about 3x.

## Voice (Phase 4): measured per call

Token counts from Nova's own usage event on real deployed calls (`make voice-evidence`), priced
at the Price List rates above:

| Call | Speech in | Text in | Speech out | Text out | Nova 2 Sonic |
|---|---|---|---|---|---|
| Spike S2, full OK call, 57 s | 749 | 1,046 | 737 | 465 | $0.0126 |
| Browser e2e, full OK call (r04), 64 s | 686 | 1,208 | 800 | 485 | **$0.0134** |
| Browser e2e, urgent call (r02), 31 s | 382 | 1,189 | 437 | 211 | **$0.0074** |

So **about $0.013 per minute of call**, dominated by speech output. Around each call the
coordinator classifies the transcript and runs the dispatcher on Nova 2 Lite (a few thousand
tokens, roughly $0.005–0.02), and the voice runtime bills a few seconds of vCPU (it is idle
waiting on I/O most of the call, which AgentCore does not charge). Call it **≤ $0.06 for a
maximum-length 3-minute browser call**, all in.

**What a malicious visitor can run up** (voice caps in SSM `/doorstep/caps`):

- Every link needs a drill that reserved that resident for voice, a 60 s presigned URL, and a
  single-use token claimed before Nova is opened. A replayed or forged link costs a WebSocket
  accept and a DynamoDB read; a connection without a valid SigV4 signature is refused by AWS
  before our code runs ($0).
- Hard limits: 3 minutes per call, 25 s of silence ends it, 20 links per day for everyone, 150
  for the whole judging period, kill switch checked when the link is issued and when the call
  connects. Per-IP: `voice_per_ip_per_hour` = 4 (raised to 10 for Phase 4 testing, reset 2026-09-13).
- **Worst case: 20 × $0.06 = $1.20/day; 150 calls in total ≈ $9 for the judging period.**
  Rotating IPs does not change that: the daily and total caps are global. The one thing an
  attacker can do is use up the day's 20 links so judges see the "limit reached" message.
- Real phone calls and Telegram messages: $0. Browser tokens are refused for live incidents,
  and the sandbox never reaches a real channel (Cedar forbid + code checks).

Standing cost of voice: $0 idle (the `doorstep_voice` runtime bills only while a call is
connected; one more Lambda and route cost nothing at rest). No EC2.

## Forward exposure

Phases 2–7 are comfortable: a few gate drills plus roughly $3–5 per full `make evals` pass.

**The judging period is the real risk.** SPEC §14 caps the public sandbox at 30 drills/day, and
each visitor gets their own 12-resident drill:

    30 drills/day x $0.37 = $11.10/day x 24 days (Sep 14 - Oct 8) = ~$266

That exceeds the $135.51 remaining: at the cap, credits run out around **day 12 of a 24-day
judging period**, on a live public link, and spend then lands on a real card. A daily cap alone
does not bound the total. Before Phase 5 ships, add a **cumulative** sandbox cap (about 300
drills covers the whole period inside credits) alongside the daily cap and the kill switch.

## Phase 3 stack: standing monthly cost (stack `Doorstep`, us-east-1)

Estimated from list prices, idle (no drills running). AgentCore Runtime bills per second only
while a session runs; idle and I/O-wait time are not charged.

| Resource | Idle month |
|---|---|
| AgentCore Runtime `doorstep_coordinator` (no open sessions; idle timeout 300 s, max 2 h) | $0.00 |
| DynamoDB `doorstep`, on-demand, a few MB | ~$0.00 |
| S3 data bucket (`sessions/` expire after 30 days) | ~$0.00 |
| HTTP API (2 routes), $1 per million requests | ~$0.00 |
| Alert poller: 4,320 Lambda runs (256 MB arm64, ~1 s) + 4,320 Scheduler invocations | ~$0.02 |
| SSM: 13 standard parameters (free) + `aws/ssm` KMS decrypts | ~$0.00–0.03 |
| CloudWatch Logs, 14-day retention | ~$0.01 |
| ECR: runtime image in the CDK assets repo (~0.7 GB compressed deps layer, stored once) | ~$0.07 |
| **Total** | **≈ $0.10–0.15 / month** |

Per cloud drill: ≈ $0.38 (the measured $0.37 of Nova tokens + under $0.01 of runtime, DynamoDB
and spans). Not deployed on purpose: no NAT gateway, EC2, customer-managed KMS key or Secrets
Manager. `make poller ARGS=off` stops the only recurring invocation; `make destroy` removes the
rest (SSM parameters stay, and cost nothing).

## Ledger (newest first)

| Date | Item | Est. | Actual | Status |
|---|---|---|---|---|
| 2026-09-13 | Phase 4b: 4 real Twilio calls to the operator's phone (66 s, 68 s, 37 s, 42 s; $0.028 each per Twilio) + about 6 no-ring rehearsals and 2 cloud-browser checks | ≈ $0.20 | Twilio ≈ $0.11 | done |
| 2026-09-12 | Phase 4: Sonic spikes S1-S3 (~3 calls), 3 deployed browser calls (2 synthetic, 1 fake-mic browser), 1 voice drill incident (assessor + triage only) | ≈ $0.10 | not posted yet | done |
| 2026-09-12 | Phase 4: `doorstep_voice` runtime + `voice-session` Lambda + route (same image, no new ECR storage) | $0 idle | | active |
| 2026-09-12 | Phase 3: three cloud drills (1 auto-approve, 2 Telegram), one cloud restart test, probes | ≈ $1.20 | $0.84 posted so far (usage $5.31 − $4.47) | done |
| 2026-09-12 | Phase 3: stack `Doorstep` (runtime, 3 Lambdas with X-Ray tracing, HTTP API, schedule, table, bucket) | ≈ $0.10–0.15/month idle | | active |
| 2026-09-12 | Phase 3: 13 SSM parameters under `/doorstep` (standard tier) | $0 | | active |
| 2026-09-12 | Phase 3: ECR pushes retried after proxy timeouts; image slimmed (no duplicate venv layer) | cents | | done |
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
