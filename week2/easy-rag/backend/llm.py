"""LLM client + shared SSE streaming helper (reused from Week-1 exercise1)."""
import asyncio
import json
import os

from dotenv import load_dotenv
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI, OpenAI

load_dotenv()

LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL")

if not all([LLM_BASE_URL, LLM_API_KEY, LLM_MODEL]):
    raise RuntimeError(
        "Missing configuration. Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in .env"
    )

# Sync client is also used for embeddings (see rag.py); async client for streaming chat.
client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
async_client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def sse_response(events) -> StreamingResponse:
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def stream_chat(payload: dict, payload_sent: dict | None = None, done_extra: dict | None = None):
    """Yield SSE events for a streaming chat completion.

    `payload_sent` is what the final `done` event reports back to the client;
    pass a redacted copy when the payload contains something you don't want to
    echo verbatim. Defaults to the payload itself.

    `done_extra` is merged into the final `done` event — EASY-RAG uses it to ship
    the retrieved `sources` (provenance) alongside the answer and token usage.
    """
    if payload_sent is None:
        payload_sent = payload

    try:
        stream = await async_client.chat.completions.create(
            **payload,
            stream=True,
            stream_options={"include_usage": True},
        )
    except Exception as exc:
        yield sse({"type": "error", "detail": f"LLM request failed: {exc}"})
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
                    yield sse({"type": "delta", "content": delta})
        done_event = {"type": "done", "payload_sent": payload_sent, "usage": usage}
        if done_extra:
            done_event.update(done_extra)
        yield sse(done_event)
    except asyncio.CancelledError:
        # The browser went away; let the cancellation propagate so the
        # upstream LLM request is aborted instead of running orphaned.
        raise
    except Exception as exc:
        yield sse({"type": "error", "detail": f"LLM stream failed: {exc}"})
    finally:
        try:
            await stream.close()
        except Exception:
            pass
