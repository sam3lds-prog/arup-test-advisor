"""
reranking_agent.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
Cross-encoder reranking with BAAI/bge-reranker-v2-m3.

Pipeline insertion point
────────────────────────
RetrievalAgent → RerankingAgent → EvidencePackager

Why a reranker
──────────────
Vector search (bi-encoder) is fast but coarse. It scores a query's vector
against pre-computed document vectors using cosine — a single dot product
that has to summarise the entire passage in one number. A cross-encoder
reads the query AND the candidate passage TOGETHER, with full transformer
attention between them, so it can answer the much sharper question:
"does this specific passage actually answer this specific query?".

For ARUP, this attacks the most common RAG failure mode:
"the vector DB found something thyroid-related, but not the actual
thyroid-nodule algorithm". The reranker promotes the on-target
algorithm chunk to the top.

Hard rules — preserved by every code path
──────────────────────────────────────────
  • All metadata on every input chunk is preserved verbatim.
  • New fields ADDED per chunk:
      retrieval_score        : float — ChromaDB cosine similarity (cached)
      retrieval_rank         : int   — 1-based position before rerank
      rerank_score           : float — raw cross-encoder logit
      rerank_score_sigmoid   : float — sigmoid(rerank_score) ∈ [0,1]
      combined_score         : float — α·sigmoid + (1-α)·retrieval, ∈ [0,1]
      rerank_rank            : int   — 1-based position after rerank
      rerank_model           : str   — model id used (for /health debugging)
  • Fail-open: any exception during load OR inference returns the original
    chunks (truncated to top_k) so the request never fails because of HF.
  • Module imports of torch/sentence_transformers happen INSIDE __init__,
    NEVER at module load time.

Score combination policy
────────────────────────
combined_score = α · sigmoid(rerank_score) + (1-α) · retrieval_score
α default = 0.7  (env: RERANKING_BLEND_ALPHA)

Rationale: pure rerank score is the recommended signal but a small blend
preserves the original retrieval prior so the reranker can't destroy
high-confidence retrieval results when it's uncertain (small score gap).
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"


def _resolve_device(requested: str) -> str:
    """Mirror of HfEmbeddingProvider's device resolver."""
    if requested and requested != "auto":
        return requested
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _sigmoid(x: float) -> float:
    # Numerically stable sigmoid for raw cross-encoder logits.
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


