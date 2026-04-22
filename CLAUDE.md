# ARUP AI Test Advisor — Project Guide for Claude

**Version:** 1.0.0  
**Last Updated:** April 22, 2026  
**Purpose:** Proof-of-concept clinical laboratory test selection application for ARUP Laboratories

---

## Architecture Overview

### Core Pipeline (8 Steps)

The application processes user queries through a sequential agent pipeline:

1. **PromptAgent** → Intent extraction and clinical context awareness
2. **RetrievalAgent** → Vector search across indexed ARUP content
3. **EvidencePackager** → Authority-weighted bundling with confidence signals
4. **ResponseAgent** → Citation-grounded answer generation
5. **ConfidenceAgent** → Multi-factor scoring and needs-review detection
6. **AlgorithmRenderer** → Graph-based diagnostic pathway visualization
   - Fidelity-first renderer (when chartflow rules approved)
   - Clinical renderer (fallback, always available)
7. **FormattingAgent** → UI schema composition (deterministic, no LLM)
8. **MessageBubble (UISchemaRenderer)** → Client-side React component rendering

**Key Properties:**
- Steps 1-6 use Claude API (Haiku for cost-sensitive steps, Sonnet for response)
- Step 7 is fully deterministic (no LLM calls)
- Evidence bundle flows through all steps after retrieval
- Designer Panel provides hot-reload formatting preferences via `formatting_rules.json`

---

## Technology Stack

### Backend
- **Framework:** FastAPI + uvicorn (Python 3.11)
- **Vector Storage:** ChromaDB with `all-MiniLM-L6-v2` embeddings (~80MB ONNX model)
- **Session State:** SQLite (`backend/data/sessions.db`)
- **AI APIs:** 
  - Anthropic Claude (Haiku 4.5 + Sonnet 4.5)
  - OpenAI GPT-4.1 (external code review in automation pipeline only)
- **Document Processing:** pypdf, pandas
- **Environment:** Local MacBook deployment, port 8010

**Critical Startup Pattern:**
```python
@app.on_event("startup")
async def startup():
    init_db()  # ChromaDB model initialization belongs HERE, not at module import
```
This prevents port binding delays — uvicorn binds to port immediately while ChromaDB loads in background.

### Frontend
- **Framework:** React 18 + Vite (port 5174)
- **Styling:** CSS variables design token system (no raw hex values)
- **Build:** `npm run dev` for hot-reload, `npm run build` for production
- **Key Components:** ChatWindow, MessageBubble, DesignerPanel, UploadPanel

### File Organization
```
~/projects/AI Test Advisor/
├── backend/
│   ├── agents/                    # All 8 pipeline agents
│   ├── knowledge/                 # DocumentProcessor, VectorStore
│   ├── data/
│   │   ├── sessions.db           # SQLite session store
│   │   └── chromadb/             # Vector embeddings
│   ├── main.py                    # FastAPI app entry point
│   └── .venv/                     # Python virtual environment
├── frontend/
│   ├── src/
│   │   ├── components/           # React components
│   │   └── assets/               # CSS, images
│   └── dist/                      # Production build output
├── data/
│   └── assets/                    # PDF assets (algorithms)
└── start.sh                       # Unified launcher script
```

---

## ARUP Design System Constraints

**CRITICAL:** All components must use CSS variables, never raw hex values.

