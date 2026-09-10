# EASY-RAG

A **dynamic** RAG web app: create assistants, upload documents into each
assistant's own embeddings collection, and chat — every answer is grounded in the
chunks retrieved from that collection.

This is `week2/exercise1` (static RAG: the *whole* document pasted into the prompt
every turn) grown into dynamic RAG (only the **retrieved** chunks ride along). It
is, roughly, the course's `simple-dynamic-rag` tool turned into a web app.

> **First-pass scope — skeleton + wiring only.** The goal here is to prove the
> pipeline end to end: *upload → insert into the right collection → question →
> retrieve from that collection → grounded answer*. Sophisticated logic is
> deliberately left as `TODO(next pass)` (see the bottom of this file).

## The one rule

The app talks to the embeddings database **only** through the course's
[`collections-manager`](../../../ludo-engsoft/week-02/collections-manager)
abstraction layer (`create_collection` / `insert` / `query`) — never to ChromaDB
directly. In this project, exactly **one** module imports it: `backend/rag.py`.

## Setup

Requires [`uv`](https://docs.astral.sh/uv/) and a running [Ollama](https://ollama.com)
with a chat model and an embeddings model:

```bash
ollama pull qwen2.5vl:7b        # chat
ollama pull nomic-embed-text    # embeddings
```

`collections-manager` is a **path dependency** (declared in `pyproject.toml`,
just like `simple-dynamic-rag` does). The default path assumes the course repo
sits at `~/ludo-engsoft`; if yours is elsewhere, edit the path under
`[tool.uv.sources]` in `pyproject.toml`.

```bash
cd week2/easy-rag
cp .env.example .env       # then adjust LLM_BASE_URL if Ollama isn't at that IP
uv sync                    # creates .venv, installs deps + editable collections-manager
```

## Run

```bash
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8123 --reload
```

Open **http://127.0.0.1:8123**. Runtime data (assistants JSON + the persisted
collections) is written under `data/` and is gitignored.

> No Docker in this pass: a path dependency to a sibling repo doesn't fit a
> self-contained image build. Run locally with `uv`, like `simple-dynamic-rag`.

## Manual end-to-end test

With the server running (`BASE=http://127.0.0.1:8123`):

```bash
# 1) Create an assistant — this also creates its collection.
curl -s -X POST $BASE/api/assistants \
  -F name='Acme Robots' \
  -F system_prompt='Use only the information in the context below to answer. If the answer is not in the context, say that you do not know.' \
  -F prompt_template='Answer the question using only the context below.

Context:
{context}

Question: {user_input}'
# -> {"id":"<AID>","collection":"assistant_<AID>","document_count":0,...}

# 2) Upload a document — trivially split (whole text = 1 chunk) and inserted.
printf '# Pallet Pup\n\nThe Pallet Pup top speed is 2.4 m/s and it carries 18 kg.\n' > robots.md
curl -s -X POST $BASE/api/assistants/<AID>/documents -F document=@robots.md
# -> {"document":{...,"chunks":1},"collection_total":1,...}

# 3) Ask a question — the message is the query; retrieved chunks fill {context}.
curl -sN -X POST $BASE/api/assistants/<AID>/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"message":"How fast is the Pallet Pup?"}'
# -> SSE: {"type":"delta",...} ... then {"type":"done","payload_sent":{...},"usage":{...}}
```

The final `done` event's `payload_sent` shows the **retrieved chunk inside
`{context}`** — that is the retrieval step made visible. In the browser, open
**Context view** (top right) to see the same payload + token usage per turn.

## Configuration (`backend/config.py`)

All tunable RAG parameters live in one file (env-overridable; see `.env.example`).
Not all are used yet — they exist from the start so there's a single place to tune.

