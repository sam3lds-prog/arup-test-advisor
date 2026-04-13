"""
prompt_agent.py  v0.7.0
────────────────────────────────────────────────────────────────────────────
Prompt Agent — clinical query analyser

Changes from v0.6:
  • Clarification contract: exactly 0 or 1 question per turn (never a list)
  • New clarification JSON shape:
      "clarification": {
        "needed":      true | false,
        "question":    "...",
        "options":     [...],
        "reason":      "...",
        "question_id": "short_snake_case_identifier"
      }
  • analyze() now accepts clarification_state parameter (passed from main.py)
    so the prompt agent knows how many questions remain and what was last asked
  • Domain-specific question policy: endocrinology, infectious disease,
    coagulation, genetics, oncology, autoimmune, toxicology, general
  • Three-test rule enforced in prompt
  • Controlled abbreviation expansion retained from v0.6
  • All other fields (intent_type, search_queries, etc.) unchanged
"""

import os
import json
import anthropic
from typing import List, Optional, Dict


# ── Controlled abbreviation & synonym table ────────────────────────────────────

ABBREVIATION_MAP: Dict[str, List[str]] = {
    "TSH":    ["thyroid stimulating hormone", "thyrotropin"],
    "T3":     ["triiodothyronine"],
    "T4":     ["thyroxine", "tetraiodothyronine"],
    "FT4":    ["free thyroxine", "free T4"],
    "PTH":    ["parathyroid hormone", "parathormone"],
    "DHEA":   ["dehydroepiandrosterone"],
    "DHEAS":  ["dehydroepiandrosterone sulfate"],
    "FSH":    ["follicle stimulating hormone"],
    "LH":     ["luteinizing hormone"],
    "ACTH":   ["adrenocorticotropic hormone", "corticotropin"],
    "IGF":    ["insulin-like growth factor"],
    "HbA1c":  ["hemoglobin A1c", "glycated hemoglobin", "A1C"],
    "BNP":    ["brain natriuretic peptide", "B-type natriuretic peptide"],
    "NT-proBNP": ["N-terminal pro-BNP"],
    "CK":     ["creatine kinase", "creatine phosphokinase", "CPK"],
    "CK-MB":  ["creatine kinase MB fraction"],
    "hsTnI":  ["high-sensitivity troponin I"],
    "hsTnT":  ["high-sensitivity troponin T"],
    "ALT":    ["alanine aminotransferase", "SGPT"],
    "AST":    ["aspartate aminotransferase", "SGOT"],
    "GGT":    ["gamma-glutamyl transferase"],
    "ALP":    ["alkaline phosphatase"],
    "LDH":    ["lactate dehydrogenase"],
    "AFP":    ["alpha-fetoprotein"],
    "CEA":    ["carcinoembryonic antigen"],
    "BUN":    ["blood urea nitrogen", "urea nitrogen"],
    "GFR":    ["glomerular filtration rate"],
    "eGFR":   ["estimated GFR", "estimated glomerular filtration rate"],
    "Cr":     ["creatinine"],
    "CBC":    ["complete blood count", "full blood count", "FBC"],
    "WBC":    ["white blood cell count", "leukocyte count"],
    "RBC":    ["red blood cell count", "erythrocyte count"],
    "Hgb":    ["hemoglobin"],
    "Hct":    ["hematocrit"],
    "MCV":    ["mean corpuscular volume"],
    "MCH":    ["mean corpuscular hemoglobin"],
    "MCHC":   ["mean corpuscular hemoglobin concentration"],
    "PT":     ["prothrombin time"],
    "INR":    ["international normalized ratio"],
    "aPTT":   ["activated partial thromboplastin time", "PTT"],
    "ESR":    ["erythrocyte sedimentation rate", "sed rate"],
    "CRP":    ["C-reactive protein"],
    "HIV":    ["human immunodeficiency virus"],
    "HCV":    ["hepatitis C virus"],
    "HBV":    ["hepatitis B virus"],
    "HBsAg":  ["hepatitis B surface antigen"],
    "EBV":    ["Epstein-Barr virus", "mononucleosis"],
    "CMV":    ["cytomegalovirus"],
    "RPR":    ["rapid plasma reagin"],
    "VDRL":   ["venereal disease research laboratory"],
    "TPPA":   ["Treponema pallidum particle agglutination"],
    "ANA":    ["antinuclear antibody"],
    "ANCA":   ["antineutrophil cytoplasmic antibody"],
    "RF":     ["rheumatoid factor"],
    "dsDNA":  ["double-stranded DNA antibody", "anti-dsDNA"],
    "TPO":    ["thyroid peroxidase antibody"],
    "PSA":    ["prostate specific antigen"],
    "CA125":  ["cancer antigen 125"],
    "CA19-9": ["cancer antigen 19-9"],
    "25-OH-D":["25-hydroxyvitamin D", "vitamin D 25-hydroxy", "calcidiol"],
    "B12":    ["vitamin B12", "cobalamin"],
    "folate": ["folic acid", "vitamin B9"],
    "BRCA":   ["breast cancer gene", "BRCA1", "BRCA2"],
    "CFTR":   ["cystic fibrosis transmembrane conductance regulator"],
    "JAK2":   ["Janus kinase 2"],
    "BCR-ABL":["Philadelphia chromosome", "chronic myeloid leukaemia marker"],
}


