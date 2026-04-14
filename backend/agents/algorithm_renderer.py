"""
algorithm_renderer.py  v3.0.0
────────────────────────────────────────────────────────────────────────────
Algorithm Rendering Engine — Graph-Shape Classifier Edition

What changed in v3.0.0:
  • THREE distinct clinical render modes with a proper graph-shape classifier:
      clinical_linear_document   — mostly linear algorithms, 0–1 late forks
      clinical_tree_document     — local yes/no branching, decision trees
      clinical_multi_zone_document — true parallel-zone algorithms (thyroid)
  • Multi-zone is now ONLY selected when max raw out-degree >= 3 (3+ explicit
    branches from one node) AND enough clean nodes — not from decision_count alone
  • Strict artifact filter (_is_renderable_clinical_node) excludes copyright,
    references, abbreviations, legend, edge-glue labels, and source metadata
  • Label normaliser (_normalize_node_label) handles:
      - keyword-only titles (title = "ORDER" → keyword=ORDER, label=body)
      - edge-glue labels ("Yes No", "Left branch") → use body or suppress
      - title/body deduplication
  • Entry section now correctly excludes the branch decision node — only true
    shared-action trunk nodes appear as entry content
  • Path-aware topological ordering for zone groups (replaces plain BFS)
  • branch_splits emitted for tree/linear layouts so frontend can render
    local splits correctly without fake zone columns
  • Backward-compat aliases: layout / layout_mode equal render_mode

Design principles (unchanged):
  DETERMINISTIC — zero LLM calls; pure graph transformation
  GROUNDED      — uses only structured node/edge data from ingestion
  GRACEFUL      — returns None when graph data unavailable
"""

import re
from collections import deque, defaultdict
from typing import Dict, List, Optional, Set, Tuple


# ── Clinical keyword sets ──────────────────────────────────────────────────────
_SCREENING_KWS    = {"screen","initial","baseline","evaluate","assess","suspect",
                     "presentation","workup","first-line","first line"}
_CONFIRMATORY_KWS = {"confirm","confirmatory","verify","diagnos","definitive",
                     "establish diagnosis","proven","gold standard"}
_MONITORING_KWS   = {"monitor","follow","repeat","surveillance","recheck",
                     "interval","periodic","ongoing","subsequent","follow-up"}

_MEANINGFUL_NODE_TYPES = {"action","decision","outcome","info","start"}

_ACTION_KEYWORDS = [
    ("route by",  "ROUTE BY"),
    ("order",     "ORDER"),
    ("consider",  "CONSIDER"),
    ("perform",   "PERFORM"),
    ("repeat",    "REPEAT"),
    ("obtain",    "OBTAIN"),
    ("proceed",   "PROCEED"),
    ("evaluate",  "EVALUATE"),
    ("select",    "SELECT"),
]

_FINDING_PATTERNS     = {"elevated","confirmed","positive","negative","indeterminate",
                         "equivocal","finding","result","detected","not detected"}
_RESULT_PILL_PATTERNS = {"elevated","confirmed","positive","negative","indeterminate",
                         "equivocal","detected","not detected"}
_CRITICAL_PATTERNS    = {"germline","genetic testing","ret ","brca","urgent","biopsy",
                         "surgical","malignant","critical","life-threatening"}

_OUTCOME_REFER_KWS    = {"refer","consult","specialist","endocrinol","oncolog",
                         "surgeon","patholog","geneticist","second opinion"}
_OUTCOME_MONITOR_KWS  = {"monitor","follow","surveillance","recheck","repeat",
                         "interval","periodic","observe","watch","reassess"}

# Zone accent palette (from thyroid cancer UX layout spec)
_ZONE_ACCENTS = [
    {"hex": "#C6B1A1", "token": "accent4",   "name": "Aspen",    "text": "#FFFFFF"},
    {"hex": "#D37E57", "token": "accent5",   "name": "Hoodoo",   "text": "#FFFFFF"},
    {"hex": "#6D0020", "token": "secondary", "name": "Red Rock", "text": "#FFFFFF"},
    {"hex": "#83A8C5", "token": "info",      "name": "Glass",    "text": "#FFFFFF"},
]


# ── Artifact-filter constants ──────────────────────────────────────────────────

# Title prefixes that always indicate non-clinical-flow artifact nodes.
# Applied AFTER stripping leading whitespace from the lowercased title.
_ARTIFACT_TITLE_PREFIXES_LOWER = (
    "© ",
    "references",
    "click here for topics",
    "abbreviations",
    "legend",
    "footnote",
    "source:",
    "last updated",
    "content reviewed",
    # Reference / citation titles
    "national comprehensive cancer",
    "haugen ",
    "gharib ",
    "2015 american",
    "2016 american",
    "endocr pract",
    "thyroid. 2016",
    "thyroid . 2016",
    "clin infect dis",
    "guidelines for clinical practice",
    "nccn clinical practice",
    "american association of clinical",
    "american thyroid association",
    "montoya jg",
    "u.s. department of health",
)

# Patterns that indicate citation/reference content anywhere in combined text.
# These catch reference nodes where the title looks innocuous but the body is
# a journal citation (e.g. title="cancer." body="Thyroid. 2016;26(1):1-133.")
_ARTIFACT_CITATION_PATTERNS = (
    "thyroid. 20",          # journal citation with year
    "thyroid . 20",
    "endocr pract. 20",
    "endocr pract . 20",
    "clin infect dis.",
    "j clin endocrinol",
    "all rights reserved",
    "gharib h,",
    "haugen br,",
    "nccn clinical practice guidelines",
    # Year;volume(issue) patterns typical of journal citations
    ";22(",
    ";26(",
    ";47(",
    "version 5.20",
    "accessed jan 20",
    "dpd\u03bex",                   # CDC parasite reference
)

# Exact normalised labels that are pure edge-glue with no clinical meaning
_EDGE_GLUE_EXACT = {
    "yes", "no", "yes no", "yes / no", "yes/no",
    "true", "false",
    "left", "right",
    "left branch", "right branch",
    "positive", "negative",        # bare, without context
    "+ -", "+/-",
}


# ── Label-normalisation constants ──────────────────────────────────────────────

# Titles that are ONLY a keyword — the real label is in the body
_KEYWORD_ONLY_TITLES = {
    "ORDER", "CONSIDER", "PERFORM", "REPEAT",
    "OBTAIN", "EVALUATE", "PROCEED", "SELECT", "ROUTE BY",
}


