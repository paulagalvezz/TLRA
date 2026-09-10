#!/bin/sh
# EASY-RAG container entrypoint.
#
# `uv sync` runs HERE (not at build time) because it must resolve the
# collections-manager path dependency, which docker-compose mounts at runtime at
# ../../../ludo-engsoft (== /workspace/ludo-engsoft). The first start downloads
# dependencies (needs network); the uv cache + .venv volumes make later starts fast.
set -e

echo "[easy-rag] resolving dependencies (uv sync) ..."
if ! uv sync; then
    echo "[easy-rag] ERROR: uv sync failed." >&2
    echo "[easy-rag] Is the course repo mounted at /workspace/ludo-engsoft ?" >&2
    echo "[easy-rag] pyproject.toml expects collections-manager at ../../../ludo-engsoft/week-02/collections-manager" >&2
    exit 1
fi

echo "[easy-rag] starting uvicorn on 0.0.0.0:8000 ..."
exec uv run --no-sync uvicorn backend.main:app --host 0.0.0.0 --port 8000
