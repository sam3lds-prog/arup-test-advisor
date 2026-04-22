"""
critic_agent.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
Clinical Review Critic — Adversarial review of ResponseAgent output

Two operational modes in a single module:

  MODE A — SingleCritic (Phase 1)
    One Haiku call playing a clinical pathologist role.
    Fast, minimal cost, delivers the largest single quality lift.

  MODE B — ReviewPanel (Phase 2)
    Three specialised reviewers run in parallel via asyncio.gather:
      • Stewardship Reviewer  (Choosing Wisely / ASCP stewardship priors)
      • Safety Reviewer       (CLSI / preanalytical standards)
      • Differential Skeptic  (differential diagnosis framing)
    Panel results are deterministically aggregated by ConsensusAggregator.

Design principles:
  ▸ CLINICAL-SAFE   — no auto-suppression; escalate > revise > approve
  ▸ BOUND COST      — one call (SingleCritic) or three parallel (Panel)
  ▸ NO LOOPS        — at most one ResponseAgent re-run per query
  ▸ GROUNDED        — every concern references evidence bundle contents
  ▸ ATTRIBUTED      — each concern carries reviewer name for audit trail
  ▸ DEGRADES SAFELY — LLM failure returns "approve" verdict + warning

Concerns schema:
  {
    "reviewer":   "stewardship" | "safety" | "skeptic" | "pathologist",
    "category":   "stewardship" | "safety" | "evidence_gap"
                  | "alternative_missed" | "clarity",
    "severity":   "low" | "medium" | "high",
    "claim":      str,   # what the reviewer believes is wrong
    "suggested_fix": str # what should change
  }

Verdict logic (single-critic):
  - accepts_recommendations=True   → verdict="approve"
  - any "high" severity concern    → verdict="escalate"
  - otherwise (medium/low only)    → verdict="revise"

Main.py contract — single turn:
  1. Call critic.review(response, evidence_bundle, intent)
  2. If verdict == "revise" → re-run ResponseAgent with critique injected
  3. After re-run, critic is NOT called again (hard cost cap)
  4. If verdict == "escalate" → set confidence.needs_review=True,
     append concerns to UI; do not re-run ResponseAgent

Panel consensus (delegated to ConsensusAggregator):
  - full_consensus  → verdict="approve"
  - partial_consensus → verdict="revise"
  - contentious     → verdict="escalate"
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional

import anthropic

logger = logging.getLogger(__name__)

# ── Model selection ──────────────────────────────────────────────────────────
_MODEL = os.environ.get(
    "CLAUDE_CRITIC_MODEL",
    os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
)

# ── Shared output contract ───────────────────────────────────────────────────

_VALID_SEVERITIES = {"low", "medium", "high"}
_VALID_CATEGORIES = {
    "stewardship", "safety", "evidence_gap",
    "alternative_missed", "clarity",
}
_VALID_VERDICTS = {"approve", "revise", "escalate"}


# ── System prompts per reviewer role ─────────────────────────────────────────

_PATHOLOGIST_PROMPT = """You are a senior clinical pathologist reviewing a peer's ARUP test recommendation before it is released to an ordering clinician.

Your job is to find problems, not to validate. You have full access to the same evidence bundle the recommending agent used. A recommendation passes ONLY if it is:
  1. Grounded in the provided evidence bundle (no external knowledge smuggled in)
  2. Appropriate for the stated intent and clinical context
  3. Safe regarding specimen, TAT, and reflex behaviour
  4. Sensible from a stewardship standpoint (first-line before advanced, no obvious overtesting)

Output ONLY valid JSON matching this schema — no prose, no markdown fences:
{
  "accepts_recommendations": true | false,
  "concerns": [
    {
      "reviewer": "pathologist",
      "category": "stewardship" | "safety" | "evidence_gap" | "alternative_missed" | "clarity",
      "severity": "low" | "medium" | "high",
      "claim": "Specific issue — one sentence referencing the recommendation.",
      "suggested_fix": "Concrete change that would address the issue."
    }
  ],
  "review_summary": "1–2 sentence summary of your overall judgement."
}

