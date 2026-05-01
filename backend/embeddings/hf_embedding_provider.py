"""
hf_embedding_provider.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
Hugging Face sentence-transformers embedding provider.

Default model: BAAI/bge-base-en-v1.5 (768-dim).

Why this provider exists
────────────────────────
The legacy ChromaDB built-in embedder (all-MiniLM-L6-v2, 384-dim) is
trained on a generic web corpus and underperforms on clinical-domain
queries like "celiac disease workup" or "thyroid nodule FNA". BGE-base
is trained with a query-document contrastive objective and ranks much
higher on retrieval benchmarks (MTEB) for short-query / long-document
tasks — which exactly matches the ARUP Test Advisor use case.

Hard rules
──────────
  • Heavy imports (sentence_transformers, torch) happen inside __init__,
    NEVER at module import time. This preserves the project's "module
    import never crashes the server" rule.
  • encode_documents() NEVER prepends the BGE query instruction.
  • encode_queries() optionally prepends the instruction
    (env: EMBEDDING_USE_QUERY_INSTRUCTION). Default off — the BGE-v1.5
    model card notes only minor degradation without it.
  • Outputs are python lists of floats (not numpy arrays).
"""

from __future__ import annotations

import logging
import os
from typing import Iterable, List

from .embedding_provider import EmbeddingProvider

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-base-en-v1.5"
DEFAULT_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


def _resolve_device(requested: str) -> str:
    """
    Resolve 'auto' to the best available device on the host machine.

    Priority on macOS arm64 (M1/M2/M3): mps > cpu.
    Priority elsewhere with a discrete GPU: cuda > cpu.
    """
    if requested and requested != "auto":
        return requested
    try:
        import torch  # local import — keeps module import light
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


class HfEmbeddingProvider(EmbeddingProvider):
    """
    sentence-transformers wrapper for BGE-style embedders.

    Constructor parameters mirror the relevant env vars so the from_env()
    factory can be used both from main.py startup and from the reindex
    script without duplicating env-parsing logic.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str = "auto",
        batch_size: int = 32,
        normalize: bool = True,
        use_query_instruction: bool = False,
        query_instruction: str = DEFAULT_QUERY_INSTRUCTION,
    ):
        # Imports are intentionally local to keep `import embeddings` cheap.
        from sentence_transformers import SentenceTransformer

        self.name = model_name
        self.device = _resolve_device(device)
        self.batch_size = max(1, int(batch_size))
        self.normalized = bool(normalize)
        self.use_query_instruction = bool(use_query_instruction)
        self.query_instruction = query_instruction

        logger.info(
            "HfEmbeddingProvider: loading model=%s device=%s batch=%d normalize=%s "
            "use_query_instruction=%s",
            self.name, self.device, self.batch_size, self.normalized,
            self.use_query_instruction,
        )
        self._model = SentenceTransformer(self.name, device=self.device)
        self.dimension = int(self._model.get_sentence_embedding_dimension())
        logger.info(
            "HfEmbeddingProvider: ready name=%s dim=%d device=%s",
            self.name, self.dimension, self.device,
        )

    # ── Encoding API ─────────────────────────────────────────────────────────

    def _encode(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        embs = self._model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=self.normalized,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        # numpy → list[list[float]] (ChromaDB-friendly)
        return [vec.tolist() for vec in embs]

    def encode_documents(self, texts: Iterable[str]) -> List[List[float]]:
        return self._encode(list(texts))

    def encode_queries(self, queries: Iterable[str]) -> List[List[float]]:
        qs = list(queries)
        if self.use_query_instruction:
            qs = [f"{self.query_instruction}{q}" for q in qs]
        return self._encode(qs)

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "HfEmbeddingProvider":
        """Build a provider from EMBEDDING_* environment variables."""
        return cls(
            model_name=os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL),
            device=os.getenv("EMBEDDING_DEVICE", "auto"),
            batch_size=int(os.getenv("EMBEDDING_BATCH_SIZE", "32")),
            normalize=os.getenv("NORMALIZE_EMBEDDINGS", "true").lower() == "true",
            use_query_instruction=(
                os.getenv("EMBEDDING_USE_QUERY_INSTRUCTION", "false").lower() == "true"
            ),
            query_instruction=os.getenv(
                "EMBEDDING_QUERY_INSTRUCTION", DEFAULT_QUERY_INSTRUCTION
            ),
        )