# ── Filtering ─────────────────────────────────────────────────────────────────

def _is_renderable_clinical_node(node: dict) -> bool:
    """
    Strict filter for clinical flow rendering.

    Stricter than the retrieval-layer meaningful-node filter.  Excludes:
      • copyright / date / source metadata
      • references / journal citations (title prefix OR citation pattern in body)
      • abbreviations / legend blocks
      • pure edge-glue label nodes ("Yes", "No", "Left branch")
      • nodes with no meaningful displayable content (< 5 chars total)

    NOTE: Node type is normalized via _safe_node_type() before the membership
    check. This is critical — algorithm JSONs may store variant type strings
    ("result", "branch", "step", "node") that _safe_node_type correctly remaps
    to valid clinical types. Without normalization, ALL such nodes would be
    silently filtered, producing an empty vis_nodes list and suppressing the
    entire algorithm component.
    """
    if _safe_node_type(node.get("type") or "") not in _MEANINGFUL_NODE_TYPES:
        return False

    raw_title = (node.get("title") or "").strip()
    raw_body  = (node.get("body")  or "").strip()

    # Must have some renderable content at all
    combined = (raw_title + " " + raw_body).strip()
    if len(combined) < 5:
        return False

    # Artifact title prefix check — strip leading whitespace first
    title_lc = raw_title.lstrip().lower()
    if any(title_lc.startswith(p) for p in _ARTIFACT_TITLE_PREFIXES_LOWER):
        return False

    # Citation / reference pattern check (anywhere in combined lowercase text)
    combined_lc = combined.lower()
    if any(p in combined_lc for p in _ARTIFACT_CITATION_PATTERNS):
        return False

    # Pure edge-glue label with no meaningful body
    normalised = title_lc.strip("., ")
    if normalised in _EDGE_GLUE_EXACT and len(raw_body) < 10:
        return False

    return True


# ── Label normalisation ────────────────────────────────────────────────────────

def _extract_keyword(label: str) -> Optional[str]:
    """Return the leading ARUP action keyword, or None."""
    lbl = label.strip().lower()
    for raw, canonical in _ACTION_KEYWORDS:
        if lbl.startswith(raw):
            return canonical
    return None


def _normalize_node_label(title: str, body: str, node_type: str
                          ) -> Tuple[str, str, Optional[str]]:
    """
    Normalise raw title + body into (display_label, description, keyword).

    Rules (applied in order):
      1. If title is EXACTLY a keyword (e.g. "ORDER") and body exists
         → keyword=title, label=body (strip leading keyword from body if present)
      2. If title is pure edge-glue ("Yes No", "Left branch") and body exists
         → try to use body as label (with keyword extraction)
      3. Normal keyword extraction: strip keyword from start of title
      4. Deduplicate body: if body starts with same text as label, strip it
    """
    title = title.strip()
    body  = body.strip()

    # ── Rule 1: keyword-only title ────────────────────────────────────────────
    if title.upper() in _KEYWORD_ONLY_TITLES and body:
        keyword   = title.upper()
        candidate = body
        # If body also starts with the keyword, strip it
        if candidate.upper().startswith(keyword):
            candidate = candidate[len(keyword):].lstrip(" :—-").strip()
        label = candidate or body
        return label[:200], "", keyword

    # ── Rule 2: edge-glue title ───────────────────────────────────────────────
    normalised = title.lower().strip("., ")
    if normalised in _EDGE_GLUE_EXACT:
        if body:
            kw = _extract_keyword(body)
            if kw and body.upper().startswith(kw):
                lbl = body[len(kw):].lstrip(" :—-").strip()
                return (lbl or body)[:200], "", kw
            return body[:200], "", None
        # No body either — return the edge-glue as-is (renderer may still filter)
        return title[:200], "", None

    # ── Rule 3: standard keyword extraction ──────────────────────────────────
    keyword = _extract_keyword(title)
    if keyword and title.upper().startswith(keyword):
        label = title[len(keyword):].lstrip(" :—-").strip()
        if not label:
            label = body or title
        desc = body if (body and body.lower() != label.lower()) else ""
        # Strip label duplication from desc
        if desc and desc.lower().startswith(label.lower()):
            desc = desc[len(label):].lstrip(" .,:;").strip()
        return label[:200], desc[:400], keyword

    # ── Rule 4: plain title, deduplicate body ────────────────────────────────
    label = title
    desc  = body
    if desc:
        if desc.lower().startswith(label.lower()):
            desc = desc[len(label):].lstrip(" .,:;—\n").strip()
        elif label.lower().startswith(desc.lower()):
            desc = ""
    return label[:200], desc[:400], keyword


# ── Graph helpers ──────────────────────────────────────────────────────────────

def _safe_node_type(raw: str) -> str:
    t = (raw or "action").lower().strip()
    if t in _MEANINGFUL_NODE_TYPES:
        return t
    if "decision" in t or "branch" in t or "choice" in t:
        return "decision"
    if "outcome" in t or "result" in t or "end" in t:
        return "outcome"
    if "start" in t or "begin" in t or "entry" in t:
        return "start"
    return "action"


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _build_simple_adj(edges: List[dict]) -> Dict[str, List[str]]:
    adj: Dict[str, List[str]] = defaultdict(list)
    for e in edges:
        f, t = e.get("from",""), e.get("to","")
        if f and t:
            adj[f].append(t)
    return dict(adj)


def _build_adjacency(edges: List[dict]) -> Dict[str, List[dict]]:
    adj: Dict[str, List[dict]] = defaultdict(list)
    for e in edges:
        f, t = e.get("from",""), e.get("to","")
        lbl  = (e.get("label") or "").strip()
        if f:
            adj[f].append({"to": t, "label": lbl})
    return dict(adj)


def _compute_levels(nodes: List[dict], edges: List[dict],
                    root_id: Optional[str]) -> Tuple[Dict[str,int], str]:
    """BFS from root to assign depth levels. Handles disconnected sub-graphs."""
    node_ids = {n["id"] for n in nodes if "id" in n}

    resolved = root_id if (root_id and root_id in node_ids) else None
    if not resolved:
        targets    = {e.get("to","") for e in edges}
        candidates = [n["id"] for n in nodes if n["id"] not in targets]
        resolved   = candidates[0] if candidates else (nodes[0]["id"] if nodes else None)

    sadj: Dict[str,List[str]] = defaultdict(list)
    for e in edges:
        f, t = e.get("from",""), e.get("to","")
        if f and t:
            sadj[f].append(t)

    levels: Dict[str,int] = {}
    visited: Set[str]     = set()
    if resolved:
        q = deque([(resolved, 0)])
        while q:
            nid, lv = q.popleft()
            if nid in visited or nid not in node_ids:
                continue
            visited.add(nid)
            levels[nid] = lv
            for c in sadj[nid]:
                if c not in visited:
                    q.append((c, lv + 1))

    remaining = max(levels.values(), default=0) + 1
    for n in nodes:
        if n["id"] not in levels:
            levels[n["id"]] = remaining

    return levels, resolved or ""


