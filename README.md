# sunnah-toolkit

REST and MCP API for the hadith corpus from [sunnah.com](https://sunnah.com).

Built on top of sunnah.com's official MariaDB snapshot (44,896 hadiths
across 15 collections) with hybrid BM25 + multilingual-semantic search.
Exposes 7 tools over two transports:

- **MCP** — for AI assistants (Claude Desktop, Claude Code, Cursor, etc.) via
  stdio or streamable-http.
- **REST** — for everything else, at `/v1/*`.

Per-hadith data includes the **grading** (`Sahih` / `Hasan` / `Da'if` / …)
and the **structured chain of narration** with sunnah.com's stable narrator
IDs — enough to filter by authenticity tier or analyse isnad relationships.

## Tools

| Tool | What it does |
|------|--------------|
| `list_collections` | All 15 collections with names and hadith counts |
| `list_books` | Chapters within a collection (e.g. the 97 books of Bukhari) |
| `get_hadith` | Single hadith by collection + number, with Arabic + English + narrator + grading + parsed isnad chain |
| `search_hadith` | English keyword search, BM25-ranked. Results include a grade tag. |
| `search_hadith_term` | Find hadiths containing a specific Arabic term, accepting any transliteration spelling (`qunut`/`qunoot`/`qonot` all match قنوت; `azan`↔`adhan`, `ramazan`↔`ramadan`). Returns matched Arabic word frequencies so collisions are visible. |
| `search_hadith_semantic` | Meaning-based search via multilingual embeddings — handles conceptual queries (`humility`, `caring for orphans`) and cross-lingual queries (`الصلاة` finds the same hadiths as `prayer`). Returns similarity scores. |
| `random_hadith` | A random hadith, optionally from one collection |

Every result includes a `sunnah.com/<collection>:<number>` reference URL so
the model can cite accurately.

## Try it live

A public instance runs on Hugging Face Spaces:

- UI + REST: <https://hassannjb-sunnah-toolkit.hf.space>
- Example: <https://hassannjb-sunnah-toolkit.hf.space/v1/search?query=mercy&limit=3>

It runs the GHCR image `ghcr.io/hassannjb/sunnah-toolkit` on HF's free CPU
tier (2 vCPU). The cross-encoder reranker is **disabled** there
(`RERANKER_DISABLED=1`) because the free tier can't carry it at interactive
latency — ranking falls back to BM25 / cosine. Warm queries land in
~0.3 s; cold starts (after ~48 h of idle) take 30–60 s while the image
is restored.

## Architecture

### Network topology

```mermaid
graph LR
    subgraph Internet
      Client["MCP client / curl"]
      CF["Cloudflare edge<br/>(TLS + rate-limit)"]
    end
    subgraph "Self-hosted server"
      Tunnel["cloudflared<br/>(outbound tunnel)"]
      Box["Docker container<br/>sunnah-toolkit :8000"]
    end
    Client -->|HTTPS| CF
    CF <-.->|persistent tunnel| Tunnel
    Tunnel -->|http://localhost:8000| Box
```

No inbound ports on the host — `cloudflared` opens an outbound tunnel to Cloudflare, which terminates TLS at the edge and forwards requests through the tunnel to the container.

### Components

```mermaid
graph TD
    subgraph Clients
      C1["MCP stdio<br/>(Claude Desktop / Code)"]
      C2["MCP HTTP<br/>(streamable-http)"]
      C3["REST<br/>(curl / apps)"]
    end

    subgraph Transports
      MCP["mcp/server.py<br/>FastMCP"]
      API["api/app.py + routes.py<br/>FastAPI · auth.py"]
    end

    subgraph "Core (protocol-agnostic)"
      Tools["core/tools.py<br/>7 tool functions"]
      Fmt["core/text_format.py"]
    end

    subgraph "Data &amp; algorithms"
      Data["core/data.py<br/>SQLite + BM25 + narrator parser"]
      Sem["core/semantic.py<br/>embeddings + MiniLM"]
      Tr["core/translit.py<br/>Arabic folding"]
    end

    subgraph "On-disk assets"
      DB[("data/hadith.sqlite")]
      Emb[("data/embeddings.npy")]
    end

    C1 --> MCP
    C2 --> MCP
    C3 --> API
    MCP --> Tools
    API --> Tools
    Tools --> Fmt
    Tools --> Data
    Tools --> Sem
    Tools --> Tr
    Data --> DB
    Sem --> Emb
    Sem --> DB
```

Both transports delegate to the same `core/tools.py` — REST returns structured JSON, MCP returns LLM-friendly text via `text_format.py`.

### Request flow — semantic search

