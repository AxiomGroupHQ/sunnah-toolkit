"""Optional sign-in (api/accounts.py, core/users.py): every flow end to end."""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from sunnah_toolkit.api import accounts, oauth
from sunnah_toolkit.core import mailer, session_cookie, users

SITE = "https://searchthehadith.fyi"
ADHAN = "https://adhan.searchthehadith.fyi"
SECRET = "test-secret-not-for-production"


class Outbox:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, text):
        self.sent.append({"to": to, "subject": subject, "text": text})

    def link(self, path):
        """Token from the newest email's link to /auth/<path>."""
        m = re.search(rf"{SITE}/auth/{path}\?token=(\S+)", self.sent[-1]["text"])
        assert m, self.sent[-1]
        return m.group(1)


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET",
              "RATE_LIMIT_AUTH_PER_MIN", "RATE_LIMIT_AUTH_EMAIL_PER_HOUR", "RATE_LIMIT_PER_MIN",
              "AUTH_RETURN_HOSTS", "AUTH_COOKIE_NAME", "RESEND_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AUTH_EMAIL_DEV", "1")
    monkeypatch.setenv("USERS_DB_PATH", str(tmp_path / "accounts" / "users.sqlite"))
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    monkeypatch.setenv("AUTH_BASE_URL", SITE)
    monkeypatch.setenv("AUTH_COOKIE_DOMAIN", ".searchthehadith.fyi")
    box = Outbox()
    monkeypatch.setattr(mailer, "from_env", lambda: box)
    return box


def new_client():
    from sunnah_toolkit.api.app import create_app
    return TestClient(create_app(), base_url=SITE, follow_redirects=False)


@pytest.fixture
def client(env):
    return new_client()


def post(c, path, data, origin=SITE):
    headers = {"origin": origin} if origin else {}
    return c.post(path, data=data, headers=headers)


def me(c):
    return c.get("/auth/me").json()["user"]


def signup(c, box, email="amina@example.com", password="correct horse", name="Amina", return_to=ADHAN + "/"):
    r = post(c, "/auth/signup", {"email": email, "password": password, "name": name, "return_to": return_to})
    assert r.status_code == 200 and "Check your email" in r.text
    token = box.link("verify")
    assert c.get("/auth/verify", params={"token": token}).status_code == 200
    return post(c, "/auth/verify", {"token": token}), token


# ── off by default ─────────────────────────────────────────────────────────────