def _expand_terms(text: str) -> List[str]:
    expansions: List[str] = []
    text_upper = text.upper()
    for abbrev, synonyms in ABBREVIATION_MAP.items():
        if abbrev.upper() in text_upper:
            expansions.extend(synonyms)
    return list(dict.fromkeys(expansions))[:10]


# ── System prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a clinical laboratory query analysis agent.

Your ONLY job: analyse incoming clinical queries and extract structured information
to guide retrieval from an ARUP Laboratories knowledge base.

ROLE BOUNDARIES (strictly enforced):
- You MAY: identify query intent, extract exact terms from the query, generate
  diverse search strings, flag missing information, and ask ONE clarification question.
- You MAY NOT: recommend tests, interpret results, or invent medical concepts
  not present in the query or supplied context.

─── TERM EXTRACTION RULES ───────────────────────────────────────────────────────
1. Extract terms EXACTLY as they appear in the query.
2. Do not invent synonyms — use only terms from the [TERM EXPANSIONS] block.
3. Use expansions to enrich search_queries, not to override the user's terms.

─── CLARIFICATION POLICY ────────────────────────────────────────────────────────
Ask exactly 0 or 1 clarification question per turn. NEVER return more than one.
A question is worth asking ONLY if ALL three tests pass:
  TEST 1 — The answer would change which ARUP test is recommended
  TEST 2 — The clinician can supply the answer in a single turn
  TEST 3 — The answer cannot be inferred from context, history, or current query

If any test fails: set clarification.needed = false and proceed with retrieval.

DOMAIN-SPECIFIC HIGHEST-VALUE QUESTIONS (ask the first applicable one only):

Endocrinology / Thyroid:
  1. Suspected new diagnosis vs treatment monitoring
  2. Adult vs pediatric patient
  3. Screening vs confirmatory testing

Infectious disease:
  1. Organism or pathogen suspected
  2. Acute/active infection vs past exposure/immunity
  3. Immunocompromised status (yes/no)
  4. Specimen source (if unusual)

Coagulation / Haemostasis:
  1. Bleeding workup vs thrombosis/clot workup
  2. Anticoagulant exposure (warfarin, heparin, DOAC)
  3. Acute event vs chronic/recurrent evaluation

Genetics / Molecular:
  1. Diagnostic testing vs carrier screening vs familial variant follow-up
  2. Affected individual vs unaffected relative
  3. Prenatal vs postnatal context

Oncology / Tumour markers:
  1. Diagnosis vs monitoring treatment response vs surveillance
  2. Cancer type confirmed vs suspected

