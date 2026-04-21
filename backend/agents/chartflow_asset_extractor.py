"""
chartflow_asset_extractor.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
ARUP Chartflow Asset Extractor

Extracts ARUP chartflow taxonomy and rules from visual design assets.
For now, uses a deterministic seed approach based on the Hierarchy_Algorithms.png
visual specification rather than attempting brittle computer vision.

Design principle:
  DETERMINISTIC — uses explicit category mapping from known visual assets
  EXTENSIBLE    — supports future asset types and extraction strategies
  PRESERVING    — maintains ARUP-defined category distinctions
"""

from typing import Dict, List, Optional
from pathlib import Path


# ── ARUP Canonical Chartflow Taxonomy (from Hierarchy_Algorithms.png) ──────

ARUP_CHARTFLOW_TAXONOMY = {
    "schema_version": "arup_chartflow_taxonomy_v1",
    "source": "Hierarchy_Algorithms.png",
    "categories": [
        {
            "id": "typography",
            "label": "Typography",
            "description": "The typeface for algorithm typography",
            "subcategories": [
                {
                    "id": "italics",
                    "label": "Italics",
                    "description": "Italicize any italic/technical words",
                },
                {
                    "id": "bold",
                    "label": "Bold",
                    "description": "Bold titles, subtitles and section labels",
                },
                {
                    "id": "superscripts",
                    "label": "Superscripts",
                    "description": "Superscripts for footnote references",
                },
            ],
            "visual_traits": {
                "font_family": "Roboto",
                "base_size": "16px",
                "line_height": "1.6",
            },
        },
        {
            "id": "entry_trigger_point",
            "label": "Entry / Trigger Points",
            "description": "Start at a flow without user input",
            "distinct_from": ["input_point"],
            "visual_traits": {
                "background_color": "#AE132A",  # ARUP Primary Red
                "text_color": "#FFFFFF",
                "border_style": "none",
                "shape": "rounded_rectangle",
                "min_padding": "16px 24px",
            },
            "semantic_traits": {
                "is_entry": True,
                "requires_input": False,
                "role": "document_entry",
            },
        },
        {
            "id": "input_point",
            "label": "Input Point",
            "description": "Typically requires data or a value",
            "distinct_from": ["entry_trigger_point"],
            "visual_traits": {
                "background_color": "transparent",
                "text_color": "#171717",  # Dark Sky
                "border_color": "#77787B",  # Granite
                "border_width": "2px",
                "border_style": "solid",
                "shape": "rounded_rectangle",
                "min_padding": "12px 16px",
            },
            "semantic_traits": {
                "is_entry": False,
                "requires_input": True,
                "role": "data_collection",
            },
        },
        {
            "id": "process_step_node",
            "label": "Process / Step Nodes",
            "description": "Living without performed by system",
            "visual_traits": {
                "background_color": "#F5EFE7",  # Warm light background
                "text_color": "#171717",
                "border_color": "#77787B",
                "border_width": "1px",
                "shape": "rounded_rectangle",
                "min_padding": "12px 16px",
            },
            "semantic_traits": {
                "role": "action_step",
                "automated": True,
            },
        },
        {
            "id": "singular_decision_point_binary",
            "label": "Singular Decision Points (Binary)",
            "description": "Yes, No, True, False, Pass, Fail, Not A, Not B, Tumor, Germ or Follicles i.e.",
            "distinct_from": ["multi_decision_consideration_point"],
            "visual_traits": {
                "background_color": "transparent",
                "text_color": "#171717",
                "border_color": "#77787B",
                "border_width": "2px",
                "border_style": "solid",
                "shape": "pill",  # Rounded pill for binary choices
                "min_padding": "8px 20px",
            },
            "semantic_traits": {
                "decision_type": "binary",
                "max_branches": 2,
                "role": "binary_branch",
            },
            "routing_labels": {
                "examples": ["Yes", "No", "Positive", "Negative", "Pass", "Fail"],
                "position": "on_edge",
            },
        },
        {
            "id": "multi_decision_consideration_point",
            "label": "Multi-Decision / Consideration Points",
            "description": "Evaluate options, choose palette, or weigh possibilities. \"Consider\", \"Select\", \"Determine best approach\"",
            "distinct_from": ["singular_decision_point_binary"],
            "visual_traits": {
                "background_color": "#FFFFFF",
                "text_color": "#171717",
                "border_color": "#306385",  # Lab Blue
                "border_width": "2px",
                "border_style": "solid",
                "shape": "rectangle",
                "min_padding": "16px 20px",
                "container_style": "stacked_options",
            },
            "semantic_traits": {
                "decision_type": "multi_option",
                "max_branches": "unlimited",
                "role": "consideration_matrix",
                "may_contain_nested_items": True,
            },
            "layout_notes": [
                "Often contains multiple sub-considerations",
                "May use container/group semantics",
                "\"TEST 1 - Confirmation Testing\" as examples",
            ],
        },
        {
            "id": "exit_termination_point",
            "label": "Exit / Termination Points",
            "description": "Completion, End of flow",
            "visual_traits": {
                "background_color": "transparent",
                "text_color": "#171717",
                "border_color": "#77787B",
                "border_width": "2px",
                "border_style": "dashed",
                "shape": "rounded_rectangle",
                "min_padding": "12px 16px",
            },
            "semantic_traits": {
                "role": "terminal_outcome",
                "is_exit": True,
            },
        },
        {
            "id": "flow_line",
            "label": "Flow Lines",
            "description": "Creates flow/order of processes",
            "distinct_from": ["section_flow_line"],
            "visual_traits": {
                "stroke_color": "#77787B",
                "stroke_width": "2px",
                "stroke_style": "solid",
                "arrow_style": "filled_triangle",
            },
            "semantic_traits": {
                "role": "node_to_node_connector",
                "directional": True,
            },
        },
        {
            "id": "section_flow_line",
            "label": "Section Flow Lines",
            "description": "Boxed grouping of sub-workflows across multiple nodes/areas. Creates structural/sectional boundaries.",
            "distinct_from": ["flow_line"],
            "visual_traits": {
                "stroke_color": "#306385",  # Lab Blue for section grouping
                "stroke_width": "2px",
                "stroke_style": "dashed",
                "fill": "transparent",
                "shape": "rectangle",
                "min_padding": "24px",
            },
            "semantic_traits": {
                "role": "section_boundary",
                "contains_multiple_nodes": True,
                "grouping_semantic": True,
            },
        },
        {
            "id": "footnotes",
            "label": "Footnotes",
            "description": "Superscripts with italics are considered numbered footnotes that should be listed in the alphanumeric order",
            "visual_traits": {
                "font_style": "italic",
                "font_size": "14px",
                "text_color": "#77787B",
                "marker_style": "superscript_number",
            },
            "semantic_traits": {
                "role": "footer_content",
                "document_section": "footer",
                "not_renderable_as_flow_node": True,
            },
        },
        {
            "id": "references",
            "label": "References",
            "description": "Bibliographic references should be listed in the chronological order",
            "visual_traits": {
                "font_size": "12px",
                "text_color": "#77787B",
                "line_height": "1.4",
            },
            "semantic_traits": {
                "role": "footer_content",
                "document_section": "footer",
                "not_renderable_as_flow_node": True,
                "ordering": "chronological",
            },
        },
        {
            "id": "abbreviations_legend",
            "label": "Abbreviations/Legend",
            "description": "Abbreviations and their expansions",
            "visual_traits": {
                "font_size": "13px",
                "text_color": "#171717",
                "layout": "two_column",
            },
            "semantic_traits": {
                "role": "footer_content",
                "document_section": "footer",
                "not_renderable_as_flow_node": True,
            },
        },
    ],
}