class RerankingAgent:
    """
    Cross-encoder reranker built on sentence-transformers' CrossEncoder.

    We deliberately use sentence_transformers.CrossEncoder rather than
    FlagEmbedding. FlagEmbedding pulls heavy transitive deps (peft,
    datasets, occasionally flash-attn) that are problematic on macOS
    arm64 — flash-attn has no M1 wheel and the stack has known install
    failures on Apple Silicon. CrossEncoder is the officially documented
    BGE path, gives identical scores, and supports model.half() for fp16.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str = "auto",
        use_fp16: bool = True,
        max_length: int = 512,
        blend_alpha: float = 0.7,
        timeout_ms: int = 2500,
        fail_open: bool = True,
    ):
        from sentence_transformers import CrossEncoder

        self.name = model_name
        self.device = _resolve_device(device)
        self.use_fp16 = bool(use_fp16) and self.device == "mps"
        self.max_length = int(max_length)
        self.alpha = max(0.0, min(1.0, float(blend_alpha)))
        self.timeout_ms = int(timeout_ms)
        self.fail_open = bool(fail_open)
        self._lock = threading.Lock()

        logger.info(
            "RerankingAgent: loading model=%s device=%s fp16=%s max_len=%d alpha=%.2f",
            self.name, self.device, self.use_fp16, self.max_length, self.alpha,
        )
        self._model = CrossEncoder(self.name, max_length=self.max_length, device=self.device)

        if self.use_fp16:
            try:
                # The inner HuggingFace model is at .model on the CrossEncoder wrapper
                self._model.model.half()
                logger.info("RerankingAgent: fp16 enabled on MPS")
            except Exception as exc:
                logger.warning("RerankingAgent: fp16 conversion failed (%s); using fp32", exc)
                self.use_fp16 = False

        logger.info("RerankingAgent: ready name=%s device=%s", self.name, self.device)

    # ── Public API ───────────────────────────────────────────────────────────

    def rerank(
        self,
        query: str,
        candidates: List[Dict],
        top_k: Optional[int] = None,
    ) -> List[Dict]:
        """
        Re-order `candidates` (list of chunk dicts) by cross-encoder relevance to `query`.

        Returns a new list — does NOT mutate input dicts.
        """
        if not candidates:
            return []
        if not query or not isinstance(query, str):
            logger.warning("RerankingAgent.rerank: empty/non-str query — returning as-is")
            return list(candidates[:top_k] if top_k else candidates)

        k = top_k if top_k is not None else len(candidates)

        try:
            # Build (query, passage) pairs; truncate per max_length is handled by tokenizer
            pairs = [(query, str(c.get("text", ""))) for c in candidates]

            t0 = time.time()
            with self._lock:
                # CrossEncoder.predict returns a numpy array of raw logits
                scores = self._model.predict(pairs, show_progress_bar=False)
            elapsed_ms = (time.time() - t0) * 1000.0

            if elapsed_ms > self.timeout_ms:
                logger.warning(
                    "RerankingAgent: rerank took %.0fms (budget %dms) — accepting anyway",
                    elapsed_ms, self.timeout_ms,
                )

            # Build augmented copies preserving all original metadata
            out: List[Dict] = []
            for i, c in enumerate(candidates):
                cc = dict(c)  # shallow copy preserves all keys
                rs = float(scores[i])
                rs_sig = _sigmoid(rs)
                # Original retrieval score may be missing; default to 0
                retrieval_score = float(cc.get("score", cc.get("retrieval_score", 0.0)) or 0.0)

                cc["retrieval_score"] = retrieval_score
                cc["retrieval_rank"]  = i + 1
                cc["rerank_score"]    = rs
                cc["rerank_score_sigmoid"] = rs_sig
                cc["combined_score"]  = (
                    self.alpha * rs_sig + (1.0 - self.alpha) * retrieval_score
                )
                cc["rerank_model"]    = self.name
                out.append(cc)

            out.sort(key=lambda c: c["combined_score"], reverse=True)
            for rank, c in enumerate(out, 1):
                c["rerank_rank"] = rank

            logger.info(
                "RerankingAgent: reranked %d candidates -> top_k=%d in %.0fms",
                len(candidates), min(k, len(out)), elapsed_ms,
            )
            return out[:k]

        except Exception as exc:
            if self.fail_open:
                logger.warning(
                    "RerankingAgent: rerank failed (%s) — falling back to retrieval order",
                    exc,
                )
                return list(candidates[:k])
            raise

    def health(self) -> dict:
        return {
            "model":      self.name,
            "device":     self.device,
            "fp16":       self.use_fp16,
            "max_length": self.max_length,
            "alpha":      self.alpha,
            "timeout_ms": self.timeout_ms,
        }

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "RerankingAgent":
        return cls(
            model_name=os.getenv("RERANKING_MODEL", DEFAULT_MODEL),
            device=os.getenv("RERANKING_DEVICE", "auto"),
            use_fp16=os.getenv("RERANKING_USE_FP16", "true").lower() == "true",
            max_length=int(os.getenv("RERANKING_MAX_LENGTH", "512")),
            blend_alpha=float(os.getenv("RERANKING_BLEND_ALPHA", "0.7")),
            timeout_ms=int(os.getenv("RERANKING_TIMEOUT_MS", "2500")),
            fail_open=os.getenv("RERANKING_FAIL_OPEN", "true").lower() == "true",
        )
