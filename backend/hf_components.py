"""
hf_components.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
Orchestrator for all Hugging Face additions to the ARUP AI Test Advisor.

This file exists so main.py needs only a TINY additive patch:

    # Top of main.py
    try:
        from hf_components import HfComponents
        _hf_available = True
    except ImportError:
        _hf_available = False
        HfComponents = None

    # Inside @app.on_event("startup")
    if _hf_available:
        app.state.hf = HfComponents.init(vector_store, retrieval_agent)
    else:
        app.state.hf = None

    # In /chat — three new lines:
    intent = await prompt_agent.analyze(...)
    if app.state.hf:
        intent = app.state.hf.enrich_intent(intent, query)
    raw_chunks = await retrieval_agent.retrieve(intent)
    if app.state.hf:
        raw_chunks = app.state.hf.enrich_and_rerank(query, intent, raw_chunks, vector_store)

    # In /health
    "huggingface": app.state.hf.health() if app.state.hf else {"enabled": False},

That's it. Every other agent file stays untouched.

Fail-open philosophy
────────────────────
Each component (embedding, reranker, NER) is loaded inside its own
try/except. If a load fails, the corresponding stage becomes a no-op
and the rest of the system continues unchanged. The HfComponents
container itself never raises after init() returns — every public
method is guarded.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List

logger = logging.getLogger(__name__)


def _env_true(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() == "true"


class HfComponents:
    """
    Container for embedding provider, reranker, and clinical NER.

    Construct via HfComponents.init(...) — never call __init__ directly.
    """

    def __init__(self):
        self.embedding_provider = None
        self.reranker = None
        self.clinical_ner = None
        self._ner_load_attempted = False
        self._ner_load_failed = False
        self._embedding_ready = False
        self._reranker_ready = False
        self._stats = {
            "rerank_calls":    0,
            "rerank_failures": 0,
            "ner_calls":       0,
            "ner_failures":    0,
            "enrichment_hits": 0,
        }

    @classmethod
    def init(cls, vector_store, retrieval_agent=None) -> "HfComponents":
        self = cls()

        # ── Embedding provider ──────────────────────────────────────────
        provider_kind = os.getenv("EMBEDDING_PROVIDER", "hf").strip().lower()
        try:
            if provider_kind == "minilm":
                from embeddings.minilm_provider import MiniLMProvider
                self.embedding_provider = MiniLMProvider.from_env()
                logger.info("HF: embedding provider = MiniLM (legacy)")
            else:
                from embeddings.hf_embedding_provider import HfEmbeddingProvider
                self.embedding_provider = HfEmbeddingProvider.from_env()
                logger.info(
                    "HF: embedding provider = %s dim=%d device=%s",
                    self.embedding_provider.name,
                    self.embedding_provider.dimension,
                    self.embedding_provider.device,
                )
            if hasattr(vector_store, "bind_provider"):
                vector_store.bind_provider(self.embedding_provider)
                self._embedding_ready = True
            else:
                logger.error("HF: VectorStore has no bind_provider(); store.py update missing")
        except Exception as exc:
            logger.exception("HF: embedding provider init failed (%s)", exc)
            try:
                from embeddings.minilm_provider import MiniLMProvider
                self.embedding_provider = MiniLMProvider.from_env()
                if hasattr(vector_store, "bind_provider"):
                    vector_store.bind_provider(self.embedding_provider)
                self._embedding_ready = True
                logger.warning("HF: degraded to legacy MiniLM after BGE failure")
            except Exception as exc2:
                logger.exception("HF: legacy fallback also failed (%s)", exc2)

        # ── Reranker ────────────────────────────────────────────────────
        if _env_true("RERANKING_ENABLED", default="true"):
            try:
                from reranking.reranking_agent import RerankingAgent
                self.reranker = RerankingAgent.from_env()
                self._reranker_ready = True
                logger.info("HF: reranker ready = %s", self.reranker.name)
            except Exception as exc:
                logger.exception("HF: reranker init failed (%s) — continuing without rerank", exc)
                self.reranker = None
        else:
            logger.info("HF: reranker DISABLED via env")

        return self

    # ── Lazy NER loader ─────────────────────────────────────────────────────

    def _ensure_ner(self) -> bool:
        if self.clinical_ner is not None:
            return True
        if self._ner_load_attempted and self._ner_load_failed:
            return False
        self._ner_load_attempted = True
        try:
            from clinical_ner.clinical_entity_agent import ClinicalEntityAgent
            self.clinical_ner = ClinicalEntityAgent.from_env()
            return True
        except Exception as exc:
            logger.exception("HF: clinical NER init failed (%s) — disabling", exc)
            self._ner_load_failed = True
            self.clinical_ner = None
            return False

    # ── Public hooks called from /chat in main.py ───────────────────────────

    def enrich_intent(self, intent: dict, query: str) -> dict:
        if not isinstance(intent, dict):
            return intent
        if not _env_true("CLINICAL_NER_ENABLED", default="false"):
            return intent
        if not self._ensure_ner():
            return intent
        try:
            ents = self.clinical_ner.extract(query or "")
            self._stats["ner_calls"] += 1
            if ents:
                self._stats["enrichment_hits"] += 1
            intent["clinical_entities"] = ents
            intent["ner_enrichment_query"] = self.clinical_ner.build_enrichment_query(ents)
        except Exception as exc:
            self._stats["ner_failures"] += 1
            logger.warning("HF: enrich_intent failed (%s) — continuing", exc)
        return intent

    def enrich_and_rerank(
        self,
        query: str,
        intent: dict,
        raw_chunks: List[Dict],
        vector_store,
    ) -> List[Dict]:
        if not isinstance(raw_chunks, list):
            return raw_chunks or []
        merged = list(raw_chunks)

        # NER-driven secondary retrieval
        enrichment_query = (intent or {}).get("ner_enrichment_query", "") if intent else ""
        if enrichment_query and vector_store is not None:
            try:
                top_n_extra = max(4, int(os.getenv("RERANKING_TOP_N", "30")) // 2)
                extra = vector_store.search(enrichment_query, n_results=top_n_extra)
                seen_ids = {c.get("id") for c in merged if c.get("id")}
                for c in extra:
                    cid = c.get("id")
                    if cid and cid not in seen_ids:
                        seen_ids.add(cid)
                        merged.append(c)
            except Exception as exc:
                logger.warning("HF: NER enrichment search failed (%s)", exc)

        # Rerank
        top_k = int(os.getenv("RERANKING_TOP_K", "8"))
        if self.reranker is not None and merged:
            try:
                self._stats["rerank_calls"] += 1
                return self.reranker.rerank(query, merged, top_k=top_k)
            except Exception as exc:
                self._stats["rerank_failures"] += 1
                logger.warning("HF: rerank failed (%s) — returning merged list", exc)
                return merged[:top_k]

        legacy_cap = max(top_k, 16)
        return merged[:legacy_cap]

    # ── Health / debug ──────────────────────────────────────────────────────

    def health(self) -> dict:
        emb = self.embedding_provider.health() if self.embedding_provider else {}
        rer = self.reranker.health() if self.reranker else None
        ner = self.clinical_ner.health() if self.clinical_ner else None
        return {
            "enabled": True,
            "embedding": {"ready": self._embedding_ready, **emb},
            "reranking": {
                "enabled": _env_true("RERANKING_ENABLED", default="true"),
                "ready":   self._reranker_ready,
                **(rer or {}),
            },
            "clinical_ner": {
                "enabled":     _env_true("CLINICAL_NER_ENABLED", default="false"),
                "loaded":      self.clinical_ner is not None,
                "load_failed": self._ner_load_failed,
                **(ner or {}),
            },
            "stats": dict(self._stats),
        }
