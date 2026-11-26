"""Tests of the photo reader against a real SeaweedFS container (S3 API).

The photo bytes are made up; no real photo is used.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import boto3
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import HttpWaitStrategy

from interpol_web.photos import S3PhotoReader

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
def bucket(endpoint_url: str) -> str:
    """A new empty bucket with one made-up photo in it."""
    name = f"test-{uuid.uuid4().hex[:12]}"
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name="us-east-1",
    )
    s3.create_bucket(Bucket=name)
    s3.put_object(Bucket=name, Key="notices/2026-1/pic", Body=FAKE_JPEG)
    return name


def test_a_stored_photo_is_read(endpoint_url: str, bucket: str) -> None:
    reader = S3PhotoReader.connect(endpoint_url, ACCESS_KEY, SECRET_KEY, bucket)

    assert reader.get("notices/2026-1/pic") == FAKE_JPEG


def test_a_missing_photo_gives_none(endpoint_url: str, bucket: str) -> None:
    reader = S3PhotoReader.connect(endpoint_url, ACCESS_KEY, SECRET_KEY, bucket)

    assert reader.get("notices/2026-1/other") is None


def test_a_missing_bucket_gives_none(endpoint_url: str) -> None:
    reader = S3PhotoReader.connect(endpoint_url, ACCESS_KEY, SECRET_KEY, "no-such-bucket")

    assert reader.get("notices/2026-1/pic") is None
