# Phase 0 smoke tests

Each script proves one external dependency works before any product code depends on it.
Exit code 0 means PASS, 1 means FAIL, 2 means missing configuration. Results are recorded
in `docs/PROGRESS.md`.

Run them from the repo root with `make smoke-NN`, or `uv run python scripts/smoke/<file>`.
All scripts read `.env` (copy `.env.example`).

| # | Script | Needs | Proves |
|---|---|---|---|
| 01 | `01_nova_lite_agent.py` | Bedrock access | A Strands `Agent` on Nova 2 Lite calls a tool and returns validated structured output |
| 02 | `02_nova_sonic_bidi.py` | Bedrock access, macOS `say` | A Strands `BidiAgent` on Nova 2 Sonic holds a two-turn spoken exchange: user turns synthesized with `say` are streamed in as 16 kHz PCM and answered with speech (`--text` sends them as cross-modal text, `--audio` uses a headset) |
| 03 | `03_agentcore_hello/run.sh` | Bedrock + AgentCore access, Node 22 | A minimal Strands agent deploys to AgentCore Runtime and keeps context across two invocations with the same session ID |
| 04 | `04_twilio_stream.py` | Twilio subaccount, ngrok | An outbound call from the `doorstep` subaccount streams μ-law audio to a local WebSocket through `<Connect><Stream>` |
| 05 | `05_telegram_ping.py` | Telegram bot | A message with inline buttons reaches the captain and the button tap comes back via long polling |

## Safety rules baked into the scripts

- 04 refuses to dial unless `SMOKE_CALL_TO` is in `CALL_ALLOWLIST`, and refuses to run unless the
  credentials belong to a Twilio **subaccount** (its `owner_account_sid` differs from its own SID).
  The call is capped at 60 seconds and hangs up itself after a few seconds of audio.
- 05 only messages `TELEGRAM_CAPTAIN_CHAT_ID`.
- 03 deploys one tiny runtime and is torn down with `make smoke-03 ARGS=teardown`.
- Nothing here places calls or sends messages to anyone but Ansh.

## Known noise

Smoke 02 prints two warnings after its PASS line ("InvalidStateError: CANCELLED" and "HTTP-stream has
completed"). They come from the experimental AWS CRT HTTP/2 client tearing down the Nova Sonic stream,
not from the test. The exit code is what counts.

## Order

Run 05 and 04 first (no AWS needed), then 01, 02 and 03 once Bedrock access is confirmed.
