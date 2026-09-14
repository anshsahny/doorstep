# Doorstep — developer commands. Keep CLAUDE.md "Commands" in sync with this file.
#
# Node: this project uses Node 22 from Homebrew's node@22 keg (pinned in .nvmrc).
# The global Node stays on v20; never brew link/unlink node or npm install -g.
export PATH := /opt/homebrew/opt/node@22/bin:$(PATH)
SHELL := /bin/bash
.DEFAULT_GOAL := help

UV ?= uv
PY := $(UV) run python

.PHONY: help setup node-check test lint fmt check \
	smoke-01 smoke-02 smoke-03 smoke-04 smoke-05 \
	local-drill telegram-drill secrets-push deploy destroy seed telegram-webhook \
	cloud-restart-test cloud-drill scan-logs trace poller evals web web-deploy \
	web-export web-test web-recorded cap-test lighthouse keyboard-pass

help: ## list targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

setup: ## create venv (Python 3.12), install all deps, install pre-commit hooks, install Node dev tools and the dashboard's packages
	$(UV) python install 3.12
	$(UV) sync --all-groups
	$(UV) run pre-commit install
	$(MAKE) node-check
	npm install
	cd web && npm ci

node-check: ## fail unless Node 22 is first on PATH
	@v=$$(node --version); case $$v in v22.*) echo "node $$v OK";; *) echo "Expected Node 22, found $$v. See CLAUDE.md (Node version)."; exit 1;; esac

test: ## run unit tests
	$(UV) run pytest -q

lint: ## ruff check + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt: ## auto-fix lint and format
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

check: lint test ## lint + tests

# --- Phase 0 smoke tests (see scripts/smoke/README.md) ---
smoke-01: ## Nova 2 Lite Strands agent: tool call + structured output
	$(PY) scripts/smoke/01_nova_lite_agent.py

smoke-02: ## Nova 2 Sonic BidiAgent: two spoken turns via macOS say (ARGS=--text or ARGS=--audio)
	$(PY) scripts/smoke/02_nova_sonic_bidi.py $(ARGS)

smoke-03: node-check ## AgentCore Runtime hello: create, deploy, invoke twice, status (ARGS=teardown to remove)
	bash scripts/smoke/03_agentcore_hello/run.sh $(ARGS)

smoke-04: ## Twilio subaccount call with <Connect><Stream> to a local WebSocket via ngrok
	$(PY) scripts/smoke/04_twilio_stream.py

smoke-05: ## Telegram message with inline buttons; waits for the tap (ARGS=--whoami lists chat IDs)
	$(PY) scripts/smoke/05_telegram_ping.py $(ARGS)

# --- Later phases ---
local-drill: ## run a drill locally with simulated residents and a terminal board (ARGS=--auto-approve etc.)
	$(PY) scripts/local_drill.py $(ARGS)

telegram-drill: ## same drill, but decisions go to the real roster chats and wait for real taps
	$(PY) scripts/local_drill.py --telegram --no-clear $(ARGS)

# --- Phase 3: cloud (AWS profile doorstep, us-east-1) ---
# Override with `make deploy AWS_PROFILE=yours` if your CLI profile is not called doorstep.
AWS_PROFILE ?= doorstep
CLOUD := AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=us-east-1 CDK_DEFAULT_REGION=us-east-1

secrets-push: ## copy secrets from .env into SSM SecureStrings (ARGS=--generate-missing)
	$(CLOUD) $(PY) scripts/secrets_push.py $(ARGS)

deploy: node-check ## CDK deploy of the whole stack (runtime image, Lambdas, API, table), then seed
	$(UV) sync --all-groups
	$(CLOUD) npx cdk synth Doorstep --quiet
	$(CLOUD) $(PY) scripts/cloud/publish_image.py
	$(CLOUD) npx cdk deploy Doorstep --require-approval never --outputs-file cdk.out/outputs.json
	$(CLOUD) $(PY) scripts/seed.py

destroy: node-check ## tear the stack down (SSM parameters are kept)
	$(CLOUD) npx cdk destroy Doorstep --force

seed: ## write the fictional org, roster and volunteers to DynamoDB
	$(CLOUD) $(PY) scripts/seed.py

telegram-webhook: ## ARGS=set|delete|info: switch the bot between the webhook and long polling
	$(CLOUD) $(PY) scripts/telegram_webhook.py $(ARGS)

cloud-restart-test: ## Gate 3: pause in one runtime process, answer via the real webhook, resume in another (ARGS=--delay 600)
	$(CLOUD) $(PY) scripts/cloud/restart_resume.py $(ARGS)

