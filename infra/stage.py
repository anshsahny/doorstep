"""Stage the runtime image's build context from an allowlist (PLAN Phase 3, least privilege).

A Docker build context is everything in a directory unless something excludes it, and CDK copies
that directory into `cdk.out`. Building from the repo root would put `.env` one `.dockerignore`
mistake away from an image in ECR. Copying only what the image needs makes that impossible.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ["pyproject.toml", "uv.lock", "README.md", "agent", "voice", "data", "evals/personas"]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", ".env*", "*.egg-info")


def stage_runtime(dest: Path | None = None) -> Path:
    dest = dest or ROOT / "build" / "runtime"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for rel in INCLUDE:
        src = ROOT / rel
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, target, ignore=SKIP)
        else:
            shutil.copy2(src, target)
    shutil.copy2(ROOT / "infra" / "runtime" / "Dockerfile", dest / "Dockerfile")
    return dest


def locked_version(package: str) -> str:
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    match = re.search(rf'name = "{re.escape(package)}"\nversion = "([^"]+)"', lock)
    if not match:
        raise RuntimeError(f"{package} is not in uv.lock")
    return match.group(1)


def stage_lambdas(dest: Path | None = None) -> Path:
    """The Lambda zip contents: `api/doorstep_api` plus the locked boto3, vendored.

    Vendored because the Lambda docs do not say which boto3 the runtime carries, and the
    `bedrock-agentcore` client must exist (Spike S5). boto3 and its dependencies are pure
    Python, so installing them for linux/arm64 from this Mac is exact. Reused while the locked
    version is unchanged.
    """
    dest = dest or ROOT / "build" / "lambda"
    version = locked_version("boto3")
    marker = dest / ".boto3-version"
    if not (marker.exists() and marker.read_text() == version):
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        subprocess.run(
            [
                "uv", "pip", "install", "--quiet", "--target", str(dest),
                "--python-platform", "aarch64-manylinux2014", "--python-version", "3.12",
                f"boto3=={version}",
            ],
            check=True,
        )  # fmt: skip
        marker.write_text(version)
    target = dest / "doorstep_api"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(ROOT / "api" / "doorstep_api", target, ignore=SKIP)
    # The token format has one implementation, shared with the voice runtime (stdlib only).
    voice = dest / "doorstep_voice"
    if voice.exists():
        shutil.rmtree(voice)
    voice.mkdir()
    for name in ("__init__.py", "tokens.py"):
        shutil.copy2(ROOT / "voice" / "doorstep_voice" / name, voice / name)
    return dest


if __name__ == "__main__":
    print(stage_runtime())
    print(stage_lambdas())
