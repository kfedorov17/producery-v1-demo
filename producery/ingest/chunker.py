"""Section-aware chunking.

Targets ~400–600 tokens per chunk with 15% overlap; numbered headings start new chunks and label them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TARGET = 2200  # chars ≈ 550 tokens
MIN = 1600
OVERLAP = round(TARGET * 0.15)
HEADING_RE = re.compile(r"^\s*(?:(\d+(?:\.\d+)*)[.)]?\s+)([A-Z][A-Z &/'\-]{3,80})\s*$")


@dataclass(frozen=True)
class Chunk:
    """One chunk of a document: its position `ord`, the heading it falls under (if any) and its text."""

    ord: int
    section: str | None
    content: str


def chunk_document(text: str) -> list[Chunk]:
    """Split a document body into section-labelled chunks.

    Lines accumulate until the buffer reaches TARGET characters, then flush with an OVERLAP-character tail
    carried into the next chunk; a line matching HEADING_RE forces a flush (no tail) and sets the section
    label for what follows. Chunk content is cut at TARGET + OVERLAP characters. Chunks shorter than MIN
    characters are emitted only on a forced flush, and chunks whose stripped content is 40 characters or
    fewer are dropped at the end.

    Args:
        text: The document body.

    Returns:
        Chunks in document order; `ord` is assigned before the final length filter, so it may have gaps.
    """
    chunks: list[Chunk] = []
    section: str | None = None
    buf: list[str] = []

    def flush(force: bool = False) -> None:
        nonlocal buf
        content = "\n".join(buf).strip()
        if not content or (not force and len(content) < MIN):
            return
        chunks.append(Chunk(len(chunks), section, content[: TARGET + OVERLAP]))
        tail = content[-OVERLAP:]
        buf = [tail] if tail and not force else []

    for line in text.splitlines():
        m = HEADING_RE.match(line)
        if m:
            flush(force=True)
            section = f"{m.group(1) + ' ' if m.group(1) else ''}{m.group(2).strip()}"
            buf.append(line.strip())
        else:
            buf.append(line)
        if len("\n".join(buf)) >= TARGET:
            flush()
    flush(force=True)
    return [c for c in chunks if len(c.content.strip()) > 40]
