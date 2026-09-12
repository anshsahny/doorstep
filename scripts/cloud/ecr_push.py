"""Push a local image to ECR through the ECR API, in resumable chunks (make ecr-push).

Why this exists: on this machine `docker push` goes through Docker Desktop's internal proxy and
repeatedly timed out on the one large dependency layer (three failed deploys, 2026-09-12). This
uploads the same bytes from the host with boto3 — `docker save` of a containerd-store image
yields the exact compressed layer blobs and manifest — retrying each 10 MB part. Once the tag is
in ECR, `cdk deploy` sees it and skips its own push.

    uv run python scripts/cloud/ecr_push.py <local image ref> <repository> <tag>
"""

from __future__ import annotations

import json
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.cloud.stack import session  # noqa: E402

PART = 10 * 1024 * 1024
MANIFEST_TYPES = (
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)


def retry(what: str, fn, attempts: int = 6):
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - network flake; report and retry
            if attempt == attempts:
                raise
            print(f"    {what}: {type(exc).__name__}, retry {attempt}")
            time.sleep(2 * attempt)


def blob(root: Path, digest: str) -> Path:
    algo, hexdigest = digest.split(":")
    return root / "blobs" / algo / hexdigest


def platform_manifest(root: Path) -> tuple[bytes, str]:
    """Follow index.json down to the linux/arm64 image manifest."""
    doc = json.loads((root / "index.json").read_text())
    for _ in range(4):
        manifests = doc.get("manifests", [])
        for m in manifests:
            if (
                m.get("mediaType") in MANIFEST_TYPES
                and m.get("platform", {}).get("architecture", "arm64") == "arm64"
                and "attestation" not in json.dumps(m.get("annotations", {}))
            ):
                data = blob(root, m["digest"]).read_bytes()
                return data, m["mediaType"]
        nested = next(
            (m for m in manifests if m.get("mediaType", "").endswith("index.v1+json")), None
        )
        if nested is None:
            break
        doc = json.loads(blob(root, nested["digest"]).read_bytes())
    raise SystemExit("no linux/arm64 image manifest in the saved image")


def upload(ecr, repository: str, path: Path, digest: str) -> None:
    size = path.stat().st_size
    upload_id = retry("initiate", lambda: ecr.initiate_layer_upload(repositoryName=repository))[
        "uploadId"
    ]
    with path.open("rb") as fh:
        first = 0
        while first < size:
            chunk = fh.read(PART)
            last = first + len(chunk) - 1
            retry(
                f"part {first // PART}",
                lambda c=chunk, f=first, lb=last: ecr.upload_layer_part(
                    repositoryName=repository,
                    uploadId=upload_id,
                    partFirstByte=f,
                    partLastByte=lb,
                    layerPartBlob=c,
                ),
            )
            first = last + 1
    retry(
        "complete",
        lambda: ecr.complete_layer_upload(
            repositoryName=repository, uploadId=upload_id, layerDigests=[digest]
        ),
    )


def main(image: str, repository: str, tag: str) -> int:
    ecr = session().client("ecr")
    with tempfile.TemporaryDirectory() as tmp:
        tar = Path(tmp) / "image.tar"
        print(f"saving {image}")
        subprocess.run(
            ["docker", "save", "--platform", "linux/arm64", "-o", str(tar), image], check=True
        )
        root = Path(tmp) / "oci"
        with tarfile.open(tar) as t:
            t.extractall(root, filter="data")
        manifest_bytes, media_type = platform_manifest(root)
        manifest = json.loads(manifest_bytes)
        blobs = [manifest["config"], *manifest["layers"]]
        available = ecr.batch_check_layer_availability(
            repositoryName=repository, layerDigests=[b["digest"] for b in blobs]
        )["layers"]
        present = {x["layerDigest"] for x in available if x.get("layerAvailability") == "AVAILABLE"}
        for b in blobs:
            mb = b["size"] / 1e6
            if b["digest"] in present:
                print(f"  {b['digest'][:19]} {mb:7.1f} MB already in ECR")
                continue
            print(f"  {b['digest'][:19]} {mb:7.1f} MB uploading")
            upload(ecr, repository, blob(root, b["digest"]), b["digest"])
        try:
            ecr.put_image(
                repositoryName=repository,
                imageManifest=manifest_bytes.decode(),
                imageManifestMediaType=media_type,
                imageTag=tag,
            )
        except ecr.exceptions.ImageAlreadyExistsException:
            pass
    print(f"pushed {repository}:{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:4]))
