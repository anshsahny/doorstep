"""Gate 2's real test: a decision survives the process that raised it.

An interrupt is raised in one interpreter, which then exits. A second interpreter — no shared
memory, no agent object, nothing but the session directory and the decision record — answers it.
The volunteer task must be sent exactly once, and a second tap must change nothing.

This runs offline (scripted model) so it belongs in `make test` and in CI, where a regression in
the resume path would otherwise only show up in a live drill.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from moto.server import ThreadedMotoServer

from conftest import DATA
from doorstep_agent.store_dynamo import DynamoBackend, create_table

HALF = Path(__file__).parent / "helpers" / "restart_half.py"
CAPTAIN_CHAT = "42424242"


@pytest.fixture(params=["memory", "dynamo"])
def backend(request: pytest.FixtureRequest) -> Iterator[dict[str, str]]:
    """Extra environment for both halves: nothing for memory, a shared moto server for dynamo."""
    if request.param == "memory":
        yield {}
        return
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = ThreadedMotoServer(port=port, verbose=False)
    server.start()
    url = f"http://127.0.0.1:{port}"
    # moto keeps one state per test process, so every test gets its own table and bucket.
    suffix = uuid.uuid4().hex[:8]
    table, bucket = f"doorstep-restart-{suffix}", f"doorstep-restart-{suffix}"
    env = {
        "RESTART_BACKEND": "dynamo",
        "DOORSTEP_ENV": "cloud",  # no .env, no AWS_PROFILE: exactly as in the container
        "DOORSTEP_TABLE": table,
        "DOORSTEP_SESSIONS_BUCKET": bucket,
        "AWS_ENDPOINT_URL_DYNAMODB": url,
        "AWS_ENDPOINT_URL_S3": url,
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1",
    }
    kw = {"region_name": "us-east-1", "aws_access_key_id": "testing"}
    kw["aws_secret_access_key"] = "testing"
    dynamo = boto3.client("dynamodb", endpoint_url=url, **kw)
    create_table(dynamo, table)
    DynamoBackend(table, "juniper-court", client=dynamo).seed_static(DATA)
    boto3.client("s3", endpoint_url=url, **kw).create_bucket(Bucket=bucket)
    env["_S3_URL"] = url
    try:
        yield env
    finally:
        server.stop()


def run_half(half: str, work: Path, extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {
        **{k: v for k, v in os.environ.items() if k != "AWS_PROFILE" or not extra},
        # The roster stores chat ids as env references; the captain's resolves to this one, so
        # half B's responder is recognised as cap-maria and nobody else.
        "TELEGRAM_CAPTAIN_CHAT_ID": CAPTAIN_CHAT,
        "TELEGRAM_VOLUNTEER_CHAT_IDS": "51515151,52525252",
        **{k: v for k, v in extra.items() if not k.startswith("_")},
    }
    return subprocess.run(
        [sys.executable, str(HALF), half, str(work)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_interrupt_survives_a_restart_and_the_tool_runs_exactly_once(
    tmp_path: Path, backend: dict[str, str]
) -> None:
    first = run_half("a", tmp_path, backend)
    assert first.returncode == 0, f"half A failed:\n{first.stdout}\n{first.stderr}"
    assert "HALF A: paused" in first.stdout

    second = run_half("b", tmp_path, backend)
    assert second.returncode == 0, f"half B failed:\n{second.stdout}\n{second.stderr}"

    # The captain's answer was applied in the second process...
    assert "HALF B: applied" in second.stdout
    # ...and the same tap again was recognised, not replayed.
    assert "HALF B AGAIN: already_answered" in second.stdout

    sent = (tmp_path / "outbox.tsv").read_text(encoding="utf-8").splitlines()
    tasks = [line for line in sent if "\tvolunteer_task\t" in line]
    assert len(tasks) == 1, f"the volunteer task must be sent exactly once, got: {tasks}"
    assert tasks[0].startswith("B\t"), "it must be sent by the process that got the approval"
    assert "vol-tom" in tasks[0]


def test_the_paused_session_is_stored_between_the_two_processes(
    tmp_path: Path, backend: dict[str, str]
) -> None:
    assert run_half("a", tmp_path, backend).returncode == 0
    if backend:
        s3 = boto3.client(
            "s3",
            endpoint_url=backend["_S3_URL"],
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        bucket = backend["DOORSTEP_SESSIONS_BUCKET"]
        keys = [o["Key"] for o in s3.list_objects_v2(Bucket=bucket)["Contents"]]
        latest = [k for k in keys if k.endswith("snapshot_latest.json")]
        assert latest and latest[0].startswith("sessions/session/doorstep-inc-test-r04"), keys
        assert not list((tmp_path / "sessions").rglob("*.json")), "nothing may land on local disk"
        saved = s3.get_object(Bucket=bucket, Key=latest[0])["Body"].read().decode()
    else:
        snapshots = list((tmp_path / "sessions").rglob("snapshot_latest.json"))
        assert snapshots, "the paused agent must be persisted, or nothing could resume it"
        saved = snapshots[0].read_text(encoding="utf-8")
    assert "doorstep-approve-door-knock" in saved
    assert '"activated": true' in saved
