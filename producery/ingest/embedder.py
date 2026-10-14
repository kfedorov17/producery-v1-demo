"""Embedder protocol + the pinned local implementation (fastembed ONNX, deterministic, no provider key).

Not an LLM: embeddings are a deterministic encoder, so they do not pass through the model door.
"""

from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    """Protocol for a text encoder: a `name`, its output size `dims` and an `embed` method."""

    name: str
    dims: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector of `dims` floats per input text, in order.

        Args:
            texts: The strings to encode.
        """
        ...


class FastEmbedEmbedder:
    """Local fastembed (ONNX) implementation of `Embedder`; the model is loaded once in `__init__`."""

    def __init__(self, model: str, dims: int, cache_dir: str) -> None:
        """Load the fastembed model and record `name` (the model id) and `dims` (as given, not measured).

        Args:
            model: fastembed model name.
            dims: Vector size the model produces, stored as `self.dims`.
            cache_dir: Directory fastembed uses to cache model files.
        """
        from fastembed import TextEmbedding

        self.name = model
        self.dims = dims
        self._model = TextEmbedding(model, cache_dir=cache_dir)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Encode `texts` with the loaded model and return plain Python float lists.

        Args:
            texts: The strings to encode.
        """
        return [[float(x) for x in vec] for vec in self._model.embed(texts)]


def vector_literal(vec: list[float]) -> str:
    """Format a vector as a pgvector literal (`[v1,v2,...]`) with six decimal places per component.

    Args:
        vec: The embedding to format.
    """
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
