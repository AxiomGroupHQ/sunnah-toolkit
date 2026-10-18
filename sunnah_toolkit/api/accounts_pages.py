"""Sign-in pages and email texts for api/accounts.py.

Plain server-rendered forms, no JavaScript, in the search page's colours
(api/ui.py). Every value from the request is escaped on the way in.
"""

from __future__ import annotations

from html import escape
from urllib.parse import quote

from fastapi.responses import HTMLResponse

_CSS = """
* { box-sizing: border-box; }
:root {
  --bg: #faf9f5; --card: #ffffff; --text: #1c1c1c; --muted: #6b6b6b; --faint: #9a988f;
  --accent: #0f7058; --accent-hover: #0a5644; --accent-soft: #e6f2ee;
  --border: #e5e3da; --soft: #f3f1e8; --error: #b03a2e; --error-bg: #fbe7e4;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #141513; --card: #1d1e1b; --text: #ecebe6; --muted: #a5a39b; --faint: #77756e;
    --accent: #3fb68f; --accent-hover: #5cc9a3; --accent-soft: #1d3029;
    --border: #2f302c; --soft: #252622; --error: #f08a7e; --error-bg: #3a1c18;
    color-scheme: dark;
  }
}
html, body { margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg); color: var(--text); line-height: 1.55; padding: 0 16px;
  -webkit-text-size-adjust: 100%;
}
main { max-width: 400px; margin: 0 auto; padding: 3.5rem 0 2rem; }
a { color: var(--accent); }
.brand { display: inline-flex; align-items: center; gap: 0.6rem; color: var(--text); text-decoration: none;
  margin-bottom: 1.4rem; font-weight: 700; font-size: 1.1rem; }
.brand svg { width: 28px; height: 28px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 1.4rem; }
h1 { margin: 0 0 0.3rem; font-size: 1.35rem; letter-spacing: -0.01em; }
.lead { color: var(--muted); margin: 0 0 1.1rem; font-size: 0.95rem; }
label { display: block; font-size: 0.86rem; font-weight: 600; margin: 0.8rem 0 0.3rem; }
label .opt { color: var(--faint); font-weight: 400; }
input[type=email], input[type=password], input[type=text] {
  width: 100%; font: inherit; font-size: 1rem; color: var(--text); background: var(--bg);
  border: 1px solid var(--border); border-radius: 10px; padding: 0.6rem 0.75rem; outline: none;
}
input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.btn {
  display: flex; justify-content: center; align-items: center; gap: 0.5rem; width: 100%;
  margin-top: 1rem; padding: 0.65rem 1rem; border-radius: 10px; font: inherit; font-weight: 600;
  font-size: 0.98rem; cursor: pointer; text-decoration: none; border: 1px solid var(--accent);
  background: var(--accent); color: #fff;
}
.btn:hover { background: var(--accent-hover); }
.btn.alt { background: var(--card); color: var(--text); border-color: var(--border); }
.btn.alt:hover { border-color: var(--accent); color: var(--accent); background: var(--card); }
.btn + .btn { margin-top: 0.5rem; }
.or { display: flex; align-items: center; gap: 0.6rem; color: var(--faint); font-size: 0.82rem; margin: 1.2rem 0 0.2rem; }
.or::before, .or::after { content: ""; flex: 1; border-top: 1px solid var(--border); }
.error { background: var(--error-bg); color: var(--error); border-radius: 10px; padding: 0.6rem 0.8rem;
  font-size: 0.9rem; margin-bottom: 0.6rem; }
.small { color: var(--muted); font-size: 0.86rem; margin: 1rem 0 0; text-align: center; }
.small a + a { margin-left: 0.8rem; }
.row { display: flex; justify-content: space-between; gap: 1rem; font-size: 0.92rem; padding: 0.4rem 0;
  border-bottom: 1px solid var(--border); }
.row span:first-child { color: var(--muted); }
main.wide { max-width: 640px; }
.policy h2 { font-size: 1.02rem; margin: 1.3rem 0 0.3rem; }
.policy p, .policy li { font-size: 0.94rem; }
.policy p { margin: 0.4rem 0; }
.policy ul { margin: 0.4rem 0; padding-left: 1.2rem; }
footer { margin-top: 2rem; text-align: center; color: var(--faint); font-size: 0.8rem; }
footer a { color: var(--muted); }
"""

_LOGO = ('<svg viewBox="0 0 64 64" aria-hidden="true"><rect width="64" height="64" rx="14" fill="#0f7058"/>'
         '<path d="M14 20c7-3 13-2 18 2 5-4 11-5 18-2v26c-7-3-13-2-18 2-5-4-11-5-18-2z" fill="none" '
         'stroke="#fff" stroke-width="3.5" stroke-linejoin="round"/><path d="M32 22v26" stroke="#fff" '
         'stroke-width="3.5"/></svg>')


_FOOTER = 'Signing in is optional. Searching works without an account. <a href="/privacy">Privacy</a>'


