import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .llm import LLM_MODEL, sse_response, stream_chat

router = APIRouter(prefix="/api/assistants", tags=["assistants"])

DATA_FILE = Path(
    os.getenv("ASSISTANTS_FILE")
    or Path(__file__).resolve().parent.parent / "data" / "assistants.json"
)
MAX_DOC_BYTES = int(os.getenv("MAX_DOC_BYTES", str(100 * 1024)))

PLACEHOLDERS = ("{context}", "{user_input}")
_PLACEHOLDER_RE = re.compile(r"\{context\}|\{user_input\}")

_lock = threading.Lock()


def _load() -> dict:
    if not DATA_FILE.exists():
        return {"assistants": []}
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Cannot read {DATA_FILE.name}: {exc}"
        )
    if not isinstance(data, dict) or not isinstance(data.get("assistants"), list):
        raise HTTPException(
            status_code=500, detail=f"{DATA_FILE.name} has an unexpected structure"
        )
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


def _summary(record: dict) -> dict:
    return {
        "id": record["id"],
        "name": record["name"],
        "document_name": record["document_name"],
        "document_chars": len(record["document_text"]),
        "created_at": record["created_at"],
    }


def _validate_text_fields(name: str, system_prompt: str, prompt_template: str) -> tuple[str, str, str]:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    if len(name) > 200:
        raise HTTPException(status_code=422, detail="name must be at most 200 characters")
    missing = [placeholder for placeholder in PLACEHOLDERS if placeholder not in prompt_template]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"prompt_template must contain {' and '.join(missing)}",
        )
    return name, system_prompt, prompt_template


async def _read_document(document: UploadFile) -> tuple[str, str]:
    raw = await document.read()
    if len(raw) > MAX_DOC_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"document exceeds the {MAX_DOC_BYTES // 1024} KB limit",
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="document must be a UTF-8 text file")
    if not text.strip():
        raise HTTPException(status_code=422, detail="document is empty")
    return document.filename or "document.txt", text


def fill_template(template: str, context: str, user_input: str) -> str:
    # Single pass so a document containing "{user_input}" (or a message
    # containing "{context}") is not substituted twice.
    replacements = {"{context}": context, "{user_input}": user_input}
    return _PLACEHOLDER_RE.sub(lambda match: replacements[match.group(0)], template)


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
    document: UploadFile = File(),
) -> dict:
    name, system_prompt, prompt_template = _validate_text_fields(
        name, system_prompt, prompt_template
    )
    document_name, document_text = await _read_document(document)
    record = {
        "id": uuid.uuid4().hex,
        "name": name,
        "system_prompt": system_prompt,
        "prompt_template": prompt_template,
        "document_name": document_name,
        "document_text": document_text,
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


@router.put("/{assistant_id}")
async def update_assistant(
    assistant_id: str,
    name: str = Form(),
    system_prompt: str = Form(),
    prompt_template: str = Form(),
    document: UploadFile | None = File(None),
) -> dict:
    name, system_prompt, prompt_template = _validate_text_fields(
        name, system_prompt, prompt_template
    )
    new_document = await _read_document(document) if document is not None else None
    with _lock:
        data = _load()
        record = _find(data, assistant_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Assistant not found")
        record["name"] = name
        record["system_prompt"] = system_prompt
        record["prompt_template"] = prompt_template
        if new_document is not None:
            record["document_name"], record["document_text"] = new_document
        _save(data)
    return _summary(record)


@router.delete("/{assistant_id}", status_code=204)
def delete_assistant(assistant_id: str) -> None:
    with _lock:
        data = _load()
        record = _find(data, assistant_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Assistant not found")
        data["assistants"].remove(record)
        _save(data)


class AssistantChatRequest(BaseModel):
    message: str = Field(min_length=1)


@router.post("/{assistant_id}/chat/stream")
async def chat_with_assistant(assistant_id: str, request: AssistantChatRequest):
    with _lock:
        record = _find(_load(), assistant_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Assistant not found")

    filled = fill_template(
        record["prompt_template"], record["document_text"], request.message
    )
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": record["system_prompt"]},
            {"role": "user", "content": filled},
        ],
    }
    return sse_response(stream_chat(payload))
