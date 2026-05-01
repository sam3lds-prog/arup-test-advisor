"""
store.py  v0.8.0
────────────────────────────────────────────────────────────────────────────
VectorStore — ChromaDB wrapper

Changes from v0.7:
  • bind_provider(provider) — accept an EmbeddingProvider implementation;
    when bound, embeddings are computed externally (BGE on CPU/MPS) and
    passed via Chroma's `embeddings=` / `query_embeddings=` parameters.
    The legacy MiniLM ONNX path is preserved as a sentinel-detected fallback.
  • Collection name now varies by provider:
        legacy MiniLM   →  "arup_knowledge"           (unchanged)
        BGE-base-v1.5   →  "arup_knowledge__bge_base_en_v1_5"
    This makes hard cutover safe — the BGE collection cannot accidentally
    overwrite the legacy one and vice versa.
  • Dimension guard — first-time access against a non-empty collection
    verifies the embedding dimension matches the bound provider. Mismatch
    raises a clear "REINDEX REQUIRED" RuntimeError instead of Chroma's
    cryptic InvalidDimensionException.
  • Lazy collection creation — defer get_or_create_collection() until
    first read/write so module-level VectorStore() instantiation stays cheap.
    Heavy embedding model load is the provider's job and happens in startup.
  • export_all_chunks() — used by the reindex script for hard cutover

Preserved from v0.7:
  • Rich metadata persistence (test_id, test_name, source_url, page_type,
    section_type, relationship_type, section_ids, chunk_type, …)
  • search() deserialises JSON list metadata
  • filename_exists / delete_by_filename / get_algorithm_graph / list_sources
  • clear() — destructive collection delete + recreate
"""

import json
import logging
import os
import uuid
from typing import Dict, List, Optional

import chromadb
from chromadb.config import Settings

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(os.path.dirname(__file__), "../../data/chroma")

# Base collection name. Provider suffix is appended at runtime.
_BASE_COLLECTION = os.getenv("CHROMA_BASE_COLLECTION", "arup_knowledge")

_EXTRA_META_KEYS = (
    "test_id",
    "test_name",
    "source_url",
    "page_type",
    "section_type",
    "relationship_type",
    "pages",
    "row_index",
    "chunk_type",
)

_JSON_LIST_KEYS = {"section_ids"}


def _build_collection_name(base: str, provider) -> str:
    """
    Compose the ChromaDB collection name from base + provider suffix.

    Legacy MiniLM (suffix == "") returns the bare base name so existing
    deployments continue using the historical collection unchanged.
    """
    if provider is None:
        return base
    suffix = getattr(provider, "collection_suffix", "") or ""
    if not suffix:
        return base
    return f"{base}__{suffix}"


