"""
evidence_packager.py  v0.7.0
────────────────────────────────────────────────────────────────────────────
Evidence Bundle Packager

Transforms a flat list of retrieval chunks into a structured evidence bundle
before it reaches the Response Agent. This step:

  • Deduplicates overlapping chunks
  • Groups evidence by candidate test
  • Separates authoritative ARUP evidence from context-only evidence
  • Attaches "source role" metadata (why this source was included)
  • Detects potential conflicts across sources
  • Surfaces evidence gaps (missing content types)

The Response Agent then reasons over this bundle instead of raw chunks,
producing safer and more explainable outputs.
"""

from collections import defaultdict
from typing import List, Dict, Optional

# ── Source role mapping ──────────────────────────────────────────────────────

SOURCE_ROLES: Dict[str, str] = {
    "Algorithm":     "testing pathway",
    "Consult Topic": "clinical rationale",
    "Fact Sheet":    "interpretation & caveats",
    "Test Directory": "order logistics",
    "General":       "general reference",
}

AUTHORITY_TYPES = {"Algorithm", "Consult Topic", "Fact Sheet", "Test Directory"}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _source_role(source_type: str) -> str:
    return SOURCE_ROLES.get(source_type, "general reference")


def _group_key(chunk: dict) -> str:
    """
    Return a stable, deduplicated grouping key for this chunk.

    Priority (highest → lowest):
      1. test_id  — stable numeric/string identifier from Test Directory
      2. test_name — human label from any source type
      3. filename stem — safe fallback for algorithm/consult/fact-sheet chunks
      4. first non-empty text line — last resort

    store.py v0.7 flattens all metadata to the top-level chunk dict.
    We also check the legacy chunk.get("metadata", {}) sub-dict defensively
    to handle any chunks ingested before the schema change.
    """
    # ── Top-level (store.py v0.7 flattened schema) ───────────────────────────
    test_id   = str(chunk.get("test_id", "") or "").strip()
    test_name = str(chunk.get("test_name", "") or "").strip()

    # ── Defensive fallback: legacy nested metadata ────────────────────────────
    meta = chunk.get("metadata") or {}
    if isinstance(meta, dict):
        if not test_id:
            test_id   = str(meta.get("test_id", "") or "").strip()
        if not test_name:
            test_name = str(meta.get("test_name") or meta.get("title") or "").strip()

    # ── Return in priority order ──────────────────────────────────────────────
    if test_id:
        return f"tid:{test_id}"          # unique, stable — best for dedup

    if test_name:
        return test_name[:80]

    filename = chunk.get("filename", "")
    if filename:
        stem = filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip()
        if stem:
            return stem[:80]

    # Text heuristic last resort
    for line in chunk.get("text", "").split("\n")[:5]:
        line = line.strip()
        if line and 3 < len(line) < 100 and not line.startswith("{"):
            return line[:80]

    return "Unknown"


def _display_name(chunk: dict, group_key: str) -> str:
    """
    Human-readable test name for display in evidence bundles.

    When grouped by test_id, prefer the test_name field for readability.
    Falls back to the group_key with the 'tid:' prefix stripped.
    """
    if group_key.startswith("tid:"):
        # Try to get a readable name alongside the numeric ID
        name = (
            str(chunk.get("test_name", "") or "").strip()
            or str((chunk.get("metadata") or {}).get("test_name", "") or "").strip()
        )
        if name:
            return name[:80]
        return group_key[4:]  # strip 'tid:' prefix — show raw ID if no name

    return group_key[:80]


def _extract_test_name(chunk: dict) -> str:
    """Compatibility shim — returns display name for a single chunk."""
    return _display_name(chunk, _group_key(chunk))


def _extract_field(chunks: List[dict], keywords: List[str], max_len: int = 120) -> str:
    """Extract the first line from chunks that contains any of the keywords."""
    for chunk in chunks:
        for line in chunk.get("text", "").split("\n"):
            line_lower = line.lower()
            if any(kw in line_lower for kw in keywords):
                return line.strip()[:max_len]
    return ""


