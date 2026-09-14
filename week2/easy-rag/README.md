# EASY-RAG

A **dynamic** RAG web app: create assistants, upload documents into each
assistant's own embeddings collection, and chat — every answer is grounded in the
chunks retrieved from that collection, and ships with **clickable sources** and
**token usage**.

This is `week2/exercise1` (static RAG: the *whole* document pasted into the prompt
every turn) grown into dynamic RAG (only the **retrieved** chunks ride along). It
is, roughly, the course's `simple-dynamic-rag` tool turned into a web app.

## The one rule

The app talks to the embeddings database **only** through the course's
[`collections-manager`](../../../ludo-engsoft/week-02/collections-manager)
abstraction layer — `create_collection` / `insert` / `query` — never to ChromaDB
directly. Exactly **one** module imports it: `backend/rag.py`. There is no local
copy or re-implementation of that layer; `collections-manager` is consumed as an
editable **path dependency** pointing at the real repo (see `pyproject.toml`).

## What it does

- **Ingestion** (`POST /api/assistants/{id}/documents`): convert the upload to
  markdown with **markitdown** (pdf, docx, pptx, html, txt, md...), store the
  **original + its markdown distillation** under `/static` (linkable by URL),
  chunk the markdown **by paragraphs** (config-driven), and `insert()` each chunk
  with provenance metadata.
- **Chat** (`POST /api/assistants/{id}/chat/stream`): the user message **is the
  query**; `query()` returns the top-K chunks **over the similarity threshold**;
  they are formatted **with provenance** into the same `{context}` / `{user_input}`
  template as exercise1 and streamed back. If **nothing passes the threshold**, the
  assistant honestly answers it doesn't know (the LLM is not called, no weak chunks
  are forced).
- **Provenance**: every answer returns the chunks used — original-document link
  (`/static/...`), chunk number, similarity — plus token usage. In the UI each
  source is clickable and opens the original document.
- **De-dup & cleanup**: re-uploading identical content (same SHA-256) for an
  assistant is skipped (`"deduplicated": true`), so chunks are never duplicated;
  deleting an assistant removes its `/static` files (its Chroma collection persists —
  see *Known limitations*).
- **In-chat tuning**: `/topk <k>` and `/threshold <num|off>` adjust retrieval live,
  per assistant (in-memory), and reply with a confirmation — they are never sent to
  the LLM.

## Setup (local, with uv)

Requires [`uv`](https://docs.astral.sh/uv/) and a running [Ollama](https://ollama.com):

```bash
ollama pull qwen2.5vl:7b        # chat
ollama pull nomic-embed-text    # embeddings
```

`collections-manager` is a **path dependency** (declared in `pyproject.toml`, like
`simple-dynamic-rag`). The default path assumes the course repo is at
`~/ludo-engsoft`; if yours is elsewhere, edit the path under `[tool.uv.sources]`.

```bash
cd week2/easy-rag
cp .env.example .env       # set LLM_BASE_URL to your Ollama endpoint
uv sync                    # creates .venv; installs deps + editable collections-manager
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8123 --reload
```

Open **http://127.0.0.1:8123**. Runtime data (assistants JSON, the collections, and
uploaded docs under `/static`) lives in `data/` and is gitignored.

## Run with Docker

The app image does **not** bake in `collections-manager` (it lives outside the
build context, in `~/ludo-engsoft`). Instead, `docker-compose.yml` **mounts the
course repo** at the exact relative location `pyproject.toml` expects, and the
entrypoint runs `uv sync` at startup so dependency resolution finds it just like
locally.

```bash
cd week2/easy-rag
cp .env.example .env       # if you haven't already
docker compose up -d --build
```

Open **http://localhost:6664**. Stop with `docker compose down`.

How the mount lines up (this is the part to understand if anything moves):

```
pyproject.toml path dep:   ../../../ludo-engsoft/week-02/collections-manager
container WORKDIR:         /workspace/TLRA/week2/easy-rag
  ../../../ from there  -> /workspace
compose bind mount:        ${LUDO_ENGSOFT_PATH:-$HOME/ludo-engsoft} -> /workspace/ludo-engsoft
  => uv sync resolves      /workspace/ludo-engsoft/week-02/collections-manager  ✓
```

> **Assumes `~/ludo-engsoft` exists on the host.** If the course repo moves, set
> `LUDO_ENGSOFT_PATH` (in your shell or `.env`) to its new location — that is the
> only variable to edit. The container-side target `/workspace/ludo-engsoft` must
> keep matching the relative path in `pyproject.toml`; if you change the `WORKDIR`
> layout in the `Dockerfile`, change both together.

