# ARUP AI Test Advisor — HF Stack Deployment Guide

**Version:** 1.3.0 (Hugging Face enhancement)
**Target:** MacBook Pro M1, 16 GB RAM, 1 TB SSD
**Estimated wall-clock:** 2-3 days, with verification gates between phases

This guide is the operational playbook for adding BGE embeddings, the
`bge-reranker-v2-m3` cross-encoder, and biomedical NER enrichment to the
ARUP AI Test Advisor. Each phase ends in a **go/no-go gate** — pass the
gate before moving on, or roll back without affecting later work.

---

## 0. Pre-flight (~30 minutes)

Run these checks BEFORE touching any code.

```bash
# 0.1 — Python version
python3 --version
# expected: 3.11.x  (3.10 also works, 3.12 is less battle-tested)

# 0.2 — MPS available
python3 -c "import torch; print('mps:', torch.backends.mps.is_available())"
# expected: mps: True   (or you'll need to install torch first)

# 0.3 — Disk space (you need ~3 GB free for models + venv)
df -h ~

# 0.4 — Snapshot the current ChromaDB (rollback artifact)
cp -R data/chroma "data/chroma.bak.$(date +%Y%m%d)"

# 0.5 — Snapshot current code
git status
git stash push -u -m "pre-hf-baseline" || true
git tag pre-hf-v1.2.0
```

**Pre-flight pass condition:** Python 3.11, MPS=True, ≥3 GB disk free,
ChromaDB snapshotted, code tagged.

---

## 1. Day 1 morning — Phase 1: Embedding cutover (3-4 hours)

### 1.1 Install new dependencies

```bash
cd backend
source .venv/bin/activate
pip install -r requirements.txt
```

This pulls in `torch==2.3.1`, `transformers==4.44.2`,
`sentence-transformers==3.0.1`, plus `accelerate`, `safetensors`,
`huggingface-hub`, and pinned `tokenizers`.

Expected: ~600 MB of new packages, no compile failures. If you see a
flash-attn build failure, you're not on this guide — we explicitly
avoid FlagEmbedding to prevent exactly that.

### 1.2 Apply code patches

```bash
# Copy in the new files
cp -R backend/embeddings        ../live/backend/
cp -R backend/reranking         ../live/backend/
cp -R backend/clinical_ner      ../live/backend/
cp -R backend/scripts           ../live/backend/
cp -R backend/eval              ../live/backend/
cp    backend/hf_components.py  ../live/backend/
cp    backend/agents/confidence_agent.py  ../live/backend/agents/
cp    backend/knowledge/store.py          ../live/backend/knowledge/
cp    backend/requirements.txt            ../live/backend/
cp    backend/.env.example                ../live/backend/

# Apply main.py patch (manually, per docs/main_py_patch.md)
# Apply start.sh patch  (manually, per docs/start_sh_patch.md)
```

> **Verification-first reminder:** Before each `cp` over the live file,
> run `diff backend/<file> ../live/backend/<file>` to see EXACTLY what's
> changing. Sam's principle #1: Live codebase is source of truth.

### 1.3 Pre-warm models (optional)

```bash
python -c "from sentence_transformers import SentenceTransformer; \
           SentenceTransformer('BAAI/bge-base-en-v1.5')"
python -c "from sentence_transformers import CrossEncoder; \
           CrossEncoder('BAAI/bge-reranker-v2-m3')"
```

Expect ~1.5 GB of downloads on first run.

### 1.4 Run the reindex

```bash
cd backend
source .venv/bin/activate

# Dry run first — confirm legacy collection has chunks to migrate
python -m scripts.reindex_knowledge --dry-run
# expect: "Source-type breakdown (legacy): Algorithm N, Fact Sheet N, ..."

# Real run — drops the BGE collection if exists, repopulates from legacy
python -m scripts.reindex_knowledge --force
# expect: ~1-3 minutes wall-clock, manifest written
```

### 1.5 Restart and verify

