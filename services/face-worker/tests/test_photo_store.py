"""Tests of the photo store against a real SeaweedFS container (S3 API).

Each test gets its own empty bucket, so tests never see each other's files. The photo
bytes are made up; no real photo is used.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import boto3
import pytest
from botocore.exceptions import ClientError
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import HttpWaitStrategy

from interpol_face_worker.photo_store import (
    PHOTO_CONTENT_TYPE,
    S3PhotoStore,
    StoredObject,
    object_key,
)

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

# Same image as docker-compose.yml.
SEAWEEDFS_IMAGE = "chrislusf/seaweedfs:4.54"
S3_PORT = 8333
ACCESS_KEY = "test-access-key"
SECRET_KEY = "test-secret-key"
FAKE_JPEG = b"\xff\xd8\xff made-up photo"


@pytest.fixture(scope="session")
def endpoint_url() -> Iterator[str]:
    """Start one SeaweedFS container for the whole test session."""
    container = (
        DockerContainer(SEAWEEDFS_IMAGE)
        # Every bucket gets its own volumes; with the default limit of 8 volumes only the
        # first bucket could be written. 0 = as many as the disk allows, kept small here.
        .with_command(
            "server -s3 -dir=/data -ip.bind=0.0.0.0 -volume.max=0 -master.volumeSizeLimitMB=64"
        )
        .with_env("AWS_ACCESS_KEY_ID", ACCESS_KEY)
        .with_env("AWS_SECRET_ACCESS_KEY", SECRET_KEY)
        .with_exposed_ports(S3_PORT)
        .waiting_for(HttpWaitStrategy(S3_PORT, "/healthz"))
    )
    with container:
        host = container.get_container_host_ip()
        yield f"http://{host}:{container.get_exposed_port(S3_PORT)}"


@pytest.fixture
def s3(endpoint_url: str) -> S3Client:
    """A plain S3 client, used to set up and inspect what the store did."""
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name="us-east-1",
    )


@pytest.fixture
def bucket(s3: S3Client) -> str:
    """A new, empty bucket for one test."""
    name = f"test-{uuid.uuid4().hex}"
    s3.create_bucket(Bucket=name)
    return name


@pytest.fixture
def store(endpoint_url: str, bucket: str) -> S3PhotoStore:
    return S3PhotoStore.connect(endpoint_url, ACCESS_KEY, SECRET_KEY, bucket)


def test_object_key_uses_the_url_form_of_the_entity_id() -> None:
    assert object_key("2026/7", "111") == "notices/2026-7/111"


def test_put_writes_the_bytes_with_the_standard_jpeg_content_type(
    store: S3PhotoStore, s3: S3Client, bucket: str
) -> None:
    store.put("notices/2026-7/111", FAKE_JPEG)

    stored = s3.get_object(Bucket=bucket, Key="notices/2026-7/111")
    assert stored["Body"].read() == FAKE_JPEG
    assert stored["ContentType"] == PHOTO_CONTENT_TYPE


def test_put_replaces_a_file_with_the_same_key(
    store: S3PhotoStore, s3: S3Client, bucket: str
) -> None:
    store.put("notices/2026-7/111", b"\xff\xd8\xff old")
    store.put("notices/2026-7/111", FAKE_JPEG)

    assert s3.get_object(Bucket=bucket, Key="notices/2026-7/111")["Body"].read() == FAKE_JPEG


def test_list_objects_yields_only_photos_with_their_upload_time(
    store: S3PhotoStore, s3: S3Client, bucket: str
) -> None:
    before = datetime.now(UTC) - timedelta(minutes=1)
    store.put("notices/2026-7/111", FAKE_JPEG)
    store.put("notices/2026-8/222", FAKE_JPEG)
    # A file outside the photo prefix is not the face worker's; it must not be listed.
    s3.put_object(Bucket=bucket, Key="other/file", Body=b"x")

    listed = sorted(store.list_objects(), key=lambda stored: stored.key)

    assert [stored.key for stored in listed] == ["notices/2026-7/111", "notices/2026-8/222"]
    assert all(isinstance(stored, StoredObject) for stored in listed)
    # Timezone-aware, so the cleanup can compare it with datetime.now(UTC).
    assert all(stored.last_modified > before for stored in listed)


def test_list_objects_of_an_empty_bucket_yields_nothing(store: S3PhotoStore) -> None:
    assert list(store.list_objects()) == []


def test_delete_removes_the_file_and_a_missing_key_is_not_an_error(
    store: S3PhotoStore,
) -> None:
    store.put("notices/2026-7/111", FAKE_JPEG)

    store.delete("notices/2026-7/111")
    store.delete("notices/2026-7/111")

    assert list(store.list_objects()) == []


def test_wrong_credentials_are_refused(endpoint_url: str, bucket: str) -> None:
    intruder = S3PhotoStore.connect(endpoint_url, "wrong", "key", bucket)

    with pytest.raises(ClientError) as caught:
        intruder.put("notices/2026-7/111", FAKE_JPEG)

    assert caught.value.response["Error"]["Code"] in {"AccessDenied", "InvalidAccessKeyId"}
