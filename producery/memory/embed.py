"""The ONE embedding function for the LangGraph store index.

Used by the FastAPI server (`bootstrap.embed_fn`) and by `langgraph.json`
(`store.index.embed = ./producery/memory/embed.py:embed_texts`) so Deployment and compose index memory records
with the same model and dimensions (fastembed bge-small, 384-d, keyless).
"""

from __future__ import annotations

import asyncio
from functools import lru_cache

from producery.ingest.embedder import FastEmbedEmbedder
from producery.settings import get_settings


@lru_cache(maxsize=1)
def _embedder() -> FastEmbedEmbedder:
    s = get_settings()
    return FastEmbedEmbedder(s.embed_model, s.embed_dims, s.fastembed_cache)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Async; fastembed runs in a worker thread, never on the event loop."""
    return await asyncio.to_thread(_embedder().embed, texts)
