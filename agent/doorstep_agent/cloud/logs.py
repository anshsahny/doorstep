"""Logging for the deployed coordinator: useful, and unable to leak the bot token.

The Telegram token is part of the request URL (`/bot<token>/sendMessage`). httpx logs every
request URL at INFO, and OpenTelemetry's httpx instrumentation records it as `url.full` on a
span, so either would copy the token into CloudWatch. The Dockerfile disables that
instrumentation (`OTEL_PYTHON_DISABLED_INSTRUMENTATIONS`); this pins the loggers.

botocore is pinned too: at DEBUG it logs whole response bodies, and the body of an SSM
`GetParameters` call with decryption is every secret in plain text (found by
`tests/test_cloud_hardening.py`).
"""

from __future__ import annotations

import logging

QUIET = ("httpx", "httpcore", "urllib3", "botocore", "strands")
DISABLED_INSTRUMENTATIONS = ("httpx", "requests", "urllib3", "urllib")


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for name in QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
