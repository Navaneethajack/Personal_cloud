"""
config.py

Central configuration module for the Personal Cloud Storage application.

Responsible for:
    - Reading Streamlit secrets
    - Validating required configuration values
    - Exposing configuration values to the rest of the application
    - Providing sensible defaults for optional values

No secret values are ever printed or logged by this module.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import List

import streamlit as st
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

logger = logging.getLogger("personal_cloud_storage.config")

# Required configuration keys. The application cannot function without these.
REQUIRED_KEYS: List[str] = [
    "APP_PASSWORD",
    "B2_ENDPOINT",
    "B2_BUCKET_NAME",
    "B2_KEY_ID",
    "B2_APPLICATION_KEY",
]

# Optional configuration keys with sensible defaults.
DEFAULT_MAX_FILE_SIZE_MB = 200
DEFAULT_FREE_STORAGE_LIMIT_GB = 10
DEFAULT_DB_PATH = "storage.db"
DEFAULT_B2_REGION_HINT = ""  # not required; endpoint already encodes region


@dataclass(frozen=True)
class AppConfig:
    """Immutable application configuration object."""

    app_password: str
    b2_endpoint: str
    b2_bucket_name: str
    b2_key_id: str
    b2_application_key: str
    max_file_size_mb: int
    free_storage_limit_gb: int
    db_path: str

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def free_storage_limit_bytes(self) -> int:
        return self.free_storage_limit_gb * 1024 * 1024 * 1024


def get_missing_keys() -> List[str]:
    """Return a list of required configuration keys that are missing or empty."""
    missing = []
    for key in REQUIRED_KEYS:
        value = os.getenv(key)
        if not value:
            try:
                value = st.secrets.get(key, "")
            except Exception:
                # st.secrets itself may not be configured at all yet.
                value = ""
        if value is None or str(value).strip() == "":
            missing.append(key)
    return missing


def is_configured() -> bool:
    """True if all required configuration values are present."""
    return len(get_missing_keys()) == 0


def render_setup_instructions() -> None:
    """Render a friendly, non-crashing setup message when configuration is missing."""
    missing = get_missing_keys()

    st.error("⚠️ Backblaze configuration is incomplete.")
    st.markdown("Please configure the following values in your `.env` file or `.streamlit/secrets.toml`:")
    for key in missing:
        st.markdown(f"- `{key}`")

    st.markdown(
        """
        ---
        **Example `.env`:**

        ```toml
        APP_PASSWORD = "YOUR_PASSWORD"

        B2_ENDPOINT = "https://s3.YOUR_REGION.backblazeb2.com"
        B2_BUCKET_NAME = "YOUR_BUCKET_NAME"
        B2_KEY_ID = "YOUR_KEY_ID"
        B2_APPLICATION_KEY = "YOUR_APPLICATION_KEY"

        MAX_FILE_SIZE_MB=200
        FREE_STORAGE_LIMIT_GB=10
        ```

        See the README for full setup instructions.
        """
    )


def endpoint_looks_valid(endpoint: str) -> bool:
    """
    Cheap sanity check on the B2_ENDPOINT value, run before ever handing it
    to boto3. Catches the most common copy-paste mistakes (missing scheme,
    stray whitespace, no dot in the host) with a clear message instead of
    letting botocore raise a raw ValueError deep in the upload/download path.
    """
    if not endpoint:
        return False
    if " " in endpoint:
        return False
    if not (endpoint.startswith("https://") or endpoint.startswith("http://")):
        return False
    host = endpoint.split("://", 1)[1]
    if not host or "." not in host:
        return False
    return True


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_config() -> AppConfig:
    """
    Load and return the application configuration.

    Callers must first check `is_configured()` before calling this, since
    it assumes required keys are present. Never logs or prints secret values.
    """
    secrets = st.secrets

    def get_val(key, default=""):
        val = os.getenv(key)
        if val:
            return val
        try:
            return secrets.get(key, default)
        except Exception:
            return default

    config = AppConfig(
        app_password=str(get_val("APP_PASSWORD", "")).strip(),
        b2_endpoint=str(get_val("B2_ENDPOINT", "")).strip().rstrip("/"),
        b2_bucket_name=str(get_val("B2_BUCKET_NAME", "")).strip(),
        b2_key_id=str(get_val("B2_KEY_ID", "")).strip(),
        b2_application_key=str(get_val("B2_APPLICATION_KEY", "")).strip(),
        max_file_size_mb=_safe_int(
            get_val("MAX_FILE_SIZE_MB", DEFAULT_MAX_FILE_SIZE_MB),
            DEFAULT_MAX_FILE_SIZE_MB,
        ),
        free_storage_limit_gb=_safe_int(
            get_val("FREE_STORAGE_LIMIT_GB", DEFAULT_FREE_STORAGE_LIMIT_GB),
            DEFAULT_FREE_STORAGE_LIMIT_GB,
        ),
        db_path=str(get_val("DB_PATH", DEFAULT_DB_PATH)),
    )

    logger.info(
        "Configuration loaded (bucket=%s, endpoint=%s, max_file_size_mb=%s, "
        "free_storage_limit_gb=%s)",
        config.b2_bucket_name,
        config.b2_endpoint,
        config.max_file_size_mb,
        config.free_storage_limit_gb,
    )
    return config