### Typography
- **Typeface:** Roboto exclusively (`fontFamily: "'Roboto', Helvetica, Arial, sans-serif"`)
- **Body Text:** Dark Sky `var(--dark-sky)` (#171717) — the ONLY permitted body text color
- **Links:** Lab Blue `var(--lab-blue)` (#306385) — hyperlinks and linked table cells ONLY
- **Rule:** Never use Lab Blue for non-link text; never use foundation reds for body text

### Foundation Colors
- **Primary:** ARUP Red `var(--primary)` (#AE132A) — primary buttons, key highlights, algorithm start nodes
- **Secondary:** Red Rock `var(--secondary)` (#6D0020) — secondary buttons, filled card backgrounds
- **Accent (Granite):** `var(--accent)` (#77787B) — TABLE borders, modal dividers, standard UI separators
- **Accent3 (Badlands):** `var(--accent3)` (#DEDFE0) — CARD borders, chart grid lines, low-emphasis outlines
- **White:** `var(--white)` (#FFFFFF) — card/page backgrounds

**Border Token Rules (enforced globally):**
- Cards: ALWAYS `1px solid var(--accent3)` (Badlands) — never Granite
- Tables: ALWAYS `1px solid var(--accent)` (Granite) — never Badlands
- Do NOT mix these tokens on the same component

### Semantic Colors (Status Only)
- Danger: `var(--danger)` + `var(--danger-container)` — errors, conflicts
- Info: `var(--info)` + `var(--info-container)` — neutral alerts
- Warning: `var(--warning)` + `var(--warning-container)` — caution states
- Success: `var(--positive)` + `var(--positive-container)` — approved states

**Rule:** Never use semantic colors for charts or decoration — status communication only.

### Spacing & Layout
- **Card padding:** 24px standard
- **Button shape:** Pill-shaped (`borderRadius: 'var(--btn-radius)'` typically 18-20px)
- **Grid gaps:** 16px between cards, 12px between form elements

---

## Critical Development Patterns

### 1. Verification-First Deployment
**ALWAYS FOLLOW THIS WORKFLOW:**
1. Upload live production files before ANY code generation
2. Request explicit diff comparison between generated code and live files
3. Confirm no regressions before applying changes
4. Changes are surgical patches, not full rewrites

**Why:** Generated files have consistently drifted from live codebase state in:
- Function signatures and export names
- Response shapes and data structures
- Import paths between modules
- Missing components or features

### 2. Safe Import Pattern (Optional Modules)
```python
try:
    from agents.fidelity_renderer import FidelityRenderer
    from agents.chartflow_rules import ChartflowRuleStore
    _fidelity_available = True
except ImportError:
    _fidelity_available = False
    FidelityRenderer = None
```
Prevents full app crash if optional agent modules are missing during development.

### 3. ChromaDB Initialization
- Heavy model loading (`all-MiniLM-L6-v2` ONNX, ~80MB) → inside `@app.on_event("startup")`
- NOT at module import time (delays port binding)
- VectorStore instantiation outside startup is fine, but embedding model loads on first use

### 4. Session ID Propagation
**Known integration gap:**
`ChatWindow.jsx` requires `props.onSessionChange?.(sid)` callback to be wired wherever session ID is first set. This enables Designer Panel Schema Inspector to function correctly.

```jsx
// In ChatWindow.jsx, when session is created/changed:
if (props.onSessionChange && sessionId) {
  props.onSessionChange(sessionId)
}
```

### 5. PDF Asset Serving
- Local PDFs: `data/assets/pdfs/` with metadata in `data/assets/assets.db`
- Served via same-origin: `/api/assets/pdf/{asset_id}`
- Remote ARUP iframing: PROHIBITED (CSP headers block it)
- Split-view rendering: Two-pane layout (algorithm left, local PDF right)

---

## Known Gotchas & Solutions

### Virtual Environment Rebuild
If `.venv` breaks after directory moves or Python upgrades:
```bash
cd ~/projects/AI\ Test\ Advisor/backend
rm -rf .venv
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
`.env` file (API keys) is unaffected and persists.

### Port Conflicts
`start.sh` handles port conflicts automatically:
- Kills existing processes on ports 8010 (backend) and 5174 (frontend)
- Waits until ports are actually free (macOS TIME_WAIT can hold for 60s)
- Auto-increments to next available port if stuck after 15s

### Algorithm Renderer Type Normalization
```python
# CRITICAL: _safe_node_type() must run BEFORE renderable-type filtering
# Adjacency data must be Dict[str, List[str]] (string IDs only)
```
Prevents `TypeError: unhashable type: 'dict'` in `_compute_groups()`.

### ReportLab PDF Generation
- Multi-column card layouts: Use nested `Table` objects (no `KeepTogether` needed)
- Multi-line strings: Consistent quote styles with escaped internal apostrophes
- Style helpers: Never pass `ParagraphStyle` objects to functions expecting dicts

### Formatting Agent Feedback Loop
LLM returns ONLY `rules_patch` and `preview_props_patch`. Server performs deep-merge to prevent accidental field loss from full object replacement.

---

## Environment Variables

Required in `backend/.env`:
```bash
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_PROMPT_MODEL=claude-haiku-4-5-20251001    # Cost-sensitive steps
CLAUDE_RESPONSE_MODEL=claude-sonnet-4-5-20250514  # Response generation

# Automation pipeline only (optional)
OPENAI_API_KEY=sk-...  # For external code review via GPT-4.1
```

---

## Automation Pipeline (Current State)

**Three-tool orchestration:**
1. **Claude.ai (this project):** Architectural validation, persistent context
2. **Claude Code CLI:** ONLY tool that directly edits local files (headless via `claude -p`)
3. **GPT-4.1 (OpenAI API):** External code reviewer (structured JSON from `git diff`)

**Flow:**
- FastAPI orchestrator (~150 lines) coordinates the pipeline
- Human trigger: Streamlit UI or ChatGPT MCP connector via ngrok
- Human approval gate: Review diff + GPT-4.1 review before any git commit/push

**Upgrade Path:**
- Moving from Streamlit UI to ChatGPT MCP connector trigger
- Alternatives evaluated: CodeRabbit CLI, Sourcery CLI (viable GPT-4.1 replacements)

---

## Designer Panel Architecture

**Sub-navigation tabs:**
- **Preview:** Live rendering of current formatting rules
- **Rules:** Editable JSON for typography, spacing, colors, buttons, cards, tables
- **Tokens:** Design system library reference
- **History:** Rule change history with rollback
- **Feedback:** LLM-powered patch-based refinement loop
- **Schema Inspector:** Live ui_schema view (requires session ID propagation from ChatWindow)

**Data Flow:**
1. User edits rules in Designer Panel → POST `/designer/update`
2. FormattingAgent reloads rules → generates new ui_schema
3. ChatWindow re-renders with updated schema
4. Schema Inspector shows live structure (when session ID wired correctly)

---

## Testing & Verification

### Health Check
```bash
curl http://localhost:8010/health
```
Returns document count, models in use, pipeline status.

### Algorithm Debug Route
```bash
curl http://localhost:8010/documents/algorithms
```
Lists all indexed algorithm files and whether each has a stored `algorithm_graph` payload.

### Frontend Hot-Reload
Vite dev server watches `frontend/src/` — changes reflect instantly without full rebuild.

---

## Common Development Tasks

### Add New Document Type
1. Update `DOCUMENT_TYPE_HINT_MAP` in `main.py`
2. Add processor logic in `knowledge/processor.py`
3. Add badge class in `App.jsx` SOURCE_CONFIG
4. Rebuild index: DELETE `/documents` → re-upload

### Modify Agent Behavior
1. Locate agent file in `backend/agents/`
2. Edit system prompt or processing logic
3. Restart backend (uvicorn hot-reloads in dev mode)

### Update Design Tokens
1. Edit `frontend/src/assets/design-tokens.json`
2. Changes reflect immediately (Vite hot-reload)
3. Never use raw hex values — always reference CSS variables

### Debug Algorithm Rendering
1. Check `/documents/algorithms` — verify `graph_available=true`
2. If false: Re-upload algorithm file (graph chunk not stored during ingest)
3. If true but rendering fails: Check `algorithm_renderer.py` node type filters

---

## Preferred Document Style

For executive documents and reports, prefer **SpecLoop AI style:**
- Numbered sections with short bold labels
- Three-column color cards for key points
- Four-box metric callouts
- Horizontal flow diagrams
- Avoid generic report formats (long prose blocks, bullet-heavy layouts)

---

## Principles & Philosophy

1. **Live codebase is source of truth** — Generated files must use actual uploaded source as base
2. **Incremental over rewrite** — Add features, preserve existing functionality unless explicitly redesigning
3. **Cross-tool validation** — Compare Claude's analysis with other AI tools before finalizing
4. **Architecture preservation** — Existing architecture stays unless Sam explicitly initiates redesign
5. **Autonomous automation** — Prefer tool-to-tool communication with single human trigger + approval gate

---

## Quick Reference Commands

```bash
# Start development servers (backend + frontend)
./start.sh

# Production build and serve
./start.sh --prod

# Backend only (API development)
./start.sh --api-only

# Rebuild virtual environment
cd backend && rm -rf .venv && python3.11 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

# Health check
curl http://localhost:8010/health

# View algorithm indexing status
curl http://localhost:8010/documents/algorithms
```

---

**Remember:** Always verify before deploying. Upload live files → request diff → confirm no regressions → apply changes.
