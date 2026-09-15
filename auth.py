"""
auth.py

Session-based authentication for the Personal Cloud Storage application.

Responsibilities:
    - Render the login form
    - Validate the entered password against st.secrets["APP_PASSWORD"]
    - Track authentication state in st.session_state
    - Handle logout / session clearing

The current version compares against a plaintext password read from
Streamlit secrets (per the project's initial-version requirements), but
the comparison itself is written so it can be swapped for a hashed-password
check (see `_password_matches`) without changing any calling code.
"""

from __future__ import annotations

import hmac
import time

import streamlit as st

from config import AppConfig

SESSION_KEY_AUTHENTICATED = "authenticated"
SESSION_KEY_LOGIN_ATTEMPTS = "login_attempts"
SESSION_KEY_LOGIN_LOCK_UNTIL = "login_lock_until"

_MAX_ATTEMPTS_BEFORE_COOLDOWN = 5
_COOLDOWN_SECONDS = 30


def _password_matches(entered_password: str, configured_password: str) -> bool:
    """
    Compare the entered password to the configured one using a
    constant-time comparison to reduce timing side-channels.

    NOTE: The configured value here is a plaintext secret from
    st.secrets. If/when this is upgraded to store a hashed password
    (e.g. in SQLite via bcrypt/argon2), only this function needs to
    change -- callers always just call is_authenticated()/login().
    """
    if not configured_password:
        return False
    return hmac.compare_digest(entered_password.encode("utf-8"), configured_password.encode("utf-8"))


def is_authenticated() -> bool:
    return bool(st.session_state.get(SESSION_KEY_AUTHENTICATED, False))


def _is_locked_out() -> bool:
    lock_until = st.session_state.get(SESSION_KEY_LOGIN_LOCK_UNTIL, 0)
    return time.time() < lock_until


def _register_failed_attempt() -> None:
    attempts = st.session_state.get(SESSION_KEY_LOGIN_ATTEMPTS, 0) + 1
    st.session_state[SESSION_KEY_LOGIN_ATTEMPTS] = attempts
    if attempts >= _MAX_ATTEMPTS_BEFORE_COOLDOWN:
        st.session_state[SESSION_KEY_LOGIN_LOCK_UNTIL] = time.time() + _COOLDOWN_SECONDS
        st.session_state[SESSION_KEY_LOGIN_ATTEMPTS] = 0


def logout() -> None:
    """Clear all session state, fully resetting authentication."""
    st.session_state.clear()


def render_login(config: AppConfig) -> None:
    """
    Render the login screen. On successful login, sets
    st.session_state["authenticated"] = True and triggers a rerun.
    """
    st.markdown(
        """
        <div style="max-width: 420px; margin: 10vh auto 0 auto; text-align: center;">
            <div style="font-size: 3rem;">☁️</div>
            <h1 style="margin-bottom: 0;">My Cloud Storage</h1>
            <p style="color: #888; margin-top: 4px;">Sign in to access your files</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _, center, _ = st.columns([1, 2, 1])
    with center:
        if _is_locked_out():
            st.error("Too many failed attempts. Please wait a moment before trying again.")
            return

        with st.form("login_form", clear_on_submit=False):
            password = st.text_input("Password", type="password", placeholder="Enter application password")
            submitted = st.form_submit_button("🔓 Login", use_container_width=True)

        if submitted:
            if _password_matches(password, config.app_password):
                st.session_state[SESSION_KEY_AUTHENTICATED] = True
                st.session_state[SESSION_KEY_LOGIN_ATTEMPTS] = 0
                st.rerun()
            else:
                _register_failed_attempt()
                st.error("Incorrect password. Please try again.")


def require_auth(config: AppConfig) -> bool:
    """
    Ensure the user is authenticated before proceeding.

    Returns True if the caller may proceed to render the protected
    application. If not authenticated, renders the login screen itself
    and returns False -- callers should stop rendering anything else
    when this returns False.
    """
    if is_authenticated():
        return True
    render_login(config)
    return False
