# Doorstep — progress log

Current phase: **7 — Launch**: video recorded; submission package ready (SUBMISSION §10). Next: Ansh tags `v1.0`, uploads the video, publishes posts 2 and 3, submits.
Next gate: **Gate 7 (submitted)**
Time now vs plan: Phase 0 ran Fri Sep 11 00:00–12:35 PDT (planned Thu evening). Phase 1 ran Fri
17:45–21:20 PDT, about 3.5 h of its 5 h box. Phase 2 started Sat 08:30 PDT and was committed
before 11:27 PDT, when Phase 3 planning started: about 1.5 h ahead of the Sat 1 PM slot.

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
| 3 | Cloud drill via AgentCore + Telegram webhook + traces | ✅ passed | **Deploy:** `make deploy` created stack `Doorstep` from nothing (118 s) and ran again green from a clean clone + this phase's diff (`make setup && make deploy`; the runtime image hashed identically). **Resume in the cloud:** `make cloud-restart-test ARGS="--delay 120"` 18/18 — paused in process A, `StopRuntimeSession`, a real-webhook tap resumed in process B from S3, exactly one volunteer task, two replays dropped at the webhook, a second tap `already_answered`, wrong secret 401. **Cloud drill with Telegram:** `make cloud-drill ARGS=--telegram` PASS on `drill-20260912-234122-d132`: 12/12 settled, r01 and r02 escalated, 0 violations, 8 taps from Ansh's phone through the webhook (7 as `captain:cap-maria`, 1 as `volunteer:vol-tom`), 117 s. **Traces:** 2,059 spans in 14 traces for that incident (`make trace`). **No secrets:** `make scan-logs` 0 matches for 17 values over 20,500 events. **Offline:** 277 tests. **Cost:** $5.31 gross / $0.00 net to date (Phase 3 ≈ $1.2 gross). | 2026-09-12 |
| 4a | 3 browser check-ins | ✅ passed | **Three consecutive real browser calls by Ansh** (Chrome, mic, headphones) through the deployed path (`/voice/session` → presigned WebSocket → `doorstep_voice` runtime → Nova 2 Sonic → coordinator), incident `drill-20260913-034202-acd6`, on the final build: **Rose (r01) OK**, 4 answers, 2 barge-ins, result applied **2.2 s** after hang-up; **George (r10) NEEDS_HELP** (ride, cooling), 3 barge-ins, volunteer task raised, applied **1.5 s** after hang-up; **Evelyn (r09) URGENT mid-call**: the live backstop matched "dizzy and confused", **the captain's decision `dec-003` existed at 04:06:16.6, 6 s before hang-up at 04:06:22**, the line was held until it was out, final classification URGENT applied **1.2 s** after hang-up. Before these: Ansh's C2 call (Mei-Ling OK, 2.5 s) and three automated `make voice-e2e` calls (OK 1.5 s and 1.8 s; urgent paged 8.2 s before hang-up). `make check` 322 tests; `make scan-logs` 0 matches. Sonic ≈ $0.010–0.016 per call. | 2026-09-12 |
| 4b | 2 real calls + mid-call escalation | ✅ passed | Real calls from the Twilio `doorstep` subaccount to Ansh's own allowlisted phone, through the `checkin_worker` dialer and the phone bridge (ngrok), Cedar allowing each only through the operator quiet-hours exception. **Call 1** `live-20260913-044054-c586`: Twilio `completed` 66 s, full protocol, OK, result applied 1.5 s after hang-up. **Call 2** `live-20260913-074003-405b` (on speaker, recorded): `completed` 68 s, full protocol, OK, RESOLVED. **Call 3** `live-20260913-080655-3cec` (Telegram on, recorded): "I feel dizzy and confused, I'm not sure what day it is". The live backstop paged at 08:07:35.0, **the captain's Telegram message was sent at 08:07:36.7, and Twilio ended the call at 08:07:52: 15.3 s before hang-up**. URGENT, ESCALATED, final classification applied 1.1 s after hang-up. Before these: no-ring rehearsals through the deployed chain, where Cedar denied non-allowlisted residents in the cloud. `make check` 354 tests. | 2026-09-13 |
| 5 | Judge flow < 4 min, Lighthouse a11y ≥ 90 | ✅ passed | **Lighthouse accessibility 100** on all 7 pages, desktop and mobile (`make lighthouse`, deployed site). **Keyboard only** in real Chrome (puppeteer, Tab/Shift+Tab/Enter/Space, no mouse): recorded 19/19, **live 22/22** on the deployed site: drill started by Enter, first decision at 25 s, answered by keyboard at 27 s, report at 28 s, every focus stop ringed, 0 console errors (`make keyboard-pass ARGS=--live`). **Caps** 14/14 on the deployed API (`make cap-test`): per-IP, everyone-per-10-min, daily and total drill caps, voice per-IP/daily/total, no-token 401, passcode lockout (right passcode refused while locked), kill switch on drills/voice/answers; counters restored. **Judge path by pointer** on the deployed site (in-app browser): drill 5.4 s after the click, synthetic voice call for Mei-Ling paged the captain 21 s before hang-up, two dashboard answers applied as `captain:cap-maria` / `volunteer:vol-tom` with `source: web`, report "all 12 in 1 min 30 s", 0 console errors. `make check` 383 tests; `make web-test` 9. **Human runs (Ansh, fresh incognito, own voice, stopwatch from opening the URL to the report headline): laptop Chrome 1:39** (`sandbox-20260913-181317-c0e82c`: call began 10.8 s after the drill started, captain paged 33.5 s, call ended 48.1 s so the page was out 14.6 s before hang-up, all 12 reached 47.6 s, his dashboard answer applied 77.7 s; 0 console errors) and **phone Safari on cellular 1:07** (`sandbox-20260913-181744-7bf558`: call 8.1 s, paged 30.2 s, ended 44.4 s so 14.2 s before hang-up, all 12 reached 47.4 s, answer applied 54.4 s). Both answers recorded as `captain:cap-maria` with `source: web`. | 2026-09-13 |
| 6 | Evals targets, CI green, clean-clone setup | ✅ **accepted by Ansh with recall 90% (2026-09-13 22:10 PDT)**; 3 of 4 met | Red team: 0 violations, 20/20 forbidden attempts denied with reasons ✅. CI green on `main` ✅. Clean clone (setup, check without AWS, web-test, local drill) ✅. **Red-flag recall 90.0% (27/30) vs 100% target ❌**: all 3 misses are calls where the simulated resident never said the red flag (evals/REPORT.md). Repo public, MIT detected, topics set, secret scanning + push protection on. | 2026-09-13 |
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

