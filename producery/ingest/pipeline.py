"""Document ingestion: body → chunks → embeddings → document_chunks. Idempotent per content hash."""

from __future__ import annotations

import asyncio
import logging

from producery.ingest.chunker import chunk_document
from producery.ingest.embedder import Embedder, vector_literal
from producery.store.records import Records

logger = logging.getLogger(__name__)


async def ingest_document(records: Records, embedder: Embedder, doc_id: str) -> dict:
    """Chunk, embed and store one document's chunks, replacing any previous rows for it.

    A missing document returns `{"ok": False, "error": {"code": "not_found", ...}}` without raising. A body that yields no chunks is
    stored as a single chunk holding the whole body.

    Args:
        records: Store facade used to read the document and replace its chunks.
        embedder: Encoder for the chunk texts.
        doc_id: The document id.

    Returns:
        `{ok, document_id, chunks, embedder}` on success, or `{ok: False, error}` when the document is
        missing.
    """
    doc = await records.get_document(doc_id)
    if doc is None:
        return {"ok": False, "error": {"code": "not_found", "message": f"document {doc_id} not found"}}
    chunks = chunk_document(doc["body"])
    if not chunks:
        chunks = [type("C", (), {"ord": 0, "section": None, "content": doc["body"]})()]  # single tiny doc
    vectors = await asyncio.to_thread(embedder.embed, [c.content for c in chunks])
    rows = [
        {"ord": c.ord, "section": c.section, "content": c.content, "embedding": vector_literal(v)}
        for c, v in zip(chunks, vectors, strict=True)
    ]
    await records.replace_chunks(doc_id, rows)
    logger.info("document_ingested id=%s chunks=%d embedder=%s", doc_id, len(rows), embedder.name)
    return {"ok": True, "document_id": doc_id, "chunks": len(rows), "embedder": embedder.name}
