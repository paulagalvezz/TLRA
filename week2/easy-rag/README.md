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


### Error codes
`404` unknown assistant · `422` invalid input (template missing a placeholder, empty
or oversized upload) · `502` conversion/insertion or LLM failure.


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


## Credits

Built on the course's `collections-manager` utility — © Marc Alier i Forment (UPC),
BSC Agents Course, CC BY-NC-SA 4.0.
