"""Optional sign-in, shared with adhan.searchthehadith.fyi. Mounted at /auth.

Off unless AUTH_SECRET and USERS_DB_PATH are set (deploy/macos/run-api.sh
sets both) and at least one way in is: a Google or GitHub client, or email.
Otherwise /auth/me just says {"enabled": false} and the search page shows no
Sign in button. Searching never needs an account.

The email methods (password, sign-up, magic link, reset) need working mail:
RESEND_API_KEY, or AUTH_EMAIL_DEV=1 to print the emails to the log for local
testing. Without either, only the provider buttons are offered and the email
routes answer 404, so nobody waits for a message that went to a log file.

Sessions live server-side (core/users.py) so sign-out, sign-out-everywhere
and a password reset end them at once. The cookie holds the session id plus
the user's id, email and name, signed with AUTH_SECRET (core/session_cookie.py)
and scoped to AUTH_COOKIE_DOMAIN (".searchthehadith.fyi" in production), so
the adhan app can read who is signed in with the same secret and no database.

Every state-changing POST must come from a page on one of our own origins
(Origin header, falling back to Referer); with SameSite=Lax cookies that
covers CSRF without a token. Emailed links land on a page with a button
rather than acting on GET, so mail scanners that prefetch links cannot use
them up. Requests for a magic link or a reset get the same answer whether or
not the address has an account.
"""

from __future__ import annotations

import json
import logging
import math
import os
import secrets
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ..core import mailer as mailer_mod
from ..core import session_cookie, users
from . import accounts_pages as pages
from . import oauth, ratelimit

_log = logging.getLogger(__name__)

PASSWORD_MIN, PASSWORD_MAX = 8, 256
OAUTH_COOKIE = "sth_oauth"


@dataclass
class Config:
    secret: str
    base_url: str                   # where these pages live, e.g. https://searchthehadith.fyi
    cookie_name: str
    cookie_domain: str | None
    session_ttl: int
    return_hosts: set[str]          # hosts (with port if any) a sign-in may send the user back to
    origins: set[str] = field(default_factory=set)
    email: bool = False             # password / magic link / reset offered

    @property
    def secure(self) -> bool:
        return self.base_url.startswith("https://")


def email_enabled() -> bool:
    return bool(os.environ.get("RESEND_API_KEY", "").strip()) or \
        os.environ.get("AUTH_EMAIL_DEV", "").strip().lower() in ("1", "true", "yes")


def config_from_env() -> Config | None:
    secret = os.environ.get("AUTH_SECRET", "").strip()
    if not secret or not users.db_path() or not (email_enabled() or oauth.configured()):
        return None
    base = (os.environ.get("AUTH_BASE_URL", "").strip() or "https://searchthehadith.fyi").rstrip("/")
    hosts_raw = os.environ.get("AUTH_RETURN_HOSTS", "").strip() or "searchthehadith.fyi,adhan.searchthehadith.fyi"
    hosts = {h.strip().lower() for h in hosts_raw.split(",") if h.strip()}
    hosts.add(urlsplit(base).netloc.lower())
    scheme = urlsplit(base).scheme
    days = os.environ.get("AUTH_SESSION_DAYS", "").strip()
    return Config(
        secret=secret, base_url=base,
        cookie_name=os.environ.get("AUTH_COOKIE_NAME", "").strip() or "sth_session",
        cookie_domain=os.environ.get("AUTH_COOKIE_DOMAIN", "").strip() or None,
        session_ttl=(int(days) if days.isdigit() and int(days) > 0 else 30) * 86400,
        return_hosts=hosts,
        origins={f"{scheme}://{h}" for h in hosts},
        email=email_enabled(),
    )


def safe_return(url: str | None, cfg: Config) -> str:
    """`url` if it points at one of our own sites, else the search page."""
    home = cfg.base_url + "/"
    url = (url or "").strip()
    if not url or any(ch in url for ch in "\\\r\n\t"):
        return home
    if url.startswith("/") and not url.startswith("//"):
        return cfg.base_url + url
    try:
        parts = urlsplit(url)
    except ValueError:
        return home
    if parts.scheme not in ("https", "http" if not cfg.secure else "https"):
        return home
    if parts.username is not None or parts.password is not None:
        return home
    if parts.netloc.lower() not in cfg.return_hosts:
        return home
    return url


