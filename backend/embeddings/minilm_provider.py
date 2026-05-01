"""
minilm_provider.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
Legacy embedding provider — wraps ChromaDB's built-in
all-MiniLM-L6-v2 (ONNX, 384-dim).

Why this exists
───────────────
Provides a clean rollback path. If the BGE migration breaks for any
reason in production, set EMBEDDING_PROVIDER=minilm and restart — the
system falls back to the pre-migration behaviour, reading from the
legacy collection name "arup_knowledge".

Implementation note
───────────────────
Unlike HfEmbeddingProvider, this provider does NOT pre-compute embeddings
and pass them to ChromaDB. Instead it returns a sentinel that tells the
VectorStore to use ChromaDB's built-in embedder (query_texts=, documents=).
This keeps legacy behaviour byte-identical to v1.2.0 of the app.

The encode_* methods raise NotImplementedError if directly invoked —
they aren't needed in the legacy path because ChromaDB embeds internally.
"""

from __future__ import annotations

from typing import Iterable, List

from .embedding_provider import EmbeddingProvider


class MiniLMProvider(EmbeddingProvider):
    """Sentinel provider that delegates embedding to ChromaDB's built-in ONNX path."""

    name = "sentence-transformers/all-MiniLM-L6-v2"
    dimension = 384
    normalized = True
    device = "cpu"

    # Sentinel — the VectorStore checks for this attribute and falls back
    # to ChromaDB's internal embedder when it's True.
    USE_CHROMA_BUILTIN = True

    def encode_documents(self, texts: Iterable[str]) -> List[List[float]]:
        raise NotImplementedError(
            "MiniLMProvider delegates embedding to ChromaDB's built-in path. "
            "VectorStore should detect USE_CHROMA_BUILTIN and use query_texts/documents instead."
        )

    def encode_queries(self, queries: Iterable[str]) -> List[List[float]]:
        raise NotImplementedError(
            "MiniLMProvider delegates embedding to ChromaDB's built-in path. "
            "VectorStore should detect USE_CHROMA_BUILTIN and use query_texts/documents instead."
        )

    @property
    def collection_suffix(self) -> str:
        # Legacy collection name is unsuffixed — return empty so VectorStore
        # uses the historical "arup_knowledge" name unchanged.
        return ""

    @classmethod
    def from_env(cls) -> "MiniLMProvider":
        return cls()
