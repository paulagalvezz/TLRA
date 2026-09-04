# EASY-CHATGPT

A small chat web app backed by a local LLM (Ollama) through the OpenAI-compatible API.

Features, built in three incremental versions (all shipped in the same app):

- **Chat** with markdown rendering (`POST /chat`)
- **Streaming** replies, token by token, via Server-Sent Events (`POST /chat/stream`)
- **Vision**: attach images to your messages (base64) and let a vision-capable model describe them
- **Context view**: side panel showing the exact payload sent to the LLM and token usage per turn

## Requirements

- Docker + Docker Compose
- A running [Ollama](https://ollama.com) instance with at least one model pulled, e.g.:

```bash
ollama pull qwen2.5vl:7b    # text + vision
```

## Configuration

Copy the example and edit `.env`:

```bash
cp .env.example .env
```

| Variable | Description | Default |
|---|---|---|
| `LLM_BASE_URL` | OpenAI-compatible endpoint of your LLM. Use `http://host.docker.internal:11434/v1` from Docker, or the LAN IP of the Ollama host, e.g. `http://172.22.240.1:11434/v1` | - |
| `LLM_API_KEY` | API key (Ollama accepts anything, e.g. `ollama`) | - |
| `LLM_MODEL` | Model name. Must be vision-capable if you want to attach images | `qwen2.5vl:7b` |
| `LLM_VISION` | Optional. `true`/`false` to force vision capability on/off. When unset it is auto-detected from Ollama's model metadata | auto |

## Run

```bash
docker compose up -d --build
```

Open **http://localhost:6663** in your browser.

Stop with `docker compose down`. Rebuild after code changes with `docker compose up -d --build`.

> Port history: v1 ran on 6661, v2 (streaming) on 6662, v3 (vision) on 6663.
> Each version kept the previous endpoints, so the current image exposes everything on 6663.

## Using the app

1. **Chat** - type a message and press Enter (or Send). The reply streams in token by token and is rendered as markdown.
2. **Attach an image** - click the paperclip button or drag & drop an image onto the input bar. A preview appears with an `x` to remove it. Images are limited to 5 MB each; multiple images per message are allowed. Once sent, the image is shown inline in the conversation.
3. **Context view** - click *Context view* (top right) to open the side panel. Every turn shows the exact payload sent to the model and its token usage (`prompt / completion / total`). Attached images appear as `[image attached, X KB]` placeholders instead of the full base64 blob. Note: with Ollama, usage arrives only when the stream finishes, and image tokens are counted inside `prompt_tokens`.
4. **Vision on a text-only model** - if you attach an image while `LLM_MODEL` is not vision-capable, the app rejects the request with a clear error instead of silently ignoring the image.

## API

Both endpoints take the same JSON body:

```json
{
  "messages": [
    {"role": "user", "content": "What is in this image?",
     "images": ["data:image/png;base64,..."]}
  ]
}
```

- `role`: `system`, `user` or `assistant`
- `images`: optional list of base64 data URLs, only on `user` messages

### `POST /chat` - full response

```bash
curl -s http://localhost:6663/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Say hi"}]}'
```

Returns `{"reply", "payload_sent", "usage"}`.

### `POST /chat/stream` - Server-Sent Events

```bash
curl -N http://localhost:6663/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Say hi"}]}'
```

Emits one event per chunk:

- `{"type":"delta","content":"..."}` - a piece of the reply
- `{"type":"done","payload_sent":{...},"usage":{...}}` - final event with usage
- `{"type":"error","detail":"..."}` - the LLM failed mid-stream

### Error codes

| Code | Meaning |
|---|---|
| 400 | Image attached but the model is not vision-capable |
| 422 | Invalid request body (bad image data URL, >5 MB, invalid base64) |
| 502 | The LLM request failed (is Ollama running?) |

## Project layout

```
backend/main.py      FastAPI app: static serving, /chat, /chat/stream
frontend/            Plain HTML/CSS/JS, no build step (marked.js from CDN)
Dockerfile           python:3.12-slim + uvicorn
docker-compose.yml   Exposes the app on port 6663
.env.example         Template for .env
```

## Troubleshooting

- **Nothing happens when sending a message / 502** - check that `LLM_BASE_URL` is reachable *from inside the container*. `host.docker.internal` works on Docker Desktop; on Linux use the host's LAN/bridge IP.
- **First reply is slow** - Ollama loads the model into memory on the first request.
- **Replies are not streaming** - make sure you are hitting `/chat/stream`; some proxies buffer SSE responses (the app sets `X-Accel-Buffering: no`).
- **"not vision-capable" error** - pull a vision model (`ollama pull qwen2.5vl:7b`) and set it in `LLM_MODEL`, or force it with `LLM_VISION=true`.
