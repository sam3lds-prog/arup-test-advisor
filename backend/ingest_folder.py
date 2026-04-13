#!/usr/bin/env python3
"""
ARUP AI Test Advisor — Folder Ingest CLI
=========================================
Usage:
    python ingest_folder.py <folder_path> [--dry-run] [--no-recursive]

Examples:
    # Preview what will be indexed (no changes made)
    python ingest_folder.py ~/Documents/arup_knowledge --dry-run

    # Index all supported files in a folder (recursive)
    python ingest_folder.py ~/Documents/arup_knowledge

    # Index only top-level files (no subdirectories)
    python ingest_folder.py ~/Documents/arup_knowledge --no-recursive

Recommended folder structure for best classification accuracy:
    arup_knowledge/
        algorithms/           <- algorithm JSON / PDF files
        fact_sheets/          <- fact sheet JSON / PDF files
        consult_topics/       <- consult topic JSON files
        test_directory/       <- test directory CSV / JSON files
"""

import sys
import os
import json
import argparse
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / ".env")

from knowledge.processor import DocumentProcessor, ALLOWED_EXTENSIONS, classify_document
from knowledge.store import VectorStore


def _colour(text, code):
    if sys.platform == "win32":
        return text
    return f"\033[{code}m{text}\033[0m"

GREEN = "32"; YELLOW = "33"; RED = "31"; CYAN = "36"; BOLD = "1"


def run_ingest(folder_path, recursive=True, dry_run=False):
    folder = Path(folder_path).expanduser().resolve()
    if not folder.exists():
        print(_colour(f"Folder not found: {folder}", RED)); sys.exit(1)
    if not folder.is_dir():
        print(_colour(f"Not a directory: {folder}", RED)); sys.exit(1)

    print(_colour(f"\n{'DRY RUN - ' if dry_run else ''}Ingesting: {folder}\n", BOLD))

    pattern = "**/*" if recursive else "*"
    files   = sorted(p for p in folder.glob(pattern)
                     if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS)

    if not files:
        print(_colour("No supported files found.", YELLOW)); return

    processor = None if dry_run else DocumentProcessor()
    store     = None if dry_run else VectorStore()
    total_chunks = ok_count = error_count = 0
    w = min(60, max(len(str(p.relative_to(folder))) for p in files) + 2)

    header = f"{'File':<{w}}  {'Predicted Type':<18}  {'Chunks':>6}  Status"
    print(header)
    print("-" * len(header))

    for path in files:
        rel  = str(path.relative_to(folder))
        ext  = path.suffix.lower()
        hint = str(path.parent.name)
        predicted = "Unknown"
        try:
            if ext == ".json":
                data = json.loads(path.read_bytes().decode("utf-8", errors="replace"))
                preview = " ".join(str(k) for k in data.keys()) if isinstance(data, dict) else ""
                predicted = classify_document(
                    filename=path.name, content_preview=preview,
                    json_data=data if isinstance(data, dict) else None,
                    folder_hint=hint)
            else:
                predicted = classify_document(filename=path.name, folder_hint=hint)
        except Exception as e:
            predicted = f"Error: {e}"

        if dry_run:
            print(f"{rel:<{w}}  {predicted:<18}  {'--':>6}  " + _colour("(dry run)", CYAN))
            continue

        try:
            chunks = processor.process(path.read_bytes(), path.name, folder_hint=hint)
            store.add_documents(chunks)
            n = len(chunks); total_chunks += n; ok_count += 1
            print(f"{rel:<{w}}  {predicted:<18}  {n:>6}  " + _colour("OK", GREEN))
        except Exception as e:
            error_count += 1
            print(f"{rel:<{w}}  {predicted:<18}  {'--':>6}  " + _colour(f"ERROR: {e}", RED))

    print("-" * len(header))
    if dry_run:
        print(_colour(f"\nDry run complete - {len(files)} files would be ingested.", CYAN))
    else:
        msg = (f"\nDone - {ok_count} files indexed, {total_chunks} chunks added"
               + (f", {error_count} errors" if error_count else ""))
        print(_colour(msg, GREEN if not error_count else YELLOW))
        if store:
            print(f"Total documents in knowledge base: {store.count()}")


def main():
    p = argparse.ArgumentParser(description="ARUP AI Test Advisor - folder ingest")
    p.add_argument("folder_path")
    p.add_argument("--dry-run",      action="store_true")
    p.add_argument("--no-recursive", action="store_true")
    args = p.parse_args()
    run_ingest(args.folder_path, recursive=not args.no_recursive, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
