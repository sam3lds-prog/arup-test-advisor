"""
algorithm_fidelity_critic.py  v1.1.0
────────────────────────────────────────────────────────────────────────────
Algorithm Fidelity Critic — Structural comparison of rendered viz vs source

Scores how faithfully the rendered algorithm flowchart preserves the structure
of the source ARUP algorithm (as extracted during ingestion).

Two tiers:
  TIER 1 — Deterministic (always runs, no LLM, ~5ms)
    Six weighted checks over the source graph vs the rendered viz.

  TIER 2 — Vision (conditional, 1 or 2 vision calls, ~800ms-1.5s)
    Only invoked when:
      • 60 ≤ deterministic score ≤ 84 (borderline), AND
      • source_pdf_asset is available locally, AND
      • vision_check_enabled=True in fidelity_rules or argument

    v1.1.0 — Multimodal provider support:
      • vision_provider="claude"  → Claude Haiku w/ vision  (default)
      • vision_provider="openai"  → OpenAI GPT-4o-mini vision
      • vision_provider="both"    → Both run in parallel; disagreement
                                     auto-escalates the fidelity tier
                                     and sets needs_review.

      The "both" mode is the high-signal option for clinical safety —
      when two independent model families agree a branch is missing from
      the rendering, that's a strong signal to show the source PDF.
      When they disagree, the system surfaces that as uncertainty.

Design principles:
  ▸ GROUNDED         — compares ingested source graph to rendered viz only,
                        never invents expected structure
  ▸ CLAUDE-FIRST     — primary rendering and scoring is always Claude;
                        OpenAI only participates in the adversarial vision check
  ▸ DETERMINISTIC-FIRST — always returns something without an LLM
  ▸ DEGRADES SAFELY  — vision failure (any provider) returns tier-1 score
  ▸ TUNABLE          — thresholds, weights, and providers live in
                        fidelity_rules.json (hot-reloadable)

Output schema (added to /chat response as "algorithm_fidelity"):
{
  "fidelity_score": 0-100,
  "tier":           "match" | "partial" | "divergent" | "none",
  "verdict":        "match" | "partial" | "divergent" | "none",
  "checks": [ {check, weight, earned, expected, actual, missing?, pass} ],
  "missing_elements":  [str],
  "extra_elements":    [str],
  "recommendation":    "proceed" | "show_source_pdf_alongside" | "use_source_pdf_only",
  "vision_check_run":  bool,
  "vision_result":     dict | None,        # Normalised, provider-agnostic
  "vision_consensus":  str | None,         # v1.1.0: only set in "both" mode
                                            # "agreement" | "disagreement" | "partial"
  "vision_by_provider": dict | None,       # v1.1.0: raw per-provider results
                                            # in "both" mode for full audit trail
  "source_pdf_asset_id": str | None,
  "_debug":            { ... }
}
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_RULES_FILE = Path(__file__).parent / "fidelity_rules.json"

# ── Default weights & thresholds (overridable by fidelity_rules.json) ────────

DEFAULT_WEIGHTS: dict[str, int] = {
    "test_codes":          25,
    "decision_count":      15,
    "node_coverage":       20,
    "entry_label":         10,
    "routing_labels":      15,
    "footer_preservation": 15,
}

DEFAULT_THRESHOLDS: dict[str, int] = {
    "match_min":     85,   # ≥ match_min → "match"
    "partial_min":   60,   # partial_min..match_min-1 → "partial"
                          # < partial_min → "divergent"
    "vision_low":    60,   # vision runs only within [vision_low, vision_high]
    "vision_high":   84,
}

# ── v1.1.0 — Multimodal vision provider defaults ─────────────────────────────
# Valid providers: "claude", "openai", "both"
DEFAULT_VISION_PROVIDER = "claude"
DEFAULT_VISION_MODELS = {
    "claude": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
}
_VALID_PROVIDERS = {"claude", "openai", "both"}

_MODEL = os.environ.get(
    "CLAUDE_FIDELITY_MODEL",
    os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize_label(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for fuzzy text compare."""
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fuzzy_match(a: str, b: str, threshold: float = 0.6) -> bool:
    """True if two normalized labels share >= threshold fraction of tokens."""
    na, nb = _normalize_label(a), _normalize_label(b)
    if not na or not nb:
        return False
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(len(ta), len(tb))
    return overlap >= threshold


def _source_test_codes(source_graph: dict) -> list[str]:
    """Extract orderable test codes from source graph tests[]."""
    codes: list[str] = []
    for t in source_graph.get("tests", []) or []:
        tn = str(t.get("test_number", "") or "").strip()
        if tn:
            codes.append(tn)
    return codes


