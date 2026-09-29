"""The squeeze odds for the next monthly OPEX, as published by tradeval-squeeze.

That service does the analysis on its own schedule and writes one JSON file
(its squeeze/export.py describes the contract). This module only reads it:
from a local path in TRADEVAL_SQUEEZE_FILE, or from S3 when
TRADEVAL_SQUEEZE_S3 names "bucket/key". Kept for ten minutes, since the file
changes at most once a day.

The S3 read names the bucket's expected owner, so a bucket that was deleted and
re-created under someone else's account is refused rather than trusted. The
owner is this process's own account -- tradeval-squeeze publishes into the
account both services deploy to -- asked of STS once and kept, which needs no
IAM permission.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

FRESH_FOR = 10 * 60

_held: dict = {"at": 0.0, "value": None}
_account: dict = {"id": None}


class NotPublished(Exception):
    """No squeeze file is configured, or it cannot be read."""


def _own_account(boto3) -> str:
    if _account["id"] is None:
        _account["id"] = boto3.client("sts").get_caller_identity()["Account"]
    return _account["id"]


def _read_s3(location: str) -> dict:
    import boto3

    bucket, _, key = location.partition("/")
    s3 = boto3.client("s3")
    body = s3.get_object(Bucket=bucket, Key=key, ExpectedBucketOwner=_own_account(boto3))["Body"].read()
    return json.loads(body)


def _read() -> dict:
    path = os.environ.get("TRADEVAL_SQUEEZE_FILE")
    location = os.environ.get("TRADEVAL_SQUEEZE_S3")
    try:
        if path:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        if location:
            return _read_s3(location)
    except Exception as exc:  # a missing or unreadable file is "not published", not a crash
        raise NotPublished(f"The squeeze odds could not be read: {exc}") from exc
    raise NotPublished("The squeeze odds have not been published here yet.")


def latest(now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    if _held["value"] is None or now - _held["at"] >= FRESH_FOR:
        _held.update(value=_read(), at=now)
    return _held["value"]


def clear() -> None:
    _held.update(value=None, at=0.0)
