"""
processor.py
────────────────────────────────────────────────────────────────────────────
Document Processor — Ingestion & Chunking Pipeline

Converts raw ARUP Laboratories files (JSON, PDF, CSV) into text chunks
suitable for storage in the VectorStore (ChromaDB). Each chunk carries
structured metadata so the RetrievalAgent and EvidencePackager can filter
and group results by source type.

Source-type detection (multi-signal, priority ordered):
  1. JSON `entity_type` field          — authoritative when present
  2. `json_index` prefix               — e.g. "algorithm_*", "fact_sheet_*"
  3. Parent folder name                — e.g. algorithms/, fact_sheets/
  4. Filename keywords                 — e.g. "algorithm", "factsheet"
  5. JSON structural key scoring       — fallback heuristic

Supported source types:
  Algorithm      — diagnostic decision-tree documents (nodes + edges JSON or PDF)
  Fact Sheet     — lab test interpretation and caveat documents
  Consult Topic  — clinical guidance and disease-specific test rationale
  Test Directory — order codes, specimen requirements, turnaround times
  General        — unclassified or mixed content

Supported file formats:
  .json  — ARUP structured exports (algorithms, fact sheets, consult topics,
            test directory entries)
  .pdf   — algorithm PDFs and other clinical reference PDFs
  .csv   — test directory exports

Key outputs per chunk:
  text         : plain-text content for embedding
  source_type  : classified document category
  filename     : original file name
  chunk_index  : position within the document
  test_id      : stable numeric/string test identifier (Test Directory only)
  test_name    : human-readable test name
  source_url   : originating ARUP URL (when available)
  chunk_type   : "algorithm_graph" for graph payload chunks (AlgorithmRenderer)
"""

import io
import os
import re
import json
from collections import deque
from typing import Any, List, Dict, Optional


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE-TYPE DETECTION  (multi-signal, priority ordered)
# ─────────────────────────────────────────────────────────────────────────────

# Signal 1 — JSON entity_type field (authoritative)
# Real values observed in ARUP JSON files:
#   algorithm          → acromegaly-testing-algorithm.json
#   fact_sheet         → acute-lymphoblastic-leukemia-fish-panels.json
#   topic              → acromegaly.json   *** "topic" NOT "consult_topic" ***
#   (test directory JSONs have NO entity_type field at all)
ENTITY_TYPE_MAP = {
    "algorithm":      "Algorithm",
    "fact_sheet":     "Fact Sheet",
    "consult_topic":  "Consult Topic",   # keep for forward-compat
    "topic":          "Consult Topic",   # ← real value in current ARUP dumps
    "test":           "Test Directory",
    "test_directory": "Test Directory",
}

# Signal 2 — json_index prefix  (all ARUP JSONs carry this field)
# Observed patterns:
#   "algorithm_acromegaly_testing_algorithm"
#   "fact_sheet_acute_lymphoblastic_leukemia_fish_panels"
#   "topic_acromegaly"
JSON_INDEX_PREFIX_MAP = {
    "algorithm_":  "Algorithm",
    "fact_sheet_": "Fact Sheet",
    "topic_":      "Consult Topic",
}

# Signal 3 — folder/path name
FOLDER_SIGNALS = [
    (["algorithm", "algorithms", "algo"],                  "Algorithm"),
    (["fact_sheet", "fact_sheets", "factsheet", "facts"],  "Fact Sheet"),
    (["consult", "consult_topic", "topics", "disease"],    "Consult Topic"),
    (["test_directory", "test_dir", "tests", "directory"], "Test Directory"),
]

# Signal 4 — filename keywords
FILENAME_SIGNALS = [
    (["algorithm", "algo", "flowchart", "pathway"],        "Algorithm"),
    (["fact_sheet", "factsheet", "fact-sheet"],            "Fact Sheet"),
    (["consult", "topic", "disease"],                      "Consult Topic"),
    (["directory", "catalog", "catalogue", "test_dir"],    "Test Directory"),
]

# Signal 5 — JSON structural key scoring
JSON_STRUCTURE_SCORES: Dict[str, Dict[str, int]] = {
    "Algorithm": {
        "nodes": 15, "edges": 15, "root_node_id": 10,
        "node_map": 8, "steps": 5,
    },
    "Fact Sheet": {
        "featured_tests": 12, "sections": 8, "analytes": 8,
        "interpretation": 6, "limitations": 6,
    },
    "Consult Topic": {
        "related_algorithms": 12, "page_type": 10,
        "body": 5, "summary": 6, "differential": 8,
        "clinical_presentation": 8, "related_topics": 5,
    },
    # Test directory JSONs (like 3016635.json) have NO entity_type — detect by structure
    "Test Directory": {
        "test_id": 20, "raw_text_for_embedding": 15,
        "test_name": 10, "details": 10,
        "test_code": 10, "loinc_codes": 8,
    },
}