class VectorStore:
    """
    Thin wrapper around a persistent ChromaDB collection.

    Two embedding paths
    ───────────────────
    1. Legacy: provider is None OR provider.USE_CHROMA_BUILTIN is True
       → ChromaDB embeds internally with all-MiniLM-L6-v2 (ONNX)
    2. Plugin: provider is a real EmbeddingProvider (e.g. BGE)
       → embeddings computed in Python and passed via ChromaDB params

    The provider is bound AFTER instantiation via bind_provider() so the
    constructor stays cheap and module import never blocks on model load.
    """

    def __init__(self):
        os.makedirs(_DB_PATH, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=_DB_PATH,
            settings=Settings(anonymized_telemetry=False),
        )
        self._provider = None
        self._collection_name = _BASE_COLLECTION
        self._collection_obj = None     # lazy
        self._dimension_verified = False

    # ── Provider binding ──────────────────────────────────────────────────────

    def bind_provider(self, provider) -> None:
        """
        Bind an embedding provider. Resets cached collection so the next
        access uses the provider-specific collection name.

        Idempotent — re-binding the same provider is a no-op.
        """
        if provider is self._provider:
            return
        self._provider = provider
        self._collection_name = _build_collection_name(_BASE_COLLECTION, provider)
        self._collection_obj = None
        self._dimension_verified = False
        logger.info(
            "VectorStore: provider bound = %s collection=%s",
            getattr(provider, "name", "<none>"), self._collection_name,
        )

    @property
    def collection_name(self) -> str:
        return self._collection_name

    @property
    def _uses_builtin_embedder(self) -> bool:
        """True when ChromaDB's internal embedder should be used (legacy path)."""
        return (
            self._provider is None
            or getattr(self._provider, "USE_CHROMA_BUILTIN", False)
        )

    # ── Lazy collection accessor ─────────────────────────────────────────────

    @property
    def _collection(self):
        if self._collection_obj is None:
            self._collection_obj = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            self._verify_dimension_once()
        return self._collection_obj

    def _verify_dimension_once(self) -> None:
        """
        On first access, peek one row and verify embedding dimension matches
        the bound provider. Raise a clear error if the collection was
        previously written by a different model.
        """
        if self._dimension_verified or self._uses_builtin_embedder:
            self._dimension_verified = True
            return
        try:
            count = self._collection_obj.count()
            if count == 0:
                self._dimension_verified = True
                return
            peek = self._collection_obj.peek(limit=1)
            embs = peek.get("embeddings")
            if embs and len(embs) > 0 and embs[0]:
                existing_dim = len(embs[0])
                expected = int(getattr(self._provider, "dimension", 0))
                if expected and existing_dim != expected:
                    raise RuntimeError(
                        f"REINDEX REQUIRED: collection {self._collection_name!r} has "
                        f"embedding dimension {existing_dim} but provider "
                        f"{self._provider.name!r} emits dimension {expected}. "
                        "Run: cd backend && python -m scripts.reindex_knowledge --force"
                    )
            self._dimension_verified = True
        except RuntimeError:
            raise
        except Exception as exc:
            # Peek can fail on very fresh collections; that's harmless.
            logger.debug("VectorStore: dimension verify skipped (%s)", exc)
            self._dimension_verified = True

    # ── Write ──────────────────────────────────────────────────────────────────

    def add_documents(self, chunks: List[Dict]) -> None:
        if not chunks:
            return

        ids, documents, metadatas = [], [], []

        for chunk in chunks:
            ids.append(str(uuid.uuid4()))
            documents.append(chunk["text"])

            meta: Dict = {
                "filename":    str(chunk.get("filename", "")),
                "source_type": str(chunk.get("source_type", "General")),
                "chunk_index": str(chunk.get("chunk_index", 0)),
            }

            for key in _EXTRA_META_KEYS:
                val = chunk.get(key)
                if val is not None:
                    meta[key] = json.dumps(val) if isinstance(val, (list, dict)) else str(val)

            if "section_ids" in chunk and chunk["section_ids"] is not None:
                meta["section_ids"] = json.dumps(chunk["section_ids"])

            metadatas.append(meta)

        # Compute embeddings in our provider when present (BGE path).
        # In legacy MiniLM mode, hand the raw documents to ChromaDB's
        # built-in embedder.
        BATCH = 100
        if self._uses_builtin_embedder:
            for i in range(0, len(ids), BATCH):
                self._collection.add(
                    ids=ids[i:i + BATCH],
                    documents=documents[i:i + BATCH],
                    metadatas=metadatas[i:i + BATCH],
                )
        else:
            embeddings = self._provider.encode_documents(documents)
            for i in range(0, len(ids), BATCH):
                self._collection.add(
                    ids=ids[i:i + BATCH],
                    documents=documents[i:i + BATCH],
                    metadatas=metadatas[i:i + BATCH],
                    embeddings=embeddings[i:i + BATCH],
                )

    def add_precomputed(
        self,
        ids: List[str],
        documents: List[str],
        metadatas: List[Dict],
        embeddings: List[List[float]],
    ) -> None:
        """
        Insert chunks where the caller has already computed embeddings.
        Used by the reindex script to avoid re-encoding when migrating
        between providers (it pulls texts from the legacy collection,
        re-encodes once with BGE, and writes here).

        Bypasses provider — caller must guarantee dimensions match the
        currently-bound provider on this VectorStore.
        """
        if not ids:
            return
        BATCH = 100
        for i in range(0, len(ids), BATCH):
            self._collection.add(
                ids=ids[i:i + BATCH],
                documents=documents[i:i + BATCH],
                metadatas=metadatas[i:i + BATCH],
                embeddings=embeddings[i:i + BATCH],
            )

    # ── Deduplication (unchanged from v0.7) ──────────────────────────────────

    def filename_exists(self, filename: str) -> bool:
        if self._collection.count() == 0:
            return False
        results = self._collection.get(
            where={"filename": filename},
            limit=1,
            include=[],
        )
        return len(results["ids"]) > 0

    def delete_by_filename(self, filename: str) -> int:
        if self._collection.count() == 0:
            return 0
        results = self._collection.get(where={"filename": filename}, include=[])
        ids_to_delete = results["ids"]
        if ids_to_delete:
            self._collection.delete(ids=ids_to_delete)
        return len(ids_to_delete)

    # ── Read ───────────────────────────────────────────────────────────────────

    def search(self, query: str, n_results: int = 6,
               where: Optional[dict] = None) -> List[Dict]:
        """
        Vector search with optional ChromaDB metadata filter.

        Same return shape as v0.7 — chunks are dicts with id, text, score
        (1 - cosine_distance), and all stored metadata. Reranker (if any)
        adds rerank_score / combined_score / etc. downstream.
        """
        total = self._collection.count()
        if total == 0:
            return []

        n = min(n_results, total)

        query_kwargs = dict(
            n_results=n,
            include=["documents", "metadatas", "distances"],
        )
        if where:
            query_kwargs["where"] = where

        # Choose the embedding path
        if self._uses_builtin_embedder:
            query_kwargs["query_texts"] = [query]
        else:
            query_kwargs["query_embeddings"] = self._provider.encode_queries([query])

        try:
            results = self._collection.query(**query_kwargs)
        except Exception:
            # Fallback: retry without filter (common Chroma edge case)
            try:
                fallback = dict(
                    n_results=n,
                    include=["documents", "metadatas", "distances"],
                )
                if self._uses_builtin_embedder:
                    fallback["query_texts"] = [query]
                else:
                    fallback["query_embeddings"] = self._provider.encode_queries([query])
                results = self._collection.query(**fallback)
            except Exception:
                return []

        chunks: List[Dict] = []
        if not results.get("ids") or not results["ids"][0]:
            return chunks

        for i, doc_id in enumerate(results["ids"][0]):
            distance = (results.get("distances") or [[1.0]])[0][i]
            score = max(0.0, 1.0 - distance)

            meta = dict(results["metadatas"][0][i])

            for key in _JSON_LIST_KEYS:
                if key in meta:
                    try:
                        meta[key] = json.loads(meta[key])
                    except Exception:
                        pass

            chunks.append({
                "id":    doc_id,
                "text":  results["documents"][0][i],
                "score": round(score, 4),
                **meta,
            })

        return chunks

    def count(self) -> int:
        return self._collection.count()

    def list_sources(self) -> List[Dict]:
        total = self.count()
        if total == 0:
            return []

        all_docs = self._collection.get(include=["metadatas"])
        sources: Dict[str, Dict] = {}

        for meta in all_docs["metadatas"]:
            fname = meta.get("filename", "Unknown")
            if fname not in sources:
                sources[fname] = {
                    "filename":    fname,
                    "source_type": meta.get("source_type", "General"),
                    "chunks":      0,
                }
            sources[fname]["chunks"] += 1

        return sorted(sources.values(), key=lambda s: s["filename"])

    # ── Algorithm graph lookup (unchanged from v0.7) ─────────────────────────

    def get_algorithm_graph(self, filename: str) -> Optional[dict]:
        if self._collection.count() == 0:
            return None
        try:
            results = self._collection.get(
                where={
                    "$and": [
                        {"filename":   {"$eq": filename}},
                        {"chunk_type": {"$eq": "algorithm_graph"}},
                    ]
                },
                include=["documents"],
            )
        except Exception:
            return None

        if not results or not results.get("ids") or not results["ids"]:
            return None

        doc = results["documents"][0] if results.get("documents") else ""
        if not doc:
            return None

        prefix = "ALGORITHM_GRAPH_DATA:"
        if doc.startswith(prefix):
            doc = doc[len(prefix):]
        try:
            return json.loads(doc)
        except Exception:
            return None

    # ── Admin ──────────────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Delete and recreate the active collection (destructive)."""
        try:
            self._client.delete_collection(self._collection_name)
        except Exception:
            pass
        self._collection_obj = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self._dimension_verified = False

    def export_all_chunks(self) -> List[Dict]:
        """
        Read every chunk back out (id, document, metadata) for migration.
        Used by the reindex script to copy from legacy collection to BGE.

        Embeddings are NOT exported — the migration recomputes them with
        the new provider.
        """
        if self._collection.count() == 0:
            return []
        all_data = self._collection.get(include=["documents", "metadatas"])
        out: List[Dict] = []
        ids = all_data.get("ids", []) or []
        docs = all_data.get("documents", []) or []
        metas = all_data.get("metadatas", []) or []
        for i, _id in enumerate(ids):
            out.append({
                "id":       _id,
                "document": docs[i] if i < len(docs) else "",
                "metadata": dict(metas[i]) if i < len(metas) else {},
            })
        return out
