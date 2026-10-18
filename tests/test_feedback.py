"""Result ratings (api/feedback.py, core/feedback.py, scripts/export_feedback.py)."""

from __future__ import annotations

import csv
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sunnah_toolkit.api import feedback as feedback_api
from sunnah_toolkit.core import data, session_cookie, users

SITE = "https://searchthehadith.fyi"
SECRET = "test-secret-not-for-production"
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET",
              "RATE_LIMIT_PER_MIN", "RATE_LIMIT_FEEDBACK_PER_MIN", "AUTH_RETURN_HOSTS", "AUTH_COOKIE_NAME",
              "RESEND_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AUTH_EMAIL_DEV", "1")
    db = tmp_path / "accounts" / "users.sqlite"
    monkeypatch.setenv("USERS_DB_PATH", str(db))
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    monkeypatch.setenv("AUTH_BASE_URL", SITE)
    monkeypatch.setenv("SEMANTIC_BACKEND", "v2")
    monkeypatch.setenv("RERANKER_NAME", "minilm-l6")
    monkeypatch.setenv("RERANKER_TOP_N", "40")
    return db


def new_client():
    from sunnah_toolkit.api.app import create_app
    return TestClient(create_app(), base_url=SITE, follow_redirects=False)


def sign_in(c, email="amina@example.com"):
    user, _ = users.user_for_email_link(email, name="Amina")
    sid, exp = users.create_session(user["id"], 3600)
    c.cookies.set("sth_session", session_cookie.sign(
        {"sid": sid, "uid": user["id"], "email": user["email"], "name": "Amina", "exp": exp}, SECRET))
    return user


@pytest.fixture
def client(env):
    c = new_client()
    sign_in(c)
    return c


RESULTS = [
    {"collection": "bukhari", "hadith_number": "6312", "number": 5874, "score": 7.25, "similarity": 0.61,
     "bucket": "strong"},
    {"collection": "muslim", "hadith_number": "2711 a", "number": 2702, "score": 4.0, "similarity": None,
     "bucket": "strong"},
    {"collection": "abudawud", "hadith_number": "5049", "number": 5031, "score": 1.5, "similarity": 0.4,
     "bucket": "weak"},
]


def payload(ratings, search_id="search-0001", query="dua before sleep", results=RESULTS, **extra):
    return {
        "search_id": search_id, "query": query, "ui_mode": "auto", "mode": "natural",
        "collections": None, "limit": 50000,
        "filters": {"result_collections": ["bukhari", "muslim", "abudawud"], "weak_visible": False, "page": 1},
        "response": [{"reranker": "minilm-l6", "threshold": 3.5, "grade_first": True}],
        "results": results,
        "ratings": [{"ref": r, "rating": v} for r, v in ratings.items()],
        **extra,
    }


def submit(c, body, origin=SITE, **kw):
    headers = {"origin": origin} if origin else {}
    return c.post("/v1/feedback", json=body, headers=headers, **kw)


def rows(db, table="feedback"):
    if not db.exists():
        return []
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone():
        return []
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


# ── who may rate ───────────────────────────────────────────────────────────────

def test_anonymous_gets_401(env):
    c = new_client()
    r = submit(c, payload({"bukhari:6312": "good"}))
    assert r.status_code == 401 and r.headers["cache-control"] == "no-store"
    assert c.get("/v1/feedback", params={"search_id": "search-0001"}).status_code == 401


def test_ended_session_gets_401(client):
    users.end_all_sessions(1)
    assert submit(client, payload({"bukhari:6312": "good"})).status_code == 401