def _assign_branches(edges: List[dict]) -> Dict[Tuple[str,str], str]:
    """Assign semantic branch labels (yes/no/left/right/alt_N) to branching edges."""
    by_parent: Dict[str,List[dict]] = defaultdict(list)
    for e in edges:
        f, t = e.get("from",""), e.get("to","")
        by_parent[f].append({"to": t, "label": (e.get("label") or "").lower()})

    branch_map: Dict[Tuple[str,str],str] = {}
    for parent, children in by_parent.items():
        if len(children) <= 1:
            continue
        for i, child in enumerate(children):
            t, lbl = child["to"], child["label"]
            if any(w in lbl for w in ("yes","positive","abnormal","elevated","true")):
                branch = "yes"
            elif any(w in lbl for w in ("no","negative","normal","false")):
                branch = "no"
            elif i == 0:
                branch = "left"
            elif i == 1:
                branch = "right"
            else:
                branch = f"alt_{i}"
            branch_map[(parent, t)] = branch
    return branch_map


# ── Graph-shape layout classifier ─────────────────────────────────────────────

def _classify_layout(raw_nodes: List[dict], edges: List[dict]) -> str:
    """
    Determine the correct clinical render mode from graph structure.

    Three modes:
      clinical_linear_document    — linear spine, 0–1 late forks
      clinical_tree_document      — local yes/no branching, decision trees
      clinical_multi_zone_document — true parallel-zone algorithms

    Key signal for multi-zone:
      • At least one node has RAW out-degree >= 3 (three explicit zone branches)
        AND there are enough clean nodes (>= 5) to justify the zone layout.
    Multi-zone is NOT triggered by decision_count or total_nodes alone.

    Key signal for tree:
      • Binary branching (max out-degree == 2) with enough nodes.

    Everything else → linear.
    """
    if not raw_nodes or not edges:
        return "clinical_linear_document"

    # Raw out-degrees (before any filtering)
    raw_out: Dict[str,int] = defaultdict(int)
    for e in edges:
        f = e.get("from","")
        if f:
            raw_out[f] += 1

    if not any(v > 1 for v in raw_out.values()):
        return "clinical_linear_document"

    max_raw_out = max(raw_out.values(), default=0)

    # Count clean renderable nodes
    clean_count = sum(1 for n in raw_nodes if _is_renderable_clinical_node(n))

    # ── Multi-zone: 3+ explicit branches from one node AND enough content ─────
    # This is the thyroid-style routing split (ROUTE BY CANCER TYPE = 3 paths).
    if max_raw_out >= 3 and clean_count >= 5:
        return "clinical_multi_zone_document"

    # ── Tree: binary branching with meaningful content ──────────────────────
    if max_raw_out == 2 and clean_count >= 4:
        return "clinical_tree_document"

    # ── Fallback: linear (single late fork or tiny algorithm) ──────────────
    return "clinical_linear_document"


# ── Node variant assignment ────────────────────────────────────────────────────

def _node_emphasis(node_type: str, label: str, desc: str) -> str:
    if node_type in ("start","decision"):
        return "primary"
    combined = (label + " " + desc).lower()
    if any(p in combined for p in _CRITICAL_PATTERNS):
        return "critical"
    return "secondary"


def _node_variant(node_type: str, label: str, emphasis: str) -> str:
    """
    Map node type + text signals to a frontend archetype variant.

    Variants:
      startNode        — dark maroon pill, white text
      routingLabel     — uppercase caption + flanking hairlines
      sharedActionCard — multi-row entry card (only assigned to entry-section nodes)
      actionNode       — white card, Badlands border, keyword
      criticalNode     — white card, maroon 2px border
      resultNode       — small auto-width pill (#F9F7F6, Aspen border), centered
      findingNode      — full-width neutral card (#F9F7F6, Aspen border)
      decisionNode     — neutral card (#F9F7F6), red dot + uppercase label
      outcomeNode      — neutral card, Badlands border, eyebrow + link
      terminalNode     — muted neutral card, italic text
      infoNode         — muted supporting card
    """
    if node_type == "start":
        return "startNode"

    lbl_lc = label.lower()

    if lbl_lc.startswith("route by"):
        return "routingLabel"

    if node_type == "decision":
        if any(p in lbl_lc for p in _FINDING_PATTERNS) and len(label) < 60:
            return "resultNode"
        return "decisionNode"

    if node_type == "outcome":
        if any(p in lbl_lc for p in {"no further","individualized","terminal","management is"}):
            return "terminalNode"
        return "outcomeNode"

    if node_type == "info":
        return "infoNode"

    # action
    if emphasis == "critical":
        return "criticalNode"
    if any(p in lbl_lc for p in _RESULT_PILL_PATTERNS) and len(label) < 50:
        return "resultNode"
    if any(p in lbl_lc for p in _FINDING_PATTERNS):
        return "findingNode"
    return "actionNode"


# ── Clinical stage ─────────────────────────────────────────────────────────────

def _clinical_stage(title: str, body: str) -> str:
    text = (title + " " + body).lower()
    if any(kw in text for kw in _CONFIRMATORY_KWS):
        return "confirmatory"
    if any(kw in text for kw in _MONITORING_KWS):
        return "monitoring"
    if any(kw in text for kw in _SCREENING_KWS):
        return "screening"
    return "general"


def _outcome_subtype(label: str, desc: str) -> str:
    """Classify an outcome node into refer / monitor / general."""
    text = (label + " " + desc).lower()
    if any(kw in text for kw in _OUTCOME_REFER_KWS):
        return "refer"
    if any(kw in text for kw in _OUTCOME_MONITOR_KWS):
        return "monitor"
    return "general"


def _node_importance(nid: str, node_type: str, root_id: str,
                     out_degree: Dict[str,int]) -> str:
    if nid == root_id or node_type == "decision" or out_degree.get(nid,0) > 1:
        return "primary"
    return "secondary"


# ── Topological path ordering ──────────────────────────────────────────────────