```mermaid
sequenceDiagram
    participant C as Client
    participant A as auth.py
    participant R as routes.py
    participant T as tools.py
    participant S as semantic.py
    participant D as data.py
    participant F as text_format.py

    C->>A: GET /v1/search/semantic?query=…
    A->>A: check optional Bearer token
    A->>R: pass-through (anon or identified)
    R->>T: search_hadith_semantic(query, collection, limit)
    T->>S: encode(query) → vector
    S->>S: cosine sim vs embeddings.npy
    S-->>T: top-k hadith IDs + scores
    T->>D: hydrate(ids) → full records
    D-->>T: hadith records
    T->>F: format(records, scores)
    F-->>R: JSON (REST) / text (MCP)
    R-->>C: 200 OK
```

Semantic search exercises the most layers; BM25 keyword search (`/v1/search`) follows the same shape but skips `semantic.py` and queries `data.py`'s BM25 index directly.

### Auth flow

```mermaid
sequenceDiagram
    participant C as Client
    participant M as auth.py middleware
    participant R as route handler

    C->>M: request to /v1/* [Authorization?]

    alt no keys file loaded (open mode)
        M->>R: pass-through, caller=anonymous
        R-->>C: 200
    else keys file loaded
        alt no Authorization header
            M->>R: pass-through, caller=anonymous
            R-->>C: 200
        else valid Bearer token
            M->>R: pass-through, caller=alice
            R-->>C: 200
        else invalid Bearer token
            M-->>C: 401 Unauthorized
        end
    end

    Note over C,R: /mcp bypasses auth entirely (by design)
```

The keyed tier identifies callers; it does not gatekeep them. Rate-limiting and abuse control live at the Cloudflare edge.

### Build pipeline

```mermaid
graph LR
    subgraph Inputs
      DS["sunnah.com<br/>HadithTable.sql.gz<br/>(MariaDB dump, ~16 MB)"]
      HF["HuggingFace<br/>MiniLM-L12-v2"]
    end

    subgraph "Build-time scripts"
      Sqlite["scripts/build_sqlite.py"]
      Build["scripts/build_embeddings.py"]
    end

    subgraph "On-disk artifacts"
      DB[("data/hadith.sqlite<br/>~80 MB")]
      Emb[("data/embeddings.npy<br/>~66 MB")]
      Cache[("HF model cache<br/>~120 MB")]
    end

    Docker["Dockerfile<br/>multi-stage build"]
    Image["sunnah-toolkit image<br/>(~3.0 GB, offline at runtime)"]

    DS --> Sqlite --> DB
    HF --> Build
    DB --> Build
    Build --> Emb
    Build --> Cache
    DB --> Docker
    Emb --> Docker
    Cache --> Docker
    Docker --> Image
```

Data and model are baked into the image at build time — no network needed at query time, which is what lets the container survive Cloudflare outages and keeps cold-start latency predictable. The runtime image is ~3.0 GB; most of it is the CPU-only PyTorch wheel + transformers (~1.3 GB) and the HF model cache (~120 MB).

## Quickstart — local dev (stdio MCP)

