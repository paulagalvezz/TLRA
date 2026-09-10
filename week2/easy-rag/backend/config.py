"""EASY-RAG configuration — the ONE place to tune the RAG pipeline.

Everything the app wants to tweak lives here as explicit config, not hardcoded in
the logic modules. Each value has a sensible default and can be overridden from
the environment / .env (see .env.example).

Two groups matter for RAG:

  * CHUNKING (ingest side) — how an uploaded document (converted to markdown by
    markitdown) is cut into chunks. The active strategy is `chunk_strategy`;
    "paragraphs" groups whole markdown paragraphs and is implemented in
    backend/chunking.py. The knobs below drive it — no magic numbers in code.

  * RETRIEVAL (query side) — `retrieval_top_k` nearest chunks come back and
    `similarity_threshold` discards the ones that are too weak. Both are passed
    straight into collections_manager.query(). If nothing passes the threshold the
    assistant answers with `no_hits_message` instead of forcing weak context.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Project layout: backend/.. is the project root; runtime data lives under data/.
_BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _BACKEND_DIR.parent
DATA_DIR = Path(os.getenv("EASY_RAG_DATA_DIR") or PROJECT_ROOT / "data")


def _str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value is not None and value != "" else default


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else default


def _float_or_none(name: str, default: float | None) -> float | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


@dataclass(frozen=True)
class RagSettings:
    # --- models & storage -------------------------------------------------
    embed_model: str                 # embeddings model (MUST match across insert & query)
    collection_metric: str           # "cosine" (higher = closer) | "euclidean"
    collection_persist_path: Path    # where collections live on disk (survive restarts)
    assistants_file: Path            # Week-1 JSON persistence for assistant records
    static_dir: Path                 # on-disk dir served at static_url_prefix (originals + .md)
    static_url_prefix: str           # URL prefix for stored documents (e.g. "/static")
    max_doc_bytes: int               # upload size cap

    # --- chunking (ingest) -------------------------------------------------
    chunk_strategy: str              # selector key — "paragraphs" (see chunking._CHUNKERS)
    paragraphs_per_chunk: int        # how many markdown paragraphs to group per chunk
    chunk_size_chars: int            # soft max chunk size in characters (never cuts a paragraph)
    chunk_overlap_chars: int         # shared margin (reserved for char-window strategies)
    heading_level: int               # headings at level <= this delimit a chunk (2 = ## and #)

    # --- retrieval (query) -------------------------------------------------
    retrieval_top_k: int             # how many nearest chunks to pull
    similarity_threshold: float | None  # min cosine similarity; None = disabled

    # --- grounded-answer behaviour ----------------------------------------
    no_hits_message: str             # honest answer when no chunk passes the threshold


settings = RagSettings(
    embed_model=_str("EMBED_MODEL", "nomic-embed-text"),
    collection_metric=_str("COLLECTION_METRIC", "cosine"),
    collection_persist_path=Path(
        os.getenv("COLLECTION_PERSIST_PATH") or DATA_DIR / "collections-store"
    ),
    assistants_file=Path(os.getenv("ASSISTANTS_FILE") or DATA_DIR / "assistants.json"),
    static_dir=Path(os.getenv("STATIC_DIR") or DATA_DIR / "static"),
    static_url_prefix=_str("STATIC_URL_PREFIX", "/static").rstrip("/"),
    max_doc_bytes=_int("MAX_DOC_BYTES", 100 * 1024),
    chunk_strategy=_str("CHUNK_STRATEGY", "paragraphs"),
    paragraphs_per_chunk=_int("PARAGRAPHS_PER_CHUNK", 1),
    chunk_size_chars=_int("CHUNK_SIZE_CHARS", 800),
    chunk_overlap_chars=_int("CHUNK_OVERLAP_CHARS", 100),
    heading_level=_int("HEADING_LEVEL", 2),
    retrieval_top_k=_int("RETRIEVAL_TOP_K", 4),
    # 0.5 is a starting point for nomic-embed-text (relevant chunks ~0.50-0.70,
    # unrelated ~0.40-0.50). Calibrate to your model/corpus: lower it if real
    # questions get starved, raise it if weak chunks slip through. Empty = off.
    similarity_threshold=_float_or_none("SIMILARITY_THRESHOLD", 0.5),
    no_hits_message=_str(
        "NO_HITS_MESSAGE",
        "I don't know — none of the uploaded documents contain information relevant "
        "to that question (no retrieved chunk met the similarity threshold).",
    ),
)