def _topological_path(start_id: str, path_set: Set[str],
                      simple_adj: Dict[str,List[str]]) -> List[str]:
    """
    Walk from start_id following edges, staying within path_set.
    Returns nodes in DFS reading order (pre-order).
    """
    result:  List[str] = []
    visited: Set[str]  = set()

    def walk(nid: str) -> None:
        if nid in visited or nid not in path_set:
            return
        visited.add(nid)
        result.append(nid)
        for c in simple_adj.get(nid, []):
            walk(c)

    walk(start_id)
    return result


# ── Trunk finder ──────────────────────────────────────────────────────────────

def _find_trunk(all_ids: Set[str], simple_adj: Dict[str,List[str]],
                root_id: str) -> Tuple[List[str], Optional[str], List[str]]:
    """
    Walk from root along the linear spine until the first node with 2+ children.

    Returns:
      trunk       — node IDs before the branch point (shared entry spine)
      branch_nid  — the first node with 2+ children, or None
      children    — outgoing children of branch_nid
    """
    trunk:   List[str] = []
    visited: Set[str]  = set()
    current = root_id
    guard   = len(all_ids) + 2

    for _ in range(guard):
        if not current or current not in all_ids or current in visited:
            break
        visited.add(current)
        children = [c for c in simple_adj.get(current,[]) if c in all_ids and c != current]
        if len(children) >= 2:
            return trunk, current, children
        elif len(children) == 1:
            trunk.append(current)
            current = children[0]
        else:
            trunk.append(current)
            break

    return trunk, None, []


# ── Zone groups for multi-zone documents ──────────────────────────────────────

def _compute_zone_groups(
    vis_nodes:     List[dict],
    simple_adj:    Dict[str,List[str]],
    edges_raw:     List[dict],
    branch_nid:    str,
    branch_children: List[str],
) -> Tuple[List[dict], List[str]]:
    """
    Build zone groups for clinical_multi_zone_document.

    Returns (groups, entry_section_node_ids).

    entry_section_node_ids: trunk nodes BEFORE the branch point.
    The branch node itself is NOT in the entry section — it is the routing pivot.

    Each group:
      group_id, decision_node_id, label, zone_label, zone_subtitle,
      branch, node_ids (topological order), col, accent, nested_fork
    """
    all_ids   = {n["id"] for n in vis_nodes}
    node_map  = {n["id"]: n for n in vis_nodes}

    # Edge condition lookup
    edge_cond: Dict[Tuple[str,str],str] = {}
    for e in edges_raw:
        f, t    = e.get("from",""), e.get("to","")
        cond    = (e.get("condition_label") or e.get("label") or "").strip()
        if f and t and cond:
            edge_cond[(f,t)] = cond

    # Find root & trunk
    incoming = {e.get("to","") for e in edges_raw}
    root_cands = [n["id"] for n in vis_nodes if n["id"] not in incoming]
    root_id    = root_cands[0] if root_cands else (vis_nodes[0]["id"] if vis_nodes else None)

    trunk_ids: List[str] = []
    if root_id:
        trunk_ids, _, _ = _find_trunk(all_ids, simple_adj, root_id)

    # Nodes that cannot be in any zone (trunk + the branch node itself)
    excluded = set(trunk_ids) | {branch_nid}

    # Generic edge-glue strings we don't want as zone labels
    _GENERIC_LABELS = {"left branch","right branch","left","right","alt_0","alt_1"}

    def derive_zone_label(child_id: str, edge_label: str) -> Tuple[str,str]:
        if edge_label and edge_label.lower() not in _GENERIC_LABELS:
            return edge_label.title(), ""
        first = node_map.get(child_id)
        if first:
            raw = first.get("raw_label") or first.get("label") or ""
            # Take first clause
            short = raw.strip()
            for sep in (",","."," and "):
                idx = short.find(sep)
                if 0 < idx <= 55:
                    short = short[:idx].strip()
                    break
            short = short[:55]
            if short and short.lower() not in _GENERIC_LABELS:
                return short, ""
        return "Branch", ""

    def bfs_zone(start_id: str) -> Tuple[List[str], Optional[dict]]:
        """Collect zone nodes in topological order; detect nested fork."""
        zone_set: Set[str]  = set()
        visited:  Set[str]  = set()
        q = deque([start_id])
        while q:
            nid = q.popleft()
            if nid in visited or nid not in all_ids or nid in excluded:
                continue
            visited.add(nid)
            zone_set.add(nid)
            for c in simple_adj.get(nid,[]):
                if c not in visited and c in all_ids and c not in excluded:
                    q.append(c)

        ordered = _topological_path(start_id, zone_set, simple_adj)

        # Detect first nested fork inside this zone
        nested_fork = None
        for nid in ordered:
            children_in_zone = [c for c in simple_adj.get(nid,[])
                                 if c in zone_set and c != nid]
            if len(children_in_zone) >= 2 and nid != start_id and nested_fork is None:
                nested_fork = {"type":"tJunctionFork","fork_node_id":nid,
                               "path_children":[(c,"") for c in children_in_zone]}
                break

        return ordered, nested_fork

    def resolve_nested_fork(raw: dict, zone_nodes: List[str]) -> dict:
        zone_set  = set(zone_nodes)
        children  = raw["path_children"]
        paths: List[dict] = []
        labels  = ["Path A","Path B","Path C","Path D"]
        claimed: Set[str] = set()

        for i,(cid,elbl) in enumerate(children):
            if cid not in zone_set:
                continue
            path_ids: List[str] = []
            pv:       Set[str]  = set()
            pq = deque([cid])
            while pq:
                nid = pq.popleft()
                if nid in pv or nid not in zone_set or nid in claimed:
                    continue
                pv.add(nid); path_ids.append(nid)
                for c in simple_adj.get(nid,[]):
                    if c not in pv and c in zone_set:
                        pq.append(c)
            claimed.update(path_ids)
            cond = elbl or ""
            if not cond:
                first = node_map.get(cid)
                if first:
                    cond = (first.get("raw_label") or first.get("label") or "")[:60]
            paths.append({"path_id":f"path_{i}",
                          "label":labels[i] if i<len(labels) else f"Path {i+1}",
                          "condition":cond, "node_ids":path_ids})

        return {"type":raw["type"],"fork_node_id":raw["fork_node_id"],"paths":paths}

    groups: List[dict] = []
    for col, child_id in enumerate(branch_children):
        if child_id not in all_ids:
            continue
        edge_label = edge_cond.get((branch_nid, child_id), "")
        zone_label, zone_subtitle = derive_zone_label(child_id, edge_label)
        zone_node_ids, nested_fork_raw = bfs_zone(child_id)
        nested_fork = resolve_nested_fork(nested_fork_raw, zone_node_ids) if nested_fork_raw else None

        groups.append({
            "group_id":         f"grp_{branch_nid}_{child_id}",
            "decision_node_id": branch_nid,
            "label":            zone_label or f"Branch {col+1}",
            "zone_label":       zone_label or f"Branch {col+1}",
            "zone_subtitle":    zone_subtitle,
            "branch":           "",
            "node_ids":         zone_node_ids,
            "col":              col,
            "accent":           _ZONE_ACCENTS[col % len(_ZONE_ACCENTS)],
            "nested_fork":      nested_fork,
        })

    # Entry section = trunk nodes only (NOT the branch node)
    entry_node_ids = trunk_ids[:]
    return groups, entry_node_ids


