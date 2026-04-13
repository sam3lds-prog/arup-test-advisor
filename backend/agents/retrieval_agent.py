"""
retrieval_agent.py  v0.7.0
────────────────────────────────────────────────────────────────────────────
Retrieval Agent — planner-driven vector search with metadata filtering

Changes from v0.6:
  • Targeted queries now push source-type filter INTO the ChromaDB query
    (using the new `where` parameter on store.search) rather than filtering
    AFTER retrieval.  This prevents high-recall unfiltered results from
    crowding out the targeted source type.
  • Broad fallback query still runs unfiltered for full coverage.
  • Dedup and re-ranking logic unchanged.
"""

from knowledge.store import VectorStore
from agents.retrieval_planner import RetrievalPlanner, DEFAULT_WEIGHTS

# Map of source_type strings to ChromaDB metadata filter dicts.
# These mirror the source_type values written by the processor.
_SOURCE_FILTER: dict[str, dict] = {
    "Algorithm":      {"source_type": {"$eq": "Algorithm"}},
    "Consult Topic":  {"source_type": {"$eq": "Consult Topic"}},
    "Fact Sheet":     {"source_type": {"$eq": "Fact Sheet"}},
    "Test Directory": {"source_type": {"$eq": "Test Directory"}},
}


class RetrievalAgent:
    def __init__(self, vector_store: VectorStore):
        self.store    = vector_store
        self.planner  = RetrievalPlanner()

    async def retrieve(self, intent: dict) -> list:
        intent_type    = intent.get("intent_type", "ambiguous")
        source_weights = self.planner.get_source_weights(intent_type)
        planned_queries = self.planner.plan_queries(intent)

        if not planned_queries:
            q = (intent.get("search_queries") or ["laboratory test"])[0]
            planned_queries = [{
                "query": q,
                "filter_source_type": None,
                "priority": 3,
                "n_results": 8,
            }]

        # ── Execute planned queries ──────────────────────────────────────────
        seen_ids: set[str] = set()
        results: list[dict] = []

        for pq in planned_queries:
            query       = pq["query"]
            filter_type = pq.get("filter_source_type")
            n           = pq.get("n_results", 6)

            # Push filter into the ChromaDB query when a target source type
            # is specified — avoids post-retrieval filtering misses.
            where = _SOURCE_FILTER.get(filter_type) if filter_type else None

            chunks = self.store.search(query, n_results=n, where=where)

            for chunk in chunks:
                cid = chunk.get("id", "")

                # Secondary guard: post-filter still runs as a safety net in case
                # the ChromaDB filter returned edge-case results.
                if filter_type and chunk.get("source_type") != filter_type:
                    continue

                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    results.append(chunk)

        # ── Intent-aware re-ranking ──────────────────────────────────────────
        results.sort(
            key=lambda c: (
                source_weights.get(c.get("source_type", "General"), 0),
                c.get("score", 0.0),
            ),
            reverse=True,
        )

        return results[:16]