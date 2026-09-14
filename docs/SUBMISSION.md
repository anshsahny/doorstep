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

## 4. Devpost text (final, numbers from evals/REPORT.md, 2026-09-14 00:48 UTC run)

**Name:** Doorstep
**Tagline:** When a heat warning hits, Doorstep checks on every at-risk neighbour — and only asks a human when it matters.

**Inspiration.** In the 2021 heat dome, 619 people died in British Columbia alone. 98% of them died indoors, and more than half lived alone. The warnings went out; what didn't happen, fast enough, was someone checking on them. Neighbourhood teams keep lists of the people most at risk, but a phone tree run by one volunteer on a hot afternoon doesn't scale.

**What it does.** Doorstep is a Good Neighbor agent built with the **Strands Agents SDK**. It watches National Weather Service alerts for a volunteer group's area. Each hazard is a plug-in profile: extreme heat ships fully tested; extreme cold, smoke and power shutoffs are roadmap profiles, not built. When a warning hits, it:
- ranks the group's opt-in list by risk (age, living alone, no AC);
- phones every resident with a natural voice agent (Amazon Nova 2 Sonic), in English or Spanish;
- classifies each check-in, sends cooling-centre info, and assigns volunteers for water or visits.

It interrupts the block captain only for the decisions a human must make: an urgent red flag, a high-risk neighbour who won't answer, or a need it can't meet. We replayed the real June 2021 NWS Portland Excessive Heat Warning against a fictional list of 48 residents. The first call started 7 seconds after the alert. All 48 were reached or escalated, which projects to 27.9 minutes on 6 phone lines, with 18 captain decisions against 204 automated actions. The same list is roughly 3.2 hours of phone calls for one volunteer.

**How we built it.**
- **Strands Agents SDK (Python):**
  - a Graph (assess → triage → outreach)
  - structured-output agents for classification
  - a dispatcher with tools
  - Strands interrupts with session persistence, so the agent pauses for the captain and resumes after a Telegram tap, even in a different process
  - Cedar authorization on every tool call (allowlisted calls only, minimal disclosure, no agent-initiated emergency calls)
  - hooks for a full audit trail
  - a BidiAgent for voice
