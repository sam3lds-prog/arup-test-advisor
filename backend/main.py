"""
ARUP AI Test Advisor — FastAPI backend  v1.0.0

What's new in v1.0.0 — Algorithm Renderer Robustness + Debug:
  • AlgorithmRenderer.render_for_bundle() called with debug=True — captures
    per-stage diagnostic info (filenames tried, graph found, node count)
  • FormattingAgent.format() receives evidence_context — enables the diagnostic
    info_block when algorithm chunks exist but viz failed, and exposes
    has_algorithm_viz / algorithm_retrieved in render_hints
  • algorithm_debug block added to /chat response (non-breaking; ignored by frontend)
  • Lightweight logger.info() around Step 7 so logs show exactly where it fails
  • GET /documents/algorithms — admin debug route: lists indexed algorithm files
    and whether each has a stored algorithm_graph payload

Retained from v0.9.0 (fully backward-compatible):
  • FormattingAgent + designer API (/designer/*)
  • ui_schema in all /chat response paths
  • Schema snapshots per session for Designer Panel

Retained from v0.8.0:
  • Clarification state transitions (pending_question / resolve_pending)
  • One question per turn, max 3 per session

Retained from v0.7.0:
  • Planner → retrieval → evidence_bundle → response → confidence pipeline
  • EvidencePackager, RetrievalPlanner, intent-aware authority weights

Retained from v0.5.1:
  • Duplicate-file detection on /upload, /upload/batch, /ingest/folder
  • Session management, clinical context persistence, prior-session memory
"""

import os
import json
import copy
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from agents.prompt_agent import PromptAgent
from agents.retrieval_agent import RetrievalAgent
from agents.response_agent import ResponseAgent
from agents.confidence_agent import ConfidenceAgent
from agents.evidence_packager import EvidencePackager
from knowledge.processor import DocumentProcessor, ALLOWED_EXTENSIONS, classify_document
from knowledge.store import VectorStore
from agents.session_store import (
    init_db, create_session, get_session,
    increment_turn, save_session_summary,
    get_recent_sessions, get_clinical_context, save_clinical_context,
    get_clarification_state, record_clarification_question,
    suppress_further_clarification, resolve_pending_clarification,
    save_feedback,
    MAX_CLARIFICATION_QUESTIONS, EMPTY_CONTEXT,
)
from agents.context_extractor import extract_context, summarise_session
from agents.algorithm_renderer import AlgorithmRenderer
from agents.formatting_agent import FormattingAgent                  # v0.9.0
from agents.designer_routes import build_router as build_designer_router  # v0.9.0

# Fidelity-first renderer and chartflow rule store — optional
try:
    from agents.arup_algorithm_template_renderer import ArupAlgorithmTemplateRenderer
    from agents.chartflow_rule_store import ChartflowRuleStore
    from agents.chartflow_normalizer import ChartflowNormalizer
    from agents.design_library_store import DesignLibraryStore
    _fidelity_renderer_available = True
except ImportError:
    _fidelity_renderer_available = False
    ArupAlgorithmTemplateRenderer = None
    ChartflowRuleStore = None
    ChartflowNormalizer = None

# Asset store — optional; app works fully without it, PDF assets just won't be available
try:
    from agents.asset_store import get_asset_store as _get_asset_store
    _asset_store_available = True
except ImportError:
    _asset_store_available = False
    _get_asset_store = None

logger = logging.getLogger(__name__)

app = FastAPI(title="ARUP AI Test Advisor API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://localhost:8010", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global exception handler — always returns JSON, never HTML ────────────────
# Without this, unhandled 500s return an HTML page that the frontend can't parse,
# causing it to show "Could not reach the backend" even though the server is up.
from fastapi import Request
from fastapi.responses import JSONResponse

@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail":     "Internal server error",
            "error_type": exc.__class__.__name__,
            # Include the error string in dev — harmless for a local MacBook deployment
            "error":      str(exc),
        },
    )

# ── Startup ────────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    init_db()

# ── Components ─────────────────────────────────────────────────────────────────
vector_store      = VectorStore()
doc_processor     = DocumentProcessor()
prompt_agent      = PromptAgent()
retrieval_agent   = RetrievalAgent(vector_store)
evidence_packager = EvidencePackager()
response_agent    = ResponseAgent()
confidence_agent  = ConfidenceAgent()
algorithm_renderer = AlgorithmRenderer(vector_store)
formatting_agent  = FormattingAgent()                                # v0.9.0
asset_store       = _get_asset_store() if _asset_store_available else None  # v1.0.0 (optional)

