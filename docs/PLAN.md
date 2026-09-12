# Doorstep — build plan

All times PDT. The deadline is Mon Sep 14, 5:00 PM; we submit by 12:00 PM Monday. About 40 working hours total.

Each phase has a **goal**, **time box**, **tasks** (H = Ansh does it, C = Claude Code does it), a **gate** that must pass before moving on, and a **cut line** if we're behind.

## Schedule

| When | Phase | Hours |
|---|---|---|
| Thu Sep 10, evening | 0. Accounts, smoke tests, scaffold | 2.5 |
| Fri Sep 11, 6 PM–12 AM | 1. Domain core + text-mode agents (local) | 5 |
| Sat Sep 12, 9 AM–1 PM | 2. Human-in-the-loop: interrupts, persistence, Telegram | 4 |
| Sat Sep 12, 1 PM–7 PM | 3. Cloud: AgentCore + AWS backend | 5 |
| Sat Sep 12, 8 PM–11 PM | 4a. Voice in the browser | 3 |
| Sun Sep 13, 9 AM–1 PM | 4b. Voice on the phone (hard stop 1 PM) | 4 |
| Sun Sep 13, 1 PM–6 PM | 5. Dashboard + judge sandbox | 5 |
| Sun Sep 13, 6 PM–12 AM | 6. Evals, hardening, README, diagram | 5 |
| Sun Sep 13, 10 PM–12 AM (optional) | 6b. Cold profile — only if Gate 6 passed | ≤2 |
| Mon Sep 14, 7 AM–12 PM | 7. Video, blog 3, Devpost submission | 5 |

Blog posts run alongside:
- **Post 1:** publish Saturday morning.
- **Post 2:** publish Sunday night.
- **Post 3:** publish Monday morning.

Publish early: builder.aws runs an automated moderation scan, and the publish button has been flaky for some people.

**Never cut:**
- End-to-end drill
- Interrupts with human decisions
- Cedar policies
- Evals report
- Live demo link
- README, license, and architecture diagram
- Video ≤5 min
- Three blog posts

---

## Phase 0 — Accounts, smoke tests, scaffold (Thu, 2.5 h)

**Goal:** prove every external dependency works before writing product code.

Human setup (H). ✅ = already done before Phase 0 started:
1. ✅ AWS account (Free plan), root MFA, $5 monthly budget alert.
2. ✅ IAM user `doorstep-dev` (AdministratorAccess) with a CLI access key; `aws configure --profile doorstep` done. `aws sts get-caller-identity` shows `user/doorstep-dev`.
3. ✅ Local tools: `uv`, `awscli`, `gitleaks`, `gh` (logged in), `portaudio`, Docker Desktop (hello-world passes), `node@22` (project-only; global stays Node 20).
4. ✅ Private GitHub repo `doorstep` with MIT LICENSE; kit committed.
5. ☐ AWS Builder ID + builder.aws alias; "Join hackathon" on Devpost.
6. ☐ Bedrock check: console as `doorstep-dev`, us-east-1, Playground, Nova 2 Lite says hello. If Bedrock quotas show 0 or AgentCore is blocked, upgrade to the Paid plan (credits carry over) and file a quota case.
7. ☐ Twilio (existing paid account):
   - Create subaccount `doorstep` and buy one US local number.
   - Set a usage trigger at $10.
   - Voice geo permissions: US and Canada only.
   - Put the subaccount SID and token in `.env`.
8. ☐ Telegram:
   - Create the bot via @BotFather.
   - Get your chat ID.
   - Get a second Telegram account (friend or family) to play "volunteer".
9. ☐ ngrok free account and auth token (for the Twilio stream smoke test).

Build tasks (C):
1. Scaffold the repo per CLAUDE.md layout:
   - pyproject (uv), ruff, pytest
   - Makefile
   - pre-commit with gitleaks
   - `.env.example`
   - GitHub Actions stub (lint + unit tests + gitleaks)
