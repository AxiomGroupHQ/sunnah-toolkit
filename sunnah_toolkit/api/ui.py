"""Single-page search UI: Sunnah Semantic Search.

Served at `GET /` (registered before the MCP mount in app.py). The page is one
self-contained HTML document: no framework, no build step, no static assets
beyond the Amiri web font. Same-origin XHR to /v1/... means no CORS or auth
dance for the user.

URL state: `?q=...&mode=...&c=slug,slug` re-runs a search, `?h=slug:number`
opens one hadith. Both work with Back/Forward and can be shared.
"""

from __future__ import annotations

from fastapi.responses import HTMLResponse


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sunnah Semantic Search</title>
  <meta name="description" content="Search 44,896 hadiths across 15 classical collections by meaning. Ask in plain English, type an Arabic word, or look up a reference.">
  <meta property="og:title" content="Sunnah Semantic Search">
  <meta property="og:description" content="Search 44,896 hadiths across 15 classical collections by meaning.">
  <meta property="og:type" content="website">
  <meta name="theme-color" content="#0f7058">
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%230f7058'/%3E%3Cpath d='M14 20c7-3 13-2 18 2 5-4 11-5 18-2v26c-7-3-13-2-18 2-5-4-11-5-18-2z' fill='none' stroke='white' stroke-width='3.5' stroke-linejoin='round'/%3E%3Cpath d='M32 22v26' stroke='white' stroke-width='3.5'/%3E%3C/svg%3E">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Amiri:wght@400;700&display=swap" rel="stylesheet">
  <style>
    * { box-sizing: border-box; }
    :root {
      --bg: #faf9f5;
      --card: #ffffff;
      --text: #1c1c1c;
      --muted: #6b6b6b;
      --faint: #9a988f;
      --accent: #0f7058;
      --accent-hover: #0a5644;
      --accent-soft: #e6f2ee;
      --border: #e5e3da;
      --soft: #f3f1e8;
      --mark: #fbecb4;
      --g-sahih: #0f7058;   --g-sahih-bg: #e3f3ec;
      --g-hsahih: #1d6f8a;  --g-hsahih-bg: #e2f0f5;
      --g-hasan: #3a5ba8;   --g-hasan-bg: #e8edf8;
      --g-daif: #9a6510;    --g-daif-bg: #fbf0dc;
      --g-maudu: #a83228;   --g-maudu-bg: #fbe7e4;
      --g-none: #6b6b6b;    --g-none-bg: #efeee8;
      --error: #b03a2e;
      color-scheme: light;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #141513;
        --card: #1d1e1b;
        --text: #ecebe6;
        --muted: #a5a39b;
        --faint: #77756e;
        --accent: #3fb68f;
        --accent-hover: #5cc9a3;
        --accent-soft: #1d3029;
        --border: #2f302c;
        --soft: #252622;
        --mark: #5a4b16;
        --g-sahih: #5cc9a3;   --g-sahih-bg: #183128;
        --g-hsahih: #6cc0dc;  --g-hsahih-bg: #172b33;
        --g-hasan: #93aef0;   --g-hasan-bg: #1e2538;
        --g-daif: #e0b25f;    --g-daif-bg: #33280f;
        --g-maudu: #f08a7e;   --g-maudu-bg: #3a1c18;
        --g-none: #a5a39b;    --g-none-bg: #2a2b27;
        --error: #f08a7e;
        color-scheme: dark;
      }
    }
    html, body { margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.55;
      padding: 0 16px;
      -webkit-text-size-adjust: 100%;
    }
    main { max-width: 760px; margin: 0 auto; padding: 3.5rem 0 2rem; }
    body.has-results main { padding-top: 1.5rem; }
    button { font-family: inherit; }
    a { color: var(--accent); }

    /* Header */
    .brand {
      display: inline-flex; align-items: center; gap: 0.6rem;
      color: var(--text); text-decoration: none;
    }
    .brand svg { width: 34px; height: 34px; flex-shrink: 0; }
    h1 { margin: 0; font-size: 1.6rem; font-weight: 700; letter-spacing: -0.01em; }
    .sub { color: var(--muted); margin: 0.5rem 0 0; font-size: 1rem; }
    body.has-results h1 { font-size: 1.25rem; }
    body.has-results .brand svg { width: 28px; height: 28px; }
    body.has-results .sub { display: none; }
    header { margin-bottom: 1.4rem; }
    .topline { display: flex; justify-content: space-between; align-items: center; gap: 0.75rem; }

    /* Account (optional sign-in, api/accounts.py) */
    .account { position: relative; flex-shrink: 0; font-size: 0.88rem; }
    .account[hidden] { display: none; }
    .account summary { list-style: none; max-width: 14rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .account summary::-webkit-details-marker { display: none; }
    .account a.link-btn { text-decoration: none; }
    .account-menu {
      position: absolute; right: 0; top: calc(100% + 0.35rem); z-index: 10; min-width: 13rem;
      background: var(--card); border: 1px solid var(--border); border-radius: 10px;
      box-shadow: 0 4px 16px rgba(0,0,0,0.08); padding: 0.35rem;
    }
    .account-menu .who { padding: 0.4rem 0.55rem 0.5rem; color: var(--muted); font-size: 0.82rem;
      border-bottom: 1px solid var(--border); margin-bottom: 0.25rem; overflow-wrap: anywhere; }
    .account-menu .who strong { display: block; color: var(--text); font-size: 0.88rem; }
    .account-menu a, .account-menu button {
      display: block; width: 100%; text-align: left; background: none; border: none; color: var(--text);
      font-size: 0.88rem; padding: 0.4rem 0.55rem; border-radius: 6px; cursor: pointer; text-decoration: none;
    }
    .account-menu a:hover, .account-menu button:hover { background: var(--soft); color: var(--accent); }
    .account-menu form { margin: 0; }
    body.has-results header { margin-bottom: 1rem; }

    /* Search bar */
    .searchbar {
      display: flex; align-items: center; gap: 0.25rem;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 0.35rem 0.35rem 0.35rem 0.9rem;
      box-shadow: 0 1px 2px rgba(0,0,0,0.04), 0 4px 16px rgba(0,0,0,0.04);
      transition: border-color 0.15s, box-shadow 0.15s;
    }
    .searchbar:focus-within { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
    .searchbar .icon { width: 20px; height: 20px; color: var(--faint); flex-shrink: 0; }
    #q {
      flex: 1; min-width: 0;
      border: none; outline: none; background: transparent; color: var(--text);
      font-size: 1.05rem; padding: 0.6rem 0.4rem; font-family: inherit;
    }
    #q::-webkit-search-cancel-button { cursor: pointer; }
    #search-btn {
      background: var(--accent); color: #fff; border: none; border-radius: 10px;
      padding: 0.65rem 1.15rem; font-size: 0.98rem; font-weight: 600; cursor: pointer;
      display: inline-flex; align-items: center; gap: 0.45rem; flex-shrink: 0;
    }
    #search-btn:hover { background: var(--accent-hover); }
    #search-btn:disabled { opacity: 0.7; cursor: progress; }
    .spinner {
      width: 14px; height: 14px; border-radius: 50%;
      border: 2px solid rgba(255,255,255,0.45); border-top-color: #fff;
      animation: spin 0.7s linear infinite; display: none;
    }
    #search-btn.loading .spinner { display: inline-block; }
    @keyframes spin { to { transform: rotate(360deg); } }

    .subbar {
      display: flex; justify-content: space-between; align-items: flex-start;
      gap: 0.75rem; margin-top: 0.7rem; flex-wrap: wrap;
    }
    .examples { color: var(--muted); font-size: 0.88rem; display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center; }
    body.has-results .examples { display: none; }
    .ex {
      background: var(--card); border: 1px solid var(--border); color: var(--text);
      border-radius: 999px; padding: 0.22rem 0.7rem; font-size: 0.85rem; cursor: pointer;
    }
    .ex:hover { border-color: var(--accent); color: var(--accent); }
    .links { display: flex; gap: 0.25rem; margin-left: auto; }
    .link-btn {
      background: none; border: none; color: var(--muted); cursor: pointer;
      font-size: 0.88rem; padding: 0.25rem 0.5rem; border-radius: 6px;
      display: inline-flex; align-items: center; gap: 0.3rem;
    }
    .link-btn:hover { color: var(--accent); background: var(--soft); }
    .link-btn[aria-expanded="true"] { color: var(--accent); }
    .link-btn .chev { transition: transform 0.15s; font-size: 0.7rem; }
    .link-btn[aria-expanded="true"] .chev { transform: rotate(180deg); }
    .adv-dot {
      width: 6px; height: 6px; border-radius: 50%; background: var(--accent);
      display: none;
    }
    .adv-dot.on { display: inline-block; }

    /* Advanced panel */
    .adv {
      margin-top: 0.7rem; background: var(--card); border: 1px solid var(--border);
      border-radius: 12px; padding: 1rem;
    }
    .adv[hidden] { display: none; }
    .adv-section + .adv-section { margin-top: 1rem; padding-top: 1rem; border-top: 1px solid var(--border); }
    .adv-head {
      display: flex; justify-content: space-between; align-items: baseline; gap: 0.5rem;
      font-size: 0.8rem; font-weight: 600; color: var(--muted);
      text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 0.55rem;
    }
    .adv-head .count { text-transform: none; letter-spacing: 0; font-weight: 500; }
    .seg { display: flex; flex-wrap: wrap; gap: 0.35rem; }
    .seg label { cursor: pointer; }
    .seg input { position: absolute; opacity: 0; pointer-events: none; }
    .seg span {
      display: inline-block; padding: 0.35rem 0.8rem; border-radius: 999px;
      border: 1px solid var(--border); font-size: 0.88rem; background: var(--card);
    }
    .seg label:hover span { border-color: var(--accent); }
    .seg input:checked + span { background: var(--accent); border-color: var(--accent); color: #fff; }
    .seg input:focus-visible + span { outline: 2px solid var(--accent); outline-offset: 2px; }
    .mode-hint { color: var(--muted); font-size: 0.86rem; margin: 0.55rem 0 0; }
    .mini-btns { display: inline-flex; gap: 0.25rem; }
    .mini-btns button {
      background: none; border: 1px solid var(--border); color: var(--accent);
      border-radius: 999px; padding: 0.1rem 0.6rem; font-size: 0.78rem; cursor: pointer;
      text-transform: none; letter-spacing: 0;
    }
    .mini-btns button:hover { background: var(--soft); }
    #coll-checkboxes {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 0.1rem 0.5rem;
    }
    .coll-item {
      display: flex; align-items: center; gap: 0.5rem; padding: 0.3rem 0.4rem;
      cursor: pointer; border-radius: 6px; font-size: 0.9rem; min-width: 0;
    }
    .coll-item:hover { background: var(--soft); }
    .coll-item input { margin: 0; flex-shrink: 0; accent-color: var(--accent); }
    .coll-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .coll-hcount { color: var(--faint); font-size: 0.78rem; }
    .toggle { display: inline-flex; align-items: center; gap: 0.5rem; font-size: 0.9rem; cursor: pointer; }
    .toggle input { accent-color: var(--accent); margin: 0; }

    /* Status */
    #status { color: var(--muted); padding: 1.1rem 0.15rem 0.6rem; font-size: 0.92rem; }
    #status.error { color: var(--error); }
    #status[hidden] { display: none; }

    /* Result filters */
    .chips {
      display: flex; flex-wrap: wrap; gap: 0.35rem; align-items: center;
      margin-bottom: 0.8rem; font-size: 0.85rem; color: var(--muted);
    }
    .chips .chips-label { margin-right: 0.15rem; }
    .chip {
      display: inline-flex; align-items: center; gap: 0.35rem;
      padding: 0.2rem 0.65rem; border-radius: 999px; cursor: pointer;
      border: 1px solid var(--border); background: var(--card); color: var(--text); font-size: 0.84rem;
    }
    .chip input { position: absolute; opacity: 0; pointer-events: none; }
    .chip:has(input:checked) { border-color: var(--accent); background: var(--accent-soft); }
    .chip:has(input:not(:checked)) { color: var(--faint); }
    .chip:has(input:focus-visible) { outline: 2px solid var(--accent); outline-offset: 2px; }
    .chip.more { color: var(--accent); background: none; border-style: dashed; }
    .chip .n { color: var(--faint); font-size: 0.78rem; }
    .chip .ar { font-family: "Amiri", "Scheherazade New", serif; font-size: 1.05rem; line-height: 1; }

    /* Result rows */
    .row {
      background: var(--card); border: 1px solid var(--border); border-radius: 12px;
      margin-bottom: 0.6rem; transition: border-color 0.12s;
    }
    .row:hover { border-color: var(--accent); }
    .row.open { border-color: var(--accent); }
    .row-main { padding: 0.85rem 1rem; cursor: pointer; display: block; width: 100%; text-align: left;
      background: none; border: none; color: inherit; font: inherit; border-radius: 12px; }
    .row-main:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
    .row-head { display: flex; justify-content: space-between; align-items: center; gap: 0.6rem; }
    .ref { font-weight: 650; color: var(--accent); font-size: 0.97rem; }
    .badges { display: inline-flex; gap: 0.3rem; align-items: center; flex-shrink: 0; }
    .chapter { display: block; color: var(--muted); font-size: 0.85rem; margin-top: 0.1rem; }
    .chapter.clamp { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .row.open .chapter.clamp { white-space: normal; }
    .snippet { display: block; color: var(--text); opacity: 0.85; font-size: 0.93rem; margin: 0.35rem 0 0; overflow-wrap: anywhere; }
    .row.open .snippet { display: none; }
    mark { background: var(--mark); color: inherit; border-radius: 3px; padding: 0 1px; }
    .score { font-size: 0.75rem; color: var(--faint); font-variant-numeric: tabular-nums; }
    .row-detail { padding: 0 1rem 1rem; }
    .divider {
      display: flex; align-items: center; gap: 0.6rem; color: var(--muted);
      font-size: 0.82rem; margin: 1.1rem 0 0.6rem;
    }
    .divider::after { content: ""; flex: 1; border-top: 1px dashed var(--border); }
    .more-btn {
      display: block; width: 100%; margin: 0.4rem 0 0; padding: 0.6rem;
      background: none; border: 1px dashed var(--border); border-radius: 10px;
      color: var(--muted); cursor: pointer; font-size: 0.9rem;
    }
    .more-btn:hover { border-color: var(--accent); color: var(--accent); }

    /* Grade badges */
    .badge {
      display: inline-block; font-size: 0.72rem; font-weight: 650; letter-spacing: 0.02em;
      padding: 0.12rem 0.5rem; border-radius: 999px; white-space: nowrap;
      color: var(--g-none); background: var(--g-none-bg);
    }
    .badge.sahih { color: var(--g-sahih); background: var(--g-sahih-bg); }
    .badge.hasan_sahih { color: var(--g-hsahih); background: var(--g-hsahih-bg); }
    .badge.hasan { color: var(--g-hasan); background: var(--g-hasan-bg); }
    .badge.daif { color: var(--g-daif); background: var(--g-daif-bg); }
    .badge.maudu { color: var(--g-maudu); background: var(--g-maudu-bg); }
    .badge.outline { background: transparent; border: 1px solid var(--border); color: var(--muted); font-weight: 550; }

    /* Full hadith */
    .hadith-card {
      background: var(--card); border: 1px solid var(--border); border-radius: 14px;
      padding: 1.25rem; margin-top: 1rem;
    }
    .hadith-card .ref { font-size: 1.1rem; }
    .hadith-card .chapter { margin-bottom: 0.2rem; }
    .h-narrator { font-style: italic; color: var(--muted); margin: 0.8rem 0 0.4rem; font-size: 0.95rem; }
    .h-english { white-space: pre-wrap; margin: 0.4rem 0 0; overflow-wrap: anywhere; }
    .h-arabic {
      direction: rtl; text-align: right;
      font-family: "Amiri", "Scheherazade New", "Traditional Arabic", serif;
      font-size: 1.35rem; line-height: 2.05; margin-top: 1rem; padding-top: 0.9rem;
      border-top: 1px solid var(--border);
    }
    .h-meta {
      margin-top: 1rem; padding-top: 0.8rem; border-top: 1px solid var(--border);
      display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; align-items: center;
      justify-content: space-between; font-size: 0.86rem; color: var(--muted);
    }
    .h-grade { display: inline-flex; align-items: center; gap: 0.45rem; flex-wrap: wrap; }
    .h-actions { display: inline-flex; gap: 0.35rem; flex-wrap: wrap; }
    .act {
      background: var(--soft); border: 1px solid var(--border); color: var(--text);
      border-radius: 8px; padding: 0.3rem 0.7rem; font-size: 0.84rem; cursor: pointer;
      text-decoration: none; display: inline-flex; align-items: center; gap: 0.3rem;
    }
    .act:hover { border-color: var(--accent); color: var(--accent); }
    .loading-line { color: var(--muted); font-size: 0.9rem; padding: 0.2rem 0 0.4rem; }

    /* Pagination */
    .page-nav { display: flex; gap: 0.3rem; justify-content: center; align-items: center; margin: 1.2rem 0 0.5rem; flex-wrap: wrap; }
    .pn-btn {
      padding: 0.4rem 0.7rem; font-size: 0.9rem; background: var(--card);
      border: 1px solid var(--border); color: var(--text); border-radius: 8px;
      min-width: 2.4rem; cursor: pointer;
    }
    .pn-btn:hover:not(:disabled) { border-color: var(--accent); }
    .pn-btn.pn-current { background: var(--accent); color: #fff; border-color: var(--accent); cursor: default; }
    .pn-btn:disabled:not(.pn-current) { opacity: 0.4; cursor: not-allowed; }
    .pn-ellipsis { color: var(--muted); padding: 0 0.2rem; }

    footer { margin-top: 3rem; text-align: center; color: var(--faint); font-size: 0.8rem; line-height: 1.6; }
    footer a { color: var(--muted); }
    kbd {
      font-family: inherit; font-size: 0.75rem; border: 1px solid var(--border);
      border-bottom-width: 2px; border-radius: 4px; padding: 0 0.3rem; background: var(--card);
    }

    @media (max-width: 520px) {
      main { padding-top: 2rem; }
      h1 { font-size: 1.35rem; }
      .sub { font-size: 0.93rem; }
      #search-btn .label { display: none; }
      #search-btn { padding: 0.65rem 0.8rem; }
      .links { margin-left: 0; width: 100%; justify-content: space-between; }
      .row-main { padding: 0.8rem 0.85rem; }
      .row-detail { padding: 0 0.85rem 0.9rem; }
      .hadith-card { padding: 1rem; }
      .h-meta { flex-direction: column; align-items: flex-start; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div class="topline">
        <a class="brand" href="/" id="home-link">
          <svg viewBox="0 0 64 64" aria-hidden="true"><rect width="64" height="64" rx="14" fill="#0f7058"/><path d="M14 20c7-3 13-2 18 2 5-4 11-5 18-2v26c-7-3-13-2-18 2-5-4-11-5-18-2z" fill="none" stroke="#fff" stroke-width="3.5" stroke-linejoin="round"/><path d="M32 22v26" stroke="#fff" stroke-width="3.5"/></svg>
          <h1>Sunnah Semantic Search</h1>
        </a>
        <div class="account" id="account" hidden></div>
      </div>
      <p class="sub">Search 44,896 hadiths across 15 classical collections by meaning. Ask a question in plain English, type an Arabic word, or look up a reference like <em>Bukhari 1</em>.</p>
    </header>

    <form id="f" role="search">
      <div class="searchbar">
        <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
        <input type="search" id="q" name="q" placeholder="Ask a question or search a topic" autocomplete="off" autofocus aria-label="Search hadiths" enterkeyhint="search">
        <button type="submit" id="search-btn"><span class="spinner" aria-hidden="true"></span><span class="label">Search</span><svg class="go" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg></button>
      </div>

      <div class="subbar">
        <div class="examples" id="examples">
          <span>Try</span>
          <button type="button" class="ex">dua before sleep</button>
          <button type="button" class="ex">controlling anger</button>
          <button type="button" class="ex">can I pray with my shoes on?</button>
          <button type="button" class="ex">قنوت</button>
          <button type="button" class="ex">Bukhari 1</button>
        </div>
        <div class="links">
          <button type="button" class="link-btn" id="adv-toggle" aria-expanded="false" aria-controls="adv">
            <span class="adv-dot" id="adv-dot" title="Advanced options are active"></span>Advanced search <span class="chev">&#9662;</span>
          </button>
          <button type="button" class="link-btn" id="random-btn">Random hadith</button>
        </div>
      </div>

      <div class="adv" id="adv" hidden>
        <div class="adv-section">
          <div class="adv-head"><span>Search by</span></div>
          <div class="seg" role="radiogroup" aria-label="Search mode">
            <label><input type="radio" name="m" value="auto" checked><span>Auto</span></label>
            <label><input type="radio" name="m" value="natural"><span>Question</span></label>
            <label><input type="radio" name="m" value="semantic"><span>Concept</span></label>
            <label><input type="radio" name="m" value="keyword"><span>Exact words</span></label>
            <label><input type="radio" name="m" value="term"><span>Arabic term</span></label>
            <label><input type="radio" name="m" value="reference"><span>Reference</span></label>
          </div>
          <p class="mode-hint" id="mode-hint"></p>
        </div>
        <div class="adv-section">
          <div class="adv-head">
            <span>Collections <span class="count" id="coll-count"></span></span>
            <span class="mini-btns"><button type="button" id="coll-all">All</button><button type="button" id="coll-none">None</button></span>
          </div>
          <div id="coll-checkboxes"></div>
        </div>
        <div class="adv-section">
          <label class="toggle"><input type="checkbox" id="show-scores"> Show relevance scores</label>
        </div>
      </div>
    </form>

    <div id="status" hidden aria-live="polite"></div>
    <section id="results">
      <div id="results-header"></div>
      <div id="results-rows"></div>
      <div id="page-nav"></div>
    </section>
    <section id="detail"></section>

    <footer>
      <p>Hadith text and grades from <a href="https://sunnah.com" target="_blank" rel="noopener">sunnah.com</a>. Results are ordered by grade, then relevance.<br>
      Press <kbd>/</kbd> to search. For rulings, consult a qualified scholar.<br>
      Searches are stored anonymously to improve results. <a href="/privacy">Privacy</a></p>
    </footer>
  </main>

  <script>
    const $ = (id) => document.getElementById(id);
    const form = $("f");
    const qIn = $("q");
    const statusEl = $("status");
    const resultsHeaderEl = $("results-header");
    const resultsRowsEl = $("results-rows");
    const pageNavEl = $("page-nav");
    const detailEl = $("detail");
    const searchBtn = $("search-btn");
    const randomBtn = $("random-btn");
    const advBtn = $("adv-toggle");
    const advEl = $("adv");
    const advDot = $("adv-dot");
    const modeHintEl = $("mode-hint");
    const collCountEl = $("coll-count");
    const collBoxEl = $("coll-checkboxes");
    const showScoresEl = $("show-scores");

    const PAGE_SIZE = 10;
    const ALL_LIMIT = 50000;

    const MODE_HINTS = {
      auto: "Picks the search for what you type: a question or topic, an Arabic word, or a reference like Bukhari 1.",
      natural: "Ask in plain English, e.g. what did the Prophet say about anger?",
      semantic: "Match by meaning, e.g. kindness to neighbours.",
      keyword: "Exact English words, e.g. intentions.",
      term: "An Arabic word, in Arabic script or transliterated, e.g. qunut, ramazan.",
      reference: "Go straight to a hadith, e.g. Bukhari 1, Sahih Muslim 5.",
    };
    const MODE_NAMES = { natural: "question", semantic: "concept", keyword: "exact words", term: "Arabic term", reference: "reference" };

    const GRADE_LABELS = {
      sahih: "Sahih", hasan_sahih: "Hasan Sahih", hasan: "Hasan",
      daif: "Da’if", maudu: "Fabricated", ungraded: "Ungraded",
    };

    // Collection lookup tables built from /v1/collections on load.
    const collections = {};
    const slugByAlias = {};
    const selectedCollections = new Set();
    let collectionsReady = null;

    // Last search state, cached so the chip filters can re-render without
    // re-fetching.
    let lastResults = [];
    let lastResultsWeak = [];
    let weakVisible = false;
    let lastMode = null;
    let lastQuery = "";
    let lastMatchedWords = [];
    let selectedWords = new Set();
    let selectedResultCollections = new Set();
    let currentPage = 1;
    let showScores = false;
    const openRows = new Set();      // "slug|num" keys of expanded rows

    const CANONICAL_ORDER = ["bukhari", "muslim", "abudawud", "tirmidhi", "nasai", "ibnmajah"];
    const NAME_PREFIXES = ["sahih", "sunan", "jami", "musnad", "al", "an", "ar", "as", "at", "az", "ad"];
    const STOPWORDS = new Set(("a an and are as at be by can did do does for from has have how i if in is it " +
      "its me my of on or say said should that the their them they this to was what when where which who why " +
      "will with you your about any prophet messenger allah hadith hadiths").split(" "));

    function safeStorage(fn, fallback) { try { return fn(); } catch (_) { return fallback; } }

    function norm(s) {
      return (s || "").toLowerCase().replace(/[‘’']/g, "").replace(/[-_.]/g, "").replace(/\s+/g, "");
    }

    function indexAlias(name, slug) {
      let n = norm(name);
      if (!n) return;
      slugByAlias[n] = slug;
      let changed = true;
      while (changed) {
        changed = false;
        for (const p of NAME_PREFIXES) {
          if (n.startsWith(p) && n.length > p.length) {
            n = n.slice(p.length);
            slugByAlias[n] = slug;
            changed = true;
          }
        }
      }
    }

    async function loadCollections() {
      try {
        const r = await fetch("/v1/collections");
        if (!r.ok) throw new Error("HTTP " + r.status);
        const j = await r.json();
        for (const c of (j.collections || [])) {
          collections[c.slug] = c;
          indexAlias(c.slug, c.slug);
          indexAlias(c.english_title, c.slug);
        }
        slugByAlias[norm("nawawi")] = "forty";
        slugByAlias[norm("nawawi40")] = "forty";
        slugByAlias[norm("riyadussaliheen")] = "riyadussalihin";
        renderCollectionCheckboxes();
      } catch (e) {
        setStatus("Could not load collections: " + e.message, true);
      }
    }

    function renderCollectionCheckboxes() {
      const inCanonical = CANONICAL_ORDER.filter((s) => collections[s]).map((s) => collections[s]);
      const seen = new Set(inCanonical.map((c) => c.slug));
      const rest = Object.values(collections)
        .filter((c) => !seen.has(c.slug))
        .sort((a, b) => a.english_title.localeCompare(b.english_title));
      const ordered = inCanonical.concat(rest);
      collBoxEl.innerHTML = ordered.map((c) =>
        '<label class="coll-item">' +
          '<input type="checkbox" value="' + escapeHtml(c.slug) + '" checked>' +
          '<span class="coll-name">' + escapeHtml(c.english_title) + '</span>' +
          '<span class="coll-hcount">' + (c.hadith_count || 0).toLocaleString() + '</span>' +
        '</label>'
      ).join("");
      for (const c of ordered) selectedCollections.add(c.slug);
      updateCollCount();
      collBoxEl.addEventListener("change", syncCollectionsFromBoxes);
    }

    function syncCollectionsFromBoxes() {
      selectedCollections.clear();
      collBoxEl.querySelectorAll("input:checked").forEach((i) => selectedCollections.add(i.value));
      updateCollCount();
    }

    function setCollections(slugs) {
      const want = slugs ? new Set(slugs) : null;
      collBoxEl.querySelectorAll("input").forEach((i) => { i.checked = !want || want.has(i.value); });
      syncCollectionsFromBoxes();
    }

    function updateCollCount() {
      const total = Object.keys(collections).length;
      collCountEl.textContent = "(" + selectedCollections.size + " of " + total + ")";
      updateAdvDot();
    }

    $("coll-all").addEventListener("click", () => setCollections(null));
    $("coll-none").addEventListener("click", () => setCollections([]));

    function currentMode() {
      return document.querySelector('input[name="m"]:checked').value;
    }
    function setMode(mode) {
      const el = document.querySelector('input[name="m"][value="' + mode + '"]');
      (el || document.querySelector('input[name="m"][value="auto"]')).checked = true;
      modeHintEl.textContent = MODE_HINTS[currentMode()];
      updateAdvDot();
    }
    document.querySelectorAll('input[name="m"]').forEach((r) =>
      r.addEventListener("change", () => setMode(r.value)));

    // A dot on "Advanced search" when anything in it differs from the default,
    // so a hidden filter never silently changes the results.
    function updateAdvDot() {
      const on = currentMode() !== "auto" || isCollectionFilterActive();
      advDot.classList.toggle("on", on);
    }

    function setAdvOpen(open) {
      advEl.hidden = !open;
      advBtn.setAttribute("aria-expanded", String(open));
    }
    advBtn.addEventListener("click", () => setAdvOpen(advEl.hidden));

    showScores = safeStorage(() => localStorage.getItem("showScores") === "1", false);
    showScoresEl.checked = showScores;
    showScoresEl.addEventListener("change", () => {
      showScores = showScoresEl.checked;
      safeStorage(() => localStorage.setItem("showScores", showScores ? "1" : "0"));
      if (lastResults.length || lastResultsWeak.length) renderResultsView();
    });

    function setStatus(msg, isError = false) {
      if (!msg) { statusEl.hidden = true; statusEl.textContent = ""; return; }
      statusEl.textContent = msg;
      statusEl.classList.toggle("error", !!isError);
      statusEl.hidden = false;
    }

    function setLoading(on) {
      searchBtn.disabled = on;
      searchBtn.classList.toggle("loading", on);
      searchBtn.querySelector(".go").style.display = on ? "none" : "";
    }

    function clearAll() {
      resultsHeaderEl.innerHTML = "";
      resultsRowsEl.innerHTML = "";
      pageNavEl.innerHTML = "";
      detailEl.innerHTML = "";
      lastResults = [];
      lastResultsWeak = [];
      weakVisible = false;
      lastMode = null;
      lastMatchedWords = [];
      selectedWords = new Set();
      selectedResultCollections = new Set();
      chipsExpanded = false;
      openRows.clear();
      currentPage = 1;
    }

    function escapeHtml(s) {
      return (s == null ? "" : String(s)).replace(/[&<>"']/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[c]));
    }

    function cleanArabic(s) {
      if (!s) return "";
      return s
        .replace(/\[narrator[^\]]*\]([\s\S]*?)\[\/narrator\]/g, "$1")
        .replace(/\[\/?[a-z]+\]/g, "")
        .replace(/[‎‏]/g, "")
        .trim();
    }

    function cleanText(s) {
      if (!s) return "";
      // The source hard-wraps lines; keep paragraph breaks, join the rest.
      return s.replace(/<[^>]+>/g, " ").replace(/\r/g, "")
        .replace(/([^\n])\n(?!\n)/g, "$1 ").replace(/[ \t]+/g, " ")
        .replace(/\n{3,}/g, "\n\n").trim();
    }

    function titleFor(slug) {
      return (collections[slug] && collections[slug].english_title) || slug;
    }

    function displayNumber(hadithNumber, number) {
      // sunnah.com squashes whitespace in suffixed numbers ("375 a" -> "375a")
      // and uses the first part of a paired range like "272, 273".
      return String(hadithNumber || number).split(",", 1)[0].replace(/\s+/g, "");
    }

    function refLabel(slug, hadithNumber, number) {
      return titleFor(slug) + " " + displayNumber(hadithNumber, number);
    }

    function referenceUrl(slug, hadithNumber, number) {
      return "https://sunnah.com/" + encodeURIComponent(slug) + ":" +
        encodeURIComponent(displayNumber(hadithNumber, number));
    }

    function permalink(slug, num) {
      return location.origin + "/?h=" + encodeURIComponent(slug + ":" + num);
    }

    // Query words worth highlighting: 3+ letters, not a stopword.
    function highlightTerms(q) {
      return Array.from(new Set(
        (q || "").toLowerCase().split(/[^\p{L}\p{N}]+/u)
          .filter((w) => w.length >= 3 && !STOPWORDS.has(w))
      ));
    }

    // Escape, then wrap whole-word-prefix matches in <mark>. Splitting the raw
    // text first means a match can never land inside an HTML entity.
    function highlight(text, terms) {
      if (!terms.length) return escapeHtml(text);
      const esc = terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
      const re = new RegExp("(?<![\\p{L}\\p{N}])(" + esc.join("|") + ")[\\p{L}]*", "giu");
      let out = "", last = 0, m;
      while ((m = re.exec(text)) !== null) {
        out += escapeHtml(text.slice(last, m.index)) + "<mark>" + escapeHtml(m[0]) + "</mark>";
        last = m.index + m[0].length;
      }
      return out + escapeHtml(text.slice(last));
    }

    function gradeBadges(item) {
      const cls = item.grade_class || "ungraded";
      const raw = item.grade_display || item.english_grade || "";
      const tip = raw || "No grade recorded for this hadith";
      let html = '<span class="badge ' + escapeHtml(cls) + '" title="' + escapeHtml(tip) + '">' +
        escapeHtml(GRADE_LABELS[cls] || "Ungraded") + '</span>';
      if (/mauq[uū]f/i.test(raw)) {
        html += '<span class="badge outline" title="A saying of a Companion, not of the Prophet">Mauquf</span>';
      } else if (/maqt[uū]/i.test(raw)) {
        html += '<span class="badge outline" title="A saying of a Successor, not of the Prophet">Maqtu’</span>';
      }
      return html;
    }

    function hadithBodyHtml(h, terms) {
      const slug = h.collection;
      const num = h.hadith_number || h.number;
      const url = referenceUrl(slug, h.hadith_number, h.number);
      const arabic = cleanArabic(h.arabic);
      const english = cleanText(h.english_text);
      const grade = h.grade_display || "";
      return (
        (h.narrator ? '<p class="h-narrator">' + escapeHtml(cleanText(h.narrator)) + '</p>' : '') +
        (english ? '<p class="h-english">' + highlight(english, terms || []) + '</p>' : '') +
        (arabic ? '<div class="h-arabic" lang="ar">' + escapeHtml(arabic) + '</div>' : '') +
        '<div class="h-meta">' +
          '<span class="h-grade">' + gradeBadges(h) +
            (!grade ? '<span>No grade recorded</span>'
              : grade.toLowerCase() === (GRADE_LABELS[h.grade_class] || "").toLowerCase() ? ''
              : '<span>' + escapeHtml(grade) + '</span>') +
          '</span>' +
          '<span class="h-actions">' +
            '<button type="button" class="act" data-act="copy" data-slug="' + escapeHtml(slug) + '" data-num="' + escapeHtml(String(num)) + '">Copy</button>' +
            '<button type="button" class="act" data-act="share" data-slug="' + escapeHtml(slug) + '" data-num="' + escapeHtml(String(num)) + '">Share link</button>' +
            '<a class="act" href="' + url + '" target="_blank" rel="noopener">sunnah.com &#8599;</a>' +
          '</span>' +
        '</div>'
      );
    }

    function renderHadithCard(h) {
      const label = refLabel(h.collection, h.hadith_number, h.number);
      return (
        '<article class="hadith-card">' +
          '<div class="ref">' + escapeHtml(label) + '</div>' +
          (h.chapter ? '<div class="chapter">' + escapeHtml(h.chapter) + '</div>' : '') +
          hadithBodyHtml(h, []) +
        '</article>'
      );
    }

    // Full hadiths fetched for expanded rows and for copy/share.
    const hadithCache = new Map();
    async function getHadith(slug, num, ctx) {
      const key = slug + "|" + num;
      if (!hadithCache.has(key)) {
        hadithCache.set(key, call("/v1/hadith/" + encodeURIComponent(slug) + "/" + encodeURIComponent(num), ctx)
          .catch((e) => { hadithCache.delete(key); throw e; }));
      }
      return hadithCache.get(key);
    }

    async function copyText(text, btn) {
      try {
        await navigator.clipboard.writeText(text);
      } catch (_) {
        const ta = document.createElement("textarea");
        ta.value = text; document.body.appendChild(ta); ta.select();
        try { document.execCommand("copy"); } catch (_) {}
        ta.remove();
      }
      const old = btn.textContent;
      btn.textContent = "Copied";
      setTimeout(() => { btn.textContent = old; }, 1400);
    }

    // Copy/share buttons live inside rows and cards; one delegated handler.
    document.addEventListener("click", async (e) => {
      const btn = e.target.closest(".act[data-act]");
      if (!btn) return;
      e.stopPropagation();
      const slug = btn.dataset.slug, num = btn.dataset.num;
      if (btn.dataset.act === "share") {
        const link = permalink(slug, num);
        if (navigator.share && matchMedia("(pointer: coarse)").matches) {
          try { await navigator.share({ title: refLabel(slug, num), url: link }); return; } catch (_) {}
        }
        copyText(link, btn);
        return;
      }
      try {
        const h = await getHadith(slug, num);
        const parts = [
          refLabel(slug, h.hadith_number, h.number) + (h.chapter ? " (" + h.chapter + ")" : ""),
          cleanText(h.narrator),
          cleanText(h.english_text),
          cleanArabic(h.arabic),
          h.grade_display ? "Grade: " + h.grade_display : "",
          referenceUrl(slug, h.hadith_number, h.number),
        ].filter(Boolean);
        copyText(parts.join("\n\n"), btn);
      } catch (err) {
        setStatus("Could not copy: " + err.message, true);
      }
    });

    function renderResultRow(item, isWeak) {
      const slug = item.slug;
      const num = String(item.hadith_number || item.number);
      const key = slug + "|" + num;
      const open = openRows.has(key);
      const label = refLabel(slug, item.hadith_number, item.number);
      const snippet = cleanText(item.snippet);
      const terms = highlightTerms(lastQuery);
      let score = "";
      if (showScores && typeof item.score === "number") {
        score = '<span class="score" title="Reranker relevance score">' + item.score.toFixed(2) + '</span>';
      }
      return (
        '<article class="row' + (open ? ' open' : '') + (isWeak ? ' weak' : '') + '" data-key="' + escapeHtml(key) + '">' +
          '<button type="button" class="row-main" aria-expanded="' + open + '" data-slug="' + escapeHtml(slug) + '" data-num="' + escapeHtml(num) + '">' +
            '<span class="row-head"><span class="ref">' + escapeHtml(label) + '</span>' +
              '<span class="badges">' + score + gradeBadges(item) + '</span></span>' +
            (item.chapter ? '<span class="chapter clamp" title="' + escapeHtml(item.chapter) + '">' + escapeHtml(item.chapter) + '</span>' : '') +
            '<span class="snippet">' + highlight(snippet, terms) + '</span>' +
          '</button>' +
          '<div class="row-detail"' + (open ? '' : ' hidden') + '></div>' +
        '</article>'
      );
    }

    async function toggleRow(rowEl, btn) {
      const key = rowEl.dataset.key;
      const detail = rowEl.querySelector(".row-detail");
      if (openRows.has(key)) {
        openRows.delete(key);
        rowEl.classList.remove("open");
        btn.setAttribute("aria-expanded", "false");
        detail.hidden = true;
        return;
      }
      openRows.add(key);
      rowEl.classList.add("open");
      btn.setAttribute("aria-expanded", "true");
      detail.hidden = false;
      await fillRowDetail(rowEl, btn.dataset.slug, btn.dataset.num);
    }

    async function fillRowDetail(rowEl, slug, num) {
      const detail = rowEl.querySelector(".row-detail");
      if (detail.dataset.filled) return;
      detail.innerHTML = '<div class="loading-line">Loading&hellip;</div>';
      try {
        const h = await getHadith(slug, num);
        detail.innerHTML = hadithBodyHtml(h, highlightTerms(lastQuery));
        detail.dataset.filled = "1";
      } catch (e) {
        detail.innerHTML = '<div class="loading-line">Could not load: ' + escapeHtml(e.message) + '</div>';
      }
    }

    function attachRowHandlers() {
      resultsRowsEl.querySelectorAll(".row").forEach((row) => {
        const btn = row.querySelector(".row-main");
        btn.addEventListener("click", () => toggleRow(row, btn));
        if (openRows.has(row.dataset.key)) fillRowDetail(row, btn.dataset.slug, btn.dataset.num);
      });
    }

    function isCollectionFilterActive() {
      const total = Object.keys(collections).length;
      return total > 0 && selectedCollections.size > 0 && selectedCollections.size < total;
    }

    function buildSearchUrl(mode, query, collection, limit) {
      const c = collection ? "&collection=" + encodeURIComponent(collection) : "";
      const q = encodeURIComponent(query);
      if (mode === "semantic") return "/v1/search/semantic?query=" + q + "&limit=" + limit + c;
      if (mode === "keyword")  return "/v1/search?query=" + q + "&limit=" + limit + c;
      if (mode === "term")     return "/v1/search/term?term=" + q + "&limit=" + limit + c;
      if (mode === "natural")  return "/v1/search/natural?query=" + q + "&limit=" + limit + c;
      throw new Error("unknown mode: " + mode);
    }

    // Server-side collection filtering: one API call per ticked collection in
    // parallel, then merge in the server's order (grade, then score).
    async function searchAcrossCollections(mode, query, ctx) {
      if (!isCollectionFilterActive()) {
        return await call(buildSearchUrl(mode, query, null, ALL_LIMIT), ctx);
      }
      const slugs = Array.from(selectedCollections);
      if (slugs.length === 1) {
        return await call(buildSearchUrl(mode, query, slugs[0], ALL_LIMIT), ctx);
      }
      const responses = await Promise.allSettled(
        slugs.map((s) => call(buildSearchUrl(mode, query, s, ALL_LIMIT), ctx))
      );
      const ok = responses.filter((r) => r.status === "fulfilled").map((r) => r.value);

      const merged = { results: [], results_weak: [], matched_words: [] };
      const gradeFirst = ok.some((j) => j.grade_first);
      const wordSum = new Map();
      for (const j of ok) {
        merged.results.push(...(j.results || []));
        merged.results_weak.push(...(j.results_weak || []));
        for (const w of (j.matched_words || [])) {
          wordSum.set(w.word, (wordSum.get(w.word) || 0) + w.count);
        }
      }
      merged.matched_words = Array.from(wordSum.entries())
        .map(([word, count]) => ({ word, count }))
        .sort((a, b) => (b.count - a.count) || a.word.localeCompare(b.word));

      const cmp = (a, b) => (b.score || b.similarity || 0) - (a.score || a.similarity || 0);
      const byGrade = (a, b) => ((a.grade_rank ?? 999) - (b.grade_rank ?? 999)) || cmp(a, b);
      merged.results.sort(gradeFirst ? byGrade : cmp);
      merged.results_weak.sort(gradeFirst ? byGrade : cmp);
      return merged;
    }

    let chipsExpanded = false;
    const CHIPS_SHOWN = 6;

    function chipsHtml(id, label, items, selected, renderLabel, collapsible) {
      // Unticked chips always stay visible so a filter is never hidden.
      const hideFrom = (collapsible && !chipsExpanded && items.length > CHIPS_SHOWN + 1) ? CHIPS_SHOWN : Infinity;
      let hidden = 0;
      const chips = items.map(([value, count], i) => {
        if (i >= hideFrom && selected.has(value)) { hidden++; return ""; }
        return '<label class="chip"><input type="checkbox" value="' + escapeHtml(value) + '"' +
          (selected.has(value) ? ' checked' : '') + '>' + renderLabel(value) +
          '<span class="n">' + count + '</span></label>';
      }).join("");
      const more = hidden ? '<button type="button" class="chip more" data-more="1">+' + hidden + ' more</button>'
        : (collapsible && chipsExpanded && items.length > CHIPS_SHOWN + 1)
          ? '<button type="button" class="chip more" data-more="0">Fewer</button>' : '';
      return '<div class="chips" id="' + id + '"><span class="chips-label">' + label + '</span>' + chips + more + '</div>';
    }

    function renderResultsView() {
      const collCounts = new Map();
      for (const r of lastResults.concat(lastResultsWeak)) {
        collCounts.set(r.slug, (collCounts.get(r.slug) || 0) + 1);
      }
      const orderedCols = Array.from(collCounts.entries())
        .sort((a, b) => (b[1] - a[1]) || a[0].localeCompare(b[0]));

      let header = "";
      if (orderedCols.length >= 2) {
        header += chipsHtml("rc-collections", "Collections", orderedCols, selectedResultCollections,
          (slug) => '<span>' + escapeHtml(titleFor(slug)) + '</span>', true);
      }
      if (lastMode === "term" && lastMatchedWords.length) {
        header += chipsHtml("rc-words", "Matched words",
          lastMatchedWords.slice(0, 8).map((w) => [w.word, w.count]), selectedWords,
          (w) => '<span class="ar" lang="ar">' + escapeHtml(w) + '</span>');
      }
      resultsHeaderEl.innerHTML = header;

      function applyFilters(rows) {
        let f = rows;
        if (orderedCols.length >= 2 && selectedResultCollections.size > 0) {
          f = f.filter((r) => selectedResultCollections.has(r.slug));
        }
        if (lastMode === "term" && lastMatchedWords.length && selectedWords.size > 0) {
          f = f.filter((r) => Array.isArray(r.matched_words) && r.matched_words.some((w) => selectedWords.has(w)));
        }
        return f;
      }

      const filteredStrong = applyFilters(lastResults);
      const filteredWeak = applyFilters(lastResultsWeak);
      const strongCount = filteredStrong.length;
      // Nothing cleared the relevance bar: show the nearest results rather
      // than an empty page.
      const autoWeak = !weakVisible && strongCount === 0 && filteredWeak.length > 0;
      const showingWeak = weakVisible || autoWeak;
      const visible = showingWeak ? filteredStrong.concat(filteredWeak) : filteredStrong;

      const visibleTotal = visible.length;
      const totalPages = Math.max(1, Math.ceil(visibleTotal / PAGE_SIZE));
      currentPage = Math.min(Math.max(currentPage, 1), totalPages);
      const start = (currentPage - 1) * PAGE_SIZE;
      const pageItems = visible.slice(start, start + PAGE_SIZE);

      let html = "";
      pageItems.forEach((it, i) => {
        const idx = start + i;
        const isWeak = idx >= strongCount;
        if (isWeak && idx === strongCount && strongCount > 0) {
          html += '<div class="divider">Less relevant</div>';
        }
        html += renderResultRow(it, isWeak);
      });

      const weakCount = filteredWeak.length;
      const onLastPage = currentPage === totalPages;
      if (weakCount > 0 && !autoWeak && onLastPage) {
        html += '<button type="button" id="show-weak" class="more-btn">' +
          (weakVisible ? "Hide less relevant results"
                       : "Show " + weakCount.toLocaleString() + " less relevant result" + (weakCount === 1 ? "" : "s")) +
          '</button>';
      }

      resultsRowsEl.innerHTML = html;
      pageNavEl.innerHTML = renderPageNav(totalPages, currentPage);
      attachRowHandlers();
      attachPageNavHandlers();

      const showWeakBtn = $("show-weak");
      if (showWeakBtn) {
        showWeakBtn.addEventListener("click", () => {
          weakVisible = !weakVisible;
          if (!weakVisible) currentPage = 1;
          renderResultsView();
        });
      }

      if (visibleTotal === 0) {
        setStatus("Nothing matches the current filters. Tick more chips to see results.");
      } else {
        const n = visibleTotal.toLocaleString();
        let msg = autoWeak
          ? "No close matches, so these are the " + n + " nearest results"
          : n + " result" + (visibleTotal === 1 ? "" : "s");
        if (lastResolvedNote) msg += " (" + lastResolvedNote + ")";
        if (totalPages > 1) msg += ", page " + currentPage + " of " + totalPages;
        setStatus(msg + ".");
      }

      resultsHeaderEl.querySelectorAll("#rc-collections input").forEach((inp) => {
        inp.addEventListener("change", () => {
          selectedResultCollections = new Set(
            Array.from(resultsHeaderEl.querySelectorAll("#rc-collections input:checked")).map((i) => i.value));
          currentPage = 1;
          renderResultsView();
        });
      });
      resultsHeaderEl.querySelectorAll(".chip.more").forEach((b) => b.addEventListener("click", () => {
        chipsExpanded = b.dataset.more === "1";
        renderResultsView();
      }));
      resultsHeaderEl.querySelectorAll("#rc-words input").forEach((inp) => {
        inp.addEventListener("change", () => {
          selectedWords = new Set(
            Array.from(resultsHeaderEl.querySelectorAll("#rc-words input:checked")).map((i) => i.value));
          currentPage = 1;
          renderResultsView();
        });
      });
    }

    function renderPageNav(totalPages, current) {
      if (totalPages <= 1) return "";
      const w = 1;
      const wanted = new Set([1, totalPages]);
      for (let p = Math.max(1, current - w); p <= Math.min(totalPages, current + w); p++) wanted.add(p);
      const sorted = Array.from(wanted).sort((a, b) => a - b);
      const parts = [];
      let prev = 0;
      for (const p of sorted) {
        if (prev && p - prev > 1) parts.push('<span class="pn-ellipsis">&hellip;</span>');
        const cur = p === current;
        parts.push('<button type="button" class="pn-btn' + (cur ? ' pn-current' : '') + '" data-page="' + p + '"' +
          (cur ? ' disabled aria-current="page"' : '') + '>' + p + '</button>');
        prev = p;
      }
      return '<nav class="page-nav" aria-label="Pages">' +
        '<button type="button" class="pn-btn" data-page="' + (current - 1) + '"' + (current <= 1 ? ' disabled' : '') + ' aria-label="Previous page">&lsaquo;</button>' +
        parts.join("") +
        '<button type="button" class="pn-btn" data-page="' + (current + 1) + '"' + (current >= totalPages ? ' disabled' : '') + ' aria-label="Next page">&rsaquo;</button>' +
        '</nav>';
    }

    function attachPageNavHandlers() {
      pageNavEl.querySelectorAll("button[data-page]").forEach((btn) => {
        if (btn.disabled) return;
        btn.addEventListener("click", () => {
          const p = parseInt(btn.dataset.page, 10);
          if (isNaN(p)) return;
          currentPage = p;
          renderResultsView();
          statusEl.scrollIntoView({ behavior: "smooth", block: "start" });
        });
      });
    }

    // Anonymous ids for the query log: one per browser tab, one per search.
    // No IP or user agent is stored server-side.
    function randomId() {
      return (crypto.randomUUID && crypto.randomUUID()) || (Date.now().toString(36) + Math.random().toString(36).slice(2));
    }
    const SESSION_ID = (() => {
      try {
        let s = sessionStorage.getItem("sss_session");
        if (!s) { s = randomId(); sessionStorage.setItem("sss_session", s); }
        return s;
      } catch (_) { return randomId(); }
    })();

    // `ctx` carries the search-level headers; row expands and copies pass none.
    async function call(url, ctx) {
      const r = await fetch(url, { headers: Object.assign({ "X-Client": "web", "X-Session": SESSION_ID }, ctx || {}) });
      if (!r.ok) {
        let detail = await r.text();
        try { detail = JSON.parse(detail).detail || detail; } catch (_) {}
        if (typeof detail !== "string") detail = JSON.stringify(detail);
        if (r.status === 429) throw new Error(detail);
        throw new Error(r.status === 404 ? "not found" : "HTTP " + r.status + ": " + detail);
      }
      return r.json();
    }

    async function showHadith(slug, number, ctx) {
      setStatus("Loading hadith…");
      try {
        const h = await getHadith(slug, number, ctx);
        detailEl.innerHTML = renderHadithCard(h);
        setStatus("");
        document.body.classList.add("has-results");
        document.title = refLabel(h.collection, h.hadith_number, h.number) + " · Sunnah Semantic Search";
      } catch (e) {
        setStatus("Could not load " + titleFor(slug) + " " + number + ": " + e.message + ".", true);
      }
    }

    function parseReference(text) {
      const m = text.trim().match(/^(.+?)\s*[#:]?\s*([0-9][0-9a-zA-Z,\s]*?)\s*$/);
      if (!m) return null;
      const slug = slugByAlias[norm(m[1])];
      return slug ? { slug, number: m[2].trim() } : null;
    }

    // Auto mode: a reference if it parses as one, Arabic-term search for
    // Arabic script, otherwise a natural-language search.
    function resolveMode(mode, q) {
      if (mode !== "auto") return mode;
      if (/\d/.test(q) && parseReference(q)) return "reference";
      if (/[؀-ۿ]/.test(q)) return "term";
      return "natural";
    }

    let lastResolvedNote = "";
    let searchSeq = 0;

    // trigger: "typed" for a search the user ran, "link" for one opened from
    // a URL (shared link, reload), "back" for one replayed by history navigation.
    async function doSearch(mode, q, trigger) {
      const seq = ++searchSeq;
      clearAll();
      q = q.trim();
      if (!q) { setStatus("Type something first.", true); return; }
      await collectionsReady;
      const resolved = resolveMode(mode, q);
      const ctx = {
        "X-Search-Id": randomId(),
        "X-Search-Trigger": trigger || "typed",
        "X-UI-Mode": mode,
        "X-Collections": isCollectionFilterActive() ? Array.from(selectedCollections).join(",") : "",
      };
      lastResolvedNote = (mode === "auto" && resolved === "term") ? "searched as an Arabic term" : "";
      document.title = q + " · Sunnah Semantic Search";
      document.body.classList.add("has-results");

      if (resolved === "reference") {
        const ref = parseReference(q);
        if (!ref) {
          setStatus("Couldn’t recognise that reference. Try “Bukhari 1” or “Sahih Muslim 5”.", true);
          return;
        }
        await showHadith(ref.slug, ref.number, ctx);
        return;
      }

      setStatus("Searching…");
      setLoading(true);
      try {
        const j = await searchAcrossCollections(resolved, q, ctx);
        if (seq !== searchSeq) return;        // a newer search started meanwhile
        const items = j.results || [];
        const weakItems = j.results_weak || [];
        if (!items.length && !weakItems.length) {
          setStatus(isCollectionFilterActive()
            ? "No matches in the selected collections. Try more collections or different wording."
            : "No matches. Try different wording, or pick a mode under Advanced search.");
          return;
        }
        lastResults = items;
        lastResultsWeak = weakItems;
        lastMode = resolved;
        lastQuery = q;
        lastMatchedWords = (resolved === "term" && Array.isArray(j.matched_words)) ? j.matched_words : [];
        selectedWords = new Set(lastMatchedWords.slice(0, 8).map((w) => w.word));
        selectedResultCollections = new Set(items.concat(weakItems).map((r) => r.slug));
        currentPage = 1;
        renderResultsView();
      } catch (e) {
        if (seq === searchSeq) setStatus("Search failed: " + e.message, true);
      } finally {
        if (seq === searchSeq) setLoading(false);
      }
    }

    // ---- URL state -------------------------------------------------------
    function stateUrl(q, mode) {
      const p = new URLSearchParams();
      p.set("q", q);
      if (mode !== "auto") p.set("mode", mode);
      if (isCollectionFilterActive()) p.set("c", Array.from(selectedCollections).join(","));
      return "/?" + p.toString();
    }

    // trigger is "link" on page load (shared link, reload), "back" on history navigation.
    async function applyUrl(trigger) {
      await collectionsReady;
      const p = new URLSearchParams(location.search);
      const h = p.get("h");
      const q = p.get("q");
      setMode(p.get("mode") || "auto");
      setCollections(p.get("c") ? p.get("c").split(",") : null);
      if (p.get("mode") || p.get("c")) setAdvOpen(true);
      if (h && h.includes(":")) {
        clearAll();
        const i = h.indexOf(":");
        qIn.value = "";
        await showHadith(h.slice(0, i), h.slice(i + 1));
      } else if (q) {
        qIn.value = q;
        await doSearch(currentMode(), q, trigger === "back" ? "back" : "link");
      } else {
        clearAll();
        qIn.value = "";
        setStatus("");
        document.body.classList.remove("has-results");
        document.title = "Sunnah Semantic Search";
      }
    }

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const q = qIn.value.trim();
      if (!q) { qIn.focus(); return; }
      const url = stateUrl(q, currentMode());
      if (url !== location.pathname + location.search) history.pushState(null, "", url);
      doSearch(currentMode(), q);
      if (matchMedia("(pointer: coarse)").matches) qIn.blur();
    });

    document.querySelectorAll(".ex").forEach((b) => b.addEventListener("click", () => {
      qIn.value = b.textContent;
      form.requestSubmit();
    }));

    $("home-link").addEventListener("click", (e) => {
      e.preventDefault();
      history.pushState(null, "", "/");
      applyUrl();
      qIn.focus();
    });

    window.addEventListener("popstate", () => applyUrl("back"));

    randomBtn.addEventListener("click", async () => {
      clearAll();
      setStatus("Picking a hadith…");
      randomBtn.disabled = true;
      try {
        const h = await call("/v1/random");
        const num = h.hadith_number || h.number;
        hadithCache.set(h.collection + "|" + num, Promise.resolve(h));
        history.pushState(null, "", "/?h=" + encodeURIComponent(h.collection + ":" + num));
        detailEl.innerHTML = renderHadithCard(h);
        document.body.classList.add("has-results");
        document.title = refLabel(h.collection, h.hadith_number, h.number) + " · Sunnah Semantic Search";
        setStatus("");
      } catch (e) {
        setStatus("Could not pick a hadith: " + e.message, true);
      } finally {
        randomBtn.disabled = false;
      }
    });

    // "/" focuses the search box; Escape leaves it.
    document.addEventListener("keydown", (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
      if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        qIn.focus();
        qIn.select();
      } else if (e.key === "Escape" && document.activeElement === qIn) {
        qIn.blur();
      }
    });

    // Optional sign-in. Hidden entirely when the server has it turned off.
    async function loadAccount() {
      const el = $("account");
      let me;
      try {
        const r = await fetch("/auth/me", { credentials: "same-origin" });
        if (!r.ok) return;
        me = await r.json();
      } catch (_) { return; }
      if (!me.enabled) return;
      if (!me.user) {
        el.innerHTML = '<a class="link-btn" id="sign-in" href="/auth/login">Sign in</a>';
        $("sign-in").addEventListener("click", (e) => {
          e.preventDefault();
          location.href = "/auth/login?return_to=" + encodeURIComponent(location.href);
        });
      } else {
        const u = me.user;
        el.innerHTML =
          '<details><summary class="link-btn">' + escapeHtml(u.name || u.email) + ' <span class="chev">&#9662;</span></summary>' +
          '<div class="account-menu">' +
            '<div class="who">Signed in as' + (u.name ? '<strong>' + escapeHtml(u.name) + '</strong>' : '') + escapeHtml(u.email) + '</div>' +
            '<a href="/auth/account" id="account-link">Account</a>' +
            '<form method="post" action="/auth/logout" id="sign-out"><input type="hidden" name="return_to"><button type="submit">Sign out</button></form>' +
          '</div></details>';
        $("sign-out").addEventListener("submit", (e) => { e.target.return_to.value = location.href; });
        $("account-link").addEventListener("click", (e) => {
          e.preventDefault();
          location.href = "/auth/account?return_to=" + encodeURIComponent(location.href);
        });
        const details = el.querySelector("details");
        document.addEventListener("click", (e) => { if (!details.contains(e.target)) details.open = false; });
      }
      el.hidden = false;
    }

    setMode("auto");
    collectionsReady = loadCollections();
    applyUrl();
    loadAccount();
  </script>
</body>
</html>
"""


def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)
