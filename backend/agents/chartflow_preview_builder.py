"""
chartflow_preview_builder.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
ARUP Chartflow Preview Builder

Builds the 3-stage preview workflow for chartflow extraction:
  1. Source asset metadata
  2. Interpretation overlay (category assignments)
  3. Normalized preview model (renderable structure)

Design principle:
  PREVIEW-FIRST — shows visual interpretation before final JSON
  DETERMINISTIC — pure data transformation, no LLM calls
  INSPECTABLE   — each stage is independently viewable
"""

from typing import Dict, List, Optional


class ChartflowPreviewBuilder:
    """
    Builds multi-stage preview for chartflow extraction workflow.
    """
    
    def build_source_stage(
        self,
        asset_metadata: dict,
        extraction_method: str = "deterministic_seed",
    ) -> dict:
        """
        Stage 1: Source asset metadata.
        
        Shows what visual assets are being interpreted.
        """
        return {
            "stage": "source",
            "title": "Source Visual Assets",
            "description": "Design assets used to extract ARUP chartflow taxonomy",
            "assets": [asset_metadata] if not isinstance(asset_metadata, list) else asset_metadata,
            "extraction_method": extraction_method,
        }
    
    def build_interpretation_stage(
        self,
        taxonomy: dict,
        sample_mappings: Optional[List[dict]] = None,
    ) -> dict:
        """
        Stage 2: Interpretation overlay.
        
        Shows how categories are defined and how they map to visual elements.
        """
        # Extract category summaries
        categories = []
        for cat in taxonomy.get("categories", []):
            cat_summary = {
                "id": cat["id"],
                "label": cat["label"],
                "description": cat["description"],
                "visual_traits": cat.get("visual_traits", {}),
                "distinct_from": cat.get("distinct_from", []),
            }
            categories.append(cat_summary)
        
        return {
            "stage": "interpretation",
            "title": "Category Interpretation",
            "description": "ARUP chartflow taxonomy with visual/semantic rules",
            "categories": categories,
            "sample_mappings": sample_mappings or [],
            "preservation_notes": [
                "entry_trigger_point ≠ input_point",
                "singular_decision_point_binary ≠ multi_decision_consideration_point",
                "flow_line ≠ section_flow_line",
                "Footer content (footnotes/references/legend) preserved, not filtered",
            ],
        }
    
    def build_normalized_stage(
        self,
        normalized_graph: dict,
        style_rules: Optional[dict] = None,
    ) -> dict:
        """
        Stage 3: Normalized preview model.
        
        Shows the renderable structure with category assignments applied.
        """
        nodes = normalized_graph.get("nodes", [])
        edges = normalized_graph.get("edges", [])
        footer_blocks = normalized_graph.get("footer_blocks", [])
        stats = normalized_graph.get("statistics", {})
        validation = normalized_graph.get("validation", {})
        
        # Build preview-friendly node summaries
        node_previews = []
        for node in nodes[:10]:  # Limit to first 10 for preview
            node_previews.append({
                "id": node.get("id"),
                "title": node.get("title"),
                "category": node.get("chartflow_category"),
                "confidence": node.get("classification_confidence"),
            })
        
        # Build footer previews
        footer_previews = []
        for fb in footer_blocks[:5]:  # Limit to first 5
            footer_previews.append({
                "id": fb.get("id"),
                "title": fb.get("title"),
                "category": fb.get("chartflow_category"),
            })
        
        return {
            "stage": "normalized",
            "title": "Normalized Preview",
            "description": "Graph data mapped to ARUP chartflow categories",
            "preview_nodes": node_previews,
            "preview_footer": footer_previews,
            "statistics": stats,
            "validation": validation,
            "style_mapping_preview": style_rules or {},
            "notes": [
                f"Total nodes: {stats.get('total_nodes', 0)}",
                f"Flow nodes: {stats.get('flow_nodes', 0)}",
                f"Footer blocks: {stats.get('footer_blocks', 0)}",
                "Category distribution: " + ", ".join(
                    f"{k}={v}" for k, v in stats.get("category_distribution", {}).items()
                ),
            ],
        }
    
    def build_complete_preview(
        self,
        asset_metadata: dict,
        taxonomy: dict,
        normalized_graph: dict,
        style_rules: Optional[dict] = None,
        sample_mappings: Optional[List[dict]] = None,
    ) -> dict:
        """
        Build the complete 3-stage preview workflow.
        
        Returns all three stages in a single structure for frontend rendering.
        """
        return {
            "preview_version": "chartflow_preview_v1",
            "stages": [
                self.build_source_stage(asset_metadata),
                self.build_interpretation_stage(taxonomy, sample_mappings),
                self.build_normalized_stage(normalized_graph, style_rules),
            ],
            "validation_summary": {
                "warnings_count": len(normalized_graph.get("validation", {}).get("warnings", [])),
                "conflicts_count": len(normalized_graph.get("validation", {}).get("category_conflicts", [])),
                "ready_for_save": len(normalized_graph.get("validation", {}).get("category_conflicts", [])) == 0,
            },
        }
