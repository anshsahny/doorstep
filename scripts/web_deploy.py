"""Publish the built dashboard (`web/dist`) to the site bucket and refresh CloudFront.

Hashed assets are cached for a year; `index.html`, `config.json` and the recorded drill are
revalidated on every load, so a redeploy shows up at once without a full invalidation.
"""

from __future__ import annotations

import json
import mimetypes
import sys
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "web" / "dist"
NO_CACHE = "no-cache, max-age=0, must-revalidate"
IMMUTABLE = "public, max-age=31536000, immutable"


def main() -> int:
    outputs = json.loads((ROOT / "cdk.out" / "outputs.json").read_text())["Doorstep"]
    if not (DIST / "index.html").exists():
        print("web/dist is empty: run the build first", file=sys.stderr)
        return 1
    (DIST / "config.json").write_text(json.dumps({"apiUrl": outputs["ApiUrl"]}) + "\n")

    session = boto3.Session()
    s3 = session.client("s3")
    bucket = outputs["SiteBucket"]
    uploaded = set()
    for path in sorted(p for p in DIST.rglob("*") if p.is_file()):
        key = path.relative_to(DIST).as_posix()
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix in (".woff2", ".woff"):
            kind = f"font/{path.suffix[1:]}"
        cache = IMMUTABLE if key.startswith("assets/") else NO_CACHE
        s3.upload_file(
            str(path), bucket, key, ExtraArgs={"ContentType": kind, "CacheControl": cache}
        )
        uploaded.add(key)
    # Remove files a previous build left behind (old hashed assets stay harmless but cost bytes).
    stale = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        stale += [o["Key"] for o in page.get("Contents", []) if o["Key"] not in uploaded]
    for start in range(0, len(stale), 1000):
        s3.delete_objects(
            Bucket=bucket, Delete={"Objects": [{"Key": k} for k in stale[start : start + 1000]]}
        )
    session.client("cloudfront").create_invalidation(
        DistributionId=outputs["SiteDistributionId"],
        InvalidationBatch={
            "Paths": {"Quantity": 1, "Items": ["/index.html"]},
            "CallerReference": str(time.time()),
        },
    )
    print(f"uploaded {len(uploaded)} files, removed {len(stale)}; {outputs['SiteUrl']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
