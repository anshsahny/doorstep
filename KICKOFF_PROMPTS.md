# Claude Code setup and prompts for Doorstep

## One-time setup (Claude Code in the desktop app)

1. `uv` is installed. The MCP servers in `.mcp.json` run through `uvx`.
2. The kit files must already be in your repo:
   - `CLAUDE.md`, `.mcp.json`, `.gitignore`, `.nvmrc` → repo root
   - `docs/*` → `docs/`
3. Open the Claude desktop app, go to the **Code** tab, and choose the `doorstep` folder. The app includes Claude Code, so there's no separate install.
4. Claude Code reads `CLAUDE.md` and `.mcp.json` at startup. When it asks, approve the two MCP servers (`strands` and `agentcore`). The AgentCore server can also create and modify AWS resources, so the prompts below tell Claude to use it for docs and ask before any action.
5. **If a server shows as failed:** the desktop app may not see Homebrew's PATH. Run `which uvx` in your terminal and replace `"uvx"` in `.mcp.json` with that full path (e.g., `/opt/homebrew/bin/uvx`). Then start a new session.

## Session hygiene

- Run one phase per session. When a gate passes, commit, then start a new session for the next phase. CLAUDE.md and PROGRESS.md carry the context between sessions.
- Keep your secrets in `.env` yourself. Never paste tokens into the chat.
- Review each gate yourself before saying "go".

---

## Prompt 1 — Phase 0 kickoff (paste as-is)

> Read CLAUDE.md, docs/PLAN.md, docs/SPEC.md and docs/PROGRESS.md fully before doing anything.
>
> Setup already done (see PROGRESS.md): AWS profile `doorstep` works as IAM user `doorstep-dev`; Docker works; gh is logged in; the private repo exists; node@22 is installed for this project only.
>
> Follow the Node rules in CLAUDE.md exactly. My global Node must stay v20.
>
> We are starting Phase 0. First, use the `strands` and `agentcore` MCP servers to verify the current install and package names for:
> - Strands Agents (Python) with the `[cedar]` extra and the experimental bidi/voice extras
> - strands-agents-tools
> - Strands Evals
> - the AgentCore CLI and Python SDK
> - the Nova 2 Lite and Nova 2 Sonic model IDs (tell me the `aws` CLI command to confirm IDs in my account)
>
> Use the agentcore MCP server for documentation only unless I approve an action.
>
> Then propose the Phase 0 plan:
> - the scaffold files you'll create
> - the exact commands you'll run
> - the five smoke scripts
> - a numbered list of the remaining human steps (PLAN.md Phase 0 items marked ☐), in the order you'll need them
>
> Do not write code until I approve the plan. After approval:
> 1. Scaffold the repo.
> 2. Write the smoke scripts.
> 3. Run each smoke test as soon as I confirm the matching human step is done.
> 4. Record results in docs/PROGRESS.md, commit, and stop at Gate 0 with a summary of what passed and how you verified it.

## Prompt 2 — Start any later phase (replace N)

> Read CLAUDE.md and docs/PROGRESS.md, then the Phase N section of docs/PLAN.md and the SPEC sections it references.
>
> Check that Gate N−1 is marked passed in PROGRESS.md. If it isn't, stop and tell me.
>
> Propose your plan for Phase N:
> - tasks in order
> - files touched
> - any spike scripts you'll write to verify unfamiliar APIs
> - the tests that prove the gate
> - human steps I need to do
> - where the time-box risk is
>
> Wait for my approval, then build in small verified steps. Run the tests after each step. Update PROGRESS.md at the end of each work block. Stop at Gate N with evidence.

## Prompt 3 — Resume after a break

> Read CLAUDE.md and docs/PROGRESS.md. Summarize in five lines:
> - where we are
> - what's verified
> - what's in progress
> - blockers
> - the next three actions
>
> Then continue from the next action, following the current phase's plan.

## Prompt 4 — Gate review (before you say "go")

> Run the full Gate N check now. Show:
> - the test output
> - the exact commands you ran
> - anything that didn't pass or that you had to work around
> - any shortcuts that would weaken the demo, the judging criteria in docs/SUBMISSION.md §2, or the safety rules in CLAUDE.md
>
> Update the gate row in PROGRESS.md only if everything passed.

## Prompt 5 — Stuck, or over the time box

> We're over the time box on [task]. Stop. Give me:
> 1. Root cause as best you know.
> 2. The smallest path to working, with a time estimate.
> 3. The phase's cut-line option and what we lose.
>
> Recommend one. Don't change code until I pick.

## Prompt 6 — Pre-submission audit (Phase 7)

> Audit the project against docs/SUBMISSION.md §1, §2, §7, §8 and §9. For each item, mark done or missing, with a link or evidence.
>
> Then do all of the following:
> - Run gitleaks on the full git history.
> - Follow the README from a fresh clone in a temp directory.
> - Hit every public endpoint.
> - Confirm the sandbox caps and kill switch work.
> - Confirm the LICENSE file is detected as MIT.
>
> Give me a punch list ordered by judging impact.
