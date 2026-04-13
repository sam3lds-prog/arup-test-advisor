/**
 * UploadPanel.jsx
 * ────────────────────────────────────────────────────────────────────────────
 * ARUP AI Test Advisor — Document Upload Panel
 *
 * Features:
 *  • Document-type dropdown (Algorithm / Fact Sheet / Consult Topic /
 *    Test Directory / Auto-detect)
 *  • Multi-file picker — handles up to 3 500 files
 *  • Chunked queue: files are sent in batches of BATCH_SIZE (50) so the
 *    browser and server are never overwhelmed
 *  • Live progress bar + per-batch status feed
 *  • Error / skip / success counts
 *  • Cancel mid-upload
 *  • Indexed documents list (refreshes after each batch)
 *
 * Props:
 *  onUploadComplete  function()  — called when the whole queue finishes
 *  apiBase           string      — base URL for the API (default "/api")
 */

import { useState, useRef, useCallback, useEffect } from "react";

// ── Constants ─────────────────────────────────────────────────────────────────

const BATCH_SIZE   = 50;   // files per HTTP request
const API_BASE     = "/api";
const ACCEPT_EXTS  = ".pdf,.json,.csv,.tsv,.txt";

const DOC_TYPES = [
  { value: "Auto-detect",    label: "Auto-detect",              icon: "🔍" },
  { value: "Algorithm",      label: "Algorithm",                icon: "🔀" },
  { value: "Fact Sheet",     label: "Fact Sheet",               icon: "📋" },
  { value: "Consult Topic",  label: "Consult Topic / Disease",  icon: "🩺" },
  { value: "Test Directory", label: "Test Directory",           icon: "🧪" },
];

