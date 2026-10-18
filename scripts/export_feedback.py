"""Export result ratings (core/feedback.py) to CSV for analysis.

    python scripts/export_feedback.py                        # every rating -> stdout
    python scripts/export_feedback.py --since 2026-10-01 -o feedback.csv
    python scripts/export_feedback.py --user amina@example.com --with-results

One row per rating, joined with its search: query, modes, options, the
server's search settings at the time (also unpacked into a few columns) and
what the server reported about the ranking. --with-results adds the full
ordered result list shown for the search (JSON, the same on every row of a
search). `search_id` matches `queries.search_id` in the query log
(scripts/export_queries.py) for the same search.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

DEFAULT_DB = os.environ.get("USERS_DB_PATH") or os.path.join(
    os.path.dirname(__file__), "..", "data", "accounts", "users.sqlite"
)

# Settings pulled out of the config snapshot into their own columns.
CONFIG_COLUMNS = ("SEMANTIC_BACKEND", "RERANKER_NAME", "RERANKER_TOP_N", "RERANKER_THRESHOLD",
                  "RETRIEVAL_CHAPTERS", "RETRIEVAL_NEIGHBORS")


def _unix(s: str) -> int:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _iso(t: int | None) -> str | None:
    return datetime.fromtimestamp(t, timezone.utc).isoformat() if t is not None else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--since", help="UTC date or timestamp, inclusive, of the rating's last change")
    ap.add_argument("--until", help="UTC date or timestamp, exclusive")
    ap.add_argument("--user", help="only this account's ratings (email)")
    ap.add_argument("--with-results", action="store_true", help="add the full result list shown for each search")
    ap.add_argument("-o", "--out", help="CSV path (default: stdout)")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"no users DB at {args.db}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'feedback'").fetchone():
        print("no ratings yet (no feedback table)", file=sys.stderr)
        return 1

    where, params = [], []
    if args.since:
        where.append("f.updated_at >= ?"); params.append(_unix(args.since))
    if args.until:
        where.append("f.updated_at < ?"); params.append(_unix(args.until))
    if args.user:
        where.append("u.email = ?"); params.append(args.user.strip().lower())
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    cur = conn.execute(f"""
        SELECT f.id AS rating_id, f.created_at, f.updated_at, u.id AS user_id, u.email,
               s.search_id, s.query, s.ui_mode, s.mode, s.collections, s."limit", s.n_strong, s.n_weak,
               f.ref, f.collection, f.hadith_number, f.number, f.rank, f.bucket, f.score, f.similarity,
               f.rating, s.filters, s.response, s.config, s.results
        FROM feedback f
        JOIN feedback_searches s ON s.id = f.search_row
        JOIN users u ON u.id = f.user_id
        {clause}
        ORDER BY s.created_at, s.id, f.rank""", params)

    cols = ["rating_id", "created_at", "updated_at", "user_id", "email", "search_id", "query", "ui_mode",
            "mode", "collections", "limit", "n_strong", "n_weak", "ref", "collection", "hadith_number",
            "number", "rank", "bucket", "score", "similarity", "rating",
            *(c.lower() for c in CONFIG_COLUMNS), "filters", "response", "config"]
    if args.with_results:
        cols.append("results")

    out = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
    w = csv.writer(out)
    w.writerow(cols)
    n = 0
    for row in cur:
        rec = dict(row)
        rec["created_at"], rec["updated_at"] = _iso(rec["created_at"]), _iso(rec["updated_at"])
        config = json.loads(rec["config"] or "{}")
        for c in CONFIG_COLUMNS:
            rec[c.lower()] = config.get(c)
        w.writerow([rec.get(c) for c in cols])
        n += 1
    if args.out:
        out.close()
        print(f"wrote {n} rows to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
