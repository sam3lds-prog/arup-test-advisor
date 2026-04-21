"""
arup_algorithm_template_renderer.py  v1.1.0
────────────────────────────────────────────────────────────────────────────
ARUP Algorithm Template Renderer — Fidelity-First Edition

REFINEMENTS in v1.1.0:
  - Horizontal two-column layout detection (like AKI/CKD algorithm)
  - Better parallel pathway identification
  - Enhanced node positioning for side-by-side flows
  - Improved visual hierarchy matching ARUP PDFs

Core differences from the clinical renderer:
  - Preserves original chartflow shape (no reclassification into 3 clinical modes)
  - Detects and preserves horizontal splits (two parallel pathways)
  - Preserves entry/trigger vs input distinction
  - Preserves binary vs multi-decision distinction
  - Preserves routing labels as edge annotations
  - Preserves section flow lines as grouping boundaries
  - Preserves footer blocks (footnotes, references, legend) as document sections
  - Uses ARUP design tokens for styling

Design principle:
  FIDELITY-FIRST — preserve source structure over abstract optimization
  DETERMINISTIC   — pure graph transformation, no LLM calls
  TOKENIZED       — uses ARUP design system tokens for all styling
"""

from typing import Dict, List, Optional, Set, Tuple
from collections import deque, defaultdict