def test_off_without_secret(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    monkeypatch.setenv("USERS_DB_PATH", str(tmp_path / "u.sqlite"))
    c = new_client()
    assert c.get("/auth/me").json() == {"enabled": False, "user": None}
    assert not (tmp_path / "u.sqlite").exists()


# ── sign-up and verification ───────────────────────────────────────────────────

def test_signup_verify_then_password_login(client, env):
    r, token = signup(client, env)
    assert r.status_code == 303 and r.headers["location"] == ADHAN + "/"
    cookie = r.headers["set-cookie"].lower()
    assert "domain=.searchthehadith.fyi" in cookie and "httponly" in cookie and "secure" in cookie
    assert "samesite=lax" in cookie
    assert me(client) == {"id": 1, "email": "amina@example.com", "name": "Amina"}
    assert client.get("/auth/me").headers["cache-control"] == "no-store"

    # The link is single-use.
    assert post(client, "/auth/verify", {"token": token}).status_code == 400

    other = new_client()
    r = post(other, "/auth/login", {"email": "Amina@Example.com ", "password": "correct horse", "return_to": "/"})
    assert r.status_code == 303 and r.headers["location"] == SITE + "/"
    assert me(other)["email"] == "amina@example.com"


def test_no_account_until_the_link_is_followed(client, env):
    post(client, "/auth/signup", {"email": "a@example.com", "password": "correct horse"})
    assert users.user_by_email("a@example.com") is None
    r = post(client, "/auth/login", {"email": "a@example.com", "password": "correct horse"})
    assert r.status_code == 400 and "Wrong email or password" in r.text


def test_signup_rejects_short_password(client, env):
    r = post(client, "/auth/signup", {"email": "a@example.com", "password": "short"})
    assert r.status_code == 400 and "at least 8" in r.text and env.sent == []


def test_signup_for_existing_email_does_not_leak_or_change_password(client, env):
    signup(client, env)
    r = post(client, "/auth/signup", {"email": "amina@example.com", "password": "attacker pass"})
    assert r.status_code == 200 and "Check your email" in r.text
    assert env.sent[-1]["subject"] == "You already have an account" and "token=" not in env.sent[-1]["text"]
    c = new_client()
    assert post(c, "/auth/login", {"email": "amina@example.com", "password": "attacker pass"}).status_code == 400
    assert post(c, "/auth/login", {"email": "amina@example.com", "password": "correct horse"}).status_code == 303


# ── password login ─────────────────────────────────────────────────────────────

def test_wrong_password_and_unknown_email_look_the_same(client, env):
    signup(client, env)
    c = new_client()
    wrong = post(c, "/auth/login", {"email": "amina@example.com", "password": "nope nope"})
    unknown = post(c, "/auth/login", {"email": "nobody@example.com", "password": "nope nope"})
    assert wrong.status_code == unknown.status_code == 400
    assert "Wrong email or password" in wrong.text and "Wrong email or password" in unknown.text
    assert "set-cookie" not in wrong.headers and me(c) is None


def test_login_is_rate_limited_per_ip(env, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_AUTH_PER_MIN", "3")
    c = new_client()
    codes = [post(c, "/auth/login", {"email": f"x{i}@example.com", "password": "whatever1"}).status_code
             for i in range(4)]
    assert codes == [400, 400, 400, 429]


def test_login_is_rate_limited_per_email(env, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_AUTH_EMAIL_PER_HOUR", "2")
    c = new_client()
    ips = ["203.0.113.1", "203.0.113.2", "203.0.113.3"]
    codes = [c.post("/auth/login", data={"email": "victim@example.com", "password": "guess1234"},
                    headers={"origin": SITE, "cf-connecting-ip": ip}).status_code for ip in ips]
    assert codes == [400, 400, 429]
    # The owner can still get a sign-in link.
    r = c.post("/auth/magic-link", data={"email": "victim@example.com"}, headers={"origin": SITE})
    assert r.status_code == 200


# ── magic link ─────────────────────────────────────────────────────────────────

def test_magic_link_creates_account_and_is_single_use(client, env):
    r = post(client, "/auth/magic-link", {"email": "bilal@example.com", "return_to": ADHAN + "/?x=1"})
    assert r.status_code == 200 and "Check your email" in r.text
    token = env.link("magic")
    # Opening the link (or a mail scanner prefetching it) does not use it up.
    assert client.get("/auth/magic", params={"token": token}).status_code == 200
    assert client.get("/auth/magic", params={"token": token}).status_code == 200
    r = post(client, "/auth/magic", {"token": token})
    assert r.status_code == 303 and r.headers["location"] == ADHAN + "/?x=1"
    assert me(client)["email"] == "bilal@example.com"
    assert post(new_client(), "/auth/magic", {"token": token}).status_code == 400
    assert new_client().get("/auth/magic", params={"token": token}).status_code == 400


def test_magic_link_expires(client, env, monkeypatch):
    post(client, "/auth/magic-link", {"email": "bilal@example.com"})
    token = env.link("magic")
    later = users.now() + users.TOKEN_TTL["magic"] + 1
    monkeypatch.setattr(users, "now", lambda: later)
    r = post(client, "/auth/magic", {"token": token})
    assert r.status_code == 400 and "expired" in r.text and me(client) is None


def test_new_magic_link_replaces_the_old_one(client, env):
    post(client, "/auth/magic-link", {"email": "bilal@example.com"})
    first = env.link("magic")
    post(client, "/auth/magic-link", {"email": "bilal@example.com"})
    assert post(client, "/auth/magic", {"token": first}).status_code == 400
    assert post(client, "/auth/magic", {"token": env.link("magic")}).status_code == 303


def test_tokens_are_stored_hashed(client, env):
    post(client, "/auth/magic-link", {"email": "bilal@example.com"})
    token = env.link("magic")
    conn = sqlite3.connect(users.db_path())
    [stored] = [r[0] for r in conn.execute("SELECT token_hash FROM email_tokens")]
    assert stored == users.sha256(token) and token not in stored


# ── password reset ─────────────────────────────────────────────────────────────

def test_reset_sets_password_and_ends_other_sessions(client, env):
    signup(client, env)
    assert me(client) is not None
    other = new_client()
    r = post(other, "/auth/forgot", {"email": "amina@example.com"})
    assert r.status_code == 200 and env.sent[-1]["subject"] == "Reset your password"
    token = env.link("reset")
    assert other.get("/auth/reset", params={"token": token}).status_code == 200
    r = post(other, "/auth/reset", {"token": token, "password": "new password 2"})
    assert r.status_code == 303 and me(other)["email"] == "amina@example.com"
    assert me(client) is None  # the first device was signed out
    c = new_client()
    assert post(c, "/auth/login", {"email": "amina@example.com", "password": "correct horse"}).status_code == 400
    assert post(c, "/auth/login", {"email": "amina@example.com", "password": "new password 2"}).status_code == 303
    assert post(other, "/auth/reset", {"token": token, "password": "again again"}).status_code == 400


def test_forgot_does_not_reveal_unknown_email(client, env):
    signup(client, env)
    sent = len(env.sent)
    known = post(new_client(), "/auth/forgot", {"email": "amina@example.com"})
    unknown = post(new_client(), "/auth/forgot", {"email": "nobody@example.com"})
    strip = lambda t: t.replace("amina@example.com", "X").replace("nobody@example.com", "X")  # noqa: E731
    assert known.status_code == unknown.status_code == 200 and strip(known.text) == strip(unknown.text)
    assert len(env.sent) == sent + 1


# ── Google / GitHub ────────────────────────────────────────────────────────────

def fake_provider(monkeypatch, routes):
    """Point oauth's HTTP client at canned responses; returns the requests it saw."""
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        body = routes[str(request.url).split("?")[0]]
        return httpx.Response(200, json=body)

    monkeypatch.setattr(oauth, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return seen


def oauth_round_trip(c, name, return_to=None):
    r = c.get(f"/auth/oauth/{name}", params={"return_to": return_to} if return_to else None)
    assert r.status_code == 303
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["redirect_uri"] == [f"{SITE}/auth/oauth/{name}/callback"] and q["code_challenge_method"] == ["S256"]
    return c.get(f"/auth/oauth/{name}/callback", params={"code": "the-code", "state": q["state"][0]}), q


GOOGLE = {"https://oauth2.googleapis.com/token": {"access_token": "g-token"},
          "https://openidconnect.googleapis.com/v1/userinfo":
              {"sub": "g-123", "email": "Amina@example.com", "email_verified": True, "name": "Amina G"}}


@pytest.fixture
def google(env, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gsecret")
    return fake_provider(monkeypatch, GOOGLE)


def test_google_links_to_account_with_same_verified_email(google, env):
    c = new_client()
    signup(c, env)
    c2 = new_client()
    assert "Continue with Google" in c2.get("/auth/login").text
    r, q = oauth_round_trip(c2, "google", ADHAN + "/")
    assert r.status_code == 303 and r.headers["location"] == ADHAN + "/"
    assert me(c2)["id"] == me(c)["id"]
    # PKCE: the verifier sent with the code matches the challenge sent at the start.
    token_req = parse_qs(google[0].content.decode())
    verifier = token_req["code_verifier"][0]
    import base64, hashlib
    assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() \
        == q["code_challenge"][0]
    # Second time: found by the stored identity, still one account.
    r, _ = oauth_round_trip(new_client(), "google")
    conn = sqlite3.connect(users.db_path())
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert conn.execute("SELECT provider, provider_user_id, user_id FROM identities").fetchall() == [("google", "g-123", 1)]


def test_google_unverified_email_is_refused(env, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gsecret")
    unverified = dict(GOOGLE)
    unverified["https://openidconnect.googleapis.com/v1/userinfo"] = {
        "sub": "g-9", "email": "amina@example.com", "email_verified": False}
    fake_provider(monkeypatch, unverified)
    c = new_client()
    r, _ = oauth_round_trip(c, "google")
    assert r.status_code == 400 and "no verified email" in r.text and me(c) is None
    assert users.user_by_email("amina@example.com") is None


def test_oauth_state_must_match(google, env):
    c = new_client()
    c.get("/auth/oauth/google")
    r = c.get("/auth/oauth/google/callback", params={"code": "x", "state": "forged"})
    assert r.status_code == 400 and "expired" in r.text and google == []
    # No state cookie at all (callback opened in another browser).
    r = new_client().get("/auth/oauth/google/callback", params={"code": "x", "state": "s"})
    assert r.status_code == 400


def test_github_uses_verified_primary_email(env, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "hid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "hsecret")
    fake_provider(monkeypatch, {
        "https://github.com/login/oauth/access_token": {"access_token": "h-token"},
        "https://api.github.com/user": {"id": 42, "login": "bilal", "name": None},
        "https://api.github.com/user/emails": [
            {"email": "old@example.com", "primary": False, "verified": True},
            {"email": "bilal@example.com", "primary": True, "verified": True}],
    })
    c = new_client()
    r, _ = oauth_round_trip(c, "github")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/"
    assert me(c) == {"id": 1, "email": "bilal@example.com", "name": "bilal"}


def test_unconfigured_provider_is_not_offered(client):
    assert "Continue with" not in client.get("/auth/login").text
    assert client.get("/auth/oauth/google").status_code == 404


# ── Google-only (no mail configured) ───────────────────────────────────────────

EMAIL_ROUTES = [("post", "/auth/login"), ("get", "/auth/signup"), ("post", "/auth/signup"),
                ("get", "/auth/verify"), ("post", "/auth/verify"), ("post", "/auth/magic-link"),
                ("get", "/auth/magic"), ("post", "/auth/magic"), ("get", "/auth/forgot"),
                ("post", "/auth/forgot"), ("get", "/auth/reset"), ("post", "/auth/reset")]


@pytest.fixture
def google_only(google, monkeypatch):
    monkeypatch.delenv("AUTH_EMAIL_DEV")
    return google


def test_google_only_login_page_has_no_email_forms(google_only, env):
    c = new_client()
    assert c.get("/auth/me").json() == {"enabled": True, "methods": ["google"], "user": None}
    page = c.get("/auth/login").text
    assert "Continue with Google" in page and "GitHub" not in page
    for absent in ('type="password"', 'type="email"', "/auth/signup", "/auth/forgot", "/auth/magic-link", ">or<"):
        assert absent not in page, absent


def test_google_only_email_routes_are_gone(google_only, env):
    c = new_client()
    for method, path in EMAIL_ROUTES:
        r = getattr(c, method)(path, **({"data": {"email": "a@example.com", "password": "correct horse",
                                                  "token": "t"}, "headers": {"origin": SITE}}
                                        if method == "post" else {}))
        assert r.status_code == 404, (method, path)
    assert env.sent == []


def test_google_only_sign_in_still_works(google_only, env):
    c = new_client()
    r, _ = oauth_round_trip(c, "google", ADHAN + "/")
    assert r.status_code == 303 and r.headers["location"] == ADHAN + "/"
    assert me(c)["email"] == "amina@example.com"
    account = c.get("/auth/account").text
    assert "amina@example.com" in account and "/auth/forgot" not in account
    assert post(c, "/auth/logout", {}).status_code == 303 and me(c) is None


def test_resend_key_turns_email_on(google, env, monkeypatch):
    monkeypatch.delenv("AUTH_EMAIL_DEV")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    assert new_client().get("/auth/me").json()["methods"] == ["google", "email"]


def test_off_with_no_way_to_sign_in(env, monkeypatch):
    monkeypatch.delenv("AUTH_EMAIL_DEV")
    assert new_client().get("/auth/me").json() == {"enabled": False, "user": None}


# ── sign out ───────────────────────────────────────────────────────────────────

def test_logout_ends_the_session(client, env):
    signup(client, env)
    stolen = client.cookies.get("sth_session")
    r = post(client, "/auth/logout", {"return_to": ADHAN + "/"})
    assert r.status_code == 303 and r.headers["location"] == ADHAN + "/"
    assert me(client) is None
    # The old cookie value is dead server-side, not just deleted in the browser.
    replay = new_client()
    replay.cookies.set("sth_session", stolen, domain=".searchthehadith.fyi")
    assert me(replay) is None


def test_logout_everywhere(client, env):
    signup(client, env)
    other = new_client()
    post(other, "/auth/login", {"email": "amina@example.com", "password": "correct horse"})
    post(other, "/auth/logout", {"everywhere": "1"}, origin=ADHAN)
    assert me(other) is None and me(client) is None


def test_posts_from_other_sites_are_refused(client, env):
    signup(client, env)
    assert post(client, "/auth/logout", {}, origin="https://evil.example").status_code == 403
    assert post(client, "/auth/logout", {}, origin=None).status_code == 403
    assert me(client) is not None
    r = client.post("/auth/logout", data={}, headers={"referer": ADHAN + "/page"})
    assert r.status_code == 303


def test_tampered_cookie_is_ignored(client, env):
    signup(client, env)
    raw = client.cookies.get("sth_session")
    payload = session_cookie.verify(raw, SECRET)
    payload["uid"] = 2
    forged = session_cookie.sign(payload, "wrong secret")
    c = new_client()
    c.cookies.set("sth_session", forged, domain=".searchthehadith.fyi")
    assert me(c) is None


# ── return URLs ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url, expected", [
    (None, SITE + "/"),
    ("/?q=anger", SITE + "/?q=anger"),
    (SITE + "/?q=x", SITE + "/?q=x"),
    (ADHAN + "/", ADHAN + "/"),
    ("https://evil.example/", SITE + "/"),
    ("https://searchthehadith.fyi.evil.example/", SITE + "/"),
    ("https://evil.example/?https://searchthehadith.fyi", SITE + "/"),
    ("//evil.example/", SITE + "/"),
    ("/\\evil.example", SITE + "/"),
    ("https://adhan.searchthehadith.fyi@evil.example/", SITE + "/"),
    ("http://adhan.searchthehadith.fyi/", SITE + "/"),
    ("javascript:alert(1)", SITE + "/"),
    ("https://other.searchthehadith.fyi/", SITE + "/"),
])
def test_return_url_allowlist(env, url, expected):
    assert accounts.safe_return(url, accounts.config_from_env()) == expected


def test_login_never_redirects_off_site(client, env):
    signup(client, env)
    c = new_client()
    r = post(c, "/auth/login", {"email": "amina@example.com", "password": "correct horse",
                                "return_to": "https://evil.example/steal"})
    assert r.status_code == 303 and r.headers["location"] == SITE + "/"


# ── cookie format shared with my-adhan ─────────────────────────────────────────

# my-adhan's tests/test_shared_login.py checks it can read this exact value.
SHARED_VECTOR = ("v1.eyJlbWFpbCI6ImFtaW5hQGV4YW1wbGUuY29tIiwiZXhwIjo0MTAyNDQ0ODAwLCJuYW1lIjoiQW1pbmEiLCJzaWQiOiJz"
                 "MSIsInVpZCI6N30.kNCaLIkia46iFR05Q15a4vrDffaps61vbStXoGwCl8I")


def test_cookie_format_is_stable():
    payload = {"sid": "s1", "uid": 7, "email": "amina@example.com", "name": "Amina", "exp": 4102444800}
    assert session_cookie.sign(payload, "shared-test-secret") == SHARED_VECTOR
    assert session_cookie.verify(SHARED_VECTOR, "shared-test-secret") == payload
    assert session_cookie.verify(SHARED_VECTOR, "shared-test-secret", purpose="oauth") is None