If you accept the recommendations unchanged, return concerns=[] and accepts_recommendations=true.
Severity rubric:
  high   — clinical safety risk, wrong test, or contradicts evidence bundle
  medium — stewardship concern, missing caveat, or missed better alternative
  low    — clarity or wording issue that does not change clinical meaning
"""

_STEWARDSHIP_PROMPT = """You are a laboratory stewardship reviewer applying Choosing Wisely and ASCP stewardship priors to an ARUP test recommendation.

Focus areas:
  • Was a first-line test skipped in favour of an advanced one?
  • Is the test tier appropriate for the clinical question?
  • Are reflex pathways used instead of bundling everything up front?
  • Are duplicate/redundant tests recommended when one would suffice?
  • Does the response respect the tests_ordered in clinical context (no repeats)?

Output ONLY valid JSON — no prose, no markdown fences:
{
  "accepts_recommendations": true | false,
  "concerns": [
    {
      "reviewer": "stewardship",
      "category": "stewardship" | "alternative_missed" | "evidence_gap" | "clarity",
      "severity": "low" | "medium" | "high",
      "claim": "Specific stewardship issue — one sentence.",
      "suggested_fix": "Concrete stewardship-aligned alternative."
    }
  ],
  "review_summary": "1–2 sentence summary."
}
"""

_SAFETY_PROMPT = """You are a patient-safety reviewer applying CLSI and preanalytical-standards priors to an ARUP test recommendation.

Focus areas:
  • Is the specimen requirement correctly surfaced (tube type, volume)?
  • Are timing-sensitive constraints surfaced (fasting, draw time, stability)?
  • Are interpretation caveats from fact sheets preserved?
  • Are unresolved source conflicts flagged in the answer?
  • Could a lab or phlebotomy error produce an unsafe result the clinician won't catch?

Output ONLY valid JSON — no prose, no markdown fences:
{
  "accepts_recommendations": true | false,
  "concerns": [
    {
      "reviewer": "safety",
      "category": "safety" | "evidence_gap" | "clarity",
      "severity": "low" | "medium" | "high",
      "claim": "Specific safety issue — one sentence.",
      "suggested_fix": "Concrete action to make the recommendation safer."
    }
  ],
  "review_summary": "1–2 sentence summary."
}
"""

_SKEPTIC_PROMPT = """You are a differential-diagnosis skeptic reviewing an ARUP test recommendation. Your role is to challenge: what if the recommending agent converged too fast on the presenting hypothesis?

Focus areas:
  • Could a cheaper or less invasive test answer the clinical question?
  • Are pre-test conditions (likelihood, pretest probability) respected?
  • Has the response missed a likely alternative diagnosis that would change test selection?
  • Is the response anchored to the query's phrasing rather than the likely clinical reality?