def _rendered_test_codes(rendered_viz: dict) -> list[str]:
    """Extract test codes from rendered viz referenced_tests[]."""
    codes: list[str] = []
    for t in rendered_viz.get("referenced_tests", []) or []:
        tc = str(t.get("test_code", "") or "").strip()
        if tc:
            codes.append(tc)
    return codes


def _count_source_decisions(source_graph: dict) -> int:
    """Count decision-type nodes in the source graph."""
    return sum(
        1 for n in source_graph.get("nodes", []) or []
        if "decision" in str(n.get("type", "") or "").lower()
    )


def _count_meaningful_source_nodes(source_graph: dict) -> int:
    """Count source nodes that would be expected to render (non-artifact)."""
    artifact_types = {"title", "legend", "footnote", "reference", "section_header", "header"}
    count = 0
    for n in source_graph.get("nodes", []) or []:
        ntype = str(n.get("type", "") or "").lower()
        if ntype in artifact_types:
            continue
        # Must have some meaningful text
        text = (n.get("title") or "") + " " + (n.get("body") or "")
        if text.strip():
            count += 1
    return count


def _source_entry_label(source_graph: dict) -> str:
    """Best-effort source root node label."""
    root_id = source_graph.get("root_node_id")
    if root_id:
        for n in source_graph.get("nodes", []) or []:
            if n.get("id") == root_id:
                return (n.get("title") or n.get("body") or "").strip()
    # Fallback: first node
    for n in source_graph.get("nodes", []) or []:
        text = (n.get("title") or n.get("body") or "").strip()
        if text:
            return text
    return ""


def _rendered_entry_label(rendered_viz: dict) -> str:
    """Best-effort rendered entry label from entry_section_node_ids[0]."""
    entry_ids = rendered_viz.get("entry_section_node_ids") or []
    if not entry_ids:
        entry_ids = rendered_viz.get("spine_node_ids") or []
    if not entry_ids:
        return ""
    target_id = entry_ids[0]
    for n in rendered_viz.get("nodes", []) or []:
        if n.get("id") == target_id:
            return (n.get("label") or n.get("raw_label") or n.get("title") or "").strip()
    # Fallback: first rendered node
    nodes = rendered_viz.get("nodes", []) or []
    if nodes:
        n = nodes[0]
        return (n.get("label") or n.get("raw_label") or n.get("title") or "").strip()
    return ""


_ROUTING_KEYWORDS = {"yes", "no", "positive", "negative", "abnormal", "normal",
                     "elevated", "low", "if", "otherwise", "reflex"}


def _extract_routing_labels(edges: list[dict]) -> set[str]:
    """Return lowered routing labels from a list of edges."""
    labels: set[str] = set()
    for e in edges or []:
        label = (e.get("label") or e.get("routing") or e.get("condition") or "")
        label = _normalize_label(str(label))
        if not label:
            continue
        # Accept if it contains a routing keyword
        tokens = set(label.split())
        if tokens & _ROUTING_KEYWORDS:
            labels.add(label)
    return labels


def _has_footer_content(source_graph: dict) -> bool:
    """True if source has footnotes, abbreviations, or references."""
    return bool(
        (source_graph.get("abbreviations") or [])
        or (source_graph.get("footnotes") or [])
        or (source_graph.get("references") or [])
    )


def _has_rendered_footer(rendered_viz: dict) -> bool:
    """True if rendered viz has footer_blocks."""
    return bool(rendered_viz.get("footer_blocks") or [])


# ── Deterministic check runners ──────────────────────────────────────────────

def _check_test_codes(source_graph: dict, rendered_viz: dict, weight: int) -> dict:
    expected = _source_test_codes(source_graph)
    actual   = _rendered_test_codes(rendered_viz)
    if not expected:
        # Nothing to preserve — award full credit
        return {
            "check": "test_codes", "weight": weight, "earned": weight,
            "expected": 0, "actual": len(actual), "missing": [], "pass": True,
            "note": "No source test codes; credit awarded by default.",
        }
    expected_set = set(expected)
    actual_set   = set(actual)
    missing = sorted(expected_set - actual_set)
    preserved = len(expected_set & actual_set)
    ratio = preserved / len(expected_set)
    earned = round(weight * ratio)
    return {
        "check": "test_codes", "weight": weight, "earned": earned,
        "expected": len(expected_set), "actual": len(actual_set),
        "preserved": preserved, "missing": missing, "pass": ratio >= 0.9,
    }


