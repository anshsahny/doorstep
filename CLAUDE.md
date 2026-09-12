# Doorstep — project guide for Claude Code

Doorstep is Ansh's entry for the AWS "Agents for Humans" hackathon on Devpost (Good Neighbor track), built with the **Strands Agents SDK** and deployed on **Amazon Bedrock AgentCore**.

- Hard deadline: **Mon Sep 14, 2026, 5:00 PM PDT**. Internal target: submitted by **12:00 PM PDT Monday**.
- Design rule: every hazard is a profile file (SPEC §3a); nothing hazard-specific is hard-coded. Heat is built and demoed; cold is optional (Phase 6b).
- Pitch: when a heat warning hits, Doorstep phones every at-risk neighbour on a volunteer group's list, sorts who is OK from who isn't, sends a volunteer to the doors that need a knock, and interrupts the block captain only for the decisions a human must make.

## Read at the start of every session

1. `docs/PROGRESS.md` — current phase, what's done, what's next, blockers, decisions.
2. `docs/PLAN.md` — the current phase's tasks, time box, gate, and cut line.
3. `docs/SPEC.md` — source of truth for product behaviour, agents, policies, data model, APIs.
4. `docs/SUBMISSION.md` — only when working on README, diagram, evals report, or Phase 7.

## How we work

- **One phase at a time.** Do not start the next phase until the current phase's gate passes and Ansh says "go".
- **Plan before editing.** At the start of a phase or large task, propose a short plan (files, commands, human steps needed, risks). If it deviates from PLAN.md, wait for approval.
- **Small, verified steps.** Run the relevant tests after every meaningful change. Prefer minimal, precise diffs over rewrites.
- **Close every work block** by updating `docs/PROGRESS.md` (done / verified how / next / blockers / decisions), running lint and tests, and summarizing for Ansh what was verified and how. Leave the changes in the working tree: Ansh commits each phase himself in one go. Claude never runs `git commit`, `git push` or `git reset`.
- **If the plan is wrong, stop.** Explain, propose the smallest change, and record the decision in PROGRESS.md.
- **Time boxes are real.** If a task runs 50% over its box, stop and offer the phase's cut line.
- **Human steps.** When something needs Ansh (console clicks, phone verification, secrets, recording), say exactly what to do, then wait.

## Verify APIs — never guess

Strands, AgentCore, and Nova 2 change quickly and are newer than your training data. Before using any Strands / AgentCore / Nova / Cedar API:

- search the `strands` MCP server (Strands docs) and the `agentcore` MCP server (AgentCore docs);
- if still unsure, read the installed package source or write a 10-line spike script and run it;
- never invent parameters, model IDs, or CLI flags. Look up model IDs with `aws bedrock list-inference-profiles --region us-east-1`.

Key docs:
- Strands: https://strandsagents.com/docs/ (interrupts, hooks, session management, Cedar authorization, Graph, BidiAgent, structured output, Evals)
- AgentCore: https://docs.aws.amazon.com/bedrock-agentcore/
- Nova 2 Sonic: https://docs.aws.amazon.com/nova/ (speech-to-speech, bidirectional streaming)
- Twilio Media Streams: https://www.twilio.com/docs/voice/media-streams
- NWS API: https://www.weather.gov/documentation/services-web-api

## Stack (ask before changing)