### 2026-09-14 about 00:40 PDT — video recorded; submission package ready
- Video recorded and edited by Ansh from SUBMISSION §5 (script rewritten to match what shipped; code cards cut for time). No music.
- Phone cold open: bridge + ngrok restarted; `make phone-preflight` fails "ngrok reaches the local bridge" from this Mac only (a local network filter blocks `*.ngrok-free.dev`; the same URL returns `{"ok":true}` from Ansh's phone on cellular). Real calls worked: e.g. `live-20260914-054123-be48`, `live-20260914-053944-91b2` (captain paged 21.4 s before hang-up).
- Second Telegram account done: Tom (`vol-tom`) now maps to Ansh's iPad, set in `.env` and SSM `/doorstep/telegram/volunteer_chat_ids` (no deploy needed). Found on the way: the webhook's `allowed_updates` filter persists into long polling, so a `getUpdates` without `allowed_updates` silently drops text messages. Telegram take `drill-20260914-065148-ce60`: captain "Send Tom (0.4 km)" from the iPhone, Tom "They're OK" from the iPad 7 s later, both via the webhook; cloud-drill checks all PASS.
- "Tom got two tasks": not a duplicate. Tapping "On my way" sends a follow-up message (They're OK / Need more help); one `volunteer_task` message was recorded.
- Dashboard: Report, Evidence and Decisions now use the full content width (`w-full` instead of `max-w-3xl/4xl`); `make web-test` 9 passed; `make web-deploy` published it before the demo takes. Not yet on `main`.
- §9 re-check: `make cap-test` 15/15 with friendly messages, kill switch restored `off` (one sandbox drill, ~$0.38). Judging-period counters: sandbox 13/120, voice 17/120. `make check` 398 passed, ruff clean.
- SUBMISSION §4 wording: "98% died indoors" (verified figure) instead of "at home". §9 ticked; §10 added (YouTube title/description, Devpost field map, screenshot list). A local `DEVPOST-PASTE.md` with the passcode and the screenshots live outside the repo.
- Rough edge seen: a model-written captain option labelled "911 call" on an urgent card (`drill-20260914-065148-ce60`, dec-001). Not fixed (main frozen); keep it off screen.
- Testing instructions step 6 now names `drill-20260914-065148-ce60` for Captain mode (captain tokens are not tied to one incident, `api/doorstep_api/access.py`; incident items carry no TTL, so it stays viewable through judging). Not tested in a browser: that needs typing the passcode.
- Blog post 2 published by Ansh and added to SUBMISSION §4/§10 (URL returns 200).
- Left for Ansh: commit + merge to `main` + tag `v1.0` + push; YouTube upload; blog posts 2 and 3; fill the three `<...>` URLs; submit on Devpost.

### 2026-09-13 about 22:10 PDT — Gate 6 accepted; Phase 7 started
- Ansh's decision: accept Gate 6 with red-flag recall at 90.0% (target 100%); he doesn't expect further changes to reach 100%. Say 90% in every submission text.
- Pre-submission audit (SUBMISSION §9), read-only: repo public + MIT; README renders (GitHub markdown API); gitleaks over all refs, 38 commits, no leaks; exact-value search of the AWS account id, Twilio subaccount SID, from-number, allowlist numbers, captain passcode and Telegram chat ids over full history and tree: 0 hits; CI green on `cac195c`; diagram covers input, Strands loop, tools, AWS, outputs, with Memory marked not built; README disclosures complete. Live site `/`, `/evidence`, `/policies`, `/report`, `/captain`, `/evals-report.json` 200. Sandbox drill, browser voice and caps not re-run tonight (last verified Gate 5, 2026-09-13).
- SUBMISSION §4 filled from evals/REPORT.md: 7.0 s to first call, 48/48, 27.9 min on 6 lines vs ≈3.2 h, 18 decisions vs 204 actions, recall 90%, 20/20 red team, 0 violations. Passcode deliberately left out of the repo (paste into Devpost only). Blog 2 URL is not in the repo.
- Link check: repo, LICENSE, raw architecture.png, raw REPORT.md, blog 1: 200. `docs/architecture.svg` is untracked, so its raw URL 404s (the README links the PNG only; upload the PNG to Devpost).
- Running until Oct 8: stack `Doorstep` (idle ≈ $0.10–0.15/month), alert poller schedule ENABLED, kill switch `off`. Worst case with caps: ≈ $6.60/day and $52.80 total on models (COST.md). Budget `doorstep-monthly` $25 (actual $7.02) with alerts at 60% and 100% actual and 80% forecast; 3 CloudWatch alarms OK with the `doorstep-ops-alerts` email subscription.
- Tag `v1.0` and pushes left to Ansh (CLAUDE.md: Claude never commits or pushes).

### 2026-09-13 about 21:20 PDT — CI flake after the final commit: moto's read-back race (test fake)
- CI failed 1 of 398: `test_ids_are_unique_under_threads[dynamo]`, 59 unique decision ids of 60.
  Not our code: `allocate` is one `UpdateItem ADD` with `ReturnValues=UPDATED_NEW`, which real
  DynamoDB computes atomically. moto's `DynamoHandler.update_item` gets its live stored item back
  from the backend and serializes it afterwards; `tests/conftest.py` only locked the backend call,
  so a concurrent `ADD` could land before the read-back and two threads saw the same counter.
- Reproduced 5/5 by pausing 1 ms inside moto's `Item.to_json` (throwaway spike, not committed);
  it had passed 25/25 locally without the pause. Fix: the conftest lock (re-entrant) now also
  wraps moto's request handlers (`put_item`, `get_item`, `query`, `scan`, `update_item`,
  `delete_item`, `transact_write_items`, `batch_write_item`, `batch_get_item`).
- Verified how: with the pause, 0/10 failures (was 5/5); all 13 DynamoDB tests pass with the pause
  (no deadlock); full suite CI-style (no AWS profile) 3 × 398 passed; ruff clean.

### 2026-09-13 about 21:00 PDT — repo public; Phase 6 final verification
- Verified how (everything read-only, from outside):
  - GitHub: **public**, MIT License detected, topics `agents-for-humans`, `bedrock-agentcore`,
    `strands-agents`, homepage = the live site, description set; secret scanning **enabled**, push
    protection **enabled**.
  - GitHub Actions on `main`: last four runs **success**, including both commits after the rewrite.
  - Anonymous mirror clone of the public repo: 8 branches, 36 commits dated Sep 10–13, only no-reply
    emails, 0 occurrences of the AWS account id, the work domain or the personal email in any
    commit or file version, gitleaks full history **no leaks**.
  - Public URLs: repo page, raw README, raw diagram and CI badge 200; all 31 README file links
    resolve; all 12 external README links return 200; live site 200 with the Evidence page showing
    the eval results.
- PLAN Phase 6 tasks: evals (4 suites, before/after, REPORT.md + JSON) done; hardening (webhook
  idempotency and backoff from Phases 3–4, DLQ and failed-call alarms, graceful Twilio failure,
  `make cap-test` from Gate 5, CI green) done; docs (README, diagram, COST.md, disclosures, testing
  instructions in SUBMISSION §4) done; clean clone done; go public done.
- **Gate 6: 3 of 4 criteria met.** Red team 0 violations; CI green; clean-clone setup succeeds.
  **Red-flag recall 90.0% (27/30), not the 100% target**: the three misses are check-ins where the
  simulated resident never said the red flag. Accepting the gate with that criterion unmet, or
  holding it, is Ansh's call.
- Kept until Phase 7 is submitted: `~/Projects/doorstep-old` (pre-rewrite working copy, has `.env`)
  and `~/Projects/doorstep-backup.git` (pre-rewrite mirror). Delete both afterwards: they hold the
  old history.

### 2026-09-13 about 20:55 PDT — git history rewritten before going public
- Ansh's decision: rewrite, not squash (the dated history is evidence that everything was built in
  the submission period). `git filter-repo` on a mirror clone replaced the AWS account id in every
  file version and mapped the work and personal emails to the GitHub no-reply address; force-pushed
  all branches (`main`, `phase0`–`phase6`). Backup mirror kept outside the repo until Phase 7.
- Verified how (before the push, on the rewritten mirror): 8 branches, 35 commits (unchanged),
  commit dates Sep 10–13 identical to the backup, only no-reply author/committer emails, 0
  occurrences of the account id, the work domain or the personal email in any commit message or
  file version, gitleaks 35 commits no leaks. After the push: GitHub attributes all 16 commits on
  `main` to `anshsahny`; commit hashes quoted earlier in this log predate the rewrite.
- Found in the fresh working clone: `make setup` rewrote `package-lock.json` (npm pruned ~320
  stale optional peer entries). `npm ci` is not a fix (it fails EBADPLATFORM on esbuild's optional
  platform packages in that lockfile), so the regenerated lockfile is committed instead; a second
  `npm install` leaves it unchanged.

### 2026-09-13 about 20:40 PDT — Phase 6 fixes, alarms and Evidence page deployed
- Ansh ran `make deploy` (the first attempt failed inside Docker Desktop with an I/O error: the
  Mac had 248 MB free; retried after freeing space) and `make web-deploy`.
- Verified how (read-only): three CloudWatch alarms `doorstep-checkin-call-failed`,
  `doorstep-checkin-jobs-dlq-not-empty`, `doorstep-checkin-worker-errors` exist in state OK, each
  with the `doorstep-ops-alerts` SNS action; the `FailedCalls` metric filter exists; coordinator
  runtime READY, updated 03:30 UTC; `/evals-report.json` served as `application/json`; the live
  Evidence page renders all seven eval rows (in-app browser).
- Left for Ansh: subscribe an email to `doorstep-ops-alerts`; push to `main` (GitHub Actions);
  the git-history decision and the go to make the repo public.

### 2026-09-13 about 18:15 PDT — CI-identical run on the Phase 6 commit
- Ansh committed Phase 6 on `phase6` (`38358bb`). `docs/architecture.{drawio,png,svg}` were left
  untracked; the README links to the PNG, so they must be added before pushing.
- Verified how: fresh clone of `HEAD`, the exact `ci.yml` steps with no AWS profile or credentials
  files: `uv sync --frozen --group infra`, `ruff check`, `ruff format --check`,
  `pytest` with the infra group: **398 passed**; gitleaks v8.30.1 container over the full history:
  **30 commits, no leaks**. GitHub Actions itself runs when the branch is pushed.
- `make deploy` was blocked by the session's permission classifier, so the fixes and alarms are
  **not deployed**; Ansh runs `make deploy` (then `make web-deploy`, and subscribes an email to
  SNS `doorstep-ops-alerts`).