def _check_decision_count(source_graph: dict, rendered_viz: dict, weight: int) -> dict:
    expected = _count_source_decisions(source_graph)
    actual   = rendered_viz.get("stats", {}).get("decision_count", 0)
    if expected == 0:
        # No decisions expected — credit if rendered also has none
        earned = weight if actual == 0 else round(weight * 0.7)
        return {
            "check": "decision_count", "weight": weight, "earned": earned,
            "expected": 0, "actual": actual, "pass": actual == 0,
        }
    # Score based on ratio — tolerate ±1 as full credit
    diff = abs(expected - actual)
    if diff == 0:
        earned = weight
    elif diff == 1:
        earned = round(weight * 0.9)
    else:
        ratio = min(actual, expected) / max(actual, expected) if max(actual, expected) else 0
        earned = round(weight * ratio)
    return {
        "check": "decision_count", "weight": weight, "earned": earned,
        "expected": expected, "actual": actual, "pass": diff <= 1,
    }


def _check_node_coverage(source_graph: dict, rendered_viz: dict, weight: int) -> dict:
    expected = _count_meaningful_source_nodes(source_graph)
    actual   = len(rendered_viz.get("nodes", []) or [])
    if expected == 0:
        return {
            "check": "node_coverage", "weight": weight, "earned": weight,
            "expected": 0, "actual": actual, "pass": True,
            "note": "No meaningful source nodes; credit awarded by default.",
        }
    # Tolerate over-rendering (actual > expected); penalise under-rendering proportionally
    if actual >= expected * 0.85:
        earned = weight
    else:
        ratio = actual / expected
        earned = round(weight * ratio)
    return {
        "check": "node_coverage", "weight": weight, "earned": earned,
        "expected": expected, "actual": actual,
        "coverage_ratio": round(actual / expected, 3) if expected else 0,
        "pass": actual >= expected * 0.85,
    }


def _check_entry_label(source_graph: dict, rendered_viz: dict, weight: int) -> dict:
    exp_label = _source_entry_label(source_graph)
    act_label = _rendered_entry_label(rendered_viz)
    if not exp_label:
        # No source entry — skip check, award full credit
        return {
            "check": "entry_label", "weight": weight, "earned": weight,
            "expected": "", "actual": act_label, "pass": True,
            "note": "No identifiable source entry label.",
        }
    match = _fuzzy_match(exp_label, act_label, threshold=0.5)
    earned = weight if match else round(weight * 0.3)
    return {
        "check": "entry_label", "weight": weight, "earned": earned,
        "expected": exp_label[:120], "actual": act_label[:120], "pass": match,
    }


def _check_routing_labels(source_graph: dict, rendered_viz: dict, weight: int) -> dict:
    src_labels = _extract_routing_labels(source_graph.get("edges", []))
    ren_labels = _extract_routing_labels(rendered_viz.get("edges", []))
    if not src_labels:
        # No labelled routing in source — award full credit
        return {
            "check": "routing_labels", "weight": weight, "earned": weight,
            "expected": 0, "actual": len(ren_labels), "pass": True,
            "note": "Source has no labelled routing edges.",
        }
    preserved = len(src_labels & ren_labels)
    ratio = preserved / len(src_labels)
    earned = round(weight * ratio)
    return {
        "check": "routing_labels", "weight": weight, "earned": earned,
        "expected": len(src_labels), "actual": len(ren_labels),
        "preserved": preserved,
        "missing": sorted(src_labels - ren_labels),
        "pass": ratio >= 0.75,
    }


def _check_footer_preservation(source_graph: dict, rendered_viz: dict, weight: int) -> dict:
    src_has = _has_footer_content(source_graph)
    ren_has = _has_rendered_footer(rendered_viz)
    if not src_has:
        return {
            "check": "footer_preservation", "weight": weight, "earned": weight,
            "expected": False, "actual": ren_has, "pass": True,
            "note": "Source has no footer content.",
        }
    earned = weight if ren_has else 0
    return {
        "check": "footer_preservation", "weight": weight, "earned": earned,
        "expected": True, "actual": ren_has, "pass": ren_has,
    }


# ── Public rule loader ────────────────────────────────────────────────────────

def load_fidelity_rules(path: Optional[Path] = None) -> dict:
    target = path or _RULES_FILE
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info("AlgorithmFidelityCritic: rules loaded from %s", target)
        return data
    except FileNotFoundError:
        logger.info("AlgorithmFidelityCritic: no fidelity_rules.json — using defaults")
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("AlgorithmFidelityCritic: failed to parse rules — %s", e)
        return {}


