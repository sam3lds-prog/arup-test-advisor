"""
reindex_knowledge.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
Hard-cutover reindex from the legacy MiniLM collection to a new BGE
collection.

What this script does
─────────────────────
1. Opens the LEGACY ChromaDB collection (default name: "arup_knowledge").
2. Exports every chunk's id, document text, and metadata. Embeddings
   are NOT exported — they will be recomputed with the new provider.
3. Loads the configured Hugging Face embedding provider (default
   BAAI/bge-base-en-v1.5).
4. Computes new embeddings in batches.
5. Writes them to the NEW collection
   (default: "arup_knowledge__bge_base_en_v1_5").
6. With --drop-legacy, removes the old collection so future writes
   can't accidentally land there.
7. Writes a manifest JSON at backend/data/.reindex_manifest.json with
   timestamp, model, dimension, chunk count, and per-source-type counts.

Why this approach
─────────────────
The legacy ChromaDB collection IS the source of truth for indexed text —
the original uploaded files are not all kept on disk in a known folder.
By migrating chunk-by-chunk we preserve the chunker's exact output and
all rich metadata (test_id, test_name, source_url, page_type, etc.)
without re-running the processor.

This is faster, safer, and idempotent — no source-file dependency.

Usage
─────
    cd backend
    source .venv/bin/activate
    python -m scripts.reindex_knowledge --force
    python -m scripts.reindex_knowledge --force --drop-legacy
    python -m scripts.reindex_knowledge --dry-run

Run from the BACKEND directory so the relative ../../data/chroma path
in store.py resolves to the same location as uvicorn at runtime.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List

# Ensure imports work whether run as `python -m scripts.reindex_knowledge`
# or `python scripts/reindex_knowledge.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledge.store import VectorStore, _BASE_COLLECTION  # noqa: E402

logger = logging.getLogger("reindex")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_provider():
    from embeddings.hf_embedding_provider import HfEmbeddingProvider
    return HfEmbeddingProvider.from_env()


def _read_legacy_chunks(legacy_collection_name: str) -> List[Dict]:
    legacy = VectorStore()
    legacy._collection_name = legacy_collection_name
    legacy._collection_obj = None
    chunks = legacy.export_all_chunks()
    logger.info("Exported %d chunks from legacy collection %r",
                len(chunks), legacy_collection_name)
    return chunks


def _write_new_collection(
    chunks: List[Dict],
    provider,
    new_collection_name: str,
    drop_existing: bool,
    batch_size: int,
) -> Dict:
    new_store = VectorStore()
    new_store._collection_name = new_collection_name
    new_store._collection_obj = None
    new_store._provider = provider
    new_store._dimension_verified = True

    if drop_existing:
        try:
            new_store._client.delete_collection(new_collection_name)
            logger.info("Dropped existing collection %r", new_collection_name)
        except Exception as exc:
            logger.debug("Collection %r did not exist: %s", new_collection_name, exc)

    _ = new_store._collection  # force creation

    by_type: Counter = Counter()
    total = len(chunks)
    if total == 0:
        return {"total": 0, "by_source_type": {}, "elapsed_s": 0}

    t_start = time.time()
    embedded = 0
    for batch_start in range(0, total, batch_size):
        batch = chunks[batch_start:batch_start + batch_size]
        texts = [c["document"] or "" for c in batch]
        ids = [c["id"] for c in batch]
        metas = [c["metadata"] or {} for c in batch]

        for m in metas:
            by_type[m.get("source_type", "Unknown")] += 1

        try:
            embeddings = provider.encode_documents(texts)
        except Exception as exc:
            logger.exception("Encoding batch %d-%d failed (%s); skipping",
                             batch_start, batch_start + len(batch), exc)
            continue

        try:
            new_store.add_precomputed(
                ids=ids,
                documents=texts,
                metadatas=metas,
                embeddings=embeddings,
            )
        except Exception as exc:
            logger.exception("Insert batch %d-%d failed (%s)",
                             batch_start, batch_start + len(batch), exc)
            continue

        embedded += len(batch)
        if embedded % (batch_size * 5) == 0 or embedded == total:
            elapsed = time.time() - t_start
            rate = embedded / elapsed if elapsed > 0 else 0
            logger.info(
                "  -> %d/%d chunks embedded  (%.1f chunks/sec)",
                embedded, total, rate,
            )

    elapsed = time.time() - t_start
    return {
        "total": embedded,
        "by_source_type": dict(by_type),
        "elapsed_s": round(elapsed, 2),
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Hard-cutover reindex from MiniLM to a Hugging Face embedding model.",
    )
    p.add_argument("--legacy-collection", default=_BASE_COLLECTION,
                   help=f"Source collection (default: {_BASE_COLLECTION})")
    p.add_argument("--force", action="store_true",
                   help="Drop the destination BGE collection if it exists.")
    p.add_argument("--drop-legacy", action="store_true",
                   help="Delete the legacy collection AFTER successful migration. "
                        "Default keeps it for rollback.")
    p.add_argument("--batch-size", type=int,
                   default=int(os.getenv("EMBEDDING_BATCH_SIZE", "32")))
    p.add_argument("--dry-run", action="store_true",
                   help="Print legacy chunk counts and exit; no writes.")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    _setup_logging(args.verbose)

    logger.info("Loading embedding provider...")
    try:
        provider = _load_provider()
    except Exception:
        logger.exception("Failed to load embedding provider — aborting")
        return 2
    logger.info("Provider: %s  dim=%d  device=%s",
                provider.name, provider.dimension, provider.device)

    target_collection = f"{_BASE_COLLECTION}__{provider.collection_suffix}"
    logger.info("Migration: %r  ->  %r", args.legacy_collection, target_collection)

    if args.legacy_collection == target_collection:
        logger.error(
            "Source and target collections are identical (%r). "
            "Refusing to overwrite.",
            target_collection,
        )
        return 2

    chunks = _read_legacy_chunks(args.legacy_collection)
    if not chunks:
        logger.warning("Legacy collection is empty — nothing to migrate.")
        if not args.dry_run:
            new_store = VectorStore()
            new_store._collection_name = target_collection
            new_store._collection_obj = None
            new_store._provider = provider
            new_store._dimension_verified = True
            _ = new_store._collection
            logger.info("Created empty target collection %r", target_collection)
        return 0

    by_type_pre = Counter(c["metadata"].get("source_type", "Unknown") for c in chunks)
    logger.info("Source-type breakdown (legacy):")
    for k, v in sorted(by_type_pre.items()):
        logger.info("  %-20s %d", k, v)

    if args.dry_run:
        logger.info("Dry run — exiting without writes.")
        return 0

    summary = _write_new_collection(
        chunks=chunks,
        provider=provider,
        new_collection_name=target_collection,
        drop_existing=args.force,
        batch_size=args.batch_size,
    )

    manifest = {
        "ts":               int(time.time()),
        "ts_iso":           time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "embedding_model":  provider.name,
        "embedding_dim":    provider.dimension,
        "embedding_device": provider.device,
        "legacy_collection":  args.legacy_collection,
        "target_collection":  target_collection,
        "total_chunks":     summary["total"],
        "by_source_type":   summary["by_source_type"],
        "elapsed_s":        summary["elapsed_s"],
        "dropped_legacy":   args.drop_legacy,
    }
    manifest_path = Path(__file__).resolve().parent.parent.parent / "data" / ".reindex_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print(json.dumps(manifest, indent=2))
    logger.info("Manifest written to %s", manifest_path)
    logger.info(
        "Reindex complete — %d chunks in %.1fs at %.1f chunks/sec",
        summary["total"], summary["elapsed_s"],
        summary["total"] / summary["elapsed_s"] if summary["elapsed_s"] > 0 else 0,
    )

    if args.drop_legacy:
        try:
            from chromadb import PersistentClient
            from chromadb.config import Settings
            db_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "../../data/chroma",
            )
            client = PersistentClient(path=db_path, settings=Settings(anonymized_telemetry=False))
            client.delete_collection(args.legacy_collection)
            logger.info("Dropped legacy collection %r", args.legacy_collection)
        except Exception:
            logger.exception("Could not drop legacy collection (non-fatal)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
