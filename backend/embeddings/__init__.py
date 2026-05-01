"""
embeddings package
────────────────────────────────────────────────────────────────────────────
Pluggable embedding providers for the ARUP AI Test Advisor.

Phase 1 of the Hugging Face enhancement plan. The legacy MiniLM path
(ChromaDB built-in ONNX model) is preserved as a fallback so the system
can roll back at any time by setting EMBEDDING_PROVIDER=minilm.
"""

from .embedding_provider import EmbeddingProvider  # noqa: F401