- **Python 3.12**, `uv`, `ruff`, `pytest`. TypeScript only for the web app.
- **Strands Agents SDK (Python)**: core, `[cedar]` extra, experimental bidi (voice), `strands-agents-tools`, Strands Evals.
- **Models (Bedrock, us-east-1)**: Nova 2 Lite (agents/reasoning), Nova Micro or Nova 2 Lite (simulated residents), Nova 2 Sonic (voice). **No Claude-on-Bedrock** (Marketplace billing isn't covered by credits).
- **AgentCore**: Runtime (coordinator), Memory (resident preferences), Observability. Stretch: Gateway + Policy.
- **AWS**: DynamoDB (single table), Lambda, API Gateway HTTP API, SQS, EventBridge Scheduler, S3, CloudFront, SSM Parameter Store, one EC2 t4g.small (voice bridge) behind CloudFront. IaC: **AWS CDK (Python)**.
- **Web**: React + Vite + TypeScript + Tailwind, Leaflet + OpenStreetMap.
- **Channels**: Twilio Voice (Media Streams) on the **`doorstep` subaccount only**; Telegram Bot API; browser voice.
- AWS CLI profile `doorstep`, region `us-east-1` (IAM user `doorstep-dev`; never use root credentials).

## Node version (important)

Ansh's global Node must stay on **v20** (Homebrew `node@20`) for his day job.
- Never change global Node, never run `brew link`/`brew unlink` on any node formula, never `npm install -g`.
- This project uses **Node 22** from `/opt/homebrew/opt/node@22/bin` (pinned in `.nvmrc`).
- Prefix every node/npm/npx command you run with `export PATH="/opt/homebrew/opt/node@22/bin:$PATH" &&`.
- The Makefile sets `export PATH := /opt/homebrew/opt/node@22/bin:$(PATH)` at the top.
- JS tools (`aws-cdk`, `@aws/agentcore`, Vite, etc.) are project devDependencies run via `npx` or npm scripts.
- If a command reports Node v20, stop and fix the PATH before continuing.

## Non-negotiable rules

**Secrets and accounts**
- Never commit secrets. `.env` is gitignored; commit `.env.example`. Deployed secrets live in SSM Parameter Store (SecureString). `gitleaks` runs pre-commit and in CI.
- Use only the Twilio **subaccount** SID/token. Never read, change, or call anything on the parent account (it runs OptoMize, a live product).

**Product safety**
- Doorstep never contacts emergency services itself. It tells the resident to call 911 and pages a human immediately.
- Real phone calls go only to numbers on the allowlist (SSM param `/doorstep/call_allowlist`), enforced **both** by Cedar policy and by a code check in the call path. Sandbox/public mode never places real calls or sends Telegram messages.
- No SMS through Twilio (A2P 10DLC). Text goes via Telegram and the dashboard.
- All resident data is synthetic and marked fictional. No real PII in the repo, logs, screenshots, or video.
- Keep health details minimal (e.g., "needs power for a medical device"). No diagnoses, no medications by name.

**Cost**
- Every public path that spends money has a per-IP rate limit, a global daily cap, and honours the `DOORSTEP_KILL_SWITCH` SSM flag.
- Tear down experimental resources. Keep a running tally in `docs/COST.md`.

**Hackathon rules**
- All code is written during the submission period. No pre-existing code. If you adapt an AWS/Twilio sample, record its URL and license in PROGRESS.md (it goes into the README "Disclosures").
- Name "Strands Agents" explicitly in the README, in the core agent module docstrings, and in the UI footer.

## Commands (keep this list current)

- `make setup` — install Python 3.12 via uv, sync all deps, install pre-commit hooks, `npm install` (Node 22)
- `make test` / `make lint` / `make fmt` / `make check` (lint + tests) / `make node-check`
- `make smoke-01` … `make smoke-05` — Phase 0 smoke tests (`ARGS=--audio`, `ARGS=--whoami`, `ARGS=teardown`); see `scripts/smoke/README.md`
- `make local-drill ARGS=--auto-approve` — run a drill locally with simulated residents (terminal board; `--transcripts`, `--report PATH`, `--no-board`, `--no-clear`; exit 0 = Gate 1 criteria met, 1 = not met, 2 = profile did not activate on the alert)
- `make deploy` — CDK deploy + AgentCore deploy
- `make evals` — run eval suites, write `evals/REPORT.md`
- `make web` / `make web-deploy`

## Repo layout

```
agent/        # doorstep_agent package: coordinator Graph, agents, tools, hooks, policies, memory
  policies/   # *.cedar policy files + tests
voice/        # voice bridge: FastAPI + Strands BidiAgent (Nova 2 Sonic), Twilio + browser IO adapters
api/          # Lambda handlers: dashboard API, Telegram webhook, Twilio webhooks, alert poller, check-in worker
web/          # React dashboard
infra/        # CDK app
data/         # synthetic roster, volunteers, cooling centres, alert fixtures (all fictional/labelled)
evals/        # personas, scenario suites, runner, REPORT.md
scripts/      # smoke tests, seed, replay, utilities
docs/         # PLAN, SPEC, PROGRESS, SUBMISSION, COST, architecture diagram, blog drafts
tests/
```

## Definition of done (any task)

Code and tests pass locally. A manual check is done for UI or voice changes. PROGRESS.md is updated. Changes are left in the working tree for Ansh to commit.
