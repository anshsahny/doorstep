# Doorstep — submission kit

## 1. Requirements checklist (from the official rules and FAQ)

Required:
- [ ] Project built with the Strands Agents SDK during the submission period (Aug 10 – Sep 14, 2026)
- [ ] Track selected: **Good Neighbor Agents**
- [ ] Text description: what it does, who it's for, how it works. Lead with the problem; name Strands Agents explicitly.
- [ ] **Public** repo (GitHub) with all source, assets and setup instructions; README; an actual MIT `LICENSE` file detected in the About section
- [ ] Architecture diagram covering:
  - user input/interface
  - the Strands agent loop
  - tools and integrations
  - AWS services
  - output
- [ ] Demo video ≤ 5:00, public on YouTube. Shows the working project end to end; the pitch covers the problem, who it's for, and why it matters.
- [ ] AWS Builder ID (enter its email)
- [ ] Testing access: live link plus captain passcode in the testing instructions; free to use until judging ends (Oct 8)
- [ ] English everywhere
- [ ] Disclosures: AI coding assistant used; any adapted samples listed with licenses; no pre-existing project code

Optional, but scored:
- [ ] Live demo link (boosts the Technical Implementation score)
- [ ] Deployed on Amazon Bedrock AgentCore (boosts the Technical Implementation score)
- [ ] 3 builder.aws posts, 0.2 each, max +0.6. Each title includes "Agents for Humans", and all must be public before the deadline.

## 2. Judging criteria → what shows it

| Criterion | Evidence we provide |
|---|---|
| Technical implementation (also the tie-breaker) | Strands used deeply: a Graph pipeline, interrupts with session persistence, Cedar policies, the voice agent, structured output, hooks, and Evals. Deployed on AgentCore Runtime with Observability. A live demo. CI and tests. |
| Design | A complete loop across a real phone call, Telegram and the dashboard. A judge sandbox. An accessible, purpose-built UI. The incident report. |
| Potential impact | The coroner-backed problem; a specific user (the block captain); the measured backtest (time to full coverage vs. a phone tree); a clear buyer path. |
| Creativity | An agent that phones people, triggered by the real world rather than a user. Minimal-disclosure briefs for volunteers. The "model proposes, policy decides" split. Multilingual voice. |
| Presentation | A tight ≤5-minute video: cold open on a real call, then the problem, the end-to-end demo, how it works, and the evidence. |

Because judging may include automated AI analysis, the README carries an explicit "How Doorstep uses Strands Agents" table with file links, plus this criteria map.

## 3. Verified facts for the pitch (use only these unless newly verified)

- The BC Coroners Service confirmed **619** heat-related deaths during the June 25 – July 1, 2021 heat dome.
- **98%** died indoors, **56%** lived alone, and **67%** were 70 or older.
- The coroner's chief medical officer said extreme heat alerts must be paired with clear protocols so no time is lost responding.
- The review asked that home and community care services identify and prioritize clients who live alone for home visits during extreme heat.

Sources:
- CBC: https://www.cbc.ca/news/canada/british-columbia/bc-heat-dome-coroners-report-1.6480026
- Globe and Mail: https://www.theglobeandmail.com/canada/british-columbia/article-bc-must-be-better-prepared-for-next-heat-wave-coroner/
- BC Coroners Service report: https://www2.gov.bc.ca/assets/gov/birth-adoption-death-marriage-and-divorce/deaths/coroners-service/death-review-panel/extreme_heat_death_review_panel_report.pdf

Framing: the same heat dome hit Portland, which is why the replay uses the real NWS Portland warning from June 2021. Don't quote Oregon death figures unless you've verified them.

## 4. Devpost text (draft — fill in bracketed numbers from REPORT.md)

**Name:** Doorstep
**Tagline:** When a heat warning hits, Doorstep checks on every at-risk neighbour — and only asks a human when it matters.

**Inspiration.** In the 2021 heat dome, 619 people died in British Columbia alone. Almost all of them died at home, more than half of them alone. The warnings went out; what didn't happen, fast enough, was someone checking on them. Neighbourhood teams keep lists of the people most at risk, but a phone tree run by one volunteer on a hot afternoon doesn't scale.

