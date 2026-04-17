"""
session_store.py  v0.8.0
────────────────────────────────────────────────────────────────────────────
SQLite-backed store for sessions and clinical context.

Changes from v0.7:
  • record_clarification_question() now accepts question_text and stores it
    as pending_question — completing the clarification state contract
  • NEW: resolve_pending_clarification() — called when the user's next turn
    answers a pending question:
      - copies (question, answer) into session.confirmed_clarifications
      - clears pending_question from clarification_state
      - returns the fully updated context dict so main.py can re-run intent
  • suppress_further_clarification() now also clears pending_question

Changes from v0.6:
  • EMPTY_CONTEXT extended with clarification_state block:
      questions_asked_count, last_question_id, pending_question, hard_stop
  • New helpers (matching live main.py imports exactly):
      get_clarification_state()          — read just the clarification_state block
      record_clarification_question()    — increment count, record question_id+text, return state
      suppress_further_clarification()   — permanently mark session as cap-reached
"""

import os
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict


_DB_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../data/sessions.db"
)


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(_DB_PATH)), exist_ok=True)
    con = sqlite3.connect(_DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    """Create tables if they don't exist. Called at app startup."""
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id   TEXT PRIMARY KEY,
                clinician_id TEXT NOT NULL,
                started_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                ended_at     TEXT,
                summary      TEXT,
                turn_count   INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS clinical_context (
                session_id   TEXT PRIMARY KEY,
                clinician_id TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                context_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_clinician
                ON sessions(clinician_id, started_at DESC);

            CREATE INDEX IF NOT EXISTS idx_context_clinician
                ON clinical_context(clinician_id);

            CREATE TABLE IF NOT EXISTS message_feedback (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   TEXT NOT NULL,
                message_index INTEGER NOT NULL,
                rating       INTEGER NOT NULL,
                created_at   TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_feedback_session
                ON message_feedback(session_id);
        """)


# ── Empty context template ─────────────────────────────────────────────────────

EMPTY_CONTEXT: Dict = {
    "patient": {
        "age":                 None,
        "sex":                 None,
        "relevant_conditions": [],
        "current_medications": [],
        "notes":               "",
    },
    "session": {
        "primary_concern":          "",
        "tests_ordered":            [],
        "tests_ruled_out":          [],
        "confirmed_clarifications": [],
        "open_questions":           [],
    },
    # v0.7: clarification flow state — enforces one-question-per-turn + max-3 policy
    "clarification_state": {
        # Total clarification questions asked this session
        "questions_asked_count": 0,
        # Short identifier of the last question (prevents re-asking same variable)
        "last_question_id":      None,
        # Full text of the currently pending (unanswered) question
        "pending_question":      None,
        # True if a hard safety blocker is active — bypasses the 3-question cap
        "hard_stop":             False,
    },
}

MAX_CLARIFICATION_QUESTIONS = 3


# ── Clarification normalization ────────────────────────────────────────────────

def _normalize_confirmed_clarifications(items) -> list:
    """
    Normalize confirmed_clarifications to a uniform list[dict] shape.

    Accepts any mix of legacy strings or current dicts and coerces each to:
        {"question": str, "answer": str}

    This handles stale SQLite rows written before the dict-based schema was
    introduced — no manual DB cleanup required.
    """
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        if isinstance(item, dict):
            normalized.append({
                "question": str(item.get("question") or ""),
                "answer":   str(item.get("answer")   or ""),
            })
        elif isinstance(item, str) and item.strip():
            # Legacy string format: treat the whole string as the answer
            normalized.append({"question": "", "answer": item.strip()})
        # Skip None / empty / malformed entries silently
    return normalized


# ── Session CRUD ───────────────────────────────────────────────────────────────

def create_session(clinician_id: str) -> str:
    session_id = str(uuid.uuid4())
    now = _now()
    with _conn() as con:
        con.execute(
            "INSERT INTO sessions (session_id, clinician_id, started_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, clinician_id, now, now),
        )
    return session_id


def get_session(session_id: str) -> Optional[Dict]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def increment_turn(session_id: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE sessions SET turn_count = turn_count + 1, updated_at = ? "
            "WHERE session_id = ?",
            (_now(), session_id),
        )


def save_session_summary(session_id: str, summary: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE sessions SET summary = ?, updated_at = ?, ended_at = ? "
            "WHERE session_id = ?",
            (summary, _now(), _now(), session_id),
        )


def get_recent_sessions(clinician_id: str, limit: int = 5) -> List[Dict]:
    """Return the last N completed sessions with summaries for a clinician."""
    with _conn() as con:
        rows = con.execute(
            "SELECT session_id, started_at, summary, turn_count "
            "FROM sessions "
            "WHERE clinician_id = ? AND summary IS NOT NULL "
            "ORDER BY started_at DESC LIMIT ?",
            (clinician_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Clinical context CRUD ──────────────────────────────────────────────────────

def get_clinical_context(session_id: str) -> Dict:
    """Return the current structured context for a session, or empty template."""
    with _conn() as con:
        row = con.execute(
            "SELECT context_json FROM clinical_context WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if row:
        try:
            stored = json.loads(row["context_json"])
            # Backfill clarification_state for sessions created before v0.7
            if "clarification_state" not in stored:
                stored["clarification_state"] = {
                    k: v for k, v in EMPTY_CONTEXT["clarification_state"].items()
                }
            # Normalize confirmed_clarifications — handles stale string-list rows
            # from sessions written before the dict-based schema (v0.8+).
            session_block = stored.setdefault("session", {})
            session_block["confirmed_clarifications"] = _normalize_confirmed_clarifications(
                session_block.get("confirmed_clarifications", [])
            )
            return stored
        except Exception:
            pass
    return json.loads(json.dumps(EMPTY_CONTEXT))   # deep copy of template


def save_clinical_context(session_id: str, clinician_id: str, context: Dict) -> None:
    """Upsert the full clinical context for a session."""
    with _conn() as con:
        con.execute(
            "INSERT INTO clinical_context (session_id, clinician_id, updated_at, context_json) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "  context_json = excluded.context_json, "
            "  updated_at   = excluded.updated_at",
            (session_id, clinician_id, _now(), json.dumps(context)),
        )


# ── Clarification state helpers ────────────────────────────────────────────────

def get_clarification_state(session_id: str) -> Dict:
    """
    Return just the clarification_state block.
    Never raises — returns the empty template block on any failure.
    """
    ctx = get_clinical_context(session_id)
    return ctx.get(
        "clarification_state",
        {k: v for k, v in EMPTY_CONTEXT["clarification_state"].items()},
    )


def record_clarification_question(
    session_id: str,
    clinician_id: str,
    question_id: str,
    question_text: str = "",
) -> Dict:
    """
    Record that a clarification question was asked this turn.

    Increments questions_asked_count, stores last_question_id, and — crucially —
    stores the full question text as pending_question so the next turn can detect
    it is answering a known clarification and route accordingly.

    Returns the updated clarification_state dict.
    Called synchronously in the main request path before returning to frontend.
    """
    ctx = get_clinical_context(session_id)
    cs  = ctx.get(
        "clarification_state",
        {k: v for k, v in EMPTY_CONTEXT["clarification_state"].items()},
    )
    cs["questions_asked_count"] = cs.get("questions_asked_count", 0) + 1
    cs["last_question_id"]      = question_id or None
    cs["pending_question"]      = question_text.strip() or None   # ← NEW
    ctx["clarification_state"]  = cs
    save_clinical_context(session_id, clinician_id, ctx)
    return cs


def resolve_pending_clarification(
    session_id: str,
    clinician_id: str,
    answer: str,
) -> Dict:
    """
    Call this when the user's next turn answers the pending clarification.

    Actions performed (all atomic — one DB write):
      1. Appends {question, answer} to session.confirmed_clarifications
      2. Clears pending_question in clarification_state (sets to None)

    Returns the fully updated context dict so main.py can pass it to the
    prompt agent for a re-evaluated intent before retrieval.

    Safe to call even if pending_question is already None — becomes a no-op.
    """
    ctx     = get_clinical_context(session_id)
    cs      = ctx.get(
        "clarification_state",
        {k: v for k, v in EMPTY_CONTEXT["clarification_state"].items()},
    )
    pending = cs.get("pending_question")

    if pending:
        # Record the resolved pair in the session
        session_block = ctx.setdefault("session", {})
        confirmed     = session_block.setdefault("confirmed_clarifications", [])
        confirmed.append({
            "question": pending,
            "answer":   answer.strip(),
        })
        # Clear the pending slot
        cs["pending_question"] = None
        ctx["clarification_state"] = cs
        save_clinical_context(session_id, clinician_id, ctx)

    return ctx


def suppress_further_clarification(session_id: str, clinician_id: str) -> None:
    """
    Permanently mark this session as having reached the clarification cap.
    Sets questions_asked_count to MAX so future turns never ask again,
    and clears any pending_question so it isn't incorrectly matched next turn.

    Called when the backend overrides a clarification attempt because
    MAX_CLARIFICATION_QUESTIONS has already been reached.
    """
    ctx = get_clinical_context(session_id)
    cs  = ctx.get(
        "clarification_state",
        {k: v for k, v in EMPTY_CONTEXT["clarification_state"].items()},
    )
    cs["questions_asked_count"] = max(
        cs.get("questions_asked_count", 0),
        MAX_CLARIFICATION_QUESTIONS,
    )
    cs["pending_question"] = None          # ← NEW: clean up any stale pending
    ctx["clarification_state"] = cs
    save_clinical_context(session_id, clinician_id, ctx)


# ── Feedback ──────────────────────────────────────────────────────────────────

def save_feedback(session_id: str, message_index: int, rating: int) -> None:
    """Store thumbs-up (+1) or thumbs-down (-1) for a specific assistant message."""
    with _conn() as con:
        con.execute(
            "INSERT INTO message_feedback (session_id, message_index, rating, created_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, message_index, rating, _now()),
        )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()