# Signal 6 — details subkeys (for test directory JSONs where details is a nested dict)
TEST_DIRECTORY_DETAIL_KEYS = {
    "specimen_type", "loinc_codes", "turnaround_time", "methodology",
    "ordering_recommendation", "stability", "collect", "cpt_codes",
    "reference_interval", "performed_days", "aliases", "components",
}

# Signal 7 — full-text keyword scoring
SOURCE_TYPE_KEYWORDS = {
    "Algorithm":    ["algorithm", "flowchart", "diagnostic pathway", "step-by-step"],
    "Consult Topic":["consult", "disease topic", "clinical presentation", "differential"],
    "Fact Sheet":   ["fact sheet", "factsheet", "interpretation", "limitation", "analytical"],
    "Test Directory":["directory", "catalog", "catalogue", "specimen", "collection",
                      "stability", "tat", "turnaround", "order code"],
}


def _score_json_structure(data: dict) -> Optional[str]:
    """Score top-level keys + url patterns. Returns best type or None."""
    keys = {k.lower() for k in data.keys()}

    # Fast-path: test directory signature — has test_id OR (url pointing to ltd.aruplab.com)
    url = str(data.get("url", ""))
    if "test_id" in keys or "ltd.aruplab.com/tests/pub" in url.lower():
        return "Test Directory"

    # Check if details subkeys look like test directory
    details = data.get("details", {})
    if isinstance(details, dict):
        detail_keys = {k.lower() for k in details.keys()}
        if len(detail_keys & TEST_DIRECTORY_DETAIL_KEYS) >= 3:
            return "Test Directory"

    # Score remaining types
    scores: Dict[str, int] = {st: 0 for st in ENTITY_TYPE_MAP.values()}
    for source_type, kw_weights in JSON_STRUCTURE_SCORES.items():
        for key, weight in kw_weights.items():
            if key in keys:
                scores[source_type] = scores.get(source_type, 0) + weight

    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] >= 8 else None


def detect_source_type(filename: str, text_preview: str) -> str:
    """Legacy keyword-based detector (kept for CSV/TXT/PDF fallback)."""
    name    = filename.lower()
    preview = text_preview.lower()
    for source_type, keywords in SOURCE_TYPE_KEYWORDS.items():
        if any(kw in name for kw in keywords):
            return source_type
    for source_type, keywords in SOURCE_TYPE_KEYWORDS.items():
        if any(kw in preview for kw in keywords):
            return source_type
    return "General"


def classify_document(
    filename: str,
    content_preview: str = "",
    json_data: Optional[dict] = None,
    folder_hint: str = "",
) -> str:
    """
    Multi-signal classifier.  Priority:
      1. JSON entity_type field         (authoritative)
      2. JSON json_index prefix         ("algorithm_*", "fact_sheet_*", "topic_*")
      3. JSON structural key analysis   (test_id, nodes/edges, featured_tests, …)
      4. Folder / path name             (strong naming convention signal)
      5. Filename keywords
      6. Full-text keyword scoring
    """
    if json_data and isinstance(json_data, dict):

        # 1. entity_type
        et = json_data.get("entity_type", "").lower().strip()
        if et in ENTITY_TYPE_MAP:
            return ENTITY_TYPE_MAP[et]

        # 2. json_index prefix
        ji = json_data.get("json_index", "").lower()
        for prefix, source_type in JSON_INDEX_PREFIX_MAP.items():
            if ji.startswith(prefix):
                return source_type

        # 3. JSON structural analysis
        st = _score_json_structure(json_data)
        if st:
            return st

    # 4. Folder signal
    folder_lower = folder_hint.lower()
    for keywords, source_type in FOLDER_SIGNALS:
        if any(kw in folder_lower for kw in keywords):
            return source_type

    # 5. Filename
    name_lower = os.path.splitext(os.path.basename(filename))[0].lower()
    for keywords, source_type in FILENAME_SIGNALS:
        if any(kw in name_lower for kw in keywords):
            return source_type

    # 6. Full-text scoring
    if content_preview:
        return detect_source_type(filename, content_preview)

    return "General"


# ─────────────────────────────────────────────────────────────────────────────
# TEXT HELPERS
# ─────────────────────────────────────────────────────────────────────────────

CHUNK_TARGET  = 1200
CHUNK_OVERLAP = 150


