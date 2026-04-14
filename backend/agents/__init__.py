# Agents package
#
# Contains all LLM and rule-based agents that power the AI Test Advisor pipeline:
#   - PromptAgent          : analyses clinical queries and extracts structured intent
#   - RetrievalPlanner     : converts intent into a prioritised vector-search plan
#   - RetrievalAgent       : executes the retrieval plan against the VectorStore
#   - EvidencePackager     : groups and validates retrieved chunks into an evidence bundle
#   - ResponseAgent        : generates grounded test recommendations from the evidence bundle
#   - ConfidenceAgent      : scores evidence strength with a rule-based confidence signal
#   - FormattingAgent      : converts structured JSON responses into a UI component schema
#   - AlgorithmRenderer    : transforms stored algorithm graphs into flowchart visualisations
#   - ContextExtractor     : extracts and summarises clinical context from conversation turns
#   - SessionStore         : persists sessions, clinical context, and clarification state (SQLite)
#   - AssetStore           : stores and resolves uploaded PDF assets (SQLite + disk)
#   - DesignLibraryStore   : persists design-system component definitions and revision history
#   - DesignerRoutes       : FastAPI router for the Designer Panel (preferences, preview, library)