# Fidelity-first renderer — optional subsystem
if _fidelity_renderer_available:
    _design_lib_store = DesignLibraryStore()
    _chartflow_rule_store = ChartflowRuleStore(_design_lib_store)
    _chartflow_normalizer = ChartflowNormalizer()
    # Fidelity renderer will be initialized per-request with current approved rules
else:
    _chartflow_rule_store = None
    _chartflow_normalizer = None

# ── Designer router (v0.9.0) ──────────────────────────────────────────────────
# build_designer_router returns a configured APIRouter AND exposes a
# schema_store dict that /chat writes into for live Designer Panel inspection.
designer_router = build_designer_router(formatting_agent)
app.include_router(designer_router)
_designer_schema_store: dict = designer_router.schema_store          # v0.9.0

DOCUMENT_TYPE_HINT_MAP = {
    "Algorithm":      "algorithms",
    "Fact Sheet":     "fact_sheets",
    "Consult Topic":  "consult_topics",
    "Test Directory": "test_directory",
    "Auto-detect":    "",
}


def _resolve_folder_hint(document_type: Optional[str], filename: str) -> str:
    if document_type and document_type in DOCUMENT_TYPE_HINT_MAP:
        hint = DOCUMENT_TYPE_HINT_MAP[document_type]
        if hint:
            return hint
    return str(Path(filename or "").parent)


def _empty_clar_state() -> dict:
    return copy.deepcopy(EMPTY_CONTEXT["clarification_state"])


# ── Request models ─────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    query:        str
    history:      list = []
    session_id:   Optional[str] = None
    clinician_id: Optional[str] = None


class SessionEndRequest(BaseModel):
    session_id:   str
    clinician_id: str
    history:      list = []


class FolderIngestRequest(BaseModel):
    folder_path: str
    recursive:   bool = True
    replace:     bool = False


# ── Background task helpers ────────────────────────────────────────────────────

def _bg_update_context(
    session_id: str,
    clinician_id: str,
    query: str,
    answer: str,
    recommendations: list,
) -> None:
    """
    Fire-and-forget: extract updated clinical context and save to DB.
    NOTE: does NOT touch clarification_state — that is managed synchronously.
    """
    try:
        current = get_clinical_context(session_id)
        updated = extract_context(current, query, answer, recommendations)
        # Preserve the clarification_state that was already written synchronously
        existing_clar_state = current.get("clarification_state", _empty_clar_state())
        updated["clarification_state"] = existing_clar_state
        save_clinical_context(session_id, clinician_id, updated)
        increment_turn(session_id)
    except Exception:
        pass


