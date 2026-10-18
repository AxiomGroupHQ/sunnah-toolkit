"""Ratings that signed-in users give search results (api/feedback.py).

Stored in the users DB (core/users.py) next to the accounts they belong to,
so deleting an account deletes its ratings. Nothing reads them back into
ranking: they are exported (scripts/export_feedback.py) and analysed by hand.

One `feedback_searches` row per user and search holds everything needed to
study it later without re-running it: the query and options, what the server
reported about the ranking, a snapshot of the search settings, and every
result returned in order. `search_id` is the id the web UI already sends as
X-Search-Id, so a rated search can be matched to its query log entries
(core/querylog.py, `queries.search_id`). One `feedback` row per rated result.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from . import users

RATINGS = ("good", "partial", "irrelevant")

SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback_searches (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    search_id   TEXT NOT NULL,      -- web UI's per-search id, = queries.search_id in the query log
    query       TEXT NOT NULL,
    ui_mode     TEXT,               -- mode picked in the UI: auto | natural | semantic | keyword | term
    mode        TEXT NOT NULL,      -- mode searched: natural | semantic | keyword | term
    collections TEXT,               -- JSON list of ticked collections, NULL = all
    "limit"     INTEGER,
    filters     TEXT,               -- JSON: result-page filters when first rated (chips, weak shown)
    response    TEXT,               -- JSON: per API call, what the server said about ranking
    config      TEXT NOT NULL,      -- JSON: server search settings when first rated
    results     TEXT NOT NULL,      -- JSON [[ref, bucket, score, similarity], ...], every result, in order
    n_strong    INTEGER NOT NULL,
    n_weak      INTEGER NOT NULL,
    created_at  INTEGER NOT NULL,   -- unix seconds
    updated_at  INTEGER NOT NULL,
    UNIQUE (user_id, search_id)
);
CREATE TABLE IF NOT EXISTS feedback (
    id            INTEGER PRIMARY KEY,
    search_row    INTEGER NOT NULL REFERENCES feedback_searches(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ref           TEXT NOT NULL,    -- "slug:hadith_number", as in the query log's `top`
    collection    TEXT NOT NULL,
    hadith_number TEXT NOT NULL,    -- sunnah.com number ("402b", "272, 273"), else id_in_book
    number        INTEGER,          -- id_in_book
    rank          INTEGER NOT NULL, -- 1-based position in the full result list (strong, then weak)
    bucket        TEXT NOT NULL,    -- strong | weak
    score         REAL,             -- reranker score
    similarity    REAL,             -- bi-encoder similarity, when that leg found it
    rating        TEXT NOT NULL CHECK (rating IN ('good', 'partial', 'irrelevant')),
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    UNIQUE (search_row, ref)
);
CREATE INDEX IF NOT EXISTS feedback_user ON feedback(user_id);
"""

_ready: set[str] = set()
_ready_lock = threading.Lock()


class Invalid(ValueError):
    """The submission does not fit the search it names."""


def _ensure() -> None:
    """Create the tables once per DB, in a transaction of their own so a
    rejected submission cannot roll them back."""
    path = users.db_path()
    with _ready_lock:
        if path in _ready:
            return
        with users.connect() as c:
            # executescript would COMMIT inside users.connect's transaction,
            # hence the split (so no semicolons in the schema's comments).
            for stmt in SCHEMA.split(";"):
                if stmt.strip():
                    c.execute(stmt)
        _ready.add(path)


def search_config() -> dict[str, Any]:
    """What decides ranking on this server right now, defaults resolved."""
    from . import reranker, retrieval, tools

    def env(name: str, default: str | None = None) -> str | None:
        return os.environ.get(name, "").strip() or default

    # Defaults as in core/semantic_v2.py, core/chapters.py, core/retrieval.py.
    return {
        "SEMANTIC_BACKEND": env("SEMANTIC_BACKEND", "v1").lower(),
        "SEMANTIC_V2_TAG": env("SEMANTIC_V2_TAG", "bge-m3"),
        "SEMANTIC_V2_COMBINE": env("SEMANTIC_V2_COMBINE", "max").lower(),
        "RERANKER_NAME": reranker.default_reranker_name(),
        "RERANKER_ENABLED": reranker.reranker_enabled(),
        "RERANKER_THRESHOLD": reranker.default_threshold(),
        "RERANKER_TOP_N": reranker.default_top_n(),
        "RETRIEVER_K": retrieval.default_k(),
        "FUSION": tools._fusion(),
        "RANK_BY_GRADE": tools._grade_first_enabled(),
        "RETRIEVAL_CHAPTERS": env("RETRIEVAL_CHAPTERS", "") in ("1", "true", "yes"),
        "CHAPTERS_TOP": env("CHAPTERS_TOP", "8"),
        "CHAPTERS_MIN_SIM": env("CHAPTERS_MIN_SIM", "0.55"),
        "CHAPTERS_MAX_MEMBERS": env("CHAPTERS_MAX_MEMBERS", "15"),
        "RETRIEVAL_NEIGHBORS": retrieval.neighbors_enabled(),
        "NEIGHBOR_SEEDS": env("NEIGHBOR_SEEDS", "8"),
        "NEIGHBOR_K": env("NEIGHBOR_K", "5"),
        "NEIGHBOR_MIN_SIM": env("NEIGHBOR_MIN_SIM", "0.85"),
        "LLM_PROVIDER": env("LLM_PROVIDER"),
    }