### 2026-09-13 about 17:55–18:20 PDT — hardening, README, clean clone (Phase 6 continued)
- Built:
  - **Alarms** (`infra/doorstep_stack.py`): SNS topic `doorstep-ops-alerts` with three alarms: DLQ
    not empty, dialer Lambda errors, and a log metric filter on the dialer's `"call failed"` line.
    The dialer catches Twilio errors and never retries (by design), so without the filter a
    Twilio failure would have reached neither the DLQ nor the error count. Not deployed yet;
    subscribing an address to the topic is a console step.
  - Twilio failure path test (`test_phone.py`): logged for the alarm, not retried.
  - **README** per SUBMISSION §7 (problem, demo, how it works, Strands feature → file table, AWS
    services, safety, evidence, quickstart, full deploy, cost, criteria map, limitations,
    roadmap, disclosures). Per-directory READMEs refreshed (no EC2, no Leaflet, no "arrives in").
  - Makefile: cloud targets take `AWS_PROFILE=...` (were hard-coded to `doorstep`); **`make setup`
    now installs `web/` packages** (found by the clean clone).
  - SPEC §12 amended with the as-built eval design. AWS account id redacted from this file
    (it remains in git history). CLAUDE.md commands updated.
- **Clean-clone test** (fresh `git clone` of this repo into a temp dir with the uncommitted
  changes laid on top, no `.env`, following the README): `make setup` exit 0; `make check` with no
  AWS credentials at all: ruff clean, **398 passed**; `make web-test` **failed** (web packages
  never installed) → fixed in the Makefile → re-run from an empty `web/node_modules`: **9 passed**;
  `make local-drill ARGS=--auto-approve` with only an AWS profile: **PASS**, 12/12 settled, r01 and
  r02 escalated, 0 violations, 84.6 s, 8 decisions (one per resident; volunteer cases closed
  "checked by volunteer" only after the reply). Note: `npm ci` run by hand under global Node 20
  is refused by `engine-strict` (correct); `make` puts Node 22 first.
- Verified how: `make check` **398 passed** in this repo; infra template tests 18 passed.
- Reviewed for publication: `KICKOFF_PROMPTS.md`, `.claude/launch.json`, `.mcp.json` hold no secrets.
- Waiting on Ansh: commit and push (then CI), deploy the fixes and alarms, `make web-deploy` for
  the Evidence page, the history decision (account id, commit emails), then going public.

### 2026-09-13 12:55–17:50 PDT — Phase 6 evals built, run before and after fixes; REPORT.md written
- Ansh: keep going without commits; stop only for a human step. Decisions taken on his behalf,
  as proposed in the plan: **AgentCore Memory cut** (labelled "not built · roadmap" in the diagram
  and the Devpost draft); cold stays roadmap only.
- Built: `evals/` (Strands Evals `Experiment`/`Case`, deterministic evaluators, task-result
  cache, token meter) with four suites, `make evals`, `evals/REPORT.md`, `evals/report.json`,
  `web/public/evals-report.json`. 36 new fictional personas (`evals/personas/heat-eval` 29,
  `heat-backtest` 7); `Persona.scripted` and `GroundTruth.adversarial/accept`;
  `DrillRunner(persona_dirs=...)`.
- Results (REPORT.md has every number): suite 1 red-flag recall **86.7% → 90.0% (27/30), target
  100% not met**; all three final misses are check-ins where the simulated resident never said
  the red flag (Barbara ×2 "Feeling fine, actually"; Carmen hung up before any question).
  Suite 2 **12/22 → 24/24**; double decisions 2 → 0; RESOLVED-with-task-open 8 → 0; violations
  2 → 0. Suite 3 **20/20 forbidden attempts denied with reasons, 12/12 forced, 2/2 controls,
  0 effects, 0 violations** (before and after). Suite 4 (48 residents, June 2021 alert): alert →
  first call 7.0 s; 48/48 reached or escalated; 27.9 min projected on 6 lines, 63.1 min on 1 line,
  vs 192 min phone tree; 18 captain decisions vs 204 automated actions; 0 violations.
- Fixed (product), each with a regression test in `tests/test_phase6_fixes.py`:
  1. one captain decision per resident (`escalate_to_captain` refuses while one is open/answered);
  2. a case with a volunteer on the way stays ASSIGNED until the reply (`close_case` refuses;
     "On my way" creates a follow-up task; ASSIGNED counts as awaiting a human; the drill's
     simulated volunteer answers "They're OK");
  3. a volunteer brief could name a non-consenting resident through the model's reason (real
     violation) — names scrubbed;
  4. UNCLEAR ×3 escalates as `doorstep-high-risk-no-answer`, not unmet need;
  5. protocol completion: a recorded answer needs the question in the transcript (the agent once
     recorded four answers without asking), and a question asked and answered counts without
     `record_answer`;
  6. `checkin_max_turns` 6 → 9; red-flag line no longer says "right now"; prompts forbid timing
     promises, claims to have called anyone, and refusal lectures; understated signs count;
     5 generic disorientation backstop phrases (chosen after the Lloyd miss — disclosed).
- Not deployed: the deployed stack still runs the pre-fix code (double decisions, "right now").
- Diagram regenerated: dashboard and report no longer "planned"; Memory "not built · roadmap";
  phone bridge "operator's machine + ngrok, not hosted"; footer names cold as roadmap.
  SUBMISSION.md Devpost draft: Memory, EC2, Leaflet and cold claims removed.
- Verified how: `make check` green (ruff, **396 tests**); `make web-test` 9/9. Eval spend about
  $2.40 (metered tokens).
- Secrets scan (read-only, values never printed): gitleaks over all 29 commits on all branches:
  no leaks; gitleaks over the publishable tree (tracked + untracked, not ignored): no leaks.
  Exact-value search of every `.env` value in history and tree: none of the secrets, phone
  numbers, chat ids, passcode or SIDs appear. Findings: AWS account id in `docs/PROGRESS.md`
  (history and tree); commit author emails in history (personal gmail, and a work-domain address
  on 16 commits); all phone-shaped strings are fictional 555 numbers; `docs/local-drill.png`
  shows only fictional data.
- Not done: README, clean-clone test, CI on a pushed commit, DLQ alarm, going public.

### 2026-09-13 about 12:50 PDT — Phase 6 plan proposed (no code)
- Gate 5 confirmed passed. Ansh's schedule: submit tonight; recording starts 19:30 regardless;
  Devpost by 22:30. **Phase 6b (cold) is cut**: cold appears only as roadmap.
- Found while planning, before any code:
  1. **Two decisions for one resident is still true** (`cloud_drill_report.json`, r03): the captain
     answered the door-knock approval with "I'm handling it"; `ApprovalHook` cancels the tool, the
     model then calls `escalate_to_captain`, which raises `doorstep-unmet-need` (dec-006) for the
     same person. Sequential, not parallel, so a code guard in `escalate_to_captain` can fix it.
  2. **RESOLVED with a pending volunteer task is not intentional**: the dispatcher prompt says
     "assign_volunteer … then close_case", and `ASSIGNED -> RESOLVED` is allowed, so the case closes
     before the volunteer replies. "I'm handling it" → RESOLVED (captain handling) is intentional.
  3. An UNCLEAR ×3 case (r07, Gloria) is escalated as `doorstep-unmet-need` because its last attempt
     was answered; it should read as "could not confirm", not an unmet need.
  4. AgentCore Memory was moved to Phase 6 and is not built; the diagram and the Devpost draft
     (SUBMISSION §4) still claim it, plus EC2, Leaflet and cold.
  5. **The AWS account ID is in committed history** (`docs/PROGRESS.md`, Phase 0 smoke 03 entry,
     since Phase 0) along with a masked parent Twilio SID fragment (`AC15...c79a`).
  6. No CloudWatch alarm on the check-in DLQ (`dead_calls` exists, nothing watches it).
- Next: Ansh answers the plan's open questions and says "go".

### 2026-09-13 afternoon — CI failure after the Phase 5 push: three thread races, two of them real
- CI failed 1 of 383: `test_ids_are_unique_under_threads[dynamo]` (51 unique ids of 60). Not
  Phase 5 code; the test dates from Phase 3 and passed 30/30 on this Mac.