class ArupAlgorithmTemplateRenderer:
    """
    Fidelity-first renderer for ARUP algorithm documents.
    
    Uses approved chartflow rules to map graph data into document structure
    while preserving the original visual hierarchy and distinctions.
    """
    
    def __init__(self, chartflow_rules: Optional[dict] = None):
        """
        Args:
            chartflow_rules: Approved ARUP chartflow rules specification.
                            If None, will fall back to basic rendering.
        """
        self.rules = chartflow_rules or {}
        self.taxonomy = self.rules.get("taxonomy", {})
    
    def render_algorithm(
        self,
        graph_data: dict,
        normalized_graph: Optional[dict] = None,
        source_pdf_url: Optional[str] = None,
        source_asset_id: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Render an algorithm using fidelity-first approach.
        
        Args:
            graph_data: Raw algorithm graph (nodes, edges)
            normalized_graph: Pre-normalized graph with category assignments
            source_pdf_url: Optional URL to source PDF for split-view
            source_asset_id: Optional asset ID for PDF serving
        
        Returns:
            Render model dict for frontend, or None if graph unavailable
        """
        if not graph_data or not graph_data.get("nodes"):
            return None
        
        # Use normalized graph if provided, otherwise use raw
        nodes = normalized_graph.get("nodes", []) if normalized_graph else graph_data.get("nodes", [])
        edges = normalized_graph.get("edges", []) if normalized_graph else graph_data.get("edges", [])
        footer_blocks = normalized_graph.get("footer_blocks", []) if normalized_graph else []
        
        if not nodes:
            return None
        
        # Build node map and graph structure
        node_map = {n["id"]: n for n in nodes + footer_blocks}
        
        successors = defaultdict(list)
        predecessors = defaultdict(list)
        for edge in edges:
            src = edge.get("source")
            tgt = edge.get("target")
            if src and tgt:
                successors[src].append(tgt)
                predecessors[tgt].append(src)
        
        # Identify document structure and layout
        entry_nodes = self._find_entry_nodes(nodes, predecessors)
        layout_mode = self._detect_layout_mode(nodes, edges, successors, entry_nodes)
        
        # Build column groups for horizontal layouts
        columns = []
        if layout_mode == "horizontal_two_column":
            columns = self._build_horizontal_columns(nodes, edges, successors, predecessors, entry_nodes)
        
        # Build renderable nodes with styling
        rendered_nodes = []
        for node in nodes:
            rendered_node = self._render_node(node, successors, predecessors)
            if rendered_node:
                rendered_nodes.append(rendered_node)
        
        # Build renderable edges with routing labels
        rendered_edges = self._render_edges(edges, node_map)
        
        # Build footer blocks
        rendered_footer = self._render_footer_blocks(footer_blocks)
        
        # Extract routing labels from edges
        routing_labels = self._extract_routing_labels(edges, node_map)
        
        return {
            "render_mode": "arup_document_fidelity",
            "document_template_id": self.rules.get("artifact_id", "unknown"),
            "layout_mode": layout_mode,
            "columns": columns,
            "nodes": rendered_nodes,
            "edges": rendered_edges,
            "routing_labels": routing_labels,
            "footer_blocks": rendered_footer,
            "source_pdf_url": source_pdf_url,
            "source_asset_id": source_asset_id,
            "entry_nodes": [n["id"] for n in entry_nodes],
            "stats": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "footer_blocks": len(footer_blocks),
                "columns": len(columns),
            },
        }
    
    def _detect_layout_mode(
        self,
        nodes: List[dict],
        edges: List[dict],
        successors: Dict[str, List[str]],
        entry_nodes: List[dict],
    ) -> str:
        """
        Detect the layout mode based on graph structure.
        
        Patterns:
        - horizontal_two_column: Entry splits into 2 major parallel pathways (like AKI algorithm)
        - vertical_linear: Simple top-to-bottom flow
        - tree: Branching decision tree
        """
        if not entry_nodes:
            return "vertical_linear"
        
        # Check if first node(s) create a horizontal split
        # Pattern: Entry -> 2 parallel branches that each have long vertical chains
        entry_id = entry_nodes[0]["id"]
        immediate_successors = successors.get(entry_id, [])
        
        if len(immediate_successors) == 2:
            # Check if both branches have significant depth (indicating parallel pathways)
            left_depth = self._calculate_branch_depth(immediate_successors[0], successors)
            right_depth = self._calculate_branch_depth(immediate_successors[1], successors)
            
            # If both branches have depth >= 3, it's likely a horizontal split
            if left_depth >= 3 and right_depth >= 3:
                return "horizontal_two_column"
        
        # Check for tree pattern (multiple binary decisions)
        decision_count = sum(
            1 for n in nodes 
            if n.get("chartflow_category") == "singular_decision_point_binary"
        )
        
        if decision_count >= 3:
            return "tree"
        
        return "vertical_linear"
    
    def _calculate_branch_depth(
        self,
        start_node: str,
        successors: Dict[str, List[str]],
        visited: Optional[Set[str]] = None,
    ) -> int:
        """Calculate the depth of a branch starting from a node."""
        if visited is None:
            visited = set()
        
        if start_node in visited:
            return 0
        
        visited.add(start_node)
        
        children = successors.get(start_node, [])
        if not children:
            return 1
        
        # Return the maximum depth among all children
        max_depth = max(
            (self._calculate_branch_depth(child, successors, visited) for child in children),
            default=0
        )
        
        return 1 + max_depth
    
    def _build_horizontal_columns(
        self,
        nodes: List[dict],
        edges: List[dict],
        successors: Dict[str, List[str]],
        predecessors: Dict[str, List[str]],
        entry_nodes: List[dict],
    ) -> List[dict]:
        """
        Build column groups for horizontal two-column layout.
        
        Returns list of column dicts with node_ids and metadata.
        """
        if not entry_nodes:
            return []
        
        entry_id = entry_nodes[0]["id"]
        immediate_successors = successors.get(entry_id, [])
        
        if len(immediate_successors) != 2:
            return []
        
        # Build two columns by traversing each branch
        left_branch = immediate_successors[0]
        right_branch = immediate_successors[1]
        
        left_nodes = self._collect_branch_nodes(left_branch, successors)
        right_nodes = self._collect_branch_nodes(right_branch, successors)
        
        # Determine column titles from first decision labels
        # Look for edges from entry to these branches
        left_label = ""
        right_label = ""
        for edge in edges:
            if edge.get("source") == entry_id:
                if edge.get("target") == left_branch:
                    left_label = edge.get("label", "")
                elif edge.get("target") == right_branch:
                    right_label = edge.get("label", "")
        
        return [
            {
                "column_id": "left",
                "title": left_label or "Left Pathway",
                "node_ids": [left_branch] + left_nodes,
                "alignment": "left",
            },
            {
                "column_id": "right",
                "title": right_label or "Right Pathway",
                "node_ids": [right_branch] + right_nodes,
                "alignment": "right",
            },
        ]
    
    def _collect_branch_nodes(
        self,
        start_node: str,
        successors: Dict[str, List[str]],
        visited: Optional[Set[str]] = None,
    ) -> List[str]:
        """Collect all nodes in a branch via BFS."""
        if visited is None:
            visited = set()
        
        result = []
        queue = deque([start_node])
        
        while queue:
            node_id = queue.popleft()
            
            if node_id in visited:
                continue
            
            visited.add(node_id)
            result.append(node_id)
            
            for child in successors.get(node_id, []):
                if child not in visited:
                    queue.append(child)
        
        return result
    
    def _find_entry_nodes(
        self,
        nodes: List[dict],
        predecessors: Dict[str, List[str]],
    ) -> List[dict]:
        """Find document entry/trigger nodes (no predecessors or explicitly marked)."""
        entry_nodes = []
        for node in nodes:
            node_id = node.get("id")
            category = node.get("chartflow_category", "")
            
            # Explicit entry/trigger category
            if category == "entry_trigger_point":
                entry_nodes.append(node)
            # Or no predecessors (graph root)
            elif not predecessors.get(node_id):
                entry_nodes.append(node)
        
        return entry_nodes
    
    def _render_node(
        self,
        node: dict,
        successors: Dict[str, List[str]],
        predecessors: Dict[str, List[str]],
    ) -> Optional[dict]:
        """
        Render a single node with ARUP styling based on chartflow category.
        """
        category = node.get("chartflow_category", "process_step_node")
        
        # Get visual traits from taxonomy
        cat_def = self._get_category_definition(category)
        visual_traits = cat_def.get("visual_traits", {}) if cat_def else {}
        
        # Build styled node
        styled_node = {
            "id": node.get("id"),
            "title": node.get("title", ""),
            "body": node.get("body", ""),
            "category": category,
            "visual": {
                "background_color": visual_traits.get("background_color", "transparent"),
                "text_color": visual_traits.get("text_color", "#171717"),
                "border_color": visual_traits.get("border_color", "#77787B"),
                "border_width": visual_traits.get("border_width", "1px"),
                "border_style": visual_traits.get("border_style", "solid"),
                "shape": visual_traits.get("shape", "rounded_rectangle"),
                "padding": visual_traits.get("min_padding", "12px 16px"),
            },
            "out_degree": len(successors.get(node["id"], [])),
            "in_degree": len(predecessors.get(node["id"], [])),
        }
        
        return styled_node
    
    def _render_edges(
        self,
        edges: List[dict],
        node_map: Dict[str, dict],
    ) -> List[dict]:
        """
        Render edges with ARUP styling based on edge type.
        """
        rendered = []
        
        for edge in edges:
            edge_type = edge.get("chartflow_edge_type", "flow_line")
            
            # Get visual traits for this edge type
            if edge_type == "section_flow_line":
                visual = {
                    "stroke_color": "#306385",  # Lab Blue
                    "stroke_width": "2px",
                    "stroke_style": "dashed",
                }
            else:  # flow_line
                visual = {
                    "stroke_color": "#77787B",  # Granite
                    "stroke_width": "2px",
                    "stroke_style": "solid",
                    "arrow_style": "filled_triangle",
                }
            
            rendered.append({
                "id": edge.get("id"),
                "source": edge.get("source"),
                "target": edge.get("target"),
                "label": edge.get("label", ""),
                "edge_type": edge_type,
                "visual": visual,
            })
        
        return rendered
    
    def _render_footer_blocks(
        self,
        footer_blocks: List[dict],
    ) -> List[dict]:
        """
        Render footer content (footnotes, references, abbreviations/legend).
        
        Footer content is NOT filtered out - it's preserved as document sections.
        """
        rendered = []
        
        for block in footer_blocks:
            category = block.get("chartflow_category", "")
            
            # Get visual traits
            if category == "footnotes":
                visual = {
                    "font_style": "italic",
                    "font_size": "14px",
                    "text_color": "#77787B",
                    "marker_style": "superscript_number",
                }
            elif category == "references":
                visual = {
                    "font_size": "12px",
                    "text_color": "#77787B",
                    "line_height": "1.4",
                }
            elif category == "abbreviations_legend":
                visual = {
                    "font_size": "13px",
                    "text_color": "#171717",
                    "layout": "two_column",
                }
            else:
                visual = {}
            
            rendered.append({
                "id": block.get("id"),
                "title": block.get("title", ""),
                "body": block.get("body", ""),
                "category": category,
                "visual": visual,
            })
        
        return rendered
    
    def _extract_routing_labels(
        self,
        edges: List[dict],
        node_map: Dict[str, dict],
    ) -> List[dict]:
        """
        Extract routing labels from edges (Yes/No, Positive/Negative, etc).
        
        Routing labels are edge annotations, not node labels.
        """
        routing_labels = []
        
        for edge in edges:
            label = edge.get("label", "").strip()
            if not label:
                continue
            
            # Check if this looks like a routing label (binary choice indicator)
            routing_keywords = {
                "yes", "no", "positive", "negative", "true", "false",
                "pass", "fail", "left", "right",
            }
            
            if label.lower() in routing_keywords:
                routing_labels.append({
                    "edge_id": edge.get("id"),
                    "label": label,
                    "source": edge.get("source"),
                    "target": edge.get("target"),
                    "visual": {
                        "font_size": "13px",
                        "font_weight": "600",
                        "color": "#306385",  # Lab Blue
                    },
                })
        
        return routing_labels
    
    def _get_category_definition(self, category_id: str) -> Optional[dict]:
        """Look up category definition from taxonomy."""
        for cat in self.taxonomy.get("categories", []):
            if cat["id"] == category_id:
                return cat
        return None
