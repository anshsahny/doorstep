"""Build and upload the runtime image that `cdk synth` asked for, before `cdk deploy` (make deploy).

CDK names the image after a hash of its build context and skips publishing when that tag is
already in ECR. Doing the build here and the upload through `ecr_push.py` (chunked, retried,
host network) means `cdk deploy` never runs `docker push`, which timed out repeatedly through
Docker Desktop's proxy on this machine.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.cloud import ecr_push  # noqa: E402
from scripts.cloud.stack import session  # noqa: E402


def main() -> int:
    assets = json.loads((ROOT / "cdk.out" / "Doorstep.assets.json").read_text())
    ecr = session().client("ecr")
    for asset in assets.get("dockerImages", {}).values():
        context = ROOT / "cdk.out" / asset["source"]["directory"]
        for dest in asset["destinations"].values():
            repo, tag = dest["repositoryName"], dest["imageTag"]
            try:
                ecr.describe_images(repositoryName=repo, imageIds=[{"imageTag": tag}])
                print(f"{repo}:{tag[:12]} already in ECR")
                continue
            except ecr.exceptions.ImageNotFoundException:
                pass
            local = f"doorstep-runtime:{tag[:12]}"
            subprocess.run(
                ["docker", "build", "--platform", "linux/arm64", "-q", "-t", local, str(context)],
                check=True,
            )
            ecr_push.main(local, repo, tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