- Reproduced 10/10 by forcing thread switches (`sys.setswitchinterval(1e-6)`), which also
  failed two more threaded tests. Three separate causes:
  1. **moto (test fake):** its DynamoDB backend reads, copies and writes an item with no lock, so
     two threads both create a new counter at 1. Real DynamoDB applies each request atomically.
     Fix: `tests/conftest.py` serializes moto's backend requests (our code still runs threaded).
  2. **botocore (real bug, deployed code):** `client.exceptions` is built lazily without a lock;
     two threads touching it first get different exception classes, so a lost conditional write
     escaped `except client.exceptions.ConditionalCheckFailedException` as a crash instead of
     `False`/`StaleWrite`. Could happen in the coordinator, where tool bodies share a client in
     threads. Fix: match the error code (`conditional_failed`) in `store_dynamo.py` (2 places)
     and `api/doorstep_api/common.py` (3 places).
  3. **InMemoryStore (real bug, local drills):** counted decisions by iterating the live dict
     while another thread saved one ("dictionary changed size during iteration"). Fix: snapshot
     with `list(...)` in `allocate`, `cases`, `decisions` and the tool-use lookup.
- The four threaded store tests now run with tight switching every time, so these races fail
  on every run instead of only on a slow runner.
- Verified how: store contract 0/15 failing runs under tight switching (was 11/15); full suite
  3 × 383 passed CI-style (no AWS profile, no `.env`); ruff clean. Deployed (45 s): captain
  session 201, unauthenticated read 401, site 200.

### 2026-09-13 about 11:20 PDT — Gate 5 passed with Ansh's two human runs
- Laptop (Chrome incognito) 1:39 and phone (Safari private, cellular) 1:07 from opening the URL
  to reading the report, both well under 4 minutes; details in the Gates table.
- Found for Phase 6 (not fixed, voice behaviour rather than dashboard):
  1. On the phone run Nova 2 Sonic said the 911 line again and again until Ansh spoke. The
     transcript shows "I'm getting someone to check on you right now. If you feel very unwell,
     please call 911. Someone will be sent to check on you right away. If you feel very unwell,
     please call 911." The page was already out (30.2 s); the call only ended at 44.4 s when the
     model finally called `end_call` after "okay thank you". So the bridge was not holding the
     line; the model delays `end_call` after the red-flag line. Two prompt paths both ask for a
     reassurance plus 911 reminder (`checkin_text.py` lines ~119 and ~134), which invites the
     repeat. Candidate fix: once the red-flag line has been spoken and the page is out, the
     bridge closes the call deterministically after that turn.
  2. Both runs again said "someone will come by right away" (the known near-promise of timing).

### 2026-09-13 about 08:20 PDT — Phase 5 built and deployed; automated Gate 5 half passes
- Ansh's call (after the plan): no map ("add it only if it makes the project win"; the door grid
  already lays out the building and streets), save money, do not commit, keep going until a
  human step.
- Built:
  - **Sandbox API** (`api/doorstep_api/dashboard.py`, `access.py`, `snapshot.py`):
    `POST /drills`, `POST /captain/session`, `GET /incidents/{id}` (`?since=`, `?view=report`),
    `POST /incidents/{id}/decisions/{decision_id}`. HMAC access tokens (label-derived key from
    the internal secret): a sandbox token names one incident; a captain token comes from the
    passcode. `/voice/session` now requires one of them.
  - **One decision path.** The dashboard's answer is forwarded as the same `decision_response`
    event a Telegram tap becomes; the coordinator calls `respond_to_decision` with
    `Responder("web", subject)`. `respond_to_decision`, `authorize`, `claim_decision` and the
    resume are unchanged; only `resolve_responder` learned the web subjects (`captain` → the org
    captain; `sandbox:<incident>` → the addressee, only on that sandbox incident). A Telegram
    edit follows a web answer so the captain's phone never shows live buttons for a settled
    decision.
  - **Sandbox mode** end to end: `DrillRunner(mode=...)`, coordinator `sandbox` event that ignores
    every channel option it is sent (one voice resident, no Telegram, no simulated captain).
  - **Dashboard** (`web/`, React 19 + Vite 8 + Tailwind 4, Atkinson Hyperlegible Next self-hosted,
    no router or map library): live board with the door grid (floors of Juniper Court, then the
    streets; container-query columns; list view), counter strip that is also the filter,
    resident panel with the step-by-step trail, the "answer as Mei-Ling" voice call, decisions
    inbox, policies (Cedar beside plain English and the tests that prove each rule), report,
    evidence, home, captain mode, recorded drill (a real sandbox run as static JSON, $0).
  - **Infra:** private S3 + CloudFront (OAC, PriceClass 100), `doorstep-dashboard` Lambda
    (read-only on incidents; writes only its own counters), four routes with throttles, CORS
    for the site only. `make web | web-test | web-deploy | web-export | web-recorded | cap-test |
    keyboard-pass | lighthouse`.
  - Site: https://d3fia1jq6liv5t.cloudfront.net
- Found and fixed on the way:
  1. SPEC §13 tokens failed AA (Check 3.2:1, OK 4.4:1 on Concrete). Revised tokens in SPEC §13.
  2. **Passcode lockout did not lock** (since Phase 3): wrong guesses were counted, but the
     passcode was compared before the count was read, so a locked-out address still got in
     with the right guess. Both `/admin/replay` and `/captain/session` now read the counter
     first (tests: the right passcode is refused while locked). The 32-character passcode kept
     this theoretical.
  3. The phone-number mask also mangled incident ids (`sandbox-[number]ad64a`); now matches phone
     shapes only, with id and timestamp cases in the test.
  4. A Phase 2 test assumed the dashboard would send a Telegram chat id as its identity; a
     browser-supplied chat id is not an identity, so the test now proves it is refused.
  5. Throttles lowered where nobody needs them: Telegram webhook 10 → 2 rps, stage default
     5 → 2 rps (`docs/COST.md` has the flood arithmetic).
  6. The in-app browser pane's key presses do not activate native buttons (a plain injected
     `<button>` did not fire either), so the keyboard pass runs in real Chrome via puppeteer.
- Verified how: see the Gate 5 row. Costs: 2 sandbox drills + 1 synthetic voice call ≈ $0.77.
- Not done / known rough edges: see "Phase 5 rough edges" below.


### 2026-09-13 about 02:30 PDT — Phase 5 plan proposed (no code)
- Gate 4 (4a and 4b) confirmed passed. Ansh's schedule change: submit Sunday night; video recorded
  at 19:30 with whatever exists; screens built in video order (board, inbox, policies, report,
  evidence, home); the map is the designated cut.
- Found while planning, before any code:
  1. SPEC §13's tokens fail AA: Check `#B7791F` is 3.21:1 on Concrete and 2.78:1 on Shade; OK
     `#2F7D57` is 4.41:1 on Concrete and 3.83:1 on Shade. Revised tokens proposed.
  2. `resolve_responder` only maps Telegram chat ids, so a web tap cannot yet reach a roster
     member; `respond_to_decision` and `authorize` can stay untouched if only the resolver learns
     a verified web subject.
  3. `POST /voice/session` takes no sandbox token today (SPEC §11 says it should).
  4. `DrillRunner` hard-codes `mode="drill"`; sandbox mode needs a parameter.
  5. Read routes are bounded only by the stage throttle (5 rps / burst 10), which is the one
     spend a visitor can sustain without a count.
- Next: Ansh reviews tokens, wireframes, caps and the commit question, then says "go".

### 2026-09-13 about 01:10 PDT — Gate 4b passed (C4, C5, C6); what went wrong on the way
- Three real calls (details in the Gates table). Call 1 on the handset, calls 2 and 3 on speaker
  and recorded by Ansh (Mac audio plus a phone screen recording of the Telegram chat).
- **The repository changed under the session at 00:05.** The reflog shows the Phase 4 working
  changes stashed (`stash@{0}`), a checkout of `main`, then `phase1`, then back to `phase4` with the
  changes restored. None of these commands came from Claude. Verified afterwards: every file
  present, 353 tests green. The deployed stack and the running bridge were unaffected. `stash@{0}`
  is now a redundant copy for Ansh to drop.
- **Found and fixed: a result reached the incident 62 s after hang-up** (call 2). The bridge's
  InvokeAgentRuntime call hung on a dead pooled connection until botocore's 60 s read timeout.
  `CoordinatorSink` now uses a client with 5 s connect, 10 s read, 4 standard retries (every event
  is claimed once, so retrying is safe) and TCP keepalive. Test added.
- **First take of call 3 was not usable for the video.**
  1. The page left 11 s late: the connection had died again, cut from 60 s to 11 s by the new
     timeout. Ansh had moved between networks, which drops open connections. Telegram still went
     out before hang-up (by construction the bridge waits for the delivery row), but the line sat
     silent until it did, then closed abruptly.
  2. **After the correct red-flag line, Sonic said "sorry, this call cannot proceed".** Cause: the
     live backstop also sent Sonic a bracketed text nudge ("[The resident just mentioned a red
     flag…]"), which it treated as an injected instruction. **Removed**: the page never depended on
     it, and the agent's prompt already handles red flags. The test now pins that no text is
     injected into a call on a red flag.
  3. A keep-warm loop and tighter timeouts were tried and then reverted at Ansh's suggestion
     (the network explained the stalls); not needed for the retake.
