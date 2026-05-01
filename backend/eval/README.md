# ARUP Retrieval Eval Harness

Read-only retrieval evaluator for the ARUP AI Test Advisor. Lets you compare
embedding/reranking configurations on a curated set of clinical queries
without touching the running app.

---

## Quick start

```bash
cd backend
source .venv/bin/activate

# 1. Baseline — legacy MiniLM, no reranker
python -m eval.run_retrieval_eval --label baseline_minilm \
    --provider minilm --no-reranker

# 2. BGE only
python -m eval.run_retrieval_eval --label bge_only \
    --provider hf --no-reranker

# 3. BGE + reranker (proposed production stack)
python -m eval.run_retrieval_eval --label bge_plus_rerank \
    --provider hf --reranker

# 4. Compare two runs
python -m eval.compare_runs baseline_minilm bge_plus_rerank
```

> **Note on running run #1.** The MiniLM baseline reads from the legacy
> ChromaDB collection `arup_knowledge`. If you've already done the hard
> cutover and dropped that collection, the MiniLM baseline run will report
> "collection empty". To still run it, restore the legacy collection from
> backup or skip the baseline and compare BGE-only vs BGE+rerank instead.

---

## What gets measured

| Metric | What it captures |
|---|---|
| **Recall@5** | Fraction of cases where a known-relevant chunk appears in the top 5 |
| **Recall@10** | Same, top 10 |
| **MRR** | Mean Reciprocal Rank — rewards getting the right answer to the very top |
| **src_coverage** | Of the source types we expect for this case, how many turned up? |
| **p50/p95/p99 ms** | Per-case end-to-end latency (search + rerank) |
| **by_difficulty** | The above, sliced by easy / medium / hard |

Each metric is `None` when the case lacks the gold-standard data needed
(e.g. no `relevant_filenames`); those cases simply don't count toward the
mean — they still contribute to source-type coverage and latency.

---

## Adding cases

Cases live in `retrieval_eval_set.jsonl` — one JSON object per line.

Schema:

```json
{
  "id": "celiac_001",
  "query": "55-year-old with chronic diarrhea ...",
  "expected_source_types": ["Algorithm", "Fact Sheet"],
  "expected_tests":        ["Tissue Transglutaminase IgA"],
  "must_not_recommend":    ["AGA IgA only"],
  "relevant_filenames":    ["celiac_algorithm"],
  "relevant_chunk_ids":    [],
  "notes":                 "tTG-IgA + total IgA is first-line.",
  "difficulty":            "easy"
}
```

Required: `id`, `query`. Everything else is optional but improves
metric coverage.

`relevant_filenames` uses **substring matching** (case-insensitive) against
each retrieved chunk's `filename` metadata — so `"celiac"` matches
`Celiac_Disease_Algorithm.pdf`. If you have exact chunk UUIDs (less common),
put them in `relevant_chunk_ids` for stricter matching.

`difficulty` should reflect retrieval-only difficulty — how easy is it for
a vector search to surface the right document? Generally:
- **easy** — single primary topic, well-keyworded
- **medium** — multi-tier evidence needed (algorithm + fact sheet)
- **hard** — atypical phrasing, narrow-scope subspecialty case, IgA-deficient
  variants, etc.

---

## Output files

Every run writes a JSON file to `backend/eval/results/`:

```
results_<label>_<UTC stamp>.json
```

Schema:

```json
{
  "label":     "bge_plus_rerank",
  "ts":        "20260430_154205",
  "config": {
    "provider":  "BAAI/bge-base-en-v1.5",
    "dimension": 768,
    "device":    "mps",
    "reranker":  "BAAI/bge-reranker-v2-m3",
    "top_n":     30,
    "top_k":     10
  },
  "aggregate": { "recall@5": 0.83, "recall@10": 0.91, "mrr": 0.71 },
  "per_case": []
}
```

Keep the `results/` directory in version control so you have a trend log
across days/weeks.

---

## Success target

For the BGE migration to count as a win, expect:

- **Recall@5** improvement ≥ +5 absolute points over the MiniLM baseline,
  on the same eval set.
- **No latency regression** beyond +1.5 s p95 (reranker + retrieval).

If Recall@5 lifts but the system feels slower, tune `RERANKING_TOP_N` down
(20 instead of 30) and `RERANKING_TOP_K` to 6–8.

If Recall@5 doesn't lift, before reverting consider:
1. Adding more cases — 17 is the floor, not the ceiling
2. Trying `EMBEDDING_USE_QUERY_INSTRUCTION=true` (BGE-recommended prefix)
3. Increasing `RERANKING_TOP_N` to 50 and seeing if the answer was
   in positions 30–50 of the bi-encoder ranking
