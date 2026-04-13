"""
context_extractor.py
────────────────────────────────────────────────────────────────────────────
Two lightweight Haiku calls:

  1. extract_context()  — after each turn, update the structured
     clinical context JSON with any new facts from the exchange.
     ~50–80 output tokens. Very cheap.

  2. summarise_session() — when a session ends, compress the full
     conversation into a 3–5 sentence paragraph for cross-session memory.
     ~100–150 output tokens.

Both calls are fire-and-forget (background tasks in main.py) so they
never add latency to the user's response.
"""

import os
import json
import anthropic
from typing import Dict

# ── Client ────────────────────────────────────────────────────────────────

def _client():
    return anthropic.Anthropic(
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        timeout=30.0,
        max_retries=2,
    )

_MODEL = "claude-haiku-4-5-20251001"


# ── Context extraction ────────────────────────────────────────────────────

_EXTRACT_SYSTEM = """You update a structured clinical context JSON based on a new conversation exchange.

RULES:
- Only add facts EXPLICITLY stated in the exchange. Never infer or assume.
- If the user mentions a patient age/sex, add it to patient block.
- If a test was recommended and accepted, add to tests_ordered.
- If a condition was confirmed, add to relevant_conditions.
- If a clarification was answered, add to confirmed_clarifications.
- Keep all lists deduplicated.
- primary_concern should be a short phrase (e.g. "acromegaly workup").
- Return ONLY valid JSON matching the input schema. No prose, no fences.
"""

def extract_context(
    current_context: Dict,
    user_query: str,
    assistant_answer: str,
    recommendations: list,
) -> Dict:
    """
    Update the clinical context JSON with facts from the latest exchange.
    Returns the updated context. Falls back to current_context on any error.
    """
    # Build a compact recommendation summary to help extraction
    rec_summary = ""
    if recommendations:
        names = [r.get("test_name", "") for r in recommendations[:4] if r.get("test_name")]
        if names:
            rec_summary = f"\nTests recommended: {', '.join(names)}"

    user_content = (
        f"Current context:\n{json.dumps(current_context, indent=2)}\n\n"
        f"New user message: {user_query}\n"
        f"New assistant answer: {assistant_answer[:600]}{rec_summary}\n\n"
        f"Return the updated context JSON."
    )

    try:
        msg = _client().messages.create(
            model=_MODEL,
            max_tokens=400,
            system=_EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0]
        updated = json.loads(raw.strip())
        # Safety: merge so we never lose keys even if the model omits them
        return _deep_merge(current_context, updated)
    except Exception:
        return current_context


# ── Session summariser ────────────────────────────────────────────────────

_SUMMARISE_SYSTEM = """Summarise a clinical advisory session in 3-5 sentences.

Include: conditions or diseases discussed, ARUP tests recommended (with codes if mentioned),
key clinical decisions made, and any patient context explicitly provided.

Be factual and concise. Do not include disclaimers. Return plain text only — no JSON, no headers."""

def summarise_session(history: list) -> str:
    """
    Compress a full conversation history into a short paragraph.
    Returns a plain-text summary, or empty string on failure.
    """
    if not history:
        return ""

    # Build a compact transcript (user turns + answer excerpts only)
    lines = []
    for m in history[-20:]:       # cap at last 20 messages
        role = "Clinician" if m.get("role") == "user" else "Advisor"
        content = (m.get("content") or "")[:300]
        lines.append(f"{role}: {content}")
    transcript = "\n".join(lines)

    try:
        msg = _client().messages.create(
            model=_MODEL,
            max_tokens=200,
            system=_SUMMARISE_SYSTEM,
            messages=[{"role": "user", "content": f"Session transcript:\n{transcript}"}],
        )
        return msg.content[0].text.strip()
    except Exception:
        return ""


# ── Helpers ───────────────────────────────────────────────────────────────

def _deep_merge(base: Dict, update: Dict) -> Dict:
    """
    Merge update into base recursively.
    Lists are unioned (deduplicated). Scalars are overwritten if non-null.
    """
    result = json.loads(json.dumps(base))   # deep copy
    for k, v in update.items():
        if k not in result:
            result[k] = v
        elif isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        elif isinstance(result[k], list) and isinstance(v, list):
            # Union deduplicated
            existing = set(
                json.dumps(i, sort_keys=True) if isinstance(i, dict) else str(i)
                for i in result[k]
            )
            for item in v:
                key = json.dumps(item, sort_keys=True) if isinstance(item, dict) else str(item)
                if key not in existing:
                    result[k].append(item)
                    existing.add(key)
        elif v is not None and v != "" and v != [] and v != {}:
            result[k] = v
    return result
