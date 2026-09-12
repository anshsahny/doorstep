"""Spike S8 (Phase 3): can moto stand in for DynamoDB and S3 in offline tests?

Proves: conditional puts reject a second writer, `ADD` counters are atomic under threads, and
Strands `S3Storage` reaches a moto server through `AWS_ENDPOINT_URL_S3` with no code hook, so a
subprocess test can share one backend by environment variables alone.

    uv run python scripts/spikes/p3_s8_moto.py
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import boto3
from moto.server import ThreadedMotoServer

server = ThreadedMotoServer(port=5055, verbose=False)
server.start()
url = "http://127.0.0.1:5055"
os.environ.update(
    AWS_ACCESS_KEY_ID="test",
    AWS_SECRET_ACCESS_KEY="test",
    AWS_REGION="us-east-1",
    AWS_DEFAULT_REGION="us-east-1",
    AWS_ENDPOINT_URL_DYNAMODB=url,
    AWS_ENDPOINT_URL_S3=url,
)
os.environ.pop("AWS_PROFILE", None)

ddb = boto3.client("dynamodb")
ddb.create_table(
    TableName="t",
    BillingMode="PAY_PER_REQUEST",
    KeySchema=[
        {"AttributeName": "PK", "KeyType": "HASH"},
        {"AttributeName": "SK", "KeyType": "RANGE"},
    ],
    AttributeDefinitions=[
        {"AttributeName": "PK", "AttributeType": "S"},
        {"AttributeName": "SK", "AttributeType": "S"},
    ],
)


def claim() -> bool:
    try:
        ddb.put_item(
            TableName="t",
            Item={"PK": {"S": "TGU#1"}, "SK": {"S": "CLAIM"}},
            ConditionExpression="attribute_not_exists(PK)",
        )
        return True
    except ddb.exceptions.ConditionalCheckFailedException:
        return False


def bump() -> int:
    r = ddb.update_item(
        TableName="t",
        Key={"PK": {"S": "INC#1"}, "SK": {"S": "META"}},
        UpdateExpression="ADD evt_seq :one",
        ExpressionAttributeValues={":one": {"N": "1"}},
        ReturnValues="UPDATED_NEW",
    )
    return int(r["Attributes"]["evt_seq"]["N"])


with ThreadPoolExecutor(16) as pool:
    claims = list(pool.map(lambda _: claim(), range(16)))
    seqs = list(pool.map(lambda _: bump(), range(200)))
print("claims won:", claims.count(True), "| counters unique:", len(set(seqs)) == 200, max(seqs))

boto3.client("s3").create_bucket(Bucket="spike-bucket")
from strands.storage import S3Storage  # noqa: E402

store = S3Storage("spike-bucket", prefix="sessions/")
asyncio.run(store.write("session/x/snapshot_latest.json", b"{}"))
got = asyncio.run(store.read("session/x/snapshot_latest.json"))
print("s3storage via endpoint env:", got)
ok = claims.count(True) == 1 and len(set(seqs)) == 200 and got == b"{}"
print("PASS" if ok else "FAIL")
server.stop()
raise SystemExit(0 if ok else 1)
