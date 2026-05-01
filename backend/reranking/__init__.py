"""
reranking package
────────────────────────────────────────────────────────────────────────────
Cross-encoder reranking layer that sits between RetrievalAgent and
EvidencePackager. Default model: BAAI/bge-reranker-v2-m3.

Phase 2 of the Hugging Face enhancement plan.

The reranker is OPTIONAL — if RERANKING_ENABLED=false or the model fails
to load, retrieval results pass through untouched (fail-open).
"""

from .reranking_agent import RerankingAgent  # noqa: F401