```bash
./start.sh --api-only
# in a second terminal:
curl -s localhost:8010/health | jq .huggingface
```

### 🚦 GATE #1 — Embedding cutover

Pass conditions (all must be true):

- [ ] `/health.huggingface.embedding.ready` is `true`
- [ ] `/health.huggingface.embedding.dimension` is `768`
- [ ] `/health.huggingface.embedding.name` is `"BAAI/bge-base-en-v1.5"`
- [ ] `vector_store.count()` matches the legacy chunk count (visible in `documents_indexed`)
- [ ] Three smoke `/chat` queries return citations and don't 500:
      *celiac disease workup*, *thyroid nodule FNA*, *hep B serology interpretation*
- [ ] `/documents/algorithms` shows `graph_available=True` for at least one algo

**If gate fails:** `EMBEDDING_PROVIDER=minilm` in `.env` and restart. The
legacy collection is untouched and the system reverts to v1.2.0.

---

## 2. Day 1 afternoon — Phase 2: Reranker (2-3 hours)

The reranker is already loaded — we set `RERANKING_ENABLED=true` as the
default in `.env.example`. So at this point reranking is ALREADY in your
production path. This phase is about **verifying** it.

### 2.1 Smoke comparison

Run the same three smoke queries as Gate #1 and visually inspect:

- The first ranked recommendation usually changes between runs with and
  without the reranker. To compare, temporarily set `RERANKING_ENABLED=false`
  and rerun — you should see different (sometimes better, sometimes worse)
  top-1 picks.
- Latency p95 from a 5-query batch should be ≤ baseline + 1.5 s.

```bash
# Latency test — run 5 chat queries, measure timing
for i in 1 2 3 4 5; do
  time curl -s -X POST localhost:8010/chat \
    -H 'Content-Type: application/json' \
    -d '{"query":"celiac disease workup adult","history":[]}' > /dev/null
done
```

### 2.2 Force-disable test (proves clean fail-open)

```bash
# In .env temporarily:  RERANKING_ENABLED=false
./start.sh --api-only
# Confirm /chat still works, /health shows reranking.ready=false
```

### 🚦 GATE #2 — Reranker

- [ ] `/health.huggingface.reranking.ready` is `true` with default config
- [ ] Latency p95 increase ≤ 1500 ms over Gate-1 baseline
- [ ] With `RERANKING_ENABLED=false`, system runs with no errors
- [ ] No 5xx responses on 5+ smoke queries

**If gate fails:** `RERANKING_ENABLED=false` in `.env` and continue.
Phase 4 (confidence signals) will degrade gracefully — both new signals
return `None` when there are no rerank scores.

---

## 3. Day 2 morning — Phase 3: Clinical NER (1-2 hours)

NER is **disabled by default** (`CLINICAL_NER_ENABLED=false`). This phase
is about flipping it on, verifying it works, and confirming nothing breaks
when it doesn't.

### 3.1 Verify default-OFF behavior

With `CLINICAL_NER_ENABLED=false` (the default):

```bash
curl -s localhost:8010/health | jq .huggingface.clinical_ner
# expect: enabled=false, loaded=false, load_failed=false
```

A `/chat` request should produce identical results to end-of-Phase-2.

### 3.2 Enable NER

```bash
# In .env:
CLINICAL_NER_ENABLED=true
# Then restart
./start.sh --api-only
```

First `/chat` request after enable triggers lazy model load (1-2 s extra).

### 3.3 Test enrichment

```bash
curl -X POST localhost:8010/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"65-year-old with iron deficiency anemia and low B12, suspect celiac disease","history":[]}'
```

In the backend log you should see:
```
HF: enrichment query = "celiac disease iron deficiency B12"
```

The response schema is unchanged — citations, recommendations, etc. all
present. NER's job was to ADD secondary retrieval, not to surface
entities to the user.

### 🚦 GATE #3 — Clinical NER