# ── Branch-split metadata for tree / linear layouts ───────────────────────────

def _compute_branch_splits(
    vis_nodes:  List[dict],
    edges_raw:  List[dict],
    simple_adj: Dict[str,List[str]],
    branch_map: Dict[Tuple[str,str],str],
) -> List[dict]:
    """
    Build branch_splits for clinical_tree_document and clinical_linear_document.

    Each split:
      {
        "from_node_id": str,
        "branches": [
          {"edge_label": str, "branch_key": str, "to_node_id": str, "node_ids": [str]}
        ]
      }

    node_ids within each branch are in topological order.
    """
    all_ids    = {n["id"] for n in vis_nodes}
    multi_out  = {n["id"] for n in vis_nodes
                  if len([c for c in simple_adj.get(n["id"],[]) if c in all_ids]) >= 2}

    if not multi_out:
        return []

    # Edge label lookup
    edge_label_map: Dict[Tuple[str,str],str] = {}
    for e in edges_raw:
        f, t = e.get("from",""), e.get("to","")
        lbl  = (e.get("condition_label") or e.get("label") or "").strip()
        if f and t:
            edge_label_map[(f,t)] = lbl

    # Nodes already claimed by an earlier split
    claimed: Set[str] = set()
    splits:  List[dict] = []

    # Process splits in BFS level order (shallow first)
    node_level = {n["id"]: n.get("level",0) for n in vis_nodes}
    ordered_multi = sorted(multi_out, key=lambda nid: node_level.get(nid,0))

    for fork_nid in ordered_multi:
        children = [c for c in simple_adj.get(fork_nid,[])
                    if c in all_ids and c not in claimed]
        if len(children) < 2:
            continue

        branches: List[dict] = []
        for c in children:
            # Collect all nodes reachable from c that are not claimed
            branch_set: Set[str] = set()
            bq  = deque([c])
            bv: Set[str] = set()
            while bq:
                nid = bq.popleft()
                if nid in bv or nid not in all_ids or nid in claimed:
                    continue
                bv.add(nid); branch_set.add(nid)
                for nc in simple_adj.get(nid,[]):
                    if nc not in bv:
                        bq.append(nc)

            ordered_branch = _topological_path(c, branch_set, simple_adj)
            raw_lbl  = edge_label_map.get((fork_nid, c), "")
            bkey     = branch_map.get((fork_nid, c), "")
            display  = raw_lbl or bkey or f"Branch {len(branches)+1}"

            branches.append({
                "edge_label":  display,
                "branch_key":  bkey,
                "to_node_id":  c,
                "node_ids":    ordered_branch,
            })
            claimed.update(branch_set)

        if branches:
            splits.append({"from_node_id": fork_nid, "branches": branches})

    return splits


# ── Spine extractor for tree / linear ─────────────────────────────────────────

def _extract_spine(vis_nodes: List[dict], simple_adj: Dict[str,List[str]],
                   root_id: str, split_nids: Set[str]) -> List[str]:
    """
    Walk from root along the single-child spine until a multi-branch node.
    Returns the ordered list of spine node IDs (before or up to the split).
    """
    all_ids  = {n["id"] for n in vis_nodes}
    spine:    List[str] = []
    visited:  Set[str]  = set()
    current = root_id
    guard   = len(all_ids) + 2

    for _ in range(guard):
        if not current or current not in all_ids or current in visited:
            break
        visited.add(current)
        spine.append(current)
        if current in split_nids:
            break  # stop at the first branch point (include the branch node in spine)
        children = [c for c in simple_adj.get(current,[]) if c in all_ids and c not in visited]
        if len(children) == 1:
            current = children[0]
        else:
            break

    return spine


# ── Source document resolution ─────────────────────────────────────────────────
#
# Priority order for PDF resolution (v4.0):
#   1. Same-origin local asset from AssetStore (uploaded or cached)
#   2. Explicit pdf_url already in graph_data that looks like a local path
#   3. Direct PDF source_url (url already ends with .pdf)
#   4. Empty string — no PDF available
#
# The old ARUP filename-guessing heuristic is removed. Remote PDF URLs
# are no longer embedded; they are only used for "open in new tab" fallback.

def _resolve_pdf_from_assets(source_url: str, title: str,
                              graph_data: dict) -> tuple[str, str, bool, dict]:
    """
    Attempt to resolve a PDF from the local AssetStore using verbose matching.

    Returns (pdf_url, asset_id, has_local_pdf, pdf_match_debug).
    pdf_url is a same-origin path like /api/assets/pdf/{asset_id} or "".
    pdf_match_debug carries the full matching diagnostics for the /chat debug block.
    """
    try:
        from agents.asset_store import get_asset_store, AssetStore
        store = get_asset_store()
    except Exception as exc:
        return "", "", False, {"strategy": "import_error", "error": str(exc)}

    source_page_url = graph_data.get("source_page_url", "") or source_url

    debug = store.find_matching_pdf_with_debug(
        source_url=source_url,
        source_page_url=source_page_url,
        title=title,
    )

    match = debug.get("match")
    if match:
        asset_id = match["asset_id"]
        return AssetStore.local_url(asset_id), asset_id, True, debug

    return "", "", False, debug