cloud-drill: ## Gate 3: 12-resident drill in AWS via POST /admin/replay (ARGS=--telegram or --auto-approve)
	$(CLOUD) $(PY) scripts/cloud/replay_cloud.py $(ARGS)

scan-logs: ## search recent runtime/Lambda logs and spans for any SSM secret value (in memory)
	$(CLOUD) $(PY) scripts/cloud/scan_logs.py $(ARGS)

trace: ## print the spans of one incident's runtime session (ARGS=<incident id>)
	$(CLOUD) $(PY) scripts/cloud/trace.py $(ARGS)

poller: ## ARGS=on|off: enable or disable the 10-minute NWS alert schedule
	$(CLOUD) $(PY) scripts/cloud/poller.py $(ARGS)

# --- Phase 4: voice ---

voice-incident: ## start a drill whose residents wait for a voice call (ARGS="--telegram --residents r01,r04")
	$(CLOUD) $(PY) scripts/voice/voice.py incident $(ARGS)

voice-page: ## serve the browser voice test page on http://localhost:5174
	$(CLOUD) $(PY) scripts/voice/voice.py page

voice-e2e: ## synthetic browser call through the deployed path (ARGS="--incident <id> --resident r04 --script ok|urgent")
	$(CLOUD) $(PY) scripts/voice/voice.py e2e $(ARGS)

voice-evidence: ## what an incident recorded for each voice call (ARGS=<incident id>)
	$(CLOUD) $(PY) scripts/voice/voice.py evidence $(ARGS)

phone-bridge: ## run the Twilio Media Streams bridge on :8765 behind ngrok (leave it running)
	$(CLOUD) $(PY) scripts/voice/phone.py bridge

phone-preflight: ## read-only checks before a real call (no phone rings)
	$(CLOUD) $(PY) scripts/voice/phone.py preflight

phone-rehearse: ## whole phone chain with a synthetic Twilio client, no phone rings (ARGS="--script urgent")
	$(CLOUD) $(PY) scripts/voice/phone.py rehearse $(ARGS)

phone-call: ## REAL call to the operator's own phone (ARGS=--telegram)
	$(CLOUD) $(PY) scripts/voice/phone.py call $(ARGS)

phone-evidence: ## what an incident recorded for a phone call (ARGS=<incident id>)
	$(CLOUD) $(PY) scripts/voice/voice.py evidence $(ARGS)

evals: ## run the four eval suites (Bedrock, ~$2) and write evals/REPORT.md (ARGS="--suite checkin --label before", ARGS=--report for $0)
	$(PY) -m evals.run $(ARGS)

# --- Phase 5: dashboard ---

web-export: ## regenerate web/src/generated (profile labels, Cedar policies + plain English)
	$(PY) scripts/web_export.py

web: node-check web-export ## run the dashboard on http://localhost:5173 against the deployed API
	$(CLOUD) $(PY) -c "import json; o=json.load(open('cdk.out/outputs.json'))['Doorstep']; open('web/public/config.json', 'w').write(json.dumps({'apiUrl': o['ApiUrl']}) + chr(10))"
	cd web && npm install && npx vite --port 5173 --strictPort

web-test: node-check ## typecheck and unit-test the dashboard
	cd web && (test -d node_modules || npm ci) && npx tsc --noEmit && npx vitest run

web-deploy: node-check web-export ## build the dashboard and publish it to S3 + CloudFront
	cd web && npm install && npx tsc --noEmit && npx vite build
	$(CLOUD) $(PY) scripts/web_deploy.py

web-recorded: ## save a finished drill as the dashboard's recorded drill (ARGS=<incident id>)
	$(CLOUD) $(PY) scripts/web_recorded.py $(ARGS)

cap-test: ## Gate 5: prove every sandbox and voice cap and the kill switch on the deployed API (~$0.38)
	$(CLOUD) $(PY) scripts/cloud/cap_test.py $(ARGS)

keyboard-pass: node-check ## Gate 5: keyboard-only pass in real Chrome (ARGS="--live" starts one sandbox drill, ~$0.38)
	cd web && node scripts/keyboard-pass.mjs $$(python3 -c "import json; print(json.load(open('../cdk.out/outputs.json'))['Doorstep']['SiteUrl'])") $(ARGS)

lighthouse: node-check ## Lighthouse accessibility scores for the deployed dashboard (ARGS=--url ...)
	$(CLOUD) $(PY) scripts/web_lighthouse.py $(ARGS)