class ChartflowAssetExtractor:
    """
    Extracts ARUP chartflow rules from visual design assets.
    
    For v1.0.0, uses deterministic seeding from known ARUP taxonomy.
    Future versions may add image analysis capabilities.
    """
    
    def __init__(self):
        self.taxonomy = ARUP_CHARTFLOW_TAXONOMY
    
    def extract_from_hierarchy_asset(
        self,
        asset_id: Optional[str] = None,
        asset_path: Optional[str] = None,
    ) -> dict:
        """
        Extract chartflow rules from the ARUP Hierarchy_Algorithms.png asset.
        
        Returns a structured rules specification ready for preview/save.
        """
        # Build source asset metadata
        source_assets = []
        if asset_id or asset_path:
            source_assets.append({
                "asset_id": asset_id or "hierarchy_algorithms_seed",
                "filename": asset_path or "Hierarchy_Algorithms.png",
                "asset_type": "image",
                "purpose": "ARUP canonical chartflow taxonomy source",
            })
        
        # Generate the full rules spec
        spec = {
            "schema_version": "arup_chartflow_rules_v1",
            "artifact_type": "algorithm_chartflow_rule_spec",
            "artifact_id": "arup_canonical_chartflow_v1",
            "name": "ARUP Canonical Chartflow Rules",
            "description": "Fidelity-first rendering rules preserving ARUP algorithm document structure",
            "source_assets": source_assets,
            "taxonomy": self.taxonomy,
            "extraction_metadata": {
                "extraction_method": "deterministic_seed",
                "extraction_version": "1.0.0",
                "manual_review_required": False,
            },
        }
        
        return spec
    
    def get_category_by_id(self, category_id: str) -> Optional[dict]:
        """Look up a category definition by ID."""
        for cat in self.taxonomy["categories"]:
            if cat["id"] == category_id:
                return cat
        return None
    
    def get_all_node_categories(self) -> List[dict]:
        """Return all categories that represent flow nodes (not connectors/footer)."""
        node_ids = {
            "entry_trigger_point",
            "input_point",
            "process_step_node",
            "singular_decision_point_binary",
            "multi_decision_consideration_point",
            "exit_termination_point",
        }
        return [
            cat for cat in self.taxonomy["categories"]
            if cat["id"] in node_ids
        ]
    
    def get_footer_categories(self) -> List[dict]:
        """Return all categories that represent footer content."""
        footer_ids = {"footnotes", "references", "abbreviations_legend"}
        return [
            cat for cat in self.taxonomy["categories"]
            if cat["id"] in footer_ids
        ]