**What it does.** Doorstep is a Good Neighbor agent built with the **Strands Agents SDK**. It watches National Weather Service alerts for a volunteer group's area. Each hazard is a plug-in profile: extreme heat ships fully tested; extreme cold, smoke and power shutoffs are roadmap profiles, not built. When a warning hits, it:
- ranks the group's opt-in list by risk (age, living alone, no AC);
- phones every resident with a natural voice agent (Amazon Nova 2 Sonic), in their language;
- classifies each check-in, sends cooling-centre info, and assigns volunteers for water or visits.

It interrupts the block captain only for the decisions a human must make: an urgent red flag, a high-risk neighbour who won't answer, or a need it can't meet. In our replay of the June 2021 Portland warning, it reached or escalated all [48] residents in [N] minutes, with [k] human decisions. The same list is roughly 3 hours of phone calls for one volunteer.

**How we built it.**
- **Strands Agents SDK (Python):**
  - a Graph (assess → triage → outreach)
  - structured-output agents for classification
  - a dispatcher with tools
  - Strands interrupts with session persistence, so the agent pauses for the captain and resumes after a Telegram tap
  - Cedar authorization on every tool call (allowlisted calls only, minimal disclosure, no agent-initiated emergency calls)
  - hooks for a full audit trail
  - a BidiAgent for voice