def _hadith_missing(results: list[dict[str, Any]]) -> list[str]:
    """Refs not in the hadith library. Only checked once the library is
    loaded (it always is on the server after the warm-up search), so a
    rating never pays the ~45 s first load."""
    from . import data
    if not data.load.cache_info().currsize:
        return []
    library = data.load()
    return [r["ref"] for r in results if library.get_hadith(r["collection"], r["hadith_number"]) is None]


def save(user_id: int, search: dict[str, Any], ratings: dict[str, str | None]) -> dict[str, str]:
    """Record `ratings` ({ref: rating, or None to clear}) for one search.

    The search row (with its result list) is written on the first submission
    and kept as it was; later ones only add, change or clear ratings. Every
    rated ref must be in that stored list. Returns the saved ratings, all of
    them, for this search.
    """
    t = users.now()
    _ensure()
    with users.connect() as c:
        row = c.execute("SELECT * FROM feedback_searches WHERE user_id = ? AND search_id = ?",
                        (user_id, search["search_id"])).fetchone()
        if row is None:
            results = search["results"]
            stored = [[r["ref"], r["bucket"], r["score"], r["similarity"]] for r in results]
            cur = c.execute(
                'INSERT INTO feedback_searches (user_id, search_id, query, ui_mode, mode, collections, "limit",'
                " filters, response, config, results, n_strong, n_weak, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, search["search_id"], search["query"], search["ui_mode"], search["mode"],
                 json.dumps(search["collections"]) if search["collections"] is not None else None,
                 search["limit"], json.dumps(search["filters"]), json.dumps(search["response"]),
                 json.dumps(search_config()), json.dumps(stored),
                 sum(r["bucket"] == "strong" for r in results), sum(r["bucket"] == "weak" for r in results),
                 t, t))
            row_id = cur.lastrowid
            by_ref = {r["ref"]: (i + 1, r) for i, r in enumerate(results)}
        else:
            if row["query"] != search["query"] or row["mode"] != search["mode"]:
                raise Invalid("that search id belongs to a different search")
            row_id = row["id"]
            by_ref = {}
            for i, (ref, bucket, score, sim) in enumerate(json.loads(row["results"])):
                coll, _, num = ref.partition(":")
                by_ref.setdefault(ref, (i + 1, {"ref": ref, "collection": coll, "hadith_number": num,
                                                "number": None, "bucket": bucket, "score": score,
                                                "similarity": sim}))
            # The stored list has no id_in_book; take it from this submission's.
            for r in search["results"]:
                if r["ref"] in by_ref:
                    by_ref[r["ref"]][1]["number"] = r["number"]
        unknown = [ref for ref in ratings if ref not in by_ref]
        if unknown:
            raise Invalid(f"not in this search's results: {', '.join(unknown[:5])}")
        if missing := _hadith_missing([by_ref[ref][1] for ref, rating in ratings.items() if rating]):
            raise Invalid(f"unknown hadith: {', '.join(missing[:5])}")
        for ref, rating in ratings.items():
            if rating is None:
                c.execute("DELETE FROM feedback WHERE search_row = ? AND ref = ?", (row_id, ref))
                continue
            rank, r = by_ref[ref]
            c.execute(
                "INSERT INTO feedback (search_row, user_id, ref, collection, hadith_number, number, rank, bucket,"
                " score, similarity, rating, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (search_row, ref) DO UPDATE SET rating = excluded.rating,"
                " updated_at = excluded.updated_at WHERE feedback.rating != excluded.rating",
                (row_id, user_id, ref, r["collection"], r["hadith_number"], r["number"], rank, r["bucket"],
                 r["score"], r["similarity"], rating, t, t))
        c.execute("UPDATE feedback_searches SET updated_at = ? WHERE id = ?", (t, row_id))
        return _ratings(c, row_id)


def _ratings(c, row_id: int) -> dict[str, str]:
    return {r["ref"]: r["rating"] for r in c.execute(
        "SELECT ref, rating FROM feedback WHERE search_row = ? ORDER BY rank", (row_id,))}


def ratings_for(user_id: int, search_id: str) -> dict[str, str]:
    """This user's saved ratings for one search ({} if none)."""
    _ensure()
    with users.connect() as c:
        row = c.execute("SELECT id FROM feedback_searches WHERE user_id = ? AND search_id = ?",
                        (user_id, search_id)).fetchone()
        return _ratings(c, row["id"]) if row else {}
