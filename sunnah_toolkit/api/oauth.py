"""Google and GitHub sign-in: the authorization-code flow with state and PKCE.

Two HTTP calls per sign-in (code for token, token for profile) over httpx,
which the app already depends on. A provider is offered only when its
<NAME>_CLIENT_ID and <NAME>_CLIENT_SECRET are set.

Only a verified email address is ever returned: Google's `email_verified`
claim, GitHub's verified primary address from /user/emails. Accounts are
linked by that address (core/users.py), so an unverified one must never get
through.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

PROVIDERS = {
    "google": {
        "label": "Google",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "userinfo": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email profile",
    },
    "github": {
        "label": "GitHub",
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "userinfo": "https://api.github.com/user",
        "emails": "https://api.github.com/user/emails",
        "scope": "read:user user:email",
    },
}


class OAuthError(Exception):
    """The provider refused, or answered with something unusable. The message is shown to the user."""


@dataclass
class Profile:
    provider: str
    id: str
    email: str | None  # verified, or None
    name: str | None


def _creds(name: str) -> tuple[str, str] | None:
    cid = os.environ.get(f"{name.upper()}_CLIENT_ID", "").strip()
    secret = os.environ.get(f"{name.upper()}_CLIENT_SECRET", "").strip()
    return (cid, secret) if cid and secret else None


def configured() -> list[str]:
    return [name for name in PROVIDERS if _creds(name)]


def _http() -> httpx.Client:
    # Tests swap this for a client on httpx.MockTransport.
    return httpx.Client(timeout=15)


def new_pkce() -> tuple[str, str]:
    """(verifier, S256 challenge)."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(name: str, redirect_uri: str, state: str, challenge: str) -> str:
    p, (cid, _) = PROVIDERS[name], _creds(name)
    params = {
        "client_id": cid, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": p["scope"], "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    if name == "google":
        params["prompt"] = "select_account"
    return f"{p['authorize']}?{urlencode(params)}"


def fetch_profile(name: str, code: str, redirect_uri: str, verifier: str) -> Profile:
    p, creds = PROVIDERS[name], _creds(name)
    if creds is None:
        raise OAuthError(f"{p['label']} sign-in is not set up.")
    cid, secret = creds
    try:
        with _http() as http:
            r = http.post(p["token"], headers={"Accept": "application/json"}, data={
                "client_id": cid, "client_secret": secret, "code": code,
                "redirect_uri": redirect_uri, "grant_type": "authorization_code",
                "code_verifier": verifier,
            })
            token = r.json().get("access_token") if r.status_code == 200 else None
            if not token:
                raise OAuthError(f"{p['label']} did not accept the sign-in. Please try again.")
            auth = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            info = http.get(p["userinfo"], headers=auth)
            info.raise_for_status()
            info = info.json()
            if name == "google":
                email = info.get("email") if info.get("email_verified") is True else None
                return Profile(name, str(info["sub"]), email, info.get("name"))
            emails = http.get(p["emails"], headers=auth)
            emails.raise_for_status()
            primary = [e for e in emails.json() if e.get("primary") and e.get("verified")]
            return Profile(name, str(info["id"]), primary[0]["email"] if primary else None,
                           info.get("name") or info.get("login"))
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        raise OAuthError(f"Could not reach {p['label']}. Please try again.") from e
