"""
asset_store.py  v1.0.0
────────────────────────────────────────────────────────────────────────────
Local PDF Asset Registry

Stores, indexes, and resolves uploaded PDF assets so the app can serve
algorithm PDFs from the same origin rather than relying on remote ARUP URLs.

Storage layout:
  data/assets/pdfs/{asset_id}.pdf   — binary PDF files
  data/assets/assets.db             — SQLite registry

Asset model:
  asset_id          str   — UUID
  filename          str   — stored filename (asset_id + .pdf)
  original_filename str   — name as uploaded
  title             str   — human title (from metadata or derived from filename)
  title_slug        str   — normalized slug for matching
  source_page_url   str   — ARUP page URL this PDF belongs to
  source_url        str   — alias for source_page_url
  asset_origin      str   — "uploaded" | "cached_remote"
  local_path        str   — absolute path on disk
  created_at        str   — ISO timestamp

Matching strategies (in order):
  1. Exact source_url / source_page_url match
  2. Filename stem match (normalized)
  3. Title slug match (normalized)
  4. Token overlap heuristic on normalized slug

Design: deterministic, no external deps beyond stdlib + sqlite3.
"""

import json
import logging
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# ── Storage paths ─────────────────────────────────────────────────────────────
# asset_store.py lives at backend/agents/asset_store.py
# __file__.parent       = backend/agents/
# __file__.parent.parent = backend/
# ../../data/assets      = root/data/assets  (matches ChromaDB: ../../data/chroma)
# asset_store.py lives at backend/agents/asset_store.py
# .parent            → backend/agents/
# .parent.parent     → backend/
# "../../data/assets" → ROOT/data/assets   (same convention as store.py's chroma path)
_BASE_DIR   = Path(__file__).parent.parent.parent / "data" / "assets"
_PDF_DIR    = _BASE_DIR / "pdfs"
_DB_PATH    = _BASE_DIR / "assets.db"

# ── Schema ────────────────────────────────────────────────────────────────────
_DDL = """
CREATE TABLE IF NOT EXISTS assets (
    asset_id          TEXT PRIMARY KEY,
    filename          TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    title             TEXT NOT NULL DEFAULT '',
    title_slug        TEXT NOT NULL DEFAULT '',
    source_page_url   TEXT NOT NULL DEFAULT '',
    source_url        TEXT NOT NULL DEFAULT '',
    asset_origin      TEXT NOT NULL DEFAULT 'uploaded',
    local_path        TEXT NOT NULL,
    mime_type         TEXT NOT NULL DEFAULT 'application/pdf',
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_source_url    ON assets(source_url);
CREATE INDEX IF NOT EXISTS idx_title_slug    ON assets(title_slug);
CREATE INDEX IF NOT EXISTS idx_original_name ON assets(original_filename);
"""


# ── Slug normalisation ─────────────────────────────────────────────────────────

def _normalize_slug(text: str) -> str:
    """
    Produce a stable normalized slug for fuzzy matching.
    lowercase → strip punctuation → collapse whitespace → replace with hyphens.

    Examples:
      "Thyroid Cancer Testing Algorithm" → "thyroid-cancer-testing-algorithm"
      "thyroid-cancer-testing-algorithm.pdf" → "thyroid-cancer-testing-algorithm"
    """
    text = text.lower()
    # Remove file extension
    text = re.sub(r'\.[a-z]{2,5}$', '', text)
    # Replace separators with space
    text = re.sub(r'[\s\-_/\\]+', ' ', text)
    # Strip non-alphanumeric (keep spaces)
    text = re.sub(r'[^a-z0-9 ]', '', text)
    # Collapse and trim
    text = re.sub(r' +', '-', text.strip())
    return text


def _slug_from_url(url: str) -> str:
    """Extract the last path segment from a URL and normalize it."""
    url = (url or "").rstrip("/")
    segment = url.split("/")[-1] if "/" in url else url
    return _normalize_slug(segment)


def _token_overlap(slug_a: str, slug_b: str) -> float:
    """Simple token-overlap score between two slugs (0.0 – 1.0)."""
    if not slug_a or not slug_b:
        return 0.0
    ta = set(slug_a.split("-"))
    tb = set(slug_b.split("-"))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


# ── AssetStore ────────────────────────────────────────────────────────────────

