"""
acceptance_checker.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
Acceptance Criteria Self-Check — Deterministic structural rule engine

Evaluates ResponseAgent output against a hot-reloadable set of Gherkin-style
acceptance rules before it reaches the user.

Design principles:
  ▸ DETERMINISTIC   — zero LLM calls, ~50ms total runtime
  ▸ HOT-RELOADABLE  — rules live in acceptance_rules.json, reloaded via reload()
  ▸ NON-BLOCKING    — failures append to evidence_gaps, do not reject the response
  ▸ ADDITIVE        — new fields only; never removes or rewrites response content
  ▸ AUDITABLE       — every failure records which rule fired and why

Rule schema (in acceptance_rules.json):
{
  "rules": [
    {
      "id": "oncology_requires_consult",
      "description": "Oncology + tumor marker must have Consult Topic citation",
      "given": {
        "intent_type_in":   ["disease_to_test"],
        "condition_keywords_any": ["cancer", "tumor", "carcinoma", "oncology"]
      },
      "when":  {
        "rank_any": ["primary", "secondary"]
      },
      "then":  {
        "evidence_coverage_requires": ["has_consult"],
        "citation_source_type_any":   ["Consult Topic"]
      },
      "severity": "medium",
      "enabled":  true
    }
  ]
}

Supported checks:
  Given:
    intent_type_in            : intent.intent_type ∈ list
    condition_keywords_any    : any intent.conditions entry contains any keyword
    condition_keywords_all    : every keyword must appear in some condition
  When:
    rank_any                  : any recommendation has rank ∈ list
    min_recommendations       : at least N recommendations present
    specimen_populated        : at least one recommendation has non-empty specimen
  Then:
    min_recommendations       : response has >= N recommendations
    specimen_required         : every matching recommendation has specimen
    tat_required              : every matching recommendation has tat
    evidence_coverage_requires: every matching rec has coverage.<key> True
    citation_source_type_any  : at least one citation has source_type ∈ list
    followup_question_present : follow_up_questions not empty
    reflex_has_primary_parent : if any rank=reflex, must exist rank=primary

Output:
  list of failures, each:
    {
      "rule_id":      str,
      "description":  str,
      "severity":     "low" | "medium" | "high",
      "failed_check": str,
      "details":      str,       # human-readable explanation
      "gap_message":  str,       # text appended to evidence_gaps
    }
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_RULES_FILE = Path(__file__).parent / "acceptance_rules.json"


def load_rules(path: Optional[Path] = None) -> dict:
    """Load acceptance_rules.json; return {} on any error so checker no-ops safely."""
    target = path or _RULES_FILE
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info("AcceptanceChecker: rules loaded from %s", target)
        return data
    except FileNotFoundError:
        logger.info("AcceptanceChecker: no acceptance_rules.json found — no-op")
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("AcceptanceChecker: failed to parse acceptance_rules.json — %s", e)
        return {}


def save_rules(rules: dict, path: Optional[Path] = None) -> None:
    """Persist updated rules to disk."""
    target = path or _RULES_FILE
    with open(target, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=2, ensure_ascii=False)
    logger.info("AcceptanceChecker: rules saved to %s", target)


# ── Check predicates ──────────────────────────────────────────────────────────

def _has_any_keyword(text_list: list[str], keywords: list[str]) -> bool:
    """Return True if any text in text_list contains any keyword (case-insensitive)."""
    lowered = [str(t).lower() for t in (text_list or [])]
    for kw in (keywords or []):
        kw_l = str(kw).lower()
        if any(kw_l in t for t in lowered):
            return True
    return False


def _has_all_keywords(text_list: list[str], keywords: list[str]) -> bool:
    lowered = [str(t).lower() for t in (text_list or [])]
    for kw in (keywords or []):
        kw_l = str(kw).lower()
        if not any(kw_l in t for t in lowered):
            return False
    return True


def _given_matches(given: dict, intent: dict) -> bool:
    """Evaluate the Given clause. If any required condition fails, return False."""
    if not given:
        return True

    # intent_type_in
    allowed = given.get("intent_type_in") or []
    if allowed:
        if intent.get("intent_type") not in allowed:
            return False

    # condition_keywords_any
    any_kws = given.get("condition_keywords_any") or []
    if any_kws:
        if not _has_any_keyword(intent.get("conditions", []), any_kws):
            return False

    # condition_keywords_all
    all_kws = given.get("condition_keywords_all") or []
    if all_kws:
        if not _has_all_keywords(intent.get("conditions", []), all_kws):
            return False

    return True


def _when_matches(when: dict, response: dict) -> list[dict]:
    """
    Evaluate the When clause. Returns the list of recommendations that match.
    If When has no rank_any, all recommendations match.
    Returns empty list if no recs match (caller will skip Then).
    """
    recs = response.get("recommendations", []) or []
    if not when:
        return recs

    ranks = when.get("rank_any") or []
    if ranks:
        recs = [r for r in recs if r.get("rank") in ranks]

    # specimen_populated requires at least one rec with non-empty specimen
    if when.get("specimen_populated") is True:
        if not any((r.get("specimen") or "").strip() for r in recs):
            return []

    # min_recommendations requires N total recs even at this stage
    min_recs = when.get("min_recommendations")
    if isinstance(min_recs, int) and len(recs) < min_recs:
        return []

    return recs


def _check_then(
    then: dict,
    matching_recs: list[dict],
    response: dict,
) -> tuple[bool, str, str]:
    """
    Evaluate the Then clause. Returns (passed, failed_check, detail).
    Short-circuits on first failure.
    """
    if not then:
        return True, "", ""

    citations = response.get("citations", []) or []
    followups = response.get("follow_up_questions", []) or []
    all_recs  = response.get("recommendations", []) or []

    # min_recommendations
    min_recs = then.get("min_recommendations")
    if isinstance(min_recs, int):
        if len(all_recs) < min_recs:
            return False, "min_recommendations", (
                f"Response has {len(all_recs)} recommendation(s); "
                f"at least {min_recs} required."
            )

    # specimen_required — every matching rec must have specimen
    if then.get("specimen_required") is True:
        missing = [r for r in matching_recs if not (r.get("specimen") or "").strip()]
        if missing:
            names = ", ".join(r.get("test_name", "?") for r in missing[:3])
            return False, "specimen_required", (
                f"{len(missing)} matching recommendation(s) missing specimen: {names}"
            )

    # tat_required — every matching rec must have tat
    if then.get("tat_required") is True:
        missing = [r for r in matching_recs if not (r.get("tat") or "").strip()]
        if missing:
            names = ", ".join(r.get("test_name", "?") for r in missing[:3])
            return False, "tat_required", (
                f"{len(missing)} matching recommendation(s) missing TAT: {names}"
            )

    # evidence_coverage_requires — every matching rec must have these coverage keys True
    required = then.get("evidence_coverage_requires") or []
    if required:
        for r in matching_recs:
            cov = r.get("evidence_coverage", {}) or {}
            for key in required:
                if not cov.get(key):
                    return False, "evidence_coverage_requires", (
                        f"Recommendation '{r.get('test_name','?')}' is missing "
                        f"required evidence coverage: {key}"
                    )

    # citation_source_type_any — at least one citation has one of these source types
    allowed_sources = then.get("citation_source_type_any") or []
    if allowed_sources:
        present = {c.get("source_type") for c in citations}
        if not any(s in present for s in allowed_sources):
            return False, "citation_source_type_any", (
                f"No citation of type {allowed_sources} present "
                f"(found: {sorted(s for s in present if s)})"
            )

    # followup_question_present
    if then.get("followup_question_present") is True:
        if not followups:
            return False, "followup_question_present", (
                "Expected a follow-up question to be present but none was provided."
            )

    # reflex_has_primary_parent — if any rec has rank=reflex, there must be a rank=primary
    if then.get("reflex_has_primary_parent") is True:
        has_reflex  = any(r.get("rank") == "reflex"  for r in all_recs)
        has_primary = any(r.get("rank") == "primary" for r in all_recs)
        if has_reflex and not has_primary:
            return False, "reflex_has_primary_parent", (
                "Response has a reflex recommendation but no primary recommendation."
            )

    return True, "", ""


# ── AcceptanceChecker class ───────────────────────────────────────────────────

class AcceptanceChecker:
    """
    Deterministic structural post-check.

    Usage:
        checker = AcceptanceChecker()   # auto-loads acceptance_rules.json
        failures = checker.check(response, intent)
        # Append failures to response.evidence_gaps via the caller.
    """

    def __init__(self, rules_path: Optional[Path] = None):
        self._rules_path = rules_path or _RULES_FILE
        self._rules: dict = {}
        self._load()

    def reload(self) -> None:
        """Hot-reload rules from disk."""
        self._load()

    def get_rules(self) -> dict:
        """Return the current rules dict (for GET /designer/acceptance)."""
        return dict(self._rules)

    def set_rules(self, rules: dict) -> None:
        """Persist and hot-reload."""
        save_rules(rules, self._rules_path)
        self._rules = rules

    # ── Main entry point ──────────────────────────────────────────────────────

    def check(self, response: dict, intent: dict) -> list[dict]:
        """
        Return a list of failure dicts. Empty list means all rules passed.

        Every failure dict includes:
          rule_id, description, severity, failed_check, details, gap_message
        """
        if not self._rules:
            return []

        rules = self._rules.get("rules", []) or []
        failures: list[dict] = []

        for rule in rules:
            if not isinstance(rule, dict):
                continue
            if not rule.get("enabled", True):
                continue

            rule_id     = rule.get("id", "unnamed_rule")
            description = rule.get("description", "")
            severity    = rule.get("severity", "medium")

            # Evaluate Given — if it doesn't apply, skip rule entirely
            try:
                if not _given_matches(rule.get("given", {}), intent):
                    continue

                # Evaluate When — get the matching recommendations subset
                matching = _when_matches(rule.get("when", {}), response)
                # If When required recs but none match, rule doesn't apply
                if rule.get("when") and not matching:
                    continue

                # Evaluate Then
                passed, failed_check, detail = _check_then(
                    rule.get("then", {}), matching, response,
                )

                if not passed:
                    failures.append({
                        "rule_id":      rule_id,
                        "description":  description,
                        "severity":     severity,
                        "failed_check": failed_check,
                        "details":      detail,
                        "gap_message":  f"[Acceptance:{rule_id}] {description}: {detail}",
                    })
            except Exception as exc:
                # Never let a broken rule crash the pipeline
                logger.warning(
                    "AcceptanceChecker: rule '%s' raised %s — skipped",
                    rule_id, exc,
                )
                continue

        return failures

    # ── Private ───────────────────────────────────────────────────────────────

    def _load(self) -> None:
        self._rules = load_rules(self._rules_path)
