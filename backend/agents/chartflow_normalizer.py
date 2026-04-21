"""
chartflow_normalizer.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
ARUP Chartflow Normalizer

Maps raw algorithm graph data to ARUP canonical chartflow categories,
preserving critical distinctions like entry_trigger vs input, binary vs
multi-decision, and footer content.

Design principle:
  PRESERVING    — never collapses distinct categories
  DETERMINISTIC — rule-based classification, no LLM calls
  VALIDATING    — surfaces conflicts and unmapped items
"""

import re
from typing import Dict, List, Optional, Set, Tuple


# ── Classification helpers ───────────────────────────────────────────────────

def _is_footer_content(node: dict) -> Optional[str]:
    """
    Detect if a node is footer content (footnotes/references/legend).
    Returns the footer category ID or None.
    """
    title = (node.get("title") or "").strip().lower()
    body = (node.get("body") or "").strip().lower()
    combined = f"{title} {body}"
    
    # Footnote markers
    if node.get("title", "").strip() and re.match(r"^\d+\.", node["title"].strip()):
        return "footnotes"
    
    # References
    if any(kw in title for kw in ["references", "bibliography", "citations"]):
        return "references"
    if any(pattern in combined for pattern in [
        "thyroid. 20", "endocr pract", "j clin endocrinol", "nccn clinical practice",
        ";22(", ";26(", ";47(", "accessed jan 20",
    ]):
        return "references"
    
    # Abbreviations/Legend
    if any(kw in title for kw in ["abbreviations", "legend", "glossary"]):
        return "abbreviations_legend"
    
    # Copyright/source metadata
    if title.startswith("©") or "all rights reserved" in combined:
        return "references"  # Treat as reference footer content
    
    return None


def _classify_node_category(node: dict, graph_context: dict) -> dict:
    """
    Classify a single node into ARUP chartflow category.
    
    Returns:
        {
            "category_id": str,
            "confidence": "high" | "medium" | "low",
            "reasoning": str,
            "warnings": [str],
        }
    """
    title = (node.get("title") or "").strip()
    body = (node.get("body") or "").strip()
    node_type = node.get("node_type", "").lower()
    combined = f"{title} {body}".lower()
    
    warnings = []
    
    # Check for footer content first
    footer_cat = _is_footer_content(node)
    if footer_cat:
        return {
            "category_id": footer_cat,
            "confidence": "high",
            "reasoning": "Footer content pattern detected",
            "warnings": [],
        }
    
    # Entry / Trigger Points
    # Look for document-entry semantics without explicit input requirement
    if node_type == "start" or any(kw in combined for kw in [
        "screening criteria", "when to test", "initial presentation",
        "presenting with", "suspect",
    ]):
        return {
            "category_id": "entry_trigger_point",
            "confidence": "high",
            "reasoning": "Document entry / screening criteria pattern",
            "warnings": [],
        }
    
    # Input Points
    # Explicit data/value input nodes
    if any(kw in combined for kw in [
        "enter", "input", "provide", "specify value", "patient data",
        "test result:", "laboratory value", "clinical finding:",
    ]):
        return {
            "category_id": "input_point",
            "confidence": "high",
            "reasoning": "Explicit input/data collection pattern",
            "warnings": [],
        }
    
    # Binary Decision Points
    # Look for yes/no, pass/fail, positive/negative branching
    # Check out-degree to confirm binary
    out_degree = len(graph_context.get("successors", {}).get(node.get("id"), []))
    if node_type == "decision":
        if out_degree == 2:
            return {
                "category_id": "singular_decision_point_binary",
                "confidence": "high",
                "reasoning": "Decision node with exactly 2 outgoing edges",
                "warnings": [],
            }
        elif out_degree > 2:
            return {
                "category_id": "multi_decision_consideration_point",
                "confidence": "high",
                "reasoning": f"Decision node with {out_degree} branches",
                "warnings": [],
            }
        else:
            # Decision node but unclear branching
            warnings.append(f"Decision node with unclear branching (out_degree={out_degree})")
            return {
                "category_id": "singular_decision_point_binary",
                "confidence": "low",
                "reasoning": "Decision type but unclear branching structure",
                "warnings": warnings,
            }
    
    # Multi-Decision / Consideration Points
    # Look for "consider", "select", "evaluate options", "choose"
    if any(kw in combined for kw in [
        "consider", "select", "evaluate options", "choose",
        "determine best", "weigh", "options:", "test 1", "test 2",
    ]):
        return {
            "category_id": "multi_decision_consideration_point",
            "confidence": "medium",
            "reasoning": "Consideration/option-weighing language detected",
            "warnings": [],
        }
    
    # Exit / Termination Points
    if node_type == "outcome" or any(kw in combined for kw in [
        "diagnosis:", "refer to", "treatment:", "end of algorithm",
        "complete", "final", "no further",
    ]):
        return {
            "category_id": "exit_termination_point",
            "confidence": "high",
            "reasoning": "Terminal outcome pattern",
            "warnings": [],
        }
    
    # Process / Step Nodes (default for action nodes)
    if node_type == "action" or any(kw in combined for kw in [
        "order", "perform", "obtain", "measure", "collect",
        "send to lab", "request", "administer",
    ]):
        return {
            "category_id": "process_step_node",
            "confidence": "medium",
            "reasoning": "Action/process step pattern",
            "warnings": [],
        }
    
    # Fallback: generic process node
    warnings.append("No clear category match, defaulting to process_step_node")
    return {
        "category_id": "process_step_node",
        "confidence": "low",
        "reasoning": "Default classification for unclear node",
        "warnings": warnings,
    }