- [ ] With `CLINICAL_NER_ENABLED=false`: response schema byte-identical to Phase 2
- [ ] With `CLINICAL_NER_ENABLED=true`: backend log shows entity extraction
- [ ] Response schema STILL byte-identical (NER never touches the response)
- [ ] No new fields appear in /chat output that the frontend doesn't expect

**If gate fails:** `CLINICAL_NER_ENABLED=false` in `.env`. The model never
loads, no functionality is lost, and Phase 4 still works.

---

## 4. Day 2 afternoon — Phase 4: Confidence signals (1 hour)

This phase doesn't load any new models — it just feeds the reranker output
into ConfidenceAgent. The new agent is already installed (Phase 1 step 1.2).

### 4.1 Determinism check

```bash
# Run the same query twice
curl -s -X POST localhost:8010/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"thyroid nodule FNA when","history":[]}' > /tmp/r1.json

curl -s -X POST localhost:8010/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"thyroid nodule FNA when","history":[]}' > /tmp/r2.json

# Confidence values must be byte-identical
jq .confidence /tmp/r1.json
jq .confidence /tmp/r2.json
diff <(jq .confidence /tmp/r1.json) <(jq .confidence /tmp/r2.json)
# expect: no diff output (signals are pure math)
```

### 4.2 Edge case — out-of-scope query

```bash
curl -s -X POST localhost:8010/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"weather forecast tokyo","history":[]}' | jq .confidence
```

Expected: low score, `semantic_alignment` near 0.3-0.5,
`reranker_agreement` near 0.5 (no clear signal).

### 🚦 GATE #4 — Confidence signals

- [ ] Same query twice → identical confidence object (deterministic)
- [ ] `confidence.signals.reranker_agreement` is a number in [0,1] when
      reranker enabled, `null` when disabled
- [ ] `confidence.signals.semantic_alignment` populated when citations
      exist, `null` otherwise
- [ ] Frontend `MessageBubble.jsx` renders without JS errors (no schema
      shock from new fields — they're additive)

**If gate fails:** `CONFIDENCE_INCLUDE_HF_SIGNALS=false` in `.env`. The
new signals stop affecting the score but remain visible in the response
debug block for inspection.

---

## 5. Day 3 morning — Phase 5: Eval harness (2-3 hours)

This phase is **purely additive** — running it doesn't modify any
production behavior. We use it to quantify the improvement.

### 5.1 Baseline run (MiniLM, no rerank)

If you haven't dropped the legacy collection (`--drop-legacy=false` is
default), run:

```bash
cd backend
source .venv/bin/activate
python -m eval.run_retrieval_eval \
    --label baseline_minilm --provider minilm --no-reranker
```

If the legacy collection is gone, skip this step — you'll compare BGE
vs BGE+rerank instead.

### 5.2 BGE-only

```bash
python -m eval.run_retrieval_eval \
    --label bge_only --provider hf --no-reranker
```

### 5.3 BGE + rerank (production stack)

```bash
python -m eval.run_retrieval_eval \
    --label bge_plus_rerank --provider hf --reranker
```

### 5.4 Compare

```bash
python -m eval.compare_runs baseline_minilm bge_plus_rerank
# (or bge_only -> bge_plus_rerank if no legacy)
```

### 🚦 GATE #5 — Eval

- [ ] `bge_plus_rerank` Recall@5 ≥ baseline + 0.05 (5 absolute points)
- [ ] `bge_plus_rerank` MRR ≥ baseline
- [ ] p95 latency (eval, not chat) is reasonable (< 2 s for top-30 retrieval + rerank)

**If gate fails:** That's actually fine — Phases 1-4 still ship. Tune
`RERANKING_TOP_N` (try 50) and `RERANKING_BLEND_ALPHA` (try 0.5) and rerun
the eval. Add more eval cases (the 17 starters are a floor, not a ceiling).

---

## 6. Day 3 afternoon — Production polish (1-2 hours)

### 6.1 Drop the legacy collection (optional)

Once Gates 1-5 are all green AND you've kept your `data/chroma.bak.*`
snapshot, you can clean up:

