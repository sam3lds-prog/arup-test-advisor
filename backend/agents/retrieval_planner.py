"""
retrieval_planner.py  v0.6.0
────────────────────────────────────────────────────────────────────────────
Retrieval Planner Agent

Replaces flat semantic search with a deterministic, intent-driven retrieval
plan. Answers: "which content types to fetch, in what order, with which queries."

Intent → ordered source priority:
  disease_to_test   → Algorithm > Consult Topic > Fact Sheet > Test Directory
  specimen_question → Test Directory > Fact Sheet > Consult Topic > Algorithm
  test_lookup       → Test Directory > Fact Sheet > Consult Topic > Algorithm
  interpretation    → Fact Sheet > Consult Topic > Algorithm > Test Directory
  monitoring        → Consult Topic > Fact Sheet > Algorithm > Test Directory
  ambiguous         → Algorithm > Consult Topic > Fact Sheet > Test Directory
"""

from typing import List, Dict, Optional

# ── Intent → ordered source priority ────────────────────────────────────────

INTENT_SOURCE_WEIGHTS: Dict[str, Dict[str, int]] = {
    "disease_to_test": {
        "Algorithm": 4, "Consult Topic": 3, "Fact Sheet": 2,
        "Test Directory": 1, "General": 0,
    },
    "specimen_question": {
        "Test Directory": 4, "Fact Sheet": 3, "Consult Topic": 2,
        "Algorithm": 1, "General": 0,
    },
    "test_lookup": {
        "Test Directory": 4, "Fact Sheet": 3, "Consult Topic": 2,
        "Algorithm": 1, "General": 0,
    },
    "interpretation": {
        "Fact Sheet": 4, "Consult Topic": 3, "Algorithm": 2,
        "Test Directory": 1, "General": 0,
    },
    "monitoring": {
        "Consult Topic": 4, "Fact Sheet": 3, "Algorithm": 2,
        "Test Directory": 1, "General": 0,
    },
    "ambiguous": {
        "Algorithm": 4, "Consult Topic": 3, "Fact Sheet": 2,
        "Test Directory": 1, "General": 0,
    },
}

DEFAULT_WEIGHTS = INTENT_SOURCE_WEIGHTS["ambiguous"]

# Plans: which source types are primary for this intent, and desired coverage
INTENT_RETRIEVAL_PLANS: Dict[str, dict] = {
    "disease_to_test": {
        "primary_sources": ["Algorithm", "Consult Topic"],
        "secondary_sources": ["Fact Sheet", "Test Directory"],
        "description": "Disease → test mapping: prioritise diagnostic pathways",
        "targeted_n": 8,   # chunks per targeted source-specific query
        "broad_n": 5,      # chunks for broad (unfiltered) queries
    },
    "specimen_question": {
        "primary_sources": ["Test Directory"],
        "secondary_sources": ["Fact Sheet", "Consult Topic"],
        "description": "Specimen/logistics: prioritise test directory",
        "targeted_n": 8,
        "broad_n": 5,
    },
    "test_lookup": {
        "primary_sources": ["Test Directory", "Fact Sheet"],
        "secondary_sources": ["Consult Topic"],
        "description": "Direct test lookup: directory + fact sheet first",
        "targeted_n": 8,
        "broad_n": 4,
    },
    "interpretation": {
        "primary_sources": ["Fact Sheet", "Consult Topic"],
        "secondary_sources": ["Algorithm", "Test Directory"],
        "description": "Interpretation: fact sheets + clinical guidance",
        "targeted_n": 8,
        "broad_n": 4,
    },
    "monitoring": {
        "primary_sources": ["Consult Topic", "Fact Sheet"],
        "secondary_sources": ["Algorithm", "Test Directory"],
        "description": "Monitoring: consult topics + follow-up guidelines",
        "targeted_n": 7,
        "broad_n": 4,
    },
    "ambiguous": {
        "primary_sources": ["Algorithm", "Consult Topic", "Fact Sheet", "Test Directory"],
        "secondary_sources": [],
        "description": "Ambiguous: broad retrieval across all sources",
        "targeted_n": 5,
        "broad_n": 6,
    },
}


class RetrievalPlanner:
    """
    Converts intent → a concrete list of (query, source_filter, priority) triples.
    Each triple is executed as a separate vector search call.
    """

    def get_plan(self, intent_type: str) -> dict:
        return INTENT_RETRIEVAL_PLANS.get(intent_type, INTENT_RETRIEVAL_PLANS["ambiguous"])

    def get_source_weights(self, intent_type: str) -> Dict[str, int]:
        return INTENT_SOURCE_WEIGHTS.get(intent_type, DEFAULT_WEIGHTS)

    def plan_queries(self, intent: dict) -> List[Dict]:
        """
        Returns a list of planned queries:
          [{ "query": str, "filter_source_type": str | None, "priority": int, "n_results": int }]

        Priority 1 = most important (targeted, primary source type).
        Priority 2 = secondary targeted.
        Priority 3 = broad/unfiltered.
        """
        intent_type = intent.get("intent_type", "ambiguous")
        plan = self.get_plan(intent_type)

        # Build unique term pool (up to 6 terms)
        all_terms: List[str] = []
        seen: set = set()
        for term in (
            intent.get("search_queries", [])
            + intent.get("conditions", [])
            + intent.get("tests_mentioned", [])
            + intent.get("clinical_concepts", [])
        ):
            t = term.strip()
            if t and t.lower() not in seen:
                all_terms.append(t)
                seen.add(t.lower())
            if len(all_terms) >= 6:
                break

        if not all_terms:
            # Absolute fallback
            q = intent.get("medical_context", "laboratory test") or "laboratory test"
            all_terms = [q[:120]]

        planned: List[Dict] = []

        # Priority 1: top 2 terms × primary source types (most targeted)
        for source_type in plan["primary_sources"]:
            for term in all_terms[:2]:
                planned.append({
                    "query": term,
                    "filter_source_type": source_type,
                    "priority": 1,
                    "n_results": plan["targeted_n"],
                })

        # Priority 2: top 2 terms × secondary source types
        for source_type in plan.get("secondary_sources", []):
            for term in all_terms[:2]:
                planned.append({
                    "query": term,
                    "filter_source_type": source_type,
                    "priority": 2,
                    "n_results": plan["targeted_n"],
                })

        # Priority 3: broad queries (no source filter) for full coverage
        for term in all_terms[:4]:
            planned.append({
                "query": term,
                "filter_source_type": None,
                "priority": 3,
                "n_results": plan["broad_n"],
            })

        return planned
