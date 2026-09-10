"""Chunking strategies — one function per strategy + a config-driven selector.

A CHUNK is the unit of retrieval: small enough to embed as one point and inject
into a prompt, big enough to still mean something on its own. How we cut decides
what the retriever can find.

The active strategy is `settings.chunk_strategy`. To add another one, write
`chunk_by_<name>(markdown, settings) -> list[str]` and register it in `_CHUNKERS`
— nothing else in the app changes. Every strategy reads its knobs from `settings`
(config), so there are no magic numbers in the code.

Implemented now:
  paragraphs — group whole markdown paragraphs. Paragraphs are atomic (an idea is
      never cut in half); headings at level <= settings.heading_level are hard
      boundaries; a chunk closes at settings.paragraphs_per_chunk paragraphs or
      when the next paragraph would exceed settings.chunk_size_chars.
"""
import re

from .config import RagSettings

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _split_paragraphs(block: str) -> list[str]:
    """Split text into paragraphs = runs of non-blank lines separated by blank lines."""
    paragraphs: list[str] = []
    current: list[str] = []
    for line in block.split("\n"):
        if line.strip() == "":
            if current:
                paragraphs.append("\n".join(current).strip())
                current = []
        else:
            current.append(line)
    if current:
        paragraphs.append("\n".join(current).strip())
    return [p for p in paragraphs if p]


def _join_chunk(heading: str | None, paragraphs: list[str]) -> str:
    body = "\n\n".join(paragraphs)
    if heading and heading.strip():
        return f"{heading.strip()}\n\n{body}"
    return body


def chunk_by_paragraphs(markdown: str, settings: RagSettings) -> list[str]:
    """Group markdown paragraphs into chunks (see module docstring for the rules)."""
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    per_chunk = max(1, settings.paragraphs_per_chunk)
    max_chars = max(0, settings.chunk_size_chars)
    level = settings.heading_level

    # 1) Split into sections at headings of level <= heading_level (hard boundaries).
    sections: list[tuple[str | None, list[str]]] = []
    heading: str | None = None
    body: list[str] = []
    for line in text.split("\n"):
        match = _HEADING_RE.match(line)
        if match and len(match.group(1)) <= level:
            sections.append((heading, body))
            heading, body = line, []
        else:
            body.append(line)
    sections.append((heading, body))

    # 2) Within each section, pack whole paragraphs into chunks.
    chunks: list[str] = []
    for sec_heading, sec_body in sections:
        paragraphs = _split_paragraphs("\n".join(sec_body))
        if not paragraphs:
            # A heading with no body still becomes a chunk (carries its title).
            if sec_heading and sec_heading.strip():
                chunks.append(sec_heading.strip())
            continue
        group: list[str] = []
        group_len = 0
        for paragraph in paragraphs:
            over_count = len(group) >= per_chunk
            over_size = max_chars and group_len + len(paragraph) + 2 > max_chars
            if group and (over_count or over_size):
                chunks.append(_join_chunk(sec_heading, group))
                group, group_len = [], 0
            group.append(paragraph)
            group_len += len(paragraph) + 2
        if group:
            chunks.append(_join_chunk(sec_heading, group))

    return [c for c in (chunk.strip() for chunk in chunks) if c]


# The selector: strategy name -> chunker. Add new strategies here.
_CHUNKERS = {
    "paragraphs": chunk_by_paragraphs,
}


def strategy_label(settings: RagSettings) -> str:
    """A short, human-readable label recorded in each chunk's metadata."""
    if settings.chunk_strategy == "paragraphs":
        return (
            f"paragraphs(per_chunk={settings.paragraphs_per_chunk},"
            f"heading<={settings.heading_level},max_chars={settings.chunk_size_chars})"
        )
    return settings.chunk_strategy


def chunk_document(markdown: str, settings: RagSettings) -> tuple[list[str], str]:
    """Select the strategy from config and cut the markdown. Returns (chunks, label)."""
    chunker = _CHUNKERS.get(settings.chunk_strategy)
    if chunker is None:
        raise ValueError(
            f"unknown chunk strategy {settings.chunk_strategy!r}; known: {list(_CHUNKERS)}"
        )
    return chunker(markdown, settings), strategy_label(settings)
