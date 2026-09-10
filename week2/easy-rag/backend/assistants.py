"""Assistants: Week-1 JSON persistence + dynamic-RAG wiring.

Reuses exercise1's persistence pattern (a JSON file guarded by a lock, the same
fill_template / validation helpers) and grows it into dynamic RAG:

  * creating an assistant also creates its collection (rag.get_collection),
  * documents are uploaded, converted + chunked + inserted (ingest.ingest_upload),
  * chat retrieves from the collection, gates on the similarity threshold, and
    ships the provenance (sources) back alongside the answer.

This module never touches collections_manager / ChromaDB — it goes through
rag.py (the seam) and ingest.py (the pipeline).
"""
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import config, ingest, rag
from .llm import LLM_MODEL, sse, sse_response, stream_chat

router = APIRouter(prefix="/api/assistants", tags=["assistants"])

_settings = config.settings
DATA_FILE = _settings.assistants_file
MAX_DOC_BYTES = _settings.max_doc_bytes

PLACEHOLDERS = ("{context}", "{user_input}")
_PLACEHOLDER_RE = re.compile(r"\{context\}|\{user_input\}")
_NEWLINE_RE = re.compile(r"\r\n|\r")

_lock = threading.Lock()


# --- JSON persistence (reused from exercise1) ------------------------------

def _load() -> dict:
    if not DATA_FILE.exists():
        return {"assistants": []}
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Cannot read {DATA_FILE.name}: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("assistants"), list):
        raise HTTPException(status_code=500, detail=f"{DATA_FILE.name} has an unexpected structure")
    return data


def _save(data: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, DATA_FILE)


def _find(data: dict, assistant_id: str) -> dict | None:
    return next(
        (record for record in data["assistants"] if record["id"] == assistant_id),
        None,
    )


def _validate_text_fields(name: str, system_prompt: str, prompt_template: str) -> tuple[str, str, str]:
    # Browsers normalize textarea newlines to \r\n on form submission; store \n.
    name = _NEWLINE_RE.sub("\n", name).strip()
    system_prompt = _NEWLINE_RE.sub("\n", system_prompt)
    prompt_template = _NEWLINE_RE.sub("\n", prompt_template)
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    if len(name) > 200:
        raise HTTPException(status_code=422, detail="name must be at most 200 characters")
    missing = [p for p in PLACEHOLDERS if p not in prompt_template]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"prompt_template must contain {' and '.join(missing)}",
        )
    return name, system_prompt, prompt_template


def fill_template(template: str, context: str, user_input: str) -> str:
    # Single pass so a document containing "{user_input}" (or a message
    # containing "{context}") is not substituted twice.
    replacements = {"{context}": context, "{user_input}": user_input}
    return _PLACEHOLDER_RE.sub(lambda match: replacements[match.group(0)], template)


def _summary(record: dict) -> dict:
    docs = record.get("documents", [])
    return {
        "id": record["id"],
        "name": record["name"],
        "collection": record["collection"],
        "document_count": len(docs),
        "chunk_count": sum(d.get("chunks", 0) for d in docs),
        "created_at": record["created_at"],
    }


# --- CRUD --------------------------------------------------------------------

@router.get("")
def list_assistants() -> dict:
    with _lock:
        data = _load()
    return {"assistants": [_summary(record) for record in data["assistants"]]}


