"""Reads notice photos from the S3-compatible object store (ADR 0011, ADR 0013).

The browser never talks to the store: the web service reads a photo and hands it on
at ``/photos/{id}``. Only reading is needed, so this class has no other method.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

# The error codes of a key or bucket that does not exist.
_MISSING = {"NoSuchKey", "NoSuchBucket", "404"}


class S3PhotoReader:
    """Reads photos from one bucket."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @classmethod
    def connect(cls, endpoint_url: str, access_key: str, secret_key: str, bucket: str) -> Self:
        """Create a reader that talks to the S3 API at ``endpoint_url``."""
        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            # S3 clients require a region; the store itself ignores it.
            region_name="us-east-1",
            # Short timeouts: a person is waiting for the page, and a stuck store must
            # not hold a page open (the lesson of the missing database timeout).
            config=Config(
                connect_timeout=5,
                read_timeout=15,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
        return cls(client, bucket)

    def get(self, key: str) -> bytes | None:
        """Return the photo's bytes, or None if the store has no such file."""
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in _MISSING:
                return None
            raise
        # The face worker stores photos of at most 5 MB, so reading it whole is fine.
        return response["Body"].read()