def _page(title: str, body: str, status: int = 200, footer: str = _FOOTER, wide: bool = False) -> HTMLResponse:
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex">
  <title>{escape(title)} · Sunnah Semantic Search</title>
  <meta name="theme-color" content="#0f7058">
  <style>{_CSS}</style>
</head>
<body>
  <main{' class="wide"' if wide else ''}>
    <a class="brand" href="/">{_LOGO}Sunnah Semantic Search</a>
    <div class="card">
{body}
    </div>
    <footer>{footer}</footer>
  </main>
</body>
</html>"""
    return HTMLResponse(html, status_code=status)


def _hidden(name: str, value: str) -> str:
    return f'<input type="hidden" name="{name}" value="{escape(value)}">'


def _error(error: str | None) -> str:
    return f'<div class="error" role="alert">{escape(error)}</div>' if error else ""


def _q(return_to: str) -> str:
    return "?return_to=" + quote(return_to, safe="")


def _provider_buttons(providers: list[tuple[str, str]], return_to: str, divider: bool = True) -> str:
    if not providers:
        return ""
    buttons = "".join(
        f'<a class="btn alt" href="/auth/oauth/{name}{_q(return_to)}">Continue with {escape(label)}</a>'
        for name, label in providers)
    return buttons + ('<div class="or">or</div>' if divider else "")


def login(providers: list[tuple[str, str]], email_on: bool, return_to: str, *, email: str = "",
          error: str | None = None, status: int = 200) -> HTMLResponse:
    head = f"""
      <h1>Sign in</h1>
      <p class="lead">One account for hadith search and the adhan clock.</p>
      {_error(error)}
      {_provider_buttons(providers, return_to, divider=email_on)}"""
    if not email_on:
        return _page("Sign in", head, status)
    return _page("Sign in", head + f"""
      <form method="post" action="/auth/login">
        {_hidden("return_to", return_to)}
        <label for="email">Email</label>
        <input type="email" id="email" name="email" value="{escape(email)}" autocomplete="email" required>
        <label for="password">Password</label>
        <input type="password" id="password" name="password" autocomplete="current-password" required>
        <button class="btn" type="submit">Sign in</button>
      </form>
      <div class="or">or get a link by email</div>
      <form method="post" action="/auth/magic-link">
        {_hidden("return_to", return_to)}
        <label for="magic-email">Email</label>
        <input type="email" id="magic-email" name="email" value="{escape(email)}" autocomplete="email" required>
        <button class="btn alt" type="submit">Email me a sign-in link</button>
      </form>
      <p class="small"><a href="/auth/signup{_q(return_to)}">Create an account</a><a href="/auth/forgot{_q(return_to)}">Forgot password?</a></p>
""", status)


def signup(providers: list[tuple[str, str]], return_to: str, *, email: str = "", name: str = "",
           error: str | None = None, status: int = 200) -> HTMLResponse:
    return _page("Create an account", f"""
      <h1>Create an account</h1>
      <p class="lead">One account for hadith search and the adhan clock.</p>
      {_error(error)}
      {_provider_buttons(providers, return_to)}
      <form method="post" action="/auth/signup">
        {_hidden("return_to", return_to)}
        <label for="name">Name <span class="opt">(optional)</span></label>
        <input type="text" id="name" name="name" value="{escape(name)}" autocomplete="name" maxlength="100">
        <label for="email">Email</label>
        <input type="email" id="email" name="email" value="{escape(email)}" autocomplete="email" required>
        <label for="password">Password</label>
        <input type="password" id="password" name="password" autocomplete="new-password" minlength="8" required>
        <button class="btn" type="submit">Create account</button>
      </form>
      <p class="small">Already have one? <a href="/auth/login{_q(return_to)}">Sign in</a></p>
""", status)


def forgot(return_to: str) -> HTMLResponse:
    return _page("Reset your password", f"""
      <h1>Reset your password</h1>
      <p class="lead">We'll email you a link to set a new one. This also works if you
      signed up with Google or GitHub and want a password too.</p>
      <form method="post" action="/auth/forgot">
        {_hidden("return_to", return_to)}
        <label for="email">Email</label>
        <input type="email" id="email" name="email" autocomplete="email" required>
        <button class="btn" type="submit">Send reset link</button>
      </form>
      <p class="small"><a href="/auth/login{_q(return_to)}">Back to sign in</a></p>
""")


def reset(token: str, *, error: str | None = None, status: int = 200) -> HTMLResponse:
    return _page("Set a new password", f"""
      <h1>Set a new password</h1>
      <p class="lead">This signs you out on every other device.</p>
      {_error(error)}
      <form method="post" action="/auth/reset">
        {_hidden("token", token)}
        <label for="password">New password</label>
        <input type="password" id="password" name="password" autocomplete="new-password" minlength="8" required>
        <button class="btn" type="submit">Save and sign in</button>
      </form>
""", status)


def confirm(title: str, text: str, action: str, token: str, button: str) -> HTMLResponse:
    return _page(title, f"""
      <h1>{escape(title)}</h1>
      <p class="lead">{escape(text)}</p>
      <form method="post" action="{escape(action)}">
        {_hidden("token", token)}
        <button class="btn" type="submit">{escape(button)}</button>
      </form>