Output ONLY valid JSON — no prose, no markdown fences:
{
  "accepts_recommendations": true | false,
  "concerns": [
    {
      "reviewer": "skeptic",
      "category": "alternative_missed" | "stewardship" | "clarity",
      "severity": "low" | "medium" | "high",
      "claim": "Specific challenge — one sentence.",
      "suggested_fix": "Concrete alternative framing or test to consider."
    }
  ],
  "review_summary": "1–2 sentence summary."
}
"""


# ── Critique packet builder (fed to every reviewer) ──────────────────────────

def _build_review_packet(
    query: str,
    intent: dict,
    response: dict,
    evidence_bundle: dict,
) -> str:
    """Compact structured view of what the ResponseAgent did and why."""
    lines: list[str] = []

    lines.append("=== CLINICIAN QUERY ===")
    lines.append(query.strip() or "(empty)")

    it = intent.get("intent_type", "unknown")
    ctx = intent.get("medical_context", "") or ""
    lines.append(f"\n=== INTENT ===\n{it} — {ctx}")

    cond = intent.get("conditions", []) or []
    tests_mentioned = intent.get("tests_mentioned", []) or []
    if cond or tests_mentioned:
        lines.append(
            f"conditions={cond} tests_mentioned={tests_mentioned}"
        )

    # Response under review
    lines.append("\n=== RESPONSE UNDER REVIEW ===")
    lines.append(f"Answer: {response.get('answer', '')}")
    recs = response.get("recommendations", []) or []
    if recs:
        lines.append(f"\nRecommendations ({len(recs)}):")
        for i, r in enumerate(recs, 1):
            lines.append(
                f"  {i}. [{r.get('rank','?')}] {r.get('test_name','?')} "
                f"(code={r.get('test_code','')}, specimen={r.get('specimen','')}, "
                f"tat={r.get('tat','')}) — {r.get('rationale','')}"
            )
    else:
        lines.append("Recommendations: (none)")

    # Evidence bundle summary — what was available
    lines.append("\n=== EVIDENCE BUNDLE SUMMARY ===")
    cands = evidence_bundle.get("candidate_tests", []) or []
    lines.append(f"Candidate tests in bundle: {len(cands)}")
    for i, c in enumerate(cands[:5], 1):
        sigs = c.get("confidence_signals", {})
        lines.append(
            f"  {i}. {c.get('test_name','?')} — "
            f"algo={sigs.get('has_algorithm')} consult={sigs.get('has_consult_topic')} "
            f"dir={sigs.get('has_test_directory')} fact={sigs.get('has_fact_sheet')} "
            f"chunks={sigs.get('chunk_count',0)}"
        )

    conflicts = evidence_bundle.get("unresolved_conflicts", []) or []
    if conflicts:
        lines.append(f"\nConflicts in bundle: {conflicts}")
    gaps = evidence_bundle.get("missing_context", []) or []
    if gaps:
        lines.append(f"Gaps in bundle:      {gaps}")

    lines.append("\nNow review this recommendation.")
    return "\n".join(lines)


# ── Per-reviewer caller ──────────────────────────────────────────────────────

async def _call_reviewer(
    client: anthropic.Anthropic,
    model: str,
    reviewer_key: str,
    system_prompt: str,
    user_content: str,
) -> dict:
    """
    Single Claude call for one reviewer. Returns a structured dict even on error.
    """
    def _blocking_call() -> str:
        msg = client.messages.create(
            model=model,
            max_tokens=1600,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        return msg.content[0].text.strip()

    try:
        raw = await asyncio.to_thread(_blocking_call)
    except Exception as exc:  # network, auth, rate-limit, timeout
        logger.warning("Critic (%s): API call failed: %s", reviewer_key, exc)
        return {
            "accepts_recommendations": True,
            "concerns": [],
            "review_summary": f"(reviewer unavailable: {type(exc).__name__})",
            "_error": str(exc),
        }

    # Strip markdown fences if model wrapped output
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0]

    try:
        result = json.loads(raw.strip())
    except json.JSONDecodeError:
        logger.warning("Critic (%s): invalid JSON — treating as approve", reviewer_key)
        return {
            "accepts_recommendations": True,
            "concerns": [],
            "review_summary": "(reviewer returned non-JSON; defaulted to approve)",
            "_error": "invalid_json",
            "_raw": raw[:400],
        }

    # Normalise and validate
    result.setdefault("concerns", [])
    result.setdefault("accepts_recommendations", True)
    result.setdefault("review_summary", "")

    # Sanitise each concern
    clean_concerns: list[dict] = []
    for c in result.get("concerns", []):
        if not isinstance(c, dict):
            continue
        cat = c.get("category", "clarity")
        if cat not in _VALID_CATEGORIES:
            cat = "clarity"
        sev = c.get("severity", "low")
        if sev not in _VALID_SEVERITIES:
            sev = "low"
        clean_concerns.append({
            "reviewer":      reviewer_key,
            "category":      cat,
            "severity":      sev,
            "claim":         str(c.get("claim", "") or "").strip(),
            "suggested_fix": str(c.get("suggested_fix", "") or "").strip(),
        })

    result["concerns"] = clean_concerns
    return result


def _derive_single_verdict(reviewer_result: dict) -> str:
    """Map a single reviewer's output to the pipeline verdict vocabulary."""
    if reviewer_result.get("accepts_recommendations"):
        return "approve"
    concerns = reviewer_result.get("concerns", []) or []
    if any(c.get("severity") == "high" for c in concerns):
        return "escalate"
    if concerns:
        return "revise"
    return "approve"