Get the dataset first. Drop sunnah.com's SQL dump at
`data/HadithTable.sql.gz` — you receive it from sunnah.com along with their
API key (request access at <https://sunnah.com/developers>; both are sent to
your inbox). Then:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m scripts.build_sqlite       # ~5s — converts the dump into data/hadith.sqlite
.venv/bin/python -m scripts.build_embeddings   # one-time, ~2 min on Apple Silicon → data/embeddings.npy (~66 MB)
.venv/bin/python -m sunnah_toolkit             # runs MCP server over stdio
```

`build_embeddings` is only required if you'll use `search_hadith_semantic`.
It downloads the `paraphrase-multilingual-MiniLM-L12-v2` model (~120 MB) into
the HuggingFace cache on first run.

Sunnah.com asks downstream apps to refresh the data at least once a month so
upstream corrections propagate — see [Refresh the dataset](#refresh-the-dataset).

## Run as an HTTP server (Docker)

The included `Dockerfile` is a self-contained image (~3.0 GB) that bakes
the SQLite database, embeddings, and model cache in at build time — no
network needed at query time.

```bash
docker build -t sunnah-toolkit .
docker run --rm -p 8000:8000 sunnah-toolkit
```

The container exposes REST under `/v1/*`, MCP-over-HTTP at `/mcp`, and a
`/healthz` probe. To run HTTP mode without Docker:

```bash
.venv/bin/python -m sunnah_toolkit --transport http --host 0.0.0.0 --port 8000
```

## REST API

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/collections` | List all collections |
| GET | `/v1/collections/{slug}/books` | Chapters within a collection |
| GET | `/v1/hadith/{slug}/{number}` | Authoritative hadith fetch |
| GET | `/v1/search?query=…&collection=&limit=` | BM25 English keyword search |
| GET | `/v1/search/term?term=…&collection=&limit=` | Arabic-term search by transliteration |
| GET | `/v1/search/semantic?query=…&collection=&limit=` | Multilingual semantic search |
| GET | `/v1/random?collection=` | Random hadith |

Examples:

```bash
curl localhost:8000/v1/collections | jq '.collections[].slug'
curl localhost:8000/v1/hadith/bukhari/1 | jq
curl 'localhost:8000/v1/search/semantic?query=kindness+to+neighbours&limit=3' | jq
curl 'localhost:8000/v1/search/term?term=qunoot&limit=5' | jq
```

REST responses are structured JSON; MCP responses are LLM-friendly text. Both
transports share the same core (see `core/tools.py` + `core/text_format.py`).

## MCP integration

### stdio — Claude Code, Claude Desktop, Cursor

The included `.mcp.json` is auto-discovered by Claude Code. The same JSON
works in `~/Library/Application Support/Claude/claude_desktop_config.json`
for Claude Desktop:

```json
{
  "mcpServers": {
    "sunnah": {
      "type": "stdio",
      "command": "/absolute/path/to/sunnah-toolkit/.venv/bin/python",
      "args": ["-m", "sunnah_toolkit"],
      "cwd": "/absolute/path/to/sunnah-toolkit"
    }
  }
}
```

A project-level Claude Code skill ships at `.claude/skills/sunnah/SKILL.md` —
it documents tool-selection and enforces verbatim quoting. Claude Code picks
it up automatically; you can invoke it explicitly with `/sunnah`.

### Streamable-HTTP — when the toolkit runs over HTTP

The MCP endpoint is mounted at `/mcp` (no trailing-slash redirect). Point
any MCP client that speaks streamable-http transport at:

```
http://localhost:8000/mcp
```

## Auth

`/v1/*` supports optional bearer-token auth via a YAML keys file. Without a
keys file the server runs in **open mode** — any token (or none) accepted —
which is what you want for local dev.

`keys.yaml`:

```yaml
alice: a-long-random-token-for-alice
bob: a-long-random-token-for-bob
```

Run with auth enabled:

```bash
.venv/bin/python -m sunnah_toolkit --transport http --keys-file ./keys.yaml
```

Then call with:

```bash
curl -H 'Authorization: Bearer a-long-random-token-for-alice' \
  localhost:8000/v1/collections
```

Invalid tokens get 401; requests without an `Authorization` header fall
through as anonymous and still succeed. `/mcp` is unauthenticated by design —
the keyed tier exists for identification, and rate-limiting / abuse control
live at the edge (e.g. Cloudflare in front of the public instance).

### Accounts (optional sign-in)

Separate from the bearer tokens above: people can sign in on the web UI with
Google, GitHub, email + password, or an emailed link. Searching never needs
it. It is off unless `AUTH_SECRET` and `USERS_DB_PATH` are set; pages live
under `/auth/*` (`api/accounts.py`), accounts in SQLite (`core/users.py`).

| Variable | Meaning |
|---|---|
| `AUTH_SECRET` | signs the session cookie; share it with any sibling site that reads the cookie |
| `USERS_DB_PATH` | SQLite file for users, sessions and emailed tokens |
| `AUTH_BASE_URL` | public address of these pages, used in links and OAuth callbacks (default `https://searchthehadith.fyi`) |
| `AUTH_COOKIE_DOMAIN` | e.g. `.searchthehadith.fyi` to share the session with subdomains; unset for localhost |
| `AUTH_RETURN_HOSTS` | comma-separated hosts a sign-in may return to |
| `GOOGLE_CLIENT_ID` / `_SECRET`, `GITHUB_CLIENT_ID` / `_SECRET` | enable each provider |
| `RESEND_API_KEY`, `AUTH_EMAIL_FROM` | send email through Resend; without a key, emails (with their links) go to the log |

For local testing: `AUTH_SECRET=dev USERS_DB_PATH=/tmp/users.sqlite AUTH_BASE_URL=http://localhost:8000`,
then open `http://localhost:8000/auth/login` and take links from the server log.

## Self-hosting

Any Linux, Unix, or macOS host with Docker can run the container. Put a
reverse proxy (nginx, Caddy, Traefik) or a Cloudflare Tunnel in front for
TLS and a public hostname; use the platform's preferred service manager
(systemd, runit, OpenRC, etc.) to keep the container running across
reboots.

The simplest way to launch it is via the included `docker-compose.yml`:

```bash
docker compose up -d
```

Artifacts in `deploy/`:

- `deploy/cloudflared/config.yml.example` — Cloudflare Tunnel ingress example
  to expose `localhost:8000` on a hostname. Setup steps are in the file
  header.

- `deploy/keys.example.yaml` — example bearer-token map.

## Project layout

```
sunnah_toolkit/
  core/        protocol-agnostic library: tools, data loading, BM25,
               embeddings, Arabic transliteration, text formatters
  mcp/         FastMCP wrappers exporting the 7 tools over stdio + HTTP
  api/         FastAPI app: /v1/* routes, /healthz, bearer-token auth,
               optional sign-in under /auth
  cli.py       argparse entrypoint: --transport {stdio,http}, --host,
               --port, --keys-file
scripts/
  build_sqlite.py      converts data/HadithTable.sql.gz → data/hadith.sqlite
  build_embeddings.py  pre-computes embeddings (run once per dataset refresh)
  refresh.sh           one-command monthly-refresh wrapper (see § "Refresh
                       the dataset")
deploy/                cloudflared, keys.yaml examples
data/                  (gitignored except for the input dump)
                         HadithTable.sql.gz      sunnah.com snapshot (input)
                         hadith.sqlite           built by build_sqlite
                         embeddings.npy          built by build_embeddings
                         embeddings_meta.json    fingerprint + model meta
Dockerfile             multi-stage, self-contained image
```

## Refresh the dataset

Per sunnah.com's request, refresh the local snapshot at least once a month
so upstream corrections (text fixes, grading updates, new translations)
propagate. Sunnah.com publishes new snapshots by overwriting the file at
the same static URL (`https://sunnah.com/HadithTable.sql.gz`); the refresh
script does an HTTP conditional GET, so it's safe to run on a cron — it
no-ops if there's nothing new upstream.

```bash
./scripts/refresh.sh
```

What it does:

1. Conditional GET against `sunnah.com/HadithTable.sql.gz`. If the
   server's `Last-Modified` matches your local file, exit immediately —
   nothing to do. Otherwise download in place.
2. Rebuild `data/hadith.sqlite` from the new dump.
3. Rebuild `data/embeddings.npy` against the new SQLite corpus.
4. Tag the current image as `sunnah-toolkit:previous` (rollback save
   point) and `docker build` the new `sunnah-toolkit:latest`.
5. Restart the running `sunnah` container against the new image and poll
   `/healthz`.

Total runtime when an update *is* available: ~12 min on Apple Silicon.
When nothing has changed: ~1 second.

Flags: `--no-image` (refresh only the local venv, for stdio-MCP users),
`--no-deploy` (rebuild the image but leave the container alone),
`--help` for the full inline doc.

If something looks wrong after a refresh, roll back instantly:

```bash
docker tag sunnah-toolkit:previous sunnah-toolkit:latest
docker stop sunnah && docker rm sunnah
docker run -d --restart unless-stopped -p 8000:8000 \
    --name sunnah sunnah-toolkit:latest
```

The dump URL is a public download — no API key required. Sunnah.com's
authenticated API at `api.sunnah.com` is a separate channel (5 req/s,
5,000 req/day on a personal key) that we don't use here. **Never bundle
that API key with the image or commit it to the repo** — sunnah.com is
explicit about that, even though it doesn't apply to the dump flow.

## Known limitations

- **Semantic search uses a small (~120 MB) multilingual model** —
  `paraphrase-multilingual-MiniLM-L12-v2`. Good baseline; conceptual queries
  on abstract terms (e.g. "humility in worship") may return adjacent rather
  than direct matches. Swappable: edit `MODEL_ID` in
  `scripts/build_embeddings.py` and rerun. OpenAI / Cohere / BGE-M3 are
  drop-in replacements (~$0.10 one-time re-embed cost for OpenAI's
  `text-embedding-3-small`).
- **Short skeletons can collide** in `search_hadith_term` (e.g. `azan`
  matches both أذان and زنى/أظن because both fold to `zn`). Mitigated by
  surfacing the matched Arabic words with frequencies so users can spot it.
- **Coverage = whatever sunnah.com serves.** Darimi and Muwatta Malik are
  not in the dump and therefore not in this toolkit. Musnad Ahmad is
  partial (chapters past book 7 are absent from sunnah.com itself, not
  just from this build). Track the upstream gaps at
  [sunnah.com/developers](https://sunnah.com/developers).
- **One English translation** per hadith — sunnah.com doesn't ship
  alternative translations.
- **The `xrefs` column is present in the schema but empty** in the snapshot
  we received — sunnah.com didn't populate cross-references. We preserve the
  column in SQLite in case future snapshots fill it in.

## Credits

- **Data**: official MariaDB snapshot from [sunnah.com](https://sunnah.com),
  used with permission and per their API/data terms. Refresh monthly; never
  bundle the API key with the app.
- Reference architecture: [quran/quran-mcp](https://github.com/quran/quran-mcp).
- Protocol: [Model Context Protocol](https://modelcontextprotocol.io/).
- Earlier versions of this toolkit used
  [AhmedBaset/hadith-json](https://github.com/AhmedBaset/hadith-json) (a
  community scrape of sunnah.com) as the data source; thanks to that
  project for bootstrapping the build pipeline.
