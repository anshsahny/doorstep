# Doorstep coordinator for Amazon Bedrock AgentCore Runtime (arm64, HTTP contract on :8080).
# Built from an allowlisted context staged by infra/stage.py: .env and local state cannot be in it.
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOCKER_CONTAINER=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

RUN pip install --no-cache-dir uv==0.12.13 \
 && useradd --create-home --uid 1000 doorstep

WORKDIR /app
# Dependencies first (one cached layer), then the project. Owned by root and only readable by
# the runtime user: no `chown -R`, which would copy the whole virtualenv into a second layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --group runtime --no-install-project

COPY agent ./agent
COPY voice ./voice
COPY data ./data
COPY evals/personas ./evals/personas
RUN UV_COMPILE_BYTECODE=0 uv sync --frozen --no-dev --group runtime

ENV PATH=/app/.venv/bin:$PATH \
    DOORSTEP_ENV=cloud \
    OTEL_PYTHON_DISABLED_INSTRUMENTATIONS=httpx,requests,urllib3,urllib

USER doorstep
EXPOSE 8080
CMD ["opentelemetry-instrument", "python", "-m", "doorstep_agent.cloud.entrypoint"]