2. Verify current install names via MCP: Strands, `[cedar]` extra, bidi extra, Strands Evals, AgentCore CLI/SDK.
3. Write and run five smoke scripts in `scripts/smoke/`:
   - `01_nova_lite_agent.py` — a Strands Agent on Nova 2 Lite calls a toy tool and returns structured output.
   - `02_nova_sonic_bidi.py` — a BidiAgent on Nova 2 Sonic holds a 2-turn text or local-audio exchange.
   - `03_agentcore_hello/` — deploy a minimal Strands agent to AgentCore Runtime and invoke it with a session ID.
   - `04_twilio_stream.py` — place a call to Ansh's allowlisted number. TwiML plays a short greeting, then `<Connect><Stream>` to a local WebSocket (via ngrok). Log the `start` and `media` events.
   - `05_telegram_ping.py` — send a message with inline buttons, then receive the button tap by long polling.

**Gate 0:**
- All five smoke scripts pass, with results recorded in PROGRESS.md.
- Repo exists (private) with LICENSE, `.gitignore`, and the kit committed.
- gitleaks hook blocks a fake key in a test commit.

**Contingencies:**
- If Bedrock is blocked, upgrade the plan or open a case, and keep going locally with retries.
- If the AgentCore quota is 0, open a case immediately and continue locally. Phase 3 falls back to a Lambda container if it isn't approved by Saturday 1 PM.
- If Nova 2 Sonic fails, try us-west-2 (it's available there). Voice stays the only at-risk feature.

---

## Phase 1 — Domain core + text-mode agents, local (Fri, 5 h)

**Goal:** a complete incident runs in the terminal with simulated residents. No cloud and no voice yet.

Tasks (C):
1. **Data** (`data/`, all fictional and labelled):
   - `roster.json`: 48 residents in a fictional SE Portland block team, "Juniper Court" (one senior building plus surrounding blocks). Coordinates are jittered; the fields follow SPEC §10. Mix of languages (~6 Spanish, ~2 Cantonese marked "English with interpreter preference"), ~40% living alone, ~35% without AC, a few needing power for medical devices.
   - `volunteers.json`: 6 volunteers plus 1 captain, with Telegram chat IDs from env.
   - `relief_centres.json`: 5 real public places (libraries or community centres) near the fictional block, each tagged `cooling` and/or `warming`, labelled "sample — verify with 211 / county before real use".
   - `alerts/2021-06-pqr-excessive-heat-warning.json`: the real June 2021 NWS Portland (PQR) Excessive Heat Warning text (VTEC code `EH`, the pre-2025 name). Fetch it from the Iowa Environmental Mesonet VTEC archive and record the source URL. Fall back to NWS text archives.
   - `agent/profiles/heat.yaml` per SPEC §3a, plus a profile loader. Scoring, prompts, red-flag rules, tips and relief-centre kind all read from the profile. Nothing heat-specific is hard-coded in agents or tools.
2. **Models:** Pydantic models for Resident, Volunteer, Incident, ResidentCase, CheckinResult, Decision, AuditEvent.
3. **Store:** a repository interface with an in-memory backend now and DynamoDB in Phase 3.
4. **Deterministic core:**
   - Risk scoring (SPEC §6.2).
   - Incident state machine (SPEC §6.3), including retry timers and a drill time-compression factor.
5. **Agents** (SPEC §6, Nova 2 Lite):
   - `alert_assessor`: structured output.
   - `triage`: tools for roster and risk score, producing call waves.
   - `checkin_text_agent`: the check-in protocol in text form, using the same script as voice.
   - `classifier`: structured CheckinResult, with a deterministic red-flag backstop that can only upgrade severity, never downgrade.
   - `dispatcher`: tools per SPEC §7.
   - A Strands **Graph** for incident start (assess → triage → outreach).
6. **Policies:**
   - `agent/policies/*.cedar` per SPEC §8, wired with Strands `CedarAuthorization`.
   - A `context_enricher` that computes mode, allowlist, consent, attempts, local hour, and recipient role.
   - Schema validation from tool definitions.
7. **Audit hook:** every tool call, policy decision (allow or deny, with reason), and model rationale becomes an AuditEvent.
8. **Simulated residents:** a persona agent (Nova Micro) driven by `evals/personas/*.yaml`. Start with 12 personas: 7 OK, 2 needs-help, 2 urgent (one explicit, one hidden), 1 no-answer.
9. **CLI:** `make local-drill` replays the 2021 alert for 12 residents. It shows a live terminal board plus pending decisions, with an `--auto-approve` flag.

**Gate 1:**
- A test loads a minimal dummy profile and confirms the questions, risk weights and red-flag rules change with no code changes.
- `pytest` unit tests green, covering:
  - risk scores
  - every state-machine transition
  - at least 12 Cedar tests, including denials: non-allowlisted call, sandbox call, group broadcast containing resident details, agent recording an emergency call
- `make local-drill` meets all of:
  - all 12 cases reach a terminal state
  - both urgent personas are escalated
  - zero policy violations
  - the audit log explains each decision
  - finishes in under 4 minutes

**Cut line:** trim dispatcher tools to escalate, assign volunteer, and cooling-centre info.

---

## Phase 2 — Human-in-the-loop (Sat 9 AM–1 PM, 4 h)

**Goal:** the agent pauses for a human and resumes correctly, even after a restart.

Tasks (C):
1. **Strands interrupts:**
   - On `escalate_to_captain` (tool interrupt).
   - On `assign_volunteer` for high-risk cases (a `BeforeToolCallEvent` hook).
   - Interrupt names are namespaced (`doorstep-…`).
2. **Session persistence:**
   - Use a Strands session manager: file-based locally, S3 in Phase 3.
   - Session ID is derived from incident and case.
   - A pending Decision record is stored with its options.
3. **Resume path:** `respond_to_decision(decision_id, response, responder)` builds the `interruptResponse` and re-invokes the agent.
4. **Telegram bot:**
   - Captain messages with inline buttons (SPEC §4).
   - Volunteer task messages ("On my way", "They're OK", "Need more help").
   - Callback handling, with responder identity checked against the roster roles.
   - Long polling locally; webhook mode in Phase 3.
5. **Web decision path:** the same `respond_to_decision` behind an API function, used later by the dashboard.

**Gate 2:**
- An automated test raises an interrupt, discards the agent object, recreates it from the persisted session, responds, and confirms the tool runs exactly once.
- Manual check:
  1. Run a local drill with Telegram.
  2. The captain approves a door-knock on their phone.
  3. The volunteer account taps "They're OK".
  4. The case closes, and the timeline shows who decided what and when.

**Cut line:** volunteer replies limited to two buttons.

---

## Phase 3 — Cloud (Sat 1 PM–7 PM, 5 h)

**Goal:** the same drill runs in AWS, triggered by a real alert poller or a replay.

Tasks (C):
1. **CDK stack:**
   - DynamoDB single table + GSI (SPEC §10)
   - S3 (sessions, web, eval artifacts)
   - SQS `checkin-jobs` with DLQ; retries use DelaySeconds
   - Lambdas: `api`, `telegram_webhook`, `twilio_voice`, `twilio_status`, `alert_poller`, `checkin_worker`
   - API Gateway HTTP API with throttling
   - EventBridge Scheduler: poller every 10 minutes
   - SSM parameters
2. **Coordinator on AgentCore Runtime:**
   - An entrypoint that accepts `{incident_id, event}`.
   - Runtime session ID derived from the incident (check the minimum length in the docs).
   - An IAM role with least privilege.
   - Lambdas invoke the runtime.
3. **AgentCore Memory:**
   - Store per-resident preferences, e.g., "hard of hearing — speak slowly", "call daughter if no answer", preferred language.
   - Read at triage and check-in; write after the incident closes.
4. **Observability:** enable AgentCore Observability (OTEL) and confirm traces appear in CloudWatch. Screenshot them for the video and blog.
5. **Alert poller:** calls the NWS `/alerts/active?point=…` for the org's location with a proper User-Agent. Adds a `POST /admin/replay` endpoint (passcode) for the 2021 fixture.
6. **Telegram:** switch to webhook mode with the secret-token header check.

**Gate 3:**
- `make deploy` works from a clean clone.
- `scripts/replay_cloud.py` runs a 12-resident drill in the cloud end to end, including a Telegram approval via webhook.
- Traces are visible.
- `docs/COST.md` shows under $3 spent so far. *(Amended 2026-09-12: gross usage was already $4.49 before Phase 3 — net $0 — so this reads "Phase 3 adds under $3 gross, net out-of-pocket $0, idle stack under $1/month".)*

**Cut line:** if it's past 7 PM, Memory moves to Phase 6 (it's nice-to-have).

