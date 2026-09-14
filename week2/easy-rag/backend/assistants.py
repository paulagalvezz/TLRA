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
import hashlib
import json
import logging
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
logger = logging.getLogger("easy-rag")

_settings = config.settings
DATA_FILE = _settings.assistants_file
MAX_DOC_BYTES = _settings.max_doc_bytes

PLACEHOLDERS = ("{context}", "{user_input}")
_PLACEHOLDER_RE = re.compile(r"\{context\}|\{user_input\}")
_NEWLINE_RE = re.compile(r"\r\n|\r")

_lock = threading.Lock()

# Per-assistant retrieval overrides set via in-chat /topk and /threshold commands.
# In-memory only (not persisted); cleared on restart and on assistant deletion.
_runtime_overrides: dict[str, dict] = {}



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
    # Remove the stored originals + markdown distillations under /static.
    removed = 0
    for doc in record.get("documents", []):
        removed += ingest.remove_stored_files(doc.get("doc_url"), doc.get("md_url"))
    # Drop the cached handle and any in-memory runtime overrides.
    rag.forget_collection(assistant_id)
    _runtime_overrides.pop(assistant_id, None)
    # collections_manager exposes no delete and we never touch ChromaDB directly,
    # so the collection's vectors remain in storage — a known limitation. Log it.
    logger.warning(
        "Assistant %s deleted: removed %d static file(s); collection '%s' remains "
        "orphaned in ChromaDB storage (collections-manager exposes no delete()).",
        assistant_id, removed, record.get("collection"),
    )



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

    # De-dup on content: re-uploading identical bytes for this assistant must NOT
    # duplicate chunks. collections_manager exposes no delete, so we skip
    # re-inserting rather than replace — for identical content the two are
    # equivalent (the existing chunks already represent this exact document).
    content_hash = hashlib.sha256(raw).hexdigest()
    with _lock:
        record = _find(_load(), assistant_id)
        existing = next(
            (d for d in (record.get("documents", []) if record else [])
             if d.get("content_hash") == content_hash),
            None,
        )
        if existing is not None:
            return {
                "document": existing,
                "collection_total": sum(d.get("chunks", 0) for d in record.get("documents", [])),
                "assistant": _summary(record),
                "deduplicated": True,
            }

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
        "content_hash": content_hash,
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
        "deduplicated": False,
    }



# --- Chat --------------------------------------------------------------------

class AssistantChatRequest(BaseModel):
    message: str = Field(min_length=1)


def _instant_events(text: str, note: str) -> list[str]:
    """A one-shot SSE reply (single delta + done) that does NOT call the LLM.

    Used for the honest no-hits answer and for /topk, /threshold confirmations:
    empty sources, zero usage.
    """
    return [
        sse({"type": "delta", "content": text}),
        sse({
            "type": "done",
            "payload_sent": {"model": LLM_MODEL, "messages": [], "note": note},
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "sources": [],
        }),
    ]


def _apply_command(assistant_id: str, text: str) -> tuple[bool, str]:
    """Handle in-chat runtime overrides. Returns (is_command, reply_text).

      /topk <int>           set retrieval top-K for this assistant (in-memory)
      /threshold <num|off>  set min similarity, or disable filtering (in-memory)

    These change retrieval from this point on in the conversation; they are NOT
    treated as a query and never reach the LLM.
    """
    parts = text.split()
    if not parts:
        return False, ""
    cmd = parts[0].lower()
    if cmd == "/topk":
        if len(parts) != 2:
            return True, "usage: /topk <positive integer>"
        try:
            k = int(parts[1])
            if k <= 0:
                raise ValueError
        except ValueError:
            return True, "usage: /topk <positive integer>"
        _runtime_overrides.setdefault(assistant_id, {})["top_k"] = k
        return True, f"top_k set to {k}"
    if cmd == "/threshold":
        if len(parts) != 2:
            return True, "usage: /threshold <number 0..1> | off"
        arg = parts[1].lower()
        if arg in ("off", "none"):
            _runtime_overrides.setdefault(assistant_id, {})["threshold"] = None
            return True, "similarity threshold disabled"
        try:
            t = float(arg)
        except ValueError:
            return True, "usage: /threshold <number 0..1> | off"
        _runtime_overrides.setdefault(assistant_id, {})["threshold"] = t
        return True, f"similarity threshold set to {t}"
    return False, ""


@router.post("/{assistant_id}/chat/stream")
async def chat_with_assistant(assistant_id: str, request: AssistantChatRequest):
    with _lock:
        record = _find(_load(), assistant_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Assistant not found")

    text = request.message.strip()

    # In-chat runtime override commands are handled locally (not sent to the LLM).
    is_command, reply = _apply_command(assistant_id, text)
    if is_command:
        return sse_response(
            _instant_events(reply, "runtime override command; the LLM was not called")
        )

    # Effective retrieval knobs: per-assistant override, else the config default.
    override = _runtime_overrides.get(assistant_id, {})
    top_k = override.get("top_k", _settings.retrieval_top_k)
    threshold = override.get("threshold", _settings.similarity_threshold)

    # Dynamic augmentation: the user message IS the query into the collection.
    hits = await run_in_threadpool(
        rag.retrieve, assistant_id, text, top_k=top_k, threshold=threshold
    )
    if not hits:
        return sse_response(_instant_events(
            _settings.no_hits_message,
            "no retrieved chunk met the similarity threshold; the LLM was not called",
        ))

    context = rag.format_context(hits)
    sources = rag.build_sources(hits)
    filled = fill_template(record["prompt_template"], context, text)
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": record["system_prompt"]},
            {"role": "user", "content": filled},
        ],
    }
    # Sources ride along in the final `done` event (provenance + usage).
    return sse_response(stream_chat(payload, done_extra={"sources": sources}))