def _resolve_pdf_url(source_url: str, graph_data: dict,
                     title: str = "") -> tuple[str, str, str, bool, dict]:
    """
    Resolve the best available PDF URL and origin.

    Returns (pdf_url, pdf_asset_id, pdf_origin, has_local_pdf, pdf_match_debug).

    pdf_origin values:
      "local_uploaded"   — uploaded by user, same-origin
      "local_cached"     — downloaded and cached by server, same-origin
      "remote_direct"    — source_url itself is a direct PDF link
      "none"             — no PDF available
    """
    # ── Priority 1: local asset store ────────────────────────────────────────
    local_url, asset_id, has_local, match_debug = _resolve_pdf_from_assets(
        source_url, title, graph_data)
    if has_local:
        # Determine origin from asset record
        try:
            from agents.asset_store import get_asset_store
            store = get_asset_store()
            asset = store.get_asset(asset_id)
            origin = (asset.get("asset_origin", "uploaded") == "cached_remote"
                      and "local_cached" or "local_uploaded")
        except Exception:
            origin = "local_uploaded"
        match_debug["pdf_origin"] = origin
        return local_url, asset_id, origin, True, match_debug

    # ── Priority 2: explicit pdf_url in graph_data that is already local ─────
    explicit = (graph_data.get("pdf_url") or "").strip()
    if explicit and (explicit.startswith("/api/") or explicit.startswith("/")):
        match_debug["pdf_origin"] = "local_cached"
        match_debug["strategy"]   = "explicit_local_pdf_url"
        return explicit, "", "local_cached", True, match_debug

    # ── Priority 3: source_url is a direct PDF ───────────────────────────────
    url = (source_url or "").strip()
    if url.lower().endswith(".pdf"):
        match_debug["pdf_origin"] = "remote_direct"
        match_debug["strategy"]   = "source_url_is_pdf"
        return url, "", "remote_direct", False, match_debug

    # ── Priority 4: no PDF ───────────────────────────────────────────────────
    match_debug["pdf_origin"] = "none"
    return "", "", "none", False, match_debug


def _resolve_source_page_url(source_url: str, graph_data: dict) -> str:
    return (graph_data.get("source_url") or source_url or "").strip()


def _resolve_source_asset_type(pdf_url: str, has_local: bool, source_url: str) -> str:
    if has_local:
        return "pdf"
    if pdf_url:
        return "pdf"
    if source_url and "arupconsult.com" in source_url.lower():
        return "html"
    return "unknown"


def _extract_footer_blocks(graph_data: dict) -> list:
    """
    Extract footnotes and abbreviations from graph_data as footer blocks.
    These should render BELOW the main flow, not as clinical flow nodes.

    Returns list of:
      {"type": "footnote" | "abbreviations", "content": str | list}
    """
    blocks = []

    # Footnotes
    footnotes = graph_data.get("footnotes", [])
    if footnotes:
        for fn in footnotes:
            if isinstance(fn, dict):
                marker  = fn.get("marker", "")
                content = fn.get("content", "")
            else:
                marker, content = "", str(fn)
            if content:
                blocks.append({
                    "type":    "footnote",
                    "marker":  marker,
                    "content": content,
                })

    # Abbreviations
    abbrevs = graph_data.get("abbreviations", [])
    if abbrevs:
        items = []
        for a in abbrevs:
            if isinstance(a, dict):
                items.append({"key": a.get("key",""), "definition": a.get("definition","")})
            elif isinstance(a, (list, tuple)) and len(a) >= 2:
                items.append({"key": a[0], "definition": a[1]})
        if items:
            blocks.append({"type": "abbreviations", "items": items})

    return blocks


# ── Core visualization builder ────────────────────────────────────────────────