*Taken 2026-09-12: Memory moved to Phase 6; `checkin-jobs`, `checkin_worker`, `twilio_voice` and `twilio_status` moved to Phase 4 (Ansh).*

---

## Phase 4 — Voice (Sat 8–11 PM browser; Sun 9 AM–1 PM phone)

**Goal:** a real voice check-in that feeds the same pipeline as text.

Tasks (C):
1. **Voice bridge:** `voice/`, FastAPI with WebSockets.
   - Strands BidiAgent on Nova 2 Sonic runs the check-in protocol (SPEC §9).
   - Tools: `record_answer`, `flag_urgent` (fires immediately mid-call using async tool calling), `end_call`.
   - The final transcript and structured result are posted to `/internal/checkin-result` with an HMAC signature.
2. **Browser IO adapter (4a):**
   - Mic capture as PCM16 in an AudioWorklet, with playback and barge-in.
   - A short-lived signed session token.
   - 3-minute cap per session; daily cap.
3. **Twilio IO adapter (4b):**
   - The `twilio_voice` Lambda returns TwiML with `<Connect><Stream>` and a `<Parameter name="token">`. Twilio stream URLs don't take query strings; confirm this in the docs.
   - The bridge validates the token on the `start` event.
   - Converts μ-law 8 kHz to and from the model's PCM rates (check the BidiAgent IO docs).
   - Handles barge-in via the `clear` message.
   - Handles no-answer, busy and voicemail from status callbacks: `machineDetection` → NO_ANSWER plus a retry.