def _detect_conflicts(chunks: List[dict]) -> List[str]:
    """
    Detect simple within-document and cross-document conflicts.
    Checks for specimen ambiguity (serum vs plasma) and
    conflicting TAT values mentioned across sources.
    """
    conflicts: List[str] = []

    # 1. Specimen ambiguity in same document
    for chunk in chunks:
        text = chunk.get("text", "").lower()
        if "serum" in text and "plasma" in text:
            conflicts.append(
                f"Specimen ambiguity in '{chunk.get('filename', 'unknown')}': "
                "both serum and plasma referenced — verify correct tube type"
            )
            break

    # 2. Conflicting test codes across sources (simple string check)
    test_codes_by_source: Dict[str, set] = defaultdict(set)
    import re
    for chunk in chunks:
        fname = chunk.get("filename", "unknown")
        codes = re.findall(r'\b\d{4,6}\b', chunk.get("text", ""))
        if codes:
            test_codes_by_source[fname].update(codes)

    if len(test_codes_by_source) >= 2:
        all_code_lists = list(test_codes_by_source.values())
        # If two sources share no codes at all and both have codes, flag it
        if (all_code_lists[0] and all_code_lists[1] and
                not all_code_lists[0].intersection(all_code_lists[1])):
            sources = list(test_codes_by_source.keys())[:2]
            conflicts.append(
                f"Different test codes referenced in '{sources[0]}' vs "
                f"'{sources[1]}' — confirm correct order code"
            )

    return conflicts[:3]   # Cap at 3 to avoid noise


# ── Evidence Packager ────────────────────────────────────────────────────────

