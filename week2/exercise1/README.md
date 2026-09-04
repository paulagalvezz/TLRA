# EASY-CHATGPT

A small chat web app backed by a local LLM (Ollama) through the OpenAI-compatible API.

Features, built in four incremental versions (all shipped in the same app):

- **Chat** with markdown rendering (`POST /chat`)
- **Streaming** replies, token by token, via Server-Sent Events (`POST /chat/stream`)
- **Vision**: attach images to your messages (base64) and let a vision-capable model describe them
- **Context view**: side panel showing the exact payload sent to the LLM and token usage per turn
- **Static RAG assistants** (v4): named assistants with a system prompt, a prompt template (`{context}` + `{user_input}`) and one uploaded plain-text document. On every turn the *full* document is substituted into the template and sent to the model - no chunking, no retrieval, no embeddings. Assistants are persisted in a JSON file on disk.

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
| `ASSISTANTS_FILE` | Optional. Path of the JSON file where assistants are persisted | `data/assistants.json` |
| `MAX_DOC_BYTES` | Optional. Max size (bytes) of an assistant document | `102400` (100 KB) |

## Run

```bash
docker compose up -d --build
```

Open **http://localhost:6663** in your browser.

Stop with `docker compose down`. Rebuild after code changes with `docker compose up -d --build`.

> Port history: v1 ran on 6661, v2 (streaming) on 6662, v3 (vision) on 6663.
> Each version kept the previous endpoints, so the current image exposes everything on 6663.
> v4 (assistants) also runs on 6663.

## Using the app

1. **Chat** - type a message and press Enter (or Send). The reply streams in token by token and is rendered as markdown.
2. **Attach an image** - click the paperclip button or drag & drop an image onto the input bar. A preview appears with an `x` to remove it. Images are limited to 5 MB each; multiple images per message are allowed. Once sent, the image is shown inline in the conversation.
3. **Context view** - click *Context view* (top right) to open the side panel. Every turn shows the exact payload sent to the model and its token usage (`prompt / completion / total`). Attached images appear as `[image attached, X KB]` placeholders instead of the full base64 blob. Note: with Ollama, usage arrives only when the stream finishes, and image tokens are counted inside `prompt_tokens`.
4. **Vision on a text-only model** - if you attach an image while `LLM_MODEL` is not vision-capable, the app rejects the request with a clear error instead of silently ignoring the image.
5. **Assistants (static RAG)** - use the left sidebar. Click **+** to create an assistant: give it a name, a system prompt, a prompt template containing `{context}` and `{user_input}`, and upload one plain-text document. Selecting an assistant starts a fresh conversation; on every turn the *entire* document is substituted into `{context}` and your message into `{user_input}`, and that filled prompt is sent to the model. Open the **Context view** to watch the full file go out and the token usage climb on every single turn. **Plain chat** at the top of the sidebar restores the normal history-based chat. Note: assistants ignore images and conversation history by design (each turn is stateless).

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

### Assistants (static RAG)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/assistants` | List (id, name, document name + size) |
| `POST` | `/api/assistants` | Create. `multipart/form-data`: `name`, `system_prompt`, `prompt_template`, `document` (file) |
| `GET` | `/api/assistants/{id}` | Full record including document text |
| `PUT` | `/api/assistants/{id}` | Update; `document` optional (keep existing when omitted) |
| `DELETE` | `/api/assistants/{id}` | Delete |
| `POST` | `/api/assistants/{id}/chat/stream` | Body `{"message": "..."}`, SSE like `/chat/stream` |

Create example:

```bash
printf 'The answer is 42.' > doc.txt
curl -s http://localhost:6663/api/assistants \
  -F name=oracle \
  -F system_prompt='You are concise.' \
  -F prompt_template='Context: {context}
Question: {user_input}' \
  -F document=@doc.txt
```

Chat: the backend fills `{context}` with the full document and `{user_input}`
with your message, sends `[system_prompt, filled_template]` to the model, and
reports the filled prompt plus usage in the final `done` event.

### Error codes

| Code | Meaning |
|---|---|
| 400 | Image attached but the model is not vision-capable |
| 404 | Unknown assistant id |
| 422 | Invalid request body (bad image data URL, >5 MB, invalid base64; assistant template missing a placeholder, document empty / not UTF-8 / >100 KB) |
| 502 | The LLM request failed (is Ollama running?) |

## Project layout

```
backend/main.py        FastAPI app: static serving, /chat, /chat/stream
backend/llm.py         LLM client config + shared SSE streaming helper
backend/assistants.py  Assistant CRUD, template filling, JSON persistence
frontend/              Plain HTML/CSS/JS, no build step (marked.js from CDN)
data/assistants.json   Assistant storage (created at runtime, volume-mounted)
Dockerfile             python:3.12-slim + uvicorn
docker-compose.yml     Exposes the app on port 6663, mounts ./data
.env.example           Template for .env
```

## Troubleshooting

- **Nothing happens when sending a message / 502** - check that `LLM_BASE_URL` is reachable *from inside the container*. `host.docker.internal` works on Docker Desktop; on Linux use the host's LAN/bridge IP.
- **First reply is slow** - Ollama loads the model into memory on the first request.
- **Replies are not streaming** - make sure you are hitting `/chat/stream`; some proxies buffer SSE responses (the app sets `X-Accel-Buffering: no`).
- **"not vision-capable" error** - pull a vision model (`ollama pull qwen2.5vl:7b`) and set it in `LLM_MODEL`, or force it with `LLM_VISION=true`.
