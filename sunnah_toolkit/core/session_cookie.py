"""Signed cookie values shared with the adhan app.

Format: `v1.<base64url JSON payload>.<base64url HMAC-SHA256>`. The payload is
readable by anyone holding the cookie (it is the user's own id and email), but
cannot be changed without AUTH_SECRET. Every payload carries `exp` (unix
seconds). Each use gets its own key derived from the secret, so an OAuth state
cookie can never pass as a session cookie.

my-adhan keeps a copy of `verify` (api/session_cookie.py) to read the session
cookie without a user database. Keep the two in step.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _key(secret: str, purpose: str) -> bytes:
    return hmac.new(secret.encode(), f"sth-cookie:{purpose}".encode(), hashlib.sha256).digest()


def sign(payload: dict[str, Any], secret: str, purpose: str = "session") -> str:
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(_key(secret, purpose), f"v1.{body}".encode(), hashlib.sha256).digest()
    return f"v1.{body}.{_b64(mac)}"


def verify(value: str, secret: str, purpose: str = "session", now: float | None = None) -> dict[str, Any] | None:
    """The payload if `value` is genuine and unexpired, else None."""
    try:
        version, body, mac = value.split(".")
        if version != "v1":
            return None
        want = hmac.new(_key(secret, purpose), f"v1.{body}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(want, _unb64(mac)):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("exp"), (int, float)):
        return None
    if payload["exp"] <= (time.time() if now is None else now):
        return None
    return payload
