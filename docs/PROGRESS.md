# Doorstep — progress log

Current phase: **0 — Accounts, smoke tests, scaffold**
Next gate: **Gate 0**
Time now vs plan: on track / behind by __ h

## Setup completed before Phase 0

- AWS account + root MFA + $5 budget; IAM user `doorstep-dev`; CLI profile `doorstep` verified with `aws sts get-caller-identity`.
- Tools: uv, awscli, gitleaks, gh (authenticated), portaudio, Docker Desktop (hello-world OK), node@22 for this project (global Node stays v20).
- Private repo `doorstep` with MIT LICENSE; kit committed.

## Gates

| Phase | Gate | Status | Verified how | Date |
|---|---|---|---|---|
| 0 | 5 smoke tests, private repo + LICENSE + kit committed, gitleaks | ☐ | | |
| 1 | Unit tests + local 12-resident drill | ☐ | | |
| 2 | Interrupt resume test + Telegram approval loop | ☐ | | |
| 3 | Cloud drill via AgentCore + Telegram webhook + traces | ☐ | | |
| 4a | 3 browser check-ins | ☐ | | |
| 4b | 2 real calls + mid-call escalation | ☐ | | |
| 5 | Judge flow < 4 min, Lighthouse a11y ≥ 90 | ☐ | | |
| 6 | Evals targets, CI green, clean-clone setup | ☐ | | |
| 7 | Submitted | ☐ | | |

## Smoke tests (Phase 0)

| # | Test | Result | Notes |
|---|---|---|---|
| 1 | Nova 2 Lite Strands agent | ☐ | model ID used: |
| 2 | Nova 2 Sonic BidiAgent | ☐ | |
| 3 | AgentCore Runtime hello | ☐ | |
| 4 | Twilio subaccount stream call | ☐ | |
| 5 | Telegram ping + button | ☐ | |

## Log (newest first)

### [date time] — [phase]
- Done:
- Verified how:
- Next:
- Blockers:
- Decisions:

## Decisions register

| Date | Decision | Why | Alternatives |
|---|---|---|---|

## Disclosures (goes into the README)

- AI coding assistant: Claude Code (used during the submission period).
- Adapted samples (URL, license, what was adapted):
- Pre-existing code: none.

## Human to-do (Ansh)

- [ ] Blog post 1 (Sat AM) · [ ] Blog post 2 (Sun PM) · [ ] Blog post 3 (Mon AM)
- [ ] Volunteer Telegram account ready
- [ ] Devpost draft created Saturday
- [ ] Monday morning blocked off for recording and submission