def build_visualization(graph_data: dict, source_file: str) -> Optional[dict]:
    """
    Transform raw algorithm graph data into a structured visualization model.

    v3.0.0: uses graph-shape classifier, strict artifact filtering, label
    normalisation, and path-aware ordering.
    """
    raw_nodes  = graph_data.get("nodes", [])
    edges      = graph_data.get("edges", [])
    root_id    = graph_data.get("root_node_id")
    title      = graph_data.get("title", source_file)
    tests      = graph_data.get("tests", [])
    source_url = graph_data.get("source_url", "")

    if not raw_nodes:
        return None

    # ── Classify layout BEFORE building vis_nodes ──────────────────────────
    render_mode = _classify_layout(raw_nodes, edges)

    # ── Graph structures ───────────────────────────────────────────────────
    raw_node_map = {n["id"]: n for n in raw_nodes if "id" in n}
    out_degree: Dict[str,int] = defaultdict(int)
    for e in edges:
        f = e.get("from","")
        if f:
            out_degree[f] += 1

    levels, resolved_root = _compute_levels(raw_nodes, edges, root_id)
    simple_adj = _build_simple_adj(edges)
    adj        = _build_adjacency(edges)
    branch_map = _assign_branches(edges)

    incoming_branch: Dict[str,Tuple[str,str]] = {}
    for (parent, child), bkey in branch_map.items():
        incoming_branch[child] = (parent, bkey)

    # ── Build vis_nodes with strict filtering and label normalisation ──────
    vis_nodes: List[dict] = []
    node_order_idx = {n["id"]: i for i, n in enumerate(raw_nodes)}

    for node in raw_nodes:
        nid = node.get("id","")
        if not nid:
            continue
        if not _is_renderable_clinical_node(node):
            continue

        raw_type = node.get("type","action")
        n_type   = _safe_node_type(raw_type)
        title_t  = _strip_html(node.get("title") or "")
        body_t   = _strip_html(node.get("body")  or "")

        # Normalise label
        label, desc, keyword = _normalize_node_label(title_t, body_t, n_type)
        if not label:
            label = nid

        raw_label = ((title_t + " " + body_t).strip())[:200]

        node_branch = incoming_branch.get(nid, (None,None))[1]
        children     = adj.get(nid, [])
        children_ids = [c["to"] for c in children if c["to"] in raw_node_map]

        emphasis = _node_emphasis(n_type, label, desc)
        variant  = _node_variant(n_type, label, emphasis)

        vis_nodes.append({
            "id":               nid,
            "type":             n_type,
            "variant":          variant,
            "emphasis":         emphasis,
            "keyword":          keyword,
            "label":            label,
            "raw_label":        raw_label,
            "description":      desc,
            "level":            levels.get(nid, 0),
            "branch":           node_branch,
            "clinical_stage":   _clinical_stage(title_t, body_t),
            "outcome_subtype":  _outcome_subtype(label, desc) if n_type == "outcome" else None,
            "importance":       _node_importance(nid, n_type, resolved_root, dict(out_degree)),
            "has_children":     bool(children_ids),
            "children_count":   len(children_ids),
            "children_ids":     children_ids,
        })

    vis_nodes.sort(key=lambda n: (n["level"], node_order_idx.get(n["id"],9999)))

    # ── Enrich edges ───────────────────────────────────────────────────────
    vis_ids = {n["id"] for n in vis_nodes}
    vis_edges: List[dict] = []
    seen_pairs: Set[Tuple[str,str]] = set()
    for e in edges:
        f, t = e.get("from",""), e.get("to","")
        if (f,t) in seen_pairs or f not in vis_ids or t not in vis_ids:
            continue
        seen_pairs.add((f,t))
        vis_edges.append({"from":f,"to":t,"label":(e.get("label") or "").strip() or None})

    # ── Referenced tests ───────────────────────────────────────────────────
    vis_tests: List[dict] = []
    for t in tests:
        code = str(t.get("test_number","") or "").strip()
        name = str(t.get("test_name","") or "").strip()
        if code and name.startswith(code):
            name = name[len(code):].lstrip(" ,").strip()
        vis_tests.append({"test_code":code,"test_name":name,
                          "test_url":str(t.get("test_url","") or ""),
                          "section":str(t.get("subsection","") or "")})

    # ── Layout-specific data ───────────────────────────────────────────────
    groups:                Optional[List[dict]] = None
    entry_section_node_ids: List[str]           = []
    branch_splits:          List[dict]           = []
    spine_node_ids:         List[str]            = []
    routing_label           = graph_data.get("routing_label","")
    is_multi_zone           = render_mode == "clinical_multi_zone_document"
    fork_style              = "none"
    first_branch_node_id    = None
    entry_type              = "none"

    # Find root in vis_nodes
    vis_incoming = {e.get("to","") for e in edges}
    root_cands   = [n["id"] for n in vis_nodes if n["id"] not in vis_incoming]
    vis_root_id  = root_cands[0] if root_cands else (vis_nodes[0]["id"] if vis_nodes else None)

    if is_multi_zone:
        # Find branch node (first node in vis with 2+ clean children)
        clean_ids = {n["id"] for n in vis_nodes}
        branch_nid_found: Optional[str] = None
        branch_kids_found: List[str]    = []
        if vis_root_id:
            _, branch_nid_found, branch_kids_found = _find_trunk(
                clean_ids, simple_adj, vis_root_id
            )
        if not branch_nid_found:
            # Fall back: find highest out-degree clean node
            best = max(vis_nodes, key=lambda n: len([c for c in simple_adj.get(n["id"],[]) if c in clean_ids]), default=None)
            if best:
                branch_nid_found = best["id"]
                branch_kids_found = [c for c in simple_adj.get(branch_nid_found,[]) if c in clean_ids]

        if branch_nid_found:
            first_branch_node_id = branch_nid_found
            fork_style = "top_level_zone"
            groups, entry_section_node_ids = _compute_zone_groups(
                vis_nodes, simple_adj, edges, branch_nid_found, branch_kids_found
            )
            entry_type = "shared_actions" if entry_section_node_ids else "none"

            if not routing_label:
                for n in vis_nodes:
                    if n["type"] == "decision" and n.get("keyword") == "ROUTE BY":
                        routing_label = n["raw_label"]
                        break
                if not routing_label:
                    routing_label = "ROUTE BY RESULT"

    else:
        # Tree or linear: compute spine + branch splits
        split_nids = {n["id"] for n in vis_nodes
                      if len([c for c in simple_adj.get(n["id"],[]) if c in vis_ids]) >= 2}

        if vis_root_id and split_nids:
            spine_node_ids = _extract_spine(vis_nodes, simple_adj, vis_root_id, split_nids)
            first_branch_node_id = next((n for n in spine_node_ids if n in split_nids), None)
            branch_splits = _compute_branch_splits(vis_nodes, edges, simple_adj, branch_map)
            fork_style = "local" if branch_splits else "none"
        else:
            spine_node_ids = [n["id"] for n in vis_nodes]

        entry_section_node_ids = spine_node_ids

    # ── Stats ──────────────────────────────────────────────────────────────
    max_depth      = max((n["level"] for n in vis_nodes), default=0)
    decision_count = sum(1 for n in vis_nodes if n["type"] == "decision")
    outcome_count  = sum(1 for n in vis_nodes if n["type"] == "outcome")

    # ── PDF / source resolution ─────────────────────────────────────────────
    pdf_url, pdf_asset_id, pdf_origin, has_local_pdf, pdf_match_debug = _resolve_pdf_url(
        source_url, graph_data, title=title
    )
    source_page_url  = _resolve_source_page_url(source_url, graph_data)
    source_asset_type = _resolve_source_asset_type(pdf_url, has_local_pdf, source_url)

    return {
        "title":                   title,
        "source_file":             source_file,
        "source_url":              source_url,
        "reviewed_date":           graph_data.get("reviewed_date",""),
        "updated_date":            graph_data.get("updated_date",""),
        # ── Source document fields ──
        "source_page_url":         source_page_url,
        "pdf_url":                 pdf_url,
        "pdf_asset_id":            pdf_asset_id,
        "pdf_origin":              pdf_origin,
        "has_local_pdf":           has_local_pdf,
        "source_asset_type":       source_asset_type,
        # ── PDF match diagnostics (non-breaking; for /chat algorithm_debug) ──
        "pdf_match_debug":         pdf_match_debug,
        # ── Layout fields ──
        "render_mode":             render_mode,
        "layout":                  render_mode,        # backward compat
        "layout_mode":             render_mode,        # backward compat
        "is_multi_zone":           is_multi_zone,
        "fork_style":              fork_style,
        "entry_type":              entry_type,
        "compact_branch_layout":   render_mode in ("clinical_linear_document","clinical_tree_document"),
        "should_center_spine":     True,
        # ── Routing ──
        "routing_label":           routing_label,
        "first_branch_node_id":    first_branch_node_id,
        # ── Layout data ──
        "entry_section_node_ids":  entry_section_node_ids,
        "spine_node_ids":          spine_node_ids,
        "groups":                  groups,
        "branch_splits":           branch_splits,
        # ── Nodes / edges / tests / footer ──
        "nodes":                   vis_nodes,
        "edges":                   vis_edges,
        "referenced_tests":        vis_tests,
        "footer_blocks":           _extract_footer_blocks(graph_data),
        "stats": {
            "total_nodes":    len(vis_nodes),
            "total_edges":    len(vis_edges),
            "max_depth":      max_depth,
            "has_branches":   fork_style != "none",
            "decision_count": decision_count,
            "outcome_count":  outcome_count,
        },
    }


