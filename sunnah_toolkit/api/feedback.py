"""Result ratings from signed-in users: POST/GET /v1/feedback (core/feedback.py).

Only the web UI calls these, with the session cookie from api/accounts.py,
so they follow its rules: a POST must come from one of our own pages (Origin,
falling back to Referer), and must be JSON, which a cross-site form cannot
send without a CORS preflight. Off (404) when sign-in is off.

The UI sends the whole result list with every submission. The server keeps
the first copy for the search and only accepts ratings for hadiths in it.
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..core import feedback
from . import accounts, ratelimit

MAX_BODY = 2_000_000
MAX_RESULTS = 10_000
MAX_RATINGS = 500
MAX_QUERY = 1000
MAX_META = 16_000   # JSON bytes each for `filters` and `response`

UI_MODES = ("auto", "natural", "semantic", "keyword", "term")
SEARCH_MODES = ("natural", "semantic", "keyword", "term")
_SEARCH_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
_SLUG = re.compile(r"[a-z0-9_-]{1,40}")
_NUMBER = re.compile(r"[0-9A-Za-z ,/-]{1,32}")   # "402b", "272, 273", "3006-3014", "1697/1698 a"


class _Bad(Exception):
    pass


def _num(v: Any, what: str) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise _Bad(f"{what} must be a number")
    return float(v)


def _result(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise _Bad("each result must be an object")
    coll = item.get("collection")
    if not isinstance(coll, str) or not _SLUG.fullmatch(coll):
        raise _Bad("bad result collection")
    number = item.get("number")
    if number is not None and (isinstance(number, bool) or not isinstance(number, int)):
        raise _Bad("bad result number")
    hn = item.get("hadith_number")
    hn = str(hn).strip() if hn not in (None, "") else (str(number) if number is not None else "")
    if not _NUMBER.fullmatch(hn):
        raise _Bad("bad result hadith_number")
    if item.get("bucket") not in ("strong", "weak"):
        raise _Bad("result bucket must be strong or weak")
    return {"ref": f"{coll}:{hn}", "collection": coll, "hadith_number": hn, "number": number,
            "bucket": item["bucket"], "score": _num(item.get("score"), "score"),
            "similarity": _num(item.get("similarity"), "similarity")}


def _meta(v: Any, kind: type, what: str) -> Any:
    if not isinstance(v, kind):
        raise _Bad(f"{what} must be a JSON {'object' if kind is dict else 'array'}")
    if len(json.dumps(v)) > MAX_META:
        raise _Bad(f"{what} is too large")
    return v


def parse(body: Any) -> tuple[dict[str, Any], dict[str, str | None]]:
    """(search, {ref: rating or None}) from a submission, or raises _Bad."""
    if not isinstance(body, dict):
        raise _Bad("expected a JSON object")
    sid = body.get("search_id")
    if not isinstance(sid, str) or not _SEARCH_ID.fullmatch(sid):
        raise _Bad("bad search_id")
    query = body.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY:
        raise _Bad(f"query must be 1 to {MAX_QUERY} characters")
    if body.get("ui_mode") not in UI_MODES or body.get("mode") not in SEARCH_MODES:
        raise _Bad("bad mode")
    colls = body.get("collections")
    if colls is not None and not (isinstance(colls, list) and len(colls) <= 50
                                  and all(isinstance(c, str) and _SLUG.fullmatch(c) for c in colls)):
        raise _Bad("bad collections")
    limit = body.get("limit")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50000):
        raise _Bad("bad limit")
    raw_results = body.get("results")
    if not isinstance(raw_results, list) or not raw_results or len(raw_results) > MAX_RESULTS:
        raise _Bad(f"results must list 1 to {MAX_RESULTS} hadiths")
    results, seen = [], set()
    for item in raw_results:
        r = _result(item)
        if r["ref"] not in seen:
            seen.add(r["ref"])
            results.append(r)
    raw_ratings = body.get("ratings")
    if not isinstance(raw_ratings, list) or not raw_ratings or len(raw_ratings) > MAX_RATINGS:
        raise _Bad(f"ratings must list 1 to {MAX_RATINGS} hadiths")
    ratings: dict[str, str | None] = {}
    for item in raw_ratings:
        if not isinstance(item, dict) or not isinstance(item.get("ref"), str) or len(item["ref"]) > 80:
            raise _Bad("each rating needs a ref")
        if item.get("rating") is not None and item.get("rating") not in feedback.RATINGS:
            raise _Bad(f"rating must be one of {', '.join(feedback.RATINGS)}, or null to clear")
        ratings[item["ref"]] = item.get("rating")
    search = {
        "search_id": sid, "query": query, "ui_mode": body["ui_mode"], "mode": body["mode"],
        "collections": colls, "limit": limit,
        "filters": _meta(body.get("filters") or {}, dict, "filters"),
        "response": _meta(body.get("response") or [], list, "response"),
        "results": results,
    }
    return search, ratings


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1", include_in_schema=False)
    cfg = accounts.config_from_env()
    raw = os.environ.get("RATE_LIMIT_FEEDBACK_PER_MIN", "").strip()
    per_user = ratelimit.SlidingWindow(int(raw) if raw.isdigit() and int(raw) > 0 else 30, window=60)

    def err(status: int, detail: str) -> JSONResponse:
        return JSONResponse({"detail": detail}, status_code=status, headers={"Cache-Control": "no-store"})

    def signed_in(request: Request) -> dict | None:
        found = accounts.current_session(request, cfg)
        return found[0] if found else None

    @router.post("/feedback")
    async def submit(request: Request) -> JSONResponse:
        if cfg is None:
            return err(404, "Sign-in is not enabled.")
        if not accounts.same_origin(request, cfg):
            return err(403, "Request blocked: not sent from this site.")
        user = signed_in(request)
        if user is None:
            return err(401, "Sign in to rate results.")
        if wait := per_user.hit(f"user:{user['id']}"):
            r = err(429, f"Too many requests. Try again in {wait} seconds.")
            r.headers["Retry-After"] = str(wait)
            return r
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return err(415, "Send JSON.")
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_BODY:
            return err(413, "Too large.")
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY:
                return err(413, "Too large.")
        try:
            search, ratings = parse(json.loads(body))
        except (ValueError, _Bad) as e:
            return err(422, str(e) if isinstance(e, _Bad) else "Invalid JSON.")
        try:
            saved = feedback.save(user["id"], search, ratings)
        except feedback.Invalid as e:
            return err(422, str(e))
        return JSONResponse({"search_id": search["search_id"], "ratings": saved},
                            headers={"Cache-Control": "no-store"})

    @router.get("/feedback")
    def fetch(request: Request, search_id: str = "") -> JSONResponse:
        if cfg is None:
            return err(404, "Sign-in is not enabled.")
        user = signed_in(request)
        if user is None:
            return err(401, "Sign in to rate results.")
        if not _SEARCH_ID.fullmatch(search_id):
            return err(422, "bad search_id")
        return JSONResponse({"search_id": search_id, "ratings": feedback.ratings_for(user["id"], search_id)},
                            headers={"Cache-Control": "no-store"})

    return router
