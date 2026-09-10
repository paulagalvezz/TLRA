"""Ingestion — how an uploaded document gets into an assistant's collection.

Mirrors the course's simple-dynamic-rag/ingest.py pipeline, adapted to a web app:

  1. STORE the original under /static so it is linkable by URL.
  2. CONVERT it to markdown with markitdown (pdf, docx, pptx, html, txt, md...).
  3. STORE the markdown distillation under /static too, linked to the original.
  4. CHUNK the markdown with the config-selected strategy (backend/chunking.py).
  5. INSERT each chunk with provenance metadata — through rag.py, which is the
     only module that calls collections_manager.insert().

This module never imports collections_manager or ChromaDB.
"""
import datetime
import uuid
from pathlib import Path

from markitdown import MarkItDown

from . import chunking, config, rag

_settings = config.settings
_DOCS_SUBDIR = "docs"
_markitdown = MarkItDown()


def _docs_dir() -> Path:
    directory = _settings.static_dir / _DOCS_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def ingest_upload(assistant_id: str, filename: str, raw: bytes) -> dict:
    """Store + convert + chunk + insert ONE uploaded document.

    Returns a result dict; on success it carries the document metadata that the
    assistant record stores (title, doc_url, md_url, chunk counts, strategy...).
    """
    docs = _docs_dir()
    doc_id = uuid.uuid4().hex
    original_ext = Path(filename).suffix.lower() or ".txt"
    original_name = f"{doc_id}{original_ext}"
    # Avoid <id>.md colliding with itself when the upload already is markdown.
    md_name = f"{doc_id}.distilled.md" if original_ext == ".md" else f"{doc_id}.md"
    original_path = docs / original_name
    md_path = docs / md_name

    doc_url = f"{_settings.static_url_prefix}/{_DOCS_SUBDIR}/{original_name}"
    md_url = f"{_settings.static_url_prefix}/{_DOCS_SUBDIR}/{md_name}"

    # 1) store the original, reachable by URL
    original_path.write_bytes(raw)

    # 2) convert to markdown with markitdown
    try:
        result = _markitdown.convert(str(original_path))
    except Exception as exc:  # unsupported/corrupt file
        return {"ok": False, "error": f"markitdown conversion failed: {exc}",
                "doc_url": doc_url, "md_url": md_url}
    markdown = (result.text_content or "")
    title = (getattr(result, "title", None) or Path(filename).stem).strip()

    # 3) store the markdown distillation, linked to the original by URL
    md_path.write_text(markdown, encoding="utf-8")

    if not markdown.strip():
        return {"ok": False, "error": "markitdown produced empty markdown",
                "title": title, "doc_url": doc_url, "md_url": md_url}

    # 4) chunk (config-selected strategy)
    chunks, strategy_label = chunking.chunk_document(markdown, _settings)

    # 5) insert each chunk with provenance metadata (via rag -> collections_manager)
    insertion = rag.insert_chunks(
        assistant_id,
        chunks,
        source=doc_id,                 # stable per-document id -> chunk ids "<doc_id>::<n>"
        title=title,
        doc_url=doc_url,
        md_url=md_url,
        chunking_strategy=strategy_label,
        ingested_at=datetime.date.today().isoformat(),
    )

    return {
        "ok": insertion["inserted"] > 0,
        "doc_id": doc_id,
        "title": title,
        "original_name": filename,
        "doc_url": doc_url,
        "md_url": md_url,
        "markdown_chars": len(markdown),
        "chunks": insertion["chunks"],
        "inserted": insertion["inserted"],
        "errors": insertion["errors"],
        "collection_total": insertion["total"],
        "chunking_strategy": strategy_label,
        "ingested_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