def same_origin(request: Request, cfg: Config) -> bool:
    origin = request.headers.get("origin")
    if not origin or origin == "null":
        ref = request.headers.get("referer")
        if not ref:
            return False
        p = urlsplit(ref)
        origin = f"{p.scheme}://{p.netloc}"
    return origin.lower() in cfg.origins


def current_session(request: Request, cfg: Config) -> tuple[dict, str, int] | None:
    """(user, session id, session expiry) for the signed-in visitor."""
    raw = request.cookies.get(cfg.cookie_name)
    payload = session_cookie.verify(raw, cfg.secret) if raw else None
    if not payload or not isinstance(payload.get("sid"), str):
        return None
    found = users.session_user(payload["sid"])
    if found is None or found[0]["id"] != payload.get("uid"):
        return None
    return found[0], payload["sid"], found[1]


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


def build_router() -> APIRouter:
    router = APIRouter(prefix="/auth", include_in_schema=False)
    cfg = config_from_env()

    if cfg is None:
        @router.get("/me")
        def me_disabled() -> JSONResponse:
            return JSONResponse({"enabled": False, "user": None})
        return router

    mail = mailer_mod.from_env()

    def need_email() -> None:
        if not cfg.email:
            raise HTTPException(status_code=404, detail="Email sign-in is not available.")

    email_only = [Depends(need_email)]
    by_ip = ratelimit.SlidingWindow(_env_int("RATE_LIMIT_AUTH_PER_MIN", 20), window=60)
    by_email = ratelimit.SlidingWindow(_env_int("RATE_LIMIT_AUTH_EMAIL_PER_HOUR", 10), window=3600)

    # ── helpers ──────────────────────────────────────────────────────────────

    def limited(request: Request, email: str | None = None) -> int:
        """Seconds to wait if this IP (or this address) has tried too often, else 0.

        Each form counts per address separately, so someone hammering an
        address's password cannot also stop its owner getting a link.
        """
        wait = by_ip.hit(ratelimit.client_key(request.headers, request.client.host if request.client else None))
        if not wait and email:
            wait = by_email.hit(f"{request.url.path}:{users.normalize_email(email)}")
        return wait

    def too_many(wait: int) -> HTMLResponse:
        when = "a minute" if wait <= 60 else f"{math.ceil(wait / 60)} minutes"
        r = pages.message("Too many attempts", f"Please wait {when} and try again.", status=429)
        r.headers["Retry-After"] = str(wait)
        return r

    def forbidden() -> HTMLResponse:
        return pages.message("Request blocked", "That form was sent from another site. "
                             "Go back and try again from this page.", status=403)

    def send(to: str, subject: str, text: str) -> None:
        try:
            mail.send(to, subject, text)
        except Exception as e:
            _log.warning("could not send %r to %s: %s", subject, to, e)

    def link(path: str, token: str) -> str:
        return f"{cfg.base_url}/auth/{path}?token={quote(token)}"

    def current(request: Request) -> tuple[dict, str, int] | None:
        return current_session(request, cfg)

    def set_session_cookie(resp: Response, user: dict, sid: str, exp: int) -> None:
        value = session_cookie.sign(
            {"sid": sid, "uid": user["id"], "email": user["email"], "name": user.get("name"), "exp": exp},
            cfg.secret)
        resp.set_cookie(cfg.cookie_name, value, max_age=max(0, exp - users.now()), path="/",
                        domain=cfg.cookie_domain, secure=cfg.secure, httponly=True, samesite="lax")

    def clear_session_cookie(resp: Response) -> None:
        resp.delete_cookie(cfg.cookie_name, path="/", domain=cfg.cookie_domain,
                           secure=cfg.secure, httponly=True, samesite="lax")

    def sign_in(user: dict, return_to: str | None) -> RedirectResponse:
        sid, exp = users.create_session(user["id"], cfg.session_ttl)
        users.touch_login(user["id"])
        resp = RedirectResponse(safe_return(return_to, cfg), status_code=303)
        set_session_cookie(resp, user, sid, exp)
        return resp

    def password_problem(password: str) -> str | None:
        if len(password) < PASSWORD_MIN:
            return f"Use at least {PASSWORD_MIN} characters for your password."
        if len(password) > PASSWORD_MAX:
            return f"Use at most {PASSWORD_MAX} characters for your password."
        return None

    def valid_email(email: str) -> bool:
        local, _, domain = email.partition("@")
        return bool(local) and "." in domain and len(email) <= 254 and not any(c.isspace() for c in email)

    providers = lambda: [(n, oauth.PROVIDERS[n]["label"]) for n in oauth.configured()]  # noqa: E731

    def methods() -> list[str]:
        return oauth.configured() + (["email"] if cfg.email else [])

    # ── who is signed in ─────────────────────────────────────────────────────

    @router.get("/me")
    def me(request: Request) -> JSONResponse:
        found = current(request)
        if found is None:
            resp = JSONResponse({"enabled": True, "methods": methods(), "user": None})
            if request.cookies.get(cfg.cookie_name):
                clear_session_cookie(resp)
        else:
            user, sid, exp = found
            resp = JSONResponse({"enabled": True, "methods": methods(), "user": {
                "id": user["id"], "email": user["email"], "name": user.get("name")}})
            # Sliding expiry: anyone who comes back within the second half of
            # their session gets a fresh full term.
            if exp - users.now() < cfg.session_ttl // 2:
                exp = users.now() + cfg.session_ttl
                users.extend_session(sid, exp)
                set_session_cookie(resp, user, sid, exp)
        return resp

    @router.get("/account")
    def account(request: Request, return_to: str | None = None) -> Response:
        found = current(request)
        if found is None:
            return RedirectResponse(f"/auth/login?return_to={quote(safe_return(return_to, cfg), safe='')}",
                                    status_code=303)
        return pages.account(found[0], safe_return(return_to, cfg), email=cfg.email)

    # ── email + password ─────────────────────────────────────────────────────

    @router.get("/login")
    def login_form(request: Request, return_to: str | None = None) -> Response:
        if current(request) is not None:
            return RedirectResponse(safe_return(return_to, cfg), status_code=303)
        return pages.login(providers(), cfg.email, safe_return(return_to, cfg))

    @router.post("/login", dependencies=email_only)
    def login(request: Request, email: str = Form(""), password: str = Form(""),
              return_to: str = Form("")) -> Response:
        if not same_origin(request, cfg):
            return forbidden()
        if wait := limited(request, email):
            return too_many(wait)
        user = users.user_by_email(email)
        if not users.check_password(user, password[:PASSWORD_MAX]):
            return pages.login(providers(), cfg.email, safe_return(return_to, cfg), email=email,
                               error="Wrong email or password.", status=400)
        return sign_in(user, return_to)

    @router.get("/signup", dependencies=email_only)
    def signup_form(return_to: str | None = None) -> Response:
        return pages.signup(providers(), safe_return(return_to, cfg))

    @router.post("/signup", dependencies=email_only)
    def signup(request: Request, background: BackgroundTasks, email: str = Form(""),
               password: str = Form(""), name: str = Form(""), return_to: str = Form("")) -> Response:
        if not same_origin(request, cfg):
            return forbidden()
        if wait := limited(request, email):
            return too_many(wait)
        email, name, return_to = users.normalize_email(email), name.strip()[:100], safe_return(return_to, cfg)
        problem = None if valid_email(email) else "Enter a valid email address."
        problem = problem or password_problem(password)
        if problem:
            return pages.signup(providers(), return_to, email=email, name=name, error=problem, status=400)
        if users.user_by_email(email) is not None:
            # Same answer as a new sign-up; the owner hears about it by email.
            background.add_task(send, email, "You already have an account", pages.already_registered_email(
                f"{cfg.base_url}/auth/login?return_to={quote(return_to, safe='')}",
                f"{cfg.base_url}/auth/forgot"))
        else:
            # The password is held with the link and only lands on an account
            # when the address's owner clicks it.
            data = json.dumps({"password_hash": users.hash_password(password), "name": name or None})
            token = users.issue_token("verify", email, data=data, return_to=return_to)
            background.add_task(send, email, "Confirm your email", pages.verify_email(link("verify", token)))
        return pages.message("Check your email", f"We sent a link to {email}. Open it to finish signing up. "
                             "It works for 24 hours.")

    @router.get("/verify", dependencies=email_only)
    def verify_form(token: str = "") -> Response:
        row = users.peek_token("verify", token)
        if row is None:
            return pages.message("Link expired", "That link has expired or was already used. "
                                 "Sign up again to get a new one.", status=400)
        return pages.confirm("Confirm your email", f"Finish signing up as {row['email']}.",
                             "/auth/verify", token, "Confirm and sign in")

    @router.post("/verify", dependencies=email_only)
    def verify(request: Request, token: str = Form("")) -> Response:
        if not same_origin(request, cfg):
            return forbidden()
        row = users.use_token("verify", token)
        if row is None:
            return pages.message("Link expired", "That link has expired or was already used. "
                                 "Sign up again to get a new one.", status=400)
        data = json.loads(row["data"] or "{}")
        user, _ = users.user_for_email_link(row["email"], name=data.get("name"),
                                            password_hash=data.get("password_hash"))
        return sign_in(user, row["return_to"])

    # ── magic link ───────────────────────────────────────────────────────────

    @router.post("/magic-link", dependencies=email_only)
    def magic_link(request: Request, background: BackgroundTasks, email: str = Form(""),
                   return_to: str = Form("")) -> Response:
        if not same_origin(request, cfg):
            return forbidden()
        if wait := limited(request, email):
            return too_many(wait)
        email = users.normalize_email(email)
        if not valid_email(email):
            return pages.login(providers(), cfg.email, safe_return(return_to, cfg), email=email,
                               error="Enter a valid email address.", status=400)
        # Sent whether or not there is an account: following it creates one.
        token = users.issue_token("magic", email, return_to=safe_return(return_to, cfg))
        background.add_task(send, email, "Your sign-in link", pages.magic_email(link("magic", token)))
        return pages.message("Check your email", f"We sent a sign-in link to {email}. It works once, "
                             "for the next 15 minutes.")

    @router.get("/magic", dependencies=email_only)
    def magic_form(token: str = "") -> Response:
        row = users.peek_token("magic", token)
        if row is None:
            return pages.message("Link expired", "That sign-in link has expired or was already used. "
                                 "Ask for a new one.", status=400)
        return pages.confirm("Sign in", f"Continue as {row['email']}.", "/auth/magic", token, "Sign in")

    @router.post("/magic", dependencies=email_only)
    def magic(request: Request, token: str = Form("")) -> Response:
        if not same_origin(request, cfg):
            return forbidden()
        row = users.use_token("magic", token)
        if row is None:
            return pages.message("Link expired", "That sign-in link has expired or was already used. "
                                 "Ask for a new one.", status=400)
        user, _ = users.user_for_email_link(row["email"])
        return sign_in(user, row["return_to"])

    # ── password reset ───────────────────────────────────────────────────────

    @router.get("/forgot", dependencies=email_only)
    def forgot_form(return_to: str | None = None) -> Response:
        return pages.forgot(safe_return(return_to, cfg))

    @router.post("/forgot", dependencies=email_only)
    def forgot(request: Request, background: BackgroundTasks, email: str = Form(""),
               return_to: str = Form("")) -> Response:
        if not same_origin(request, cfg):
            return forbidden()
        if wait := limited(request, email):
            return too_many(wait)
        email = users.normalize_email(email)
        if users.user_by_email(email) is not None:
            token = users.issue_token("reset", email, return_to=safe_return(return_to, cfg))
            background.add_task(send, email, "Reset your password", pages.reset_email(link("reset", token)))
        return pages.message("Check your email", f"If {email} has an account, we sent it a link to set "
                             "a new password. It works for 30 minutes.")

    @router.get("/reset", dependencies=email_only)
    def reset_form(token: str = "") -> Response:
        if users.peek_token("reset", token) is None:
            return pages.message("Link expired", "That reset link has expired or was already used. "
                                 "Ask for a new one.", status=400)
        return pages.reset(token)

    @router.post("/reset", dependencies=email_only)
    def reset(request: Request, token: str = Form(""), password: str = Form("")) -> Response:
        if not same_origin(request, cfg):
            return forbidden()
        if problem := password_problem(password):
            return pages.reset(token, error=problem, status=400)
        row = users.use_token("reset", token)
        user = users.user_by_email(row["email"]) if row else None
        if user is None:
            return pages.message("Link expired", "That reset link has expired or was already used. "
                                 "Ask for a new one.", status=400)
        users.set_password(user["id"], users.hash_password(password))
        # Whoever knew the old password is signed out everywhere.
        users.end_all_sessions(user["id"])
        return sign_in(user, row["return_to"])

    # ── Google / GitHub ──────────────────────────────────────────────────────

    def redirect_uri(name: str) -> str:
        return f"{cfg.base_url}/auth/oauth/{name}/callback"

    @router.get("/oauth/{name}")
    def oauth_start(request: Request, name: str, return_to: str | None = None) -> Response:
        if name not in oauth.configured():
            return pages.message("Not available", "That sign-in method is not set up.", status=404)
        if wait := limited(request):
            return too_many(wait)
        state = secrets.token_urlsafe(24)
        verifier, challenge = oauth.new_pkce()
        resp = RedirectResponse(oauth.authorize_url(name, redirect_uri(name), state, challenge), status_code=303)
        value = session_cookie.sign({"p": name, "state": state, "v": verifier,
                                     "r": safe_return(return_to, cfg), "exp": users.now() + 600},
                                    cfg.secret, purpose="oauth")
        resp.set_cookie(OAUTH_COOKIE, value, max_age=600, path="/auth/oauth", secure=cfg.secure,
                        httponly=True, samesite="lax")
        return resp

    @router.get("/oauth/{name}/callback")
    def oauth_callback(request: Request, name: str, code: str = "", state: str = "",
                       error: str = "") -> Response:
        raw = request.cookies.get(OAUTH_COOKIE)
        saved = session_cookie.verify(raw, cfg.secret, purpose="oauth") if raw else None
        return_to = saved.get("r") if saved else None

        def fail(msg: str, status: int = 400) -> Response:
            r = pages.login(providers(), cfg.email, safe_return(return_to, cfg), error=msg, status=status)
            r.delete_cookie(OAUTH_COOKIE, path="/auth/oauth")
            return r

        if error:
            return fail("Sign-in was cancelled.")
        if not saved or saved.get("p") != name or not state or saved.get("state") != state or not code:
            return fail("That sign-in attempt expired. Please try again.")
        try:
            profile = oauth.fetch_profile(name, code, redirect_uri(name), saved["v"])
        except oauth.OAuthError as e:
            return fail(str(e), status=502)
        if not profile.email:
            return fail(f"Your {oauth.PROVIDERS[name]['label']} account has no verified email address. "
                        "Verify one there, or sign in with email instead.")
        user, how = users.user_for_identity(name, profile.id, profile.email, profile.name)
        _log.info("%s sign-in for user %s (%s)", name, user["id"], how)
        resp = sign_in(user, return_to)
        resp.delete_cookie(OAUTH_COOKIE, path="/auth/oauth")
        return resp

    # ── sign out ─────────────────────────────────────────────────────────────

    @router.post("/logout")
    def logout(request: Request, return_to: str = Form(""), everywhere: str = Form("")) -> Response:
        if not same_origin(request, cfg):
            return forbidden()
        found = current(request)
        if found is not None:
            user, sid, _ = found
            if everywhere:
                users.end_all_sessions(user["id"])
            else:
                users.end_session(sid)
        resp = RedirectResponse(safe_return(return_to, cfg), status_code=303)
        clear_session_cookie(resp)
        return resp

    return router