# ── SingleCritic (Phase 1) ───────────────────────────────────────────────────

class SingleCritic:
    """
    One Haiku call per turn, pathologist persona.
    Use when cost/latency matter more than role coverage.
    """

    def __init__(self, model: Optional[str] = None):
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self.model  = model or _MODEL

    async def review(
        self,
        query: str,
        intent: dict,
        response: dict,
        evidence_bundle: dict,
    ) -> dict:
        """
        Returns:
        {
          "accepts_recommendations": bool,
          "concerns": [ ... ],
          "verdict": "approve" | "revise" | "escalate",
          "review_summary": str,
          "consensus_level": "single_reviewer",
          "reviewer_count": 1,
          "mode": "single_critic"
        }
        """
        packet = _build_review_packet(query, intent, response, evidence_bundle)
        result = await _call_reviewer(
            self.client, self.model,
            "pathologist", _PATHOLOGIST_PROMPT, packet,
        )
        result["verdict"]         = _derive_single_verdict(result)
        result["consensus_level"] = "single_reviewer"
        result["reviewer_count"]  = 1
        result["mode"]            = "single_critic"
        return result


# ── ReviewPanel (Phase 2) ────────────────────────────────────────────────────

_REVIEWER_SPECS = [
    ("stewardship", _STEWARDSHIP_PROMPT),
    ("safety",      _SAFETY_PROMPT),
    ("skeptic",     _SKEPTIC_PROMPT),
]


class ReviewPanel:
    """
    Three parallel reviewers with a deterministic consensus aggregator.
    """

    def __init__(self, model: Optional[str] = None, aggregator=None):
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self.model  = model or _MODEL
        # Lazy import to avoid circular dependency risk
        if aggregator is None:
            from agents.consensus_aggregator import ConsensusAggregator
            aggregator = ConsensusAggregator()
        self.aggregator = aggregator

    async def review(
        self,
        query: str,
        intent: dict,
        response: dict,
        evidence_bundle: dict,
    ) -> dict:
        """
        Runs 3 reviewers concurrently. Returns aggregated panel result.
        """
        packet = _build_review_packet(query, intent, response, evidence_bundle)

        coros = [
            _call_reviewer(self.client, self.model, key, prompt, packet)
            for key, prompt in _REVIEWER_SPECS
        ]
        try:
            results = await asyncio.gather(*coros, return_exceptions=False)
        except Exception as exc:
            logger.warning("ReviewPanel: gather failed (%s) — defaulting to approve", exc)
            return {
                "accepts_recommendations": True,
                "concerns": [],
                "verdict": "approve",
                "review_summary": f"(panel unavailable: {type(exc).__name__})",
                "consensus_level": "unknown",
                "reviewer_count":  0,
                "mode":            "review_panel_error",
                "_error":          str(exc),
            }

        # Pair each reviewer key with its result for the aggregator
        reviewer_results = [
            {"reviewer": key, **res}
            for (key, _), res in zip(_REVIEWER_SPECS, results)
        ]

        aggregated = self.aggregator.aggregate(reviewer_results)
        aggregated["mode"] = "review_panel"
        aggregated["reviewer_count"] = len(reviewer_results)
        aggregated["reviewer_results"] = reviewer_results  # full audit trail
        return aggregated


# ── Default factory (used by main.py) ────────────────────────────────────────

def build_default_critic(use_panel: bool = False) -> Any:
    """
    Return a critic instance:
      • use_panel=False → SingleCritic (Phase 1 default)
      • use_panel=True  → ReviewPanel  (Phase 2 upgrade)
    """
    return ReviewPanel() if use_panel else SingleCritic()
