# Doorstep — progress log

Current phase: **2 — Human-in-the-loop (interrupts, persistence, Telegram)**: ✅ **complete, Gate 2
passed** (both halves). Ready for Ansh to commit on `phase2` and say "go" for Phase 3.
Next gate: **Gate 3**
Time now vs plan: Phase 0 ran Fri Sep 11 00:00–12:35 PDT (planned Thu evening). Phase 1 ran Fri
17:45–21:20 PDT, about 3.5 h of its 5 h box. Phase 2 started Sat 08:30 PDT; about 2.5 h of its
4 h box used at the hand-off point, so Phase 3 (Sat 1 PM) is on schedule.

## Setup completed before Phase 0

- AWS account + root MFA + $5 budget; IAM user `doorstep-dev`; CLI profile `doorstep` verified with `aws sts get-caller-identity`.
- Tools: uv, awscli, gitleaks, gh (authenticated), portaudio, Docker Desktop (hello-world OK), node@22 for this project (global Node stays v20).
- Private repo `doorstep` with MIT LICENSE; kit committed.

## Gates

| Phase | Gate | Status | Verified how | Date |
|---|---|---|---|---|
| 0 | 5 smoke tests, private repo + LICENSE + kit committed, gitleaks | ✅ passed | All five smoke scripts exit 0 with results in the table below (05 Telegram, 04 Twilio, 01 Nova 2 Lite, 02 Nova 2 Sonic, 03 AgentCore Runtime). Private repo with MIT LICENSE, `.gitignore` and kit committed on `main`; Phase 0 work on branch `phase0`. gitleaks: the pre-commit hook blocked a commit containing a fake `AKIA…` key (rule `aws-access-token`, commit aborted, HEAD unchanged). | 2026-09-11 |
| 1 | Unit tests + local 12-resident drill | ✅ passed | `make check` green: ruff clean, 158 unit tests. They cover the drill setup, the deterministic graph gates (activation, plan, outreach), the dummy-profile test (questions, weights, red flags, needs and relief kind change with no code change), a hazard-word guard over the whole agent package, every risk factor and wave rule, every state-machine transition plus retries, 12 backstop tests incl. a 300-case property test (never lowers, never drops flags, both disagreement kinds logged), 29 Cedar cases through the real `CedarAuthorization` handler (non-allowlisted call, sandbox real-channel call, broadcast with resident details, agent recording an emergency call, plus quiet hours/Extreme, attempts cap, consent, distance, availability, unknown tool, schema typos), audit hook, tool code checks, personas, violations. `make local-drill ARGS=--auto-approve`: 12/12 cases RESOLVED with the expected classification for every persona, both urgent personas escalated (r01 explicit via `flag_urgent`, r02 hidden via the backstop), Harold escalated after 3 unanswered attempts, 5 decisions, 13 messages recorded, 139 audit events, 0 policy violations (1 attempt denied by Cedar and audited with the deciding facts), 53.6 s wall-clock on the final tree (49.7 s the run before). Report in `evals/drill_report.json`. | 2026-09-11 |
| 2 | Interrupt resume test + Telegram approval loop | ✅ passed | **Automated (the real gate):** `tests/test_restart_resume.py` raises an interrupt in one subprocess which then exits, rebuilds from the persisted session in a second interpreter that has only the session directory and the decision record, answers it, and asserts the volunteer task was sent **exactly once** (by process B) and that a second tap returns `already_answered` without re-running anything. `make check` green: ruff clean, **206 tests** (was 165), all offline via a `ScriptedModel` double so they run in CI. Identity: a non-roster chat, a volunteer answering a captain decision, and a volunteer answering another volunteer's task are all refused and audited. Idempotency: double tap, tap-after-expiry, changed choice on a second tap, and a redelivered Telegram `update_id` all leave exactly one effect. Minimal disclosure swept over all 12 drill residents. `make local-drill ARGS=--auto-approve` PASS on the new machinery: 12/12 RESOLVED, both urgent personas escalated, 0 violations, 100.3 s. **Manual (Ansh's phone, `make telegram-drill`):** 12 taps over 8 decisions in 154 s — one decision tapped five times (four `already_answered`), one expired then tapped (`expired`), every tap attributed to `captain:cap-maria` through a chat shared with `vol-tom`, 0 violations, 12/12 settled. The run surfaced two real bugs (lost "Send Sam" decisions; a `9999 km` label) and a TTL design error; all three fixed and covered by tests before the gate was marked. | 2026-09-12 |
| 3 | Cloud drill via AgentCore + Telegram webhook + traces | ☐ | | |
| 4a | 3 browser check-ins | ☐ | | |
| 4b | 2 real calls + mid-call escalation | ☐ | | |
| 5 | Judge flow < 4 min, Lighthouse a11y ≥ 90 | ☐ | | |
| 6 | Evals targets, CI green, clean-clone setup | ☐ | | |
| 7 | Submitted | ☐ | | |

## Smoke tests (Phase 0)

