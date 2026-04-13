"""
response_agent.py  v0.7.0
────────────────────────────────────────────────────────────────────────────
Response Agent — grounded ARUP test recommendations

Changes from v0.6:
  • Consumes structured evidence BUNDLE instead of raw chunks
  • Evidence block includes: candidate tests grouped with their signals,
    unresolved conflicts, evidence gaps, and numbered sources for citation
  • Output schema extended with evidence_coverage per recommendation,
    conflicts_surfaced, and evidence_gaps
  • Safety: model must surface conflicts and gaps, not silently omit them

Changes in v0.7:
  • NEW _validate_response() post-parse validator:
      - Removes source_refs that don't match a real citation number
      - Back-fills missing citation document names from authority_sources
      - Removes duplicate citation entries
      - Appends validation warnings to evidence_gaps for transparency
      - Does NOT raise exceptions — degrades gracefully for clinical safety
"""

import os
import json
import anthropic

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a clinical laboratory test advisor for ARUP Laboratories.

Your role: generate accurate, grounded test recommendations using ONLY the provided ARUP evidence bundle.

STRICT RULES — violating these breaks clinical safety:
1. Use ONLY facts present in the evidence bundle. Do not add external medical knowledge.
2. Every factual claim must reference its source inline: write [SOURCE N].
3. If the bundle contains insufficient evidence, say so explicitly.
4. Never invent test codes, specimen requirements, or clinical criteria.
5. If unresolved_conflicts are present in the bundle, surface them in your answer.
6. If evidence_gaps are present, list them so the clinician knows what is missing.
7. Keep language professional and concise — this is a clinical decision-support tool.
8. Per-recommendation evidence_coverage must reflect what the bundle actually contains.

The evidence bundle includes:
  • candidate_tests: tests grouped with clinical rationale, ordering info, and algorithm support
  • unresolved_conflicts: contradictions detected across sources
  • missing_context: content types not found during retrieval
  • authority_sources: numbered [SOURCE N] blocks for inline citation

Respond ONLY with valid JSON matching this schema exactly (no prose, no markdown fences):

{
  "answer": "Narrative explanation with inline citations [SOURCE N]. 2–4 sentences. Mention conflicts or gaps if present.",
  "recommendations": [
    {
      "test_name": "Full ARUP test name",
      "test_code": "ARUP order code or empty string",
      "rank": "primary" | "secondary" | "reflex",
      "rationale": "One sentence: why this test for this query [SOURCE N]",
      "specimen": "Collection type and volume, or empty string if unknown",
      "tat": "Turnaround time, or empty string if unknown",
      "source_refs": [1, 2],
      "evidence_coverage": {
        "has_algorithm": true | false,
        "has_consult": true | false,
        "has_directory": true | false,
        "has_fact_sheet": true | false
      }
    }
  ],
  "citations": [
    {
      "number": 1,
      "source_type": "Algorithm" | "Consult Topic" | "Fact Sheet" | "Test Directory" | "General",
      "source_role": "testing pathway" | "clinical rationale" | "order logistics" | "interpretation & caveats" | "general reference",
      "document": "original filename",
      "excerpt": "Verbatim excerpt under 80 words that directly supports the recommendation"
    }
  ],
  "conflicts_surfaced": ["Description of any contradictions found — empty list if none"],
  "evidence_gaps": ["What evidence was missing — empty list if complete"],
  "follow_up_questions": ["Question if critical clinical context is missing — empty list if not needed"]
}