def _classify_edge_type(edge: dict, source_node: dict, target_node: dict) -> str:
    """
    Classify an edge as flow_line or section_flow_line.
    
    Section flow lines are grouping boundaries, not node-to-node connectors.
    """
    label = (edge.get("label") or "").strip().lower()
    
    # Section flow lines typically have grouping semantics
    if any(kw in label for kw in ["section", "group", "zone", "pathway"]):
        return "section_flow_line"
    
    # Standard node-to-node connector
    return "flow_line"


class ChartflowNormalizer:
    """
    Normalizes algorithm graph data to ARUP chartflow categories.
    """
    
    def normalize_graph(self, graph_data: dict) -> dict:
        """
        Normalize a complete algorithm graph.
        
        Args:
            graph_data: Raw graph with nodes/edges from ingestion
        
        Returns:
            Normalized structure with ARUP category assignments,
            validation warnings, and preserved footer content.
        """
        nodes = graph_data.get("nodes", [])
        edges = graph_data.get("edges", [])
        
        # Build graph context for degree calculations
        successors = {}
        predecessors = {}
        for edge in edges:
            src = edge.get("source")
            tgt = edge.get("target")
            if src and tgt:
                successors.setdefault(src, []).append(tgt)
                predecessors.setdefault(tgt, []).append(src)
        
        graph_context = {
            "successors": successors,
            "predecessors": predecessors,
        }
        
        # Classify each node
        normalized_nodes = []
        footer_blocks = []
        validation_warnings = []
        category_counts = {}
        
        for node in nodes:
            node_id = node.get("id")
            classification = _classify_node_category(node, graph_context)
            
            cat_id = classification["category_id"]
            category_counts[cat_id] = category_counts.get(cat_id, 0) + 1
            
            normalized_node = {
                **node,
                "chartflow_category": cat_id,
                "classification_confidence": classification["confidence"],
                "classification_reasoning": classification["reasoning"],
            }
            
            # Separate footer content
            if cat_id in {"footnotes", "references", "abbreviations_legend"}:
                footer_blocks.append(normalized_node)
            else:
                normalized_nodes.append(normalized_node)
            
            # Collect warnings
            for warning in classification.get("warnings", []):
                validation_warnings.append({
                    "node_id": node_id,
                    "category": cat_id,
                    "warning": warning,
                })
        
        # Classify edges
        normalized_edges = []
        node_map = {n["id"]: n for n in nodes}
        
        for edge in edges:
            src_node = node_map.get(edge.get("source"), {})
            tgt_node = node_map.get(edge.get("target"), {})
            edge_type = _classify_edge_type(edge, src_node, tgt_node)
            
            normalized_edges.append({
                **edge,
                "chartflow_edge_type": edge_type,
            })
        
        # Detect category conflicts
        conflicts = []
        
        # Check for entry_trigger vs input confusion
        entry_count = category_counts.get("entry_trigger_point", 0)
        input_count = category_counts.get("input_point", 0)
        if entry_count == 0 and input_count > 2:
            conflicts.append({
                "type": "missing_entry_trigger",
                "description": f"Found {input_count} input_points but no entry_trigger_point. Check if first input should be entry/trigger.",
            })
        
        # Check for binary vs multi-decision confusion
        binary_count = category_counts.get("singular_decision_point_binary", 0)
        multi_count = category_counts.get("multi_decision_consideration_point", 0)
        if multi_count == 0 and binary_count > 5:
            conflicts.append({
                "type": "all_binary_decisions",
                "description": f"All {binary_count} decisions classified as binary. Verify no multi-option considerations missed.",
            })
        
        return {
            "schema_version": "arup_chartflow_normalized_v1",
            "nodes": normalized_nodes,
            "edges": normalized_edges,
            "footer_blocks": footer_blocks,
            "statistics": {
                "total_nodes": len(nodes),
                "flow_nodes": len(normalized_nodes),
                "footer_blocks": len(footer_blocks),
                "category_distribution": category_counts,
            },
            "validation": {
                "warnings": validation_warnings,
                "category_conflicts": conflicts,
            },
        }