| # | Test | Result | Notes |
|---|---|---|---|
| 1 | Nova 2 Lite Strands agent | ✅ PASS 2026-09-11 | model ID used: `us.amazon.nova-2-lite-v1:0`. The agent called the `heat_index` tool once (98 °F, 60 % RH → 122.6 °F) and returned a validated `HeatIndexReport` via `structured_output_model`: danger_level `extreme_danger`, advice sentence. 2,773 tokens, 1.7 s. |
| 2 | Nova 2 Sonic BidiAgent | ✅ PASS 2026-09-11 | model ID: `amazon.nova-2-sonic-v1:0` (on-demand, us-east-1, no `us.` profile). Speech mode: two user turns synthesized with macOS `say`, streamed as 16 kHz PCM16; Sonic transcribed them ("hello this is a smoke test can you hear me", "great what is two plus two") and answered with speech plus final transcripts ("I can hear you loud and clear.", "Two plus two is four."), 18 and 17 audio chunks back, 15 s total. `--text` mode (cross-modal text over a silent audio stream) also passed. |
| 3 | AgentCore Runtime hello | ✅ PASS 2026-09-11 | npm `@aws/agentcore` 0.28.1 CLI, CodeZip build, target `default` (us-east-1). `agentcore deploy -y` bootstrapped CDK and created stack `AgentCore-DoorstepHello-default` with runtime `DoorstepHello_hello-7LKoIm5pUj` (Strands agent on Nova 2 Lite). Two invocations with one 36-char session ID: "remember the word juniper" → `OK`; "which word?" → `juniper`. `agentcore status`: Runtime READY. Torn down afterwards (see log); the CDK bootstrap stays for Phase 3. |
| 4 | Twilio subaccount stream call | ✅ PASS 2026-09-11 | Guards passed: credentials are the `doorstep` subaccount (owner SID differs; Full, active), the from-number is owned by it, the callee is on the allowlist. Ansh answered; `<Connect><Stream>` through ngrok delivered `connected`, `start` (audio/x-mulaw, 8000 Hz, 1 channel, customParameters token + purpose) and 150 media frames of 160 bytes in 3 s. The server closed the stream, TwiML resumed, the call completed after 12 s. Two calls placed: the first run failed only on an over-strict `stop` criterion (Twilio sends `stop` only when it closes the stream first); criterion fixed, rerun passed. |
| 5 | Telegram ping + button | ✅ PASS 2026-09-11 | Bot `@doorstep_agent_bot`; captain chat ID from the `--whoami` step. Message with 3 inline buttons sent; the `I'm handling it` callback arrived 7.5 s later via long polling, was answered, and the message was edited. First run timed out at 120 s with no tap; second run passed. |

## Log (newest first)

### 2026-09-12 about 11:00 PDT — Phase 2 built; automated half of Gate 2 passes
- Spikes first, because the whole design turns on one distinction (both scripts kept in
  `scripts/spikes/`, both PASS):
  - **Spike A** — `tool_context.interrupt` vs `BeforeToolCallEvent.interrupt`. Proven in the
    installed SDK: a tool interrupt re-runs the tool body **from the top** on resume (the
    pre-interrupt lines execute twice), while a hook interrupt returns from the executor
    **before the tool runs at all** and runs it exactly once on approval, or never on
    `cancel_tool`. Interrupt id is `v1:{tool_call|before_tool_call}:{toolUseId}:{uuid5(name)}`,
    so it is stable across processes.
  - **Spike B** — `SnapshotSessionManager` + `LocalFileStorage` round-trips interrupt state,
    including `pending_tool_execution`, across a real process boundary.
- Design following from that: `escalate_to_captain` raises a **tool** interrupt (its only
  pre-interrupt work is one idempotent upsert, and paging the captain *is* the escalation);
  `assign_volunteer` is interrupted by **`ApprovalHook`** at admission, because its side effect is
  a resident's details leaving the building and must not happen and then be regretted.
- Built: `decisions.py` (the single `respond_to_decision` path both Telegram and the Phase 5 web
  app use — identity, idempotency, expiry, resume), `approvals.py`, `sessions.py`, `messages.py`
  (every human-facing string in one place), `notify/` (Notifier protocol, RecordingNotifier,
  TelegramNotifier). `Decision` gained `draft|pending|answered|expired`, `audience`, `tool_use_id`,
  `interrupt_id`, `session_id`, `expires_at`, `delivery[]`.
- Verified how: `make check` green (ruff clean, **205 tests**, up from 165). New suites:
  `test_restart_resume.py` (**the Gate 2 test**: two real subprocesses, the tool runs exactly once
  and the second tap is recognised), `test_interrupt_resume.py` (7), `test_decisions.py` (14),
  `test_messages.py` (7), `test_telegram.py` (11). All offline via a `ScriptedModel` double
  (the SDK ships no mock provider), so they run in CI.
  `make local-drill ARGS=--auto-approve` PASS on the new machinery: 12/12 RESOLVED, both urgent
  personas escalated, 0 violations, 96.4 s, 8 decisions including the full
  approve-door-knock → volunteer-task chain.
- Found and fixed while running the live drill: after the captain chose "I'm handling it", the
  dispatcher closed one case and **silently forgot two others** (r01, r02 left ESCALATED with no
  outcome), because carrying out the choice was left to the model. The deterministic half of a
  decision now runs in code after the resume (`_apply_directly`, idempotent), so the model gets
  its turn and the captain's choice happens either way. Re-ran: all 12 RESOLVED.
  `tests/test_interrupt_resume.py::test_i_am_handling_it_closes_the_case_even_if_the_model_forgets`
  pins it with a model that never calls `close_case`.
- Minimal-disclosure fix: `_volunteer_brief` was interpolating `result.key_quote`, so the most
  sensitive sentence of a call would have been texted to a volunteer. Removed. The rule is now
  written down and swept over the whole roster by `test_messages.py`: **standing facts vs today's
  call** — consented roster notes travel (they change how a volunteer knocks), the resident's own
  words do not.
- Also: the dispatcher prompt now forbids guessing a resident's pronouns from their name (the
  roster records none); drills never send Telegram unless `--telegram` is passed.
