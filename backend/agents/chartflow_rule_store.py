"""
chartflow_rule_store.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
ARUP Chartflow Rule Store

Loads and saves ARUP chartflow rule artifacts via the design library store.
Bridges the chartflow extraction workflow to versioned persistence.

Design principle:
  CANONICAL     — maintains single logical artifact with version history
  VERSIONED     — all updates create snapshots, preserving full lineage
  INTEGRATED    — uses existing design_library_store infrastructure
"""

from typing import Optional
from pathlib import Path
import json


# Canonical artifact ID for ARUP chartflow rules
CANONICAL_CHARTFLOW_ARTIFACT_ID = "arup_canonical_chartflow_rules"


class ChartflowRuleStore:
    """
    Loads and saves ARUP chartflow rule artifacts.
    
    Wraps design_library_store to provide chartflow-specific helpers.
    """
    
    def __init__(self, design_library_store):
        """
        Args:
            design_library_store: DesignLibraryStore instance
        """
        self.store = design_library_store
    
    def get_approved_rules(self) -> Optional[dict]:
        """
        Get the currently approved ARUP chartflow rules artifact.
        
        Returns:
            Complete rules spec dict or None if no approved artifact exists.
        """
        try:
            component = self.store.get_component(CANONICAL_CHARTFLOW_ARTIFACT_ID)
            if component and component.get("status") == "approved":
                # The rules are stored in the 'rules' field
                return component.get("rules")
            return None
        except Exception:
            return None
    
    def get_latest_rules(self, include_drafts: bool = True) -> Optional[dict]:
        """
        Get the latest chartflow rules (approved or draft).
        
        Args:
            include_drafts: If True, return draft rules if no approved version exists
        
        Returns:
            Complete rules spec dict or None
        """
        try:
            component = self.store.get_component(CANONICAL_CHARTFLOW_ARTIFACT_ID)
            if not component:
                return None
            
            status = component.get("status")
            if status == "approved" or (include_drafts and status == "draft"):
                return component.get("rules")
            
            return None
        except Exception:
            return None
    
    def save_rules_draft(
        self,
        rules_spec: dict,
        summary: str = "Updated ARUP chartflow rules",
    ) -> dict:
        """
        Save chartflow rules as a draft artifact.
        
        If the canonical artifact exists, updates it.
        Otherwise, creates it as a new artifact.
        
        Args:
            rules_spec: Complete chartflow rules specification
            summary: Version summary
        
        Returns:
            Updated component dict
        """
        # Check if canonical artifact exists
        existing = None
        try:
            existing = self.store.get_component(CANONICAL_CHARTFLOW_ARTIFACT_ID)
        except Exception:
            pass
        
        if existing:
            # Update existing artifact
            updated = self.store.update_component(
                component_id=CANONICAL_CHARTFLOW_ARTIFACT_ID,
                updates={"rules": rules_spec},
                version_summary=summary,
            )
            return updated
        else:
            # Create new canonical artifact
            new_component = {
                "id": CANONICAL_CHARTFLOW_ARTIFACT_ID,
                "name": "ARUP Canonical Chartflow Rules",
                "type": "chartflow_rule_spec",
                "variant": "default",
                "status": "draft",
                "description": "Fidelity-first rendering rules for ARUP algorithm documents",
                "tags": ["chartflow", "algorithm", "fidelity", "taxonomy"],
                "source_basis": {
                    "based_on": "Hierarchy_Algorithms.png visual specification",
                    "design_tokens_used": ["typography", "colors", "spacing_rules", "card_rules"],
                    "sample_assets_used": ["Hierarchy_Algorithms.png"],
                },
                "rules": rules_spec,
                "preview_props": {},
                "conversation": [],
                "versions": [],
            }
            created = self.store.create_component(new_component)
            return created
    
    def approve_rules(self) -> dict:
        """
        Approve the current draft chartflow rules.
        
        Returns:
            Updated component dict with status='approved'
        """
        return self.store.approve_component(CANONICAL_CHARTFLOW_ARTIFACT_ID)
    
    def reopen_for_editing(self) -> dict:
        """
        Reopen approved rules for editing (status -> draft).
        
        Returns:
            Updated component dict with status='draft'
        """
        return self.store.reopen_component(CANONICAL_CHARTFLOW_ARTIFACT_ID)
    
    def get_version_history(self) -> list:
        """
        Get version history for the canonical chartflow rules.
        
        Returns:
            List of version snapshots
        """
        try:
            component = self.store.get_component(CANONICAL_CHARTFLOW_ARTIFACT_ID)
            if component:
                return component.get("versions", [])
            return []
        except Exception:
            return []
    
    def export_rules_json(self, filepath: Optional[Path] = None) -> str:
        """
        Export the approved rules to a JSON file.
        
        Args:
            filepath: Where to save (default: chartflow_rules_export.json)
        
        Returns:
            Path to saved file
        """
        rules = self.get_approved_rules()
        if not rules:
            raise ValueError("No approved chartflow rules to export")
        
        if filepath is None:
            filepath = Path(__file__).parent / "chartflow_rules_export.json"
        
        with open(filepath, "w") as f:
            json.dump(rules, f, indent=2)
        
        return str(filepath)
