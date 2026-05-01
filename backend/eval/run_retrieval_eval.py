"""
run_retrieval_eval.py  v1.0.1
────────────────────────────────────────────────────────────────────────────
Retrieval evaluation harness for the ARUP AI Test Advisor.

Compares baseline (MiniLM) vs BGE-only vs BGE+rerank on a curated set of
ARUP-typical clinical queries. Reports Recall@K, MRR, source-type
coverage, and latency percentiles.

Usage
─────
    cd backend
    source .venv/bin/activate

    # Baseline — legacy MiniLM, no reranker
    python -m eval.run_retrieval_eval --label baseline_minilm \
        --provider minilm --no-reranker

    # BGE only
    python -m eval.run_retrieval_eval --label bge_only \
        --provider hf --no-reranker

    # BGE + reranker (the proposed production stack)
    python -m eval.run_retrieval_eval --label bge_plus_rerank \
        --provider hf --reranker

Each run writes a JSON file to backend/eval/results/. Use compare_runs.py
to diff two runs for trend tracking.

When neither relevant_filenames nor relevant_chunk_ids is provided, the
harness falls back to source-type coverage and latency only — Recall and
MRR are reported as None for those cases.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

# Ensure imports work whether run as `python -m eval.run_retrieval_eval`
# or `python eval/run_retrieval_eval.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledge.store import VectorStore  # noqa: E402

logger = logging.getLogger("eval")


# ── Loading ────────────────────────────────────────────────────────────────────

def load_cases(path: Path) -> List[Dict]:
    cases: List[Dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning("Skipping malformed eval line: %s", exc)
    return cases


def build_provider(kind: str):
    """
    Construct an embedding provider directly via its from_env() factory.
    No central registry — each provider class owns its own env-parsing.
    """
    if kind == "minilm":
        from embeddings.minilm_provider import MiniLMProvider
        return MiniLMProvider.from_env()
    from embeddings.hf_embedding_provider import HfEmbeddingProvider
    return HfEmbeddingProvider.from_env()


def build_reranker(enabled: bool):
    if not enabled:
        return None
    from reranking.reranking_agent import RerankingAgent
    return RerankingAgent.from_env()


# ── Metrics ────────────────────────────────────────────────────────────────────

def _matches(retrieved_chunk: Dict, case: Dict) -> bool:
    """Does this retrieved chunk count as a hit for this case?"""
    chunk_ids = set(case.get("relevant_chunk_ids") or [])
    filenames = set(case.get("relevant_filenames") or [])
    if chunk_ids and retrieved_chunk.get("id") in chunk_ids:
        return True
    if filenames:
        chunk_fname = (retrieved_chunk.get("filename") or "").lower()
        for f in filenames:
            if f.lower() in chunk_fname:
                return True
    return False


def recall_at_k(retrieved_chunks: List[Dict], case: Dict, k: int) -> Optional[float]:
    """Fraction of relevant items found in top-k. None when no gold provided."""
    if not (case.get("relevant_chunk_ids") or case.get("relevant_filenames")):
        return None
    top = retrieved_chunks[:k]
    hit = any(_matches(c, case) for c in top)
    return 1.0 if hit else 0.0


def mrr(retrieved_chunks: List[Dict], case: Dict) -> Optional[float]:
    """Reciprocal rank of the first hit. None when no gold provided."""
    if not (case.get("relevant_chunk_ids") or case.get("relevant_filenames")):
        return None
    for i, c in enumerate(retrieved_chunks, start=1):
        if _matches(c, case):
            return 1.0 / i
    return 0.0


def source_type_coverage(retrieved_chunks: List[Dict], case: Dict) -> Optional[float]:
    expected = set(case.get("expected_source_types") or [])
    if not expected:
        return None
    seen = {c.get("source_type") for c in retrieved_chunks if c.get("source_type")}
    return round(len(seen & expected) / len(expected), 4)


def percentile(xs: List[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


# ── Per-case evaluation ────────────────────────────────────────────────────────

def evaluate_case(
    case: Dict,
    store: VectorStore,
    reranker,
    top_n: int,
    top_k: int,
) -> Dict:
    t0 = time.time()
    try:
        candidates = store.search(case["query"], n_results=top_n)
    except Exception as exc:
        logger.exception("Retrieval failed for case %s (%s)", case.get("id"), exc)
        candidates = []

    if reranker is not None and candidates:
        try:
            ranked = reranker.rerank(case["query"], candidates, top_k=top_k)
        except Exception as exc:
            logger.warning("Rerank failed for case %s (%s)", case.get("id"), exc)
            ranked = candidates[:top_k]
    else:
        ranked = candidates[:top_k]

    elapsed_ms = (time.time() - t0) * 1000.0

    return {
        "id":         case.get("id"),
        "difficulty": case.get("difficulty", "n/a"),
        "recall@5":     recall_at_k(ranked, case, 5),
        "recall@10":    recall_at_k(ranked, case, 10),
        "mrr":          mrr(ranked, case),
        "src_coverage": source_type_coverage(ranked, case),
        "latency_ms":   round(elapsed_ms, 1),
        "top_filenames": [c.get("filename") for c in ranked[:5]],
    }


# ── Aggregation ────────────────────────────────────────────────────────────────

def _mean_of_non_none(xs: List[Optional[float]]) -> Optional[float]:
    vals = [x for x in xs if x is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def aggregate(per_case: List[Dict]) -> Dict:
    lats = [c["latency_ms"] for c in per_case if c.get("latency_ms") is not None]
    by_difficulty: Dict[str, Dict] = defaultdict(lambda: {
        "recall@5": [], "recall@10": [], "mrr": [], "src_coverage": []
    })
    for c in per_case:
        d = c.get("difficulty", "n/a")
        for k in ("recall@5", "recall@10", "mrr", "src_coverage"):
            if c.get(k) is not None:
                by_difficulty[d][k].append(c[k])

    diff_summary = {}
    for d, scores in by_difficulty.items():
        diff_summary[d] = {
            k: (round(sum(v) / len(v), 4) if v else None)
            for k, v in scores.items()
        }

    return {
        "recall@5":     _mean_of_non_none([c.get("recall@5")     for c in per_case]),
        "recall@10":    _mean_of_non_none([c.get("recall@10")    for c in per_case]),
        "mrr":          _mean_of_non_none([c.get("mrr")          for c in per_case]),
        "src_coverage": _mean_of_non_none([c.get("src_coverage") for c in per_case]),
        "p50_ms":       round(percentile(lats, 50), 1),
        "p95_ms":       round(percentile(lats, 95), 1),
        "p99_ms":       round(percentile(lats, 99), 1),
        "by_difficulty": diff_summary,
        "case_count":   len(per_case),
    }


# ── Console table ──────────────────────────────────────────────────────────────

def print_table(per_case: List[Dict], agg: Dict, label: str) -> None:
    print()
    print("=" * 96)
    print(f"  Run label: {label}")
    print("=" * 96)
    header = f"{'id':<22} {'diff':<7} {'r@5':>6} {'r@10':>6} {'mrr':>6} {'srcCov':>7} {'lat(ms)':>9}"
    print(header)
    print("-" * len(header))
    for c in per_case:
        def _fmt(v):
            return "—" if v is None else f"{v:.2f}"
        print(
            f"{(c['id'] or '')[:22]:<22} "
            f"{(c['difficulty'] or '')[:7]:<7} "
            f"{_fmt(c['recall@5']):>6} "
            f"{_fmt(c['recall@10']):>6} "
            f"{_fmt(c['mrr']):>6} "
            f"{_fmt(c['src_coverage']):>7} "
            f"{c['latency_ms']:>9.1f}"
        )
    print("-" * len(header))

    def _fmt_or_dash(v):
        return f"{v:.2f}" if v is not None else "—"

    print(
        f"{'AGGREGATE':<22} {'':<7} "
        f"{_fmt_or_dash(agg['recall@5']):>6} "
        f"{_fmt_or_dash(agg['recall@10']):>6} "
        f"{_fmt_or_dash(agg['mrr']):>6} "
        f"{_fmt_or_dash(agg['src_coverage']):>7} "
        f"{agg['p50_ms']:>9.1f}"
    )
    print(f"  p50={agg['p50_ms']}ms  p95={agg['p95_ms']}ms  p99={agg['p99_ms']}ms  cases={agg['case_count']}")
    print()


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--label", required=True, help="Run label, used in output filename")
    p.add_argument("--provider", choices=["hf", "minilm"], default="hf")
    p.add_argument("--reranker", action="store_true", default=True)
    p.add_argument("--no-reranker", dest="reranker", action="store_false")
    p.add_argument("--cases", default=None,
                   help="Path to eval JSONL (default: backend/eval/retrieval_eval_set.jsonl)")
    p.add_argument("--top-n", type=int, default=int(os.getenv("RERANKING_TOP_N", "30")))
    p.add_argument("--top-k", type=int, default=int(os.getenv("RERANKING_TOP_K", "10")))
    p.add_argument("--out-dir", default=None)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    cases_path = Path(args.cases) if args.cases else (
        Path(__file__).resolve().parent / "retrieval_eval_set.jsonl"
    )
    if not cases_path.exists():
        logger.error("Eval cases file not found: %s", cases_path)
        return 2
    cases = load_cases(cases_path)
    logger.info("Loaded %d eval cases from %s", len(cases), cases_path)

    # ── Build provider + bind to a VectorStore ──────────────────────────────
    provider = build_provider(args.provider)
    logger.info("Provider: %s  dim=%d  device=%s",
                provider.name, provider.dimension, provider.device)

    store = VectorStore()
    store.bind_provider(provider)

    if store.count() == 0:
        logger.error(
            "Active collection (%s) is empty. "
            "Run the reindex script first if you switched providers.",
            store.collection_name,
        )
        return 2

    reranker = build_reranker(args.reranker)
    if reranker:
        logger.info("Reranker: %s  device=%s", reranker.name, reranker.device)

    # ── Run ─────────────────────────────────────────────────────────────────
    per_case = [
        evaluate_case(case, store, reranker, args.top_n, args.top_k)
        for case in cases
    ]
    agg = aggregate(per_case)

    print_table(per_case, agg, args.label)

    # ── Persist ─────────────────────────────────────────────────────────────
    out_dir = Path(args.out_dir) if args.out_dir else (
        Path(__file__).resolve().parent / "results"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"results_{args.label}_{stamp}.json"
    out_path.write_text(json.dumps({
        "label":      args.label,
        "ts":         stamp,
        "config": {
            "provider":  provider.name,
            "dimension": provider.dimension,
            "device":    provider.device,
            "reranker":  getattr(reranker, "name", None),
            "top_n":     args.top_n,
            "top_k":     args.top_k,
        },
        "aggregate": agg,
        "per_case":  per_case,
    }, indent=2))
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