- Not done: the manual Telegram loop (needs Ansh's phone) and `docs/SPEC.md` amendments for the
  seven issues listed below.

### 2026-09-12 about 18:00 PDT — Gate 2 manual half passed; the run found two real bugs
- Ansh ran `make telegram-drill` and answered on his phone. **12 taps, 8 decisions, 0 violations,
  12/12 settled, 154 s.** He tapped one decision **five times** (four `already_answered`), let one
  expire and then tapped it (`expired`), and every tap resolved to `captain:cap-maria` through a
  chat shared with `vol-tom`. Stronger evidence than the scripted plan asked for.
- **Bug 1 (serious): the captain chose "Send Sam" for both urgent residents and nobody was sent.**
  `_apply_directly` only carried out `resolve`, `escalate` and `acknowledge`; `assign_volunteer`
  fell through to the model, which dropped it — the same class of failure fixed that morning for
  "I'm handling it", in the branch that had not been covered. The real work is now
  `tools.send_volunteer_task`, called by the tool *and* by `decisions._apply_directly`, and
  idempotent so it is safe after a model that already assigned. Two tests cover it.
  Verified: a re-run sends `vol-priya` and `vol-tom` their tasks and both cases reach RESOLVED.
- **Bug 2: "Send Sam (9999 km)" on the captain's phone.** `ApprovalHook` read
  `volunteer_distance_km` out of `_cedar_session`, which belongs to whichever tool call wrote it
  last — a preceding `find_nearest_volunteers` (no `volunteer_id`) leaves the enricher's 9999
  sentinel there. The hook now measures the distance itself.
- **Decision TTL was being compressed, and should not be.** Compression exists to speed up the
  agent's own timers (retries, re-checks). A human's thinking time is not one of them: a
  15-minute deadline became 30 seconds, and the drill expired a decision 7 seconds before Ansh's
  thumb landed while 8 arrived at once. `expires_at` is now real time, never compressed, with
  `--decision-ttl` (real minutes) to demonstrate expiry deliberately.
- Verified how: `make check` green (**209 tests**); `make local-drill ARGS=--auto-approve` PASS,
  12/12 RESOLVED, 0 violations, 100.3 s, with volunteer tasks actually delivered.

### 2026-09-12 about 11:30 PDT — budget alert investigated: $0.00 out of pocket, but two real findings
- The `doorstep-monthly` $4.25 alert fired. Cause: the budget has no cost-type filters, so it
  tracks **gross usage before credits**. Measured in Cost Explorer: usage $4.4749, credits
  -$4.4749, **net $0.00**. Credits: $140 granted, $135.51 remaining, expiring Sep 2027. Nothing is
  running continuously — only the `CDKToolkit` bootstrap stack; no AgentCore runtimes, no EC2, and
  Sep 9-11 show $0.00.
- Finding 1: **a drill costs about $0.37, not $0.03–0.05.** 12.3M Nova 2 Lite input tokens over
  roughly a dozen drills. Nova 2 Lite input is $0.33/1M (5.5x Nova Lite 1.0) and is 90% of all
  spend. `docs/COST.md` corrected with measured rates.
- Finding 2: **no Nova 2 Sonic line item exists at all.** Eight Phase 0 voice sessions produced no
  billable usage record. Phase 4 runs entirely on Sonic, so its cost is an unknown; measure it on
  the first real call in Phase 4 before sizing any sandbox cap.
- Consequence for Phase 5 (recorded now, acted on then): SPEC §14's 30 drills/day sandbox cap is
  $11.10/day, about $266 over the 24-day judging period, which exceeds the remaining credits
  around day 12 — on a live public link. A daily cap does not bound the total; a **cumulative**
  cap (~300 drills) is needed alongside it.
- Changed how we work: full 12-resident drills only at a gate. The 205 offline tests cover the
  logic at $0, and two live drills were run today where one would have done.

### 2026-09-12 about 08:30 PDT — Gate 1 was flaky: a skipped protocol question hid an urgent case
- Found: a verification drill FAILED the gate. Walter (r02, the hidden-urgent persona) came out OK. The
  check-in agent had skipped question 2, "How are you feeling right now?", jumping from the greeting
  straight to the air-conditioning question because Walter volunteered "air conditioner's on, got water"
  in his first turn. His persona only reveals the confusion when asked directly, so the red-flag phrase
  was never spoken, the classifier correctly saw nothing, and the phrase backstop had nothing to match.
  The backstop can only catch what the resident actually says. In the same run Anita (r11) hung up
  mid-question and the classifier called that URGENT, which its own rules define as UNCLEAR.
- Fixed in three places, smallest first:
  1. The check-in prompt now says to ask EVERY question aloud, in order, one at a time, even when the
     resident seems to have answered already, "because people downplay how they feel until asked".
  2. The classifier prompt says a call that ended early is UNCLEAR, never URGENT; URGENT needs a red flag
     or a stated emergency in the resident's own words.
  3. A deterministic protocol-completion check: questions may be marked `required: true` in the profile
     (the shared "feeling" question is), and if a call ends with no answer to one, an OK result becomes
     UNCLEAR, which the state machine retries rather than closing the case. It never touches NEEDS_HELP or
     URGENT, so like the phrase backstop it can only make a result more cautious. `classify_attempt` now
     reads as: model proposes, then protocol check, mid-call flag and phrase backstop may only raise.
- Verified how: `make check` green (165 tests, including `tests/test_protocol_completion.py`). Two
  consecutive drills passed the gate with r02 escalated both times, once because the agent asked properly
  and once via the backstop (99.5 s and 98.6 s, 0 violations, 1-2 denials audited).
- Note for Phase 6: this is exactly the "protocol completion" metric SPEC §12 asks for; the eval suite
  should measure how often each question is actually asked, not only whether the classification was right.

### 2026-09-12 about 08:00 PDT — Check-in guard fix; board labels the data as fictional
- Fixed: the check-in agent's model-call guard was capped at 4 calls per turn, but one turn may legitimately
  record two or three answers before speaking, so it cut a normal call short (Evelyn, run
  `drill-20260912-074344`). Worse, the guard's cancel message was appended to the transcript as if the
  agent had said it, which then fed the classifier a line nobody spoke. Cap is now 8 per turn
  (`MAX_MODEL_CALLS_PER_TURN`), and a cut-short turn ends the call with a bracketed marker instead.
  `tests/test_guards.py` covers the cap, the reset between invocations and the audit note.
- Done: the drill board header now names what is fictional (roster and personas) and what is real (the
  archived NWS alert text), so any screenshot or video frame carries its own label per the CLAUDE.md rule.
  `docs/blog/` and `evals/drill_report.json` are gitignored: blog drafts are published on builder.aws, and
  the report is regenerated by every drill run (the Gate 1 evidence lives in the gates table above).
- Verified how: `make check` green (159 tests); `make local-drill ARGS=--auto-approve` passed on run
  `drill-20260912-074344`: 12/12 RESOLVED, both urgent personas escalated, 1 Cedar denial audited with its
  deciding facts, 0 violations, 51.0 s.

### 2026-09-11 about 22:00 PDT — Phase 1 cleanup pass before commit
- Done: removed everything the drill and tests did not use: the `model_voice`, `profiles_dir` and
  `personas_dir` settings, `Incident.metrics` / `closed_at`, an unused `PlanProblem` exception, the unused
  `at` parameter of `transition`, an unread raw-output stash in the graph hook, an unused board parameter,
  a persona turn counter, a redundant drill helper, the `tools`/`schema_tools` parameters of `build_cedar`
  (the schema always covers every tool), a dead block in the fixture script and a `sys.path` hack in the
  drill script. Simplified the two-branch transition in `assign_volunteer` and the wave-jump branch of
  `validate_call_plan`. Added `tests/test_graph_gates.py` (5 deterministic tests, no model calls).
- Fixed after Ansh tried it: a profile without `eval_personas` (the dummy profile) made the runner glob the
  repo root for personas and choke on `.pre-commit-config.yaml`. The runner now assesses such a profile on
  the roster's drill subset, prints the incident status and the assessor's rationale, and simulates
  nothing; `load_personas` rejects a missing directory. `tests/test_drill_setup.py` covers both setups.
- Verified how: `make check` green (ruff clean, 158 tests); `make local-drill ARGS=--auto-approve` exit 0
  on the trimmed tree: 12/12 RESOLVED with expected results, r01 and r02 escalated, 0 violations, 49.6 s.
  `DOORSTEP_PROFILE=tests/fixtures/profiles/dummy.yaml` + the drill script prints
  "Incident not_activated for profile 'dummy'" with the assessor's rationale and `RESULT: NOT ACTIVATED`
  (exit 2: nothing to gate, by design).

### 2026-09-11 about 21:20 PDT — Phase 1 built; Gate 1 passed
- Done (all local, no cloud, no Twilio, no Telegram):
  - Data: `data/org.json`, 48-resident `roster.json` from `scripts/gen_roster.py` (seeded; the first 12 are
    hand-authored and match the personas), `volunteers.json` (chat IDs as env references only), five real SE
    Portland relief centres labelled "sample - verify", and the real June 2021 PQR Excessive Heat Warning text
    fetched from the IEM VTEC archive by `scripts/fetch_alert_fixture.py` (source URL recorded in the file).
  - Hazard profiles: `agent/doorstep_agent/profiles/heat.yaml` merged over `_shared.yaml` by `profiles/loader.py`.
    Alert events, risk weights, questions, red-flag phrases (EN/ES), needs, relief-centre kind, tips and the
    intro/closing lines all come from the profile. `tests/fixtures/profiles/dummy.yaml` proves it.
  - Models, in-memory store, deterministic risk scoring with call-plan validation (up one wave with a reason,
    never down), the case state machine with retries and the drill clock (10 min -> 20 s).
  - Agents on Nova 2 Lite: alert_assessor and triage as Strands Graph nodes with structured output plus a
    deterministic outreach node; checkin_text with the same tools as voice (`record_answer`, `flag_urgent`,
    `end_call`); classifier with the phrase backstop; dispatcher with 13 tools behind `CedarAuthorization`.
    Personas on Nova Micro through the Strands Evals `ActorSimulator` with a phone-call template.
  - Policies: six `.cedar` files, a context enricher for the SPEC §8 session values, a Cedar schema generated
    from the tool specs (validated at startup), an audit hook on every tool call and denial, a post-hoc
    violation checker, and a model-call guard that stops runaway invocations.
  - `make local-drill`: async runner (6 check-ins at once), live terminal board, pending decisions with
    `--auto-approve`, JSON report, exit code = Gate 1 criteria.
- Verified how: `make check` (ruff + 149 tests) green; `make local-drill ARGS="--auto-approve --transcripts
  --report evals/drill_report.json"` exit 0, run twice on the finished tree (49.7 s and 53.6 s). Board and
  report: 12/12 RESOLVED, every result equal to the persona's hidden ground truth, r01 and r02 escalated,
  0 violations, 1 audited Cedar denial (dispatcher tried `assign_volunteer` for r05 without a valid
  volunteer). One polish item for Phase 6: after the red-flag line Nova sometimes ends the call with an
  awkward "I can't continue this conversation" sentence. Spikes B/C/D all passed
  (ActorSimulator on Nova Micro 1.5 s/turn; Graph 3.6 s; Cedar denial visible in `AfterToolCallEvent`).
- Found and fixed while running the drill (each now covered by a test or a guard):
  1. Nova 2 Lite returns list fields as strings and text fields as lists in structured output; Strands re-asks
     the model on every validation failure with no cap, which looked like a hang (40 retries on `notes`).
     Fix: `before` validators coerce those shapes; `ModelCallGuard` cancels an invocation after N model calls.
  2. The dispatcher's Cedar schema was generated from its own tool subset, so policies naming read-only tools
     failed validation ("unrecognized action"). Fix: the schema is always generated from `ALL_TOOLS`.
  3. "Violations" were counted as every denial. Now denials (attempts refused, audited with the deciding
     facts) and violations (forbidden actions that actually happened, checked against the outbox and audit)
     are separate; Gate 1 needs zero violations.
- Not done / deferred: the SPEC §12 evals (Phase 6); interrupts and Telegram (Phase 2). Under `--auto-approve`
  the simulated captain takes the first option of every decision; without it, escalated cases stay ESCALATED
  (counted as settled) with the decisions listed on the board.
- Next: Ansh commits Phase 1 on `phase1` and says "go" for Phase 2 (interrupts, persistence, Telegram).

### 2026-09-11 about 17:40 PDT — Phase 1 prep (baseline fixed, plan proposed, Cedar spike)
- Done: `.env.example` restored with every secret, phone number and chat ID blank (Ansh had deleted it to keep a
  single env file; the two scaffold tests read it). Phase 1 plan proposed to Ansh (tasks, files, spikes, models,
  Gate 1 tests, SPEC issues); waiting for "go". No product code written.
- Verified how: `make test` 5 passed, `make lint` clean, `gitleaks detect --no-git --source .env.example` no leaks.
- Spike A (Cedar, local, no cost): `CedarAuthorization` with the documented `tools=` auto-schema denies every call
  with "failed to parse schema from request" as soon as `context_enricher` adds fields such as `mode` or `role`,
  because the generated `SessionContext` only declares `hour_utc` and `call_count`. Without a schema the enricher
  works but only action-name typos are caught. With our own schema that declares every session field and every tool
  input, evaluation is correct (allow, deny, `forbid` beats `permit`, quiet hours with the Extreme override) and
  startup validation catches action typos, context-attribute typos and type errors.
- Next: Ansh says "go" → Task 0 (spikes B/C/D against Bedrock, about two cents) → Phase 1 build per the plan.

### 2026-09-11 about 12:35 PDT — Phase 0 (smoke 03 passed; Gate 0)
- Done: smoke 03 deployed the hello agent to AgentCore Runtime with the npm CLI and proved session continuity.
  Two prerequisites surfaced by a `deploy --dry-run`: the generated CDK app is TypeScript and needs `npm install`
  in `agentcore/cdk/` (`tsc` missing after `--skip-install`), and the agent needs `uv sync` in `app/hello/`.
  `run.sh` now does both when the regenerated project lacks them. The dry run also auto-created the deployment
  target from the `doorstep` profile (`aws-targets.json`: account <account-id>, us-east-1).
- Verified how: `make smoke-03` exit 0. Deploy log: bootstrap check, CloudFormation deploy, outputs with the runtime
  ARN `arn:aws:bedrock-agentcore:us-east-1:<account-id>:runtime/DoorstepHello_hello-7LKoIm5pUj`; invoke 1 printed
  `OK`, invoke 2 with the same session ID printed `juniper`; `agentcore status` showed `Runtime: READY`;
  `PASS: AgentCore Runtime kept context across two invocations in one session`.
- Notes for Phase 3: the CLI project layout is `agentcore/agentcore.json` (runtimes, memories, gateways …),
  `agentcore/aws-targets.json`, `agentcore/cdk/` (TypeScript CDK app) and `app/<agent>/` with its own pyproject;
  CodeZip runtimes default to `runtimeVersion: PYTHON_3_14`; the deploy enabled CloudWatch Transaction Search
  (needed for AgentCore Observability traces; ~10 min to index). First deploy including bootstrap took about 5 min.
- Teardown: `agentcore remove all -y` then `agentcore deploy -y` reported "Tear down stack" and `agentcore status`
  shows no resources; the CDK bootstrap stack `CDKToolkit` is kept for Phase 3.
- Gate 0: passed (see the Gates table). Ansh commits all of Phase 0 in one go.
- Next: "go" for Phase 1 (domain core + text-mode agents, local), ideally in a fresh session.

### 2026-09-11 about 12:20 PDT — Phase 0 (Bedrock cleared; smoke 01 and 02 passed)
- Done: Bedrock account verification cleared (Ansh's `bedrock-runtime converse` probe answered). Smoke 01 passed
  first try. Smoke 02 needed two fixes: (1) `BedrockNovaSonicModel` rejects `boto_session` together with `region`
  (the session carries the region); (2) a text-only Sonic session never answers: after a text turn the model only
  reported input-token usage and sat idle for 45 s. Debug logging plus the Nova 2 Sonic input-events docs showed
  cross-modal text is only honoured "during an active voice session", so an audio content block must be open.
  Rewrote the script around an audio feeder that streams 20 ms chunks at real-time pace: silence by default, and the
  user turns synthesized with macOS `say` + `afconvert` (default) or sent as text alongside the silence (`--text`).
- Verified how: `make smoke-01` exit 0 (`heat_index_calls=1`, validated `HeatIndexReport`, 122.6 °F, `extreme_danger`).
  `make smoke-02` exit 0 with user transcripts, assistant transcripts and audio chunk counts for both turns;
  `make smoke-02 ARGS=--text` exit 0 too. `make lint` and `pytest` green.
- Decision: the Phase 4 voice bridge must keep Sonic's audio content block open for the whole call and keep streaming
  audio (silence when nobody speaks); any text injected into the call rides alongside that stream.
- Note: on shutdown the experimental AWS CRT HTTP/2 client logs two warnings ("InvalidStateError: CANCELLED",
  "HTTP-stream has completed") after the PASS line. They come from the SDK's teardown, not from the test.
- Next: smoke 03 (AgentCore Runtime hello) and its teardown, then Gate 0.

### 2026-09-11 about 11:50 PDT — Phase 0 (smoke 04 passed)
- Done: Ansh created the Twilio subaccount `doorstep`, bought a US local number, set geo permissions and the $10
  trigger, and filled `.env`. Smoke 04 passed on the second call.
- Verified how: `make smoke-04` exit 0. Output shows the subaccount check (`owner=AC15...c79a` differs from the
  subaccount SID), number ownership, allowlist check, ngrok `wss://…ngrok-free.dev/media`, call `ringing` →
  `in-progress`, WebSocket `connected` + `start` with `mediaFormat={'encoding': 'audio/x-mulaw', 'sampleRate': 8000,
  'channels': 1}` and both `<Parameter>` values, 150 media frames, `final call status=completed duration=12s`,
  `PASS: Twilio subaccount call streamed 150 frames of audio/x-mulaw @ 8000 Hz to the local WebSocket`.
- Decision: the pass criterion no longer requires a `stop` event. When our server closes the WebSocket, Twilio
  resumes the TwiML (`<Say>` goodbye, `<Hangup/>`) and never sends `stop`; `stop` only arrives when Twilio ends the
  stream first. Pass now = `start` + ≥ 50 media frames + call status `completed`. This is also the hang-up pattern
  the Phase 4 voice bridge will use.
- Next: smoke 01 and 02 once Bedrock verification clears; then approve the deploy for smoke 03.

### 2026-09-11 about 11:15 PDT — Phase 0 (smoke 05 passed)
- Done: smoke 05 passed with the remade bot `@doorstep_agent_bot`. Ansh's earlier `/start` and `Hi` had gone to the
  deleted bot's cached chat (same display name); the deep link `t.me/doorstep_agent_bot?start=smoke` reached the new bot,
  `--whoami` returned the chat ID, and it was written to `.env` as `TELEGRAM_CAPTAIN_CHAT_ID`.
- Verified how: `make smoke-05` exit 0. Output: message sent (message_id 6), `callback received: data='smoke:handling'`,
  `PASS: Telegram round trip: message + inline buttons + callback (I'm handling it)`.
- Next: Twilio human step, then smoke 04.
- Lesson: after deleting and recreating a bot with the same username, open it by link or username search; the old chat
  in the client still points at the deleted bot ID.

### 2026-09-11 about 10:50 PDT — Phase 0 (branch move, Telegram step in progress)
- Done:
  - The scaffold commit on `main` (caff7d5) was undone with `git reset HEAD~1` and a branch `phase0` was created;
    the same work is re-committed on `phase0` as fbb0a07. Phase 0 continues on `phase0`.
  - The Telegram bot token was briefly pasted into `.env.example` (a tracked file). It was moved to `.env` and the
    example restored before any commit; gitleaks would also have blocked it. The token should be rotated after Phase 0
    (BotFather → /mybots → API Token → Revoke) and the new one put in `.env` only.
- Verified how: `git reflog`; `git status` clean after fbb0a07; `.env.example` token line blank in the commit.
- Smoke 05 status: the token authenticates as `@doorstep_agent_bot` (display name "Doorstep"); no webhook;
  `getWebhookInfo.pending_update_count` = 0 and `getUpdates` empty, so the `/start` and `Hi` Ansh sent to a bot named
  "Doorstep" did not reach this bot. Waiting for Ansh to confirm the bot's username or message `t.me/doorstep_agent_bot`.
- Next: smoke 05 once the right bot is messaged, then the Twilio step.

### 2026-09-11 01:00 PDT — Phase 0 (scaffold + smoke scripts written)
- Done:
  - Verified install names through the `strands` and `agentcore` MCP servers, PyPI and npm:
    `strands-agents` 1.55.1 with extras `cedar`, `bidi`, `otel` (`bidi-pyaudio` for local audio; `bidi` needs Python ≥ 3.12),
    `strands-agents-tools` 0.8.8, `strands-agents-evals` 1.2.0, `bedrock-agentcore` 1.22.0 (Python SDK),
    `@aws/agentcore` 0.28.1 (npm CLI; the AWS docs say it replaces the pip `bedrock-agentcore-starter-toolkit`).
    AgentCore runtime session IDs must be 33 to 256 characters.
  - Model IDs confirmed in the account with read-only CLI calls: `us.amazon.nova-2-lite-v1:0`, `amazon.nova-2-sonic-v1:0`,
    `us.amazon.nova-micro-v1:0` (there is no Nova 2 Micro).
  - Scaffold: `pyproject.toml` (uv, hatchling, package at `agent/doorstep_agent`, ruff + pytest config), `.python-version` (3.12),
    `uv.lock`, `package.json` + lockfile with `@aws/agentcore` and `aws-cdk` as devDependencies, `.npmrc` (engine-strict),
    `Makefile` (Node 22 PATH pin; setup, test, lint, fmt, check, smoke-01..05, stubs for later phases), `.pre-commit-config.yaml`
    (gitleaks local hook, ruff, standard hooks), `.env.example`, `.github/workflows/ci.yml` (ruff, pytest, gitleaks),
    `tests/test_scaffold.py`, `docs/COST.md`, one-line README per directory.
  - Five smoke scripts in `scripts/smoke/` plus `_common.py` and `README.md`. Smoke 03 uses a project generated by
    `agentcore create` (gitignored; `run.sh` regenerates it) with our `main.py` on Nova 2 Lite.
- Verified how:
  - `uv sync --all-groups` on uv-managed Python 3.12.14; every planned import resolves at the versions above.
  - `make lint` and `uv run pytest -q` green (5 tests). `make help` and `make node-check` OK on Node 22.23.2; global Node still v20.20.0.
  - `uv run pre-commit install` done; the gitleaks gate test above.
  - APIs checked against the installed package source, not memory: `BedrockModel(model_id, boto_session, region_name)`,
    `AgentResult.metrics.tool_metrics`, `BedrockNovaSonicModel(model_id, region, boto_session, audio)`,
    `BidiAgent.start/send/receive/stop` and the Bidi event classes, `BedrockAgentCoreApp` entrypoint `(payload, context)` with
    `context.session_id`, Twilio `calls.create(twiml=, time_limit=)`.
  - Twilio Media Streams contract cross-checked in the Twilio docs: `<Connect><Stream>` is bidirectional, the URL takes no
    query string, `<Parameter>` values arrive in the `start` message, TwiML resumes after the server closes the socket.
- Next:
  1. Ansh: Telegram bot + chat ID → run smoke 05.
  2. Ansh: Twilio subaccount + number + `.env` → run smoke 04 (Ansh answers the call).
  3. Ansh: tell Claude when Bedrock verification clears → run smoke 01 and 02.
  4. Ansh: approve the AgentCore deploy → run smoke 03, then `make smoke-03 ARGS=teardown`.
  5. Record results here, mark Gate 0, commit.
- Blockers: Bedrock returns "Your account is currently being verified" (blocks smoke 01, 02, 03).
- Decisions: see the register.

## Decisions register

| Date | Decision | Why | Alternatives |
|---|---|---|---|
| 2026-09-11 | AgentCore CLI is the npm package `@aws/agentcore`, installed as a project devDependency and run via `npx` | AWS docs say it replaces the pip starter toolkit and that the pip command shadows it; CLAUDE.md forbids `npm install -g` | pip `bedrock-agentcore-starter-toolkit` |
| 2026-09-11 | gitleaks runs as a pre-commit `local` hook using the Homebrew binary | Same rules, no Go toolchain download by pre-commit | upstream `gitleaks/gitleaks` hook repo |
| 2026-09-11 | The project generated by `agentcore create` for smoke 03 is gitignored; only our `main.py` and `run.sh` are committed | About 30 generated files including a CDK TypeScript project; `run.sh` regenerates them | commit the generated project |
| 2026-09-11 | Simulated residents use `us.amazon.nova-micro-v1:0` | No Nova 2 Micro exists in Bedrock; SPEC allows Nova Micro | Nova 2 Lite for personas too |
| 2026-09-11 | Python 3.12 managed by uv (`.python-version`) | The `bidi` extra needs ≥ 3.12; the system Python is 3.14 | system Python 3.14 |
| 2026-09-11 | Smoke 04 hangs up by closing the WebSocket after ~3 s of audio, with a 60 s call time limit and a REST hang-up as backup | Bounded cost and no runaway call if the script dies | let the callee hang up |
| 2026-09-11 | Smoke 02 drives Nova 2 Sonic with synthesized speech over a continuously streamed audio channel (silence between turns); cross-modal text is a `--text` option | Sonic only answers inside an active voice session (audio content block open); speech-to-speech is what the phone path needs | text-only session (never answers); headset-only manual test |
| 2026-09-11 | Phase 1 Cedar: generate our own `.cedarschema` from the Strands tool specs plus the enricher's declared session fields, and pass it as `schema=` | The SDK's `tools=` auto-schema fails at runtime once the enricher adds fields (spike A); our schema keeps full validation at startup | no schema (only action typos caught); the SDK auto-schema (unusable with an enricher) |
| 2026-09-11 | Shared hazard parts live in `profiles/_shared.yaml` and are merged under each profile; a profile may opt out with `include_shared: false` | Shared red-flag categories and needs are data too, so the dummy-profile test can prove nothing is hard-coded | duplicate the shared parts in every profile |
| 2026-09-11 | Structured-output models carry `before` validators that coerce the shapes Nova emits, and every agent has a `ModelCallGuard` | Strands retries structured output without a cap; the drill wedged on a string-vs-list field | prompt-only fixes (fragile); a Strands retry cap (none exists in 1.55) |
| 2026-09-11 | Denials and violations are different counters: denials = attempts refused (Cedar or code check), violations = forbidden actions that executed, found by `violations.find_violations` | Gate 1 and the red-team eval say "every attempt must be denied"; a denial is the policy working | count every denial as a violation (fails the gate whenever the model tries something and is stopped) |
| 2026-09-11 | Residents are simulated with the Strands Evals `ActorSimulator` on `us.amazon.nova-micro-v1:0`, a phone-call prompt template and a `message`/`stop` reply model | It is the SDK's user-simulation primitive and Micro answers in about 1.5 s | a hand-rolled persona Agent; Nova 2 Lite personas |
| 2026-09-11 | Phase 1 treats ESCALATED as a settled state and `--auto-approve` plays the captain by taking the first option | Real decisions arrive with Strands interrupts in Phase 2; the drill must finish without a human | block the drill on a terminal prompt |
| 2026-09-12 | `escalate_to_captain` uses a **tool** interrupt; `assign_volunteer` uses a **`BeforeToolCallEvent`** hook | Spike A: a tool interrupt re-runs the tool body on resume, a hook interrupt stops the tool running at all. Escalation's pre-interrupt work is one idempotent upsert and paging the captain *is* the escalation; an unapproved volunteer task must never leave the process | both as tool interrupts (would send then regret); both as hooks (escalation has nothing to guard) |
| 2026-09-12 | Tools raise decisions as `draft`; the runner stamps the interrupt and flips them to `pending`, and only then does a channel deliver | Closes the race where a captain could tap a button before the decision knew its own interrupt id. Also keeps all I/O out of a tool body that re-executes | send from inside the tool (racy, and sends twice) |
| 2026-09-12 | Decisions are keyed on `tool_use_id`, not a counter | A tool that interrupts re-runs from the top, so `upsert_decision` must find the record it already made rather than make a second one | a deterministic hash of incident+resident+name (wrong when one resident is escalated twice) |
| 2026-09-12 | The deterministic half of a decision runs in code after the resume, not only in the model | A live drill showed the dispatcher closing one case after "I'm handling it" and silently forgetting two others. SPEC §5: the model proposes, deterministic code decides | trust the prompt (lost two captain decisions in one run) |
| 2026-09-12 | Volunteer task replies are stored as decisions named `doorstep-volunteer-update` with no interrupt | SPEC §4 says volunteers get tasks, not decisions, but the mechanics — identity check, answered once, expiry — are identical, and one path cannot drift from the other | a separate reply path (a second place to get identity wrong) |
| 2026-09-12 | A volunteer brief carries consented roster notes but never the resident's words from today's call | The line that survives scrutiny is standing facts vs today's call: a note changes how a volunteer knocks, a quote is the captain's evidence | drop all health-adjacent detail (deletes "hard of hearing", which a volunteer needs) |
| 2026-09-12 | Drills send Telegram only with `--telegram`; `SnapshotSessionManager` + `LocalFileStorage` for sessions (S3 is a storage swap in Phase 3) | CLAUDE.md bans messages in sandbox but is silent on drill; a casual `make local-drill` or CI run must not text anyone. Strands docs recommend SnapshotSessionManager for new single-agent sessions | opt-out flag (wrong default); `FileSessionManager` (legacy per the docs) |
| 2026-09-11 | The active profile and the replay fixture are configuration (`DOORSTEP_PROFILE`, `DOORSTEP_ALERT_FIXTURE` in `config.py`); a test forbids hazard words anywhere else in the agent package | Makes "nothing hazard-specific is hard-coded" checkable | a default in the drill runner (caught by the guard) |

## Second-number swap (do before the video and submission)

Phase 2 ran with **one** Telegram account playing both the captain and `vol-tom`:
`TELEGRAM_VOLUNTEER_CHAT_IDS` is set to the captain's own chat id, so both messages land in the
same chat. The code handles this deliberately — when a chat id is ambiguous, the responder
resolves to the member the decision was **addressed to**, pinned by
`tests/test_decisions.py::test_one_phone_playing_two_roles_resolves_to_the_addressee`.

It is still worth swapping in a real second account before submitting, for two reasons: the
demo shows the captain's phone and the volunteer's phone as genuinely different people, and the
role check stops being something only the unit tests can show.

To swap, edit one line in `.env` (index 0 is `vol-tom`, index 1 `vol-priya`, and so on in the
order of `data/volunteers.json`):

    TELEGRAM_VOLUNTEER_CHAT_IDS=<second account's chat id>,<third>,...

Get the second account's chat id by opening `t.me/doorstep_agent_bot` on that phone, tapping
Start, sending any message, then running `make smoke-05 ARGS=--whoami`. No code changes are
needed; nothing else reads these ids. Re-run `make telegram-drill` to confirm the captain's
message and the volunteer's task land on different phones, and that the volunteer's phone cannot
answer a captain decision (it should reply "You're not on the Juniper Court list for this
decision").

## Disclosures (goes into the README)

- AI coding assistant: Claude Code (used during the submission period).
- Adapted samples (URL, license, what was adapted):
  - `scripts/smoke/03_agentcore_hello/DoorstepHello/` is scaffolding generated by `agentcore create`
    (npm `@aws/agentcore` 0.28.1, Apache-2.0, https://github.com/aws/agentcore-cli). It is gitignored and regenerated
    by `run.sh`; our own `main.py` replaces its template entrypoint.
  - Smoke scripts follow the usage patterns shown in the Strands docs (https://strandsagents.com/docs/) and the Twilio
    Media Streams docs (https://www.twilio.com/docs/voice/media-streams); no sample code was copied.
- Data sources: `data/alerts/2021-06-pqr-excessive-heat-warning.json` is the real, public-domain NWS Portland
  Excessive Heat Warning of June 2021, fetched from the Iowa Environmental Mesonet VTEC archive
  (https://mesonet.agron.iastate.edu/vtec/?year=2021&wfo=KPQR&phenomena=EH&significance=W&eventid=0001) by
  `scripts/fetch_alert_fixture.py`. Relief centres are real public places listed from public information,
  labelled "sample - verify". Everything else in `data/` and `evals/personas/` is invented.
- Pre-existing code: none.

## Human to-do (Ansh)

- [x] Phase 0: Telegram bot + chat ID in `.env`; Twilio subaccount + number + `.env`; say when Bedrock verification clears; approve the AgentCore deploy
- [ ] After Phase 0: rotate the Telegram bot token in BotFather (it was pasted into a tracked file once) and update `.env` only
- [x] Phase 1: review and commit the working tree on `phase1` (one commit), then say "go" for Phase 2
- [x] Phase 2: set `TELEGRAM_VOLUNTEER_CHAT_IDS` in `.env` to the captain chat id, so one phone
      plays both parts (done 2026-09-12)
- [ ] **Before submitting: swap in a real second Telegram account.** See "Second-number swap"
      below — it is one `.env` line, and it makes the role check visibly real in the demo.
- [ ] Blog post 1 (Sat AM) · [ ] Blog post 2 (Sun PM) · [ ] Blog post 3 (Mon AM)
- [ ] Volunteer Telegram account ready
- [ ] Devpost draft created Saturday
- [ ] Monday morning blocked off for recording and submission
