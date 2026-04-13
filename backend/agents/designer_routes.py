"""
designer_routes.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
Designer API Routes — UX Training & Schema Preview Endpoints

Provides REST endpoints for the Designer Panel frontend component:

  GET  /designer/preferences           — load current formatting_rules.json
  POST /designer/preferences           — save & hot-reload preferences
  GET  /designer/components            — list available component catalogue
  POST /designer/preview               — generate a UI schema from sample data
  GET  /designer/schema/{session_id}   — retrieve last schema for a session
  POST /designer/component/generate    — synthesise a new component spec

These routes are registered as a FastAPI router and mounted at /designer
in main.py.  They share the formatting_agent singleton with the chat pipeline
so preference changes are immediately reflected in live responses.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

import anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.design_library_store import DesignLibraryStore

# Singleton library store (shared across all requests)
_library_store = DesignLibraryStore()

router = APIRouter(prefix="/designer", tags=["designer"])


# ── Request / Response models ──────────────────────────────────────────────────

class PreferencesPayload(BaseModel):
    """Body for POST /designer/preferences"""
    preferences: dict
    updated_by: Optional[str] = "designer"


class PreviewPayload(BaseModel):
    """
    Body for POST /designer/preview.
    Accepts a minimal synthetic response payload so the Designer Panel can
    preview how the FormattingAgent would schema-ify it.
    """
    answer: str = "Sample clinical answer text."
    recommendations: list = []
    citations:        list = []
    conflicts_surfaced: list = []
    evidence_gaps:    list = []
    algorithm_visualization: Optional[dict] = None
    confidence_score: int = 75
    intent_type: str = "test_selection"


class ComponentGeneratePayload(BaseModel):
    """Body for POST /designer/component/generate"""
    name:        str
    description: str
    props:       Optional[dict] = None
    based_on:    Optional[str] = None   # existing component type to derive from


# ── Library request/response models ───────────────────────────────────────────

class LibraryPreviewPayload(BaseModel):
    """Body for POST /designer/library/preview"""
    component_id:   str
    override_props: Optional[dict] = None

class LibraryCreatePayload(BaseModel):
    """Body for POST /designer/library/component"""
    name:          str
    type:          str
    description:   str
    tags:          list = []
    rules:         Optional[dict] = None
    preview_props: Optional[dict] = None
    based_on:      Optional[str] = None

class LibraryFeedbackPayload(BaseModel):
    """Body for POST /designer/library/feedback"""
    component_id: str
    feedback:     str

class LibraryApprovePayload(BaseModel):
    """Body for POST /designer/library/approve"""
    component_id: str


# ── Route factory (takes formatting_agent instance) ───────────────────────────

def build_router(fa_instance) -> APIRouter:
    """
    Returns the configured router with access to the FormattingAgent singleton.
    Called once from main.py:

        from agents.designer_routes import build_router
        app.include_router(build_router(formatting_agent))
    """

    # In-memory store for per-session schema snapshots (populated by main.py)
    # Key: session_id → last generated ui_schema
    # Shared with main.py via the module-level dict below
    _schema_store: dict[str, dict] = {}

    @router.get("/preferences")
    async def get_preferences():
        """Return the current formatting_rules.json content."""
        return {
            "preferences":        fa_instance.get_preferences(),
            "preferences_applied": fa_instance._preferences_applied,
            "rules_file":         str(fa_instance._rules_path),
        }

    @router.post("/preferences")
    async def save_preferences(payload: PreferencesPayload):
        """
        Persist updated preferences and hot-reload the FormattingAgent.
        Changes are immediately reflected in subsequent /chat responses.
        """
        prefs = payload.preferences
        # Stamp metadata
        prefs.setdefault("_meta", {})
        prefs["_meta"]["last_updated"] = datetime.now(timezone.utc).isoformat()
        prefs["_meta"]["updated_by"]   = payload.updated_by

        try:
            fa_instance.set_preferences(prefs)
        except (OSError, PermissionError) as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to write formatting_rules.json: {e}"
            )

        return {
            "success":     True,
            "message":     "Preferences saved and hot-reloaded.",
            "updated_by":  payload.updated_by,
            "last_updated": prefs["_meta"]["last_updated"],
        }

    @router.get("/components")
    async def get_components():
        """
        Return the component catalogue — all available types, variants, and
        overridable props.  Used by the Designer Panel Component Gallery tab.
        """
        return {
            "components": fa_instance.get_available_components(),
            "custom_components": fa_instance.get_preferences().get("custom_components", []),
        }

    @router.post("/preview")
    async def preview_schema(payload: PreviewPayload):
        """
        Generate a UI schema from synthetic or partial response data.
        Lets designers preview layout changes without running a real query.
        """
        synthetic_response = {
            "answer":             payload.answer,
            "recommendations":    payload.recommendations,
            "citations":          payload.citations,
            "conflicts_surfaced": payload.conflicts_surfaced,
            "evidence_gaps":      payload.evidence_gaps,
        }
        synthetic_confidence = {
            "score":         payload.confidence_score,
            "level":         "high" if payload.confidence_score >= 70 else "moderate",
            "needs_review":  payload.confidence_score < 40,
            "factors":       [],
            "per_test_scores": [],
            "conflicts_detected": len(payload.conflicts_surfaced) > 0,
        }
        synthetic_intent = {
            "intent_type": payload.intent_type,
        }

        algorithm_viz = None
        if payload.algorithm_visualization:
            algorithm_viz = payload.algorithm_visualization

        schema = fa_instance.format(
            response=synthetic_response,
            algorithm_viz=algorithm_viz,
            confidence=synthetic_confidence,
            intent=synthetic_intent,
        )

        return {
            "ui_schema":    schema,
            "preview_mode": True,
            "input_summary": {
                "recommendations": len(payload.recommendations),
                "citations":       len(payload.citations),
                "conflicts":       len(payload.conflicts_surfaced),
                "gaps":            len(payload.evidence_gaps),
                "has_algorithm":   algorithm_viz is not None,
            },
        }

    @router.get("/schema/{session_id}")
    async def get_session_schema(session_id: str):
        """
        Retrieve the most recently generated UI schema for a session.
        Populated by the /chat pipeline in main.py.
        """
        schema = _schema_store.get(session_id)
        if schema is None:
            raise HTTPException(
                status_code=404,
                detail=f"No schema snapshot found for session '{session_id}'. "
                       "Run a /chat query first."
            )
        return {"session_id": session_id, "ui_schema": schema}

    @router.post("/component/generate")
    async def generate_component(payload: ComponentGeneratePayload):
        """
        Synthesise a new component specification from existing primitives.
        Returns a JSON spec + React skeleton that the designer can refine and
        optionally add to the custom_components list in formatting_rules.json.
        """
        base_props = payload.props or {}

        # If based_on is provided, inherit defaults from the catalogue
        inherited = {}
        if payload.based_on:
            catalogue = {c["type"]: c for c in fa_instance.get_available_components()}
            if payload.based_on in catalogue:
                inherited = {"based_on_type": payload.based_on}

        spec = {
            "type":        payload.name.lower().replace(" ", "_"),
            "variant":     "default",
            "description": payload.description,
            "props":       {
                "content":     "",
                "typography":  "text-body",
                "color_token": "var(--dark-sky)",
                "bg_token":    "var(--bg-salt)",
                "border_token":"var(--accent3)",
                **base_props,
            },
            **inherited,
            "react_skeleton": _generate_react_skeleton(payload.name, base_props),
            "add_to_preferences": {
                "instruction": (
                    "To register this component, add it to "
                    "formatting_rules.json under 'custom_components', "
                    "then POST the updated preferences to /designer/preferences."
                ),
                "payload": {
                    "type":        payload.name.lower().replace(" ", "_"),
                    "description": payload.description,
                    "props":       base_props,
                },
            },
        }

        return {"component_spec": spec}

    # ── Library endpoints ─────────────────────────────────────────────────────

    @router.get("/tokens")
    async def get_design_tokens():
        """
        Return all design-system token files (typography, buttons, colors,
        table, card_rules, spacing_rules).

        Used by the Designer Panel to display authoritative token rules in the
        component workbench and to drive AI-assisted component feedback prompts.
        """
        tokens = fa_instance.get_design_tokens()
        return {
            "tokens":       tokens,
            "token_files":  list(tokens.keys()),
            "count":        len(tokens),
        }

    @router.get("/library")
    async def list_library():
        """Return all design system components (summaries, no conversation history)."""
        components = _library_store.list_components()
        return {
            "components": components,
            "counts": {
                "system":   sum(1 for c in components if c.get("status") == "system"),
                "approved": sum(1 for c in components if c.get("status") == "approved"),
                "draft":    sum(1 for c in components if c.get("status") == "draft"),
            },
        }

    @router.get("/library/{component_id}")
    async def get_library_component(component_id: str):
        """Return a single component with full conversation and version history."""
        comp = _library_store.get_component(component_id)
        if comp is None:
            raise HTTPException(status_code=404, detail=f"Component '{component_id}' not found.")
        return {"component": comp}

    @router.post("/library/preview")
    async def library_preview(payload: LibraryPreviewPayload):
        """
        Return the component record with resolved preview_props.
        Optionally override preview_props for ad-hoc rendering experiments.
        """
        comp = _library_store.get_component(payload.component_id)
        if comp is None:
            raise HTTPException(status_code=404, detail=f"Component '{payload.component_id}' not found.")
        resolved_props = {**comp.get("preview_props", {}), **(payload.override_props or {})}
        return {
            "component_id": payload.component_id,
            "type":         comp["type"],
            "variant":      comp.get("variant", "default"),
            "rules":        comp.get("rules", {}),
            "preview_props": resolved_props,
        }

    @router.post("/library/component")
    async def create_library_component(payload: LibraryCreatePayload):
        """Create a new custom component and add it to the library."""
        # If based_on is set, inherit rules/preview_props from the existing component
        base_rules:  dict = {}
        base_props:  dict = {}
        if payload.based_on:
            parent = _library_store.get_component(payload.based_on)
            if parent:
                base_rules  = dict(parent.get("rules", {}))
                base_props  = dict(parent.get("preview_props", {}))

        spec = {
            "id":           payload.type.lower().replace(" ", "_"),
            "name":         payload.name,
            "type":         payload.type.lower().replace(" ", "_"),
            "variant":      "default",
            "status":       "draft",
            "description":  payload.description,
            "tags":         payload.tags,
            "source_basis": f"Derived from '{payload.based_on}'" if payload.based_on else "Custom",
            "rules":        {**base_rules,  **(payload.rules or {})},
            "preview_props":{**base_props,  **(payload.preview_props or {})},
        }
        created = _library_store.create_component(spec)
        return {"component": created, "created": True}

    @router.post("/library/feedback")
    async def library_feedback(payload: LibraryFeedbackPayload):
        """
        Submit designer feedback for a component.  Uses Claude (Haiku) to
        analyse the feedback against the current component spec and return
        a structured JSON diff: updated_rules, updated_preview_props,
        change_summary, rationale.  Persists conversation history.
        """
        comp = _library_store.get_component(payload.component_id)
        if comp is None:
            raise HTTPException(status_code=404, detail=f"Component '{payload.component_id}' not found.")

        # Persist user message
        _library_store.add_conversation_entry(payload.component_id, "user", payload.feedback)

        # Build prompt
        system_prompt = (
            "You are an expert UI design systems engineer for ARUP Laboratories. "
            "You receive a design system component specification (JSON) and a designer's "
            "natural-language feedback. You must respond ONLY with a single valid JSON object "
            "(no markdown, no backticks, no preamble) containing exactly these keys:\n"
            "  rules_patch         — dict containing ONLY the rule keys that need to change "
            "(omit unchanged keys entirely — this is a partial patch, not a full replacement)\n"
            "  preview_props_patch — dict containing ONLY the preview_props keys that need to change "
            "(omit unchanged keys entirely — partial patch only)\n"
            "  change_summary      — short string (≤80 chars) summarising what changed\n"
            "  rationale           — 1–3 sentence explanation of the design decisions made\n"
            "\nIMPORTANT: Only include keys that actually need to change in rules_patch and "
            "preview_props_patch. Omit all unchanged keys. The server will merge these patches "
            "into the existing spec using deep merge, so unmentioned keys are preserved.\n"
            "\nDesign token constraints: use only var(--primary), var(--secondary), "
            "var(--lab-blue), var(--dark-sky), var(--accent3), var(--bg-salt), var(--white), "
            "var(--positive), var(--card-padding), var(--card-radius). No raw hex values."
        )
        user_prompt = (
            f"Component spec:\n{json.dumps({k: v for k, v in comp.items() if k not in ('conversation', 'versions')}, indent=2)}"
            f"\n\nDesigner feedback:\n{payload.feedback}"
        )

        model_name = os.environ.get("MODEL_NAME", "claude-haiku-4-5-20251001")
        client = anthropic.Anthropic()
        try:
            message = client.messages.create(
                model=model_name,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = message.content[0].text.strip()
            # Strip markdown fences if model misbehaves
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            diff = json.loads(raw)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Claude feedback processing failed: {e}")

        # Apply as a patch merge — only override keys present in the patch;
        # all other existing fields are preserved unchanged.
        updates = {
            "rules":         {**comp.get("rules", {}),         **diff.get("rules_patch", {})},
            "preview_props": {**comp.get("preview_props", {}), **diff.get("preview_props_patch", {})},
        }
        _library_store.update_component(
            payload.component_id,
            updates,
            version_summary=diff.get("change_summary", "Feedback applied"),
        )
        # Persist assistant reply
        assistant_reply = (
            f"**Changes applied:** {diff.get('change_summary', '')}\n\n"
            f"{diff.get('rationale', '')}"
        )
        _library_store.add_conversation_entry(payload.component_id, "assistant", assistant_reply)

        return {
            "component_id":        payload.component_id,
            "change_summary":      diff.get("change_summary"),
            "rationale":           diff.get("rationale"),
            "rules_patch":         diff.get("rules_patch", {}),
            "preview_props_patch": diff.get("preview_props_patch", {}),
            "assistant_reply":     assistant_reply,
        }

    @router.post("/library/approve")
    async def approve_library_component(payload: LibraryApprovePayload):
        """Promote a component from draft → approved."""
        comp = _library_store.approve_component(payload.component_id)
        if comp is None:
            raise HTTPException(status_code=404, detail=f"Component '{payload.component_id}' not found.")
        return {"component_id": payload.component_id, "status": comp.get("status"), "approved": True}

    @router.get("/library/history/{component_id}")
    async def get_library_history(component_id: str):
        """Return the version history for a component."""
        history = _library_store.get_history(component_id)
        if not history and _library_store.get_component(component_id) is None:
            raise HTTPException(status_code=404, detail=f"Component '{component_id}' not found.")
        return {"component_id": component_id, "versions": history}

    @router.post("/library/reset/{component_id}")
    async def reset_library_component(component_id: str):
        """
        Reset a non-system component's rules and preview_props to its first
        version snapshot.  Status is set back to 'draft'.
        Used by the Designer Panel 'Reset to Base' affordance.
        """
        comp = _library_store.get_component(component_id)
        if comp is None:
            raise HTTPException(
                status_code=404,
                detail=f"Component '{component_id}' not found.",
            )
        if comp.get("status") == "system":
            raise HTTPException(
                status_code=400,
                detail="System components cannot be reset.",
            )
        versions = comp.get("versions", [])
        if not versions:
            raise HTTPException(
                status_code=400,
                detail="No version history to reset to.",
            )
        # First snapshot in the list is the original baseline
        first = versions[0]
        updates = {
            "rules":         first.get("rules_snapshot",        {}),
            "preview_props": first.get("preview_props_snapshot", {}),
            "status":        "draft",
        }
        updated = _library_store.update_component(
            component_id, updates,
            version_summary="Reset to initial version",
        )
        _library_store.add_conversation_entry(
            component_id, "assistant",
            "Component has been reset to its initial version. Rules and preview props restored from baseline snapshot.",
        )
        return {
            "component_id": component_id,
            "reset":        True,
            "status":       updated.get("status"),
            "message":      "Component reset to its initial version.",
        }

    # Expose the schema store so main.py can write to it
    router.schema_store = _schema_store  # type: ignore[attr-defined]

    return router


def _generate_react_skeleton(name: str, props: dict) -> str:
    """
    Generate a minimal React component skeleton string for a new component.
    Uses only design system tokens — no raw hex values.
    """
    comp_name = "".join(word.capitalize() for word in name.replace("-", " ").split())
    prop_lines = "\n  ".join(
        f"const {k} = props?.{k} || '';" for k in (list(props.keys())[:5] if props else ["content"])
    )
    return f"""function {comp_name}({{ props }}) {{
  {prop_lines}

  return (
    <div style={{{{
      padding: 'var(--card-padding)',
      background: props?.bg_token || 'var(--bg-salt)',
      border: '1px solid var(--accent3)',
      borderRadius: 'var(--card-radius)',
      marginTop: 12,
    }}}}>
      <p className={{props?.typography || 'text-body'}} style={{{{ color: props?.color_token || 'var(--dark-sky)' }}}}>
        {{content || props?.content}}
      </p>
    </div>
  )
}}"""