# ── AlgorithmRenderer class ────────────────────────────────────────────────────

class AlgorithmRenderer:
    """
    Retrieves stored algorithm graph data from the VectorStore and converts
    it into a structured visualization model.  Deterministic; no LLM calls.

    Usage (from main.py):
        renderer = AlgorithmRenderer(vector_store)
        result = renderer.render_for_bundle(evidence_bundle)
        # result: {"algorithm_visualization": {...} | None, "_debug": {...}}

    render_for_bundle() uses two lookup paths so it remains robust when
    evidence grouping is imperfect:

      Path 1 (preferred)  — candidate_tests[*].algorithm_support[*].filename
                            Only candidates where has_algorithm == True.
      Path 2 (fallback)   — authority_sources[*] where source_type == "Algorithm"
                            Used when Path 1 yields no filenames (grouping miss).
      Path 3 (supplement) — evidence_bundle["algorithm_sources"] added by
                            EvidencePackager v0.7.1 top-level index.

    For each filename, the store is queried in order:
      1. Exact get_algorithm_graph(fname)  — fast path
      2. Fuzzy stem match via _fuzzy_graph_lookup()  — handles slight name drift
    """

    def __init__(self, vector_store):
        self._store = vector_store

    # ── Internal helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _normalize_stem(filename: str) -> str:
        """Lowercase, strip extension, collapse separators."""
        import re
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        stem = stem.lower()
        stem = re.sub(r"[\s\-_/\\]+", "-", stem)
        stem = re.sub(r"[^a-z0-9\-]", "", stem)
        return stem.strip("-")

    def _fuzzy_graph_lookup(self, filename: str) -> Optional[dict]:
        """
        Try to retrieve an algorithm graph when the exact filename is not found.

        Strategy (deterministic, metadata-only):
          1. Exact match (already tried by caller — included here for completeness)
          2. Normalized stem match against all stored algorithm_graph filenames

        Returns graph dict or None.
        """
        # Attempt 1: exact
        graph = self._store.get_algorithm_graph(filename)
        if graph:
            return graph

        # Attempt 2: stem match — requires store to expose a listing method
        target_stem = self._normalize_stem(filename)
        if not target_stem:
            return None

        # Use list_algorithm_graphs() if available; degrade gracefully if not
        lister = getattr(self._store, "list_algorithm_graphs", None)
        if not callable(lister):
            return None

        try:
            available = lister()          # returns [{"filename": str, ...}]
        except Exception:
            return None

        for entry in (available or []):
            stored_fname = (entry.get("filename") or "").strip()
            if not stored_fname:
                continue
            if self._normalize_stem(stored_fname) == target_stem:
                graph = self._store.get_algorithm_graph(stored_fname)
                if graph:
                    return graph

        return None

    # ── Primary entry point ────────────────────────────────────────────────────

    def render_for_bundle(self, evidence_bundle: dict, debug: bool = False) -> dict:
        """
        Build an algorithm visualization from the evidence bundle.

        Parameters
        ----------
        evidence_bundle : dict — output of EvidencePackager.package()
        debug           : bool — when True, attach a _debug block with
                          per-stage diagnostic info (non-breaking extra field)

        Returns
        -------
        {
          "algorithm_visualization": {...} | None,
          "_debug": { ... }   # only when debug=True
        }
        """
        candidate_tests = evidence_bundle.get("candidate_tests", [])

        # ── Path 1: candidate_tests.algorithm_support (preferred) ─────────────
        candidate_fnames: List[str] = []
        for ct in candidate_tests:
            if not ct.get("confidence_signals", {}).get("has_algorithm"):
                continue
            for chunk in ct.get("algorithm_support", []):
                fname = (chunk.get("filename") or "").strip()
                if fname and fname not in candidate_fnames:
                    candidate_fnames.append(fname)

        # ── Path 2: authority_sources fallback ────────────────────────────────
        # Triggered when candidate grouping missed algorithm chunks — e.g. the
        # algorithm chunks were retrieved but _group_key() assigned them to a
        # different group that lost has_algorithm=True, or algorithm_support was
        # not populated for that candidate.
        fallback_fnames: List[str] = []
        candidate_set = set(candidate_fnames)

        if not candidate_fnames:
            for chunk in evidence_bundle.get("authority_sources", []):
                if chunk.get("source_type") != "Algorithm":
                    continue
                fname = (chunk.get("filename") or "").strip()
                if fname and fname not in candidate_set and fname not in fallback_fnames:
                    fallback_fnames.append(fname)

        # ── Path 3: top-level algorithm_sources index (EvidencePackager v0.7.1) ─
        all_seen = candidate_set | set(fallback_fnames)
        for src in evidence_bundle.get("algorithm_sources", []):
            fname = (src.get("filename") or "").strip()
            if fname and fname not in all_seen:
                fallback_fnames.append(fname)
                all_seen.add(fname)

        all_fnames = candidate_fnames + fallback_fnames

        debug_info: dict = {
            "candidate_algorithm_filenames": candidate_fnames,
            "fallback_algorithm_filenames":  fallback_fnames,
            "total_candidates_with_algorithm": sum(
                1 for ct in candidate_tests
                if ct.get("confidence_signals", {}).get("has_algorithm")
            ),
            "graph_lookup_results": [],
        }

        if not all_fnames:
            result: dict = {"algorithm_visualization": None}
            if debug:
                result["_debug"] = debug_info
            return result

        # ── Graph lookup + visualization ───────────────────────────────────────
        for fname in all_fnames[:4]:          # cap at 4 attempts
            graph = self._fuzzy_graph_lookup(fname)
            found = graph is not None
            viz   = build_visualization(graph, fname) if graph else None
            node_count = len((viz or {}).get("nodes", []))

            debug_info["graph_lookup_results"].append({
                "filename":    fname,
                "graph_found": found,
                "viz_nodes":   node_count,
                "from_fallback": fname in fallback_fnames,
            })

            if viz and node_count > 0:
                result = {"algorithm_visualization": viz}
                if debug:
                    result["_debug"] = debug_info
                return result

        result = {"algorithm_visualization": None}
        if debug:
            result["_debug"] = debug_info
        return result

    def render_by_filename(self, filename: str) -> dict:
        graph = self._fuzzy_graph_lookup(filename)
        if not graph:
            return {"algorithm_visualization": None}
        viz = build_visualization(graph, filename)
        return {"algorithm_visualization": viz}