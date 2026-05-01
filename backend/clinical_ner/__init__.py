"""
clinical_ner package
────────────────────────────────────────────────────────────────────────────
Biomedical named-entity recognition for retrieval enrichment ONLY.

Phase 3 of the Hugging Face enhancement plan. Default model:
d4data/biomedical-ner-all (DistilBERT, ~266 MB on disk, Apache-2.0).

CRITICAL ROLE BOUNDARY
──────────────────────
NER NEVER makes clinical recommendations. NER NEVER alters intent
classification. NER NEVER drops or filters chunks. NER's only job is
to extract clinical entities (diseases, symptoms, tests, drugs,
anatomy, lab values) from the user's query so the retrieval layer
can run an additional search pass against ARUP content. The reranker
then decides what survives.

This is enforced via the CLINICAL_NER_ROLE constant — any value other
than "enrichment" raises at construction time.
"""

from .clinical_entity_agent import ClinicalEntityAgent  # noqa: F401