def _bg_end_session(session_id: str, history: list) -> None:
    try:
        summary = summarise_session(history)
        if summary:
            save_session_summary(session_id, summary)
    except Exception:
        pass


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status":            "ok",
        "documents_indexed": vector_store.count(),
        "prompt_model":      os.environ.get("CLAUDE_PROMPT_MODEL",
                             os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")),
        "response_model":    os.environ.get("CLAUDE_RESPONSE_MODEL",
                             os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")),
        "version":           "1.0.0",
        "document_types":    list(DOCUMENT_TYPE_HINT_MAP.keys()),
        "pipeline": (
            "planner → retrieval → evidence_bundle → response → "
            "confidence → algorithm → formatting"
        ),
        "clarification":     f"max {MAX_CLARIFICATION_QUESTIONS} questions/session, 1 per turn",
        "designer_panel":    "active — /designer/*",
        "preferences_applied": formatting_agent._preferences_applied,
    }


# ── Asset helpers ──────────────────────────────────────────────────────────────

def _is_algorithm_pdf(filename: str, document_type: Optional[str],
                      source_type: Optional[str]) -> bool:
    """Return True when an uploaded PDF should be registered as a local asset."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext != ".pdf":
        return False
    if document_type == "Algorithm":
        return True
    if source_type and source_type.lower() in ("algorithm", "algorithms"):
        return True
    return False


def _register_pdf_asset(content: bytes, filename: str,
                         source_type: Optional[str],
                         chunks: list) -> Optional[dict]:
    """
    Register an uploaded algorithm PDF in the asset store.
    Returns the asset record dict or None on failure (or if asset_store unavailable).
    """
    if not asset_store:
        return None
    # Try to extract title and source_url from indexed chunks
    title      = ""
    source_url = ""
    for chunk in chunks or []:
        if chunk.get("source_url") and not source_url:
            source_url = str(chunk["source_url"])
        if not title and chunk.get("source_type") == "Algorithm":
            title = str(chunk.get("test_name", "") or "")
    # Fallback title from filename
    if not title:
        import re as _re
        title = _re.sub(r"[-_]+", " ", Path(filename).stem).title()
    try:
        record = asset_store.save_asset(
            file_bytes=content,
            original_filename=filename,
            metadata={
                "title":            title,
                "source_url":       source_url,
                "source_page_url":  source_url,
            },
            origin="uploaded",
        )
        return record
    except Exception as exc:
        logger.warning("asset_store: failed to register PDF %s: %s", filename, exc)
        return None


# ── Asset endpoints ─────────────────────────────────────────────────────────────

@app.get("/assets/pdf/{asset_id}")
async def serve_pdf_asset(asset_id: str):
    """Serve a locally stored PDF asset by its asset_id (same-origin)."""
    if not asset_store:
        raise HTTPException(status_code=503, detail="PDF asset store not available")
    pdf_path = asset_store.get_asset_path(asset_id)
    if not pdf_path:
        raise HTTPException(status_code=404, detail=f"PDF asset not found: {asset_id}")
    return FileResponse(path=str(pdf_path), media_type="application/pdf",
                        filename=asset_id + ".pdf")


@app.get("/assets/list")
async def list_pdf_assets(origin: Optional[str] = None, limit: int = 50):
    """List registered PDF assets (admin/debug)."""
    if not asset_store:
        return {"count": 0, "assets": [], "note": "Asset store not available"}
    assets = asset_store.list_assets(origin=origin, limit=min(limit, 200))
    return {
        "count": len(assets),
        "assets": [
            {"asset_id": a["asset_id"], "original_filename": a["original_filename"],
             "title": a["title"], "title_slug": a["title_slug"],
             "asset_origin": a["asset_origin"], "source_url": a["source_url"],
             "local_url": asset_store.local_url(a["asset_id"]),
             "created_at": a["created_at"]}
            for a in assets
        ],
    }

@app.post("/sessions/start")
async def start_session(clinician_id: str):
    """Create a new session for a clinician. Returns session_id."""
    if not clinician_id.strip():
        raise HTTPException(status_code=400, detail="clinician_id cannot be empty")
    sid = create_session(clinician_id.strip())
    return {"session_id": sid, "clinician_id": clinician_id.strip()}


@app.post("/sessions/end")
async def end_session(request: SessionEndRequest, background_tasks: BackgroundTasks):
    """Summarise and close a session (background — returns immediately)."""
    background_tasks.add_task(_bg_end_session, request.session_id, request.history)
    return {"success": True, "session_id": request.session_id}


@app.get("/sessions/{clinician_id}")
async def get_sessions(clinician_id: str, limit: int = 5):
    """Return recent completed session summaries for a clinician."""
    sessions = get_recent_sessions(clinician_id, limit=min(limit, 10))
    return {"clinician_id": clinician_id, "sessions": sessions}


# ── Upload ─────────────────────────────────────────────────────────────────────

@app.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    document_type: Optional[str] = Form(None),
    replace: bool = Form(False),
):
    """
    Upload a single document.

    Dedup behaviour (v0.5.1):
      - Already indexed + replace=False → HTTP 409 "duplicate"
      - replace=True → delete old chunks, re-index, return replaced_chunks
    """
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400,
            detail=f"Unsupported file type '{ext}'. Accepted: {', '.join(ALLOWED_EXTENSIONS)}")

    content  = await file.read()
    filename = file.filename or "unknown"

    already_exists  = vector_store.filename_exists(filename)
    replaced_chunks = 0

    if already_exists:
        if not replace:
            raise HTTPException(
                status_code=409,
                detail=f"duplicate: '{filename}' is already indexed. "
                       "Upload with replace=true to overwrite.",
            )
        replaced_chunks = vector_store.delete_by_filename(filename)

    folder_hint = _resolve_folder_hint(document_type, filename)
    try:
        chunks = doc_processor.process(content, filename, folder_hint=folder_hint)
        vector_store.add_documents(chunks)

        detected_source = chunks[0]["source_type"] if chunks else "Unknown"

        # ── Register algorithm PDFs as local assets (v1.0.0) ────────────────
        asset_record = None
        if _is_algorithm_pdf(filename, document_type, detected_source):
            asset_record = _register_pdf_asset(content, filename, detected_source, chunks)

        result = {
            "success":            True,
            "filename":           filename,
            "source_type":        detected_source,
            "chunks_indexed":     len(chunks),
            "document_type_used": document_type or "Auto-detect",
            "was_replaced":       already_exists,
            "replaced_chunks":    replaced_chunks,
        }
        if asset_record:
            result["asset_registered"] = True
            result["asset_id"]         = asset_record["asset_id"]
            result["asset_local_url"]  = asset_store.local_url(asset_record["asset_id"])
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/upload/batch")
async def upload_batch(
    files: List[UploadFile] = File(...),
    document_type: Optional[str] = Form(None),
    replace: bool = Form(False),
):
    results: List[dict] = []
    total_chunks = 0

    for file in files:
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            results.append({
                "filename": file.filename, "status": "skipped",
                "detail": f"Unsupported type '{ext}'",
                "source_type": "", "chunks_indexed": 0,
            })
            continue

        content  = await file.read()
        filename = file.filename or "unknown"

        already_exists  = vector_store.filename_exists(filename)
        replaced_chunks = 0

        if already_exists:
            if not replace:
                results.append({
                    "filename":       filename,
                    "status":         "duplicate",
                    "detail":         "Already indexed — skipped (use replace=true to overwrite)",
                    "source_type":    "",
                    "chunks_indexed": 0,
                })
                continue
            replaced_chunks = vector_store.delete_by_filename(filename)

        folder_hint = _resolve_folder_hint(document_type, filename)
        try:
            chunks = doc_processor.process(content, filename, folder_hint=folder_hint)
            vector_store.add_documents(chunks)
            total_chunks += len(chunks)

            detected_source = chunks[0]["source_type"] if chunks else "Unknown"

            # ── Register algorithm PDFs as local assets (v1.0.0) ────────────
            asset_record = None
            if _is_algorithm_pdf(filename, document_type, detected_source):
                asset_record = _register_pdf_asset(content, filename, detected_source, chunks)

            batch_result = {
                "filename":        filename,
                "status":          "replaced" if already_exists else "ok",
                "source_type":     detected_source,
                "chunks_indexed":  len(chunks),
                "replaced_chunks": replaced_chunks,
                "detail":          "",
            }
            if asset_record:
                batch_result["asset_registered"] = True
                batch_result["asset_id"]         = asset_record["asset_id"]
                batch_result["asset_local_url"]  = asset_store.local_url(asset_record["asset_id"])
            results.append(batch_result)
        except Exception as e:
            results.append({
                "filename": filename, "status": "error",
                "detail": str(e), "source_type": "", "chunks_indexed": 0,
            })

    ok_count        = sum(1 for r in results if r["status"] == "ok")
    replaced_count  = sum(1 for r in results if r["status"] == "replaced")
    duplicate_count = sum(1 for r in results if r["status"] == "duplicate")
    skipped_count   = sum(1 for r in results if r["status"] == "skipped")
    error_count     = sum(1 for r in results if r["status"] == "error")

    return {
        "success":              error_count == 0,
        "files_processed":      ok_count + replaced_count,
        "files_replaced":       replaced_count,
        "files_duplicates":     duplicate_count,
        "files_skipped":        skipped_count,
        "files_errored":        error_count,
        "total_chunks_indexed": total_chunks,
        "document_type_used":   document_type or "Auto-detect",
        "results":              results,
    }


# ── Folder preview / ingest ────────────────────────────────────────────────────

@app.post("/ingest/preview")
async def preview_folder(request: FolderIngestRequest):
    folder = Path(request.folder_path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"Folder not found: {folder}")
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {folder}")
    pattern = "**/*" if request.recursive else "*"
    items = []
    for path in sorted(folder.glob(pattern)):
        if not path.is_file(): continue
        ext = path.suffix.lower()
        if ext not in ALLOWED_EXTENSIONS: continue
        hint = str(path.parent.name)
        predicted = "Unknown"
        try:
            if ext == ".json":
                data = json.loads(path.read_bytes().decode("utf-8", errors="replace"))
                preview = " ".join(str(k) for k in data.keys()) if isinstance(data, dict) else ""
                predicted = classify_document(filename=path.name, content_preview=preview,
                    json_data=data if isinstance(data, dict) else None, folder_hint=hint)
            else:
                predicted = classify_document(filename=path.name, folder_hint=hint)
        except Exception:
            predicted = "Unknown"
        items.append({
            "filename":              str(path.relative_to(folder)),
            "full_path":             str(path),
            "extension":             ext,
            "size_kb":               round(path.stat().st_size / 1024, 1),
            "predicted_source_type": predicted,
            "folder_hint":           hint,
            "already_indexed":       vector_store.filename_exists(path.name),
        })
    return {"folder": str(folder), "total_files": len(items), "files": items}


@app.post("/ingest/folder")
async def ingest_folder(request: FolderIngestRequest):
    folder = Path(request.folder_path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"Folder not found: {folder}")
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {folder}")
    pattern = "**/*" if request.recursive else "*"
    results: List[dict] = []
    total_chunks = 0

    for path in sorted(folder.glob(pattern)):
        if not path.is_file(): continue
        ext = path.suffix.lower()
        if ext not in ALLOWED_EXTENSIONS: continue
        hint = str(path.parent.name)

        already_exists  = vector_store.filename_exists(path.name)
        replaced_chunks = 0

        if already_exists:
            if not request.replace:
                results.append({
                    "filename":       str(path.relative_to(folder)),
                    "status":         "duplicate",
                    "source_type":    "",
                    "chunks_indexed": 0,
                    "detail":         "Already indexed — skipped",
                })
                continue
            replaced_chunks = vector_store.delete_by_filename(path.name)

        try:
            chunks = doc_processor.process(path.read_bytes(), path.name, folder_hint=hint)
            vector_store.add_documents(chunks)
            total_chunks += len(chunks)

            detected_source = chunks[0]["source_type"] if chunks else "Unknown"

            # ── Register algorithm PDFs as local assets (v1.0.0) ────────────
            folder_result: dict = {
                "filename":        str(path.relative_to(folder)),
                "status":          "replaced" if already_exists else "ok",
                "source_type":     detected_source,
                "chunks_indexed":  len(chunks),
                "replaced_chunks": replaced_chunks,
                "detail":          "",
            }
            if path.suffix.lower() == ".pdf" and detected_source in ("Algorithm", "algorithms"):
                asset_record = _register_pdf_asset(
                    path.read_bytes(), path.name, detected_source, chunks
                )
                if asset_record:
                    folder_result["asset_registered"] = True
                    folder_result["asset_id"]         = asset_record["asset_id"]
            results.append(folder_result)
        except Exception as e:
            results.append({
                "filename":       str(path.relative_to(folder)),
                "status":         "error",
                "source_type":    "",
                "chunks_indexed": 0,
                "detail":         str(e),
            })

    ok_count        = sum(1 for r in results if r["status"] == "ok")
    replaced_count  = sum(1 for r in results if r["status"] == "replaced")
    duplicate_count = sum(1 for r in results if r["status"] == "duplicate")
    error_count     = sum(1 for r in results if r["status"] == "error")

    return {
        "success":              error_count == 0,
        "folder":               str(folder),
        "files_processed":      ok_count + replaced_count,
        "files_replaced":       replaced_count,
        "files_duplicates":     duplicate_count,
        "files_errored":        error_count,
        "total_chunks_indexed": total_chunks,
        "results":              results,
    }


# ── Documents ──────────────────────────────────────────────────────────────────

@app.get("/documents")
async def list_documents():
    return {"documents": vector_store.list_sources()}


@app.delete("/documents")
async def clear_documents():
    vector_store.clear()
    return {"success": True, "message": "All documents cleared"}


@app.get("/documents/algorithms")
async def list_algorithm_documents():
    """
    Debug/admin route (v1.0.0) — lists all indexed Algorithm source files and
    reports whether each has a stored algorithm_graph payload in the vector store.

    Use this to diagnose algorithm renderer failures:
      - graph_available=False → algorithm JSON was ingested but graph chunk
        wasn't stored (check processor.py algorithm_graph chunk emission)
      - graph_available=True  → graph exists; if renderer still fails, the issue
        is in build_visualization() (node type filter, artifact filter, etc.)

    Hit: GET /documents/algorithms
    """
    all_sources = vector_store.list_sources()

    # Filter to Algorithm source type only
    algo_sources = [
        s for s in all_sources
        if (s.get("source_type") or "").lower() in ("algorithm", "algorithms")
    ]

    results = []
    for src in algo_sources:
        fname = src.get("filename") or src.get("source") or ""
        graph_available = False
        node_count      = 0

        if fname:
            try:
                graph = vector_store.get_algorithm_graph(fname)
                if graph:
                    graph_available = True
                    node_count = len(graph.get("nodes", []))
            except Exception:
                graph_available = False

        results.append({
            "filename":        fname,
            "source_type":     src.get("source_type", ""),
            "chunk_count":     src.get("chunk_count", 0),
            "graph_available": graph_available,
            "node_count":      node_count,
            "title":           src.get("title", ""),
        })

    # Also check via list_algorithm_graphs() if the store exposes it
    store_graph_listing = []
    lister = getattr(vector_store, "list_algorithm_graphs", None)
    if callable(lister):
        try:
            store_graph_listing = lister() or []
        except Exception:
            pass

    return {
        "algorithm_source_files": len(algo_sources),
        "graphs_available":       sum(1 for r in results if r["graph_available"]),
        "sources":                results,
        "store_graph_listing":    store_graph_listing,
        "note": (
            "graph_available=False means the algorithm_graph chunk was not stored "
            "during ingest. Re-upload the algorithm file to fix."
        ),
    }


# ── Feedback ───────────────────────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    session_id:    str
    message_index: int
    rating:        int   # +1 thumbs-up, -1 thumbs-down

@app.post("/feedback")
async def feedback(request: FeedbackRequest):
    if request.rating not in (1, -1):
        raise HTTPException(status_code=400, detail="rating must be 1 or -1")
    save_feedback(request.session_id, request.message_index, request.rating)
    return {"ok": True}


# ── Chat ───────────────────────────────────────────────────────────────────────

@app.post("/chat")
async def chat(request: ChatRequest, background_tasks: BackgroundTasks):
    query        = request.query.strip()
    session_id   = request.session_id
    clinician_id = (request.clinician_id or "anonymous").strip()

    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    if vector_store.count() == 0:
        return {
            "answer": "No documents have been indexed yet. Please upload ARUP content.",
            "recommendations": [], "citations": [],
            "confidence": {
                "score": 0, "level": "low",
                "factors": ["No knowledge base"], "needs_review": True,
                "per_test_scores": [], "conflicts_detected": False,
            },
            "intent": {}, "follow_up_questions": [], "clarification_questions": [],
            "clarification": {"needed": False, "question": "", "options": []},
            "clinical_context": None, "conflicts_surfaced": [], "evidence_gaps": [],
            "algorithm_visualization": None,
            "algorithm_debug":         None,
            "ui_schema":               None,
            "disclaimer":              "",
        }

    # ── Load session context ────────────────────────────────────────────────────
    clinical_context = None
    prior_sessions   = []
    clar_state       = _empty_clar_state()   # default: fresh session

    if session_id and get_session(session_id):
        clinical_context = get_clinical_context(session_id)
        prior_sessions   = get_recent_sessions(clinician_id, limit=3)
        clar_state       = clinical_context.get("clarification_state", _empty_clar_state())

    questions_asked = clar_state.get("questions_asked_count", 0)
    hard_stop       = clar_state.get("hard_stop", False)

    # ── 0. Clarification answer detection ──────────────────────────────────────
    #
    # If the previous turn ended with a pending clarification question AND we have
    # a live session, this incoming query is the user's answer to that question.
    # We resolve the state BEFORE running the prompt agent so the updated
    # confirmed_clarifications are visible to intent analysis and retrieval.
    #
    pending_question = clar_state.get("pending_question")
    if pending_question and session_id and get_session(session_id):
        # Store (question, answer) → clear pending_question → get updated context
        clinical_context = resolve_pending_clarification(session_id, clinician_id, query)
        clar_state       = clinical_context.get("clarification_state", _empty_clar_state())
        questions_asked  = clar_state.get("questions_asked_count", 0)
        hard_stop        = clar_state.get("hard_stop", False)
        # pending_question is now None — intent re-evaluation proceeds below
        # with the updated context that includes the confirmed answer

    # ── 1. Prompt Agent — intent extraction ─────────────────────────────────────
    intent = await prompt_agent.analyze(
        query,
        request.history,
        clinical_context=clinical_context,
        prior_sessions=prior_sessions,
        clarification_state=clar_state,
    )

    # ── 2. Clarification gate (backend-enforced sequencing) ───────────────────
    #
    # The LLM may have set clarification.needed=True.  The backend OVERRIDES
    # this to False if:
    #   (a) the session has already hit MAX_CLARIFICATION_QUESTIONS, AND
    #   (b) no hard safety blocker is active
    #
    clar_obj = intent.get("clarification", {})
    clar_needed = clar_obj.get("needed", False) and bool(clar_obj.get("question"))

    if clar_needed and questions_asked >= MAX_CLARIFICATION_QUESTIONS and not hard_stop:
        # Suppress — proceed with best-effort answer
        clar_needed = False
        clar_obj    = {"needed": False, "question": "", "options": [], "reason": "",
                       "question_id": "", "hard_stop": False}
        intent["clarification"]            = clar_obj
        intent["clarification_questions"]  = []
        intent["needs_clarification"]      = False
        if session_id and get_session(session_id):
            suppress_further_clarification(session_id, clinician_id)

    if clar_needed:
        # Record this question synchronously before returning
        question_id   = clar_obj.get("question_id", "")
        question_text = clar_obj.get("question", "")
        if session_id and get_session(session_id):
            clar_state = record_clarification_question(
                session_id, clinician_id, question_id, question_text=question_text
            )

        questions_asked_now = clar_state.get("questions_asked_count", questions_asked + 1)
        questions_remaining = max(0, MAX_CLARIFICATION_QUESTIONS - questions_asked_now)

        return {
            "answer": "To give you the most accurate recommendation, I have one question:",
            "recommendations": [], "citations": [],
            "confidence": {
                "score": 20, "level": "low",
                "factors": [
                    f"Clarification needed ({questions_asked_now}/{MAX_CLARIFICATION_QUESTIONS} questions asked)",
                    f"{questions_remaining} question(s) remaining in this session",
                ],
                "needs_review": True, "per_test_scores": [], "conflicts_detected": False,
            },
            "intent":                  intent,
            "follow_up_questions":     [],
            "clarification_questions": intent.get("clarification_questions", []),
            "clarification":           clar_obj,
            "clinical_context":        clinical_context,
            "conflicts_surfaced":      [],
            "evidence_gaps":           [],
            "algorithm_visualization": None,
            "algorithm_debug":         None,
            "ui_schema":               None,
            "disclaimer":              "",
        }

    # ── 3. Retrieval Agent — planner-driven vector search ────────────────────────
    raw_chunks = await retrieval_agent.retrieve(intent)

    # ── 4. Evidence Packager ───────────────────────────────────────────────────
    intent_type     = intent.get("intent_type", "ambiguous")
    evidence_bundle = evidence_packager.package(raw_chunks, intent_type=intent_type)

    # ── 5. Response Agent ──────────────────────────────────────────────────────
    response = await response_agent.generate(query, intent, evidence_bundle)

    # ── 6. Confidence Agent ────────────────────────────────────────────────────
    confidence = confidence_agent.score(
        query, evidence_bundle, response, intent_type=intent_type
    )

    # ── 7. Algorithm Rendering Engine ─────────────────────────────────────────
    # Two rendering paths:
    #   1. Fidelity-first renderer (if approved chartflow rules exist)
    #   2. Clinical renderer (fallback or when fidelity renderer unavailable)
    
    algorithm_viz = None
    algo_debug = {}
    render_mode_used = "none"
    
    # Try fidelity-first renderer ONLY if rules are approved
    fidelity_attempted = False
    if _fidelity_renderer_available and _chartflow_rule_store:
        try:
            approved_rules = _chartflow_rule_store.get_approved_rules()
            
            if approved_rules:
                fidelity_attempted = True
                # Find algorithm graph data from evidence bundle
                algo_sources = evidence_bundle.get("algorithm_sources", [])
                graph_data = None
                for src in algo_sources:
                    if src.get("algorithm_graph"):
                        graph_data = src["algorithm_graph"]
                        break
                
                if graph_data:
                    # Normalize graph using chartflow categories
                    normalized_graph = _chartflow_normalizer.normalize_graph(graph_data)
                    
                    # Get PDF URL if available
                    source_pdf_url = None
                    source_asset_id = None
                    if algo_sources and asset_store:
                        first_algo = algo_sources[0]
                        filename = first_algo.get("filename", "")
                        if filename:
                            match_result = asset_store.find_matching_pdf(filename)
                            if match_result:
                                source_asset_id = match_result["asset_id"]
                                source_pdf_url = f"/api/assets/pdf/{source_asset_id}"
                    
                    # Render with fidelity-first approach
                    fidelity_renderer = ArupAlgorithmTemplateRenderer(approved_rules)
                    fidelity_result = fidelity_renderer.render_algorithm(
                        graph_data=graph_data,
                        normalized_graph=normalized_graph,
                        source_pdf_url=source_pdf_url,
                        source_asset_id=source_asset_id,
                    )
                    
                    if fidelity_result and fidelity_result.get("nodes"):
                        algorithm_viz = fidelity_result
                        render_mode_used = "fidelity_first"
                        algo_debug = {
                            "renderer": "arup_fidelity_first",
                            "rules_version": approved_rules.get("schema_version", "unknown"),
                            "stats": fidelity_result.get("stats", {}),
                        }
                        logger.info(
                            "AlgorithmRenderer: Using fidelity-first renderer, "
                            "nodes=%d footer_blocks=%d",
                            len(fidelity_result.get("nodes", [])),
                            len(fidelity_result.get("footer_blocks", [])),
                        )
        except Exception as e:
            logger.warning("Fidelity-first renderer failed, will use clinical: %s", e)
            algorithm_viz = None
            fidelity_attempted = False
    
    # ALWAYS use clinical renderer if fidelity didn't produce a result
    # This ensures algorithms always render even without approved chartflow rules
    if algorithm_viz is None:
        logger.info("Using clinical renderer (fidelity_attempted=%s)", fidelity_attempted)
        algo_result = algorithm_renderer.render_for_bundle(evidence_bundle, debug=True)
        algorithm_viz = algo_result.get("algorithm_visualization")
        algo_debug = algo_result.get("_debug", {})
        
        if algorithm_viz:
            render_mode_used = "clinical_fallback" if fidelity_attempted else "clinical_default"
            if "renderer" not in algo_debug:
                algo_debug["renderer"] = "clinical_graph_classifier"
        
        # Diagnostic counters for logging
        algo_chunk_count = sum(
            1 for c in evidence_bundle.get("authority_sources", [])
            if c.get("source_type") == "Algorithm"
        )
        cands_with_algo = sum(
            1 for ct in evidence_bundle.get("candidate_tests", [])
            if ct.get("confidence_signals", {}).get("has_algorithm")
        )
        logger.info(
            "AlgorithmRenderer (clinical): algo_chunks=%d candidates_with_algo=%d "
            "viz_found=%s renderer=%s",
            algo_chunk_count,
            cands_with_algo,
            algorithm_viz is not None,
            render_mode_used,
        )

    # ── 7.5 Formatting Agent — UI schema generation ────────────────────────────
    # Deterministic component composition — no LLM calls.
    # evidence_context feeds the fallback diagnostic info_block and render hints.
    ui_schema = formatting_agent.format(
        response=response,
        algorithm_viz=algorithm_viz,
        confidence=confidence,
        intent=intent,
        evidence_context={
            "algorithm_sources": evidence_bundle.get("algorithm_sources", []),
            "algorithm_debug":   algo_debug,
        },
    )

    # ── 7.6 Store schema snapshot for Designer Panel inspection ───────────────
    if session_id:
        _designer_schema_store[session_id] = ui_schema

    # ── 8. Background: update session context (preserves clar_state) ──────────
    if session_id and get_session(session_id):
        background_tasks.add_task(
            _bg_update_context,
            session_id, clinician_id, query,
            response.get("answer", ""),
            response.get("recommendations", []),
        )

    return {
        "answer":                  response.get("answer", ""),
        "recommendations":         response.get("recommendations", []),
        "citations":               response.get("citations", []),
        "confidence":              confidence,
        "intent":                  intent,
        "follow_up_questions":     response.get("follow_up_questions", []),
        "clarification_questions": [],        # empty — clarification was not triggered
        "clarification":           {"needed": False, "question": "", "options": []},
        "clinical_context":        clinical_context,
        "conflicts_surfaced":      response.get("conflicts_surfaced", []),
        "evidence_gaps":           response.get("evidence_gaps", []),
        "algorithm_visualization": algorithm_viz,
        # ── Debug block (v1.1.0) — ignored by frontend, readable in DevTools ──
        "algorithm_debug": {
            "render_mode_used": render_mode_used,
            "fidelity_renderer_available": _fidelity_renderer_available,
            "chartflow_rules_approved": bool(_chartflow_rule_store and _chartflow_rule_store.get_approved_rules()) if _chartflow_rule_store else False,
            "retrieved_algorithm_chunks": sum(
                1 for c in evidence_bundle.get("authority_sources", [])
                if c.get("source_type") == "Algorithm"
            ) if 'evidence_bundle' in locals() else 0,
            "candidate_tests_with_algorithm": sum(
                1 for ct in evidence_bundle.get("candidate_tests", [])
                if ct.get("confidence_signals", {}).get("has_algorithm")
            ) if 'evidence_bundle' in locals() else 0,
            "algorithm_sources_indexed": len(evidence_bundle.get("algorithm_sources", [])) if 'evidence_bundle' in locals() else 0,
            "renderer_debug": algo_debug,
            # PDF match diagnostics — shows which matching strategy fired (or why it failed)
            "pdf_match_debug": (algorithm_viz or {}).get("pdf_match_debug", {
                "strategy": "no_viz_rendered",
            }),
        },
        "ui_schema":  ui_schema,
        "disclaimer": (
            "Recommendations are grounded in uploaded ARUP content. "
            "For complex clinical presentations, consult an ARUP pathologist. "
            "This tool supports — and does not replace — independent clinical review."
        ),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8010, reload=True)