Autoimmune / Rheumatology:
  1. Diagnosis vs monitoring established disease
  2. Specific suspected condition (e.g. SLE, RA, vasculitis)

Toxicology / Pharmacology:
  1. Clinical toxicity suspected vs therapeutic drug monitoring
  2. Substance or drug class involved
  3. Quantitative level needed vs qualitative screen

General (use only if no domain applies):
  1. Screening vs diagnostic vs monitoring context

─── SESSION CLARIFICATION STATE ─────────────────────────────────────────────────
If a [CLARIFICATION STATE] block is provided:
- Do NOT re-ask a question with the same question_id as last_question_id
- Do NOT ask if questions_asked_count is already at or above the cap shown
- Treat pending_question as the last question that was asked and is now being answered
  by the current user message

─── CONTEXT USAGE ───────────────────────────────────────────────────────────────
If a CLINICAL CONTEXT block is provided:
- Avoid suggesting tests already in tests_ordered
- Carry forward confirmed_clarifications — do NOT re-ask those variables
- Use primary_concern to sharpen search queries

If PRIOR SESSIONS are provided:
- Recognise recurring scenarios (do NOT treat as current context unless relevant)

─── OUTPUT SCHEMA ───────────────────────────────────────────────────────────────
Respond ONLY with valid JSON — no prose, no markdown fences.

{
  "intent_type": "disease_to_test" | "specimen_question" | "test_lookup" | "interpretation" | "monitoring" | "ambiguous",
  "clinical_concepts": ["exact terms from query, plus expansion terms"],
  "conditions": ["disease or condition names as typed"],
  "tests_mentioned": ["any test names or codes explicitly in the query"],
  "medical_context": "1–2 sentence plain-English summary of this query",
  "competitor_normalization": ["brand/external name → ARUP equivalent hint, if any"],
  "search_queries": ["3–5 diverse short search strings for vector retrieval"],
  "clarification": {
    "needed": true | false,
    "question": "Single question text shown to user, or empty string if needed=false",
    "options": ["Option A", "Option B", "Option C"],
    "reason": "One sentence: why this question changes test selection, or empty string",
    "question_id": "short_snake_case_id e.g. suspected_vs_monitoring, or empty string"
  },
  "missing_info": ["additional context that would further improve recommendations"],
  "clarification_questions": []
}