class EvidencePackager:
    """
    Packages raw retrieval chunks into a structured evidence bundle.

    Output schema:
    {
      "candidate_tests": [
        {
          "test_name": str,
          "supporting_clinical_rationale": [{"text", "source_type", "filename"}],
          "ordering_constraints": {"specimen", "tat", "raw_chunks"},
          "algorithm_support": [{"text", "filename"}],
          "fact_sheet_caveats": [{"text", "filename"}],
          "confidence_signals": {
            "has_algorithm", "has_consult", "has_directory", "has_fact_sheet",
            "chunk_count", "mean_score"
          }
        }
      ],
      "unresolved_conflicts": [str],
      "missing_context": [str],
      "authority_sources": [chunk],
      "context_sources": [chunk],
      "total_chunks": int
    }
    """

    def package(self, chunks: List[dict], intent_type: str = "ambiguous") -> dict:
        if not chunks:
            return {
                "candidate_tests": [],
                "unresolved_conflicts": [],
                "missing_context": ["No evidence retrieved from knowledge base"],
                "authority_sources": [],
                "context_sources": [],
                "total_chunks": 0,
            }

        # ── 1. Tag source roles, split authority vs context ──────────────────
        authority_sources: List[dict] = []
        context_sources: List[dict] = []

        for chunk in chunks:
            st = chunk.get("source_type", "General")
            chunk = dict(chunk)   # shallow copy — don't mutate originals
            chunk["source_role"] = _source_role(st)
            if st in AUTHORITY_TYPES:
                authority_sources.append(chunk)
            else:
                context_sources.append(chunk)

        # ── 2. Group authority chunks by stable group key ────────────────────
        test_groups: Dict[str, List[dict]] = defaultdict(list)
        group_display: Dict[str, str] = {}          # group_key → display name

        for chunk in authority_sources:
            gk   = _group_key(chunk)
            name = _display_name(chunk, gk)
            test_groups[gk].append(chunk)
            # Keep the first (usually most informative) display name per group
            if gk not in group_display:
                group_display[gk] = name

        # ── 3. Build candidate test objects ──────────────────────────────────
        candidate_tests = []
        for gk, test_chunks in test_groups.items():
            test_name = group_display.get(gk, gk.lstrip("tid:"))[:80]
            clinical = [c for c in test_chunks
                        if c.get("source_type") in {"Consult Topic", "Algorithm"}]
            ordering = [c for c in test_chunks
                        if c.get("source_type") == "Test Directory"]
            alg      = [c for c in test_chunks
                        if c.get("source_type") == "Algorithm"]
            facts    = [c for c in test_chunks
                        if c.get("source_type") == "Fact Sheet"]

            specimen = _extract_field(ordering, ["specimen", "collection", "tube", "blood", "urine", "csf"])
            tat      = _extract_field(ordering, ["tat", "turnaround", "days", "hours"])

            mean_score = (
                sum(c.get("score", 0) for c in test_chunks) / len(test_chunks)
                if test_chunks else 0.0
            )

            candidate_tests.append({
                "test_name": test_name,
                "supporting_clinical_rationale": [
                    {
                        "text": c.get("text", "")[:350],
                        "source_type": c.get("source_type"),
                        "filename": c.get("filename"),
                    }
                    for c in clinical
                ],
                "ordering_constraints": {
                    "specimen": specimen,
                    "tat": tat,
                    "raw_chunks": [c.get("text", "")[:220] for c in ordering],
                },
                "algorithm_support": [
                    {"text": c.get("text", "")[:350], "filename": c.get("filename")}
                    for c in alg
                ],
                "fact_sheet_caveats": [
                    {"text": c.get("text", "")[:350], "filename": c.get("filename")}
                    for c in facts
                ],
                "confidence_signals": {
                    "has_algorithm":      any(c.get("source_type") == "Algorithm"      for c in test_chunks),
                    "has_consult_topic":  any(c.get("source_type") == "Consult Topic"  for c in test_chunks),
                    "has_test_directory": any(c.get("source_type") == "Test Directory" for c in test_chunks),
                    "has_fact_sheet":     any(c.get("source_type") == "Fact Sheet"     for c in test_chunks),
                    # Backward-compat aliases used by FormattingAgent._coverage_badges
                    "has_consult":        any(c.get("source_type") == "Consult Topic"  for c in test_chunks),
                    "has_directory":      any(c.get("source_type") == "Test Directory" for c in test_chunks),
                    # tier_count: number of distinct ARUP source types present (used by ConfidenceAgent)
                    "tier_count":         sum([
                        any(c.get("source_type") == "Algorithm"      for c in test_chunks),
                        any(c.get("source_type") == "Consult Topic"  for c in test_chunks),
                        any(c.get("source_type") == "Test Directory" for c in test_chunks),
                        any(c.get("source_type") == "Fact Sheet"     for c in test_chunks),
                    ]),
                    "chunk_count":        len(test_chunks),
                    "mean_score":         round(mean_score, 4),
                },
                "chunk_count": len(test_chunks),
            })

        # Sort candidates: multi-tier evidence first, then by chunk volume + score
        def _candidate_sort_key(ct: dict) -> tuple:
            cs = ct["confidence_signals"]
            tier_count = cs.get("tier_count", sum([
                cs["has_algorithm"], cs["has_consult_topic"],
                cs["has_test_directory"], cs["has_fact_sheet"],
            ]))
            return (tier_count, cs["chunk_count"], cs["mean_score"])

        candidate_tests.sort(key=_candidate_sort_key, reverse=True)

        # ── 4. Detect conflicts ───────────────────────────────────────────────
        conflicts = _detect_conflicts(authority_sources)

        # ── 5. Identify evidence gaps ─────────────────────────────────────────
        types_present = {c.get("source_type", "General") for c in chunks}
        missing: List[str] = []

        if "Test Directory" not in types_present:
            missing.append(
                "No test directory entries retrieved — specimen and TAT details may be incomplete"
            )
        if "Algorithm" not in types_present and "Consult Topic" not in types_present:
            missing.append(
                "No algorithm or consult topic retrieved — clinical rationale may be limited"
            )
        if "Fact Sheet" not in types_present:
            missing.append(
                "No fact sheet retrieved — interpretation caveats unavailable"
            )

        # ── 6. Build top-level algorithm index ───────────────────────────────
        # Provides a direct, flat list of all retrieved algorithm source files
        # so AlgorithmRenderer can find them even when candidate grouping is
        # imperfect (ChatGPT fallback path — Path 2 / Path 3).
        seen_algo: set = set()
        algorithm_sources: List[dict] = []
        for chunk in authority_sources:
            if chunk.get("source_type") != "Algorithm":
                continue
            fname = (chunk.get("filename") or "").strip()
            if not fname or fname in seen_algo:
                continue
            seen_algo.add(fname)
            algorithm_sources.append({
                "filename":    fname,
                "source_url":  chunk.get("source_url", ""),
                "score":       chunk.get("score", 0.0),
                "text_excerpt": chunk.get("text", "")[:200],
            })

        return {
            "candidate_tests":      candidate_tests[:6],
            "algorithm_sources":    algorithm_sources,   # flat index for renderer fallback
            "unresolved_conflicts": conflicts,
            "missing_context":      missing,
            "authority_sources":    authority_sources,
            "context_sources":      context_sources,
            "total_chunks":         len(chunks),
        }