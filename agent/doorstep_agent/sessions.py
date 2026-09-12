"""Strands session persistence for the agents that can pause (PLAN Phase 2 task 2).

A dispatcher run that interrupts must be resumable by a different process minutes later, so its
conversation and interrupt state live in a Strands session keyed by incident and resident.

Locally that session is a file tree; in the cloud only the `Storage` changes (`S3Storage`, chosen
by `DOORSTEP_SESSIONS_BUCKET`), not the session id, the manager, or anything that calls this
module. The Strands docs recommend `SnapshotSessionManager` for new single-agent sessions, and
Spike B confirmed it round-trips interrupt state — including the pending tool execution — across
a real process boundary.
"""

from __future__ import annotations

import re

from strands.session import SnapshotSessionManager
from strands.storage import LocalFileStorage, S3Storage, Storage

from .config import Settings

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


def session_id(incident_id: str, resident_id: str, agent_name: str = "dispatcher") -> str:
    """A stable session id for one agent working one resident's case.

    Stable is the whole point: the id is recomputed from the case in a later process, with no
    shared memory, to find the paused conversation again.
    """
    parts = [_UNSAFE.sub("-", part).strip("-") for part in (incident_id, resident_id, agent_name)]
    return "doorstep-" + "-".join(p for p in parts if p)


def build_storage(settings: Settings) -> Storage:
    """Where sessions are kept: S3 when a bucket is configured, the local directory otherwise.

    The prefix is `sessions` with no trailing slash: `S3Storage` adds the separator itself, and
    `"sessions/"` produces `sessions//…` keys (Spike S8).
    """
    if settings.sessions_bucket:
        return S3Storage(settings.sessions_bucket, prefix="sessions")
    settings.sessions_dir.mkdir(parents=True, exist_ok=True)
    return LocalFileStorage(str(settings.sessions_dir))


def build_session_manager(settings: Settings, session: str) -> SnapshotSessionManager:
    """A session manager for one conversation.

    One live writer per session id: Doorstep's sessions are per resident per incident, so two
    cases never share one.
    """
    return SnapshotSessionManager(session_id=session, storage=build_storage(settings))