""")


def message(title: str, text: str, *, status: int = 200) -> HTMLResponse:
    return _page(title, f"""
      <h1>{escape(title)}</h1>
      <p class="lead">{escape(text)}</p>
      <p class="small"><a href="/">Back to search</a></p>
""", status)


def account(user: dict, return_to: str, *, email: bool = True) -> HTMLResponse:
    name = f'<div class="row"><span>Name</span><span>{escape(user["name"])}</span></div>' if user.get("name") else ""
    password_link = (f'      <p class="small"><a href="/auth/forgot{_q(return_to)}">Set or change password</a></p>'
                     if email else "")
    return _page("Your account", f"""
      <h1>Your account</h1>
      {name}
      <div class="row"><span>Email</span><span>{escape(user["email"])}</span></div>
      <a class="btn alt" href="{escape(return_to)}">Back</a>
      <form method="post" action="/auth/logout">
        {_hidden("return_to", return_to)}
        <button class="btn alt" type="submit">Sign out</button>
      </form>
      <form method="post" action="/auth/logout">
        {_hidden("return_to", return_to)}{_hidden("everywhere", "1")}
        <button class="btn alt" type="submit">Sign out on all devices</button>
      </form>
{password_link}
""")


# ── Email bodies (plain text) ──────────────────────────────────────────────────

_SIGNOFF = "\n\nIf you didn't ask for this, you can ignore this email.\n\nSunnah Semantic Search\nhttps://searchthehadith.fyi\n"


def verify_email(link: str) -> str:
    return f"Confirm your email to finish creating your account:\n\n{link}\n\nThe link works for 24 hours." + _SIGNOFF


def magic_email(link: str) -> str:
    return f"Sign in to Sunnah Semantic Search:\n\n{link}\n\nThe link works once, for the next 15 minutes." + _SIGNOFF


def reset_email(link: str) -> str:
    return f"Set a new password:\n\n{link}\n\nThe link works for 30 minutes." + _SIGNOFF


def already_registered_email(login_link: str, forgot_link: str) -> str:
    return ("Someone tried to create an account with this email address, but you already have one.\n\n"
            f"Sign in: {login_link}\nForgot your password? {forgot_link}" + _SIGNOFF)


# ── Privacy policy (GET /privacy, registered in api/app.py) ────────────────────

PRIVACY_UPDATED = "2026-10-18"


def privacy() -> HTMLResponse:
    """Covers searchthehadith.fyi and adhan.searchthehadith.fyi. Keep it in step with
    core/querylog.py, core/users.py and my-adhan's assistant/querylog.py."""
    r = _page("Privacy", f"""
      <div class="policy">
      <h1>Privacy</h1>
      <p class="lead">For searchthehadith.fyi and adhan.searchthehadith.fyi. Last updated {PRIVACY_UPDATED}.</p>

      <h2>Accounts are optional</h2>
      <p>Both sites work fully without signing in. If you sign in with Google we store your
      email address, your name and your Google account ID. If you use email sign-in we store
      your email address, the name you give, and a one-way hash of your password (never the
      password itself). Signing in sets a cookie scoped to .searchthehadith.fyi so one sign-in
      covers both sites; it holds a session id and your account id, email and name, and is
      removed when you sign out. Emailed sign-in links are single-use and expire.</p>

      <h2>Search queries and adhan questions</h2>
      <p>To improve results we log what is searched on searchthehadith.fyi and the questions
      asked in the adhan clock's chat. Each entry holds the text, the time, the search options
      or matched answer, the results returned, how long it took, a random id your browser tab
      makes up (so searches from one visit can be grouped) and the country Cloudflare reports.
      It does not hold your account, your IP address or your browser details. These logs stay
      on our server.</p>

      <h2>Server logs</h2>
      <p>Like most web servers, ours keeps access logs of IP addresses and the addresses of
      pages requested, used to run the service and to deal with abuse.</p>

      <h2>Other services</h2>
      <ul>
        <li>Cloudflare sits in front of both sites as a proxy and CDN, and sees every request.
        It may set its own security cookies.</li>
        <li>The search page loads its Arabic font from Google Fonts.</li>
        <li>The adhan clock loads its page libraries from jsDelivr. To work out your prayer
        times it uses your browser's location if you allow it, otherwise asks ipapi.co for an
        approximate location, and looks up place names with OpenStreetMap's Nominatim.</li>
        <li>Your browser keeps your settings (for example the adhan calculation method) in its
        own local storage. They are not sent to us.</li>
      </ul>
      <p>There are no ads and no analytics or tracking scripts, and we do not sell or share
      your data.</p>

      <h2>Deleting your data, questions</h2>
      <p>Email <a href="mailto:hn.najeeb@gmail.com">hn.najeeb@gmail.com</a> to have your
      account deleted or to ask anything about this page.</p>
      </div>
""", footer='<a href="/">Back to search</a>', wide=True)
    r.headers["Cache-Control"] = "public, max-age=3600"
    return r