@router.post("", status_code=201)
async def create_assistant(
    name: str = Form(),
    system_prompt: str = Form(),
    prompt_template: str = Form(),
) -> dict:
    name, system_prompt, prompt_template = _validate_text_fields(
        name, system_prompt, prompt_template
    )
    assistant_id = uuid.uuid4().hex
    # Creating an assistant creates its collection (get_or_create).
    await run_in_threadpool(rag.get_collection, assistant_id)
    record = {
        "id": assistant_id,
        "name": name,
        "system_prompt": system_prompt,
        "prompt_template": prompt_template,
        "collection": rag.collection_name(assistant_id),
        "documents": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _lock:
        data = _load()
        data["assistants"].append(record)
        _save(data)
    return _summary(record)


@router.get("/{assistant_id}")
def get_assistant(assistant_id: str) -> dict:
    with _lock:
        record = _find(_load(), assistant_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    return record


@router.delete("/{assistant_id}", status_code=204)
def delete_assistant(assistant_id: str) -> None:
    with _lock:
        data = _load()
        record = _find(data, assistant_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Assistant not found")
        data["assistants"].remove(record)
        _save(data)
    # NOTE: collections_manager exposes no delete, so the on-disk collection (and
    # the document files under /static) are left orphaned. TODO: cleanup pass.


# --- Upload / ingestion ------------------------------------------------------

@router.post("/{assistant_id}/documents", status_code=201)
async def upload_document(assistant_id: str, document: UploadFile = File()) -> dict:
    with _lock:
        record = _find(_load(), assistant_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Assistant not found")

    raw = await document.read()
    if len(raw) > MAX_DOC_BYTES:
        raise HTTPException(
            status_code=422, detail=f"document exceeds the {MAX_DOC_BYTES // 1024} KB limit"
        )
    if not raw.strip():
        raise HTTPException(status_code=422, detail="document is empty")
    filename = document.filename or "document.txt"

    # markitdown conversion + chunking + insertion (blocking) off the event loop.
    result = await run_in_threadpool(ingest.ingest_upload, assistant_id, filename, raw)
    if not result.get("ok"):
        detail = result.get("error") or (result.get("errors") or ["ingestion failed"])
        raise HTTPException(status_code=502, detail=str(detail))

    doc_meta = {
        "doc_id": result["doc_id"],
        "name": result["original_name"],
        "title": result["title"],
        "doc_url": result["doc_url"],
        "md_url": result["md_url"],
        "chars": result["markdown_chars"],
        "chunks": result["inserted"],
        "chunking_strategy": result["chunking_strategy"],
        "ingested_at": result["ingested_at"],
    }
    with _lock:
        data = _load()
        record = _find(data, assistant_id)
        if record is not None:
            record.setdefault("documents", []).append(doc_meta)
            _save(data)
        summary = _summary(record) if record is not None else None
    return {
        "document": doc_meta,
        "collection_total": result["collection_total"],
        "assistant": summary,
    }


# --- Chat --------------------------------------------------------------------

class AssistantChatRequest(BaseModel):
    message: str = Field(min_length=1)


async def _no_answer_stream(message: str):
    """SSE reply used when retrieval finds nothing over the threshold.

    We do NOT call the LLM and do NOT feed it weak/irrelevant chunks: the honest
    "I don't know" is returned directly, with empty sources and zero usage.
    """
    yield sse({"type": "delta", "content": message})
    yield sse({
        "type": "done",
        "payload_sent": {
            "model": LLM_MODEL,
            "messages": [],
            "note": "no retrieved chunk met the similarity threshold; the LLM was not called",
        },
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "sources": [],
    })


@router.post("/{assistant_id}/chat/stream")
async def chat_with_assistant(assistant_id: str, request: AssistantChatRequest):
    with _lock:
        record = _find(_load(), assistant_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Assistant not found")

    # Dynamic augmentation: the user message IS the query into the collection,
    # gated by top_k + similarity threshold (both from config).
    hits = await run_in_threadpool(rag.retrieve, assistant_id, request.message)
    if not hits:
        return sse_response(_no_answer_stream(_settings.no_hits_message))

    context = rag.format_context(hits)
    sources = rag.build_sources(hits)
    filled = fill_template(record["prompt_template"], context, request.message)
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": record["system_prompt"]},
            {"role": "user", "content": filled},
        ],
    }
    # Sources ride along in the final `done` event (requirement: provenance + usage).
    return sse_response(stream_chat(payload, done_extra={"sources": sources}))
