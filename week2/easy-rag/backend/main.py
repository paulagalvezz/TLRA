"""EASY-RAG — FastAPI app.

Serves the frontend, mounts the assistants API, and serves uploaded documents
(originals + their markdown distillation) under /static so citations are linkable.
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import assistants, config

_settings = config.settings
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Ensure the static directory exists before mounting it.
_settings.static_dir.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="EASY-RAG", version="0.2")
app.include_router(assistants.router)
# /static/<...> -> data/static/<...> (originals + .md distillations, cited in answers)
app.mount(
    _settings.static_url_prefix,
    StaticFiles(directory=str(_settings.static_dir)),
    name="static",
)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/{filename}")
def frontend_asset(filename: str) -> FileResponse:
    path = (FRONTEND_DIR / filename).resolve()
    if FRONTEND_DIR not in path.parents or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path)
