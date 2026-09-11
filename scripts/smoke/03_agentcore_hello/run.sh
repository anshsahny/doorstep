#!/usr/bin/env bash
# Smoke test 03: deploy a minimal Strands agent to Amazon Bedrock AgentCore Runtime and invoke it
# twice with one runtime session ID (a 36-char UUID; the API minimum is 33 characters).
#
#   bash run.sh            create the CLI project if missing, deploy, invoke twice, show status
#   bash run.sh teardown   remove every resource from the project and deploy the empty state
#
# Uses the project-local AgentCore CLI (npm @aws/agentcore) under Node 22. The generated
# DoorstepHello/ project is gitignored; this script regenerates it when absent.
set -euo pipefail
export PATH="/opt/homebrew/opt/node@22/bin:$PATH"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
AGENTCORE="$REPO/node_modules/.bin/agentcore"
PROJECT="$HERE/DoorstepHello"

export AWS_PROFILE="${AWS_PROFILE:-doorstep}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="$AWS_REGION"

if [[ ! -x "$AGENTCORE" ]]; then
  echo "AgentCore CLI not found at $AGENTCORE. Run: make setup" >&2
  exit 2
fi

if [[ "${1:-run}" == "teardown" ]]; then
  cd "$PROJECT"
  "$AGENTCORE" remove all -y
  "$AGENTCORE" deploy -y
  echo "Teardown deploy finished. Verify with: $AGENTCORE status"
  exit 0
fi

if [[ ! -f "$PROJECT/agentcore/agentcore.json" ]]; then
  echo "... generating the AgentCore project with 'agentcore create'"
  (cd "$HERE" && "$AGENTCORE" create --project-name DoorstepHello --name hello \
      --language Python --framework Strands --model-provider Bedrock --memory none \
      --build CodeZip --skip-git --skip-install)
fi

# The generated CDK app is TypeScript (needs tsc) and the agent has its own pyproject.
if [[ ! -x "$PROJECT/agentcore/cdk/node_modules/.bin/tsc" ]]; then
  echo "... installing the generated CDK project's npm dependencies (Node 22)"
  (cd "$PROJECT/agentcore/cdk" && npm install --no-fund --no-audit)
fi
if [[ ! -d "$PROJECT/app/hello/.venv" ]]; then
  echo "... syncing the generated agent's Python dependencies"
  (cd "$PROJECT/app/hello" && uv sync)
fi

cp "$HERE/main.py" "$PROJECT/app/hello/main.py"
cd "$PROJECT"

echo "... deploying (the first deploy also bootstraps CDK in the account)"
"$AGENTCORE" deploy -y

SESSION_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
echo "... session id: $SESSION_ID (${#SESSION_ID} chars)"

echo "... invoke 1"
"$AGENTCORE" invoke --session-id "$SESSION_ID" \
  --prompt "Please remember the word juniper. Reply with just OK." | tee "$HERE/.last-invoke-1.log"

echo "... invoke 2 (same session)"
"$AGENTCORE" invoke --session-id "$SESSION_ID" \
  --prompt "Which word did I ask you to remember? Reply with just that word." | tee "$HERE/.last-invoke-2.log"

"$AGENTCORE" status

if grep -qi juniper "$HERE/.last-invoke-2.log"; then
  echo "PASS: AgentCore Runtime kept context across two invocations in one session"
else
  echo "FAIL: the second reply did not contain 'juniper'" >&2
  exit 1
fi
