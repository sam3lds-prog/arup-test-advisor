"""
clinical_entity_agent.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
Biomedical NER for retrieval-query enrichment.

Default model
─────────────
d4data/biomedical-ner-all  (DistilBERT-base, 107 entity classes, Apache-2.0)
The model card describes it as a biomedical NER model trained on case-report-
style clinical text.

Output buckets (normalised from the model's 107 raw labels)
──────────────────────────────────────────────────────────
  diseases       — Disease_disorder
  symptoms       — Sign_symptom, Detailed_description
  tests          — Diagnostic_procedure, Lab_value
  medications    — Medication
  organisms      — Bacteria, Virus, Microorganism, Fungus
  anatomy        — Biological_structure, Body_part
  procedures     — Therapeutic_procedure
  other          — anything that didn't match above (kept for transparency)

Hard rules
──────────
  • CLINICAL_NER_ROLE must be "enrichment" — any other value raises.
  • extract() must NEVER return clinical recommendations.
  • Imports of transformers/torch are local to __init__.
  • Pipeline runs on CPU by default — single short query, MPS overhead
    dominates the kernel speedup for one-token batches.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "d4data/biomedical-ner-all"

# Map raw model labels → coarse buckets used for retrieval enrichment.
# Anything not in this map falls into "other".
_LABEL_BUCKETS: Dict[str, str] = {
    # Diseases / disorders
    "Disease_disorder":         "diseases",
    "Cancer":                   "diseases",
    # Symptoms / signs
    "Sign_symptom":             "symptoms",
    "Detailed_description":     "symptoms",
    "Severity":                 "symptoms",
    # Tests / labs
    "Diagnostic_procedure":     "tests",
    "Lab_value":                "tests",
    "Test":                     "tests",
    "Biomarker":                "tests",
    # Drugs
    "Medication":               "medications",
    "Drug":                     "medications",
    "Therapeutic_procedure":    "procedures",
    # Microbiology
    "Bacteria":                 "organisms",
    "Virus":                    "organisms",
    "Microorganism":            "organisms",
    "Fungus":                   "organisms",
    # Anatomy
    "Biological_structure":     "anatomy",
    "Body_part":                "anatomy",
    "Tissue":                   "anatomy",
    "Cell":                     "anatomy",
    "Organ":                    "anatomy",
}

_BUCKET_NAMES = [
    "diseases", "symptoms", "tests", "medications",
    "organisms", "anatomy", "procedures", "other",
]


class ClinicalEntityAgent:
    """NER pipeline wrapper. Extract-only. Cannot recommend tests."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str = "cpu",
        min_score: float = 0.70,
        max_entities: int = 12,
    ):
        # Enforce role at construction time. The env var exists only to make
        # the rule visible/auditable in deployment configs.
        role = os.getenv("CLINICAL_NER_ROLE", "enrichment")
        if role != "enrichment":
            raise RuntimeError(
                f"CLINICAL_NER_ROLE must be 'enrichment' (got {role!r}). "
                "Decision-making roles are forbidden by design."
            )

        from transformers import (
            AutoModelForTokenClassification, AutoTokenizer, pipeline,
        )

        self.name = model_name
        self.device = device or "cpu"
        self.min_score = float(min_score)
        self.max_entities = int(max_entities)

        logger.info(
            "ClinicalEntityAgent: loading model=%s device=%s min_score=%.2f",
            self.name, self.device, self.min_score,
        )

        tokenizer = AutoTokenizer.from_pretrained(self.name)
        model = AutoModelForTokenClassification.from_pretrained(self.name)

        # transformers' pipeline expects:
        #   • int (-1=cpu, 0=cuda:0) OR
        #   • str ("mps") in recent versions
        if self.device == "cpu":
            pipe_device = -1
        elif self.device == "mps":
            pipe_device = "mps"
        else:
            pipe_device = 0  # cuda

        self._pipe = pipeline(
            "ner",
            model=model,
            tokenizer=tokenizer,
            aggregation_strategy="simple",  # merges sub-tokens into spans
            device=pipe_device,
        )
        logger.info("ClinicalEntityAgent: ready name=%s", self.name)

    # ── Extraction ───────────────────────────────────────────────────────────

    def extract(self, text: str) -> List[Dict]:
        """
        Return a list of entity dicts:
            {text, label (raw), bucket, score, start, end}
        Sorted by score desc, capped at max_entities, filtered by min_score.
        """
        if not text or not isinstance(text, str):
            return []

        try:
            raw = self._pipe(text)
        except Exception as exc:
            logger.warning("ClinicalEntityAgent: NER failed (%s)", exc)
            return []

        ents: List[Dict] = []
        for r in raw:
            try:
                score = float(r.get("score", 0.0))
            except (TypeError, ValueError):
                continue
            if score < self.min_score:
                continue
            label = str(r.get("entity_group", r.get("entity", "Other")))
            ents.append({
                "text":   str(r.get("word", "")).strip(),
                "label":  label,
                "bucket": _LABEL_BUCKETS.get(label, "other"),
                "score":  round(score, 4),
                "start":  int(r.get("start", -1)) if r.get("start") is not None else -1,
                "end":    int(r.get("end", -1)) if r.get("end") is not None else -1,
            })

        ents.sort(key=lambda e: e["score"], reverse=True)
        return ents[: self.max_entities]

    # ── Helper: build a free-text enrichment query from extracted entities ──

    def build_enrichment_query(self, entities: List[Dict]) -> str:
        """
        Produce a short free-text string suitable for a secondary vector
        search. Prioritises diseases > tests > symptoms > organisms.

        Returns "" when no useful entities are present.
        """
        if not entities:
            return ""

        priority_order = ["diseases", "tests", "symptoms", "organisms",
                          "medications", "anatomy", "procedures"]
        bucketed: Dict[str, List[str]] = {b: [] for b in _BUCKET_NAMES}
        for e in entities:
            bucketed.setdefault(e["bucket"], []).append(e["text"])

        terms: List[str] = []
        seen: set = set()
        for b in priority_order:
            for t in bucketed.get(b, []):
                tl = t.lower().strip()
                if tl and tl not in seen and len(tl) >= 2:
                    seen.add(tl)
                    terms.append(t)
                if len(terms) >= 6:
                    break
            if len(terms) >= 6:
                break

        return " ".join(terms)

    def health(self) -> dict:
        return {
            "model":        self.name,
            "device":       self.device,
            "min_score":    self.min_score,
            "max_entities": self.max_entities,
            "role":         "enrichment",
        }

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "ClinicalEntityAgent":
        return cls(
            model_name=os.getenv("CLINICAL_NER_MODEL", DEFAULT_MODEL),
            device=os.getenv("CLINICAL_NER_DEVICE", "cpu"),
            min_score=float(os.getenv("CLINICAL_NER_MIN_SCORE", "0.70")),
            max_entities=int(os.getenv("CLINICAL_NER_MAX_ENTITIES", "12")),
        )