Two Docker-specific notes:

- **LLM endpoint.** A host LAN/bridge IP in `.env` (e.g. `172.22.240.1`) is often
  *not* routable from inside a container. `docker-compose.yml` therefore overrides
  `LLM_BASE_URL` to `http://host.docker.internal:11434/v1` (the `host-gateway`
  added via `extra_hosts`). Override with `DOCKER_LLM_BASE_URL` if your Ollama is
  elsewhere. When running **without** Docker, `.env`'s `LLM_BASE_URL` is used as-is.
- **First start is slow.** `uv sync` runs at container start and downloads
  dependencies the first time. The `easyrag_uvcache` and `easyrag_venv` named
  volumes make subsequent starts fast. `easyrag_data` persists assistants,
  collections and uploads across restarts.

## Manual end-to-end test

With the app running (`BASE=http://127.0.0.1:8123`, or `:6664` under Docker):

```bash
# 1) Create an assistant — also creates its collection.
curl -s -X POST $BASE/api/assistants \
  -F name='Acme Field Bot' \
  -F system_prompt='Use only the information in the context below to answer. If it is not there, say you do not know.' \
  -F prompt_template='Answer the question using only the context below.

Context:
{context}

Question: {user_input}'
# -> {"id":"<AID>","collection":"assistant_<AID>","document_count":0,"chunk_count":0,...}

# 2) Upload a document (pdf/docx/pptx/html/txt/md). It is converted, stored under
#    /static, chunked by paragraphs, and inserted.
curl -s -X POST $BASE/api/assistants/<AID>/documents -F document=@handbook.md
# -> {"document":{"chunks":6,"doc_url":"/static/docs/<id>.md","md_url":"/static/docs/<id>.distilled.md",...},...}

# 3) Ask a question — streams SSE; the final `done` event carries sources + usage.
curl -sN -X POST $BASE/api/assistants/<AID>/chat/stream \
  -H 'Content-Type: application/json' -d '{"message":"How fast is the Pallet Pup?"}'

# 4) Open a cited source (the ORIGINAL document) under /static:
curl -sI $BASE/static/docs/<id>.pdf
```

In the browser, open **Context view** (top right) to see the filled prompt (with the
retrieved chunks inside `{context}`), the sources, and token usage per turn.

## API

