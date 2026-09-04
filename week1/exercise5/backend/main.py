import asyncio
import base64
import binascii
import json
import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel, Field, field_validator

load_dotenv()

LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL")
LLM_VISION = os.getenv("LLM_VISION", "").strip().lower()

MAX_IMAGE_BYTES = 5 * 1024 * 1024

if not all([LLM_BASE_URL, LLM_API_KEY, LLM_MODEL]):
    raise RuntimeError(
        "Missing configuration. Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in .env"
    )

client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
async_client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="EASY-CHATGPT", version="3")

DATA_URL_RE = re.compile(r"^data:image/[\w.+-]+;base64,")


def _decoded_size(data_url: str) -> int:
    data = data_url.split(";base64,", 1)[1]
    return (len(data) * 3) // 4


def _detect_vision_support() -> bool:
    root = LLM_BASE_URL.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    try:
        resp = httpx.post(f"{root}/api/show", json={"name": LLM_MODEL}, timeout=10)
        resp.raise_for_status()
        model_info = resp.json().get("model_info", {})
        return any(".vision." in key for key in model_info)
    except Exception:
        # Cannot introspect (non-Ollama provider?); let the provider answer
        # and surface its own error rather than blocking the request.
        return True


_vision_support_cache: bool | None = None


def _model_supports_vision() -> bool:
    global _vision_support_cache
    if LLM_VISION in ("true", "false"):
        return LLM_VISION == "true"
    if _vision_support_cache is None:
        _vision_support_cache = _detect_vision_support()
    return _vision_support_cache


class Message(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = ""
    images: list[str] = Field(default_factory=list)

    @field_validator("images")
    @classmethod
    def validate_images(cls, images: list[str]) -> list[str]:
        for image in images:
            if not DATA_URL_RE.match(image):
                raise ValueError("images must be base64 data URLs (data:image/...;base64,...)")
            if _decoded_size(image) > MAX_IMAGE_BYTES:
                raise ValueError(f"image exceeds the {MAX_IMAGE_BYTES // (1024 * 1024)} MB limit")
            try:
                base64.b64decode(image.split(";base64,", 1)[1], validate=True)
            except (binascii.Error, ValueError):
                raise ValueError("image payload is not valid base64")
        return images


def _require_vision_support(messages: list[Message]) -> None:
    if any(message.images for message in messages) and not _model_supports_vision():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Model '{LLM_MODEL}' is not vision-capable. Remove the attached image, "
                "set LLM_MODEL to a vision model, or force it with LLM_VISION=true."
            ),
        )


def _to_openai_message(message: Message) -> dict:
    if message.role == "user" and message.images:
        content: list[dict] = [{"type": "text", "text": message.content}]
        content.extend(
            {"type": "image_url", "image_url": {"url": image}} for image in message.images
        )
        return {"role": message.role, "content": content}
    return {"role": message.role, "content": message.content}


def _redact_payload(payload: dict) -> dict:
    messages = []
    for message in payload["messages"]:
        content = message.get("content")
        if isinstance(content, list):
            content = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"[image attached, {max(1, _decoded_size(part['image_url']['url']) // 1024)} KB]"
                    },
                }
                if part.get("type") == "image_url"
                else part
                for part in content
            ]
            message = {**message, "content": content}
        messages.append(message)
    return {**payload, "messages": messages}


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1)


class ChatResponse(BaseModel):
    reply: str
    payload_sent: dict
    usage: dict


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/{filename}")
def static(filename: str) -> FileResponse:
    path = (FRONTEND_DIR / filename).resolve()
    if FRONTEND_DIR not in path.parents or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path)


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    _require_vision_support(request.messages)
    payload = {
        "model": LLM_MODEL,
        "messages": [_to_openai_message(message) for message in request.messages],
    }
    try:
        completion = client.chat.completions.create(**payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM request failed: {exc}")

    usage = completion.usage
    return ChatResponse(
        reply=completion.choices[0].message.content or "",
        payload_sent=_redact_payload(payload),
        usage={
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
        },
    )


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    _require_vision_support(request.messages)
    payload = {
        "model": LLM_MODEL,
        "messages": [_to_openai_message(message) for message in request.messages],
    }

    async def event_stream():
        try:
            stream = await async_client.chat.completions.create(
                **payload,
                stream=True,
                stream_options={"include_usage": True},
            )
        except Exception as exc:
            yield _sse({"type": "error", "detail": f"LLM request failed: {exc}"})
            return

        usage = {}
        try:
            async for chunk in stream:
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage:
                    usage = {
                        "prompt_tokens": getattr(chunk_usage, "prompt_tokens", 0),
                        "completion_tokens": getattr(chunk_usage, "completion_tokens", 0),
                        "total_tokens": getattr(chunk_usage, "total_tokens", 0),
                    }
                if chunk.choices:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        yield _sse({"type": "delta", "content": delta})
            yield _sse({"type": "done", "payload_sent": _redact_payload(payload), "usage": usage})
        except asyncio.CancelledError:
            # The browser went away; let the cancellation propagate so the
            # upstream LLM request is aborted instead of running orphaned.
            raise
        except Exception as exc:
            yield _sse({"type": "error", "detail": f"LLM stream failed: {exc}"})
        finally:
            try:
                await stream.close()
            except Exception:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
