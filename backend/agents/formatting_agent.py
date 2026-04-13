"""
formatting_agent.py  v1.1.0
────────────────────────────────────────────────────────────────────────────
Formatting Agent — Design System UI Schema Generator

Converts structured clinical JSON (from ResponseAgent + AlgorithmRenderer)
into a deterministic UI component schema consumed by the frontend renderer.

v1.1.0 additions:
  ▸ PREFERENCES  — loads formatting_rules.json on startup; applies designer
                    overrides to component props, spacing, and composition rules
  ▸ PREFERENCE API — load_preferences() / save_preferences() / apply_preferences()
  ▸ RUNTIME RELOAD — reload() method lets main.py refresh rules after save
  ▸ BACKWARD-COMPAT — all v1.0.0 behaviour preserved when no rules file present

Design principles:
  ▸ DETERMINISTIC  — zero LLM calls; pure rule-based composition
  ▸ STATELESS      — takes inputs, returns schema, no side effects
  ▸ CLINICAL-SAFE  — preserves all clinical content; only decides presentation
  ▸ ADDITIVE       — produces render hints that extend existing frontend logic
  ▸ TOKEN-ONLY     — all style values reference CSS variables, never raw hex

Component types (from design system):
  text_block          — narrative answer, info paragraphs, disclaimer text
  recommendation_card — single test recommendation with rank/specimen/TAT
  table               — multi-test comparison (≥3 recommendations)
  algorithm_flow      — clinical pathway flowchart
  badge_group         — evidence coverage signals
  warning_block       — conflict alerts
  info_block          — evidence gaps, follow-up prompts
  citation_table      — grounded sources list

Output schema (added to /chat response as "ui_schema"):
{
  "layout":          "card_stack" | "mixed_layout",
  "component_count": int,
  "render_hints":    { ... },
  "preferences_applied": bool,
  "components": [
    { "type": str, "variant": str, "props": {...}, "priority": int }
  ]
}
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Design-system token catalogue ─────────────────────────────────────────────
TOKEN = {
    "primary":            "var(--primary)",
    "secondary":          "var(--secondary)",
    "accent":             "var(--accent)",
    "accent2":            "var(--accent2)",
    "accent3":            "var(--accent3)",
    "dark_sky":           "var(--dark-sky)",
    "lab_blue":           "var(--lab-blue)",
    "positive":           "var(--positive)",
    "positive_container": "var(--positive-container)",
    "danger":             "var(--danger)",
    "danger_container":   "var(--danger-container)",
    "info_container":     "var(--info-container)",
    "bg_salt":            "var(--bg-salt)",
    "bg_primary":         "var(--bg-primary)",
    "white":              "var(--white)",
    "card_radius":        "var(--card-radius)",
    "card_padding":       "var(--card-padding)",
    "card_border":        "var(--card-border)",
    "btn_radius":         "var(--btn-radius)",
}

# Badge class mappings from SOURCE_CFG in MessageBubble.jsx
SOURCE_BADGE_CFG = {
    "Algorithm":      {"cls": "badge badge-source-algo",    "label": "Algorithm"},
    "Consult Topic":  {"cls": "badge badge-source-consult", "label": "Consult Topic"},
    "Fact Sheet":     {"cls": "badge badge-source-fact",    "label": "Fact Sheet"},
    "Test Directory": {"cls": "badge badge-source-dir",     "label": "Directory"},
    "General":        {"cls": "badge badge-source-general", "label": "General"},
}

RANK_BADGE_CFG = {
    "primary":   {"cls": "badge badge-danger",   "label": "Primary",   "accent": TOKEN["primary"]},
    "secondary": {"cls": "badge badge-info",     "label": "Secondary", "accent": TOKEN["accent"]},
    "reflex":    {"cls": "badge badge-positive", "label": "Reflex",    "accent": TOKEN["positive"]},
}

TYPOGRAPHY = {
    "display":  "text-display",
    "h1":       "text-h1",
    "h2":       "text-h2",
    "h3":       "text-h3",
    "h4":       "text-h4",
    "body_lg":  "text-body-lg",
    "body":     "text-body",
    "body_sm":  "text-body-sm",
    "caption":  "text-caption",
    "label":    "text-label",
}

# Default composition order — recommendations before algorithm.
# The clinical product is a test-selection assistant; the algorithm is supporting evidence.
DEFAULT_COMPOSITION_ORDER = [
    "text_block",
    "recommendation_card",
    "table",
    "algorithm_flow",
    "badge_group",
    "warning_block",
    "info_block",
    "citation_table",
]

# Default spacing (can be overridden by formatting_rules.json)
DEFAULT_SPACING = {
    "card_gap":    12,
    "section_gap": 20,
    "component_margin_top": {
        "text_block":          0,
        "recommendation_card": 12,
        "table":               20,
        "algorithm_flow":      16,
        "badge_group":          8,
        "warning_block":       14,
        "info_block":          10,
        "citation_table":      20,
    },
}

# Default table rules
DEFAULT_TABLE_RULES = {
    "min_rows_for_table":       3,
    "require_specimen_and_tat": False,
    "populated_threshold_ratio": 0.5,
}

# Default confidence thresholds
DEFAULT_CONFIDENCE = {"high": 70, "moderate": 40}


# ── Preferences file location ──────────────────────────────────────────────────
# Resolved relative to this module file so it works regardless of CWD.
_RULES_FILE   = Path(__file__).parent / "formatting_rules.json"

# ── Design-token directory ─────────────────────────────────────────────────────
# Six JSON token files live here and are loaded once at startup.
# The directory is beside this module:  agents/design_tokens/
_TOKENS_DIR   = Path(__file__).parent / "design_tokens"

# Token file names (without .json) — loaded into a flat dict keyed by name.
_TOKEN_FILES  = ["typography", "buttons", "colors", "table", "card_rules", "spacing_rules"]


def load_design_tokens(tokens_dir: Path | None = None) -> dict:
    """
    Load all design-system token JSON files from *tokens_dir*.

    Returns a dict keyed by token file name (e.g. "typography", "buttons").
    Files that are missing or malformed are skipped with a warning — the
    agent degrades gracefully to its hard-coded TOKEN / TYPOGRAPHY constants.
    """
    target = tokens_dir or _TOKENS_DIR
    result: dict = {}
    for name in _TOKEN_FILES:
        fpath = target / f"{name}.json"
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                result[name] = json.load(f)
            logger.debug("FormattingAgent: token file loaded — %s", fpath.name)
        except FileNotFoundError:
            logger.info("FormattingAgent: design token file not found — %s (using built-in defaults)", fpath.name)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("FormattingAgent: failed to parse %s — %s", fpath.name, exc)
    return result


# ── Preferences helpers ────────────────────────────────────────────────────────

def load_preferences(path: Path | None = None) -> dict:
    """
    Load formatting_rules.json from disk.  Returns {} if file not found or
    malformed (so the FormattingAgent degrades gracefully to defaults).
    """
    target = path or _RULES_FILE
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info("FormattingAgent: preferences loaded from %s", target)
        return data
    except FileNotFoundError:
        logger.info("FormattingAgent: no formatting_rules.json found — using defaults")
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("FormattingAgent: failed to parse formatting_rules.json: %s", e)
        return {}


def save_preferences(prefs: dict, path: Path | None = None) -> None:
    """
    Persist updated preferences to formatting_rules.json, stamping metadata.
    Raises OSError / PermissionError on write failure (caller handles).
    """
    target = path or _RULES_FILE
    prefs.setdefault("_meta", {})
    prefs["_meta"]["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(target, "w", encoding="utf-8") as f:
        json.dump(prefs, f, indent=2, ensure_ascii=False)
    logger.info("FormattingAgent: preferences saved to %s", target)


# ── Helper: confidence tier ────────────────────────────────────────────────────

def _confidence_tier(score: int | None, thresholds: dict | None = None) -> str:
    t = thresholds or DEFAULT_CONFIDENCE
    if score is None:
        return "low"
    if score >= t.get("high", 70):
        return "high"
    if score >= t.get("moderate", 40):
        return "moderate"
    return "low"


# ── Helper: evidence coverage badges ──────────────────────────────────────────

def _coverage_badges(coverage: dict) -> list[dict]:
    """Convert has_algorithm / has_consult / etc. into badge specs."""
    mapping = [
        ("has_algorithm",   "Algorithm",      SOURCE_BADGE_CFG["Algorithm"]),
        ("has_consult",     "Consult Topic",  SOURCE_BADGE_CFG["Consult Topic"]),
        ("has_fact_sheet",  "Fact Sheet",     SOURCE_BADGE_CFG["Fact Sheet"]),
        ("has_directory",   "Test Directory", SOURCE_BADGE_CFG["Test Directory"]),
    ]
    return [
        {"label": label, "cls": cfg["cls"]}
        for key, label, cfg in mapping
        if coverage.get(key)
    ]


# ── Helper: filter clinical gaps ──────────────────────────────────────────────

def _clinical_gaps(gaps: list[str]) -> list[str]:
    """Return only gaps that are meaningful to a clinician (not [Validation] entries)."""
    return [g for g in gaps if not g.startswith("[Validation]")]


# ── Helper: table-suitability check ───────────────────────────────────────────

def _should_use_table(recommendations: list[dict], rules: dict | None = None) -> bool:
    r = rules or DEFAULT_TABLE_RULES
    min_rows = r.get("min_rows_for_table", 3)
    threshold = r.get("populated_threshold_ratio", 0.5)
    require_both = r.get("require_specimen_and_tat", False)

    if len(recommendations) < min_rows:
        return False

    if require_both:
        populated = sum(
            1 for rec in recommendations
            if rec.get("specimen") and rec.get("tat")
        )
    else:
        # Either field is sufficient
        populated = sum(
            1 for rec in recommendations
            if rec.get("specimen") or rec.get("tat")
        )

    return populated >= len(recommendations) * threshold


# ── Component builders ─────────────────────────────────────────────────────────

def _build_text_block(response: dict, priority: int, overrides: dict | None = None) -> dict:
    """Primary answer narrative — applies designer overrides if present."""
    ov = (overrides or {}).get("answer_narrative", {})
    return {
        "type":    "text_block",
        "variant": "answer_narrative",
        "props": {
            "content":    response.get("answer", ""),
            "typography": ov.get("font_size_class", TYPOGRAPHY["body"]),
            "color":      ov.get("color_token",    TOKEN["dark_sky"]),
            "line_height": ov.get("line_height",   1.6),
        },
        "priority": priority,
    }


def _build_algorithm_flow(viz: dict, priority: int, overrides: dict | None = None) -> dict:
    """Clinical pathway flowchart component — passes all v4 renderer fields through."""
    ov    = overrides or {}
    stats = viz.get("stats", {})
    return {
        "type":    "algorithm_flow",
        "variant": "clinical_pathway",
        "props": {
            "graph": {
                # Core identity
                "title":                   viz.get("title", "Clinical Algorithm"),
                "source_file":             viz.get("source_file", ""),
                "source_url":              viz.get("source_url", ""),
                "source_page_url":         viz.get("source_page_url", ""),
                "pdf_url":                 viz.get("pdf_url", ""),
                "pdf_asset_id":            viz.get("pdf_asset_id", ""),
                "pdf_origin":              viz.get("pdf_origin", "none"),
                "has_local_pdf":           viz.get("has_local_pdf", False),
                "source_asset_type":       viz.get("source_asset_type", "unknown"),
                "reviewed_date":           viz.get("reviewed_date", ""),
                "updated_date":            viz.get("updated_date", ""),
                # Layout
                "render_mode":             viz.get("render_mode", "clinical_linear_document"),
                "layout":                  viz.get("layout",      "clinical_linear_document"),
                "layout_mode":             viz.get("layout_mode", "clinical_linear_document"),
                "is_multi_zone":           viz.get("is_multi_zone", False),
                "fork_style":              viz.get("fork_style", "none"),
                "entry_type":              viz.get("entry_type", "none"),
                "compact_branch_layout":   viz.get("compact_branch_layout", False),
                "should_center_spine":     viz.get("should_center_spine", True),
                # Routing / entry
                "routing_label":           viz.get("routing_label", ""),
                "entry_section_node_ids":  viz.get("entry_section_node_ids", []),
                "spine_node_ids":          viz.get("spine_node_ids", []),
                # Graph data
                "nodes":                   viz.get("nodes", []),
                "edges":                   viz.get("edges", []),
                "groups":                  viz.get("groups", []),
                "branch_splits":           viz.get("branch_splits", []),
                "referenced_tests":        viz.get("referenced_tests", []),
                "footer_blocks":           viz.get("footer_blocks", []),
                "stats":                   stats,
            },
            "defaultExpanded":        ov.get("default_expanded",      True),
            "showLegend":             ov.get("show_legend",            True),
            "maxHeightPx":            ov.get("max_height_px",          700),
            "showReferencedTests":    ov.get("show_referenced_tests",  True)
                                      and len(viz.get("referenced_tests", [])) > 0,
            "showSourceTabs":         ov.get("show_source_tabs",       True),
            # ── Split-view hints (v2.1.0) ──────────────────────────────────
            # defaultViewMode: 'split' when a local PDF is attached; 'rendered' otherwise.
            # The frontend (AlgorithmFlowchart) reads these to set its initial view mode
            # and to decide whether to show the Split tab.  All values are designer-
            # overridable via formatting_rules.json component overrides.
            "defaultViewMode":        ov.get("default_view_mode",
                                             "split" if viz.get("has_local_pdf") else "rendered"),
            "showSplitView":          ov.get("show_split_view",         True),
            "preferSplitWhenLocalPdf": ov.get("prefer_split_when_local_pdf", True),
        },
        "priority": priority,
    }


def _build_recommendation_card(
    rec: dict, index: int, priority: int, overrides: dict | None = None,
    algo_test_refs: set | None = None,
) -> dict:
    """Single test recommendation card — applies designer overrides.
    algo_test_refs: set of test codes that appear in the algorithm, used to
    show an 'Appears in algorithm' badge on the card.
    """
    ov      = overrides or {}
    rank    = rec.get("rank", "secondary")
    rank_cfg = RANK_BADGE_CFG.get(rank, RANK_BADGE_CFG["secondary"])
    coverage = rec.get("evidence_coverage", {})
    badges   = _coverage_badges(coverage) if ov.get("show_evidence_badges", True) else []
    source_refs = rec.get("source_refs", [])
    test_code   = rec.get("test_code", "")

    # Cross-reference with algorithm
    appears_in_algo = bool(algo_test_refs and test_code and test_code in algo_test_refs)

    return {
        "type":    "recommendation_card",
        "variant": rank,
        "props": {
            "index":               index,
            "test_name":           rec.get("test_name", ""),
            "test_code":           test_code,
            "rank":                rank,
            "rank_badge":          rank_cfg,
            "rationale":           rec.get("rationale", ""),
            "specimen":            rec.get("specimen", "") if ov.get("show_specimen_tat", True) else "",
            "tat":                 rec.get("tat", "")      if ov.get("show_specimen_tat", True) else "",
            "source_refs":         source_refs,
            "source_count":        len(source_refs),
            "coverage_badges":     badges,
            "accent_color":        rank_cfg["accent"],
            "border_width":        ov.get("accent_border_width", 4),
            "border_token":        TOKEN["accent3"],
            "bg_token":            TOKEN["bg_primary"],
            "appears_in_algorithm": appears_in_algo,
            "test_url":            rec.get("test_url", ""),
        },
        "priority": priority,
    }


def _build_recommendations_table(
    recommendations: list[dict], priority: int, overrides: dict | None = None,
    algorithm_viz: dict | None = None,
) -> dict:
    """
    Multi-test comparison table.
    Columns: Rank | Test Name | Code | Specimen | TAT | Evidence
    Applies designer overrides for pagination, zebra-stripe, expand.
    """
    ov = overrides or {}

    columns = [
        {"key": "rank",     "label": "Rank",     "width": 90},
        {"key": "test_name","label": "Test",     "width": None},
        {"key": "test_code","label": "Code",     "width": 80},
        {"key": "specimen", "label": "Specimen", "width": 120},
        {"key": "tat",      "label": "TAT",      "width": 80},
    ]
    if ov.get("show_evidence_column", True):
        columns.append({"key": "evidence", "label": "Evidence", "width": 100})

    paginate_at = ov.get("paginate_at", 8)
    algo_test_refs = {t.get("test_code","") for t in (algorithm_viz or {}).get("referenced_tests", [])} if algorithm_viz else set()
    rows = []
    for i, rec in enumerate(recommendations):
        rank     = rec.get("rank", "secondary")
        rank_cfg = RANK_BADGE_CFG.get(rank, RANK_BADGE_CFG["secondary"])
        coverage = rec.get("evidence_coverage", {})
        code     = rec.get("test_code", "")
        rows.append({
            "index":               i,
            "rank":                rank,
            "rank_badge":          rank_cfg,
            "test_name":           rec.get("test_name", ""),
            "test_code":           code,
            "test_url":            rec.get("test_url", ""),
            "specimen":            rec.get("specimen", "") or "—",
            "tat":                 rec.get("tat", "") or "—",
            "evidence":            _coverage_badges(coverage) if ov.get("show_evidence_column", True) else [],
            "source_refs":         rec.get("source_refs", []),
            "rationale":           rec.get("rationale", ""),
            "appears_in_algorithm": bool(algo_test_refs and code and code in algo_test_refs),
            "is_even":             i % 2 != 0,
        })

    return {
        "type":    "table",
        "variant": "recommendations_comparison",
        "props": {
            "columns":             columns,
            "rows":                rows,
            "row_count":           len(rows),
            "paginate":            len(rows) > paginate_at,
            "page_size":           paginate_at,
            "zebra_stripe":        ov.get("zebra_stripe", True),
            "show_expand_rationale": ov.get("show_expand_rationale", True),
            "header_bg":           TOKEN["bg_primary"],
            "even_row_bg":         TOKEN["bg_salt"],
            "odd_row_bg":          TOKEN["bg_primary"],
            "border_color":        TOKEN["accent3"],
            "header_text_cls":     TYPOGRAPHY["label"],
            "cell_text_cls":       TYPOGRAPHY["body_sm"],
            "table_css_class":     "arup-table",
        },
        "priority": priority,
    }


def _build_warning_block(conflicts: list[str], priority: int, overrides: dict | None = None) -> dict:
    """Source conflict warning component — applies designer colour overrides."""
    ov = overrides or {}
    return {
        "type":    "warning_block",
        "variant": "source_conflicts",
        "props": {
            "title":        "Source conflicts detected",
            "icon":         ov.get("icon",         "⚠"),
            "items":        conflicts,
            "bg_color":     ov.get("bg_color",     "#FFF8F0"),
            "border_color": ov.get("border_color", "#F5A623"),
            "text_color":   ov.get("text_color",   "#8B5E00"),
            "title_cls":    TYPOGRAPHY["caption"],
        },
        "priority": priority,
    }


def _build_info_block(gaps: list[str], priority: int, overrides: dict | None = None) -> dict:
    """Evidence gaps informational component — applies designer overrides."""
    ov = overrides or {}
    return {
        "type":    "info_block",
        "variant": "evidence_gaps",
        "props": {
            "title":        "Evidence gaps",
            "icon":         ov.get("icon",         "ℹ"),
            "items":        gaps,
            "bg_token":     ov.get("bg_token",     TOKEN["bg_salt"]),
            "border_token": ov.get("border_token", TOKEN["accent3"]),
            "text_color":   ov.get("text_token",   TOKEN["accent2"]),
            "title_cls":    TYPOGRAPHY["caption"],
        },
        "priority": priority,
    }


def _build_citation_table(
    citations: list[dict], priority: int, overrides: dict | None = None
) -> dict:
    """Collapsible citations table — applies designer overrides."""
    ov = overrides or {}
    rows = []
    for i, cit in enumerate(citations):
        src_type = cit.get("source_type", "General")
        src_cfg  = SOURCE_BADGE_CFG.get(src_type, SOURCE_BADGE_CFG["General"])
        row = {
            "number":      cit.get("number"),
            "source_type": src_type,
            "badge_cls":   src_cfg["cls"],
            "badge_label": src_cfg["label"],
            "is_even":     i % 2 != 0,
        }
        if ov.get("show_source_role", True):
            row["source_role"] = cit.get("source_role", "")
        if ov.get("show_excerpt", True):
            row["excerpt"] = cit.get("excerpt", "")
        row["document"] = cit.get("document", "")
        rows.append(row)

    columns = ["#", "Source type", "Document"]
    if ov.get("show_excerpt", True):
        columns.append("Excerpt")

    return {
        "type":    "citation_table",
        "variant": "supporting_sources",
        "props": {
            "title":           f"Supporting sources ({len(citations)}) — ARUP authoritative",
            "rows":            rows,
            "default_open":    ov.get("default_open", True),
            "columns":         columns,
            "table_css_class": "arup-table",
            "even_row_bg":     TOKEN["bg_salt"],
            "odd_row_bg":      TOKEN["bg_primary"],
        },
        "priority": priority,
    }


# ── Main FormattingAgent class ─────────────────────────────────────────────────

class FormattingAgent:
    """
    Deterministic UI schema generator with designer preferences support.

    Usage:
        agent = FormattingAgent()          # auto-loads formatting_rules.json
        schema = agent.format(
            response=response_agent_output,
            algorithm_viz=algorithm_renderer_output,   # or None
            confidence=confidence_agent_output,
            intent=prompt_agent_output,
        )

        # After saving new preferences via /designer/preferences API:
        agent.reload()                     # hot-reload rules without restart
    """

    MAX_COMPONENTS = 6

    def __init__(self, rules_path: Path | None = None, tokens_dir: Path | None = None):
        self._rules_path  = rules_path  or _RULES_FILE
        self._tokens_dir  = tokens_dir  or _TOKENS_DIR
        self._prefs: dict = {}
        self._preferences_applied: bool = False
        self._design_tokens: dict = {}
        self._load()

    # ── Public API ─────────────────────────────────────────────────────────────

    def reload(self) -> None:
        """Hot-reload preferences and design tokens from disk."""
        self._load()

    def get_preferences(self) -> dict:
        """Return current preferences dict (for GET /designer/preferences)."""
        return dict(self._prefs)

    def get_design_tokens(self) -> dict:
        """
        Return all loaded design-system token files.
        Used by GET /designer/tokens to expose the full token set to the
        Designer Panel for rules display and component evolution reference.
        """
        return dict(self._design_tokens)

    def set_preferences(self, prefs: dict) -> None:
        """
        Persist new preferences and reload in-memory state.
        Called by POST /designer/preferences.
        """
        save_preferences(prefs, self._rules_path)
        self._prefs = prefs
        self._apply_prefs()

    def get_available_components(self) -> list[dict]:
        """
        Return the component catalogue — used by the Designer Panel
        to display available component types and their variants.
        """
        return [
            {
                "type": "text_block",
                "variants": ["answer_narrative", "fallback_card"],
                "description": "Narrative answer paragraph. Always first.",
                "overridable": list(
                    (self._prefs.get("component_overrides", {})
                     .get("text_block", {})
                     .get("answer_narrative", {})
                     .keys())
                ),
            },
            {
                "type": "recommendation_card",
                "variants": ["primary", "secondary", "reflex"],
                "description": "Single test recommendation with rank, rationale, specimen, TAT.",
                "overridable": ["show_evidence_badges", "show_specimen_tat", "accent_border_width"],
            },
            {
                "type": "table",
                "variants": ["recommendations_comparison"],
                "description": "Multi-test comparison table (≥3 recommendations).",
                "overridable": ["show_expand_rationale", "paginate_at", "zebra_stripe", "show_evidence_column"],
            },
            {
                "type": "algorithm_flow",
                "variants": ["clinical_pathway"],
                "description": "Clinical pathway flowchart from algorithm JSON.",
                "overridable": ["default_expanded", "show_legend", "max_height_px", "show_referenced_tests"],
            },
            {
                "type": "warning_block",
                "variants": ["source_conflicts"],
                "description": "Conflict alert for differing source recommendations.",
                "overridable": ["icon", "bg_color", "border_color", "text_color"],
            },
            {
                "type": "info_block",
                "variants": ["evidence_gaps"],
                "description": "Evidence gap informational block.",
                "overridable": ["icon", "bg_token", "border_token", "text_token"],
            },
            {
                "type": "citation_table",
                "variants": ["supporting_sources"],
                "description": "Collapsible grounded source citations.",
                "overridable": ["default_open", "show_excerpt", "show_source_role"],
            },
            {
                "type": "badge_group",
                "variants": ["evidence_coverage"],
                "description": "Pill row showing which ARUP source types covered a response.",
                "overridable": [],
            },
        ]

    # ── format() — main entry point ───────────────────────────────────────────

    def format(
        self,
        response: dict,
        algorithm_viz: dict | None,
        confidence: dict,
        intent: dict,
        evidence_context: dict | None = None,
    ) -> dict:
        """
        Convert pipeline outputs into a structured UI component schema,
        applying any active designer preferences from formatting_rules.json.

        Parameters
        ----------
        response          : validated ResponseAgent output dict
        algorithm_viz     : AlgorithmRenderer output (the viz dict inside
                            "algorithm_visualization"), or None
        confidence        : ConfidenceAgent output dict
        intent            : PromptAgent intent dict
        evidence_context  : optional — dict with keys:
                              algorithm_sources  : list  (from EvidencePackager)
                              algorithm_debug    : dict  (from AlgorithmRenderer._debug)
                            Used to show a diagnostic info_block when algorithm
                            chunks were retrieved but visualization failed.

        Returns
        -------
        ui_schema dict
        """
        components: list[dict] = []
        priority = 1

        recommendations = response.get("recommendations", [])
        citations       = response.get("citations", [])
        conflicts       = response.get("conflicts_surfaced", []) or []
        raw_gaps        = response.get("evidence_gaps", []) or []
        clinical_gaps   = _clinical_gaps(raw_gaps)

        conf_score = (confidence or {}).get("score")
        tier       = _confidence_tier(conf_score, self._conf_thresholds)
        intent_type = (intent or {}).get("intent_type", "unknown")

        has_algo      = algorithm_viz is not None and bool(algorithm_viz.get("nodes"))
        has_recs      = len(recommendations) > 0
        use_table     = _should_use_table(recommendations, self._table_rules)
        has_conflicts = len(conflicts) > 0
        has_gaps      = len(clinical_gaps) > 0
        has_citations = len(citations) > 0

        # Algorithm retrieval diagnostic — did we get algorithm chunks but fail to viz?
        ev_ctx            = evidence_context or {}
        algo_sources      = ev_ctx.get("algorithm_sources", [])
        algo_was_retrieved = bool(algo_sources)
        algo_viz_failed   = algo_was_retrieved and not has_algo

        # ── Resolve component overrides ───────────────────────────────────────
        comp_ov = self._prefs.get("component_overrides", {})

        # ── Determine layout ──────────────────────────────────────────────────
        layout = "mixed_layout" if has_algo else "card_stack"

        # ── Algorithm position hint ───────────────────────────────────────────
        # "after_recommendations" = standard — recs shown first (test-selection priority)
        # "after_answer"          = no recs, algorithm follows answer text directly
        algorithm_position = "after_recommendations" if has_recs else "after_answer"

        # ── Render hints ──────────────────────────────────────────────────────
        render_hints = {
            "show_algorithm_first":          False,  # always false — recs come first
            "algorithm_position":            algorithm_position,
            "use_table_for_recommendations": use_table,
            "has_conflicts":                 has_conflicts,
            "has_gaps":                      has_gaps,
            "confidence_tier":               tier,
            "primary_intent":                intent_type,
            "has_local_pdf":                 (algorithm_viz or {}).get("has_local_pdf", False),
            "pdf_origin":                    (algorithm_viz or {}).get("pdf_origin", "none"),
            # Algorithm diagnostic hints (safe to expose — non-clinical metadata)
            "has_algorithm_viz":             has_algo,
            "algorithm_retrieved":           algo_was_retrieved,
            "algorithm_viz_failed":          algo_viz_failed,
        }

        # ── Component composition: recommendation-first clinical priority ──────
        # Order: answer → recs/table → algorithm → warnings → gaps → citations
        max_c = self._prefs.get("max_components", self.MAX_COMPONENTS)

        # 1. Answer narrative (always first)
        components.append(_build_text_block(
            response,
            priority=priority,
            overrides=comp_ov.get("text_block", {}).get("answer_narrative"),
        ))
        priority += 1

        # 2. Recommendations — table or individual cards (before algorithm)
        if has_recs and len(components) < max_c:
            if use_table:
                components.append(_build_recommendations_table(
                    recommendations, priority=priority,
                    overrides=comp_ov.get("table"),
                    algorithm_viz=algorithm_viz if has_algo else None,
                ))
                priority += 1
            else:
                algo_test_refs = {t.get("test_code","") for t in (algorithm_viz or {}).get("referenced_tests", [])} if has_algo else set()
                for i, rec in enumerate(recommendations):
                    if len(components) >= max_c:
                        break
                    components.append(_build_recommendation_card(
                        rec, index=i, priority=priority,
                        overrides=comp_ov.get("recommendation_card"),
                        algo_test_refs=algo_test_refs,
                    ))
                    priority += 1

        # 3. Algorithm flow (after recommendations)
        if has_algo and len(components) < max_c:
            components.append(_build_algorithm_flow(
                algorithm_viz,
                priority=priority,
                overrides=comp_ov.get("algorithm_flow"),
            ))
            priority += 1

        # 3b. Diagnostic info_block: algorithm chunks retrieved but viz unavailable
        # Surfaces in the UI so the failure is visible rather than silent.
        # Only shown in dev/debug — suppress in production by setting
        # "hide_algorithm_debug_block": true in formatting_rules.json.
        if algo_viz_failed and len(components) < max_c:
            hide_debug = self._prefs.get("hide_algorithm_debug_block", False)
            if not hide_debug:
                fnames = [s.get("filename", "?") for s in algo_sources[:3]]
                components.append(_build_info_block(
                    [
                        f"Algorithm source retrieved ({len(algo_sources)} file(s): "
                        f"{', '.join(fnames)}) but visualization could not be built. "
                        "Check that the algorithm graph was ingested correctly and the "
                        "source file type is 'Algorithm'."
                    ],
                    priority=priority,
                    overrides=comp_ov.get("info_block"),
                ))
                priority += 1

        # 4. Warning block
        if has_conflicts and len(components) < max_c:
            components.append(_build_warning_block(
                conflicts, priority=priority,
                overrides=comp_ov.get("warning_block"),
            ))
            priority += 1

        # 5. Info block (evidence gaps)
        if has_gaps and len(components) < max_c:
            components.append(_build_info_block(
                clinical_gaps, priority=priority,
                overrides=comp_ov.get("info_block"),
            ))
            priority += 1

        # 6. Citations table
        if has_citations and len(components) < max_c:
            components.append(_build_citation_table(
                citations, priority=priority,
                overrides=comp_ov.get("citation_table"),
            ))
            priority += 1

        # ── Fallback ──────────────────────────────────────────────────────────
        if not components:
            components.append({
                "type":    "text_block",
                "variant": "fallback_card",
                "props": {
                    "content":    response.get("answer", "No response generated."),
                    "typography": TYPOGRAPHY["body"],
                    "color":      TOKEN["dark_sky"],
                    "card_cls":   "card",
                    "padding":    TOKEN["card_padding"],
                },
                "priority": 1,
            })

        schema: dict = {
            "layout":               layout,
            "component_count":      len(components),
            "render_hints":         render_hints,
            "preferences_applied":  self._preferences_applied,
            "components":           components,
        }

        # Attach renderer debug info when available (non-breaking; ignored by frontend)
        algo_debug = ev_ctx.get("algorithm_debug")
        if algo_debug:
            schema["algorithm_debug"] = algo_debug

        return schema

    # ── Private ───────────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load preferences and design tokens from disk, then apply prefs."""
        self._prefs         = load_preferences(self._rules_path)
        self._design_tokens = load_design_tokens(self._tokens_dir)
        self._apply_prefs()

    def _apply_prefs(self) -> None:
        """Derive runtime values from preferences dict."""
        if not self._prefs:
            self._preferences_applied = False
            self._table_rules         = DEFAULT_TABLE_RULES
            self._conf_thresholds     = DEFAULT_CONFIDENCE
            return

        self._table_rules     = self._prefs.get("table_rules",             DEFAULT_TABLE_RULES)
        self._conf_thresholds = self._prefs.get("confidence_thresholds",   DEFAULT_CONFIDENCE)
        self._preferences_applied = True