```bash
cd backend
python -m scripts.reindex_knowledge --drop-legacy
# (re-runs the migration but adds the legacy delete at the end)
```

### 6.2 Tag the release

```bash
git add -A
git commit -m "feat: Hugging Face stack v1.3.0 (BGE + rerank + NER + eval)"
git tag v1.3.0
```

### 6.3 Archive eval baselines

```bash
mkdir -p docs/eval-baselines
cp backend/eval/results/*.json docs/eval-baselines/
git add docs/eval-baselines && git commit -m "Eval baselines for v1.3.0"
```

---

## Rollback playbook

| Issue | Action | Side effect |
|---|---|---|
| BGE model fails to load | `EMBEDDING_PROVIDER=minilm` | Falls back to legacy MiniLM. Reads `arup_knowledge` legacy collection. |
| Reranker too slow / wrong | `RERANKING_ENABLED=false` | Bi-encoder ranking only. ConfidenceAgent's HF signals become null. |
| NER causing odd retrieval | `CLINICAL_NER_ENABLED=false` | No-op. Default state anyway. |
| Confidence scores look weird | `CONFIDENCE_INCLUDE_HF_SIGNALS=false` | Confidence reverts to v1.0.0 base score. |
| Total disaster | Restore `data/chroma.bak.*`, `git checkout pre-hf-v1.2.0`, `pip install -r requirements.txt` (the old one) | Full rollback to pre-HF state. |

---

## Memory / disk budget on M1 16 GB

| Component | Disk | Resident | Comment |
|---|---|---|---|
| BGE base | ~440 MB | ~500 MB | Always loaded |
| BGE reranker (fp16) | ~570 MB | ~700 MB | Loaded when RERANKING_ENABLED=true |
| Biomedical NER | ~266 MB | ~350 MB | Lazy-loaded; only when CLINICAL_NER_ENABLED=true |
| Torch + tokenizers (shared) | ~600 MB | ~300 MB | One-time per process |
| Existing FastAPI/Chroma/Anthropic | — | ~450 MB | Pre-existing baseline |
| **Total backend process** | **~1.9 GB** | **~2.3 GB** | All three loaded |

With Chrome + Vite + macOS overhead (~5 GB), total memory pressure on a
16 GB MacBook is around 8 GB — leaving 6+ GB headroom.

---

## What changed vs v1.2.0

**New files (15):**
```
backend/embeddings/__init__.py
backend/embeddings/embedding_provider.py
backend/embeddings/hf_embedding_provider.py
backend/embeddings/minilm_provider.py
backend/reranking/__init__.py
backend/reranking/reranking_agent.py
backend/clinical_ner/__init__.py
backend/clinical_ner/clinical_entity_agent.py
backend/scripts/__init__.py
backend/scripts/reindex_knowledge.py
backend/eval/__init__.py
backend/eval/retrieval_eval_set.jsonl
backend/eval/run_retrieval_eval.py
backend/eval/compare_runs.py
backend/eval/README.md
backend/hf_components.py
```

**Modified files (4):**
```
backend/main.py                — ~14 lines added (per docs/main_py_patch.md)
backend/start.sh               — ~6 lines added (per docs/start_sh_patch.md)
backend/knowledge/store.py     — provider-aware embeddings, dim guard
backend/agents/confidence_agent.py — new HF signals (additive)
backend/requirements.txt       — torch, transformers, sentence-transformers
backend/.env.example           — full HF env-var template
```

**Untouched files:**
```
backend/main.py — except the small additive patch
backend/agents/prompt_agent.py
backend/agents/retrieval_agent.py
backend/agents/evidence_packager.py
backend/agents/response_agent.py
backend/agents/critic_agent.py
backend/agents/acceptance_checker.py
backend/agents/algorithm_renderer.py
backend/agents/algorithm_fidelity_critic.py
backend/agents/formatting_agent.py
backend/knowledge/processor.py
frontend/* — no changes anywhere
```
