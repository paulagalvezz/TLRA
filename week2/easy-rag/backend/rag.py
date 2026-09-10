"""RAG seam — the ONLY module that talks to `collections_manager`.

The rest of the app (ingest.py, assistants.py, main.py) calls the helpers here
and never imports collections_manager or ChromaDB directly. That keeps the
abstraction layer the single point of contact, exactly as the course intends:
swap the engine underneath by rewriting collections_manager, not the application.

collections_manager exposes three functions — create_collection / insert / query
— and those three are all this module (and therefore the whole app) ever uses
against the database.
"""
from collections_manager import Collection, create_collection, insert, query

from . import config
from .llm import client  # reuse the same OpenAI-compatible endpoint for embeddings

_settings = config.settings

# Cache of open collection handles, keyed by collection name (one per assistant).
# create_collection() is get_or_create, so re-opening is safe; caching just avoids
# rebuilding the handle on every request.
_collections: dict[str, Collection] = {}


def _embedding_function(texts: list[str]) -> list[list[float]]:
    """Embed via the same endpoint as chat (LLM_BASE_URL), model = EMBED_MODEL.

    Passed to create_collection so we don't depend on collections_manager's own
    OPENAI_ENDPOINT env — one endpoint to configure, not two.
    """
    resp = client.embeddings.create(model=_settings.embed_model, input=texts)
    return [row.embedding for row in resp.data]


def collection_name(assistant_id: str) -> str:
    """A valid, stable Chroma collection name for an assistant."""
    return f"assistant_{assistant_id}"


def get_collection(assistant_id: str) -> Collection:
    """Create-or-open (and cache) the collection belonging to an assistant."""
    name = collection_name(assistant_id)
    col = _collections.get(name)
    if col is None:
        col = create_collection(
            name,
            embedding_function=_embedding_function,
            description=f"EASY-RAG knowledge for assistant {assistant_id}",
            metric=_settings.collection_metric,
            persist_path=str(_settings.collection_persist_path),
        )
        _collections[name] = col
    return col


def insert_chunks(
    assistant_id: str,
    chunks: list[str],
    *,
    source: str,
    title: str,
    doc_url: str,
    md_url: str,
    chunking_strategy: str,
    ingested_at: str,
) -> dict:
    """Insert every chunk of one document with its provenance metadata.

    Metadata written per chunk (all mandatory fields for EASY-RAG):
      source            stable id for the document (chunk ids are "<source>::<n>")
      title             human-readable document title
      doc_url           URL of the ORIGINAL document under /static
      md_url            URL of the MARKDOWN distillation under /static
      chunk_number      position of the chunk within the document
      chunking_strategy the strategy + knobs that produced it
      ingested_at       ISO date of ingestion

    Returns {chunks, inserted, errors, total}.
    """
    col = get_collection(assistant_id)
    inserted = 0
    errors: list[str] = []
    for number, chunk in enumerate(chunks):
        result = insert(col, chunk, {
            "source": source,
            "title": title,
            "doc_url": doc_url,
            "md_url": md_url,
            "chunk_number": number,
            "chunking_strategy": chunking_strategy,
            "ingested_at": ingested_at,
        })
        if result["ok"]:
            inserted += 1
        else:
            errors.append(str(result["error"]))
    return {"chunks": len(chunks), "inserted": inserted, "errors": errors, "total": col.count()}


def retrieve(assistant_id: str, query_text: str) -> list[dict]:
    """The nearest chunks to `query_text`, filtered by the configured threshold.

    `retrieval_top_k` and `similarity_threshold` come from config (not constants).
    collections_manager.query() drops anything below the threshold, so an empty
    result means "nothing relevant enough" — the caller then refuses honestly.
    """
    col = get_collection(assistant_id)
    return query(
        col,
        query_text,
        top_k=_settings.retrieval_top_k,
        threshold=_settings.similarity_threshold,
    )


def _score(hit: dict) -> float | None:
    return hit.get("similarity", hit.get("distance"))


def format_context(hits: list[dict]) -> str:
    """Build the {context} string: each chunk preceded by its provenance.

    Provenance = document title, chunk number and similarity score, so the model
    (and the Context view) can see where each piece came from.
    """
    blocks = []
    for hit in hits:
        meta = hit["metadata"]
        score = _score(hit)
        score_txt = f"{score:.3f}" if isinstance(score, (int, float)) else "?"
        header = (
            f"[{meta.get('title', 'untitled')} · chunk {meta.get('chunk_number', '?')} "
            f"· similarity {score_txt}]"
        )
        blocks.append(f"{header}\n{hit['chunk']}")
    return "\n\n".join(blocks)


def build_sources(hits: list[dict]) -> list[dict]:
    """The provenance list returned to the frontend alongside the answer.

    Each source carries the clickable link to the ORIGINAL document under /static
    (doc_url), the markdown distillation (md_url), the chunk number and the score.
    """
    sources = []
    for hit in hits:
        meta = hit["metadata"]
        chunk = hit["chunk"]
        sources.append({
            "title": meta.get("title"),
            "source": meta.get("source"),
            "doc_url": meta.get("doc_url"),
            "md_url": meta.get("md_url"),
            "chunk_number": meta.get("chunk_number"),
            "chunking_strategy": meta.get("chunking_strategy"),
            "similarity": _score(hit),
            "snippet": chunk if len(chunk) <= 280 else chunk[:280].rstrip() + "…",
        })
    return sources