4. **Outbound calls:** `place_checkin_call` → SQS → `checkin_worker` → Twilio REST create-call on the **subaccount**. The allowlist is checked in code, in addition to Cedar.
5. **Hosting:**
   - EC2 t4g.small (Amazon Linux 2023, Docker, systemd) with an instance role for Bedrock.
   - Behind CloudFront with WebSockets enabled.
   - The origin security group allows only CloudFront's origin-facing prefix list, and requests must carry a secret origin header.
   - Fallback for recording: ngrok static domain.
6. **Language:** residents with `language=es` get the Spanish protocol, with the same flow and the same tools.

**Gate 4a:** 3 consecutive successful browser check-ins. Each result shows in the incident within 5 seconds of hang-up.

**Gate 4b:**
- 2 consecutive real calls to Ansh's phone complete.
- In a third call, saying a red-flag phrase mid-call ("I feel dizzy and confused") sends the captain's Telegram alert **before** hang-up.

**Hard stop:** if the phone path isn't passing by **Sun 1 PM**, ship browser voice only. Record the decision and move on.

---

## Phase 5 — Dashboard + judge sandbox (Sun 1–6 PM, 5 h)

**Goal:** a judge who never talks to us can understand and try Doorstep in under 4 minutes.

Tasks (C):
1. **Design pass** per SPEC §13, in two passes:
   - First pass: tokens and wireframes.
   - Review them against the brief and revise anything generic.
   - Second pass: build.
2. **Screens:**
   - **Home:** what it is, who it's for, a "Run a drill" button.
   - **Live board:** door grid plus map, counters, filters.
   - **Resident panel:** transcript, classification, timeline with the agent's reasoning and policy decisions.
   - **Decisions inbox:** the same interrupts as Telegram.
   - **Policies:** plain English next to the Cedar text, plus recent denials.
   - **Incident report:** metrics, human decisions vs. automated actions, phone-tree baseline.
   - **Evidence:** eval results.
