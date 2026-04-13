"""
confidence_agent.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
Confidence Agent — Rule-Based Evidence Scoring

Evaluates the strength of the evidence bundle returned by the EvidencePackager
and produces a structured confidence score for each chat response.

Design principles:
  ▸ DETERMINISTIC  — zero LLM calls; pure rule-based scoring
  ▸ GROUNDED       — score reflects actual retrieved evidence signals
  ▸ TRANSPARENT    — returns named signals alongside the final score
  ▸ CLINICAL-SAFE  — errs toward lower confidence when evidence is thin

Scoring (0–100):
  Base score per candidate test (signals that add points):
    • chunk_count ≥ 3              → +20
    • mean_score  ≥ 0.55           → +15
    • has_algorithm                → +15
    • has_fact_sheet               → +10
    • has_consult_topic            → +10
    • has_test_directory           → +10
    • multi-source (tier_count≥2)  → +10
    • intent-matched retrieval     → +10
  Final score = weighted average of top-3 candidate scores, capped at 100.

Output dict (added to /chat response as "confidence"):
{
  "score":          int,          # 0–100
  "tier":           str,          # "high" | "medium" | "low" | "none"
  "signal_count":   int,          # total positive signals across top candidates
  "candidate_count":int,          # how many candidate tests were evaluated
  "signals": {                    # named boolean signals from top candidate
    "has_algorithm":       bool,
    "has_fact_sheet":      bool,
    "has_consult_topic":   bool,
    "has_test_directory":  bool,
    "multi_source":        bool,
    "strong_retrieval":    bool,
  },
  "intent_type":    str,
}
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Scoring weights ────────────────────────────────────────────────────────────
_W_CHUNK_COUNT     = 20   # ≥ 3 chunks retrieved for this test
_W_STRONG_SCORE    = 15   # mean cosine score ≥ 0.55
_W_ALGORITHM       = 15   # algorithm document supports this test
_W_FACT_SHEET      = 10   # fact sheet document supports this test
_W_CONSULT_TOPIC   = 10   # consult topic document supports this test
_W_TEST_DIRECTORY  = 10   # test directory entry found
_W_MULTI_SOURCE    = 10   # evidence from ≥ 2 document types (tiers)
_W_INTENT_MATCH    = 10   # intent_type is specific (not "ambiguous")

_CHUNK_THRESHOLD   = 3
_SCORE_THRESHOLD   = 0.55

# ── Tier thresholds ────────────────────────────────────────────────────────────
_TIER_HIGH   = 70
_TIER_MEDIUM = 40
_TIER_LOW    = 10


def _score_candidate(candidate: dict, intent_type: str) -> tuple[int, dict]:
    """
    Score a single candidate test and return (raw_score, signal_dict).
    """
    cs      = candidate.get("confidence_signals", {})
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


class ConfidenceAgent:
    """
    Rule-based confidence scorer for the ARUP AI Test Advisor.

    Usage (from main.py):
        confidence_agent = ConfidenceAgent()
        confidence = confidence_agent.score(query, evidence_bundle, response, intent_type=intent_type)
    """

    def score(
        self,
        query: str,
        evidence_bundle: dict,
        response: dict,
        intent_type: str = "ambiguous",
    ) -> dict:
        """
        Evaluate evidence strength and return a structured confidence dict.

        Parameters
        ----------
        query          : str  — original user query (reserved for future use)
        evidence_bundle: dict — output of EvidencePackager.package()
        response       : dict — output of ResponseAgent.generate()
        intent_type    : str  — classified intent from PromptAgent

        Returns
        -------
        dict — see module-level docstring for output schema
        """
        candidates = evidence_bundle.get("candidate_tests", [])

        if not candidates:
            return {
                "score":           0,
                "tier":            "none",
                "signal_count":    0,
                "candidate_count": 0,
                "signals": {
                    "has_algorithm":      False,
                    "has_fact_sheet":     False,
                    "has_consult_topic":  False,
                    "has_test_directory": False,
                    "multi_source":       False,
                    "strong_retrieval":   False,
                },
                "intent_type": intent_type,
            }

        # Score top-3 candidates and average them (best candidates already sorted first)
        top_candidates = candidates[:3]
        scored = [_score_candidate(c, intent_type) for c in top_candidates]

        raw_scores = [s[0] for s in scored]
        final_score = round(sum(raw_scores) / len(raw_scores))

        # Use signals from the top candidate for the named signals block
        top_signals = scored[0][1]
        signal_count = sum(1 for v in top_signals.values() if v)

        return {
            "score":           final_score,
            "tier":            _tier(final_score),
            "signal_count":    signal_count,
            "candidate_count": len(candidates),
            "signals":         top_signals,
            "intent_type":     intent_type,
        }