const TYPE_COLOURS = {
  "Algorithm":      { bg: "#EEF2FF", border: "#6366F1", text: "#4338CA" },
  "Fact Sheet":     { bg: "#F0FDF4", border: "#22C55E", text: "#15803D" },
  "Consult Topic":  { bg: "#FFF7ED", border: "#F97316", text: "#C2410C" },
  "Test Directory": { bg: "#F0F9FF", border: "#0EA5E9", text: "#0369A1" },
  "General":        { bg: "#F9FAFB", border: "#9CA3AF", text: "#374151" },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatBytes(bytes) {
  if (bytes < 1024)        return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function TypeBadge({ type }) {
  const c = TYPE_COLOURS[type] || TYPE_COLOURS["General"];
  return (
    <span style={{
      background: c.bg, border: `1px solid ${c.border}`, color: c.text,
      fontSize: 11, fontWeight: 600, padding: "1px 7px", borderRadius: 10,
      whiteSpace: "nowrap",
    }}>
      {type}
    </span>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function UploadPanel({ onUploadComplete, apiBase = API_BASE }) {

  // ── State ─────────────────────────────────────────────────────────────────
  const [docType,      setDocType]      = useState("Auto-detect");
  const [selectedFiles, setSelectedFiles] = useState([]);   // File[]
  const [uploading,    setUploading]    = useState(false);
  const [cancelled,    setCancelled]    = useState(false);
  const [autoReplace,  setAutoReplace]  = useState(true);  // smart dedup: replace existing by default

  // Progress
  const [progress, setProgress] = useState({
    total: 0, done: 0, ok: 0, replaced: 0, skipped: 0, errors: 0, chunks: 0,
  });

  // Log feed
  const [log, setLog] = useState([]);   // { msg, level }[]

  // Indexed documents list
  const [docs,        setDocs]        = useState([]);
  const [loadingDocs, setLoadingDocs] = useState(false);

  const cancelRef  = useRef(false);
  const fileInputRef = useRef(null);

  // ── Load indexed docs ──────────────────────────────────────────────────────
  const refreshDocs = useCallback(async () => {
    setLoadingDocs(true);
    try {
      const r = await fetch(`${apiBase}/documents`);
      const d = await r.json();
      setDocs(d.documents || []);
    } catch {
      // ignore
    } finally {
      setLoadingDocs(false);
    }
  }, [apiBase]);

  useEffect(() => { refreshDocs(); }, [refreshDocs]);

  // ── File picker handler ────────────────────────────────────────────────────
  const handleFilePick = (e) => {
    const files = Array.from(e.target.files || []);
    setSelectedFiles(files);
    setLog([]);
    setProgress({ total: 0, done: 0, ok: 0, replaced: 0, skipped: 0, errors: 0, chunks: 0 });
  };

  // ── Core upload queue ──────────────────────────────────────────────────────
  const startUpload = useCallback(async () => {
    if (!selectedFiles.length) return;

    setUploading(true);
    setCancelled(false);
    cancelRef.current = false;

    const total = selectedFiles.length;
    setProgress({ total, done: 0, ok: 0, replaced: 0, skipped: 0, errors: 0, chunks: 0 });

    const modeLabel = autoReplace ? "smart replace (auto-dedup)" : "skip duplicates";
    setLog([{
      msg: `Starting upload of ${total} file${total > 1 ? "s" : ""} as "${docType}" — ${modeLabel}`,
      level: "info",
    }]);

    let cumOk = 0, cumReplaced = 0, cumSkipped = 0, cumErrors = 0, cumChunks = 0;

    // Slice files into batches of BATCH_SIZE
    for (let batchStart = 0; batchStart < total; batchStart += BATCH_SIZE) {
      if (cancelRef.current) {
        setLog(prev => [...prev, { msg: "Upload cancelled by user.", level: "warn" }]);
        break;
      }

      const batch        = selectedFiles.slice(batchStart, batchStart + BATCH_SIZE);
      const batchNum     = Math.floor(batchStart / BATCH_SIZE) + 1;
      const totalBatches = Math.ceil(total / BATCH_SIZE);

      // Build FormData — pass replace flag from autoReplace toggle
      const form = new FormData();
      batch.forEach(f => form.append("files", f));
      if (docType !== "Auto-detect") form.append("document_type", docType);
      form.append("replace", autoReplace ? "true" : "false");

      try {
        const resp = await fetch(`${apiBase}/upload/batch`, {
          method: "POST",
          body: form,
        });

        if (!resp.ok) {
          const err = await resp.text();
          setLog(prev => [...prev, {
            msg: `Batch ${batchNum}/${totalBatches} — server error: ${err}`,
            level: "error",
          }]);
          cumErrors += batch.length;
        } else {
          const data    = await resp.json();
          const results = data.results || [];

          // Accumulate summary counters
          const batchNew      = results.filter(r => r.status === "ok").length;
          const batchReplaced = results.filter(r => r.status === "replaced").length;
          const batchSkipped  = results.filter(r => r.status === "duplicate").length;
          const batchErrors   = results.filter(r => r.status === "error").length;
          const batchChunks   = data.total_chunks_indexed || 0;

          cumOk      += batchNew;
          cumReplaced += batchReplaced;
          cumSkipped  += batchSkipped;
          cumErrors   += batchErrors;
          cumChunks   += batchChunks;

          // Per-file log entries — only log replaced + errors individually to avoid log spam
          results.forEach(r => {
            if (r.status === "replaced") {
              setLog(prev => [...prev, {
                msg: `↺ ${r.filename} — replaced (${r.replaced_chunks || 0} old chunks removed, ${r.chunks_indexed} new)`,
                level: "info",
              }]);
            } else if (r.status === "error") {
              setLog(prev => [...prev, {
                msg: `✗ ${r.filename}: ${r.detail}`,
                level: "error",
              }]);
            } else if (r.status === "duplicate" && !autoReplace) {
              setLog(prev => [...prev, {
                msg: `⚠ ${r.filename} — already indexed, skipped (enable auto-replace to update)`,
                level: "warn",
              }]);
            }
            // Asset registration feedback for algorithm PDFs
            if (r.asset_registered) {
              setLog(prev => [...prev, {
                msg: `📎 PDF asset registered: "${r.filename}" — will appear as embedded split-view PDF when its title matches a returned algorithm.`,
                level: "ok",
              }]);
            }
          });

          // Batch summary line
          const parts = [];
          if (batchNew      > 0) parts.push(`${batchNew} new`);
          if (batchReplaced > 0) parts.push(`${batchReplaced} replaced`);
          if (batchSkipped  > 0) parts.push(`${batchSkipped} skipped`);
          if (batchErrors   > 0) parts.push(`${batchErrors} errors`);
          parts.push(`${batchChunks} chunks`);

          setLog(prev => [...prev, {
            msg: `Batch ${batchNum}/${totalBatches} — ${parts.join(", ")}`,
            level: batchErrors > 0 ? "warn" : "ok",
          }]);
        }
      } catch (err) {
        setLog(prev => [...prev, {
          msg: `Batch ${batchNum}/${totalBatches} — network error: ${err.message}`,
          level: "error",
        }]);
        cumErrors += batch.length;
      }

      const done = Math.min(batchStart + batch.length, total);
      setProgress({
        total, done,
        ok:       cumOk,
        replaced: cumReplaced,
        skipped:  cumSkipped,
        errors:   cumErrors,
        chunks:   cumChunks,
      });
    }

    // Final summary
    if (!cancelRef.current) {
      const summaryParts = [];
      if (cumOk       > 0) summaryParts.push(`${cumOk} new`);
      if (cumReplaced > 0) summaryParts.push(`${cumReplaced} replaced`);
      if (cumChunks   > 0) summaryParts.push(`${cumChunks} chunks`);
      if (cumErrors   > 0) summaryParts.push(`${cumErrors} errors`);
      if (cumSkipped  > 0) summaryParts.push(`${cumSkipped} skipped`);
      setLog(prev => [...prev, {
        msg: `Done — ${summaryParts.join(", ")}`,
        level: cumErrors > 0 ? "warn" : "ok",
      }]);
    }

    setUploading(false);
    await refreshDocs();
    if (onUploadComplete) onUploadComplete();
  }, [selectedFiles, docType, autoReplace, apiBase, refreshDocs, onUploadComplete]);

  const cancelUpload = () => {
    cancelRef.current = true;
    setCancelled(true);
  };

  const clearSelection = () => {
    setSelectedFiles([]);
    setLog([]);
    setProgress({ total: 0, done: 0, ok: 0, replaced: 0, skipped: 0, errors: 0, chunks: 0 });
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  // ── Computed ───────────────────────────────────────────────────────────────
  const progressPct  = progress.total > 0
    ? Math.round((progress.done / progress.total) * 100) : 0;
  const totalSize    = selectedFiles.reduce((s, f) => s + f.size, 0);
  const totalBatches = Math.ceil(selectedFiles.length / BATCH_SIZE);
  const selectedType = DOC_TYPES.find(d => d.value === docType) || DOC_TYPES[0];

  // ── Styles (ARUP design tokens) ────────────────────────────────────────────
  const S = {
    panel: {
      background: "#fff",
      border: "1px solid #77787B",
      borderRadius: 8,
      padding: 20,
      fontFamily: "Roboto, sans-serif",
      color: "#171717",
    },
    label: {
      fontSize: 12,
      fontWeight: 600,
      color: "#77787B",
      textTransform: "uppercase",
      letterSpacing: "0.05em",
      marginBottom: 6,
      display: "block",
    },
    select: {
      width: "100%",
      padding: "9px 12px",
      border: "1px solid #77787B",
      borderRadius: 6,
      fontSize: 14,
      color: "#171717",
      background: "#FAFAFA",
      cursor: "pointer",
      outline: "none",
      marginBottom: 14,
    },
    dropZone: (active) => ({
      border: `2px dashed ${active ? "#AE132A" : "#77787B"}`,
      borderRadius: 8,
      padding: "28px 16px",
      textAlign: "center",
      background: active ? "#FFF5F5" : "#FAFAFA",
      cursor: "pointer",
      transition: "all 0.15s",
      marginBottom: 14,
    }),
    btnPrimary: (disabled) => ({
      background: disabled ? "#D1D5DB" : "#AE132A",
      color: "#fff",
      border: "none",
      borderRadius: "50vh",
      padding: "7px 22px",
      fontSize: 14,
      fontWeight: 600,
      cursor: disabled ? "not-allowed" : "pointer",
      transition: "background 0.15s",
    }),
    btnSecondary: {
      background: "transparent",
      color: "#AE132A",
      border: "1px solid #AE132A",
      borderRadius: "50vh",
      padding: "6px 18px",
      fontSize: 13,
      fontWeight: 600,
      cursor: "pointer",
      marginLeft: 8,
    },
    btnCancel: {
      background: "transparent",
      color: "#6B7280",
      border: "1px solid #9CA3AF",
      borderRadius: "50vh",
      padding: "6px 18px",
      fontSize: 13,
      fontWeight: 600,
      cursor: "pointer",
      marginLeft: 8,
    },
    progressBar: {
      height: 8,
      background: "#E5E7EB",
      borderRadius: 4,
      overflow: "hidden",
      marginBottom: 6,
    },
    progressFill: (pct, hasErrors) => ({
      height: "100%",
      width: `${pct}%`,
      background: hasErrors ? "#F59E0B" : "#AE132A",
      borderRadius: 4,
      transition: "width 0.3s ease",
    }),
    logBox: {
      background: "#F9FAFB",
      border: "1px solid #E5E7EB",
      borderRadius: 6,
      padding: "8px 10px",
      maxHeight: 160,
      overflowY: "auto",
      fontSize: 12,
      lineHeight: 1.6,
      marginTop: 10,
      fontFamily: "monospace",
    },
    logLine: (level) => ({
      color: level === "error" ? "#DC2626"
           : level === "warn"  ? "#D97706"
           : level === "ok"    ? "#16A34A"
           : "#6B7280",
    }),
    docsTable: {
      width: "100%",
      borderCollapse: "collapse",
      fontSize: 12,
      marginTop: 10,
    },
    th: {
      background: "#F3F4F6",
      padding: "5px 8px",
      textAlign: "left",
      fontWeight: 600,
      color: "#374151",
      borderBottom: "1px solid #E5E7EB",
    },
    td: (i) => ({
      padding: "5px 8px",
      borderBottom: "1px solid #F3F4F6",
      background: i % 2 === 0 ? "#fff" : "#FAFAFA",
    }),
  };

  // ── Drop-zone drag handlers ────────────────────────────────────────────────
  const [dragging, setDragging] = useState(false);
  const onDragOver  = (e) => { e.preventDefault(); setDragging(true); };
  const onDragLeave = ()  => setDragging(false);
  const onDrop      = (e) => {
    e.preventDefault();
    setDragging(false);
    const files = Array.from(e.dataTransfer.files || []);
    setSelectedFiles(files);
    setLog([]);
    setProgress({ total: 0, done: 0, ok: 0, replaced: 0, skipped: 0, errors: 0, chunks: 0 });
  };

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div style={S.panel}>

      {/* ── Section title ── */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <span style={{ fontWeight: 700, fontSize: 15, color: "#AE132A" }}>
          Upload Documents
        </span>
        <span style={{ fontSize: 11, color: "#77787B" }}>
          {docs.length} source{docs.length !== 1 ? "s" : ""} indexed
        </span>
      </div>

      {/* ── Document-type selector ── */}
      <label style={S.label}>Document Type</label>
      <select
        value={docType}
        onChange={e => setDocType(e.target.value)}
        disabled={uploading}
        style={S.select}
      >
        {DOC_TYPES.map(d => (
          <option key={d.value} value={d.value}>
            {d.icon}  {d.label}
          </option>
        ))}
      </select>

      {/* Type description */}
      <div style={{
        fontSize: 12, color: "#6B7280", background: "#F9FAFB",
        border: "1px solid #E5E7EB", borderRadius: 6,
        padding: "6px 10px", marginBottom: 14, lineHeight: 1.5,
      }}>
        {docType === "Auto-detect"    && "The system will classify each file automatically using its content and filename."}
        {docType === "Algorithm"      && "Diagnostic algorithm JSON or PDF files from ARUP Consult (nodes + edges structure)."}
        {docType === "Fact Sheet"     && "ATI fact sheet JSONs from ARUP Consult (sections + featured_tests structure)."}
        {docType === "Consult Topic"  && "Disease / consult topic JSONs from ARUP Consult (entity_type: topic)."}
        {docType === "Test Directory" && "Test directory JSONs scraped from ltd.aruplab.com (test_id + details structure)."}
      </div>

      {/* Algorithm PDF hint */}
      {(docType === "Algorithm" || docType === "Auto-detect") && (
        <div style={{
          fontSize: 11, color: "#0369A1",
          background: "#F0F9FF", border: "1px solid #BAE6FD",
          borderRadius: 6, padding: "8px 12px", marginBottom: 14,
          lineHeight: 1.6,
        }}>
          <div style={{ fontWeight: 700, marginBottom: 3 }}>
            📎 Embedded split-view PDF requires two uploads:
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <div>
              <span style={{ fontWeight: 600 }}>① Algorithm JSON</span>
              {" "}— provides the rendered flowchart (nodes + edges structure).
            </div>
            <div>
              <span style={{ fontWeight: 600 }}>② Matching algorithm PDF</span>
              {" "}— enables the side-by-side embedded PDF pane. The PDF filename
              or title must match the algorithm JSON title so the app can link them automatically.
            </div>
          </div>
          <div style={{ marginTop: 4, color: "#0284C7", fontStyle: "italic" }}>
            When both are present, the algorithm card opens in split view by default — no remote URL needed.
          </div>
        </div>
      )}

      {/* ── Auto-Replace / Dedup toggle ── */}
      <div
        onClick={() => !uploading && setAutoReplace(v => !v)}
        style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "10px 12px",
          background: autoReplace ? "#FFF5F5" : "#F9FAFB",
          border: `1px solid ${autoReplace ? "#AE132A" : "#E5E7EB"}`,
          borderRadius: 6,
          marginBottom: 14,
          cursor: uploading ? "not-allowed" : "pointer",
          userSelect: "none",
          transition: "all 0.15s",
        }}
      >
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, color: autoReplace ? "#AE132A" : "#374151" }}>
            {autoReplace ? "⟳  Auto-Replace Duplicates" : "⏭  Skip Duplicates"}
          </div>
          <div style={{ fontSize: 11, color: "#6B7280", marginTop: 2, lineHeight: 1.4 }}>
            {autoReplace
              ? "Existing files will be deleted and re-indexed automatically. Recommended after processor updates."
              : "Files already indexed will be skipped. Toggle on to refresh existing documents."}
          </div>
        </div>
        {/* Toggle pill */}
        <div style={{
          width: 40, height: 22, borderRadius: 11,
          background: autoReplace ? "#AE132A" : "#D1D5DB",
          position: "relative", flexShrink: 0, marginLeft: 12,
          transition: "background 0.2s",
        }}>
          <div style={{
            position: "absolute",
            top: 3, left: autoReplace ? 21 : 3,
            width: 16, height: 16,
            borderRadius: "50%",
            background: "#fff",
            boxShadow: "0 1px 3px rgba(0,0,0,.2)",
            transition: "left 0.2s",
          }} />
        </div>
      </div>

      {/* ── Drop zone / file picker ── */}
      <label style={S.label}>Select Files</label>
      <div
        style={S.dropZone(dragging)}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onClick={() => !uploading && fileInputRef.current?.click()}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ACCEPT_EXTS}
          style={{ display: "none" }}
          onChange={handleFilePick}
          disabled={uploading}
        />
        <div style={{ fontSize: 28, marginBottom: 6 }}>
          {selectedFiles.length > 0 ? "📁" : "⬆️"}
        </div>
        {selectedFiles.length === 0 ? (
          <>
            <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>
              Click to browse or drag & drop files here
            </div>
            <div style={{ fontSize: 12, color: "#6B7280" }}>
              PDF, JSON, CSV, TSV, TXT — up to 3,500 files at once
            </div>
          </>
        ) : (
          <>
            <div style={{ fontWeight: 700, fontSize: 14, color: "#111827", marginBottom: 2 }}>
              {selectedFiles.length.toLocaleString()} file{selectedFiles.length !== 1 ? "s" : ""} selected
            </div>
            <div style={{ fontSize: 12, color: "#6B7280" }}>
              Total size: {formatBytes(totalSize)}
              {totalBatches > 1 && ` · Will upload in ${totalBatches} batches of ${BATCH_SIZE}`}
            </div>
            <div style={{ fontSize: 12, color: "#9CA3AF", marginTop: 2 }}>
              Click to change selection
            </div>
          </>
        )}
      </div>

      {/* Selected type badge */}
      {selectedFiles.length > 0 && (
        <div style={{ marginBottom: 14, fontSize: 12, color: "#6B7280" }}>
          Will be indexed as:&nbsp;
          <TypeBadge type={docType === "Auto-detect" ? "Auto-detect" : docType} />
          &nbsp;·&nbsp;
          <span
            style={{ color: "#AE132A", cursor: "pointer", textDecoration: "underline" }}
            onClick={clearSelection}
          >
            Clear selection
          </span>
        </div>
      )}

      {/* ── Action buttons ── */}
      <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 14 }}>
        <button
          style={S.btnPrimary(!selectedFiles.length || uploading)}
          disabled={!selectedFiles.length || uploading}
          onClick={startUpload}
        >
          {uploading
            ? `Uploading… (${progress.done}/${progress.total})`
            : selectedFiles.length > 0
              ? `${autoReplace ? "Replace &" : "Upload"} ${selectedFiles.length.toLocaleString()} File${selectedFiles.length !== 1 ? "s" : ""}`
              : "Upload Files"}
        </button>

        {uploading && (
          <button style={S.btnCancel} onClick={cancelUpload}>
            Cancel
          </button>
        )}

        {!uploading && selectedFiles.length > 0 && (
          <button style={S.btnSecondary} onClick={clearSelection}>
            Clear
          </button>
        )}
      </div>

      {/* ── Progress bar ── */}
      {(uploading || progress.done > 0) && (
        <div style={{ marginBottom: 10 }}>
          <div style={S.progressBar}>
            <div style={S.progressFill(progressPct, progress.errors > 0)} />
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "#6B7280" }}>
            <span>{progressPct}% — {progress.done.toLocaleString()} / {progress.total.toLocaleString()} files</span>
            <span style={{ display: "flex", gap: 10 }}>
              {progress.ok > 0       && <span style={{ color: "#16A34A" }}>✓ {progress.ok.toLocaleString()} new</span>}
              {progress.replaced > 0 && <span style={{ color: "#AE132A" }}>↺ {progress.replaced.toLocaleString()} replaced</span>}
              {progress.errors > 0   && <span style={{ color: "#DC2626" }}>✗ {progress.errors}</span>}
              {progress.skipped > 0  && <span style={{ color: "#D97706" }}>⚠ {progress.skipped} skipped</span>}
              {progress.chunks > 0   && <span style={{ color: "#6B7280" }}>{progress.chunks.toLocaleString()} chunks</span>}
            </span>
          </div>
        </div>
      )}

      {/* ── Log feed ── */}
      {log.length > 0 && (
        <div style={S.logBox}>
          {log.map((entry, i) => (
            <div key={i} style={S.logLine(entry.level)}>
              {entry.level === "ok"    ? "✓" :
               entry.level === "error" ? "✗" :
               entry.level === "warn"  ? "⚠" : "·"} {entry.msg}
            </div>
          ))}
        </div>
      )}

      {/* ── Indexed documents list ── */}
      <div style={{ marginTop: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: "#374151" }}>
            Indexed Documents
          </span>
          <button
            style={{ ...S.btnSecondary, padding: "3px 10px", fontSize: 11 }}
            onClick={refreshDocs}
            disabled={loadingDocs}
          >
            {loadingDocs ? "…" : "↻ Refresh"}
          </button>
        </div>

        {docs.length === 0 ? (
          <div style={{ fontSize: 12, color: "#9CA3AF", padding: "10px 0" }}>
            No documents indexed yet.
          </div>
        ) : (
          <div style={{ maxHeight: 240, overflowY: "auto" }}>
            <table style={S.docsTable}>
              <thead>
                <tr>
                  <th style={S.th}>File</th>
                  <th style={S.th}>Type</th>
                  <th style={{ ...S.th, textAlign: "right" }}>Chunks</th>
                </tr>
              </thead>
              <tbody>
                {docs.map((doc, i) => (
                  <tr key={doc.filename}>
                    <td style={S.td(i)} title={doc.filename}>
                      <span style={{
                        display: "block", maxWidth: 180,
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                      }}>
                        {doc.filename}
                      </span>
                    </td>
                    <td style={S.td(i)}>
                      <TypeBadge type={doc.source_type} />
                    </td>
                    <td style={{ ...S.td(i), textAlign: "right", color: "#6B7280" }}>
                      {doc.chunks}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

    </div>
  );
}