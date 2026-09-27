"""The squeeze odds for the next monthly OPEX, as published by tradeval-squeeze.

That service does the analysis on its own schedule and writes one JSON file
(its squeeze/export.py describes the contract). This module only reads it:
from a local path in TRADEVAL_SQUEEZE_FILE, or from S3 when
TRADEVAL_SQUEEZE_S3 names "bucket/key". Kept for ten minutes, since the file
changes at most once a day.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

FRESH_FOR = 10 * 60

_held: dict = {"at": 0.0, "value": None}


class NotPublished(Exception):
    """No squeeze file is configured, or it cannot be read."""


def _read() -> dict:
    path = os.environ.get("TRADEVAL_SQUEEZE_FILE")
    location = os.environ.get("TRADEVAL_SQUEEZE_S3")
    try:
        if path:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        if location:
            import boto3

            bucket, _, key = location.partition("/")
            body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
            return json.loads(body)
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