All assistant routes are under `/api/assistants`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/assistants` | List assistants (summaries) |
| `POST` | `/api/assistants` | Create assistant (+ create its collection) |
| `GET` | `/api/assistants/{id}` | Full record (prompts, collection, documents[]) |
| `DELETE` | `/api/assistants/{id}` | Delete record + its `/static` files (Chroma collection orphaned — see limitations) |
| `POST` | `/api/assistants/{id}/documents` | Upload → convert → chunk → insert (de-dups identical content) |
| `POST` | `/api/assistants/{id}/chat/stream` | Grounded, streaming answer (SSE); also takes `/topk`, `/threshold` |

### `POST /api/assistants` — `multipart/form-data`
Fields: `name`, `system_prompt`, `prompt_template` (must contain `{context}` and
`{user_input}`). → `201` with the assistant **summary**:
```json
{"id":"...","name":"...","collection":"assistant_...","document_count":0,"chunk_count":0,"created_at":"..."}
```

### `POST /api/assistants/{id}/documents` — `multipart/form-data`
Field: `document` (file). Re-uploading **identical content** (same SHA-256) for an
assistant is detected and skipped (`"deduplicated": true`), so chunks are never
duplicated. → `201`:
```json
{
  "document": {
    "doc_id": "...", "name": "handbook.md", "title": "handbook",
    "doc_url": "/static/docs/<id>.md", "md_url": "/static/docs/<id>.distilled.md",
    "chars": 910, "chunks": 6,
    "chunking_strategy": "paragraphs(per_chunk=1,heading<=2,max_chars=800)",
    "content_hash": "<sha256>", "ingested_at": "2026-09-09T12:00:40+00:00"
  },
  "collection_total": 6,
  "assistant": { "id": "...", "document_count": 1, "chunk_count": 6, ... },
  "deduplicated": false
}
```
Errors: `422` (empty / >`MAX_DOC_BYTES`), `502` (conversion or insertion failed).


### `POST /api/assistants/{id}/chat/stream` — JSON `{"message": "..."}` → SSE
Events:
- `{"type":"delta","content":"..."}` — a piece of the answer (repeated)
- `{"type":"done","payload_sent":{...},"usage":{...},"sources":[...]}` — final
- `{"type":"error","detail":"..."}` — the LLM failed mid-stream

`usage` = `{"prompt_tokens","completion_tokens","total_tokens"}`.
Each `sources[]` item:
```json
{
  "title": "handbook", "source": "<doc_id>",
  "doc_url": "/static/docs/<id>.md", "md_url": "/static/docs/<id>.distilled.md",
  "chunk_number": 1, "chunking_strategy": "paragraphs(...)",
  "similarity": 0.8021, "snippet": "## Pallet Pup ..."
}
```
When nothing passes the threshold, the LLM is **not** called: a single delta carries
the honest "I don't know", `sources` is `[]`, `usage` is all zeros, and
`payload_sent.note` explains why.

**In-chat runtime overrides** (per assistant, in-memory; reset on restart): sending
`/topk <int>` or `/threshold <number|off>` as the message changes the retrieval
top-K / similarity threshold from that point on, and returns a short confirmation
(e.g. `top_k set to 5`) as a one-shot reply — it is **not** sent to the LLM. They
override the config defaults for the rest of that assistant's conversation.


### Error codes
`404` unknown assistant · `422` invalid input (template missing a placeholder, empty
or oversized upload) · `502` conversion/insertion or LLM failure.

## Configuration (`backend/config.py`)

One file, env-overridable (see `.env.example`). No magic numbers in the logic.

| Key | Env var | Default | Meaning |
|---|---|---|---|
| `embed_model` | `EMBED_MODEL` | `nomic-embed-text` | Embeddings model (must match across insert & query) |
| `collection_metric` | `COLLECTION_METRIC` | `cosine` | `cosine` (higher=closer) or `euclidean` |
| `collection_persist_path` | `COLLECTION_PERSIST_PATH` | `data/collections-store` | Where collections live on disk |
| `assistants_file` | `ASSISTANTS_FILE` | `data/assistants.json` | Week-1 JSON persistence |
| `static_dir` | `STATIC_DIR` | `data/static` | On-disk dir served at `static_url_prefix` |
| `static_url_prefix` | `STATIC_URL_PREFIX` | `/static` | URL prefix for stored originals + `.md` |
| `max_doc_bytes` | `MAX_DOC_BYTES` | `102400` | Upload size cap |
| `chunk_strategy` | `CHUNK_STRATEGY` | `paragraphs` | Selector key (see `chunking._CHUNKERS`) |
| `paragraphs_per_chunk` | `PARAGRAPHS_PER_CHUNK` | `1` | Paragraphs grouped into one chunk |
| `chunk_size_chars` | `CHUNK_SIZE_CHARS` | `800` | Soft max chunk size (a paragraph is never cut) |
| `chunk_overlap_chars` | `CHUNK_OVERLAP_CHARS` | `100` | Reserved for future char-window strategies |
| `heading_level` | `HEADING_LEVEL` | `2` | Headings at level ≤ this delimit a chunk |
| `retrieval_top_k` | `RETRIEVAL_TOP_K` | `4` | Nearest chunks pulled per turn |
| `similarity_threshold` | `SIMILARITY_THRESHOLD` | `0.5` | Min cosine similarity; empty = off |
| `no_hits_message` | `NO_HITS_MESSAGE` | (honest "I don't know") | Reply when nothing passes the threshold |

Chat-model config (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`) is reused from
exercise1 and read in `backend/llm.py`. `similarity_threshold=0.5` is a starting
point for `nomic-embed-text` (relevant ≈0.50–0.70, unrelated ≈0.40–0.50); calibrate
to your model/corpus.

## Project layout

```
pyproject.toml          uv project; collections-manager + markitdown[pdf,docx,pptx]
Dockerfile              app image (python:3.12-slim + uv); does NOT bake collections-manager
docker-entrypoint.sh    runs `uv sync` at start (resolves the mounted path dep), then uvicorn
docker-compose.yml      mounts ~/ludo-engsoft at /workspace/ludo-engsoft; data/uv/venv volumes
.env / .env.example     LLM + embeddings config, retrieval/chunking knobs
backend/
  config.py     ───────► the RAG config file (all tunables, env-overridable)
  llm.py        ───────► OpenAI client (chat + embeddings) + SSE stream_chat(done_extra=sources)
  chunking.py   ───────► chunk_by_paragraphs + selector (add a strategy = add a fn + register)
  rag.py        ───────► the ONLY module importing collections_manager:
                          get_collection, insert_chunks, retrieve(top_k+threshold),
                          format_context(provenance), build_sources
  ingest.py     ───────► markitdown convert + store original & .md under /static + chunk + insert
  assistants.py ───────► JSON persistence + routes (create→collection, upload→ingest, chat→retrieve)
  main.py       ───────► FastAPI app: serves frontend, mounts /static, mounts the router
frontend/
  index.html / app.js / styles.css   create+list, upload, streaming chat, clickable sources, usage
data/ (runtime, gitignored)
  assistants.json        assistant records (id, prompts, collection, documents[])
  collections-store/     persisted Chroma collections, one per assistant
  static/docs/           uploaded originals + their .md distillations (served at /static)
```

