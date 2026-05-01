# Patch: `start.sh` — Hugging Face environment exports

**Goal:** Add the env-var exports needed for stable BGE/reranker/NER
operation on macOS arm64. None of these change existing behaviour; they
just suppress noisy warnings and route MPS fallbacks correctly.

---

## Where to add

Find the section near the top of `start.sh` where existing environment
configuration happens (after `step "Python environment"` and before the
backend launch). **Add this block:**

```bash
# ── Hugging Face stack — env exports (v1.3.0) ─────────────────────────────
# Required for stable MPS operation on Apple Silicon. Without
# PYTORCH_ENABLE_MPS_FALLBACK=1, certain ops (cumsum, scatter_reduce,
# etc.) will raise NotImplementedError on first inference.
export PYTORCH_ENABLE_MPS_FALLBACK=1

# Suppresses the noisy "TOKENIZERS_PARALLELISM" fork warning that
# appears every time uvicorn spawns a worker.
export TOKENIZERS_PARALLELISM=false

# Centralise the HF model cache inside the backend dir so it's easy to
# reset / inspect / version-control. Falls back to ~/.cache/huggingface
# if not set, which is also fine.
export HF_HOME="${HF_HOME:-$BACKEND/.hf_cache}"

# Disable HF Hub telemetry and progress bars in production logs.
export HF_HUB_DISABLE_TELEMETRY=1
export HF_HUB_DISABLE_PROGRESS_BARS=1
```

That's the entire change.

---

## Optional: pre-warm HF model downloads on first run

If you want the first launch after `pip install` to download all three
models BEFORE uvicorn starts (so the first `/chat` request is instant),
add this block AFTER the dependency-install step and BEFORE the uvicorn
launch:

```bash
# ── Optional: pre-warm HF model cache (one-time download, ~1.8 GB) ───────
PREWARM_FLAG="$BACKEND/.hf_cache/.warmed"
if [ ! -f "$PREWARM_FLAG" ]; then
  step "Pre-warming Hugging Face model cache"
  info "First-time download — this takes 2-5 minutes on a fresh laptop"
  mkdir -p "$BACKEND/.hf_cache"
  "$VENV/bin/python" - <<'PYEOF' || warn "HF prewarm failed — models will download on first use"
import os
os.environ.setdefault("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
from sentence_transformers import SentenceTransformer, CrossEncoder
print("  -> BGE base embedder...")
SentenceTransformer("BAAI/bge-base-en-v1.5")
print("  -> BGE reranker...")
CrossEncoder("BAAI/bge-reranker-v2-m3")
print("  -> biomedical NER...")
from transformers import AutoTokenizer, AutoModelForTokenClassification
AutoTokenizer.from_pretrained("d4data/biomedical-ner-all")
AutoModelForTokenClassification.from_pretrained("d4data/biomedical-ner-all")
print("done")
PYEOF
  touch "$PREWARM_FLAG"
fi
```

This step is fully optional — without it, models download lazily on first
use, which adds 1-3 s to the very first `/chat` call.

---

## Verification

```bash
./start.sh --api-only
# wait for "Backend ready" message
curl -s localhost:8010/health | jq .huggingface.embedding.ready
# expected output:  true
```

If pre-warm was enabled:

```bash
ls -la backend/.hf_cache/
# should show populated subdirs for hub/, modules/, etc.
du -sh backend/.hf_cache/
# expected: ~1.8 GB total
```
