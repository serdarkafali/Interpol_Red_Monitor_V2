"""Stores notice photos in the S3-compatible object store (ADR 0011).

Only the plain S3 API is used, through boto3, so the store behind it (SeaweedFS today)
can be replaced by changing the endpoint alone.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Self

import boto3
from botocore.config import Config

from interpol_face_worker.interpol_photos import to_url_id

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

# Interpol sends the non-standard "image/JPG"; the store keeps the standard value.
PHOTO_CONTENT_TYPE = "image/jpeg"
KEY_PREFIX = "notices/"


def object_key(entity_id: str, picture_id: str) -> str:
    """The key of one photo, e.g. ``notices/2026-7/111``."""
    return f"{KEY_PREFIX}{to_url_id(entity_id)}/{picture_id}"


@dataclass(frozen=True)
class StoredObject:
    """One file in the store and when it was uploaded."""

    key: str
    last_modified: datetime


class S3PhotoStore:
    """Writes, reads, lists and deletes photos in one bucket."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @classmethod
    def connect(cls, endpoint_url: str, access_key: str, secret_key: str, bucket: str) -> Self:
        """Create a store that talks to the S3 API at ``endpoint_url``."""
        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            # S3 clients require a region; the store itself ignores it.
            region_name="us-east-1",
            config=Config(
                connect_timeout=10,
                read_timeout=60,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
        return cls(client, bucket)

    def put(self, key: str, content: bytes) -> None:
        """Write one photo; an existing file with the same key is replaced."""
        self._client.put_object(
            Bucket=self._bucket, Key=key, Body=content, ContentType=PHOTO_CONTENT_TYPE
        )

    def list_objects(self) -> Iterator[StoredObject]:
        """Yield every stored photo with its upload time, one page of 1000 at a time."""
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=KEY_PREFIX):
            for item in page.get("Contents", []):
                yield StoredObject(key=item["Key"], last_modified=item["LastModified"])

    def delete(self, key: str) -> None:
        """Delete one photo; deleting a key that does not exist is not an error."""
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def get(self, key: str) -> bytes:
        """Read one photo and return its bytes.

        A missing key raises botocore's ClientError with the code ``NoSuchKey``.
        """
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        # Body is a stream from the store; read() downloads the whole file (at most 5 MB).
        return response["Body"].read()
