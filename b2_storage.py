"""
b2_storage.py

Encapsulates all interaction with Backblaze B2 via its S3-compatible API
(through boto3). No other module should talk to B2 directly.

Responsibilities:
    - Create and cache an S3 client configured for B2
    - Upload objects (streamed from an in-memory buffer)
    - Download objects (streamed)
    - Delete objects
    - List objects / paginate a bucket
    - Check object existence
    - Compute aggregate storage usage directly from B2
    - Copy objects (used to implement "rename")

The B2 Application Key and Key ID never leave this module / the server
process. They are never sent to the browser.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import BinaryIO, Iterator, List, Optional

import boto3
import streamlit as st
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from config import AppConfig

logger = logging.getLogger("personal_cloud_storage.b2_storage")


class B2Error(Exception):
    """Raised when a Backblaze B2 operation fails, with a safe user-facing message."""


@dataclass
class B2Object:
    """Lightweight representation of an object listed from B2."""

    key: str
    size: int
    last_modified: Optional[str]


def _build_client(endpoint: str, key_id: str, application_key: str):
    """Create a new boto3 S3 client configured for the B2 S3-compatible API."""
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=application_key,
        config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
    )


@st.cache_resource(show_spinner=False)
def _cached_client(endpoint: str, key_id: str, application_key: str):
    """
    Cache the boto3 client per unique credential/endpoint combination.

    Streamlit's cache_resource keeps this out of the serialized session
    state and shares one client per process, avoiding repeated client
    construction on every rerun.
    """
    return _build_client(endpoint, key_id, application_key)


def get_client(config: AppConfig):
    """Return a cached boto3 S3 client configured for this app's B2 credentials."""
    return _cached_client(config.b2_endpoint, config.b2_key_id, config.b2_application_key)


def check_connection(config: AppConfig) -> None:
    """
    Verify that the configured bucket is reachable with the given credentials.

    Raises B2Error with a friendly message on failure.
    """
    client = get_client(config)
    try:
        client.head_bucket(Bucket=config.b2_bucket_name)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchBucket"):
            raise B2Error(f"Bucket '{config.b2_bucket_name}' was not found.") from exc
        if code in ("403", "InvalidAccessKeyId", "SignatureDoesNotMatch"):
            raise B2Error("Backblaze B2 credentials were rejected. Check your Key ID and Application Key.") from exc
        raise B2Error(f"Could not reach Backblaze B2 (error code: {code}).") from exc
    except (BotoCoreError, Exception) as exc:  # noqa: BLE001 - network errors vary widely
        raise B2Error("Could not connect to Backblaze B2. Check your network and B2_ENDPOINT.") from exc


def upload_fileobj(config: AppConfig, file_obj: BinaryIO, object_key: str, content_type: str) -> None:
    """
    Upload a file-like object to B2 under the given object key, streaming
    rather than loading the whole file into a separate buffer.
    """
    client = get_client(config)
    try:
        file_obj.seek(0)
        client.upload_fileobj(
            file_obj,
            config.b2_bucket_name,
            object_key,
            ExtraArgs={"ContentType": content_type or "application/octet-stream"},
        )
    except (ClientError, BotoCoreError) as exc:
        logger.exception("B2 upload failed for key=%s", object_key)
        raise B2Error(f"Upload to Backblaze B2 failed for '{object_key}'.") from exc


def download_object_stream(config: AppConfig, object_key: str):
    """
    Return the raw streaming body for an object from B2.

    Caller is responsible for reading and closing the returned stream
    (e.g. via `.read()` on the returned botocore StreamingBody).
    """
    client = get_client(config)
    try:
        response = client.get_object(Bucket=config.b2_bucket_name, Key=object_key)
        return response["Body"]
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            raise B2Error(f"File '{object_key}' no longer exists in storage.") from exc
        logger.exception("B2 download failed for key=%s", object_key)
        raise B2Error(f"Download from Backblaze B2 failed for '{object_key}'.") from exc
    except BotoCoreError as exc:
        raise B2Error(f"Download from Backblaze B2 failed for '{object_key}'.") from exc


def delete_object(config: AppConfig, object_key: str) -> None:
    """Delete a single object from B2. Raises B2Error on failure."""
    client = get_client(config)
    try:
        client.delete_object(Bucket=config.b2_bucket_name, Key=object_key)
    except (ClientError, BotoCoreError) as exc:
        logger.exception("B2 delete failed for key=%s", object_key)
        raise B2Error(f"Could not delete '{object_key}' from Backblaze B2.") from exc


def object_exists(config: AppConfig, object_key: str) -> bool:
    """Return True if the given object key exists in the bucket."""
    client = get_client(config)
    try:
        client.head_object(Bucket=config.b2_bucket_name, Key=object_key)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey"):
            return False
        logger.exception("B2 head_object failed for key=%s", object_key)
        raise B2Error(f"Could not check existence of '{object_key}' in Backblaze B2.") from exc


def copy_object(config: AppConfig, source_key: str, destination_key: str) -> None:
    """Copy an object within the same bucket (used to implement rename/move)."""
    client = get_client(config)
    try:
        client.copy_object(
            Bucket=config.b2_bucket_name,
            CopySource={"Bucket": config.b2_bucket_name, "Key": source_key},
            Key=destination_key,
        )
    except (ClientError, BotoCoreError) as exc:
        logger.exception("B2 copy failed: %s -> %s", source_key, destination_key)
        raise B2Error(f"Could not rename/move '{source_key}' in Backblaze B2.") from exc


def list_all_objects(config: AppConfig, prefix: str = "") -> Iterator[B2Object]:
    """
    Yield every object in the bucket under the given prefix, transparently
    paginating through B2's list-objects API.
    """
    client = get_client(config)
    paginator = client.get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=config.b2_bucket_name, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield B2Object(
                    key=obj["Key"],
                    size=obj["Size"],
                    last_modified=obj["LastModified"].isoformat() if obj.get("LastModified") else None,
                )
    except (ClientError, BotoCoreError) as exc:
        logger.exception("B2 list_objects failed for prefix=%s", prefix)
        raise B2Error("Could not list files from Backblaze B2.") from exc


def calculate_bucket_usage(config: AppConfig) -> int:
    """
    Compute the total number of bytes used in the bucket by summing all
    object sizes directly from B2. This is the authoritative source of
    truth for storage usage (independent of the SQLite cache).
    """
    total = 0
    for obj in list_all_objects(config):
        total += obj.size
    return total


def list_object_keys(config: AppConfig, prefix: str = "") -> List[str]:
    """Convenience helper: return just the object keys under a prefix."""
    return [obj.key for obj in list_all_objects(config, prefix=prefix)]
