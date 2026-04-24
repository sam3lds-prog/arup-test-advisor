# Plain-Text Clinical Report Export Feature

## Overview

A new plain-text export feature has been added to the AI Test Advisor that transforms clinical responses into print-optimized documents suitable for clinical documentation and printing.

## Implementation Summary

### 1. Backend Components

#### `backend/agents/plain_text_formatter.py`
- **Main formatter module** that transforms AI Test Advisor response data into plain-text reports
- **Key functions:**
  - `format_clinical_report()` - Core formatting function
  - `format_from_chat_response()` - Convenience wrapper for frontend data
  - Helper functions for text wrapping, markdown stripping, and depersonalization

#### `backend/main.py`
- **New API endpoint:** `POST /export/plain-text`
- **Input:** Session ID, message index, and full response data
- **Output:** Plain-text report with `text/plain` content type

### 2. Frontend Components

#### `frontend/src/components/MessageBubble.jsx`
- **New component:** `ExportPlainTextButton`
  - Located between Word and PDF export buttons
  - Calls backend API endpoint
  - Downloads formatted report as `.txt` file
- **New icon:** `DocumentIcon` (SVG icon for plain-text export)

## Output Format

The plain-text clinical report includes:

1. **Header Section** (centered)
   - ARUP Laboratories branding
   - Document title
   - Generation timestamp

2. **Clinical Query** (quoted, indented)
   - Original user query

3. **Recommended Tests** (numbered list)
   - Test name (UPPERCASE)
   - ARUP code (when available)
   - Rank (Primary/Secondary/Reflex)
   - Clinical rationale
   - Specimen type and TAT (when available)

4. **Clinical Rationale** (rewritten paragraphs)
   - Response text with markdown stripped
   - Citation markers converted to (Ref N)
   - Conversational language converted to professional style
   - First-person references removed

5. **Reference Sources** (numbered citations)
   - Source type (Algorithm, Consult Topic, etc.)
   - Document name
   - Excerpt (when available)

6. **Footer**
   - Confidence score (when available)
   - Clinical disclaimer
   - Page number placeholder

## Key Features

### Design Principles

- **Plain text only** - No formatting codes, suitable for any text editor
- **Print-optimized** - 80-character width, clear section breaks
- **Page-break aware** - Inserts `[PAGE BREAK]` marker when content exceeds one page
- **Graceful degradation** - Handles missing data fields without breaking
- **Clinical accuracy** - Preserves all clinical content while improving readability
- **Professional style** - Converts conversational AI language to clinical documentation style

### Text Processing

1. **Markdown stripping:**
   - Removes bold/italic markers
   - Converts hyperlinks to plain text
   - Removes code markers
   - Converts citation markers `[SOURCE N]` to `(Ref N)`

2. **Depersonalization:**
   - Removes first-person references ("I recommend" → "This analysis recommends")
   - Removes conversational openings ("Based on...")
   - Removes hedging phrases ("It appears that...")
   - Converts "your patient" to "the patient"

3. **Text wrapping:**
   - Automatic line wrapping at 80 characters
   - Preserves paragraph breaks
   - Indented block quotes for clinical query

## Usage

### From UI
1. Navigate to any AI Test Advisor response
2. Click the document icon (plain-text export button)
3. File downloads as `arup-response_[session]_[date]_[time].txt`

### From API
```bash
curl -X POST http://localhost:8010/export/plain-text \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "session_12345",
    "message_index": 2,
    "response_data": {
      "text": "What tests for hypothyroidism?",
      "answer": "...",
      "recommendations": [...],
      "citations": [...],
      "confidence": {"score": 85}
    }
  }' \
  --output report.txt
```

## File Locations

- Backend formatter: `backend/agents/plain_text_formatter.py`
- Backend endpoint: `backend/main.py` (lines 892-927)
- Frontend button: `frontend/src/components/MessageBubble.jsx` (lines 3172-3290)
- Frontend icon: `frontend/src/components/MessageBubble.jsx` (lines 2901-2913)
- Test scripts:
  - `backend/test_plain_text_formatter.py` (basic formatter tests)
  - `backend/test_export_endpoint.py` (frontend integration test)

## Testing

### Run formatter tests:
```bash
cd backend
source .venv/bin/activate
python test_plain_text_formatter.py
```

### Run integration test:
```bash
cd backend
source .venv/bin/activate
python test_export_endpoint.py
```

## Error Handling

- **Missing data:** Gracefully omits unavailable fields (no error thrown)
- **Empty recommendations:** Shows "No specific test recommendations available"
- **Missing citations:** Shows "No citations available"
- **Missing confidence score:** Omits confidence section
- **Missing timestamp:** Uses current time

## Future Enhancements (Optional)

Potential improvements if needed:

1. **Page numbering:** Add actual page numbers instead of placeholder
2. **Custom headers:** Allow customizable organization branding
3. **Footer customization:** Configurable disclaimer text
4. **Section reordering:** User-selectable section order
5. **Field filtering:** Allow users to exclude specific sections
6. **Print preview:** Show formatted text before download
7. **Batch export:** Export multiple responses in one document

## Notes

- The formatter is purely deterministic (no LLM calls)
- All clinical content is preserved exactly as provided
- Text processing follows ARUP clinical documentation standards
- Compatible with all response types (recommendations, algorithms, clarifications)
- Works independently of FormattingAgent (different use case)

---

**Version:** 1.0.0  
**Last Updated:** April 24, 2026  
**Status:** Production ready
