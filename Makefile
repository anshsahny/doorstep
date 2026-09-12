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
	local-drill telegram-drill deploy evals web web-deploy

help: ## list targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

setup: ## create venv (Python 3.12), install all deps, install pre-commit hooks, install Node dev tools
	$(UV) python install 3.12
	$(UV) sync --all-groups
	$(UV) run pre-commit install
	$(MAKE) node-check
	npm install

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

deploy: ## Phase 3: CDK deploy + AgentCore deploy
	@echo "make deploy arrives in Phase 3"; exit 1

evals: ## Phase 6: run eval suites, write evals/REPORT.md
	@echo "make evals arrives in Phase 6"; exit 1

web: ## Phase 5: run the dashboard locally
	@echo "make web arrives in Phase 5"; exit 1

web-deploy: ## Phase 5: build + publish the dashboard
	@echo "make web-deploy arrives in Phase 5"; exit 1