Note: always include "clarification_questions": [] for backward compatibility.
"""


# ── Formatters ─────────────────────────────────────────────────────────────────

def _format_confirmed_clarifications(items) -> str:
    """
    Safely format confirmed_clarifications for prompt injection.

    Accepts both the legacy list[str] shape and the current list[dict] shape:
        {"question": str, "answer": str}

    Returns a comma-joined human-readable string, e.g.:
        "Suspected vs monitoring → Follow-up, Patient age → 45"
    Returns "" when the list is empty or unrecognisable.
    """
    if not items:
        return ""
    parts = []
    for item in items:
        if isinstance(item, dict):
            q = str(item.get("question") or "").strip()
            a = str(item.get("answer")   or "").strip()
            if q and a:
                parts.append(f"{q} \u2192 {a}")
            elif a:
                parts.append(a)
            elif q:
                parts.append(q)
        elif isinstance(item, str) and item.strip():
            parts.append(item.strip())
        # None / malformed entries skipped silently
    return ", ".join(parts)


def _format_clinical_context(context: Optional[Dict]) -> str:
    if not context:
        return ""
    patient = context.get("patient", {})
    session = context.get("session", {})
    lines   = ["--- Structured Clinical Context ---"]

    if any([patient.get("age"), patient.get("sex"),
            patient.get("relevant_conditions"), patient.get("notes")]):
        lines.append("Patient:")
        if patient.get("age"):
            lines.append(f"  Age: {patient['age']}")
        if patient.get("sex"):
            lines.append(f"  Sex: {patient['sex']}")
        if patient.get("relevant_conditions"):
            lines.append(f"  Conditions: {', '.join(patient['relevant_conditions'])}")
        if patient.get("current_medications"):
            lines.append(f"  Medications: {', '.join(patient['current_medications'])}")
        if patient.get("notes"):
            lines.append(f"  Notes: {patient['notes']}")

    if session.get("primary_concern"):
        lines.append(f"Primary concern: {session['primary_concern']}")
    if session.get("tests_ordered"):
        lines.append(f"Tests ordered this session: {', '.join(session['tests_ordered'])}")
    if session.get("tests_ruled_out"):
        lines.append(f"Tests ruled out: {', '.join(session['tests_ruled_out'])}")
    confirmed_str = _format_confirmed_clarifications(session.get("confirmed_clarifications"))
    if confirmed_str:
        lines.append(f"Already confirmed (do NOT re-ask): {confirmed_str}")
    if session.get("open_questions"):
        lines.append(f"Open questions: {', '.join(session['open_questions'])}")

    if len(lines) == 1:
        return ""
    lines.append("--- End Clinical Context ---")
    return "\n".join(lines)


def _format_clarification_state(cs: Optional[Dict]) -> str:
    """
    Format the clarification_state block so the LLM knows what's been asked
    and how many questions remain.
    """
    if not cs:
        return ""
    asked   = cs.get("questions_asked_count", 0)
    cap     = 3   # matches MAX_CLARIFICATION_QUESTIONS
    last_id = cs.get("last_question_id")
    pending = cs.get("pending_question")
    hard_stop = cs.get("hard_stop", False)

    if asked == 0 and not pending:
        return ""   # nothing to report

    lines = ["[CLARIFICATION STATE]"]
    lines.append(f"  Questions asked this session: {asked} of {cap}")
    if last_id:
        lines.append(f"  Last question asked (do NOT re-ask): {last_id}")
    if pending:
        lines.append(f"  Pending question (user is answering this now): {pending}")
    if hard_stop:
        lines.append("  Hard stop active — safety blocker, cap bypassed")
    elif asked >= cap:
        lines.append("  Cap reached — set clarification.needed=false")
    lines.append("[End Clarification State]")
    return "\n".join(lines)


def _format_prior_sessions(prior_sessions: List[Dict]) -> str:
    if not prior_sessions:
        return ""
    lines = ["--- Prior Session Summaries ---"]
    for s in prior_sessions:
        date    = (s.get("started_at") or "")[:10]
        summary = s.get("summary", "").strip()
        turns   = s.get("turn_count", 0)
        if summary:
            lines.append(f"[{date} · {turns} turns] {summary}")
    if len(lines) == 1:
        return ""
    lines.append("--- End Prior Sessions ---")
    return "\n".join(lines)


def _format_history(history: list) -> str:
    if not history:
        return ""
    recent = history[-10:]
    lines  = ["--- Conversation History (most recent last) ---"]
    for msg in recent:
        role    = "Clinician" if msg.get("role") == "user" else "Advisor"
        content = msg.get("content", "")
        if role == "Advisor" and len(content) > 400:
            try:
                parsed = json.loads(content)
                recs   = parsed.get("recommendations", [])
                answer = parsed.get("answer", "")
                if recs:
                    names   = ", ".join(r.get("test_name", "") for r in recs[:3])
                    content = f"[Recommended: {names}] {answer[:200]}"
                else:
                    content = answer[:300]
            except Exception:
                content = content[:300] + "..."
        lines.append(f"{role}: {content.strip()}")
    lines.append("--- End of History ---")
    return "\n".join(lines)


# ── Prompt Agent ───────────────────────────────────────────────────────────────

class PromptAgent:
    def __init__(self):
        self.client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            timeout=60.0,
            max_retries=3,
        )
        self.model = os.environ.get(
            "CLAUDE_PROMPT_MODEL",
            os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
        )

    async def analyze(
        self,
        query: str,
        history: list,
        enriched_concepts: Optional[List[str]] = None,
        clinical_context: Optional[Dict] = None,
        prior_sessions: Optional[List[Dict]] = None,
        clarification_state: Optional[Dict] = None,   # v0.7: passed from main.py
    ) -> dict:
        """
        Analyse a clinical query and return structured intent JSON.

        clarification_state is injected from session storage so the LLM
        knows exactly what has been asked and how many questions remain.
        """
        context_block   = _format_clinical_context(clinical_context)
        clar_state_block = _format_clarification_state(clarification_state)
        sessions_block  = _format_prior_sessions(prior_sessions or [])
        history_block   = _format_history(history)

        # Controlled expansion from abbreviation map + optional BioPortal terms
        controlled_expansions = _expand_terms(query)
        all_expansions = list(dict.fromkeys(
            controlled_expansions + (enriched_concepts or [])
        ))[:20]

        enrichment_block = ""
        if all_expansions:
            enrichment_block = (
                "\n\n[TERM EXPANSIONS — use to enrich search_queries only]\n"
                + ", ".join(all_expansions)
                + "\n[End Term Expansions]"
            )

        parts = [f"Clinical query: {query}"]
        if clar_state_block:
            parts.append(clar_state_block)
        if context_block:
            parts.append(context_block)
        if sessions_block:
            parts.append(sessions_block)
        if history_block:
            parts.append(history_block)
        if enrichment_block:
            parts.append(enrichment_block)

        user_content = "\n\n".join(parts)

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=1200,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
        except anthropic.APITimeoutError:
            return _fallback(query, "APITimeoutError")
        except anthropic.APIConnectionError:
            return _fallback(query, "APIConnectionError")
        except anthropic.RateLimitError:
            return _fallback(query, "RateLimitError")
        except Exception as e:
            return _fallback(query, str(e))

        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0]

        try:
            result = json.loads(raw.strip())
        except json.JSONDecodeError:
            return _fallback(query, "JSONDecodeError")

        result.setdefault("medical_context", "")
        result.setdefault("missing_info", [])
        result.setdefault("competitor_normalization", [])
        result.setdefault("clarification_questions", [])   # backward compat

        # Normalise clarification to the single-object shape
        result["clarification"] = _normalise_clarification(result)

        return result


def _normalise_clarification(result: dict) -> dict:
    """
    Accept either the new clarification object or the old clarification_questions
    array and return a normalised single-question object.
    """
    empty = {
        "needed":      False,
        "question":    "",
        "options":     [],
        "reason":      "",
        "question_id": "",
    }

    # New shape already present
    if isinstance(result.get("clarification"), dict):
        c = result["clarification"]
        c.setdefault("needed",      False)
        c.setdefault("question",    "")
        c.setdefault("options",     [])
        c.setdefault("reason",      "")
        c.setdefault("question_id", "")
        # Validate: needed=True requires a non-empty question with at least 2 options
        if c["needed"] and (not c["question"].strip() or len(c.get("options", [])) < 2):
            c["needed"] = False
        return c

    # Old array shape — take only the first valid entry
    old_list = result.get("clarification_questions", [])
    needs    = result.get("needs_clarification", False)

    if needs and isinstance(old_list, list) and old_list:
        first = old_list[0]
        if isinstance(first, dict) and first.get("question") and len(first.get("options", [])) >= 2:
            return {
                "needed":      True,
                "question":    first["question"],
                "options":     first["options"],
                "reason":      "",
                "question_id": "",
            }

    return empty


def _fallback(query: str, reason: str) -> dict:
    return {
        "intent_type":              "disease_to_test",
        "clinical_concepts":        [query],
        "conditions":               [],
        "tests_mentioned":          [],
        "medical_context":          f"[Fallback — {reason}] Direct keyword search.",
        "competitor_normalization": [],
        "search_queries":           [query],
        "clarification": {
            "needed":      False,
            "question":    "",
            "options":     [],
            "reason":      "",
            "question_id": "",
        },
        "clarification_questions": [],
        "missing_info":            [],
    }