"""
confidence_agent.py  v1.1.0
────────────────────────────────────────────────────────────────────────────
Confidence Agent — Rule-Based Evidence Scoring + HF retrieval signals

Changes from v1.0.0
───────────────────
  • TWO new deterministic signals derived from reranker output (Phase 4):
      reranker_agreement   : Spearman rank correlation between retrieval
                             order and rerank order, mapped to [0,1].
                             High → bi-encoder and cross-encoder agree
                             on which chunks are best — high confidence.
                             Low  → reranker overruled retrieval — be
                             cautious.
      semantic_alignment   : Mean of sigmoid(rerank_score) over the chunks
                             that were actually cited by ResponseAgent.
                             Falls back to mean retrieval_score when
                             rerank not available.
  • Both are NULL when the inputs aren't available (no rerank scores OR
    fewer than 3 chunks for rank correlation). NULL signals are excluded
    from the score blend so legacy behaviour is preserved when reranker
    is disabled.
  • New env knobs:
      CONFIDENCE_INCLUDE_HF_SIGNALS=true  (default)
      CONFIDENCE_RERANKER_AGREEMENT_WEIGHT=0.15
      CONFIDENCE_SEMANTIC_ALIGNMENT_WEIGHT=0.20
  • Rest of v1.0.0 scoring logic is UNCHANGED — base score is still
    derived deterministically from chunk_count, mean_score, and tier
    coverage.

Hard rules preserved
────────────────────
  • Zero LLM calls. Pure math + dict reads.
  • Errs toward LOWER confidence when evidence is thin.
  • Output dict shape backward-compatible — adds keys only.

Output dict (added to /chat response as "confidence"):
{
  "score":          int,          # 0–100
  "tier":           str,          # "high" | "medium" | "low" | "none"
  "signal_count":   int,
  "candidate_count":int,
  "signals": {
    "has_algorithm":      bool,
    "has_fact_sheet":     bool,
    "has_consult_topic":  bool,
    "has_test_directory": bool,
    "multi_source":       bool,
    "strong_retrieval":   bool,
    "reranker_agreement": float | None,    # NEW v1.1.0
    "semantic_alignment": float | None,    # NEW v1.1.0
  },
  "intent_type":    str,
}
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Scoring weights (v1.0.0, unchanged) ───────────────────────────────────────
_W_CHUNK_COUNT     = 20
_W_STRONG_SCORE    = 15
_W_ALGORITHM       = 15
_W_FACT_SHEET      = 10
_W_CONSULT_TOPIC   = 10
_W_TEST_DIRECTORY  = 10
_W_MULTI_SOURCE    = 10
_W_INTENT_MATCH    = 10

_CHUNK_THRESHOLD   = 3
_SCORE_THRESHOLD   = 0.55

# ── Tier thresholds (v1.0.0, unchanged) ───────────────────────────────────────
_TIER_HIGH   = 70
_TIER_MEDIUM = 40
_TIER_LOW    = 10


# ── HF signal helpers (v1.1.0) ────────────────────────────────────────────────

def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def reranker_agreement(chunks: List[Dict]) -> Optional[float]:
    """
    Spearman rank correlation between retrieval_rank and rerank_rank,
    rescaled to [0, 1].

    Returns None when fewer than 3 chunks have both ranks set.
    """
    pairs: List[Tuple[int, int]] = []
    for c in chunks:
        rr = c.get("retrieval_rank")
        kr = c.get("rerank_rank")
        if isinstance(rr, int) and isinstance(kr, int) and rr > 0 and kr > 0:
            pairs.append((rr, kr))

    if len(pairs) < 3:
        return None

    n = len(pairs)
    d2 = sum((a - b) ** 2 for a, b in pairs)
    rho = 1.0 - (6.0 * d2) / (n * (n * n - 1))
    rho = max(-1.0, min(1.0, rho))
    return round((rho + 1.0) / 2.0, 4)


def semantic_alignment(cited_chunks: List[Dict]) -> Optional[float]:
    """
    Mean of sigmoid(rerank_score) over cited chunks. Falls back to mean
    retrieval_score (already in [0,1]) when reranker not present.
    Returns None when no cited chunks have any score.
    """
    if not cited_chunks:
        return None
    scored: List[float] = []
    for c in cited_chunks:
        if "rerank_score_sigmoid" in c and isinstance(c["rerank_score_sigmoid"], (int, float)):
            scored.append(float(c["rerank_score_sigmoid"]))
        elif "rerank_score" in c and isinstance(c["rerank_score"], (int, float)):
            scored.append(_sigmoid(float(c["rerank_score"])))
    if not scored:
        # Fallback to retrieval_score / score
        for c in cited_chunks:
            for k in ("retrieval_score", "score"):
                v = c.get(k)
                if isinstance(v, (int, float)):
                    scored.append(float(v))
                    break
    if not scored:
        return None
    return round(sum(scored) / len(scored), 4)


def _resolve_cited_chunks(response: dict, evidence_bundle: dict) -> List[Dict]:
    """
    Find the chunks that ResponseAgent cited. Citations are 1-based
    [SOURCE N] references into evidence_bundle.authority_sources.
    """
    citations = response.get("citations", []) or []
    if not citations:
        return []
    sources = evidence_bundle.get("authority_sources", []) or []
    by_index: Dict[int, Dict] = {}
    for i, src in enumerate(sources, start=1):
        by_index[i] = src

    cited: List[Dict] = []
    seen_keys: set = set()
    for cit in citations:
        n = cit.get("number") if isinstance(cit, dict) else None
        if not isinstance(n, int):
            continue
        src = by_index.get(n)
        if not src:
            continue
        # authority_sources entries from evidence_packager carry the chunk's
        # rerank/retrieval scores when reranking was performed upstream.
        key = src.get("chunk_id") or src.get("id") or (
            src.get("filename"), src.get("excerpt", "")[:40]
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        cited.append(src)
    return cited


# ── Per-candidate scorer (unchanged from v1.0.0) ──────────────────────────────

def _score_candidate(candidate: dict, intent_type: str) -> tuple[int, dict]:
    """Score a single candidate test and return (raw_score, signal_dict)."""
    cs = candidate.get("confidence_signals", {})
    signals = {
        "has_algorithm":      bool(cs.get("has_algorithm")),
        "has_fact_sheet":     bool(cs.get("has_fact_sheet")),
        "has_consult_topic":  bool(cs.get("has_consult_topic")),
        "has_test_directory": bool(cs.get("has_test_directory")),
        "multi_source":       (cs.get("tier_count", 0) or 0) >= 2,
        "strong_retrieval":   (cs.get("mean_score", 0) or 0) >= _SCORE_THRESHOLD,
    }

    score = 0
    if (cs.get("chunk_count") or 0) >= _CHUNK_THRESHOLD:
        score += _W_CHUNK_COUNT
    if signals["strong_retrieval"]:
        score += _W_STRONG_SCORE
    if signals["has_algorithm"]:
        score += _W_ALGORITHM
    if signals["has_fact_sheet"]:
        score += _W_FACT_SHEET
    if signals["has_consult_topic"]:
        score += _W_CONSULT_TOPIC
    if signals["has_test_directory"]:
        score += _W_TEST_DIRECTORY
    if signals["multi_source"]:
        score += _W_MULTI_SOURCE
    if intent_type and intent_type != "ambiguous":
        score += _W_INTENT_MATCH

    return min(score, 100), signals


def _tier(score: int) -> str:
    if score >= _TIER_HIGH:
        return "high"
    if score >= _TIER_MEDIUM:
        return "medium"
    if score >= _TIER_LOW:
        return "low"
    return "none"


# ── Confidence Agent ──────────────────────────────────────────────────────────

class ConfidenceAgent:
    """Rule-based confidence scorer + HF retrieval signals."""

    def __init__(self):
        self.include_hf = os.getenv(
            "CONFIDENCE_INCLUDE_HF_SIGNALS", "true"
        ).strip().lower() == "true"
        self.weight_agreement = float(
            os.getenv("CONFIDENCE_RERANKER_AGREEMENT_WEIGHT", "0.15")
        )
        self.weight_alignment = float(
            os.getenv("CONFIDENCE_SEMANTIC_ALIGNMENT_WEIGHT", "0.20")
        )

    # ── Public scoring API ───────────────────────────────────────────────────

    def score(
        self,
        query: str,
        evidence_bundle: dict,
        response: dict,
        intent_type: str = "ambiguous",
    ) -> dict:
        """
        Evaluate evidence strength and return a structured confidence dict.
        Backward-compatible with v1.0.0 — only adds keys.
        """
        candidates = evidence_bundle.get("candidate_tests", []) or []

        # ── Base v1.0.0 scoring ─────────────────────────────────────────────
        if not candidates:
            base_score = 0
            top_signals: Dict[str, Any] = {
                "has_algorithm":      False,
                "has_fact_sheet":     False,
                "has_consult_topic":  False,
                "has_test_directory": False,
                "multi_source":       False,
                "strong_retrieval":   False,
            }
            signal_count = 0
        else:
            top_candidates = candidates[:3]
            scored = [_score_candidate(c, intent_type) for c in top_candidates]
            raw_scores = [s[0] for s in scored]
            base_score = round(sum(raw_scores) / len(raw_scores))
            top_signals = scored[0][1]
            signal_count = sum(1 for v in top_signals.values() if v)

        # ── Phase 4 — HF signals ────────────────────────────────────────────
        # Pull the chunks that flowed through reranking. EvidencePackager
        # builds candidate_tests + authority_sources from the same pool, so
        # we can compute signals directly from the bundle.
        all_chunks: List[Dict] = []
        for ct in candidates:
            for tc in ct.get("test_chunks", []) or []:
                all_chunks.append(tc)
        # authority_sources is the canonical "what made it into the answer"
        # list; if test_chunks isn't populated, fall back to it.
        if not all_chunks:
            all_chunks = evidence_bundle.get("authority_sources", []) or []

        agreement = reranker_agreement(all_chunks)

        cited = _resolve_cited_chunks(response, evidence_bundle)
        alignment = semantic_alignment(cited) if cited else semantic_alignment(all_chunks[:8])

        top_signals["reranker_agreement"] = agreement
        top_signals["semantic_alignment"] = alignment

        # ── Optional blend ──────────────────────────────────────────────────
        final_score = base_score
        if self.include_hf and base_score > 0:
            # Each contributes a +/- adjustment around 0.5.
            # adjustment in points = weight * 100 * (signal - 0.5) * 2
            # so signal=1.0 adds +weight*100, signal=0.0 subtracts weight*100.
            adj = 0.0
            if agreement is not None:
                adj += self.weight_agreement * 100.0 * (agreement - 0.5) * 2.0
            if alignment is not None:
                adj += self.weight_alignment * 100.0 * (alignment - 0.5) * 2.0
            blended = base_score + adj
            final_score = max(0, min(100, round(blended)))

        return {
            "score":           final_score,
            "tier":            _tier(final_score),
            "signal_count":    signal_count,
            "candidate_count": len(candidates),
            "signals":         top_signals,
            "intent_type":     intent_type,
            # Optional debug field — visible in dev, ignored by frontend
            "hf_signals": {
                "reranker_agreement": agreement,
                "semantic_alignment": alignment,
                "base_score":         base_score,
                "blended_score":      final_score,
                "include_hf":         self.include_hf,
            },
        }