## How it flows (ingestion & retrieval)

```
INGESTION — POST /api/assistants/{id}/documents   (multipart file)
  upload bytes
    └─ sha256(raw) already in this assistant's documents[]?
         ├─ yes ─► SKIP, return existing doc meta  {"deduplicated": true}
         └─ no  ─► ingest.py
              1. store original      → data/static/docs/<doc_id><ext>   (served at /static)
              2. markitdown.convert  → markdown (+ title)          [pdf/docx/pptx/html/txt/md]
              3. store distillation  → data/static/docs/<doc_id>.md
              4. chunking.chunk_document(markdown, settings)
                   paragraph chunks; cut at headings ≤ heading_level; group
                   paragraphs_per_chunk; soft cap chunk_size_chars (never splits a paragraph)
              5. rag.insert_chunks → collections_manager.insert(chunk, metadata) per chunk
                   metadata: source, title, doc_url, md_url,
                             chunk_number, chunking_strategy, ingested_at
    └─ append doc meta (incl. content_hash) to data/assistants.json

RETRIEVAL — POST /api/assistants/{id}/chat/stream   {"message": "..."}
  message
    └─ starts with /topk or /threshold ?
         ├─ yes ─► update in-memory override for this assistant,
         │         reply one-shot confirmation ("top_k set to 5"), NO LLM call
         └─ no  ─► effective top_k + threshold = (override else config default)
              1. rag.retrieve → collections_manager.query(top_k, threshold)
              2. zero hits ?
                   ├─ yes ─► honest "I don't know", sources [], usage 0, NO LLM call
                   └─ no  ─► rag.format_context (chunks + provenance) → fill {context}/{user_input}
              3. llm.stream_chat → SSE delta… → done{payload_sent, usage, sources[]}
                   sources[] = {title, doc_url (/static), md_url, chunk_number, similarity, snippet}

DELETE — DELETE /api/assistants/{id}
  remove record  +  ingest.remove_stored_files(doc_url, md_url)  → /static files gone
  collections_manager has no delete() → Chroma collection left orphaned (logged as a warning)
```

## Example (real output)

`POST /api/assistants/{id}/chat/stream` with `{"message":"How fast is the Pallet Pup and how much can it carry?"}`:

```
ANSWER: The Pallet Pup has a top speed of 2.4 meters per second and can carry up to
        18 kilograms of groceries in a single trip.
USAGE : {"prompt_tokens": 290, "completion_tokens": 34, "total_tokens": 324}
SOURCES:
  - handbook · chunk 1 · sim 0.8021 · /static/docs/7dc9...803.md
  - quickcard · chunk 0 · sim 0.745  · /static/docs/2e6e...7cc.pdf
  - handbook · chunk 2 · sim 0.7083 · /static/docs/7dc9...803.md
  - handbook · chunk 3 · sim 0.6607 · /static/docs/7dc9...803.md
```

Out-of-context question (`"Who wrote Hamlet?"`) → nothing passes the threshold:

```
ANSWER: I don't know — none of the uploaded documents contain information relevant
        to that question (no retrieved chunk met the similarity threshold).
USAGE : {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
SOURCES: (none)   payload_sent.note: "no retrieved chunk met the similarity threshold; the LLM was not called"
```

## Known limitations / deferred

- **Deletion leaves the collection orphaned.** Deleting an assistant removes its
  record and its `/static` files (original + markdown distillation), but
  `collections-manager` exposes no `delete()`, so the collection's vectors **remain
  in ChromaDB storage** under `data/collections-store/`. We do **not** touch ChromaDB
  directly to work around this; the deletion logs a warning naming the orphaned
  collection. (A real fix belongs in the abstraction layer, not the app.)
- **Char-window / overlap strategy** — `chunk_overlap_chars` is reserved for a future
  `chunk_by_chars` strategy (add it to `chunking._CHUNKERS`). Deferred.
- **OCR for scanned PDFs** — image-only PDFs need an OCR model, not markitdown. Deferred.


## Credits

Built on the course's `collections-manager` utility — © Marc Alier i Forment (UPC),
BSC Agents Course, CC BY-NC-SA 4.0.
