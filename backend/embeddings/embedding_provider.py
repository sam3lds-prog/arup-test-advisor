"""
embedding_provider.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
Abstract embedding provider interface.

All providers MUST:
  • L2-normalize their outputs when normalize=True so cosine similarity
    is equivalent to inner-product in ChromaDB.
  • Return python list[list[float]] (not numpy arrays) — ChromaDB's
    add() and query() Python bindings prefer plain lists.
  • Distinguish encode_documents() from encode_queries() so providers
    that need a query-side prefix (e.g. BGE) can apply it without
    polluting the document corpus.

This file MUST NOT import torch, transformers, or sentence_transformers
at the top level. Heavy imports happen inside the concrete subclasses
(hf_embedding_provider.py, minilm_provider.py) so importing this module
never crashes a stripped-down environment.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Iterable, List


def slugify_model_name(name: str) -> str:
    """
    Convert a Hugging Face model id into a ChromaDB-safe collection suffix.

    Examples
    --------
    >>> slugify_model_name("BAAI/bge-base-en-v1.5")
    'bge_base_en_v1_5'
    >>> slugify_model_name("sentence-transformers/all-MiniLM-L6-v2")
    'all_minilm_l6_v2'
    """
    short = name.split("/", 1)[-1].lower()
    short = re.sub(r"[^a-z0-9]+", "_", short)
    short = short.strip("_")
    return short or "unknown"


class EmbeddingProvider(ABC):
    """
    Pluggable embedder.

    Concrete subclasses set these instance attributes during __init__:
        self.name           : str   — Hugging Face model id
        self.dimension      : int   — embedding dimension
        self.normalized     : bool  — True if encode_* outputs are unit length
        self.device         : str   — 'cpu' | 'mps' | 'cuda'
    """

    name: str = ""
    dimension: int = 0
    normalized: bool = True
    device: str = "cpu"

    @abstractmethod
    def encode_documents(self, texts: Iterable[str]) -> List[List[float]]:
        """Encode passage / document text. NEVER prepends a query instruction."""

    @abstractmethod
    def encode_queries(self, queries: Iterable[str]) -> List[List[float]]:
        """Encode user queries. May prepend a model-specific instruction prefix."""

    @property
    def collection_suffix(self) -> str:
        """ChromaDB-safe collection suffix derived from the model name."""
        return slugify_model_name(self.name)

    def health(self) -> dict:
        """Status dict for the /health endpoint."""
        return {
            "name":       self.name,
            "dimension":  self.dimension,
            "normalized": self.normalized,
            "device":     self.device,
        }

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}"
            f"(name={self.name!r}, dim={self.dimension}, device={self.device!r})"
        )
