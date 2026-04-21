"""
design_library_store.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
Design System Library — Persistence Layer

JSON-file-backed store for component definitions, revision history, and
designer feedback conversations.  Pre-seeded with all 7 system components
used by the FormattingAgent and MessageBubble renderers.

Component model
───────────────
  id             str        Stable snake_case identifier
  name           str        Human display name
  type           str        FormattingAgent component type key
  variant        str        "default" | "compact" | "highlighted" …
  status         str        "system" | "draft" | "approved"
  description    str        One-line purpose statement
  tags           [str]      Searchable labels
  source_basis   str        Where rules originate (spec doc, design review…)
  rules          dict       Typography, colour, spacing, behaviour rules
  preview_props  dict       Sample data for live preview rendering
  conversation   [entry]    Feedback thread (role/content/timestamp)
  versions       [snapshot] Revision history (timestamped rule snapshots)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── File location ─────────────────────────────────────────────────────────────
_STORE_FILE = Path(__file__).parent / "design_system_library.json"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_version(rules: dict, preview_props: dict, summary: str) -> dict:
    return {
        "version_id": str(uuid.uuid4())[:8],
        "timestamp":  _now(),
        "summary":    summary,
        "rules_snapshot":        rules,
        "preview_props_snapshot": preview_props,
    }


# ── Seed data ─────────────────────────────────────────────────────────────────

SEED_COMPONENTS: list[dict] = [
    {
        "id": "text_block",
        "name": "Text Block",
        "type": "text_block",
        "variant": "default",
        "status": "system",
        "description": "Primary prose answer block — renders the main clinical narrative.",
        "tags": ["text", "prose", "primary", "answer"],
        "source_basis": {
            "based_on": "ARUP Design System v1 · FormattingAgent spec",
            "design_tokens_used": ["typography", "colors", "spacing_rules"],
            "sample_assets_used": [],
            "token_refs": {
                "typography": "scale.body (text-body, 16px/1.5 lh)",
                "color":      "colors.neutral.darkSky for text",
                "spacing":    "spacing_rules.semanticTokens.componentMarginTop.text_block = 0"
            }
        },
        "rules": {
            "typography": "text-body",
            "color_token": "var(--dark-sky)",
            "bg_token": "transparent",
            "line_height": 1.6,
            "max_width": "100%",
            "margin_bottom": 12,
            "supports_markdown": True,
            "confidence_threshold_for_display": 0,
        },
        "preview_props": {
            "content": (
                "Based on the clinical presentation, **Celiac Disease Antibody Panel** "
                "is the recommended first-line evaluation. Testing should include "
                "IgA tTG antibody with reflex to IgA endomysial antibody for confirmation. "
                "Total IgA should be measured concurrently to rule out selective IgA deficiency, "
                "which occurs in ~2–3% of celiac patients and causes false-negative tTG results."
            ),
        },
        "conversation": [],
        "versions": [],
    },
    {
        "id": "recommendation_card",
        "name": "Recommendation Card",
        "type": "recommendation_card",
        "variant": "primary",
        "status": "system",
        "description": "Highlighted test recommendation with priority badge, rationale, and ARUP order code.",
        "tags": ["recommendation", "test", "order", "priority", "card"],
        "source_basis": {
            "based_on": "ARUP Design System v1 · FormattingAgent spec",
            "design_tokens_used": ["typography", "colors", "card_rules", "spacing_rules", "buttons"],
            "sample_assets_used": [],
            "token_refs": {
                "card":    "card_rules.variants.semantic — 4px left border in priority colour",
                "color":   "colors.brand.primary (high) / colors.neutral.labBlue (moderate)",
                "spacing": "spacing_rules.semanticTokens.cardPadding = 24px"
            }
        },
        "rules": {
            "border_left_width": 4,
            "border_left_color_high":     "var(--primary)",
            "border_left_color_moderate": "var(--lab-blue)",
            "border_left_color_low":      "var(--accent3)",
            "bg_token":   "var(--white)",
            "padding":    "var(--card-padding)",
            "card_radius":"var(--card-radius)",
            "show_order_code": True,
            "show_turnaround": True,
            "show_rationale":  True,
            "priority_badge_style": "pill",
        },
        "preview_props": {
            "test_name":  "Celiac Disease Antibody Panel",
            "order_code": "CELIAC",
            "priority":   "high",
            "rationale":  "First-line serological screen with >95% sensitivity for active celiac disease on a gluten-containing diet.",
            "turnaround": "1–3 days",
            "components": ["tTG IgA", "Endomysial Ab IgA", "Total IgA"],
            "special_instructions": "Patient must be on gluten-containing diet for accurate results.",
        },
        "conversation": [],
        "versions": [],
    },
    {
        "id": "table",
        "name": "Data Table",
        "type": "table",
        "variant": "zebra",
        "status": "system",
        "description": "Zebra-striped comparison table for test panels, reference ranges, and structured data.",
        "tags": ["table", "comparison", "data", "grid", "zebra"],
        "source_basis": {
            "based_on": "ARUP Design System v1 · FormattingAgent spec",
            "design_tokens_used": ["table", "colors", "typography", "spacing_rules"],
            "sample_assets_used": [],
            "token_refs": {
                "table":   "table.stripes, table.header, table.cell, table.linkStyle",
                "color":   "colors.background.salt (even rows) / colors.background.primary (odd)",
                "spacing": "table.cell.padding = 8px"
            }
        },
        "rules": {
            "stripe_even_bg":   "var(--bg-salt)",
            "stripe_odd_bg":    "var(--white)",
            "header_bg":        "var(--secondary)",
            "header_color":     "var(--white)",
            "header_font_weight": 600,
            "cell_padding":     "8px 12px",
            "border_color":     "var(--accent3)",
            "font_size":        13,
            "sticky_header":    True,
            "max_height_px":    400,
        },
        "preview_props": {
            "headers": ["Test", "Sensitivity", "Specificity", "Turnaround", "Order Code"],
            "rows": [
                ["tTG IgA",         "95%", "97%", "1–2 days", "TTGA"],
                ["Endomysial Ab IgA","85%", "99%", "3–5 days", "EMA"],
                ["Deamidated Gliadin IgG", "80%", "98%", "1–2 days", "DGLIGG"],
                ["Total IgA",       "N/A", "N/A", "1 day",    "IGA"],
            ],
            "caption": "Celiac serological tests comparison",
        },
        "conversation": [],
        "versions": [],
    },
    {
        "id": "algorithm_flow",
        "name": "Algorithm Flow",
        "type": "algorithm_flow",
        "variant": "vertical",
        "status": "system",
        "description": "Diagnostic decision-tree visualisation built from ARUP algorithm documents.",
        "tags": ["algorithm", "flowchart", "decision", "tree", "diagnostic"],
        "source_basis": {
            "based_on": "ARUP Design System v1 · FormattingAgent spec · AlgorithmRenderer v1.1",
            "design_tokens_used": ["colors", "spacing_rules", "card_rules"],
            "sample_assets_used": ["celiac_algorithm.json"],
            "token_refs": {
                "layout":  "layout_mode: vertical | tree | grouped_branch_layout | image_guided",
                "color":   "node type → color mapping via colors.brand / colors.semantic",
                "spacing": "spacing_rules.algorithmSpacing.*"
            }
        },
        "rules": {
            "node_styles": {
                "start":     {"bg": "var(--secondary)",  "color": "var(--white)",   "border_radius": "50vh"},
                "end":       {"bg": "var(--primary)",    "color": "var(--white)",   "border_radius": "50vh"},
                "test":      {"bg": "var(--lab-blue)",   "color": "var(--white)",   "border_radius": 8},
                "decision":  {"bg": "var(--white)",      "color": "var(--dark-sky)","border_radius": 6, "border": "2px solid var(--primary)"},
                "result":    {"bg": "var(--bg-salt)",    "color": "var(--dark-sky)","border_radius": 6},
            },
            "edge_color":         "var(--accent3)",
            "edge_label_font_size": 10,
            "layout_modes":       ["vertical", "tree", "grouped_branch_layout", "image_guided"],
            "default_layout":     "vertical",
            "node_min_width_px":  160,
            "canvas_padding_px":  24,
        },
        "preview_props": {
            "title": "Celiac Disease Diagnostic Algorithm",
            "layout_mode": "vertical",
            "nodes": [
                {"id": "n1", "label": "Clinical suspicion of celiac disease", "type": "start"},
                {"id": "n2", "label": "Measure Total IgA + tTG IgA", "type": "test"},
                {"id": "n3", "label": "IgA sufficient?", "type": "decision"},
                {"id": "n4", "label": "tTG IgA positive?", "type": "decision"},
                {"id": "n5", "label": "Measure Deamidated Gliadin IgG (DGP IgG)", "type": "test"},
                {"id": "n6", "label": "Confirm with Endomysial Ab IgA", "type": "test"},
                {"id": "n7", "label": "Refer for duodenal biopsy", "type": "result"},
                {"id": "n8", "label": "Celiac disease unlikely — consider alternatives", "type": "end"},
            ],
            "edges": [
                {"from": "n1", "to": "n2"},
                {"from": "n2", "to": "n3"},
                {"from": "n3", "to": "n4",  "label": "Yes"},
                {"from": "n3", "to": "n5",  "label": "No (IgA deficient)"},
                {"from": "n4", "to": "n6",  "label": "Positive"},
                {"from": "n4", "to": "n8",  "label": "Negative"},
                {"from": "n5", "to": "n6",  "label": "Positive"},
                {"from": "n5", "to": "n8",  "label": "Negative"},
                {"from": "n6", "to": "n7"},
            ],
        },
        "conversation": [],
        "versions": [],
    },
    {
        "id": "warning_block",
        "name": "Warning Block",
        "type": "warning_block",
        "variant": "default",
        "status": "system",
        "description": "Amber-accented callout for clinical conflicts, ordering cautions, and evidence gaps.",
        "tags": ["warning", "conflict", "caution", "alert", "amber"],
        "source_basis": {
            "based_on": "ARUP Design System v1 · FormattingAgent spec",
            "design_tokens_used": ["colors", "card_rules", "spacing_rules"],
            "sample_assets_used": [],
            "token_refs": {
                "card":    "card_rules.variants.semantic — left border + container bg",
                "icon":    "card_rules.iconPlacement.exception — icon above title in vertical layout",
                "spacing": "spacing_rules.semanticTokens.componentMarginTop.warning_block = 14"
            }
        },
        "rules": {
            "bg_token":          "var(--warning-bg, #FFF8E1)",
            "border_left_color": "var(--warning, #F59E0B)",
            "border_left_width": 4,
            "icon":              "⚠",
            "label_color":       "var(--warning, #F59E0B)",
            "text_color":        "var(--dark-sky)",
            "padding":           "12px 16px",
            "card_radius":       "var(--card-radius)",
            "font_size":         13,
        },
        "preview_props": {
            "title": "Ordering Conflict Detected",
            "items": [
                "Patient is on a gluten-free diet — tTG IgA sensitivity drops to <30%. Consider HLA-DQ2/DQ8 typing instead.",
                "Previous tTG IgA result (3 months ago): 142 U/mL. Repeat testing interval may be insufficient for clinical decision-making.",
            ],
        },
        "conversation": [],
        "versions": [],
    },
    {
        "id": "info_block",
        "name": "Info Block",
        "type": "info_block",
        "variant": "default",
        "status": "system",
        "description": "Blue-accented informational callout for clinical context, methodology notes, and reference data.",
        "tags": ["info", "context", "note", "methodology", "blue"],
        "source_basis": {
            "based_on": "ARUP Design System v1 · FormattingAgent spec",
            "design_tokens_used": ["colors", "card_rules", "typography"],
            "sample_assets_used": [],
            "token_refs": {
                "color":  "colors.neutral.labBlue for border/icon/label — links only",
                "card":   "card_rules.variants.semantic — 4px left border + info-container bg",
                "type":   "typography.scale.bodySm (14px) for item text"
            }
        },
        "rules": {
            "bg_token":          "var(--info-bg, #EEF4FB)",
            "border_left_color": "var(--lab-blue)",
            "border_left_width": 4,
            "icon":              "ℹ",
            "label_color":       "var(--lab-blue)",
            "text_color":        "var(--dark-sky)",
            "padding":           "12px 16px",
            "card_radius":       "var(--card-radius)",
            "font_size":         13,
        },
        "preview_props": {
            "title": "Clinical Context",
            "items": [
                "Celiac disease affects approximately 1% of the population; up to 83% may be undiagnosed.",
                "HLA-DQ2 and HLA-DQ8 haplotypes are present in >99% of celiac patients and are useful for exclusion.",
                "Gluten challenge (3g/day × 2 weeks) may be required before serological testing in patients on a gluten-free diet.",
            ],
        },
        "conversation": [],
        "versions": [],
    },
    {
        "id": "citation_table",
        "name": "Citation Table",
        "type": "citation_table",
        "variant": "compact",
        "status": "system",
        "description": "Compact source attribution table listing ARUP documents, fact sheets, and consult topics referenced in the response.",
        "tags": ["citations", "sources", "references", "attribution", "evidence"],
        "source_basis": {
            "based_on": "ARUP Design System v1 · FormattingAgent spec",
            "design_tokens_used": ["table", "colors", "typography"],
            "sample_assets_used": [],
            "token_refs": {
                "link":   "colors.neutral.labBlue — links only, never decorative",
                "type":   "typography.scale.caption (12px) for citations",
                "table":  "table.stripes applied; table.statusBadges for doc-type chips"
            }
        },
        "rules": {
            "header_bg":       "var(--bg-salt)",
            "header_color":    "var(--accent)",
            "header_font_size": 11,
            "cell_font_size":   12,
            "link_color":      "var(--lab-blue)",
            "border_color":    "var(--accent3)",
            "row_padding":     "6px 10px",
            "show_doc_type_badge": True,
            "max_rows_before_collapse": 5,
        },
        "preview_props": {
            "citations": [
                {"doc_type": "Fact Sheet",     "title": "Celiac Disease Antibody Testing",  "source": "ARUP Laboratories", "url": "https://ltd.aruplab.com"},
                {"doc_type": "Consult Topic",  "title": "Celiac Disease Serologic Workup",  "source": "ARUP Consult",      "url": "https://ltd.aruplab.com"},
                {"doc_type": "Algorithm",      "title": "Celiac Disease Diagnostic Algorithm","source": "ARUP Algorithms",  "url": "https://ltd.aruplab.com"},
                {"doc_type": "Test Directory", "title": "tTG IgA (TTGA) Test Details",      "source": "ARUP Test Dir",     "url": "https://ltd.aruplab.com"},
            ],
        },
        "conversation": [],
        "versions": [],
    },
]


# ── Store class ───────────────────────────────────────────────────────────────

class DesignLibraryStore:
    """
    Thread-safe (single-process) JSON-backed store for design system components.
    Writes are synchronous; the file is re-read on every mutation to ensure
    any external edits are picked up.
    """

    def __init__(self, path: Path = _STORE_FILE):
        self._path = path
        self._ensure_seeded()

    # ── Internal I/O ──────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if not self._path.exists():
            return {"components": {}}
        with open(self._path, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: dict) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _ensure_seeded(self) -> None:
        """Write seed components if the store file does not yet exist."""
        if self._path.exists():
            return
        data: dict = {"components": {}}
        for comp in SEED_COMPONENTS:
            comp = dict(comp)  # shallow copy
            # Stamp initial version
            comp["versions"] = [
                _make_version(comp.get("rules", {}), comp.get("preview_props", {}), "Initial system component")
            ]
            data["components"][comp["id"]] = comp
        self._save(data)

    # ── Public API ────────────────────────────────────────────────────────────

    def list_components(self) -> list[dict]:
        """Return all components as a list (summary, no conversation history)."""
        data = self._load()
        out = []
        for comp in data["components"].values():
            out.append({k: v for k, v in comp.items() if k not in ("conversation", "versions")})
        return out

    def get_component(self, component_id: str) -> Optional[dict]:
        """Return full component record including conversation and versions."""
        data = self._load()
        return data["components"].get(component_id)

    def create_component(self, spec: dict) -> dict:
        """Create a new custom component and persist it."""
        data = self._load()
        cid = spec.get("id") or spec.get("type") or str(uuid.uuid4())[:8]
        if cid in data["components"]:
            cid = f"{cid}_{str(uuid.uuid4())[:6]}"
        spec["id"]          = cid
        spec["status"]      = spec.get("status", "draft")
        spec["conversation"] = spec.get("conversation", [])
        spec["versions"]    = [
            _make_version(spec.get("rules", {}), spec.get("preview_props", {}), "Created")
        ]
        spec["created_at"]  = _now()
        data["components"][cid] = spec
        self._save(data)
        return spec

    def update_component(self, component_id: str, updates: dict, version_summary: str = "Updated") -> Optional[dict]:
        """Apply partial updates to a component and snapshot the version."""
        data = self._load()
        comp = data["components"].get(component_id)
        if comp is None:
            return None
        # Never overwrite id, status for system components
        if comp.get("status") == "system" and "status" in updates:
            updates.pop("status")
        comp.update(updates)
        comp["updated_at"] = _now()
        # Snapshot version
        comp.setdefault("versions", []).append(
            _make_version(comp.get("rules", {}), comp.get("preview_props", {}), version_summary)
        )
        data["components"][component_id] = comp
        self._save(data)
        return comp

    def add_conversation_entry(self, component_id: str, role: str, content: str) -> Optional[dict]:
        """Append a chat entry (role: 'user'|'assistant') to the component's feedback thread."""
        data = self._load()
        comp = data["components"].get(component_id)
        if comp is None:
            return None
        comp.setdefault("conversation", []).append({
            "role":      role,
            "content":   content,
            "timestamp": _now(),
        })
        data["components"][component_id] = comp
        self._save(data)
        return comp

    def approve_component(self, component_id: str) -> Optional[dict]:
        """Promote a draft component to approved status."""
        return self.update_component(
            component_id,
            {"status": "approved"},
            version_summary="Approved by designer",
        )

    def reopen_component(self, component_id: str) -> Optional[dict]:
        """Reopen an approved component for editing (status -> draft)."""
        return self.update_component(
            component_id,
            {"status": "draft"},
            version_summary="Reopened for editing",
        )

    def get_history(self, component_id: str) -> list[dict]:
        """Return the version history for a component."""
        data = self._load()
        comp = data["components"].get(component_id)
        return comp.get("versions", []) if comp else []

    def delete_component(self, component_id: str) -> bool:
        """Delete a non-system component. Returns True if deleted."""
        data = self._load()
        comp = data["components"].get(component_id)
        if comp is None:
            return False
        if comp.get("status") == "system":
            return False  # Cannot delete system components
        del data["components"][component_id]
        self._save(data)
        return True