# ARUP AI Test Advisor — Local POC

A full-stack, multi-agent clinical test selection assistant that runs entirely on your MacBook.
Upload your ARUP content (PDFs, CSVs), then ask natural-language questions to get grounded,
citation-backed test recommendations.

---

## Current status
Local proof of concept for grounded ARUP-only clinical test recommendation.
Runs fully on a MacBook with local ChromaDB and Claude API calls for reasoning.

## Quick start (one command)

```bash
cd arup-test-advisor
./start.sh
```

The script will:
1. Prompt for your Anthropic API key (saved to `backend/.env` for future runs)
2. Create a Python virtual environment and install all dependencies
3. Install Node/npm packages
4. Start the FastAPI backend on **http://localhost:8000**
5. Start the React frontend on **http://localhost:5173**

Open **http://localhost:5173** in your browser.

---

## Requirements

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.9+ | https://python.org |
| Node.js | 18+ | https://nodejs.org |
| Anthropic API key | — | https://console.anthropic.com |

---

## Manual setup (if start.sh fails)

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

---

## Uploading ARUP content

The app accepts **PDF**, **CSV**, **TSV**, and **TXT** files.

### Source type auto-detection

The system automatically classifies each file into an authority tier based on its filename.
Rename your files to match these patterns for best results:

| Source type | Include in filename | Authority |
|-------------|---------------------|-----------|
| Algorithm | `algorithm`, `pathway` | Highest |
| Consult Topic | `consult`, `topic` | High |
| Fact Sheet | `factsheet`, `fact-sheet` | Medium |
| Test Directory | `directory`, `catalog` | Baseline |

**Examples:**
- `APS_diagnostic_algorithm.pdf` → Algorithm
- `antiphospholipid_consult_topic.pdf` → Consult Topic
- `lupus_anticoagulant_factsheet.csv` → Fact Sheet
- `arup_test_directory_export.csv` → Test Directory

### CSV format

Any CSV with column headers works. Each row becomes one searchable chunk.
Recommended columns for test directory CSVs:

```
TestName, TestCode, Specimen, Volume, Stability, TAT, Methodology, ClinicalUse
```

---

## Architecture

```
User ──► React UI (Vite, port 5173)
              │
              ▼ /api/chat (proxied)
         FastAPI (port 8000)
              │
        Agent Orchestrator
         ├── Prompt Agent       ← classifies intent, extracts concepts (Claude API)
         ├── Retrieval Agent    ← multi-query ChromaDB search + deduplication
         ├── Response Agent     ← grounded answer generation (Claude API, ARUP-only)
         └── Confidence Agent   ← rule-based score (no model call)
              │
         Secure Knowledge Layer
         ├── ChromaDB (local, persistent)   ← vector index
         └── Document Processor             ← PDF/CSV/TXT ingestion
```

### Agent contracts

| Agent | Input | Output | Claude API? |
|-------|-------|--------|------------|
| Prompt Agent | Raw query + history | Structured intent JSON | Yes |
| Retrieval Agent | Intent JSON | Top-12 evidence chunks | No (ChromaDB only) |
| Response Agent | Query + intent + evidence | Grounded answer + citations | Yes |
| Confidence Agent | Evidence + response | Score, level, factors | No (rule-based) |

---

## API reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Status + chunk count |
| `/upload` | POST | Upload a PDF/CSV/TXT |
| `/documents` | GET | List indexed sources |
| `/documents` | DELETE | Clear all indexed content |
| `/chat` | POST | Submit a query |

### Chat request

```json
{
  "query": "What ARUP test is recommended for diagnosing antiphospholipid syndrome?",
  "history": []
}
```

### Chat response

```json
{
  "answer": "For suspected APS with recurrent pregnancy loss… [SOURCE 1]",
  "recommendations": [
    {
      "test_name": "Antiphospholipid Antibody Evaluation, Comprehensive",
      "test_code": "2008490",
      "rank": "primary",
      "rationale": "Covers all three antibody categories required for APS diagnosis [SOURCE 1]",
      "specimen": "Gold tube 3 mL + Blue tube 2.7 mL citrate",
      "tat": "3–5 days",
      "source_refs": [1]
    }
  ],
  "citations": [
    {
      "number": 1,
      "source_type": "Algorithm",
      "document": "APS_diagnostic_algorithm.pdf",
      "excerpt": "Initial workup should include testing for all three antibody types…"
    }
  ],
  "confidence": {
    "score": 87,
    "level": "high",
    "factors": ["Algorithm-level guidance present", "High semantic similarity"],
    "needs_review": false
  },
  "follow_up_questions": [],
  "disclaimer": "Recommendations are grounded in uploaded ARUP content…"
}
```

---

## Data storage

All vector data is stored locally in `data/chroma/`. Nothing is sent to external services
except Claude API calls (query analysis and response generation).

To clear the knowledge base: click the trash icon in the UI or call `DELETE /documents`.

---

## Troubleshooting

**"No module named 'chromadb'"**
```bash
cd backend && source .venv/bin/activate && pip install -r requirements.txt
```

**"Could not reach the backend"**
Make sure the Python server is running on port 8000. Check the terminal for errors.

**ChromaDB downloads a model on first run (~80 MB)**
The `all-MiniLM-L6-v2` embedding model is downloaded automatically the first time
you upload a document. Subsequent uploads are fast.

**Scanned PDFs return no content**
The processor uses text extraction only. Scanned PDFs need OCR pre-processing
(e.g. Adobe Acrobat, or `ocrmypdf` CLI) before uploading.

**Source type shows "General" for all files**
Rename files to include the keywords listed in the "Source type auto-detection" table above.

---

## Roadmap (post-POC)

- [ ] EHR/FHIR integration layer (ServiceRequest output schema)
- [ ] Formatting Agent (structured UI schema per query type)
- [ ] Safety & Compliance Agent (disclaimer enforcement, PHI redaction)
- [ ] Human review queue (low-confidence routing)
- [ ] Evaluation harness (gold dataset + groundedness checks)
- [ ] Auth layer (API key per client lab)
