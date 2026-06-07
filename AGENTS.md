# ARUP AI Test Advisor — Agent Notes

## Cursor Cloud specific instructions

### Services and ports

| Service | Port | Start command |
|---------|------|---------------|
| FastAPI backend | `8010` | See manual start below |
| Vite frontend (dev) | `5174` | `cd frontend && npm run dev -- --port 5174 --host 0.0.0.0` |

`./start.sh` is interactive (API key prompt, port-kill confirmation). In non-interactive Cloud VMs, start backend and frontend manually in separate tmux sessions instead.

### Manual start (non-interactive)

```bash
# Backend
cd backend
export PYTORCH_ENABLE_MPS_FALLBACK=1 TOKENIZERS_PARALLELISM=false
export HF_HOME="$PWD/.hf_cache" HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_DISABLE_PROGRESS_BARS=1
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8010 --reload

# Frontend (separate terminal)
printf "VITE_BACKEND_PORT=8010\n" > frontend/.env.local
cd frontend && npm run dev -- --port 5174 --host 0.0.0.0
```

### First-time system dependency (Ubuntu)

If `python3 -m venv` fails with `ensurepip is not available`, install once:

```bash
sudo apt-get install -y python3.12-venv
```

### Environment / secrets

- Copy `backend/.env.example` → `backend/.env` and set a valid `ANTHROPIC_API_KEY`.
- `/chat` and the full agent pipeline require a working Anthropic key; `/health`, `/upload`, `/documents`, and deterministic formatters work without it.
- First backend start may download Hugging Face embedding/reranker models (~1–2 GB) into `backend/.hf_cache`.

### Lint / test / build

There is no ESLint or pytest suite in-repo. Use these checks:

| Check | Command |
|-------|---------|
| Backend smoke tests | `cd backend && source .venv/bin/activate && python test_plain_text_formatter.py && python test_export_endpoint.py` |
| Frontend production build | `cd frontend && npm run build` |
| Backend health | `curl http://localhost:8010/health` |
| API proxy (via Vite) | `curl http://localhost:5174/api/health` |

### Gotchas

- README still lists ports `8000`/`5173`; the live launcher and Vite config use **`8010`** and **`5174`**.
- `start.sh` only runs `npm install` when `frontend/node_modules` is missing; after dependency changes, run `npm install --prefix frontend` manually.
- ChromaDB data persists under `data/chroma/`; document count survives restarts.
