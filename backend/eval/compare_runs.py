"""
compare_runs.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
Diff two eval result JSON files and print deltas.

Usage
─────
    python -m eval.compare_runs baseline_minilm bge_plus_rerank
    # -> Loads the most recent results_baseline_minilm_*.json and
    #    results_bge_plus_rerank_*.json from backend/eval/results/

    python -m eval.compare_runs --file-a path/a.json --file-b path/b.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


RESULTS_DIR = Path(__file__).resolve().parent / "results"


def latest_for_label(label: str) -> Optional[Path]:
    matches = sorted(RESULTS_DIR.glob(f"results_{label}_*.json"))
    return matches[-1] if matches else None


def fmt(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def delta(a, b):
    if a is None or b is None:
        return "—"
    d = b - a
    sign = "+" if d > 0 else ""
    return f"{sign}{d:.4f}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("labels", nargs="*", help="Two run labels (looks up latest results)")
    p.add_argument("--file-a", default=None)
    p.add_argument("--file-b", default=None)
    args = p.parse_args()

    if args.file_a and args.file_b:
        a_path = Path(args.file_a)
        b_path = Path(args.file_b)
        a_label = a_path.stem
        b_label = b_path.stem
    elif len(args.labels) == 2:
        a_label, b_label = args.labels
        a_path = latest_for_label(a_label)
        b_path = latest_for_label(b_label)
        if not (a_path and b_path):
            print(f"Could not locate results for one or both labels in {RESULTS_DIR}",
                  file=sys.stderr)
            return 2
    else:
        print("Provide either two labels or --file-a + --file-b", file=sys.stderr)
        return 2

    a = json.loads(a_path.read_text())
    b = json.loads(b_path.read_text())

    print()
    print("=" * 90)
    print(f"  A: {a_label}  ({a_path.name})")
    print(f"  B: {b_label}  ({b_path.name})")
    print("=" * 90)
    print(f"{'metric':<22} {'A':>14} {'B':>14} {'Delta (B-A)':>14}")
    print("-" * 90)
    metrics = ["recall@5", "recall@10", "mrr", "src_coverage",
               "p50_ms", "p95_ms", "p99_ms"]
    for m in metrics:
        av = a["aggregate"].get(m)
        bv = b["aggregate"].get(m)
        print(f"{m:<22} {fmt(av):>14} {fmt(bv):>14} {delta(av, bv):>14}")
    print()

    # ── Per-difficulty ──────────────────────────────────────────────────────
    print("Per-difficulty Recall@5:")
    a_bd = a["aggregate"].get("by_difficulty", {})
    b_bd = b["aggregate"].get("by_difficulty", {})
    diffs = sorted(set(list(a_bd) + list(b_bd)))
    print(f"  {'difficulty':<12} {'A':>10} {'B':>10} {'Delta':>10}")
    for d in diffs:
        av = a_bd.get(d, {}).get("recall@5")
        bv = b_bd.get(d, {}).get("recall@5")
        print(f"  {d:<12} {fmt(av):>10} {fmt(bv):>10} {delta(av, bv):>10}")
    print()

    # ── Per-case Δ recall@5 ─────────────────────────────────────────────────
    print("Per-case Recall@5 changes:")
    a_by_id = {c["id"]: c for c in a.get("per_case", [])}
    b_by_id = {c["id"]: c for c in b.get("per_case", [])}
    ids = sorted(set(list(a_by_id) + list(b_by_id)))
    print(f"  {'case_id':<24} {'A':>6} {'B':>6} {'Delta':>7}")
    for cid in ids:
        ar = a_by_id.get(cid, {}).get("recall@5")
        br = b_by_id.get(cid, {}).get("recall@5")
        print(f"  {cid:<24} {fmt(ar):>6} {fmt(br):>6} {delta(ar, br):>7}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