- **Amazon Bedrock AgentCore:** Runtime hosts the coordinator and the browser voice agent; Observability traces every run. Resident preferences ("hard of hearing — speak slowly") come from the roster in DynamoDB; AgentCore Memory is roadmap.
- **Models:** Nova 2 Lite (reasoning), Nova 2 Sonic (voice), Nova Micro (simulated residents for drills and evals).
- **AWS:** Lambda, API Gateway, SQS, DynamoDB, EventBridge Scheduler, S3, CloudFront, SSM, CloudWatch, CDK. (The phone bridge runs on the operator's machine behind ngrok; it is not hosted.)
- **Channels:** Twilio Media Streams (phone), Telegram (captain and volunteers), React dashboard.
- **Evidence:** Strands Evals with 40 simulated residents, including hidden red flags and prompt-injection attempts. Red-flag recall 90% over 30 urgent check-ins (every miss was a call where the simulated resident never said the red flag), 20 of 20 forbidden actions denied by policy, 0 policy violations.

**Challenges.**
- Getting the page out *during* the call. A deterministic red-flag backstop listens to the live transcript; on a real phone call the captain's Telegram message went out 15 seconds before the resident hung up.
- Resuming a paused agent in a different process. The captain might tap twenty minutes later, after the process that asked has died, and exactly one volunteer task has to go out: not zero, not two.
- Understated red flags. A resident who says "fine, bit foggy, I put the milk in the oven" is confused, and the model read it as OK. Our evals caught it; we changed the prompts and added phrases to a deterministic backstop that can only raise a classification, never lower it. Some simulated residents never said their red flag at all, and no classifier can catch that; a direct screening question is next.

**Accomplishments.**
- A real phone call that pages a human mid-conversation.
- A replay of a real 2021 warning: 48 of 48 residents reached or escalated, 18 human decisions.
- 0 policy violations, and 20 of 20 red-team attempts refused with an audit reason.
- A public judge sandbox that can't place real calls, with spending caps and a kill switch.

**What we learned.** A single eval run lies, so we ran every urgent persona three times. The model should propose and deterministic code should decide: state changes, disclosure and permissions live outside the model. And we didn't hit our own 100% recall target, so we say 90%.

**What's next.**
- Pilots with a senior building and a neighbourhood emergency team
- Canadian alerts (Environment and Climate Change Canada)
- More hazard profiles: extreme cold, smoke, and power shutoffs — each is a profile file, not a rebuild
- Opt-in enrolment by phone

**Built with:** strands-agents, amazon-bedrock, amazon-nova, bedrock-agentcore, aws-lambda, amazon-api-gateway, amazon-dynamodb, amazon-sqs, amazon-eventbridge, amazon-s3, amazon-cloudfront, amazon-cloudwatch, aws-cdk, cedar, twilio, telegram, python, react, typescript, tailwind, leaflet

**Links:**
- Live: https://d3fia1jq6liv5t.cloudfront.net
- Repo: https://github.com/anshsahny/doorstep
- Blog 1: https://builder.aws.com/content/3JGXWIyhgd50C2viO3fHFXM74aE/agents-for-humans-ai-agents-that-check-on-neighbourhood-residents-during-a-heat-wave
- Blog 2: https://builder.aws.com/content/3JKG2tNTQWAICanKIpDCJsYSEYe/agents-for-humans-pausing-a-strands-agent-for-human-verification-and-resuming-after-a-tap
- Blog 3: `<BLOG_3_URL>`
- Video: `<YOUTUBE_URL>`

**Testing instructions:**
1. Open https://d3fia1jq6liv5t.cloudfront.net (Chrome or Safari; allow the microphone for step 3).
2. Click **Run a drill** to get your own sandbox with 12 fictional residents.
3. When the call panel appears, click **Answer as <name>** to talk to Doorstep as that resident. Try saying you feel dizzy and confused: the captain is paged before you hang up.
4. Approve a decision in the inbox.
5. Open **Report**, **Policies** and **Evidence**.
6. Captain view (**Captain mode** link at the bottom of the home page): passcode `<paste CAPTAIN_PASSCODE from .env into Devpost only; never commit it>`. Optional: after signing in, enter incident `drill-20260914-065148-ce60` to see the cloud drill from the video, where the captain and a volunteer answered by real Telegram taps.
7. The sandbox never places real calls or sends Telegram messages; the video shows the real phone path. All data is fictional. Drills and voice calls have daily caps; if one is reached, the site says so and offers a recorded drill.

## 5. Video script (target 4:40, hard max 5:00)

| Time | Visual | Voiceover (short) |
|---|---|---|
| 0:00–0:20 | Real phone call on speaker (bridge + ngrok running; number masked). Doorstep's check-in; you say "I feel dizzy and confused, I'm not sure what day it is". Captain's Telegram buzzes **before** you hang up. | (No voiceover; let the call play.) |
| 0:20–0:30 | Title card: "Doorstep — built with Strands Agents on AWS" | "That call was made by an agent, not a person. And it paged a human before the call ended." |
| 0:30–1:10 | Stats on screen (sources cited on screen) | 619 deaths, 98% indoors, 56% alone. Alerts went out; check-ins didn't. Who it's for: the volunteer block captain with a list and a day job. |
| 1:10–1:25 | Home page | What Doorstep does, in one sentence. |
| 1:25–3:20 | Live demo on the deployed site | 1. **Run a drill**: your own sandbox of 12 fictional residents against the real June 2021 NWS Portland heat warning (the alert shows at the top of the board). 2. Door grid fills in risk order; open one door to show why that resident was called when they were and each step the agent took. 3. **Answer as \<name\>**: browser voice call on Nova 2 Sonic; say you feel dizzy and confused; the decision lands in the inbox while you're still on the call. When it repeats the 911 line, say "okay, thank you". 4. Inbox: answer the **first** card (captain). Say the real Telegram version is what you saw in the cold open. 5. **Policies**: the written Cedar rules and their named tests; voiceover the red-team example from Evidence (the agent tried to phone a number not on the allowlist; Cedar denied it and logged why). 6. **Report**: "all 12 reached" in about a minute and a half, how many decisions a person made, and how many are still waiting. |
| 3:20–4:10 | Architecture diagram, then code flashes; 10-second shot of `heat.yaml` (cold, smoke, power shutoffs named as roadmap profiles) | Strands Graph, interrupts + sessions, Cedar on every tool call, BidiAgent on Nova 2 Sonic; AgentCore Runtime, Observability traces. Memory is roadmap. "Every hazard is a profile: same agent, different questions and danger signs." |
| 4:10–4:30 | Evidence page | 40 simulated residents. "90% red-flag recall; every red flag a resident actually said was caught." 20 of 20 forbidden actions refused, 0 policy violations. The full 2021 replay: 48 residents, first call in 7 s, everyone reached or escalated in a projected 27.9 minutes on 6 lines vs about 3.2 hours by phone tree, 18 human decisions. |
| 4:30–4:50 | Closing | Why it matters, what's next (pilots, Canadian alerts, more hazard profiles), live link and repo. |

Do not say or show: 100% recall; a Spanish call (untested on voice); two separate Telegram phones (one phone plays both roles); an agent "rewriting" a denied broadcast; "3 hours" next to the 12-resident drill report; `docs/local-drill.png`.

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

## 9. Final pre-submit checklist (checked 2026-09-14 about 00:30 PDT)

- [x] Live URL works; recorded drill renders; caps and kill switch return a friendly message (`make cap-test` 15/15 on the deployed API, kill switch restored to `off`). Sandbox drill and browser voice last completed in the video takes the same night.
- [x] The repo's About section shows MIT; the README renders; gitleaks clean over all refs (38 commits)
- [ ] Video is public, under 5:00, captions on; live link and repo in the description (§10)
- [ ] All three blog posts are public, with "Agents for Humans" in the titles; URLs collected (posts 1 and 2 public; 3 pending)
- [ ] Every Devpost field is filled (§10)
- [ ] Testing instructions include the passcode (paste from `.env`, never commit it)
- [ ] Submitted, then the confirmation page re-checked
- [x] Budget alarms on; infrastructure stays up until Oct 8
- [ ] `v1.0` tagged and pushed; `main` frozen

## 10. Ready to submit

Everything below is final except the two `<...>` values Ansh adds: `<YOUTUBE_URL>` and `<BLOG_3_URL>`.

### YouTube upload
- **Title:** Doorstep — an agent that checks on neighbours during a heat wave (Strands Agents on Amazon Bedrock AgentCore)
- **Visibility:** Public (not unlisted). Not made for kids. Captions: auto-generated, then review the transcript for "Doorstep", "Strands", "AgentCore", "Nova", "Cedar".
- **Description:**

  > When a heat warning hits, Doorstep phones every at-risk neighbour on a volunteer group's list, sorts who is OK from who isn't, sends a volunteer to the doors that need a knock, and interrupts the block captain only for the decisions a person must make.
  >
  > Built with the Strands Agents SDK on Amazon Bedrock AgentCore, Amazon Nova 2 Lite and Nova 2 Sonic, Cedar policies, Twilio and Telegram. Entry for the AWS "Agents for Humans" hackathon, Good Neighbor track.
  >
  > Try it: https://d3fia1jq6liv5t.cloudfront.net
  > Code (MIT): https://github.com/anshsahny/doorstep
  >
  > All residents shown are fictional. The alert replayed is the real, public NWS Portland Excessive Heat Warning of June 2021. Doorstep never calls 911 itself: it tells the resident to call and pages a person. Heat-dome figures: BC Coroners Service, Extreme Heat Death Review Panel.

### Devpost, field by field
| Field | Value |
|---|---|
| Project name | Doorstep |
| Elevator pitch / tagline | §4 tagline |
| Track | **Good Neighbor Agents** |
| About the project | §4 from **Inspiration** through **What's next** (Devpost headings: Inspiration, What it does, How we built it, Challenges we ran into, Accomplishments that we're proud of, What we learned, What's next) |
| Built with | §4 Built with list, one tag at a time |
| "Try it out" links | Live site; GitHub repo; blog posts 1–3 |
| Video demo link | `<YOUTUBE_URL>` |
| Image gallery | the files in "Screenshots" below, in that order |
| Architecture diagram | `docs/architecture.png` |
| Testing instructions | §4 Testing instructions, with the captain passcode pasted from `.env` `CAPTAIN_PASSCODE` |
| AWS Builder ID email | Ansh's Builder ID email (enter by hand) |
| Deployed on AgentCore? | Yes: coordinator and browser voice run on AgentCore Runtime; Observability on |
| Blog post URLs | post 1: https://builder.aws.com/content/3JGXWIyhgd50C2viO3fHFXM74aE/agents-for-humans-ai-agents-that-check-on-neighbourhood-residents-during-a-heat-wave · https://builder.aws.com/content/3JKG2tNTQWAICanKIpDCJsYSEYe/agents-for-humans-pausing-a-strands-agent-for-human-verification-and-resuming-after-a-tap · `<BLOG_3_URL>` |
| AI tools disclosure | Claude Code used as a coding assistant during the submission period; adapted samples as in the README Disclosures |

### Screenshots (1600×1000, taken from the live site's recorded drill, $0)
Produced outside the repo (screenshots are not committed): `home.png`, `board.png` (door grid), `decisions.png`, `policies.png` (Cedar rules), `report.png`, `evidence.png`, plus `docs/architecture.png`. Telegram: take a frame of the captain's "Send Tom" card and Tom's task from the video footage; do not use a frame that shows the model-written "911 call" button.

### Judging-period headroom (2026-09-14 about 00:30 PDT)
Sandbox drills used 13 of 120 for the whole period (daily cap 15, resets 17:00 PDT); voice calls 17 of 120. Kill switch `off`. Budget `doorstep-monthly` $25 with alerts; three CloudWatch alarms OK.