def test_off_when_sign_in_is_off(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    monkeypatch.setenv("USERS_DB_PATH", str(tmp_path / "u.sqlite"))
    assert submit(new_client(), payload({"bukhari:6312": "good"})).status_code == 404


@pytest.mark.parametrize("origin", [None, "https://evil.example", "null"])
def test_other_origins_are_blocked(client, env, origin):
    assert submit(client, payload({"bukhari:6312": "good"}), origin=origin).status_code == 403
    assert not rows(env)


def test_referer_counts_when_origin_is_missing(client):
    r = client.post("/v1/feedback", json=payload({"bukhari:6312": "good"}), headers={"referer": SITE + "/?q=x"})
    assert r.status_code == 200


def test_must_be_json(client):
    r = client.post("/v1/feedback", content=json.dumps(payload({"bukhari:6312": "good"})),
                    headers={"origin": SITE, "content-type": "text/plain"})
    assert r.status_code == 415


# ── saving and updating ────────────────────────────────────────────────────────

def test_submit_then_update_upserts(client, env):
    r = submit(client, payload({"bukhari:6312": "good", "abudawud:5049": "irrelevant"}))
    assert r.status_code == 200, r.text
    assert r.json() == {"search_id": "search-0001",
                        "ratings": {"bukhari:6312": "good", "abudawud:5049": "irrelevant"}}

    [search] = rows(env, "feedback_searches")
    assert search["user_id"] == 1 and search["query"] == "dua before sleep" and search["mode"] == "natural"
    assert search["ui_mode"] == "auto" and search["collections"] is None and search["limit"] == 50000
    assert (search["n_strong"], search["n_weak"]) == (2, 1)
    assert json.loads(search["results"]) == [["bukhari:6312", "strong", 7.25, 0.61],
                                             ["muslim:2711 a", "strong", 4.0, None],
                                             ["abudawud:5049", "weak", 1.5, 0.4]]
    config = json.loads(search["config"])
    assert config["SEMANTIC_BACKEND"] == "v2" and config["RERANKER_NAME"] == "minilm-l6"
    assert config["RERANKER_TOP_N"] == 40 and config["RERANKER_THRESHOLD"] == 3.5
    assert {"RETRIEVAL_CHAPTERS", "RETRIEVAL_NEIGHBORS", "RANK_BY_GRADE", "FUSION", "RETRIEVER_K"} <= set(config)
    assert json.loads(search["response"])[0]["reranker"] == "minilm-l6"
    assert json.loads(search["filters"])["page"] == 1

    good, bad = rows(env)
    assert (good["ref"], good["collection"], good["hadith_number"], good["number"]) == \
        ("bukhari:6312", "bukhari", "6312", 5874)
    assert (good["rank"], good["bucket"], good["score"], good["similarity"], good["rating"]) == \
        (1, "strong", 7.25, 0.61, "good")
    assert (bad["rank"], bad["bucket"], bad["rating"]) == (3, "weak", "irrelevant")

    # Change one, clear one, add one: still one search, no duplicate rows.
    r = submit(client, payload({"bukhari:6312": "partial", "abudawud:5049": None, "muslim:2711 a": "good"}))
    assert r.status_code == 200
    assert r.json()["ratings"] == {"bukhari:6312": "partial", "muslim:2711 a": "good"}
    assert len(rows(env, "feedback_searches")) == 1
    assert {(f["ref"], f["rating"], f["rank"]) for f in rows(env)} == \
        {("bukhari:6312", "partial", 1), ("muslim:2711 a", "good", 2)}
    assert client.get("/v1/feedback", params={"search_id": "search-0001"}).json()["ratings"] == \
        {"bukhari:6312": "partial", "muslim:2711 a": "good"}


def test_each_user_has_their_own_ratings(client, env):
    submit(client, payload({"bukhari:6312": "good"}))
    other = new_client()
    sign_in(other, "bilal@example.com")
    assert other.get("/v1/feedback", params={"search_id": "search-0001"}).json()["ratings"] == {}
    assert submit(other, payload({"bukhari:6312": "irrelevant"})).status_code == 200
    assert sorted((f["user_id"], f["rating"]) for f in rows(env)) == [(1, "good"), (2, "irrelevant")]


def test_hadith_number_falls_back_to_id_in_book(client, env):
    results = [{"collection": "forty", "hadith_number": "", "number": 3, "score": 5.0, "bucket": "strong"}]
    assert submit(client, payload({"forty:3": "good"}, results=results)).status_code == 200
    assert rows(env)[0]["hadith_number"] == "3"


# ── validation ─────────────────────────────────────────────────────────────────

def test_rejects_hadith_not_in_the_results(client, env):
    r = submit(client, payload({"tirmidhi:1": "good"}))
    assert r.status_code == 422 and "tirmidhi:1" in r.json()["detail"]
    assert rows(env, "feedback_searches") == []  # nothing half-written
    assert client.get("/v1/feedback", params={"search_id": "search-0001"}).json()["ratings"] == {}


def test_result_list_is_fixed_by_the_first_submission(client):
    assert submit(client, payload({"bukhari:6312": "good"})).status_code == 200
    padded = RESULTS + [{"collection": "tirmidhi", "hadith_number": "1", "number": 1, "bucket": "weak"}]
    assert submit(client, payload({"tirmidhi:1": "good"}, results=padded)).status_code == 422


def test_search_id_cannot_be_reused_for_another_query(client):
    assert submit(client, payload({"bukhari:6312": "good"})).status_code == 200
    assert submit(client, payload({"bukhari:6312": "good"}, query="anger")).status_code == 422


def test_rejects_hadith_missing_from_the_library(client, monkeypatch):
    if not data.DB_PATH.exists():
        pytest.skip("hadith dataset not built")
    data.load()  # as on the server after its warm-up search
    results = RESULTS + [{"collection": "bukhari", "hadith_number": "999999", "number": 999999, "bucket": "weak"}]
    r = submit(client, payload({"bukhari:999999": "good"}, results=results))
    assert r.status_code == 422 and "unknown hadith" in r.json()["detail"]
    assert submit(client, payload({"bukhari:6312": "good"}, search_id="search-0002")).status_code == 200


@pytest.mark.parametrize("change", [
    {"search_id": "short"},
    {"search_id": "has spaces in it"},
    {"query": ""},
    {"query": "x" * 1001},
    {"mode": "reference"},
    {"ui_mode": "bogus"},
    {"collections": ["Bukhari!"]},
    {"limit": 0},
    {"results": []},
    {"results": [{"collection": "bukhari", "hadith_number": "1<script>", "bucket": "strong"}]},
    {"results": [{"collection": "bukhari", "hadith_number": "1", "bucket": "middling"}]},
    {"results": [{"collection": "bukhari", "hadith_number": "1", "bucket": "strong", "score": "high"}]},
    {"ratings": []},
    {"ratings": [{"ref": "bukhari:6312", "rating": "excellent"}]},
    {"filters": {"x": "y" * 20_000}},
    {"response": "not a list"},
])
def test_rejects_malformed_submissions(client, env, change):
    body = {**payload({"bukhari:6312": "good"}), **change}
    assert submit(client, body).status_code == 422
    assert not rows(env, "feedback_searches")


def test_rejects_too_many_results_or_ratings(client):
    many = [{"collection": "bukhari", "hadith_number": str(i), "number": i, "bucket": "weak"}
            for i in range(feedback_api.MAX_RESULTS + 1)]
    assert submit(client, payload({"bukhari:1": "good"}, results=many)).status_code == 422
    lots = {f"bukhari:{i}": "good" for i in range(feedback_api.MAX_RATINGS + 1)}
    assert submit(client, payload(lots, results=many[:600])).status_code == 422


def test_rejects_oversized_body(client):
    body = json.dumps(payload({"bukhari:6312": "good"}, padding="x" * (feedback_api.MAX_BODY + 1)))
    r = client.post("/v1/feedback", content=body, headers={"origin": SITE, "content-type": "application/json"})
    assert r.status_code == 413


def test_rejects_invalid_json(client):
    r = client.post("/v1/feedback", content=b"{not json",
                    headers={"origin": SITE, "content-type": "application/json"})
    assert r.status_code == 422


def test_rate_limited_per_user(env, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_FEEDBACK_PER_MIN", "2")
    c = new_client()
    sign_in(c)
    assert submit(c, payload({"bukhari:6312": "good"})).status_code == 200
    assert submit(c, payload({"bukhari:6312": "partial"})).status_code == 200
    r = submit(c, payload({"bukhari:6312": "good"}))
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


# ── export ─────────────────────────────────────────────────────────────────────

def test_export_script(client, env, tmp_path):
    submit(client, payload({"bukhari:6312": "good", "abudawud:5049": "partial"}))
    out = tmp_path / "feedback.csv"
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_feedback.py"), "--db", str(env),
                        "-o", str(out), "--with-results"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "wrote 2 rows" in p.stderr
    with open(out, newline="", encoding="utf-8") as f:
        recs = list(csv.DictReader(f))
    assert [(r["ref"], r["rank"], r["rating"]) for r in recs] == [("bukhari:6312", "1", "good"),
                                                                   ("abudawud:5049", "3", "partial")]
    first = recs[0]
    assert first["email"] == "amina@example.com" and first["search_id"] == "search-0001"
    assert first["query"] == "dua before sleep" and first["mode"] == "natural" and first["bucket"] == "strong"
    assert first["semantic_backend"] == "v2" and first["reranker_name"] == "minilm-l6"
    assert first["reranker_top_n"] == "40" and first["created_at"].endswith("+00:00")
    assert len(json.loads(first["results"])) == 3

    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_feedback.py"), "--db", str(env),
                        "--user", "nobody@example.com"], capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout.strip().count("\n") == 0  # header only


def test_export_without_db(tmp_path):
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_feedback.py"),
                        "--db", str(tmp_path / "none.sqlite")], capture_output=True, text=True)
    assert p.returncode == 1