- **This Mac cannot open the ngrok URL** (TLS "wrong version number" over IPv4 and IPv6, on both
  networks; a `utun4` interface suggests a VPN or security client filtering ngrok domains). Twilio
  reaches it from the internet: confirmed twice from an AgentCore cloud browser (`/healthz` →
  `{"ok":true}`). `make phone-rehearse ARGS=--local` rehearses against the bridge on localhost.
- **Retake of call 3: clean.** The page left in 0.54 s, Telegram was delivered 15.3 s before
  hang-up, and there was no refusal line.
- Found for the Phase 6 evals (not fixed): on the retake Sonic added "Someone will be sent to check
  on you right away", a near-promise of timing (the same pattern as George in Gate 4a).
- Cost: each real call is about $0.028 Twilio plus about $0.01 Sonic.

### 2026-09-12 about 21:40 PDT — phone path built, deployed and rehearsed; at C4
- Built:
  - **Cedar quiet-hours exception** (Ansh's C1 option a). The session gains
    `operator_test_call`, true only when the incident is flagged `operator_test` **and** the callee
    normalizes to exactly `OPERATOR_TEST_NUMBER` **and** is allowlisted. Live mode, consent and
    the attempt cap still apply.
  - **E.164 normalization** everywhere a number is compared (`runtime.normalize_number`,
    mirrored in `doorstep_api.common` and tested equal).
  - **Real `place_checkin_call`**: live only, allowlisted, consented, a dialer present; claims
    `DIAL#…` once; enqueues the resident, never the number.
  - `agents/planned.PlannedAction`: a one-turn model, so a deterministic call still crosses Cedar
    and the audit hook (spike S4).
  - Coordinator `live_call` event: operator only (IAM invoke, no public route), a one-resident
    live incident.
  - SQS `doorstep-checkin-jobs` (max receive 1, then a DLQ: a real call is never retried).
  - **`checkin_worker` Lambda**: re-derives everything from DynamoDB and SSM, dials only an exact
    allowlist number, refuses a parent account, claims `DIALED#…`, mints a 2-minute phone token
    and passes it as TwiML `<Parameter>`. `twilio_rest.py` is the only code that can create a call.
  - **`doorstep_voice.phone`**: Twilio Media Streams port. 8 kHz mu-law, `clear` on barge-in,
    `mark` to hear the closing line. On `start` it checks media format, subaccount SID, kill
    switch, phone token, live mode and single use before any model.
  - `make phone-bridge | phone-preflight | phone-rehearse | phone-call | phone-evidence`.
  - SSM: `operator_test_number` (from `.env` `SMOKE_CALL_TO`, only because it is `CALL_ALLOWLIST[0]`),
    `twilio/subaccount_sid|subaccount_token|from_number`, `voice_bridge_url` (set by the bridge).
- Verified how:
  - `make check` green, **353 tests** (was 322). New `tests/test_phone.py` (29):
    - normalizers agree;
    - Cedar: operator number allowed at 23:00, ordinary live incident denied, another allowlisted
      number denied with the flag, drill/sandbox denied, 25 random numbers denied;
    - the tool: queues once, refuses random numbers, other modes and a missing dialer;
    - the worker: dials once with a valid phone token; refuses drill, sandbox, a resident outside
      the incident, an unknown incident, a parent account, a bridge URL with a query, the kill
      switch;
    - **a 300-job property test**: every call is to an allowlisted number;
    - no code but `twilio_rest.py` creates calls;
    - the bridge refuses another account, a browser token and a drill token before any model;
    - **one call end to end offline**: `live_call` at 23:00 → Cedar allow (and deny without the
      flag) → queue → worker → fake Twilio → token from TwiML → bridge → red flag → captain's
      decision out while the line is held → case ESCALATED on the phone channel.
  - Two new CDK tests: only the dialer holds Twilio credentials and it cannot reach the runtime;
    the queue never retries. `make deploy` green (204 s).
  - `make phone-preflight`: **PASS 9/9**. Parameters exist, the operator number is allowlist[0],
    kill switch off, dialer deployed, ngrok reaches the bridge, the Twilio credentials are an
    active subaccount (read-only lookup), the deployed dialer refuses an unknown incident and a
    malformed job, the coordinator answers.
  - `make phone-rehearse` (synthetic Twilio client through ngrok, **no phone rang**):
    - OK script, r04: `live_call` **DENIED by Cedar in the cloud** (no phone on file); the full
      8 kHz mu-law call reached OK, RESOLVED, channel phone.
    - Urgent script, r02: DENIED again; red flag at 22.5 s, **captain's decision out at 28.6 s**,
      stream closed at 36.1 s, URGENT, ESCALATED.
  - `make telegram-webhook ARGS=info`: webhook on the API host, 0 pending.
  - `make scan-logs ARGS="--hours 1"`: 0 of 25 secret values, dialer logs included.

### 2026-09-12 about 21:10 PDT — Gate 4a passed with Ansh's voice (C2, C3)
- C2: Ansh's first call (Mei-Ling, r06) was a clean OK with 2 barge-ins, result applied 2.5 s after
  hang-up. It showed the attempt's start time was when the coordinator created the attempt, not
  when the call began. Fixed (the urgent event carries `started_at`) and redeployed; a synthetic
  call on the new build confirmed it (63 s call, 1.8 s to result).
- C3: three consecutive calls, all passing (details in the Gates table). Ansh's tab kept the
  earlier incident URL, so they landed in `drill-20260913-034202-acd6` rather than the fresh one;
  that does not change what they prove.
- Found, not fixed (recorded for the Phase 6 evals, not worth reopening a passing gate):
  1. After George pressed for a ride, Sonic said "Someone will arrange that for you right away".
     That brushes against "never promise an arrival time". Candidate prompt line: "say the team
     will follow up; never say when".
  2. For George the dispatcher raised the volunteer task and then called `close_case` in the same
     turn, so the case shows RESOLVED while the volunteer's "On my way / They're OK" task is still
     pending. Text-path behaviour, not voice-specific; Phase 6 should decide whether a case waits
     for the volunteer.
  3. For Evelyn the page came from the live backstop. Sonic said the red-flag line after the
     nudge but never called `flag_urgent` itself; the final classification added it. This is
     exactly why the backstop runs live.

### 2026-09-12 about 20:55 PDT — browser voice deployed and working end to end; at C2
- Built:
  - `checkin_text.protocol_prompt(profile, org, resident, channel)`: the one script for text and
    voice. The text prompt is byte-for-byte unchanged for all 12 residents (checked against a
    snapshot taken before the refactor).
  - `flag_urgent` calls an optional `on_urgent` callback and no longer needs a RunContext.
  - `voice/doorstep_voice/`: `tokens` (HMAC, 60 s, single use), `audio` (mu-law, identical to
    `audioop` on all inputs), `session.VoiceCheckin`, `ports.BrowserPort`, `sink`, `serve`,
    `entrypoint`.
  - Coordinator events `checkin_urgent` (page now, provisional URGENT, dispatch) and
    `checkin_attempt` (classified in the coordinator by the same layers as text; never lowers a
    mid-call page). `CheckinAttempt.key` and `.meta`.
  - `DrillRunner(voice_residents=...)` and the replay option.
  - `POST /voice/session` Lambda with a presigned URL and caps.
  - CDK: the `doorstep_voice` runtime from the same image (`DOORSTEP_SERVICE=voice`) with a
    header allowlist for the token, the Lambda, the route, CORS for `http://localhost:5174`.
  - `web/voice-test/` (AudioWorklet capture and playback, barge-in `clear`).
  - `make voice-incident | voice-page | voice-e2e | voice-evidence`. `scan-logs` now covers both
    new log groups.
- Verified how:
  - `make check` green, **322 tests** (was 278). New: `tests/test_voice.py` (25: tokens, codec,
    prompt reuse, dummy profile, the real `flag_urgent` tool, and a call on a fake Sonic: the
    resident's words page before the line closes, the agent's flag from a tool thread, negation,
    barge-in, hang-up, time cap, silence, page wait gives up) and `tests/test_voice_cloud.py`
    (20: every admission refusal before a model stream, reused token, mid-call page then a final
    "OK" that stays URGENT, the backstop on a voice transcript, link caps, kill switch). Two new
    CDK least-privilege tests.
  - `make deploy` green (110 s).
  - Deployed synthetic browser calls (`make voice-e2e`, `say` audio through
    `/voice/session` → presigned WebSocket → Sonic → coordinator), incident
    `drill-20260913-034202-acd6`:
    - **OK script, r04: PASS**. Full protocol, four answers recorded, result OK applied **1.5 s**
      after hang-up, case RESOLVED.
    - **Urgent script, r02: PASS**. Red-flag words transcribed at 25.0 s, **captain's decision
      out at 26.8 s**, the line held until the page was out, hang-up at 33.4 s. Evidence: "captain
      paged 8.2 s BEFORE hang-up", result URGENT applied 1.2 s after hang-up, case ESCALATED.
  - A real browser (in-app pane, oscillator in place of the mic, r05): presigned WebSocket
    opened, 318 KB of Sonic audio played through the worklet, transcripts shown, silence cap ended
    it, no console errors.
  - `make scan-logs ARGS="--hours 1"` PASS, 0 of 17 secrets in 1,430 events, voice included.
  - Nova 2 Sonic prices confirmed with the AWS Price List API; per-call costs in `docs/COST.md`.
- Changed in SSM: `/doorstep/caps` gains `voice_per_ip_per_hour: 10` (testing; **reset to 4 on 2026-09-13 01:45**), `voice_daily: 20`, `voice_total: 150`.
- Noise, not a bug: awscrt logs `InvalidStateError: CANCELLED` when a Sonic stream closes (seen
  since Phase 0).

### 2026-09-12 evening — Phase 4 spikes (all PASS) and the browser voice build
- Ansh at C1: quiet-hours exception (a), AgentCore Runtime WebSocket for the browser with no EC2
  ("no more money"), IAM-signed events to the coordinator, Ansh commits at the end, Gate 4a on
  the deployed runtime.
- **S1 PASS** (`scripts/spikes/p4_s1_sonic_checkin.py`, 16 kHz, the real protocol prompt and
  `CHECKIN_TOOLS` on `amazon.nova-2-sonic-v1:0`): a cross-modal text line makes Sonic greet first;
  the resident's red-flag utterance began at 27.1 s, ended at 28.8 s, and **`flag_urgent` ran at
  29.5 s** (0.75 s after the words ended); the tool body was made to block for 5 s and **61 audio
  chunks still arrived meanwhile**, so mid-call tool calls do not stall the call. Sonic then said
  the profile's red-flag line and called `end_call`.
- **S2 PASS** (`--rate 8000`, resident audio pushed through mu-law encode/decode like a phone
  line): a full OK call, all four `record_answer` calls with the right question ids, `end_call`.
  Nova accepts 8 kHz in and out, so the phone path needs no resampling.
- **S3 PASS** (`--barge-in`): talking over the greeting produced `BidiInterruptionEvent` 0.4 s
  later with `stop_reason=interrupted`; Sonic streams audio about 1.7 s ahead of real time.
- **Cost, first measurement** (S2, a 57 s call): 749 speech + 1,046 text tokens in, 737 speech +
  465 text tokens out (Nova's raw usage event; Strands only logs the split at DEBUG, so the voice
  session taps that log). At $3/$12 per 1M speech tokens in/out and $0.33/$2.75 text: **≈ $0.013
  for a one-minute call.** Prices are third-party list prices until Cost Explorer confirms.
- **S4 PASS** (`p4_s4_direct_call_cedar.py`, $0): a direct `agent.tool.x()` call cannot carry the
  RunContext (Strands merges invocation state into the tool input and fails to serialize it), so
  a deterministic real-call trigger must be a one-turn model through the normal loop. That path
  ran Cedar: allowlisted r01 **allow**; a random number **deny** with the deciding facts audited.
- Found: `record_answer` calls may arrive all at once at the end of a voice call rather than after
  each answer (S2). Harmless: the coordinator classifies the whole attempt after hang-up.

### 2026-09-12 — Architecture diagram (SUBMISSION §8)
- `docs/architecture.drawio` (source), `architecture.png` (2132 px), `architecture.svg`; generated by
  `scripts/gen_architecture.py`, exported with draw.io Desktop CLI (`brew install --cask drawio`),
  official AWS icons from draw.io's aws4 library. Checked by eye at 2132 px and scaled to 800 px.
- **Decision (Ansh):** draw the final design, including parts not built yet. Not in code as of Phase 3:
  voice bridge (EC2, BidiAgent, Nova 2 Sonic, Twilio; `place_checkin_call` is a stub), dashboard +
  CloudFront, SQS, AgentCore Memory (resident notes come from DynamoDB via `get_resident_memory`).
  Update the diagram if any of those are cut.

### 2026-09-12 about 17:40 PDT — Phase 4 plan proposed at C1 (no code)
- Gate 3 confirmed passed. Ansh's change: all of Phase 4 tonight in one 7 h box, browser voice
  shipped and committed first (C1–C3), then the phone path (C4–C6); abort after 3 h of phone work
  or at 02:00.
- Verified before planning (Strands docs MCP, AgentCore docs, Nova 2 Sonic docs, installed source
  strands-agents 1.55.1 / bedrock-agentcore 1.22.0):
  - `BidiAgent` runs each tool call as its own task (`bidi/agent/loop.py` `_run_model` →
    `_task_pool.create(self._run_tool(...))`) while the model stream keeps flowing, so a tool can
    page the captain mid-call. **Tool interrupts are not supported in bidi** (`RuntimeError`), so
    `flag_urgent` must never interrupt; the page happens in the coordinator.
  - `BedrockNovaSonicModel` sends `audio.input_rate` / `output_rate` to Nova unchanged (defaults
    16000/16000, PCM16 mono, base64) and does **no** resampling. Nova 2 Sonic accepts
    8000 | 16000 | 24000 Hz on both input and output (sonic-input-events docs); voices include
    `tiffany` and `lupe`/`carlos`. Interruptions arrive as `BidiInterruptionEvent`.
  - AgentCore Runtime serves WebSockets at `/ws` (`@app.websocket`) with SigV4 presigned URLs
    (`AgentCoreRuntimeClient.generate_presigned_url(runtime_arn, session_id, custom_headers,
    expires)`); 250 frames/s, 64 KB frames, 60 min per connection. Twilio stream URLs take no
    query string and no custom headers, so Twilio cannot use it.
  - The replay fixture's severity is **Severe**, so `real_call_guarded` denies real calls outside
    08:00–21:00 local: a phone gate after 21:00 tonight is blocked by our own policy.

### 2026-09-12 evening — CI fix after the Phase 3 commit
- CI failed 2 of 277: `test_restart_resume.py[dynamo]` raised `ProfileNotFound: doorstep`.
  `config.py` defaulted `AWS_PROFILE=doorstep` in every non-AWS process, and CI has no AWS
  profile, so boto3 failed even for clients given explicit moto keys. It passed locally only
  because the profile exists on Ansh's Mac.
- Reproduced locally with `AWS_CONFIG_FILE`/`AWS_SHARED_CREDENTIALS_FILE` pointed at an empty file.
  Fixed: the profile is defaulted only when it exists in the shared config or credentials file
  (`config._profile_exists`, tested), and the restart fixture removes `AWS_PROFILE` and uses an
  explicit session, like the other moto fixtures.
- Verified how: 278 passed both CI-like (no profile files, no `AWS_PROFILE`, `.env` set aside)
  and with `make check` locally; local processes still get `AWS_PROFILE=doorstep`.

### 2026-09-12 about 16:50 PDT — Gate 3 passed with real Telegram taps; the first run found two bugs
- **First Telegram drill** (`drill-20260912-232958-a26a`): Ansh answered 6 captain decisions
  through the webhook in 28 s, 0 violations — but it scored FAIL on "all settled", and correctly:
  1. **A volunteer task that cannot be delivered left the case ASSIGNED forever.** The captain
     chose "Send Sam"; `.env` maps only `vol-tom` to a chat, so the task was rightly not sent
     (audited) — and Rose then waited on a reply that could never come. Fix:
     `decisions.withdraw_undeliverable_task` expires the task and escalates the case back to the
     captain with the reason recorded; `deliver_pending` calls it when a channel returns no
     delivery. Test: `test_telegram.py::test_a_task_for_an_unreachable_volunteer_goes_back_to_the_captain`.
  2. **"Call the family contact" had no deterministic branch** (SPEC §4a says every option action
     must). Fix: `tools.send_family_notice`, shared by the tool and `_apply_directly`, keyed on the
     decision so the family hears once whether the model, the branch or both act; consent is
     re-checked because this path skips Cedar. Three tests, red-green verified by disabling the
     branch (two fail without it).
  That run was stopped by the operator and scored from DynamoDB; both fixes deployed (under 1 MB
  of image layers uploaded).
- **Second Telegram drill** (`drill-20260912-234122-d132`): **PASS.** Ansh followed a tap script
  and each effect was checked in DynamoDB afterwards: Rose "Send Sam" → task withdrawn, case
  ESCALATED, nothing sent; Walter, Luis, Gloria "I'm handling it" → RESOLVED; Dolores "Send Tom" →
  Tom's task delivered, "They're OK" attributed to `volunteer:vol-tom`, RESOLVED; Harold "Call the
  family contact" → exactly one family notice.
- Also found and fixed this block: **no spans for anything started through a Lambda.** The
  Lambdas forwarded an X-Ray header with `Sampled=0` and the runtime's sampler followed it; Lambda
  active tracing is now on (template test added). After the fix the drill produced 2,059 spans.
- Noted for Phase 6 (evals), not fixed: the dispatcher raised an "unmet need" decision for Gloria,
  who said she was fine (an unnecessary interruption, not a safety issue); the outbox records a
  volunteer task when it is written, before delivery, so a report counts the withdrawn Sam task as
  a message (the audit log shows it was never sent).
- Verified how: `make check` green, **277 tests**; `make scan-logs ARGS="--hours 2"` PASS (0 of 17
  secret values in 20,508 events); Cost Explorer: Sep 12 usage $5.31, credits -$5.31, net $0.

### 2026-09-12 about 16:10 PDT — Phase 3 built and deployed; cloud restart test and cloud drill pass
- Scope per Ansh: Memory moved to Phase 6; SQS check-in queue, check-in worker and the two Twilio
  Lambdas moved to Phase 4 (see the decisions register).
- Spikes (scripts kept in `scripts/spikes/`): **S1 PASS** async entrypoint returns in 0.02 s while
  `/ping` says `HealthyBusy`, a second invocation is served mid-task, session id arrives from the
  header. **S8 PASS** moto: one of 16 conditional claims wins, 200 threaded `ADD` counters unique,
  Strands `S3Storage` works through `AWS_ENDPOINT_URL_S3`; finding: prefix must be `sessions`
  (not `sessions/`). **S3 PASS** `cdk synth` of `CfnRuntime`. **S4 PASS** (deployed) the runtime
  works with the hand-written role: no `GetWorkloadAccessToken*`, no explicit KMS grant for
  `aws/ssm`; cold invoke 1.1 s, warm 0.2 s. **S5** Lambda docs do not state the bundled boto3, so
  boto3 1.43.92 is vendored into the Lambda zip (27 MB, includes `bedrock-agentcore`).
- Built: `store_dynamo.py` (identity map + versioned writes), repository primitives (`allocate`,
  `claim_decision`, `claim`, outbox rows), `applied_at` + `redrive_unapplied`, shared
  `deliver_pending` with claim-before-send, incident-scoped Telegram callback data, `cloud/`
  (coordinator, entrypoint, SSM hydration, log hardening), arm64 Dockerfile built from an
  allowlisted context, three Lambdas (`telegram_webhook`, `admin_replay`, `alert_poller`), the CDK
  stack, and `make secrets-push / deploy / seed / telegram-webhook / cloud-restart-test /
  cloud-drill / scan-logs / trace / poller`.
- Findings fixed on the way (each now has a test): (1) botocore at DEBUG logs whole SSM
  `GetParameters` response bodies, i.e. every decrypted secret; botocore is pinned to WARNING.
  (2) The replay caps counted a request that a later limit refused; limits are now checked
  narrowest first. (3) `docker push` through Docker Desktop's proxy timed out three times on the
  dependency layer; the Dockerfile's `chown -R` had also duplicated the venv into a second 420 MB
  layer. The layer is gone, and `make deploy` now uploads the image through the ECR API in 10 MB
  retried parts (`scripts/cloud/ecr_push.py`) so `cdk deploy` never runs `docker push`. (4) The
  HTTP API stage's route throttling needs the routes to exist first (explicit dependency); the
  first create rolled back and the empty stack was deleted before redeploying.
- Verified how: `make check` green, **272 tests** (was 209), all offline. New suites:
  `test_store_contract.py` (13 contract tests on both stores + 4 DynamoDB-only: second process,
  stale write, 6-process claim race, replayed start), `test_restart_resume.py` now also runs both
  halves on moto DynamoDB + S3, `test_coordinator.py` (pause in one coordinator, tap answered once
  by another, retried tap no-op), `test_lambdas.py` (13: wrong secret, triple delivery, failed
  forward releases the claim, caps, passcode lockout, idempotency key, kill switch, poller modes),
  `test_cloud_hardening.py`, `test_infra_template.py` (9 least-privilege/no-secret checks).
- Deployed: `make deploy` created stack `Doorstep` in 118 s and seeded 61 rows. Runtime
  `doorstep_coordinator-f27wW9H3Nv`.
- **`make cloud-restart-test ARGS="--delay 120"`: PASS 18/18** (incident
  `cloudtest-20260912-230043-b722`). Nova 2 Lite escalated r01 and paused (process A, boot
  `0418cd393970`); snapshot in S3; `StopRuntimeSession`; 120 s later a "Send Sam (0.2 km)" tap
  through the real webhook resumed in process B (boot `b2db6fcffb28`); exactly one volunteer task;
  the identical update twice more was dropped at the webhook (coordinator saw 2 taps, not 4); a
  genuine second tap was `already_answered`; a wrong secret got 401.
- **`make cloud-drill ARGS=--auto-approve`: PASS** (incident `drill-20260912-230318-8475`,
  accepted in 3.7 s): 12/12 RESOLVED, r01 and r02 escalated (r02 via the backstop), 0 violations,
  7 decisions all applied, 239 audit events, 88.6 s.
- **`make scan-logs`: PASS**, 0 matches for 14 secret values over 4,483 log and span events.
- **Traces:** `make trace ARGS=cloudtest-20260912-230043-b722` prints `POST /invocations ->
  invoke_agent dispatcher -> execute_event_loop_cycle -> chat us.amazon.nova-2-lite-v1:0 ->
  execute_tool escalate_to_captain` from the runtime log group's `spans` stream.

### 2026-09-12 about 11:45 PDT — Phase 3 plan proposed (no code, nothing deployed)
- Gate 2 confirmed passed; branch `phase3` clean at c48d3a4. Baseline `make check`: ruff clean,
  209 passed in 2.2 s.
- Verified before planning (agentcore docs MCP, installed package source, read-only AWS calls):
  the Runtime HTTP contract (arm64, port 8080, `POST /invocations`, `GET /ping`), background work
  via `add_async_task` + `HealthyBusy`, async entrypoints running on a dedicated worker loop
  (`bedrock_agentcore/runtime/app.py`, SDK 1.22.0), `runtimeSessionId` 33–256 chars (responses
  use `[a-zA-Z0-9][a-zA-Z0-9-_]*`, ≤ 100), idle timeout 60–28800 s (default 900), max lifetime
  default 8 h, `StopRuntimeSession`; `AWS::BedrockAgentCore::Runtime` in CloudFormation and
  `aws_cdk.aws_bedrockagentcore.CfnRuntime` in aws-cdk-lib 2.269.0; the execution-role template;
  observability = Transaction Search (already `ACTIVE`) + ADOT `opentelemetry-instrument`;
  `strands.storage.S3Storage` ships in strands-agents 1.55.1; `SnapshotSessionManager` saves at
  the end of each invocation. Account state: only `CDKToolkit` (bootstrap v32); no SSM
  parameters, runtimes, memories or tables.
- Found while reading, to fix in Phase 3: (1) decision ids and audit seqs are minted as
  `len(...)+1` while Strands runs sync tool bodies in threads (`asyncio.to_thread`,
  `strands/tools/decorator.py:654`), a latent duplicate-id race that DynamoDB latency would widen;
  (2) `drill.py:185-186` saves a case object loaded before `dispatch`, which is only safe because
  `InMemoryStore` hands out shared objects; (3) `config.py` forces `AWS_PROFILE=doorstep`, which
  breaks boto3 inside AWS; (4) Telegram callback data carries no incident id, so a webhook cannot
  route a tap; (5) the bot token is in the Telegram URL path, so httpx INFO logs and OTEL httpx
  spans would record it.
- Correction: the log entry "about 18:00 PDT — Gate 2 manual half passed" below is 18:00 **UTC**
  (11:00 PDT). Drill ids are UTC timestamps.
- Next: Ansh reviews the Phase 3 plan and answers the open decisions, then says "go".

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
| 2026-09-12 | Phase 3 scope (Ansh, "yes to all"): AgentCore Memory moves to Phase 6; `checkin-jobs` SQS, `checkin_worker`, `twilio_voice`, `twilio_status` move to Phase 4; poller defaults to `observe`; table and bucket use `RemovalPolicy.DESTROY`; `moto` added as a dev dependency | Memory is the cut line and Sunday needs to be finishing, not building; the four resources have no caller until the phone path exists; a real NWS alert must not page a phone about fictional residents | build all of PLAN Phase 3 as written |
| 2026-09-12 | One CDK (Python) stack deploys the runtime too (L1 `CfnRuntime`, arm64 `DockerImageAsset`), not the `@aws/agentcore` CLI | One `make deploy`, one IAM design with a hand-written least-privilege role; AWS docs call the CLI-created policies unsuitable for production | agentcore CLI's generated TypeScript CDK |
| 2026-09-12 | Simulated check-ins run inside the coordinator (the unchanged `DrillRunner` as a background task reporting `HealthyBusy`); every other event is a short invocation on the incident's runtime session | Reuses the Gate 1/2 drill code as-is; one process per incident keeps the DynamoDB identity map single-writer | event-driven rewrite of the drill loop on SQS (a day, not an hour) |
| 2026-09-12 | `DynamoStore` keeps a per-process identity map with versioned conditional writes; `Repository` gains `allocate`, `claim_decision`, `claim`, `append_message`/`messages` | `drill.py` saves a case loaded before dispatch (safe only with shared objects); decision ids were `len+1` while tool bodies run in threads; answered-once must be enforced by the database | fresh copies per read (silently loses dispatcher updates) |
| 2026-09-12 | Telegram callback data is `d|<incident>|<decision>|<option>` | Decision ids are unique only per incident, and the webhook must route a tap to its incident's session before looking anything up; worst case 51 of 64 bytes | globally unique decision ids plus a lookup row |
| 2026-09-12 | Webhook dedupe claims `update_id` before anything else and releases the claim only if forwarding fails; deliveries are claimed before `sendMessage` | A Telegram retry must be a no-op; a crash between claim and send leaves a decision unsent (board still shows it) rather than sent twice | claim after forwarding (a retry during a slow forward double-forwards) |
| 2026-09-12 | Secrets live in SSM SecureStrings written by `make secrets-push`; processes read them into memory at start; CDK references names only; the runtime image is built from an allowlisted staging directory | Nothing sensitive in the template, outputs, env config or image; `.env` cannot reach `cdk.out` | Secrets Manager ($0.40/secret/month); `.dockerignore` (a denylist) |
| 2026-09-12 | Quiet hours: a narrow Cedar exception for the operator's own test number (SSM `/doorstep/operator_test_number`) on incidents flagged `operator_test`; live mode, allowlist, consent and the attempts cap still apply (Ansh, C1 option a) | The replay alert is Severe, so `real_call_guarded` denies calls after 21:00 and the phone gate runs tonight; a resident's allowlisted number stays denied at night | keep the policy and run the phone gate Sunday after 08:00 |
| 2026-09-12 | Browser voice runs on AgentCore Runtime's WebSocket (`/ws`, a separate `doorstep_voice` runtime from the same image) reached by a 60 s SigV4 presigned URL; the phone bridge is the same code run locally behind ngrok; no EC2 (Ansh: "no more money") | No standing EC2 cost (~$12/month) and no plain-HTTP origin hop; AgentCore bills only while a session runs. Twilio cannot present SigV4 (no query string, no headers), so it needs its own host | EC2 t4g.small + CloudFront (PLAN) |
| 2026-09-12 | Voice processes send raw attempts and mid-call urgent events to the coordinator with IAM-signed `InvokeAgentRuntime`, and the coordinator classifies | No shared secret, one hop fewer; the deterministic layers run in one place for text and voice | HMAC-signed `/internal/checkin-result` Lambda (PLAN) |
| 2026-09-12 | Ansh commits Phase 4 himself at the end; checkpoints leave the working tree ready | Standing rule (CLAUDE.md) | Claude commits at C3/C6 |
| 2026-09-13 | Phase 5: no map; the door grid is laid out as the building's floors and the streets, with a list view | Ansh: add the map only if it makes the project win. A Leaflet map adds a library, OSM tile-policy exposure and a second place to keep accessible, and says less than the grid | Leaflet + OpenStreetMap (PLAN) |
| 2026-09-13 | Sandbox caps 1/IP/10 min, 3 everyone/10 min, 15/day, 120 total; voice 15/day, 120 total | Saves money: $52.80 hard ceiling on models for the judging period vs SPEC's 30/day | SPEC §14's 30/day with no total |
| 2026-09-13 | Dashboard tokens are HMAC, keyed by a label-derived key from `internal_hmac_secret`; no new secret | No human step, and a voice token can never pass as a dashboard token (different label) | A new SSM secret (a `.env` + `secrets-push` step) |
| 2026-09-13 | The web identity is resolved in `resolve_responder`; `respond_to_decision` is unchanged | The dashboard and Telegram share identity, idempotency, the claim and the resume by construction | A separate web answer path |
| 2026-09-13 | The API stays on its own domain; the site is on CloudFront | Through CloudFront every request would come from an edge IP and the per-IP limits would count the wrong thing | `/api/*` behind the same distribution |
| 2026-09-13 | Keyboard pass and Lighthouse are scripts (`make keyboard-pass`, `make lighthouse`) | Repeatable Gate 5 evidence; the in-app pane cannot activate buttons from the keyboard | a manual pass |
| 2026-09-11 | The active profile and the replay fixture are configuration (`DOORSTEP_PROFILE`, `DOORSTEP_ALERT_FIXTURE` in `config.py`); a test forbids hazard words anywhere else in the agent package | Makes "nothing hazard-specific is hard-coded" checkable | a default in the drill runner (caught by the guard) |

## Phase 6 rough edges (avoid on camera)

- Say "90% red-flag recall; every red flag a resident actually said was caught", never 100%.
- "7 s to first call" excludes the poller's up-to-10-minute interval; say "projected on 6 lines"
  for the 27.9 minutes.
- `docs/local-drill.png` (untracked) shows the old double decisions: don't commit or show it.
- Nova 2 Lite still sometimes says a refusal line on urgent text calls (3 of 60); Sonic may repeat
  the 911 line (not fixed).
- The phone path needs the local bridge and ngrok running.

## Phase 5 rough edges (avoid on camera, or fix in Phase 6)

- Model-written option labels appear as-is, e.g. "door-knock" and "nearest volunteer" beside
  "I'm handling it" (the dispatcher sometimes adds its own options). Same on Telegram.
- A drill raises many decisions at once (6–7 in about 90 s), so the inbox reads as a wall. The
  first card is the one to tap on camera.
- Nova 2 Sonic still says "Someone will be sent to check on you right away" after a red flag
  (a near-promise of timing; Phase 6 evals item since Gate 4a), and may repeat the 911 line until
  the resident speaks: on camera, answer "okay, thank you" as soon as it finishes the first time.
- The report's "reached everyone in 1 min 30 s" counts a case as reached when its first result
  lands; decisions can still be waiting. The report says how many are waiting.
- Evidence shows Gates 1–4 only; eval results appear when Phase 6 writes `web/public/evals-report.json`.
- Policy refusals are rare in a sandbox drill (0 in both runs), so "Refused in your drill" is
  usually empty; each rule lists the named Cedar tests that prove it instead.
- Voice links today: the UTC day's counter was already at 10 of 15 after Phase 4 and this phase
  (it resets at 17:00 PDT).

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
- [x] Phase 3: NWS user agent in `.env` and SSM (personal email for now); Telegram gate drill (2026-09-12)
- [ ] Phase 3: review and commit the working tree on `phase3`, then say "go" for Phase 4
- [ ] Note: the bot is in webhook mode now; run `make telegram-webhook ARGS=delete` before any local `make telegram-drill`, and `ARGS=set` afterwards
- [x] Phase 6: commit `phase6`; `make deploy` (dispatcher fixes + alarms); `make web-deploy` (2026-09-13)
- [x] Phase 6: SNS email subscription; push to `main`, CI green; history rewritten; repo public (2026-09-13)
- [ ] After Phase 7 is submitted: delete `~/Projects/doorstep-old` and `~/Projects/doorstep-backup.git`
- [x] **Before submitting: swap in a real second Telegram account.** (iPad, 2026-09-14) See "Second-number swap"
      below — it is one `.env` line, and it makes the role check visibly real in the demo.
- [x] **Phase 5 Gate 5 (human half):** laptop 1:39, phone 1:07 (2026-09-13)
- [ ] Phase 5: review the working tree on `phase5` and commit it yourself
- [ ] Blog post 1 (Sat AM) · [ ] Blog post 2 (Sun PM) · [ ] Blog post 3 (Mon AM)
- [ ] Volunteer Telegram account ready
- [ ] Devpost draft created Saturday
- [ ] Monday morning blocked off for recording and submission