class AssetStore:
    """
    Lightweight SQLite-backed store for local PDF assets.

    Usage:
        store = AssetStore()
        asset = store.save_asset(pdf_bytes, "thyroid-algorithm.pdf",
                                 {"title": "Thyroid Cancer Testing Algorithm",
                                  "source_url": "https://arupconsult.com/algorithm/..."})
        asset_id = asset["asset_id"]

        # Later: resolve from algorithm metadata
        match = store.find_matching_pdf(
            source_url="https://arupconsult.com/algorithm/thyroid-cancer-testing-algorithm",
            title="Thyroid Cancer Testing Algorithm",
        )
    """

    def __init__(self, base_dir: Optional[Path] = None):
        self._base  = Path(base_dir) if base_dir else _BASE_DIR
        self._pdfs  = self._base / "pdfs"
        self._db    = self._base / "assets.db"
        self._pdfs.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Init ──────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(_DDL)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db))
        conn.row_factory = sqlite3.Row
        return conn

    # ── Write ─────────────────────────────────────────────────────────────────

    def save_asset(
        self,
        file_bytes: bytes,
        original_filename: str,
        metadata: Optional[dict] = None,
        origin: str = "uploaded",
    ) -> dict:
        """
        Persist a PDF binary and register it in the registry.

        Parameters
        ----------
        file_bytes        : raw PDF bytes
        original_filename : original upload filename
        metadata          : optional dict with keys:
                              title, source_url, source_page_url
        origin            : "uploaded" | "cached_remote"

        Returns
        -------
        asset record dict
        """
        meta = metadata or {}
        asset_id       = str(uuid.uuid4())
        filename       = f"{asset_id}.pdf"
        local_path     = str(self._pdfs / filename)
        title          = str(meta.get("title", "") or "").strip()
        source_url     = str(meta.get("source_url", "") or "").strip()
        source_page_url = str(meta.get("source_page_url", "") or source_url).strip()

        # Derive title from filename if not provided
        if not title:
            title = re.sub(r'[-_]+', ' ', Path(original_filename).stem).title()

        title_slug = _normalize_slug(title or original_filename)

        # Write binary
        with open(local_path, "wb") as f:
            f.write(file_bytes)

        created_at = datetime.now(timezone.utc).isoformat()

        row = {
            "asset_id":          asset_id,
            "filename":          filename,
            "original_filename": original_filename,
            "title":             title,
            "title_slug":        title_slug,
            "source_page_url":   source_page_url,
            "source_url":        source_url,
            "asset_origin":      origin,
            "local_path":        local_path,
            "mime_type":         "application/pdf",
            "created_at":        created_at,
        }

        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO assets
                  (asset_id, filename, original_filename, title, title_slug,
                   source_page_url, source_url, asset_origin, local_path,
                   mime_type, created_at)
                VALUES
                  (:asset_id, :filename, :original_filename, :title, :title_slug,
                   :source_page_url, :source_url, :asset_origin, :local_path,
                   :mime_type, :created_at)
            """, row)

        logger.info("AssetStore: saved PDF asset %s ← %s", asset_id, original_filename)
        return row

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_asset(self, asset_id: str) -> Optional[dict]:
        """Return asset record by ID, or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM assets WHERE asset_id = ?", (asset_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_asset_path(self, asset_id: str) -> Optional[Path]:
        """Return local Path for an asset if it exists on disk, else None."""
        asset = self.get_asset(asset_id)
        if not asset:
            return None
        p = Path(asset["local_path"])
        return p if p.exists() else None

    def list_assets(
        self,
        origin: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        """List registered assets, optionally filtered by origin."""
        sql = "SELECT * FROM assets"
        params: list = []
        if origin:
            sql += " WHERE asset_origin = ?"
            params.append(origin)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── Matching ──────────────────────────────────────────────────────────────

    def find_matching_pdf(
        self,
        source_url:      Optional[str] = None,
        source_page_url: Optional[str] = None,
        title:           Optional[str] = None,
        filename:        Optional[str] = None,
        threshold:       float = 0.5,
    ) -> Optional[dict]:
        """
        Find the best-matching registered PDF asset.

        Matching strategies (in priority order):
          1. Exact source_url match
          2. Exact source_page_url match
          3. Filename stem slug match
          4. Title slug match (exact)
          5. Token-overlap heuristic (>= threshold)

        Returns the best matching asset record, or None.
        """
        with self._conn() as conn:
            all_rows = [dict(r) for r in conn.execute("SELECT * FROM assets").fetchall()]

        if not all_rows:
            return None

        # ── Priority 1: exact source_url match ──────────────────────────────
        if source_url:
            for row in all_rows:
                if row["source_url"] and row["source_url"] == source_url:
                    return row

        # ── Priority 2: exact source_page_url match ──────────────────────────
        if source_page_url:
            for row in all_rows:
                if row["source_page_url"] and row["source_page_url"] == source_page_url:
                    return row

        # ── Priority 3: filename stem slug match ─────────────────────────────
        if filename:
            fname_slug = _normalize_slug(Path(filename).stem)
            if fname_slug:
                for row in all_rows:
                    orig_slug = _normalize_slug(Path(row["original_filename"]).stem)
                    if orig_slug and orig_slug == fname_slug:
                        return row

        # ── Priority 4 & 5: title slug ───────────────────────────────────────
        if title:
            query_slug = _normalize_slug(title)
            if query_slug:
                # Exact slug match
                for row in all_rows:
                    if row["title_slug"] == query_slug:
                        return row
                # URL-last-segment slug match (e.g. from source_url path)
                url_slug = _slug_from_url(source_url or source_page_url or "")
                if url_slug:
                    for row in all_rows:
                        if row["title_slug"] == url_slug:
                            return row
                # Token overlap fallback
                best_score = 0.0
                best_row   = None
                for row in all_rows:
                    score = _token_overlap(query_slug, row["title_slug"])
                    if score > best_score:
                        best_score = score
                        best_row   = row
                if best_score >= threshold and best_row:
                    return best_row

        return None

    def find_matching_pdf_with_debug(
        self,
        source_url:      Optional[str] = None,
        source_page_url: Optional[str] = None,
        title:           Optional[str] = None,
        filename:        Optional[str] = None,
        threshold:       float = 0.5,
    ) -> dict:
        """
        Like find_matching_pdf but returns full match diagnostics.

        Return shape::

            {
                "match":               dict | None,
                "strategy":            str,   # which strategy fired, or failure reason
                "score":               float, # 1.0 exact, <1.0 token-overlap
                "candidates_tried":    int,
                "source_url_used":     str,
                "source_page_url_used": str,
                "title_used":          str,
                "asset_id":            str | None,
                "matched_filename":    str | None,
            }
        """
        base: dict = {
            "match": None, "strategy": "no_match", "score": 0.0,
            "candidates_tried": 0,
            "source_url_used":      source_url      or "",
            "source_page_url_used": source_page_url or "",
            "title_used":           title           or "",
            "asset_id": None, "matched_filename": None,
        }

        with self._conn() as conn:
            all_rows = [dict(r) for r in conn.execute("SELECT * FROM assets").fetchall()]

        base["candidates_tried"] = len(all_rows)
        if not all_rows:
            base["strategy"] = "no_assets"
            return base

        def _hit(row: dict, strategy: str, score: float = 1.0) -> dict:
            base.update({
                "match":            row,
                "strategy":         strategy,
                "score":            score,
                "asset_id":         row["asset_id"],
                "matched_filename": row.get("original_filename", ""),
            })
            return base

        # Priority 1 — exact source_url
        if source_url:
            for row in all_rows:
                if row["source_url"] and row["source_url"] == source_url:
                    return _hit(row, "exact_source_url")

        # Priority 2 — exact source_page_url
        if source_page_url:
            for row in all_rows:
                if row["source_page_url"] and row["source_page_url"] == source_page_url:
                    return _hit(row, "exact_source_page_url")

        # Priority 3 — filename stem slug
        if filename:
            fname_slug = _normalize_slug(Path(filename).stem)
            if fname_slug:
                for row in all_rows:
                    orig_slug = _normalize_slug(Path(row["original_filename"]).stem)
                    if orig_slug and orig_slug == fname_slug:
                        return _hit(row, "filename_stem_slug")

        # Priorities 4 & 5 — title slug / token-overlap
        if title:
            query_slug = _normalize_slug(title)
            if query_slug:
                # 4a — exact title_slug match
                for row in all_rows:
                    if row["title_slug"] == query_slug:
                        return _hit(row, "title_slug_exact")
                # 4b — URL last-segment slug
                url_slug = _slug_from_url(source_url or source_page_url or "")
                if url_slug:
                    for row in all_rows:
                        if row["title_slug"] == url_slug:
                            return _hit(row, "url_segment_slug")
                # 5 — token-overlap fallback
                best_score, best_row = 0.0, None
                for row in all_rows:
                    s = _token_overlap(query_slug, row["title_slug"])
                    if s > best_score:
                        best_score, best_row = s, row
                if best_score >= threshold and best_row:
                    return _hit(best_row, "token_overlap", best_score)
                base["strategy"] = (
                    f"no_match_best_score_{best_score:.2f}"
                    if best_score > 0 else "no_match_zero_overlap"
                )

        return base

    # ── Local URL builder ────────────────────────────────────────────────────

    @staticmethod
    def local_url(asset_id: str) -> str:
        """Return the same-origin serving URL for an asset."""
        return f"/api/assets/pdf/{asset_id}"

    # ── Delete ────────────────────────────────────────────────────────────────

    def delete_asset(self, asset_id: str) -> bool:
        """Remove asset from registry and disk. Returns True if found."""
        asset = self.get_asset(asset_id)
        if not asset:
            return False
        p = Path(asset["local_path"])
        if p.exists():
            p.unlink()
        with self._conn() as conn:
            conn.execute("DELETE FROM assets WHERE asset_id = ?", (asset_id,))
        logger.info("AssetStore: deleted asset %s", asset_id)
        return True


# ── Module-level singleton (shared across all imports) ────────────────────────

_store: Optional[AssetStore] = None


def get_asset_store() -> AssetStore:
    """Return the module-level AssetStore singleton, creating it if needed."""
    global _store
    if _store is None:
        _store = AssetStore()
    return _store