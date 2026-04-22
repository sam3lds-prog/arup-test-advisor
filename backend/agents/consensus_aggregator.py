"""
consensus_aggregator.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
Consensus Aggregator — Deterministic panel-review consensus logic

Aggregates the output of the ReviewPanel (3 reviewers) into a single verdict
using the mapping laid out in the agentic framework document:

  Consensus Level       What It Means                        Pipeline Action
  ───────────────────── ──────────────────────────────────── ───────────────
  full_consensus        All reviewers approved                "approve"
  partial_consensus     Some concerns, nothing high-severity  "revise"
  contentious           ≥1 high-severity concern OR           "escalate"
                        reviewer disagreement on "accept"

Design principles:
  ▸ DETERMINISTIC  — zero LLM calls; pure rule logic
  ▸ CLINICAL-SAFE  — errs toward escalation, never auto-suppresses
  ▸ TRANSPARENT    — named signals alongside the verdict
  ▸ AUDITABLE      — preserves each reviewer's original output

Output schema (consumed by main.py and FormattingAgent):
{
  "accepts_recommendations": bool,      # True only on full_consensus
  "concerns":                [ ... ],   # merged across reviewers, each with reviewer attribution
  "verdict":                 "approve" | "revise" | "escalate",
  "consensus_level":         "full_consensus" | "partial_consensus" | "contentious",
  "review_summary":          str,
  "reviewer_count":          int,
  "signals": {
    "any_high_severity":       bool,
    "any_medium_severity":     bool,
    "reviewers_accepting":     int,
    "reviewers_rejecting":     int,
    "reviewers_errored":       int,
  }
}
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ConsensusAggregator:
    """
    Deterministic merge of N reviewer results into a single panel verdict.

    Usage:
        aggregator = ConsensusAggregator()
        panel_result = aggregator.aggregate(reviewer_results)
    """

    def aggregate(self, reviewer_results: list[dict]) -> dict:
        """
        Aggregate a list of reviewer result dicts (from critic_agent._call_reviewer)
        into a single panel verdict dict.
        """
        if not reviewer_results:
            return {
                "accepts_recommendations": True,
                "concerns": [],
                "verdict": "approve",
                "consensus_level": "full_consensus",
                "review_summary": "(no reviewers ran)",
                "reviewer_count": 0,
                "signals": {
                    "any_high_severity":   False,
                    "any_medium_severity": False,
                    "reviewers_accepting": 0,
                    "reviewers_rejecting": 0,
                    "reviewers_errored":   0,
                },
            }

        # ── 1. Merge concerns, preserving reviewer attribution ───────────────
        all_concerns: list[dict] = []
        accepting = 0
        rejecting = 0
        errored   = 0
        summaries: list[str] = []

        for r in reviewer_results:
            if r.get("_error"):
                errored += 1
                continue
            if r.get("accepts_recommendations"):
                accepting += 1
            else:
                rejecting += 1
            for c in r.get("concerns", []) or []:
                if not isinstance(c, dict):
                    continue
                # Ensure reviewer attribution is present
                c.setdefault("reviewer", r.get("reviewer", "unknown"))
                all_concerns.append(c)
            summary = (r.get("review_summary") or "").strip()
            if summary:
                summaries.append(f"[{r.get('reviewer', '?')}] {summary}")

        # ── 2. Compute severity signals ──────────────────────────────────────
        any_high   = any(c.get("severity") == "high"   for c in all_concerns)
        any_medium = any(c.get("severity") == "medium" for c in all_concerns)

        # ── 3. Decide consensus level ────────────────────────────────────────
        #
        # contentious if:
        #   - any high severity concern, OR
        #   - at least one reviewer accepted AND at least one rejected
        #     (disagreement on accept/reject)
        #
        # full_consensus if:
        #   - all non-errored reviewers accepted AND no concerns surfaced
        #
        # partial_consensus: everything else
        disagreement = accepting > 0 and rejecting > 0

        if any_high or disagreement:
            consensus_level = "contentious"
            verdict         = "escalate"
            accepts         = False
        elif rejecting == 0 and not all_concerns:
            consensus_level = "full_consensus"
            verdict         = "approve"
            accepts         = True
        else:
            consensus_level = "partial_consensus"
            verdict         = "revise"
            accepts         = False

        # ── 4. Build review summary line ─────────────────────────────────────
        if summaries:
            panel_summary = " | ".join(summaries[:3])
        else:
            panel_summary = {
                "full_consensus":    "All reviewers accepted the recommendations.",
                "partial_consensus": "Reviewers raised concerns but none are blocking.",
                "contentious":       "Reviewers disagreed or surfaced a high-severity concern.",
            }[consensus_level]

        # ── 5. Emit aggregated dict ─────────────────────────────────────────
        return {
            "accepts_recommendations": accepts,
            "concerns":                all_concerns,
            "verdict":                 verdict,
            "consensus_level":         consensus_level,
            "review_summary":          panel_summary,
            "reviewer_count":          len(reviewer_results),
            "signals": {
                "any_high_severity":   any_high,
                "any_medium_severity": any_medium,
                "reviewers_accepting": accepting,
                "reviewers_rejecting": rejecting,
                "reviewers_errored":   errored,
            },
        }