If no sources are relevant, return:
{
  "answer": "The uploaded ARUP content does not contain sufficient information to answer this query confidently.",
  "recommendations": [],
  "citations": [],
  "conflicts_surfaced": [],
  "evidence_gaps": ["Insufficient ARUP content indexed for this query"],
  "follow_up_questions": ["What specific condition or test are you looking for?"]
}
"""


# ── Evidence block builder ─────────────────────────────────────────────────────

def _build_evidence_block(bundle: dict) -> str:
    """Format a structured evidence bundle for LLM consumption."""
    lines: list[str] = []

    # ── Candidate tests summary (gives model the grouped view) ───────────────
    candidate_tests = bundle.get("candidate_tests", [])
    if candidate_tests:
        lines.append("=== CANDIDATE TESTS (grouped evidence summary) ===")
        for i, ct in enumerate(candidate_tests, 1):
            cs = ct.get("confidence_signals", {})
            oc = ct.get("ordering_constraints", {})
            lines.append(f"\nCandidate {i}: {ct['test_name']}")
            lines.append(
                f"  Evidence coverage — algorithm={cs.get('has_algorithm')}, "
                f"consult={cs.get('has_consult')}, "
                f"directory={cs.get('has_directory')}, "
                f"fact_sheet={cs.get('has_fact_sheet')}, "
                f"chunks={cs.get('chunk_count')}"
            )
            if oc.get("specimen"):
                lines.append(f"  Specimen: {oc['specimen']}")
            if oc.get("tat"):
                lines.append(f"  TAT: {oc['tat']}")

    # ── Conflicts ────────────────────────────────────────────────────────────
    conflicts = bundle.get("unresolved_conflicts", [])
    if conflicts:
        lines.append("\n=== UNRESOLVED CONFLICTS (must surface in answer) ===")
        for c in conflicts:
            lines.append(f"  ⚠ {c}")

    # ── Evidence gaps ────────────────────────────────────────────────────────
    missing = bundle.get("missing_context", [])
    if missing:
        lines.append("\n=== EVIDENCE GAPS (list in evidence_gaps field) ===")
        for m in missing:
            lines.append(f"  ! {m}")

    # ── Numbered sources (authoritative, for citation) ───────────────────────
    authority_sources = bundle.get("authority_sources", [])
    if authority_sources:
        lines.append("\n=== NUMBERED SOURCES — cite as [SOURCE N] ===")
        for i, chunk in enumerate(authority_sources[:14], start=1):
            lines.append(
                f"\n[SOURCE {i}]\n"
                f"Type: {chunk.get('source_type', 'General')} "
                f"({chunk.get('source_role', '')})\n"
                f"Document: {chunk.get('filename', 'Unknown')}\n"
                f"Relevance: {chunk.get('score', 0.0):.2f}\n"
                f"Content:\n{chunk.get('text', '').strip()[:600]}\n"
                "---"
            )

    # ── Context sources (supporting only, not primary citation) ─────────────
    context_sources = bundle.get("context_sources", [])
    if context_sources:
        lines.append("\n=== CONTEXT SOURCES (supporting context only) ===")
        for chunk in context_sources[:3]:
            lines.append(
                f"[Context] {chunk.get('source_type', 'General')} | "
                f"{chunk.get('filename', '')} | "
                f"{chunk.get('text', '').strip()[:200]}\n---"
            )

    return "\n".join(lines) if lines else "(No evidence retrieved)"


# ── Response validator ─────────────────────────────────────────────────────────

def _validate_response(result: dict, bundle: dict) -> dict:
    """
    Post-parse validation of the LLM-produced response against the evidence bundle.

    Checks performed:
      1. Citation numbers are sequential integers (1..N, no gaps, no duplicates)
      2. Duplicate citation entries (same number) are collapsed to the first occurrence
      3. source_refs in each recommendation are clamped to [1..N]
      4. Missing citation document names are back-filled from authority_sources
      5. Empty or whitespace-only excerpts are flagged

    All fixes are applied in-place.  Warnings are appended to evidence_gaps so
    the clinician can see that the LLM output was adjusted — this is intentional
    transparency for a clinical tool.

    Never raises — degrades gracefully.
    """
    warnings: list[str] = []

    authority_sources = bundle.get("authority_sources", [])
    max_source_n      = len(authority_sources)   # highest valid [SOURCE N] index

    citations: list[dict] = result.get("citations", [])

    # ── 1. Collapse duplicate citation numbers ────────────────────────────────
    seen_numbers: set[int] = set()
    deduped_citations: list[dict] = []
    for cite in citations:
        n = cite.get("number")
        if not isinstance(n, int):
            warnings.append(f"Citation has non-integer number ({n!r}) — dropped")
            continue
        if n in seen_numbers:
            warnings.append(f"Duplicate citation number {n} — second occurrence dropped")
            continue
        seen_numbers.add(n)
        deduped_citations.append(cite)
    result["citations"] = deduped_citations

    # ── 2. Back-fill missing document names from authority_sources ────────────
    for cite in result["citations"]:
        n = cite.get("number")
        if not cite.get("document") and isinstance(n, int) and 1 <= n <= max_source_n:
            cite["document"] = authority_sources[n - 1].get("filename", "Unknown")
            warnings.append(
                f"Citation {n} had no document name — filled from source: {cite['document']}"
            )

    # ── 3. Flag empty excerpts ────────────────────────────────────────────────
    for cite in result["citations"]:
        if not str(cite.get("excerpt", "")).strip():
            warnings.append(
                f"Citation {cite.get('number')} has an empty excerpt — "
                "model did not ground this citation in source text"
            )

    # ── 4. Clamp source_refs to valid citation numbers ────────────────────────
    valid_numbers = {c.get("number") for c in result["citations"]
                     if isinstance(c.get("number"), int)}

    for rec in result.get("recommendations", []):
        original_refs = rec.get("source_refs", [])
        valid_refs    = [r for r in original_refs if r in valid_numbers]
        invalid_refs  = [r for r in original_refs if r not in valid_numbers]
        if invalid_refs:
            warnings.append(
                f"Recommendation '{rec.get('test_name', '?')}' had invalid "
                f"source_refs {invalid_refs} — removed (valid range: {sorted(valid_numbers)})"
            )
        rec["source_refs"] = valid_refs

    # ── 5. Append warnings to evidence_gaps for transparency ─────────────────
    if warnings:
        existing = result.setdefault("evidence_gaps", [])
        for w in warnings:
            existing.append(f"[Validation] {w}")

    return result


# ── Response Agent ─────────────────────────────────────────────────────────────

class ResponseAgent:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self.model = os.environ.get(
            "CLAUDE_RESPONSE_MODEL",
            os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
        )

    async def generate(self, query: str, intent: dict, evidence_bundle: dict) -> dict:
        """
        Generate grounded recommendations from a structured evidence bundle.

        Parameters
        ----------
        query           : raw user query
        intent          : structured intent from PromptAgent
        evidence_bundle : structured bundle from EvidencePackager
        """
        evidence_block = _build_evidence_block(evidence_bundle)

        user_content = (
            f"Clinical query: {query}\n\n"
            f"Query intent: {intent.get('intent_type', 'unknown')} — "
            f"{intent.get('medical_context', '')}\n\n"
            f"ARUP EVIDENCE BUNDLE:\n\n{evidence_block}"
        )

        message = self.client.messages.create(
            model=self.model,
            max_tokens=3200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )

        raw = message.content[0].text.strip()

        # Strip markdown code fences if model wraps response
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0]

        try:
            result = json.loads(raw.strip())
        except json.JSONDecodeError:
            return {
                "answer": raw,
                "recommendations": [],
                "citations": [],
                "conflicts_surfaced": [],
                "evidence_gaps": [],
                "follow_up_questions": [],
            }

        # Ensure all expected fields are present
        result.setdefault("conflicts_surfaced", [])
        result.setdefault("evidence_gaps", [])
        result.setdefault("follow_up_questions", [])
        result.setdefault("citations", [])
        result.setdefault("recommendations", [])

        # ── Post-parse validation against evidence bundle ─────────────────────
        result = _validate_response(result, evidence_bundle)

        return result