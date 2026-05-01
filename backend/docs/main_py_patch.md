# Patch: `backend/main.py` — Hugging Face wiring

**Goal:** Enable the HF stack with a ~14-line additive patch. No existing
behaviour changes; everything is gated by `_hf_available` so a missing
`hf_components.py` or import error degrades to v1.2.0 behaviour.

This patch was deliberately split across **four small insertion points**
so a side-by-side diff against your live `main.py` is short and obvious.

---

## Insertion #1 — top of file, alongside other optional imports

Find the existing block of try/except optional imports (the one with
`SingleCritic`, `AcceptanceChecker`, `AlgorithmFidelityCritic`, etc.).
**Add right below it:**

```python
# ── v1.3.0 — Hugging Face stack (Phases 1–4) ───────────────────────────────
# Optional. Failure to import is non-fatal; behaviour falls back to v1.2.0.
try:
    from hf_components import HfComponents
    _hf_available = True
except ImportError:
    _hf_available = False
    HfComponents = None
```

---

## Insertion #2 — `@app.on_event("startup")`

Replace the existing one-line startup hook:

```python
@app.on_event("startup")
async def startup():
    init_db()
```

with this version:

```python
@app.on_event("startup")
async def startup():
    init_db()

    # ── HF subsystems (additive, fail-open) ────────────────────────────
    app.state.hf = None
    if _hf_available:
        try:
            app.state.hf = HfComponents.init(vector_store, retrieval_agent)
            logger.info(
                "HF stack ready — embedding=%s reranker=%s",
                getattr(app.state.hf.embedding_provider, "name", "?"),
                getattr(app.state.hf.reranker, "name", None) or "disabled",
            )
        except Exception:
            logger.exception("HF init failed at startup; continuing without HF stack")
            app.state.hf = None
```

---

## Insertion #3 — inside `/chat` handler

Find the existing pipeline section that looks roughly like this:

```python
# ── 1. Prompt Agent — intent extraction ────────────────────────────────
intent = await prompt_agent.analyze(
    query, request.history,
    clinical_context=clinical_context,
    prior_sessions=prior_sessions,
    clarification_state=clar_state,
)

# ... (clarification gate logic unchanged) ...

# ── 3. Retrieval Agent — planner-driven vector search ──────────────────
raw_chunks = await retrieval_agent.retrieve(intent)
```

**Add ONE line** immediately after `prompt_agent.analyze(...)` returns
(before the clarification gate):

```python
intent = await prompt_agent.analyze(...)

# v1.3.0 — additive NER enrichment (no-op when CLINICAL_NER_ENABLED=false)
if app.state.hf:
    intent = app.state.hf.enrich_intent(intent, query)
```

**Add ONE line** immediately after `retrieval_agent.retrieve(intent)`:

```python
raw_chunks = await retrieval_agent.retrieve(intent)

# v1.3.0 — NER-driven secondary retrieval + cross-encoder rerank
if app.state.hf:
    raw_chunks = app.state.hf.enrich_and_rerank(query, intent, raw_chunks, vector_store)
```

That's the entire chat-pipeline change. EvidencePackager, ResponseAgent,
CriticAgent, AcceptanceChecker, AlgorithmRenderer, AlgorithmFidelityCritic,
and FormattingAgent see exactly the same chunk shape they saw before — just
with new metadata fields (`rerank_score`, `combined_score`, `retrieval_rank`,
`rerank_rank`) tacked on. ConfidenceAgent reads those new fields for its
reranker_agreement / semantic_alignment signals.

---

## Insertion #4 — `/health` endpoint

Find the existing `@app.get("/health")` handler. The current return dict
already has nested objects for `critic`, `acceptance_checker`,
`algorithm_fidelity_critic`. **Add one more entry**, anywhere in the dict:

```python
"huggingface": (
    app.state.hf.health()
    if getattr(app.state, "hf", None)
    else {"enabled": False, "reason": "module not available or init failed"}
),
```

Now `curl localhost:8010/health | jq .huggingface` shows the embedding
model, reranker model, NER status, and per-component readiness.

---

## Verification gate

After applying all four insertions, restart the backend and check:

```bash
curl -s localhost:8010/health | jq .huggingface
```

Expected (with default env):

```json
{
  "enabled": true,
  "embedding": {
    "ready": true,
    "name": "BAAI/bge-base-en-v1.5",
    "dimension": 768,
    "device": "mps"
  },
  "reranking": {
    "enabled": true,
    "ready": true,
    "model": "BAAI/bge-reranker-v2-m3",
    "device": "mps",
    "fp16": true
  },
  "clinical_ner": {
    "enabled": false,
    "loaded": false,
    "load_failed": false
  },
  "stats": { "rerank_calls": 0 }
}
```

If `embedding.ready=false`, check the backend log for the failure mode —
likely the model download failed or `EMBEDDING_PROVIDER` is mistyped.

---

## Total `main.py` diff size

- **Lines added: ~14**
- **Lines removed: 0**
- **Lines modified: 0** (the startup hook gets new lines added inside; existing line stays)

This is the smallest possible footprint that wires up four phases of HF
enhancement into the existing pipeline.