def _strip_html(html: str) -> str:
    """Remove HTML tags and normalise whitespace."""
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _text_to_chunks(text: str, filename: str, source_type: str) -> List[Dict]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[Dict] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) > CHUNK_TARGET and current:
            chunks.append({"text": current.strip(), "filename": filename,
                           "source_type": source_type, "chunk_index": len(chunks)})
            current = current[-CHUNK_OVERLAP:] + "\n\n" + para
        else:
            current += ("\n\n" if current else "") + para
    if current.strip():
        chunks.append({"text": current.strip(), "filename": filename,
                       "source_type": source_type, "chunk_index": len(chunks)})
    return chunks


def _flatten(value: Any, prefix: str = "") -> List[str]:
    lines: List[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            label = f"{prefix}.{k}" if prefix else k
            lines.extend(_flatten(v, label))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            lines.extend(_flatten(item, f"{prefix}[{i}]" if prefix else f"[{i}]"))
    else:
        if value not in (None, "", [], {}):
            lines.append(f"{prefix}: {value}")
    return lines


# ─────────────────────────────────────────────────────────────────────────────
# ARUP ALGORITHM PROCESSOR  (unchanged from original)
# ─────────────────────────────────────────────────────────────────────────────

def _build_node_map(nodes: List[Dict]) -> Dict[str, Dict]:
    return {n["id"]: n for n in nodes}


def _build_adjacency(edges: List[Dict]) -> Dict[str, List[str]]:
    adj: Dict[str, List[str]] = {}
    for e in edges:
        adj.setdefault(e["from"], []).append(e["to"])
    return adj


def _node_text(node: Dict) -> str:
    """Combine title + body into clean text for a single algorithm node."""
    parts = []
    title = (node.get("title") or "").strip()
    body  = (node.get("body")  or "").strip()
    if title:
        parts.append(title)
    if body and body not in title:
        parts.append(body)
    return "\n".join(parts)


# Node types that carry clinical meaning (skip pure structural / copyright nodes)
MEANINGFUL_NODE_TYPES = {"action", "decision", "outcome", "info", "start"}
SKIP_TITLE_PREFIXES   = ("© ", "References", "Click here for topics")


def _is_meaningful(node: Dict) -> bool:
    if node.get("type") not in MEANINGFUL_NODE_TYPES:
        return False
    title = (node.get("title") or "").strip()
    return not any(title.startswith(p) for p in SKIP_TITLE_PREFIXES)


def _process_algorithm(data: Dict, filename: str) -> List[Dict]:
    """
    Convert an ARUP algorithm JSON into searchable chunks.

    Produces:
      1. A header chunk with title + algorithm overview
      2. A flow narrative chunk reconstructed by BFS traversal of nodes/edges
      3. A dedicated orderable-tests chunk (highly retrievable for test queries)
      4. Abbreviations/footnotes chunk (if present)
    """
    source_type   = "Algorithm"
    title         = data.get("title", filename)
    source_url    = data.get("source_url", "")
    nodes         = data.get("nodes", [])
    edges         = data.get("edges", [])
    tests         = data.get("tests", [])
    footnotes     = data.get("footnotes", [])
    abbreviations = data.get("abbreviations", [])
    root_id       = data.get("root_node_id", nodes[0]["id"] if nodes else None)

    chunks: List[Dict] = []
    base = {"filename": filename, "source_type": source_type}

    # ── Chunk 1: header / overview ────────────────────────────────────────────
    header_lines = [
        f"Algorithm: {title}",
        f"Source: {source_url}" if source_url else "",
        f"Total algorithm steps: {len(nodes)}",
        f"Orderable ARUP tests referenced: {len(tests)}",
    ]
    chunks.append({**base,
                   "text": "\n".join(l for l in header_lines if l),
                   "chunk_index": 0})

    # ── Chunk 2: BFS flow narrative ───────────────────────────────────────────
    node_map = _build_node_map(nodes)
    adj      = _build_adjacency(edges)

    visited:    set       = set()
    flow_lines: List[str] = [f"Algorithm flow: {title}", ""]

    queue = deque([root_id]) if root_id and root_id in node_map else deque()
    if not queue:
        targets = {e["to"] for e in edges}
        roots   = [n["id"] for n in nodes if n["id"] not in targets]
        queue.extend(roots[:1])

    step = 1
    while queue:
        nid = queue.popleft()
        if nid in visited or nid not in node_map:
            continue
        visited.add(nid)
        node = node_map[nid]

        if _is_meaningful(node):
            node_type = node.get("type", "").upper()
            text      = _node_text(node)
            if text.strip():
                flow_lines.append(f"Step {step} [{node_type}]: {text}")
                step += 1

        for child_id in adj.get(nid, []):
            if child_id not in visited:
                queue.append(child_id)

    # Add any unvisited meaningful nodes (disconnected sub-graphs)
    for node in nodes:
        if node["id"] not in visited and _is_meaningful(node):
            text = _node_text(node)
            if text.strip():
                flow_lines.append(f"[{node.get('type','').upper()}]: {text}")

    flow_text = "\n".join(flow_lines)
    for c in _text_to_chunks(flow_text, filename, source_type):
        c["chunk_index"] = len(chunks)
        chunks.append(c)

    # ── Chunk 3: orderable ARUP tests ─────────────────────────────────────────
    if tests:
        test_lines = [f"Orderable ARUP tests referenced in: {title}", ""]
        for t in tests:
            num  = t.get("test_number", "")
            name = t.get("test_name", "").replace(num, "").strip().rstrip(",")
            url  = t.get("test_url", "")
            meth = t.get("methodology") or ""
            sub  = t.get("subsection") or ""
            line = f"Test code: {num} | Name: {name}"
            if meth:
                line += f" | Methodology: {meth}"
            if sub:
                line += f" | Section: {sub}"
            if url:
                line += f" | URL: {url}"
            test_lines.append(line)
        chunks.append({**base,
                       "text": "\n".join(test_lines),
                       "chunk_index": len(chunks)})

    # ── Chunk 4: footnotes + abbreviations ────────────────────────────────────
    extras: List[str] = []
    if abbreviations:
        extras.append(f"Abbreviations used in {title}:")
        for ab in abbreviations:
            if isinstance(ab, dict):
                extras.append(f"  {ab.get('abbr','')}: {ab.get('definition','')}")
            else:
                extras.append(f"  {ab}")
    if footnotes:
        extras.append(f"\nFootnotes for {title}:")
        for fn in footnotes:
            extras.append(f"  {fn}" if isinstance(fn, str) else f"  {json.dumps(fn)}")
    if extras:
        chunks.append({**base,
                       "text": "\n".join(extras),
                       "chunk_index": len(chunks)})

    # ── Chunk 5: algorithm graph structure (for AlgorithmRenderer) ────────────
    # Serialises the raw node/edge graph as compact JSON into a dedicated chunk.
    # This chunk is retrieved by metadata lookup (not semantic search) when the
    # frontend requests a flowchart visualisation for this algorithm.
    # chunk_type="algorithm_graph" is stored as metadata by store.py so it can
    # be filtered without scanning the full collection.
    try:
        graph_payload = {
            "title":        title,
            "source_url":   source_url,
            "root_node_id": root_id,
            "nodes": [
                {
                    "id":    n.get("id"),
                    "type":  n.get("type"),
                    "title": (n.get("title") or "")[:300],
                    "body":  (n.get("body")  or "")[:500],
                }
                for n in nodes[:250]          # cap at 250 nodes
            ],
            "edges": [
                {
                    "from":  e.get("from"),
                    "to":    e.get("to"),
                    "label": (e.get("label") or ""),
                }
                for e in edges[:500]          # cap at 500 edges
            ],
            "tests": [
                {
                    "test_number": t.get("test_number", ""),
                    "test_name":   t.get("test_name", ""),
                    "test_url":    t.get("test_url", ""),
                    "subsection":  t.get("subsection", ""),
                }
                for t in tests[:75]
            ],
        }
        graph_text = "ALGORITHM_GRAPH_DATA:" + json.dumps(graph_payload, ensure_ascii=False, separators=(',', ':'))
        chunks.append({
            **base,
            "text":       graph_text,
            "chunk_type": "algorithm_graph",   # stored as metadata key in store.py
            "chunk_index": len(chunks),
        })
    except Exception:
        pass   # graph chunk is optional — never let it fail the upload

    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# ARUP FACT SHEET PROCESSOR  (unchanged from original)
# ─────────────────────────────────────────────────────────────────────────────

# Sections to skip (low clinical value for retrieval)
SKIP_SECTION_IDS = {"feedback", "references"}


def _process_fact_sheet(data: Dict, filename: str) -> List[Dict]:
    """
    Convert an ARUP fact sheet JSON into searchable chunks.

    Produces:
      1. One chunk per meaningful section (HTML stripped, grouped if short)
      2. A dedicated featured-tests chunk
    """
    source_type = "Fact Sheet"
    title       = data.get("title", filename)
    source_url  = data.get("source_url", "")
    sections    = data.get("sections", [])
    tests       = data.get("featured_tests", [])

    chunks: List[Dict] = []
    base = {"filename": filename, "source_type": source_type}

    # ── Header chunk ──────────────────────────────────────────────────────────
    chunks.append({**base,
                   "text": "\n".join(filter(None, [
                       f"Fact Sheet: {title}",
                       f"Source: {source_url}" if source_url else "",
                       f"Sections: {len(sections)} | Featured tests: {len(tests)}",
                   ])),
                   "chunk_index": 0})

    # ── Section chunks ────────────────────────────────────────────────────────
    current_text     = ""
    current_sections: List[str] = []

    for sec in sections:
        sec_id    = sec.get("id", "")
        sec_title = sec.get("title", "").strip()
        body_html = sec.get("body_html", "") or ""

        if sec_id in SKIP_SECTION_IDS:
            continue

        body = _strip_html(body_html)
        if not body and not sec_title:
            continue

        section_block = f"--- {sec_title} ---\n{body}" if body else f"--- {sec_title} ---"

        # Group short sections; flush when target size reached
        if len(current_text) + len(section_block) > CHUNK_TARGET and current_text:
            chunks.append({**base,
                           "text": f"From fact sheet: {title}\n\n{current_text.strip()}",
                           "section_ids": ", ".join(current_sections),
                           "chunk_index": len(chunks)})
            current_text     = ""
            current_sections = []

        current_text     += ("\n\n" if current_text else "") + section_block
        current_sections.append(sec_id)

    # Flush remainder
    if current_text.strip():
        chunks.append({**base,
                       "text": f"From fact sheet: {title}\n\n{current_text.strip()}",
                       "section_ids": ", ".join(current_sections),
                       "chunk_index": len(chunks)})

    # ── Featured tests chunk ──────────────────────────────────────────────────
    if tests:
        test_lines = [f"Featured ARUP tests in fact sheet: {title}", ""]
        for t in tests:
            num  = t.get("test_number", "")
            name = t.get("test_name", "").replace(num, "").strip().rstrip(",")
            url  = t.get("test_url", "")
            meth = t.get("methodology") or ""
            sub  = t.get("subsection") or ""
            line = f"Test code: {num} | Name: {name}"
            if meth:
                line += f" | Methodology: {meth}"
            if sub:
                line += f" | Section: {sub}"
            if url:
                line += f" | URL: {url}"
            test_lines.append(line)
        chunks.append({**base,
                       "text": "\n".join(test_lines),
                       "chunk_index": len(chunks)})

    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# ARUP CONSULT TOPIC PROCESSOR  (new — was a stub / fell to generic in v1)
# ─────────────────────────────────────────────────────────────────────────────

# Sections with low retrieval value — skip body but keep title as a label
SKIP_TOPIC_SECTION_IDS = {
    "cite-this-page", "feedback", "references",
    "related-information-from-arup-laboratories", "topics-from-arup-consult",
}

# Sections whose body content is highest-value for retrieval
HIGH_VALUE_TOPIC_SECTIONS = {
    "indications-for-testing", "laboratory-testing", "differential-diagnosis",
    "clinical-presentation", "background", "etiology", "pathophysiology",
    "monitoring", "arup-laboratory-tests",
}


def _process_consult_topic(data: Dict, filename: str) -> List[Dict]:
    """
    Convert an ARUP Consult Topic JSON (entity_type: "topic") into searchable chunks.

    Observed structure (acromegaly.json):
      title, slug, source_url, page_type, entity_type ("topic"),
      sections (list with id, title, body_html),
      tests (list with test_number, test_name, relationship_type),
      related_algorithms (list with title, url, slug)

    Produces:
      1. Header chunk — title, source, page_type
      2. One chunk per meaningful section (HTML stripped, grouped if short)
      3. ARUP tests chunk (ordered by relationship_type: primary first)
      4. Related algorithms cross-link chunk
    """
    source_type       = "Consult Topic"
    title             = data.get("title", filename)
    source_url        = data.get("source_url", "")
    page_type         = data.get("page_type", "")
    sections          = data.get("sections", [])
    tests             = data.get("tests", [])
    related_algos     = data.get("related_algorithms", [])

    chunks: List[Dict] = []
    base = {"filename": filename, "source_type": source_type}

    # ── Chunk 1: header ───────────────────────────────────────────────────────
    header_lines = [
        f"Consult Topic: {title}",
        f"Source: {source_url}" if source_url else "",
        f"Page type: {page_type}" if page_type else "",
        f"Tests: {len(tests)} | Sections: {len(sections)}",
    ]
    chunks.append({**base,
                   "text": "\n".join(l for l in header_lines if l),
                   "chunk_index": 0})

    # ── Chunk 2+: section content ─────────────────────────────────────────────
    current_text     = ""
    current_sections: List[str] = []

    for sec in sections:
        sec_id    = sec.get("id", "")
        sec_title = sec.get("title", "").strip()
        body_html = sec.get("body_html", "") or ""

        if sec_id in SKIP_TOPIC_SECTION_IDS:
            continue

        body = _strip_html(body_html)
        if not body and not sec_title:
            continue

        section_block = f"--- {sec_title} ---\n{body}" if body else f"--- {sec_title} ---"

        # High-value sections get their own chunk
        if sec_id in HIGH_VALUE_TOPIC_SECTIONS and body:
            if current_text:
                chunks.append({**base,
                               "text": f"From consult topic: {title}\n\n{current_text.strip()}",
                               "section_ids": ", ".join(current_sections),
                               "chunk_index": len(chunks)})
                current_text     = ""
                current_sections = []
            # Emit this high-value section immediately (possibly split if very long)
            for c in _text_to_chunks(
                f"From consult topic: {title}\n\n{section_block}", filename, source_type
            ):
                c["chunk_index"] = len(chunks)
                chunks.append(c)
            continue

        # Group normal sections
        if len(current_text) + len(section_block) > CHUNK_TARGET and current_text:
            chunks.append({**base,
                           "text": f"From consult topic: {title}\n\n{current_text.strip()}",
                           "section_ids": ", ".join(current_sections),
                           "chunk_index": len(chunks)})
            current_text     = ""
            current_sections = []

        current_text     += ("\n\n" if current_text else "") + section_block
        current_sections.append(sec_id)

    if current_text.strip():
        chunks.append({**base,
                       "text": f"From consult topic: {title}\n\n{current_text.strip()}",
                       "section_ids": ", ".join(current_sections),
                       "chunk_index": len(chunks)})

    # ── Chunk: ARUP tests (primary first) ────────────────────────────────────
    if tests:
        # Sort: primary → secondary → others
        def _rel_rank(t):
            rt = (t.get("relationship_type") or "").lower()
            return 0 if rt == "primary" else (1 if rt == "secondary" else 2)

        test_lines = [f"ARUP tests for consult topic: {title}", ""]
        for t in sorted(tests, key=_rel_rank):
            num  = t.get("test_number", "")
            name = t.get("test_name", "")
            url  = t.get("test_url", "")
            rel  = t.get("relationship_type", "")
            line = f"Test code: {num} | Name: {name}"
            if rel:
                line += f" | Type: {rel}"
            if url:
                line += f" | URL: {url}"
            test_lines.append(line)
        chunks.append({**base,
                       "text": "\n".join(test_lines),
                       "chunk_index": len(chunks)})

    # ── Chunk: related algorithms ─────────────────────────────────────────────
    if related_algos:
        algo_lines = [f"Related diagnostic algorithms for: {title}", ""]
        for a in related_algos:
            algo_title = a.get("title", "")
            algo_url   = a.get("url", "")
            if algo_title or algo_url:
                algo_lines.append(f"  Algorithm: {algo_title} | {algo_url}")
        if len(algo_lines) > 2:
            chunks.append({**base,
                           "text": "\n".join(algo_lines),
                           "chunk_index": len(chunks)})

    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# ARUP TEST DIRECTORY PROCESSOR  (new — handles scraped test JSONs like 3016635.json)
# ─────────────────────────────────────────────────────────────────────────────

def _process_test_directory(data: Dict, filename: str) -> List[Dict]:
    """
    Convert an ARUP test directory JSON into searchable chunks.

    Observed structure (3016635.json):
      test_id, test_name, original_name, source, url,
      description, details (dict with specimen_type, loinc_codes, methodology,
        ordering_recommendation, collect, specimen_preparation, stability,
        turnaround_time, performed_days, aliases, components, reference_interval, …),
      raw_text_for_embedding  ← pre-built embedding text, use directly

    Produces:
      1. Primary retrieval chunk — uses raw_text_for_embedding if present,
         otherwise builds from structured fields
      2. Specimen + logistics chunk (specimen, collection, stability, TAT)
      3. Components / aliases chunk (for synonym-based retrieval)
    """
    source_type  = "Test Directory"
    test_id      = str(data.get("test_id", ""))
    test_name    = data.get("test_name", data.get("original_name", filename))
    source_url   = data.get("url", "")
    description  = data.get("description", "")
    details      = data.get("details", {}) if isinstance(data.get("details"), dict) else {}
    raw_text     = data.get("raw_text_for_embedding", "")

    chunks: List[Dict] = []
    base = {"filename": filename, "source_type": source_type,
            "test_id": test_id, "test_name": test_name}

    # ── Chunk 1: primary retrieval ────────────────────────────────────────────
    if raw_text.strip():
        # Use the pre-built embedding text — it already contains the most
        # retrieval-relevant content
        primary = (
            f"Test Directory Entry\n"
            f"Test code: {test_id} | Name: {test_name}\n"
            f"Source: {source_url}\n\n"
            f"{raw_text.strip()}"
        )
    else:
        # Build from structured fields
        primary_parts = [
            f"Test Directory Entry",
            f"Test code: {test_id} | Name: {test_name}",
            f"Source: {source_url}" if source_url else "",
        ]
        if description:
            primary_parts.append(f"\nOrdering Recommendation:\n{description.strip()}")

        method = details.get("methodology", "")
        if method:
            primary_parts.append(f"Methodology: {method}")

        loinc = details.get("loinc_codes", [])
        if loinc:
            loinc_str = ", ".join(loinc) if isinstance(loinc, list) else str(loinc)
            primary_parts.append(f"LOINC codes: {loinc_str}")

        primary = "\n".join(p for p in primary_parts if p)

    for c in _text_to_chunks(primary, filename, source_type):
        c["chunk_index"] = len(chunks)
        # Copy test identifiers into every chunk for filtering
        c["test_id"]   = test_id
        c["test_name"] = test_name
        chunks.append(c)

    # ── Chunk 2: specimen + logistics ─────────────────────────────────────────
    specimen_parts = [f"Specimen and logistics for test: {test_id} {test_name}", ""]

    spec_fields = [
        ("Specimen type",       details.get("specimen_type") or details.get("collect", "")),
        ("Collection",          details.get("collect", "")),
        ("Specimen preparation",details.get("specimen_preparation", "")),
        ("Storage/Transport",   details.get("storage_transportation", "")),
        ("Stability",           details.get("stability", "")),
        ("Unacceptable",        details.get("unacceptable_conditions", "")),
        ("Turnaround time",     details.get("turnaround_time", "")),
        ("Performed",           details.get("performed_days", "")),
        ("NY DOH",              details.get("ny_doh_approval", "")),
        ("Patient preparation", details.get("patient_preparation", "")),
        ("Reference interval",  details.get("reference_interval", "")),
        ("Critical values",     details.get("critical_values", "")),
        ("Interpretation",      details.get("interpretation", "")),
    ]
    for label, value in spec_fields:
        v = (value or "").strip()
        if v:
            specimen_parts.append(f"{label}: {v}")

    if len(specimen_parts) > 2:
        chunks.append({**base,
                       "text": "\n".join(specimen_parts),
                       "chunk_index": len(chunks)})

    # ── Chunk 3: aliases + components (synonym retrieval) ─────────────────────
    alias_parts = [f"Aliases and components for test: {test_id} {test_name}", ""]

    aliases = details.get("aliases", "")
    if aliases:
        alias_parts.append(f"Aliases / synonyms:\n{aliases.strip()}")

    components = details.get("components", "")
    if components:
        alias_parts.append(f"\nComponents / sub-tests:\n{components.strip()}")

    cpt = details.get("cpt_codes", [])
    if cpt:
        cpt_str = ", ".join(str(c) for c in cpt) if isinstance(cpt, list) else str(cpt)
        alias_parts.append(f"\nCPT codes: {cpt_str}")

    if len(alias_parts) > 2:
        chunks.append({**base,
                       "text": "\n".join(alias_parts),
                       "chunk_index": len(chunks)})

    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# GENERIC JSON FALLBACK  (unchanged from original)
# ─────────────────────────────────────────────────────────────────────────────

def _process_generic_json(data: Any, filename: str) -> List[Dict]:
    """Handles JSON array of records or arbitrary nested objects."""
    if isinstance(data, list):
        if not data:
            raise ValueError(f"JSON array in {filename} is empty")

        preview = " ".join(str(k) for k in (data[0].keys() if isinstance(data[0], dict) else []))
        source_type = detect_source_type(filename, preview)
        chunks: List[Dict] = []

        for idx, item in enumerate(data[:2000]):
            if isinstance(item, dict):
                parts = []
                for k, v in item.items():
                    if v not in (None, "", [], {}):
                        val = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
                        parts.append(f"{k}: {val}")
                text = "\n".join(parts)
            else:
                text = str(item)

            if text.strip():
                chunks.append({"text": text, "filename": filename,
                               "source_type": source_type,
                               "record_index": str(idx), "chunk_index": idx})

        if not chunks:
            raise ValueError(f"No usable records found in {filename}")
        return chunks

    elif isinstance(data, dict):
        preview_parts = [filename]
        for k, v in data.items():
            preview_parts.append(str(k))
            if isinstance(v, str):
                preview_parts.append(v[:200])
        source_type = detect_source_type(filename, " ".join(preview_parts))
        flat_lines  = _flatten(data)
        full_text   = "\n".join(flat_lines)
        if not full_text.strip():
            raise ValueError(f"No readable content found in {filename}")
        return _text_to_chunks(full_text, filename, source_type)

    else:
        text = str(data)
        return _text_to_chunks(text, filename, detect_source_type(filename, text[:500]))


# ─────────────────────────────────────────────────────────────────────────────
# MAIN DOCUMENT PROCESSOR CLASS
# ─────────────────────────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {".pdf", ".csv", ".tsv", ".json", ".txt"}


class DocumentProcessor:

    def process(
        self,
        content: bytes,
        filename: str,
        folder_hint: str = "",
    ) -> List[Dict]:
        """
        Process a document and return a list of searchable chunks.

        Args:
            content:     Raw file bytes.
            filename:    Original filename.
            folder_hint: Parent folder name — strong classification signal.
                         e.g. "algorithms", "fact_sheets", "consult_topics", "tests"
        """
        ext = os.path.splitext(filename)[1].lower()

        if ext == ".pdf":
            return self._process_pdf(content, filename, folder_hint)
        elif ext in (".csv", ".tsv"):
            return self._process_csv(content, filename, ext, folder_hint)
        elif ext == ".json":
            return self._process_json(content, filename, folder_hint)
        elif ext == ".txt":
            return self._process_txt(content, filename, folder_hint)
        else:
            raise ValueError(
                f"Unsupported file type '{ext}'. Accepted: .pdf, .csv, .tsv, .json, .txt"
            )

    # ── JSON dispatcher ───────────────────────────────────────────────────────

    def _process_json(self, content: bytes, filename: str, folder_hint: str = "") -> List[Dict]:
        try:
            data = json.loads(content.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {filename}: {e}")

        if isinstance(data, dict):
            # Build a content preview for text-based fallback
            preview_parts = [str(k) for k in data.keys()]
            for v in list(data.values())[:3]:
                if isinstance(v, str):
                    preview_parts.append(v[:200])
            preview = " ".join(preview_parts)
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            preview = " ".join(str(k) for k in data[0].keys())
        else:
            preview = ""

        source_type = classify_document(
            filename=filename,
            content_preview=preview,
            json_data=data if isinstance(data, dict) else None,
            folder_hint=folder_hint,
        )

        # Dispatch to specialist processor
        if source_type == "Algorithm":
            return _process_algorithm(data, filename)
        elif source_type == "Fact Sheet":
            return _process_fact_sheet(data, filename)
        elif source_type == "Consult Topic":
            return _process_consult_topic(data, filename)
        elif source_type == "Test Directory":
            return _process_test_directory(data, filename)
        else:
            return _process_generic_json(data, filename)

    # ── PDF ───────────────────────────────────────────────────────────────────

    def _process_pdf(self, content: bytes, filename: str, folder_hint: str = "") -> List[Dict]:
        try:
            import pypdf
        except ImportError:
            raise RuntimeError("pypdf is not installed. Run: pip install pypdf")

        reader  = pypdf.PdfReader(io.BytesIO(content))
        preview = "".join(page.extract_text() or "" for page in reader.pages[:3])
        source_type = classify_document(filename=filename, content_preview=preview,
                                        folder_hint=folder_hint)

        chunks: List[Dict] = []
        current = ""
        current_pages: List[int] = []

        for i, page in enumerate(reader.pages):
            page_text = (page.extract_text() or "").strip()
            if not page_text:
                continue
            current += ("\n\n" if current else "") + page_text
            current_pages.append(i + 1)

            if len(current) >= CHUNK_TARGET or i == len(reader.pages) - 1:
                chunks.append({"text": current.strip(), "filename": filename,
                               "source_type": source_type, "pages": str(current_pages),
                               "chunk_index": len(chunks)})
                current = current[-CHUNK_OVERLAP:]
                current_pages = []

        if not chunks:
            raise ValueError(f"No readable text found in {filename}. Is it a scanned PDF?")
        return chunks

    # ── CSV / TSV ─────────────────────────────────────────────────────────────

    def _process_csv(self, content: bytes, filename: str, ext: str, folder_hint: str = "") -> List[Dict]:
        try:
            import pandas as pd
        except ImportError:
            raise RuntimeError("pandas is not installed. Run: pip install pandas")

        sep = "\t" if ext == ".tsv" else ","
        df  = pd.read_csv(io.BytesIO(content), sep=sep, dtype=str).fillna("")
        col_preview = " ".join(df.columns.tolist())
        source_type = classify_document(filename=filename, content_preview=col_preview,
                                        folder_hint=folder_hint)

        chunks: List[Dict] = []
        for idx, row in df.iterrows():
            if idx >= 2000:
                break
            parts = [f"{col}: {val}" for col, val in row.items() if str(val).strip()]
            if parts:
                chunks.append({"text": "\n".join(parts), "filename": filename,
                               "source_type": source_type, "row_index": str(idx),
                               "chunk_index": int(idx)})

        if not chunks:
            raise ValueError(f"No data rows found in {filename}")
        return chunks

    # ── Plain text ────────────────────────────────────────────────────────────

    def _process_txt(self, content: bytes, filename: str, folder_hint: str = "") -> List[Dict]:
        text = content.decode("utf-8", errors="replace")
        source_type = classify_document(filename=filename, content_preview=text[:1000],
                                        folder_hint=folder_hint)
        return _text_to_chunks(text, filename, source_type)