- **Amazon Bedrock AgentCore:** Runtime hosts the coordinator and the browser voice agent; Observability traces every run. Resident preferences ("hard of hearing — speak slowly") come from the roster in DynamoDB; AgentCore Memory is roadmap.
- **Models:** Nova 2 Lite (reasoning), Nova 2 Sonic (voice), Nova Micro (simulated residents for drills and evals).
- **AWS:** Lambda, API Gateway, SQS, DynamoDB, EventBridge Scheduler, S3, CloudFront, SSM, CDK. (The phone bridge runs on the operator's machine behind ngrok; it is not hosted.)
- **Channels:** Twilio Media Streams (phone), Telegram (captain and volunteers), React dashboard.
- **Evidence:** Strands Evals with 40 simulated residents, including hidden red flags and prompt-injection attempts. Red-flag recall [100%], policy violations [0].

**Challenges.** [Bridging phone audio to Nova 2 Sonic; resuming interrupted agents across processes; catching understated red flags.]

**Accomplishments.** [A real phone call that pages a human mid-conversation; the replay numbers; zero policy violations in the red team.]

**What we learned.** [Short and honest.]

**What's next.**
- Pilots with a senior building and a neighbourhood emergency team
- Canadian alerts (Environment and Climate Change Canada)
- More hazard profiles: extreme cold, smoke, and power shutoffs — each is a profile file, not a rebuild
- Opt-in enrolment by phone

**Built with:** strands-agents, amazon-bedrock, amazon-nova, bedrock-agentcore, aws-lambda, amazon-dynamodb, amazon-sqs, amazon-eventbridge, amazon-cloudfront, aws-cdk, cedar, twilio, telegram, python, react, typescript, tailwind

**Testing instructions (draft):**
1. Open [live URL].
2. Click **Run a drill** to get your own sandbox with 12 fictional residents.
3. Click **Answer a call** to talk to Doorstep as a resident. Try saying you feel dizzy.
4. Approve a decision in the inbox.
5. Open **Report** and **Evidence**.
6. Captain view: passcode [xxxx].
7. The sandbox never places real calls; the video shows the real phone path. All data is fictional.

## 5. Video script (target 4:40, hard max 5:00)

| Time | Visual | Voiceover (short) |
|---|---|---|
| 0:00–0:20 | Phone rings on the desk. Answer on speaker: Doorstep's check-in, as "Mrs. Chen". Red-flag phrase, then the captain's phone buzzes. | (No voiceover; let the call play.) |
| 0:20–0:30 | Title card: "Doorstep — built with Strands Agents on AWS" | "That call was made by an agent, not a person." |
| 0:30–1:10 | Stats on screen (sources cited on screen) | 619 deaths, 98% at home, 56% alone. Alerts went out; check-ins didn't. Who it's for: the volunteer captain with a list and a day job. |
| 1:10–1:25 | Home page | What Doorstep does, in one sentence. |
| 1:25–3:20 | Live demo | 1. Replay the 2021 Portland warning → activation with its reasoning. 2. Door grid fills; triage waves. 3. One Spanish call. 4. Urgent flag → Telegram decision → captain taps "Send Tom" → volunteer taps "They're OK". 5. Policy panel: denial of a group broadcast with personal details; agent rewrites. 6. Incident report: [N] minutes vs. ~3 hours, [k] human decisions. |
| 3:20–4:10 | Architecture diagram, then code flashes; 10-second shot of `heat.yaml` (cold, smoke, power shutoffs named as roadmap profiles) | Strands Graph, interrupts + sessions, Cedar, BidiAgent on Nova 2 Sonic; AgentCore Runtime, Observability traces. "Every hazard is a profile: same agent, different questions and danger signs." |
| 4:10–4:30 | Evidence page | 40 simulated residents: red-flag recall, zero policy violations, injection attempts blocked. |
| 4:30–4:50 | Closing | Why it matters, what's next (pilots, Canada), live link. |

Recording tips:
- Record the phone call with the phone on speaker next to a good microphone.
- Mask your personal number on screen.
- Use real UI, not mockups.
- Add captions.
- Keep cuts fast.
- Rehearse the drill once so timings are predictable.
- Use drill mode so demo timing is deterministic.

## 6. Blog posts (builder.aws, each title includes "Agents for Humans")

1. **Saturday: "Agents for Humans: building an agent that checks on neighbours during a heat wave"**
   - The problem and the coroner findings
   - Why the Good Neighbor track
   - The architecture plan
   - Choosing Nova + AgentCore
   - Cost plan (all-Nova, free-tier credits)
   - What's next
2. **Sunday: "Agents for Humans: pausing a Strands agent for a human — interrupts, sessions and Cedar on AgentCore"**
   - Code snippets for the interrupt hook, the session manager and resume, the Cedar policies with a denial example, and the audit hook
   - Lessons learned
3. **Monday: "Agents for Humans: giving my agent a phone — Nova 2 Sonic, Twilio Media Streams, and what 40 simulated residents taught me"**
   - The voice bridge and audio conversion
   - Mid-call escalation
   - Eval results before and after fixes
   - Links to the repo and video

Each post: 600–1,000 words, one diagram or screenshot, a link to the repo and demo. Tags: agents-for-humans, strands-agents, bedrock-agentcore, amazon-nova.

## 7. README outline

1. Title, one-line pitch, badges (MIT, CI), hero GIF of the door grid filling
2. The problem (3 lines + sources) and who it's for
3. Demo: live link, video, 60-second tour
4. How it works (diagram) and the human decision points
5. **How Doorstep uses Strands Agents** (feature → file table)
6. AWS services used and why
7. Safety and privacy (policies, consent-only, fictional data, never replaces 911)
8. Evidence (eval table from REPORT.md)
9. Quickstart: `make setup` → `make local-drill` (needs only AWS credentials with Bedrock), then full deploy
10. Configuration and cost (link docs/COST.md)
11. Judging criteria map (section 2 above)
12. Limitations (honest), roadmap
13. Disclosures (built during the submission period, AI coding assistant used, adapted samples with licenses), license

## 8. Architecture diagram contents

- **Users:** captain (Telegram + dashboard), volunteers (Telegram), residents (phone / browser), judges (dashboard sandbox)
- **Strands agents box:** the Graph nodes, dispatcher, voice BidiAgent, and the agent loop (model → tools → reasoning → response), with Cedar sitting on the tool boundary
- **AWS:**
  - AgentCore Runtime, Observability (Memory: roadmap, labelled as not built)
  - Bedrock with Nova 2 Lite, Nova 2 Sonic, Nova Micro
  - Lambda, API Gateway, SQS, DynamoDB, EventBridge Scheduler, S3, CloudFront, SSM (no EC2: the phone bridge runs locally)
- **External:** NWS API, Twilio, Telegram
- **Outputs:** calls, tasks, decisions, incident report

Use AWS architecture icons (draw.io). Commit both the source file and a PNG.

## 9. Final pre-submit checklist (Monday)

- [ ] Live URL works in incognito; sandbox drill completes; voice works; caps return a friendly message
- [ ] The repo's About section shows MIT; the README renders; no secrets (run gitleaks again)
- [ ] Video is public, under 5:00, with captions; links in the description
- [ ] All three blog posts are public, with "Agents for Humans" in the titles; URLs collected
- [ ] Every Devpost field is filled; track set to Good Neighbor; Builder ID email; screenshots (door grid, Telegram decision, policy denial, report, traces); diagram uploaded
- [ ] Testing instructions include the passcode
- [ ] Submitted, then the confirmation page re-checked
- [ ] Budget alarms on; infrastructure stays up until Oct 8; `main` frozen
