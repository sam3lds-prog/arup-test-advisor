"""
store.py  v0.7.0
────────────────────────────────────────────────────────────────────────────
VectorStore — ChromaDB wrapper

Changes from v0.6:
  • Persists richer chunk metadata from the processor:
      test_id, test_name, source_url, page_type, section_type,
      relationship_type, section_ids (serialised as JSON string)
  • search() deserialises list fields back to Python so EvidencePackager
    can group by test_id/test_name instead of filename heuristics
  • No breaking changes to existing write/read/dedup API
"""

import json
import os
import uuid
from typing import List, Dict, Optional

import chromadb
from chromadb.config import Settings

_DB_PATH = os.path.join(os.path.dirname(__file__), "../../data/chroma")

# Extra metadata keys carried from processor chunks into ChromaDB.
# All values are coerced to str — ChromaDB only accepts str/int/float/bool.
_EXTRA_META_KEYS = (
    "test_id",
    "test_name",
    "source_url",
    "page_type",
    "section_type",
    "relationship_type",
    "pages",
    "row_index",
    "chunk_type",          # "algorithm_graph" for AlgorithmRenderer chunks
)

# Keys that may be stored as JSON-encoded lists and need round-trip deserialisation
_JSON_LIST_KEYS = {"section_ids"}


class VectorStore:
    """
    Thin wrapper around a persistent ChromaDB collection.

    ChromaDB's default embedding function (all-MiniLM-L6-v2 via onnxruntime)
    is used — no external embedding API required (~80 MB, downloaded on first use).
    """

    def __init__(self):
        os.makedirs(_DB_PATH, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=_DB_PATH,
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name="arup_knowledge",
            metadata={"hnsw:space": "cosine"},
        )

    # ── Write ──────────────────────────────────────────────────────────────────

    def add_documents(self, chunks: List[Dict]) -> None:
        if not chunks:
            return

        ids, documents, metadatas = [], [], []

        for chunk in chunks:
            ids.append(str(uuid.uuid4()))
            documents.append(chunk["text"])

            # Required fields
            meta: Dict = {
                "filename":    str(chunk.get("filename", "")),
                "source_type": str(chunk.get("source_type", "General")),
                "chunk_index": str(chunk.get("chunk_index", 0)),
            }

            # Rich optional metadata — persist whatever the processor provides
            for key in _EXTRA_META_KEYS:
                val = chunk.get(key)
                if val is not None:
                    meta[key] = json.dumps(val) if isinstance(val, (list, dict)) else str(val)

            # section_ids is a list — serialise to JSON string
            if "section_ids" in chunk and chunk["section_ids"] is not None:
                meta["section_ids"] = json.dumps(chunk["section_ids"])

            metadatas.append(meta)

        BATCH = 100
        for i in range(0, len(ids), BATCH):
            self._collection.add(
                ids=ids[i : i + BATCH],
                documents=documents[i : i + BATCH],
                metadatas=metadatas[i : i + BATCH],
            )

    # ── Deduplication (unchanged from v0.5.1) ─────────────────────────────────

    def filename_exists(self, filename: str) -> bool:
        """Return True if any chunks with this filename are already indexed."""
        if self._collection.count() == 0:
            return False
        results = self._collection.get(
            where={"filename": filename},
            limit=1,
            include=[],
        )
        return len(results["ids"]) > 0

    def delete_by_filename(self, filename: str) -> int:
        """Remove all chunks for a filename. Returns count deleted."""
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

        Parameters
        ----------
        query     : semantic search text
        n_results : max results to return
        where     : optional ChromaDB `where` filter dict, e.g.
                    {"source_type": {"$eq": "Algorithm"}}
                    When provided, the filter is pushed into the Chroma query
                    itself so only matching chunks are semantically ranked —
                    much more effective than post-retrieval filtering.

        Returns
        -------
        List of chunk dicts enriched with score and deserialized metadata.
        """
        total = self._collection.count()
        if total == 0:
            return []

        n = min(n_results, total)

        # Build query kwargs — only pass `where` when supplied and non-empty
        query_kwargs = dict(
            query_texts=[query],
            n_results=n,
            include=["documents", "metadatas", "distances"],
        )
        if where:
            query_kwargs["where"] = where

        try:
            results = self._collection.query(**query_kwargs)
        except Exception:
            # Fallback: retry without filter (e.g. if no chunks match the filter
            # and Chroma raises because n_results > matching count)
            try:
                results = self._collection.query(
                    query_texts=[query],
                    n_results=n,
                    include=["documents", "metadatas", "distances"],
                )
            except Exception:
                return []

        chunks: List[Dict] = []
        if not results["ids"] or not results["ids"][0]:
            return chunks

        for i, doc_id in enumerate(results["ids"][0]):
            distance = (results.get("distances") or [[1.0]])[0][i]
            score    = max(0.0, 1.0 - distance)

            meta = dict(results["metadatas"][0][i])

            # Deserialise JSON-encoded list fields back to Python lists
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

    # ── Algorithm graph lookup ────────────────────────────────────────────────────

    def get_algorithm_graph(self, filename: str) -> Optional[dict]:
        """
        Retrieve the stored algorithm graph payload for a given filename.

        This uses a metadata-only lookup (no semantic search) to find the
        special "algorithm_graph" chunk written by processor._process_algorithm().

        Returns the deserialized graph dict, or None if not found.
        """
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

        # The first (and only) matching document contains the JSON payload
        doc = results["documents"][0] if results.get("documents") else ""
        if not doc:
            return None

        # Strip the sentinel prefix and parse JSON
        prefix = "ALGORITHM_GRAPH_DATA:"
        if doc.startswith(prefix):
            doc = doc[len(prefix):]
        try:
            return json.loads(doc)
        except Exception:
            return None

    # ── Admin ──────────────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Delete and recreate the collection (destructive)."""
        self._client.delete_collection("arup_knowledge")
        self._collection = self._client.get_or_create_collection(
            name="arup_knowledge",
            metadata={"hnsw:space": "cosine"},
        )