3. **"Answer a call as a resident" button:** a judge talks to the agent via browser voice, and the result flows into their drill.
4. **Sandbox mode:**
   - Each visitor gets their own drill with 12 residents.
   - Per-IP limit (1 drill per 10 minutes); global cap (30 drills and 20 voice sessions per day); kill switch.
   - No real calls or Telegram messages in sandbox.
   - "Captain mode" for the live demo sits behind a passcode, which goes in the testing instructions.
5. **Deploy:** S3 + CloudFront. The footer reads: "Built with Strands Agents on Amazon Bedrock AgentCore".

**Gate 5:**
- In fresh incognito sessions on both laptop and phone: run a drill, answer a call by voice, approve a decision, and read the report, all in under 4 minutes with no console errors.
- Lighthouse accessibility score of 90 or higher.
- Keyboard navigation works.

**Cut line:** map → list plus door grid only.

---

## Phase 6 — Evals, hardening, docs (Sun 6 PM–12 AM, 5 h)

**Goal:** evidence that it works, and a repo a stranger can run.

Tasks (C):
1. **Evals** per SPEC §12, with Strands Evals.
   - Run all four suites:
     - 40-persona check-in classification
     - Dispatcher trajectories
     - Red team
     - 2021 replay backtest
   - `make evals` writes `evals/REPORT.md` and a JSON file the dashboard reads.
   - Fix failures and re-run. Keep before/after numbers for blog post 3.
2. **Hardening:**
   - Idempotency on webhooks.
   - Retries with backoff; DLQ alarm.
   - Graceful Twilio failures.
   - Cost caps verified by a script.
   - CI green: lint, unit tests, gitleaks.
3. **Docs:**
   - README per the SUBMISSION.md outline.
   - Architecture diagram: draw.io source plus PNG, containing everything the FAQ lists.
   - `docs/COST.md` and the Disclosures section.
   - Testing instructions.
4. **Clean-clone test:** Claude follows the README in a fresh directory and fixes any gaps.
5. **Go public:**
   - Run gitleaks on the full git history first.
   - Switch the repo to public and confirm the About section shows "MIT".
   - Add topics: strands-agents, bedrock-agentcore, agents-for-humans.

**Gate 6:**
- Red-flag recall is 100% on suite 1.
- Zero policy violations in the red team.
- CI is green.
- The clean-clone setup succeeds.

---

## Phase 6b — Optional: cold profile (only if Gate 6 passes by 10 PM Sunday; max 2 h)

**Goal:** prove the hazard-profile design with a second real hazard, without touching the heat demo.

1. Add `agent/profiles/cold.yaml` per SPEC §3a:
   - red flags in EN/ES, including carbon-monoxide risk
   - `warming` tags added in `relief_centres.json`
2. Add 8 cold personas: 4 OK, 2 needs-help, 2 urgent (one hypothermia, one using an oven for heat with a headache).
3. Add a replay fixture: a real archived NWS Extreme Cold Warning (or pre-Oct-2024 Wind Chill Warning) from the IEM archive, with the source URL recorded.
4. Dashboard: a profile badge on the incident, and a "Run a cold drill" option.

**Gate 6b:**
- Both cold urgent personas are escalated.
- Zero policy violations.
- The heat eval numbers are unchanged. Re-run suite 1 to confirm.

**Skip rule:** if Gate 6 isn't passed by 10 PM Sunday, skip this phase entirely. Mention cold as roadmap only.

---

## Phase 7 — Launch (Mon 7 AM–12 PM)

H and C together, per SUBMISSION.md:
1. **Freeze and deploy:** tag `v1.0`; health-check every link.
2. **Video:**
   - Record per the script (real phone call on camera).
   - Edit to 4:30–4:55.
   - Upload publicly to YouTube with captions.
3. **Blog:** publish post 3 and confirm posts 1 and 2 are public.
4. **Devpost:**
   - Fill every field.
   - Track: Good Neighbor.
   - Add your Builder ID email, screenshots, the diagram, the live link, the repo, and the video.
   - Submit by 12 PM, then re-open the submission page to confirm.
5. **After submitting:**
   - Keep the infrastructure running until the judging period ends on **Oct 8**.
   - Leave the `main` branch frozen; do any further work on a branch.
   - Keep monitoring the budget alarm.