# ── search page ────────────────────────────────────────────────────────────────

def test_page_has_rating_hooks_gated_on_sign_in(env):
    from sunnah_toolkit.api.ui import INDEX_HTML
    r = new_client().get("/")
    assert r.status_code == 200 and r.text == INDEX_HTML
    assert 'id="fb-bar" hidden' in INDEX_HTML and 'id="fb-submit"' in INDEX_HTML
    assert 'fetch("/v1/feedback"' in INDEX_HTML
    # Controls render only for a signed-in visitor with a search to file them under.
    assert 'if (!signedIn || !lastSearch) return "";' in INDEX_HTML
    assert "signedIn = true;" in INDEX_HTML and INDEX_HTML.count("signedIn = true") == 1
    for label in ("Good match", "Partly relevant", "Not relevant"):
        assert label in INDEX_HTML
    assert 'aria-pressed="' in INDEX_HTML and "feedbackHtml(item)" in INDEX_HTML


def test_search_json_shape_is_unchanged(env, monkeypatch):
    from sunnah_toolkit.core import tools
    fake = {"query": "q", "results": [{"slug": "bukhari", "hadith_number": "1", "number": 1, "score": 1.0}],
            "results_weak": []}
    monkeypatch.setattr(tools, "search_hadith_natural", lambda *a, **k: dict(fake))
    assert new_client().get("/v1/search/natural", params={"query": "q"}).json() == fake