| Setting | Env var | Default | Used now? |
|---|---|---|---|
| Embeddings model | `EMBED_MODEL` | `nomic-embed-text` | yes |
| Collection metric | `COLLECTION_METRIC` | `cosine` | yes |
| Persist path | `COLLECTION_PERSIST_PATH` | `data/collections-store` | yes |
| Assistants file | `ASSISTANTS_FILE` | `data/assistants.json` | yes |
| Max upload bytes | `MAX_DOC_BYTES` | `102400` | yes |
| Retrieval top-K | `RETRIEVAL_TOP_K` | `4` | **yes** |
| Similarity threshold | `SIMILARITY_THRESHOLD` | *(empty = off)* | wired, disabled |
| Chunk strategy | `CHUNK_STRATEGY` | `paragraphs` | placeholder |
| Paragraphs per chunk | `PARAGRAPHS_PER_CHUNK` | `1` | placeholder |
| Chunk size (chars) | `CHUNK_SIZE_CHARS` | `800` | placeholder |
| Chunk overlap (chars) | `CHUNK_OVERLAP_CHARS` | `100` | placeholder |
| Heading level | `HEADING_LEVEL` | `2` | placeholder |

Chat-model config (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`) is reused from
exercise1 and read in `backend/llm.py`.

## Project layout & how the pieces relate

```
pyproject.toml            uv project; collections-manager as a PATH dependency
.env / .env.example       LLM endpoint + model, EMBED_MODEL, optional RAG_* overrides
backend/
  config.py    ──────────► the RAG config file (all tunables, env-overridable)
  llm.py       ──────────► OpenAI client (chat) + AsyncOpenAI + SSE streaming helper
  rag.py       ──────────► the ONLY module importing collections_manager:
                            embeddings fn (reuses llm.client), get_collection(),
                            split_document() [trivial + TODO], ingest_document(),
                            retrieve(), format_context()
  assistants.py ─────────► JSON persistence (reused from exercise1) + routes:
                            create (→ rag.get_collection), list, get, delete,
                            upload (→ rag.ingest_document), chat (→ rag.retrieve)
  main.py      ──────────► FastAPI app: serves frontend/, mounts assistants.router
frontend/
  index.html / app.js / styles.css   create+list assistants, upload, streaming chat,
                                     context view (adapted from exercise1)
data/  (runtime, gitignored)
  assistants.json          assistant records (id, name, prompts, collection, documents[])
  collections-store/       persisted Chroma collections, one per assistant
```

Request flow:

```
CREATE  POST /api/assistants          assistants.py ─► rag.get_collection ─► create_collection()
UPLOAD  POST /api/assistants/{id}/documents
                                      assistants.py ─► rag.ingest_document ─► split (1 chunk) ─► insert()
CHAT    POST /api/assistants/{id}/chat/stream
        user message ─► rag.retrieve ─► query(top_k) ─► format_context ─► fill {context}/{user_input}
                     ─► llm.stream_chat ─► SSE deltas ─► done(payload_sent, usage)
```

Each assistant owns one collection named `assistant_<id>`; the assistant record
in `data/assistants.json` stores that name plus per-document metadata (name,
chars, chunks, ingested_at). The document **text lives in the collection**, not
in the JSON.

## Not implemented yet (next pass) — look for `TODO(next pass)`

- **markitdown conversion** — uploads are assumed UTF-8 text; pdf/docx/pptx/html
  → markdown is not wired (`assistants._read_upload`, `rag.split_document`).
- **Real chunking** — `rag.split_document` inserts the whole document as ONE
  trivial chunk. The intended **by-paragraph** strategy (using `paragraphs_per_chunk`,
  `heading_level`, `chunk_size_chars`, `chunk_overlap_chars`) is stubbed.
- **Rich metadata** — chunks carry only `source`/`title`/`ingested_at`; no
  `doc_url`/`md_url`/`chunking_strategy` yet.
- **Real similarity threshold** — `similarity_threshold` is wired into `query()`
  but defaults to off; it needs calibrating against the embeddings model + corpus.
- **Sources / citations UI** — context is fed in raw; no provenance shown to the user.
- **Collection cleanup** — `collections-manager` exposes no delete, so deleting an
  assistant leaves its on-disk collection orphaned.

## Credits

Built on the course's `collections-manager` utility — © Marc Alier i Forment
(UPC), BSC Agents Course, CC BY-NC-SA 4.0.