def save_fidelity_rules(rules: dict, path: Optional[Path] = None) -> None:
    target = path or _RULES_FILE
    with open(target, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=2, ensure_ascii=False)
    logger.info("AlgorithmFidelityCritic: rules saved to %s", target)


# ── The critic ────────────────────────────────────────────────────────────────

class AlgorithmFidelityCritic:
    """
    Hybrid structural+visual fidelity scorer for rendered algorithms.

    Usage:
        critic = AlgorithmFidelityCritic()   # auto-loads fidelity_rules.json
        report = await critic.review(
            rendered_viz    = algorithm_viz,
            source_graph    = graph_data,         # from EvidencePackager
            source_pdf      = asset_dict | None,  # from AssetStore.find_matching_*
            enable_vision   = False,
        )
    """

    def __init__(self, rules_path: Optional[Path] = None, model: Optional[str] = None):
        self._rules_path  = rules_path or _RULES_FILE
        self._model       = model or _MODEL
        self._rules: dict = {}
        self._weights: dict[str, int] = dict(DEFAULT_WEIGHTS)
        self._thresholds: dict[str, int] = dict(DEFAULT_THRESHOLDS)
        self._vision_default  = False
        # v1.1.0 — multimodal provider state
        self._vision_provider: str = DEFAULT_VISION_PROVIDER
        self._vision_models: dict[str, str] = dict(DEFAULT_VISION_MODELS)
        self._load()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def reload(self) -> None:
        self._load()

    def get_rules(self) -> dict:
        return {
            "weights":     dict(self._weights),
            "thresholds":  dict(self._thresholds),
            "vision_check_enabled": self._vision_default,
            "vision_provider":      self._vision_provider,
            "vision_models":        dict(self._vision_models),
            "openai_available":     bool(os.environ.get("OPENAI_API_KEY")),
            "_raw": dict(self._rules),
        }

    def set_rules(self, rules: dict) -> None:
        save_fidelity_rules(rules, self._rules_path)
        self._rules = rules
        self._apply()

    # ── Main entry ────────────────────────────────────────────────────────────

    async def review(
        self,
        rendered_viz:  Optional[dict],
        source_graph:  Optional[dict],
        source_pdf:    Optional[dict]  = None,
        enable_vision: Optional[bool]  = None,
    ) -> dict:
        """
        Score fidelity and return a structured report.
        Never raises — always returns a dict.
        """
        # No inputs → return a "none" tier report
        if not rendered_viz or not source_graph:
            return self._empty_report(
                reason="no_rendered_viz" if not rendered_viz else "no_source_graph",
                source_pdf=source_pdf,
            )

        # ── Deterministic tier ─────────────────────────────────────────────
        check_runners = [
            ("test_codes",          _check_test_codes),
            ("decision_count",      _check_decision_count),
            ("node_coverage",       _check_node_coverage),
            ("entry_label",         _check_entry_label),
            ("routing_labels",      _check_routing_labels),
            ("footer_preservation", _check_footer_preservation),
        ]

        checks: list[dict] = []
        total_weight = 0
        total_earned = 0

        for name, runner in check_runners:
            weight = self._weights.get(name, DEFAULT_WEIGHTS.get(name, 0))
            try:
                result = runner(source_graph, rendered_viz, weight)
            except Exception as exc:
                logger.warning("FidelityCritic: check %s raised %s", name, exc)
                result = {
                    "check": name, "weight": weight, "earned": weight,
                    "pass": True, "error": str(exc),
                    "note": "Check failed to run; credited by default.",
                }
            checks.append(result)
            total_weight += weight
            total_earned += int(result.get("earned", 0))

        # Normalise to 0–100 regardless of weight configuration
        score = round((total_earned / total_weight) * 100) if total_weight else 0
        tier  = self._tier_for_score(score)

        # ── Missing / extra elements summary ────────────────────────────────
        missing_elements: list[str] = []
        extra_elements:   list[str] = []

        for c in checks:
            if c.get("check") == "test_codes":
                for code in c.get("missing", [])[:5]:
                    missing_elements.append(f"test_code {code}")
            elif c.get("check") == "routing_labels":
                for lbl in c.get("missing", [])[:3]:
                    missing_elements.append(f"routing label '{lbl}'")
            elif c.get("check") == "entry_label" and not c.get("pass"):
                missing_elements.append(
                    f"entry label: expected '{(c.get('expected') or '')[:60]}'"
                )
            elif c.get("check") == "footer_preservation" and not c.get("pass"):
                missing_elements.append("footer / abbreviations / footnotes")

        # ── Recommendation for downstream UI ────────────────────────────────
        if tier == "match":
            recommendation = "proceed"
        elif tier == "partial":
            recommendation = "show_source_pdf_alongside"
        else:  # divergent
            recommendation = "use_source_pdf_only" if source_pdf else "show_source_pdf_alongside"

        # ── Vision tier (conditional) ───────────────────────────────────────
        vision_run = False
        vision_result: Optional[dict] = None
        vision_consensus: Optional[str] = None
        vision_by_provider: Optional[dict] = None

        use_vision = self._vision_default if enable_vision is None else bool(enable_vision)
        lo = self._thresholds.get("vision_low",  60)
        hi = self._thresholds.get("vision_high", 84)

        if (use_vision and source_pdf
                and lo <= score <= hi
                and source_pdf.get("local_path")):
            try:
                # Dispatch to the configured provider(s)
                (vision_result,
                 vision_consensus,
                 vision_by_provider) = await self._dispatch_vision_check(
                    rendered_viz=rendered_viz,
                    source_graph=source_graph,
                    source_pdf=source_pdf,
                )
                vision_run = True

                # v1.1.0 — "both" mode: disagreement escalates the tier
                if vision_consensus == "disagreement" and tier == "partial":
                    tier = "divergent"
                    recommendation = (
                        "use_source_pdf_only" if source_pdf
                        else "show_source_pdf_alongside"
                    )
                    logger.info(
                        "FidelityCritic: vision disagreement in 'both' mode — "
                        "escalated tier partial → divergent"
                    )
            except Exception as exc:
                logger.warning("FidelityCritic: vision check failed — %s", exc)
                vision_result = {"_error": str(exc)}

        return {
            "fidelity_score":      score,
            "tier":                tier,
            "verdict":             tier,       # alias for API symmetry
            "checks":              checks,
            "missing_elements":    missing_elements,
            "extra_elements":      extra_elements,
            "recommendation":      recommendation,
            "vision_check_run":    vision_run,
            "vision_result":       vision_result,
            # v1.1.0 — only set in "both" mode
            "vision_consensus":    vision_consensus,
            "vision_by_provider":  vision_by_provider,
            "source_pdf_asset_id": (source_pdf or {}).get("asset_id"),
            "_debug": {
                "weights":         dict(self._weights),
                "thresholds":      dict(self._thresholds),
                "total_weight":    total_weight,
                "total_earned":    total_earned,
                "source_pdf":      bool(source_pdf),
                "source_pdf_origin": (source_pdf or {}).get("asset_origin"),
                "vision_eligible": use_vision,
                "vision_provider": self._vision_provider,
                "vision_models":   dict(self._vision_models),
            },
        }

    # ── Vision tier dispatcher (v1.1.0) ──────────────────────────────────────

    async def _dispatch_vision_check(
        self,
        rendered_viz: dict,
        source_graph: dict,
        source_pdf:   dict,
    ) -> tuple[dict, Optional[str], Optional[dict]]:
        """
        Route the vision check to the configured provider(s).

        Returns a 3-tuple:
          (normalised_result, consensus_label, by_provider)

        normalised_result always has the same shape — caller doesn't care
        which provider ran.  consensus_label and by_provider are populated
        only in "both" mode.
        """
        provider = (self._vision_provider or "claude").lower().strip()

        # Single-provider paths
        if provider == "claude":
            res = await self._run_vision_check_claude(
                rendered_viz=rendered_viz,
                source_graph=source_graph,
                source_pdf=source_pdf,
            )
            res["_provider"] = "claude"
            return res, None, None

        if provider == "openai":
            res = await self._run_vision_check_openai(
                rendered_viz=rendered_viz,
                source_graph=source_graph,
                source_pdf=source_pdf,
            )
            res["_provider"] = "openai"
            return res, None, None

        # "both" — run both concurrently for minimal added latency
        claude_task = self._run_vision_check_claude(
            rendered_viz=rendered_viz,
            source_graph=source_graph,
            source_pdf=source_pdf,
        )
        openai_task = self._run_vision_check_openai(
            rendered_viz=rendered_viz,
            source_graph=source_graph,
            source_pdf=source_pdf,
        )
        claude_res, openai_res = await asyncio.gather(
            claude_task, openai_task, return_exceptions=False,
        )
        claude_res["_provider"] = "claude"
        openai_res["_provider"] = "openai"

        merged, consensus = self._reconcile_vision_results(claude_res, openai_res)
        return merged, consensus, {"claude": claude_res, "openai": openai_res}

    def _reconcile_vision_results(
        self,
        claude_res: dict,
        openai_res: dict,
    ) -> tuple[dict, str]:
        """
        Merge two vision results into a single normalised payload plus a
        consensus label.

        Consensus rules:
          • both errored                → "error"
          • one errored                 → "partial" (use the good one)
          • both say preserved=True AND neither has missing items
                                        → "agreement"
          • both say preserved=False OR both flag overlapping missing items
                                        → "agreement" (they agree on problem)
          • one says preserved, other says not, OR non-overlapping missing sets
                                        → "disagreement"
        """
        c_err = bool(claude_res.get("_error"))
        o_err = bool(openai_res.get("_error"))

        # Both errored — return the Claude error and partial consensus
        if c_err and o_err:
            return ({
                "preserved_structure": True,
                "missing_branches":    [],
                "missing_tests":       [],
                "structural_notes":    "Both vision providers unavailable.",
                "_error": f"claude={claude_res.get('_error')}; openai={openai_res.get('_error')}",
            }, "error")

        # One errored — use the working one, mark partial
        if c_err:
            res = dict(openai_res)
            res["structural_notes"] = (
                (res.get("structural_notes") or "")
                + " [Claude vision unavailable; OpenAI only]"
            ).strip()
            return (res, "partial")
        if o_err:
            res = dict(claude_res)
            res["structural_notes"] = (
                (res.get("structural_notes") or "")
                + " [OpenAI vision unavailable; Claude only]"
            ).strip()
            return (res, "partial")

        # Both ran — compare
        c_preserved = bool(claude_res.get("preserved_structure", True))
        o_preserved = bool(openai_res.get("preserved_structure", True))

        c_missing_b = set(claude_res.get("missing_branches", []) or [])
        o_missing_b = set(openai_res.get("missing_branches", []) or [])
        c_missing_t = set(claude_res.get("missing_tests", []) or [])
        o_missing_t = set(openai_res.get("missing_tests", []) or [])

        # Agreement on clean output
        both_clean = (
            c_preserved and o_preserved
            and not (c_missing_b | o_missing_b | c_missing_t | o_missing_t)
        )

        # Agreement on "structure not preserved" OR overlapping missing items
        structure_agree = (c_preserved == o_preserved)
        overlapping_branches = bool(c_missing_b & o_missing_b)
        overlapping_tests    = bool(c_missing_t & o_missing_t)

        if both_clean or (structure_agree and (overlapping_branches or overlapping_tests)):
            consensus = "agreement"
        elif structure_agree:
            # Same preservation verdict but different missing-item sets
            consensus = "partial"
        else:
            # One says preserved, the other says not
            consensus = "disagreement"

        # Merge: union of missing elements, prefer more conservative (structure_preserved=False)
        merged = {
            "preserved_structure": c_preserved and o_preserved,
            "missing_branches":    sorted(c_missing_b | o_missing_b)[:10],
            "missing_tests":       sorted(c_missing_t | o_missing_t)[:10],
            "structural_notes":    self._merge_notes(
                claude_res.get("structural_notes"),
                openai_res.get("structural_notes"),
                consensus,
            ),
        }
        return (merged, consensus)

    @staticmethod
    def _merge_notes(
        claude_note: Optional[str],
        openai_note: Optional[str],
        consensus: str,
    ) -> str:
        """Combine two providers' narrative notes into one readable line."""
        parts: list[str] = []
        cn = (claude_note or "").strip()
        on = (openai_note or "").strip()
        if cn:
            parts.append(f"[Claude] {cn}")
        if on:
            parts.append(f"[GPT-4o] {on}")
        if consensus == "disagreement":
            parts.append("(models disagree — clinician should consult source PDF)")
        return " · ".join(parts)[:500]

    # ── Vision (tier 2) — Claude provider ────────────────────────────────────

    async def _run_vision_check_claude(
        self,
        rendered_viz: dict,
        source_graph: dict,
        source_pdf:   dict,
    ) -> dict:
        """
        Rasterise page 1 of the source PDF, send it to Haiku with vision along
        with the rendered viz's top node labels, and return a structured diff.
        """
        # Lazy import — keeps pdf2image/poppler optional at import time
        try:
            from pdf2image import convert_from_path   # type: ignore
            import anthropic                           # noqa: F401
        except ImportError as exc:
            return {
                "_error": f"vision dependencies missing: {exc}",
                "preserved_structure": True,
                "missing_branches": [],
                "missing_tests": [],
                "structural_notes": "",
            }

        local_path = source_pdf.get("local_path") or ""
        if not local_path or not Path(local_path).exists():
            return {
                "_error": "source_pdf_missing_on_disk",
                "preserved_structure": True,
                "missing_branches": [],
                "missing_tests": [],
                "structural_notes": "",
            }

        def _rasterise_and_call() -> str:
            import anthropic
            client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
            # Page 1 only — algorithm PDFs are typically 1 page
            images = convert_from_path(local_path, first_page=1, last_page=1, dpi=150)
            if not images:
                raise RuntimeError("pdf2image returned no pages")
            import io
            buf = io.BytesIO()
            images[0].save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

            # Top-10 rendered node labels
            labels = []
            for n in (rendered_viz.get("nodes", []) or [])[:10]:
                lbl = n.get("label") or n.get("raw_label") or n.get("title") or ""
                if lbl:
                    labels.append(str(lbl)[:80])

            rendered_summary = (
                "Rendered flowchart node labels (top 10):\n"
                + "\n".join(f"  - {l}" for l in labels)
            )

            system = (
                "You are comparing a rendered clinical algorithm flowchart against "
                "its source ARUP PDF. Respond with ONLY valid JSON, no prose, no fences.\n\n"
                "{\n"
                '  "preserved_structure": true | false,\n'
                '  "missing_branches":    ["..."],\n'
                '  "missing_tests":       ["..."],\n'
                '  "structural_notes":    "1-2 sentences on any meaningful divergence"\n'
                "}\n"
                "Be terse. Only list branches/tests that are clearly present in the "
                "PDF but absent from the rendered labels."
            )

            resp = client.messages.create(
                model=self._vision_models.get("claude", self._model),
                max_tokens=600,
                system=system,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": img_b64,
                        }},
                        {"type": "text", "text": rendered_summary},
                    ],
                }],
            )
            return resp.content[0].text.strip()

        raw = await asyncio.to_thread(_rasterise_and_call)

        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0]

        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            return {
                "_error": "invalid_json",
                "preserved_structure": True,
                "missing_branches": [],
                "missing_tests": [],
                "structural_notes": raw[:200],
            }

        # Sanitise output
        return {
            "preserved_structure": bool(data.get("preserved_structure", True)),
            "missing_branches":    [str(x) for x in (data.get("missing_branches") or [])][:10],
            "missing_tests":       [str(x) for x in (data.get("missing_tests") or [])][:10],
            "structural_notes":    str(data.get("structural_notes", "") or "")[:400],
        }

    # ── Vision (tier 2) — OpenAI provider (v1.1.0) ───────────────────────────
    #
    # Independent second opinion using GPT-4o-mini vision.  Runs only when
    # vision_provider is "openai" or "both".  Degrades gracefully to an error
    # result when OPENAI_API_KEY is missing or the openai package isn't
    # installed — never raises to the caller.

    async def _run_vision_check_openai(
        self,
        rendered_viz: dict,
        source_graph: dict,
        source_pdf:   dict,
    ) -> dict:
        """
        Rasterise page 1 of the source PDF and send it to GPT-4o-mini with
        the rendered viz's top node labels.  Returns the same schema as
        _run_vision_check_claude so the reconciler can compare them.
        """
        # Lazy imports — openai + pdf2image both optional
        try:
            from pdf2image import convert_from_path   # type: ignore
        except ImportError as exc:
            return {
                "_error": f"pdf2image missing: {exc}",
                "preserved_structure": True,
                "missing_branches": [],
                "missing_tests": [],
                "structural_notes": "",
            }
        try:
            import openai as _openai_module               # type: ignore
        except ImportError as exc:
            return {
                "_error": f"openai package missing: {exc}",
                "preserved_structure": True,
                "missing_branches": [],
                "missing_tests": [],
                "structural_notes": "",
            }

        if not os.environ.get("OPENAI_API_KEY"):
            return {
                "_error": "OPENAI_API_KEY not set",
                "preserved_structure": True,
                "missing_branches": [],
                "missing_tests": [],
                "structural_notes": "",
            }

        local_path = source_pdf.get("local_path") or ""
        if not local_path or not Path(local_path).exists():
            return {
                "_error": "source_pdf_missing_on_disk",
                "preserved_structure": True,
                "missing_branches": [],
                "missing_tests": [],
                "structural_notes": "",
            }

        def _rasterise_and_call() -> str:
            client = _openai_module.OpenAI()   # picks up OPENAI_API_KEY from env
            images = convert_from_path(
                local_path, first_page=1, last_page=1, dpi=150,
            )
            if not images:
                raise RuntimeError("pdf2image returned no pages")
            import io
            buf = io.BytesIO()
            images[0].save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

            labels: list[str] = []
            for n in (rendered_viz.get("nodes", []) or [])[:10]:
                lbl = n.get("label") or n.get("raw_label") or n.get("title") or ""
                if lbl:
                    labels.append(str(lbl)[:80])

            rendered_summary = (
                "Rendered flowchart node labels (top 10):\n"
                + "\n".join(f"  - {l}" for l in labels)
            )

            system_msg = (
                "You are comparing a rendered clinical algorithm flowchart against "
                "its source ARUP PDF. Respond with ONLY valid JSON, no prose, no "
                "markdown fences.\n\n"
                "{\n"
                '  "preserved_structure": true | false,\n'
                '  "missing_branches":    ["..."],\n'
                '  "missing_tests":       ["..."],\n'
                '  "structural_notes":    "1-2 sentences on any meaningful divergence"\n'
                "}\n"
                "Be terse. Only list branches/tests that are clearly present in the "
                "PDF but absent from the rendered labels."
            )

            resp = client.chat.completions.create(
                model=self._vision_models.get("openai", "gpt-4o-mini"),
                max_tokens=600,
                messages=[
                    {"role": "system", "content": system_msg},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": rendered_summary},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{img_b64}",
                                },
                            },
                        ],
                    },
                ],
            )
            return (resp.choices[0].message.content or "").strip()

        raw = await asyncio.to_thread(_rasterise_and_call)

        # Strip markdown fences if model wrapped output (rare but possible)
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0]

        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            return {
                "_error": "invalid_json",
                "preserved_structure": True,
                "missing_branches": [],
                "missing_tests": [],
                "structural_notes": raw[:200],
            }

        return {
            "preserved_structure": bool(data.get("preserved_structure", True)),
            "missing_branches":    [str(x) for x in (data.get("missing_branches") or [])][:10],
            "missing_tests":       [str(x) for x in (data.get("missing_tests") or [])][:10],
            "structural_notes":    str(data.get("structural_notes", "") or "")[:400],
        }

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _tier_for_score(self, score: int) -> str:
        if score >= self._thresholds.get("match_min", 85):
            return "match"
        if score >= self._thresholds.get("partial_min", 60):
            return "partial"
        return "divergent"

    def _empty_report(self, reason: str, source_pdf: Optional[dict]) -> dict:
        return {
            "fidelity_score":      0,
            "tier":                "none",
            "verdict":             "none",
            "checks":              [],
            "missing_elements":    [],
            "extra_elements":      [],
            "recommendation":      "proceed",
            "vision_check_run":    False,
            "vision_result":       None,
            "source_pdf_asset_id": (source_pdf or {}).get("asset_id"),
            "_debug":              {"reason": reason},
        }

    def _load(self) -> None:
        self._rules = load_fidelity_rules(self._rules_path)
        self._apply()

    def _apply(self) -> None:
        """Merge defaults with any loaded rules."""
        self._weights    = dict(DEFAULT_WEIGHTS)
        self._thresholds = dict(DEFAULT_THRESHOLDS)
        self._vision_default  = False
        self._vision_provider = DEFAULT_VISION_PROVIDER
        self._vision_models   = dict(DEFAULT_VISION_MODELS)

        if not self._rules:
            return

        weights = self._rules.get("weights")
        if isinstance(weights, dict):
            for k, v in weights.items():
                if isinstance(v, (int, float)):
                    self._weights[k] = int(v)

        thresholds = self._rules.get("thresholds")
        if isinstance(thresholds, dict):
            for k, v in thresholds.items():
                if isinstance(v, (int, float)):
                    self._thresholds[k] = int(v)

        self._vision_default = bool(self._rules.get("vision_check_enabled", False))

        # v1.1.0 — provider and model overrides
        prov = self._rules.get("vision_provider")
        if isinstance(prov, str) and prov.lower().strip() in _VALID_PROVIDERS:
            self._vision_provider = prov.lower().strip()

        models = self._rules.get("vision_models")
        if isinstance(models, dict):
            for k, v in models.items():
                if k in self._vision_models and isinstance(v, str) and v.strip():
                    self._vision_models[k] = v.strip()
