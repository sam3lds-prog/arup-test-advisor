/**
 * DesignerPanel.jsx  v2.0.0
 * ─────────────────────────────────────────────────────────────────────────────
 * v2.0.0 additions (Design System Library):
 *   • Tab "Component Gallery" renamed to "Design System Library"
 *   • ComponentGallery replaced with DesignSystemLibrary workbench
 *   • Panel widens to 720px when library tab is active
 *   • Live preview renderers for all 8 component types:
 *       PreviewTextBlock, PreviewRecommendationCard, PreviewTable,
 *       PreviewAlgorithmFlow, PreviewWarningBlock, PreviewInfoBlock,
 *       PreviewBadgeGroup, PreviewCitationTable
 *   • Algorithm layout modes: vertical | tree | grouped_branch_layout | image_guided
 *       — BFS-based level grouping renders parallel branches side-by-side
 *   • Feedback chat against any library component (AI-powered, Claude Haiku)
 *   • Version history panel with restore affordance
 *   • Action buttons: Render Preview, Reset to Base, Approve, Save to Library, History
 *   • Design token rules panel (from GET /designer/tokens)
 *   • Status filter: All | System | Approved | Draft
 *
 * v1.0.0 retained (fully backward-compatible):
 *   • Schema Inspector tab
 *   • Preferences Editor tab
 *
 * Design constraints:
 *   • No raw hex values — all colours via CSS variables
 *   • Roboto font only
 *   • Link text via var(--lab-blue)
 *   • Uses existing badge classes from index.css
 *   • Token refs: typography.json, buttons.json, colors.json, card-rules.json,
 *                 table.json, spacing-rules.json (loaded from backend)
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import MessageBubble, { AlgorithmFlowchart } from './MessageBubble.jsx'

const API = '/api'

/* ── Tab config ──────────────────────────────────────────────────────────── */
const TABS = [
  { id: 'schema',      label: 'Schema Inspector',      icon: '⬡' },
  { id: 'preferences', label: 'Preferences',           icon: '⚙' },
  { id: 'library',     label: 'Design System Library', icon: '⊞' },
]

/* ── Component type → badge class ───────────────────────────────────────── */
const TYPE_BADGE = {
  text_block:          'badge badge-source-general',
  recommendation_card: 'badge badge-danger',
  table:               'badge badge-info',
  algorithm_flow:      'badge badge-source-algo',
  badge_group:         'badge badge-positive',
  warning_block:       'badge badge-source-fact',
  info_block:          'badge badge-source-consult',
  citation_table:      'badge badge-source-dir',
}

/* ── Status badge colors ─────────────────────────────────────────────────── */
const STATUS_STYLE = {
  system:   { bg: 'var(--secondary)', color: 'var(--white)', label: 'System' },
  approved: { bg: 'var(--positive)', color: 'var(--white)', label: 'Approved' },
  draft:    { bg: 'var(--warning, #F59E0B)', color: 'var(--white)', label: 'Draft' },
  archived: { bg: 'var(--accent2)', color: 'var(--white)', label: 'Archived' },
}

/* ── Node type styles for algorithm preview ──────────────────────────────── */
const ALGO_NODE_STYLE = {
  start:    { bg: 'var(--secondary)',          color: 'var(--white)',    icon: '▶', radius: '50vh', fontWeight: 700 },
  end:      { bg: 'var(--primary)',            color: 'var(--white)',    icon: '■', radius: '50vh', fontWeight: 700 },
  test:     { bg: 'var(--lab-blue)',           color: 'var(--white)',    icon: '⬡', radius: 8, fontWeight: 500 },
  decision: { bg: 'var(--white)',              color: 'var(--dark-sky)', icon: '◆', radius: 6, border: '2px solid var(--primary)', fontWeight: 500 },
  result:   { bg: 'var(--positive-container)', color: 'var(--positive)', icon: '✓', radius: 6, fontWeight: 400 },
  action:   { bg: 'var(--white)',              color: 'var(--dark-sky)', icon: '→', radius: 6, border: '1px solid var(--accent)', fontWeight: 400 },
  info:     { bg: 'var(--bg-salt)',            color: 'var(--accent2)',  icon: 'ℹ', radius: 6, fontWeight: 400 },
}

/* ── Simple inline-markdown: **bold** ───────────────────────────────────── */
function InlineMd({ text = '' }) {
  const parts = text.split(/\*\*(.+?)\*\*/g)
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 1 ? <strong key={i}>{p}</strong> : p
      )}
    </>
  )
}

/* ═══════════════════════════════════════════════════════════════════════════
   Preview renderers — one per component type
   All use CSS variables exclusively; match the ARUP design system.
   ═══════════════════════════════════════════════════════════════════════════ */

/* ── Text Block ─────────────────────────────────────────────────────────── */
function PreviewTextBlock({ props: p = {}, rules: r = {} }) {
  return (
    <div style={{
      padding: '14px 16px',
      background: r.bg_token || 'transparent',
      borderRadius: 'var(--card-radius)',
      lineHeight: r.line_height || 1.6,
      fontSize: 14,
      color: r.color_token || 'var(--dark-sky)',
    }}>
      <InlineMd text={p.content || 'Sample narrative text block content.'} />
    </div>
  )
}

/* ── Recommendation Card ─────────────────────────────────────────────────── */
function PreviewRecommendationCard({ props: p = {}, rules: r = {} }) {
  const priorityColor =
    p.priority === 'high'     ? (r.border_left_color_high     || 'var(--primary)') :
    p.priority === 'moderate' ? (r.border_left_color_moderate || 'var(--lab-blue)') :
                                (r.border_left_color_low      || 'var(--accent3)')

  const priorityLabel =
    p.priority === 'high' ? 'Primary' :
    p.priority === 'moderate' ? 'Secondary' : 'Supporting'

  return (
    <div style={{
      background: r.bg_token || 'var(--white)',
      border: '1px solid var(--accent3)',
      borderLeft: `${r.border_left_width || 4}px solid ${priorityColor}`,
      borderRadius: 'var(--card-radius)',
      padding: 'var(--card-padding)',
      display: 'flex',
      flexDirection: 'column',
      gap: 8,
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, justifyContent: 'space-between' }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--dark-sky)', lineHeight: 1.25 }}>
            {p.test_name || 'TSH – Thyroid Stimulating Hormone'}
          </div>
          {p.order_code && (
            <div style={{ fontSize: 11, color: 'var(--lab-blue)', marginTop: 2, fontWeight: 500 }}>
              ARUP Code: {p.order_code}
            </div>
          )}
        </div>
        <span style={{
          fontSize: 10, fontWeight: 700, padding: '2px 10px',
          background: priorityColor, color: 'var(--white)',
          borderRadius: '50vh', whiteSpace: 'nowrap', flexShrink: 0,
        }}>
          {priorityLabel}
        </span>
      </div>

      {/* Components */}
      {p.components?.length > 0 && (
        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
          {p.components.map((c, i) => (
            <span key={i} style={{
              fontSize: 11, padding: '1px 8px',
              background: 'var(--bg-salt)', border: '1px solid var(--accent3)',
              borderRadius: '50vh', color: 'var(--accent2)',
            }}>{c}</span>
          ))}
        </div>
      )}

      {/* Rationale */}
      {p.rationale && r.show_rationale !== false && (
        <p style={{ fontSize: 13, color: 'var(--dark-sky)', lineHeight: 1.5, margin: 0 }}>
          {p.rationale}
        </p>
      )}

      {/* Meta row */}
      <div style={{ display: 'flex', gap: 16, fontSize: 12, color: 'var(--accent)', flexWrap: 'wrap' }}>
        {p.turnaround && r.show_turnaround !== false && (
          <span><strong style={{ color: 'var(--dark-sky)' }}>TAT:</strong> {p.turnaround}</span>
        )}
        {p.specimen && (
          <span><strong style={{ color: 'var(--dark-sky)' }}>Specimen:</strong> {p.specimen}</span>
        )}
      </div>

      {/* Special instructions */}
      {p.special_instructions && (
        <div style={{
          fontSize: 11, color: 'var(--accent2)',
          background: 'var(--info-container, #E3EEF5)',
          border: '1px solid var(--info, #83A8C5)',
          borderRadius: 4, padding: '6px 10px',
        }}>
          ⚠ {p.special_instructions}
        </div>
      )}
    </div>
  )
}

/* ── Table ──────────────────────────────────────────────────────────────── */
function PreviewTable({ props: p = {}, rules: r = {} }) {
  const headers = p.headers || ['Test', 'Sensitivity', 'Specificity', 'Order Code']
  const rows    = p.rows    || [
    ['tTG IgA', '95%', '97%', 'TTGA'],
    ['EMA IgA', '85%', '99%', 'EMA'],
    ['DGP IgG', '80%', '98%', 'DGLIGG'],
  ]

  return (
    <div>
      {p.caption && (
        <p style={{ fontSize: 12, color: 'var(--accent)', marginBottom: 8, fontWeight: 500 }}>
          {p.caption}
        </p>
      )}
      <div style={{ overflowX: 'auto' }}>
        <table style={{
          width: '100%', borderCollapse: 'collapse',
          fontSize: r.font_size || 13,
          border: `1px solid ${r.border_color || 'var(--accent3)'}`,
        }}>
          <thead>
            <tr>
              {headers.map((h, i) => (
                <th key={i} style={{
                  background: r.header_bg || 'var(--secondary)',
                  color: r.header_color || 'var(--white)',
                  fontWeight: r.header_font_weight || 600,
                  padding: r.cell_padding || '8px 12px',
                  textAlign: 'left',
                  fontSize: 12,
                  borderRight: i < headers.length - 1 ? `1px solid rgba(255,255,255,0.15)` : 'none',
                }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri} style={{
                background: ri % 2 === 0
                  ? (r.stripe_odd_bg  || 'var(--white)')
                  : (r.stripe_even_bg || 'var(--bg-salt)'),
              }}>
                {row.map((cell, ci) => (
                  <td key={ci} style={{
                    padding: r.cell_padding || '8px 12px',
                    color: ci === 0 ? 'var(--dark-sky)' : 'var(--accent2)',
                    fontWeight: ci === 0 ? 500 : 400,
                    borderBottom: `1px solid ${r.border_color || 'var(--accent3)'}`,
                    borderRight: ci < row.length - 1
                      ? `1px solid ${r.border_color || 'var(--accent3)'}` : 'none',
                    fontSize: r.font_size || 13,
                  }}>
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/* ── Algorithm Flow ─────────────────────────────────────────────────────── */
/* Uses the shared AlgorithmFlowchart component from MessageBubble to guarantee
   identical rendering between the Designer preview and live chat output.
   Supports all v3 render modes: clinical_linear_document, clinical_tree_document,
   clinical_multi_zone_document. */
function PreviewAlgorithmFlow({ props: p = {}, rules: r = {} }) {
  const [overrideMode, setOverrideMode] = useState(null) // null = auto
  const [widthPreset, setWidthPreset]   = useState('document') // card | document | full
  const [pdfPreview,  setPdfPreview]    = useState(false) // simulate has_local_pdf for split-view testing

  // Build a viz object compatible with AlgorithmFlowchart
  const baseMode = p.render_mode || p.layout_mode || 'clinical_linear_document'
  const effectiveMode = overrideMode || baseMode
  const maxW = widthPreset === 'card' ? 560 : widthPreset === 'full' ? '100%' : 820

  const viz = {
    title:                   p.title || 'Algorithm Preview',
    source_url:              p.source_url || '',
    source_page_url:         p.source_page_url || p.source_url || '',
    // PDF fields — use props if supplied, otherwise fall back to pdfPreview toggle
    pdf_url:                 p.pdf_url  || (pdfPreview ? '/api/assets/pdf/preview-mock-id' : ''),
    has_local_pdf:           p.has_local_pdf  != null ? p.has_local_pdf  : pdfPreview,
    pdf_origin:              p.pdf_origin     || (pdfPreview ? 'local_uploaded' : 'none'),
    pdf_asset_id:            p.pdf_asset_id   || (pdfPreview ? 'preview-mock-id' : ''),
    source_asset_type:       p.source_asset_type || 'unknown',
    reviewed_date:           p.reviewed_date || '',
    updated_date:            p.updated_date || '',
    routing_label:           p.routing_label || '',
    render_mode:             effectiveMode,
    layout:                  effectiveMode,
    layout_mode:             effectiveMode,
    is_multi_zone:           effectiveMode === 'clinical_multi_zone_document',
    fork_style:              p.fork_style || 'none',
    entry_type:              p.entry_type || 'none',
    compact_branch_layout:   effectiveMode !== 'clinical_multi_zone_document',
    should_center_spine:     true,
    entry_section_node_ids:  Array.isArray(p.entry_section_node_ids) ? p.entry_section_node_ids : [],
    spine_node_ids:          Array.isArray(p.spine_node_ids) ? p.spine_node_ids : [],
    groups:                  Array.isArray(p.groups) ? p.groups : [],
    branch_splits:           Array.isArray(p.branch_splits) ? p.branch_splits : [],
    nodes:                   Array.isArray(p.nodes) ? p.nodes : [],
    edges:                   Array.isArray(p.edges) ? p.edges : [],
    referenced_tests:        Array.isArray(p.referenced_tests) ? p.referenced_tests : [],
    footer_blocks:           Array.isArray(p.footer_blocks) ? p.footer_blocks : [],
    stats: p.stats || {
      total_nodes:    Array.isArray(p.nodes) ? p.nodes.length : 0,
      total_edges:    Array.isArray(p.edges) ? p.edges.length : 0,
      max_depth:      p.max_depth || 0,
      has_branches:   false,
      decision_count: Array.isArray(p.nodes) ? p.nodes.filter(n => n.type === 'decision').length : 0,
      outcome_count:  Array.isArray(p.nodes) ? p.nodes.filter(n => n.type === 'outcome').length : 0,
    },
  }

  // Ensure nodes have all required fields for the richer renderer
  viz.nodes = viz.nodes.map(n => ({
    variant:   n.variant   || (n.type === 'start' ? 'startNode' : n.type === 'decision' ? 'decisionNode' : n.type === 'outcome' ? 'outcomeNode' : 'actionNode'),
    emphasis:  n.emphasis  || (n.type === 'start' || n.type === 'decision' ? 'primary' : 'secondary'),
    keyword:   n.keyword   || null,
    raw_label: n.raw_label || n.label || '',
    level:     n.level     || 0,
    branch:    n.branch    || null,
    has_children: n.has_children || false,
    children_count: n.children_count || 0,
    children_ids: n.children_ids || [],
    ...n,
  }))

  if (!viz.nodes.length) {
    return (
      <div style={{ padding: 20, textAlign: 'center', color: 'var(--accent)', fontSize: 13 }}>
        No algorithm nodes to preview. Add nodes to the algorithm_flow component.
      </div>
    )
  }

  const modeOptions = [
    { id: null,                           label: 'Auto' },
    { id: 'clinical_linear_document',     label: 'Linear' },
    { id: 'clinical_tree_document',       label: 'Tree' },
    { id: 'clinical_multi_zone_document', label: 'Multi-zone' },
  ]

  return (
    <div style={{ padding: '0 0 4px' }}>
      {/* Preview controls — layout mode + width preset */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12, marginBottom: 8 }}>
        {/* Layout mode selector */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 10, color: 'var(--accent)', fontWeight: 600 }}>Layout:</span>
          <div style={{ display: 'flex', gap: 2, background: 'var(--bg-salt)', borderRadius: 5, padding: 2 }}>
            {modeOptions.map(opt => (
              <button key={String(opt.id)} onClick={() => setOverrideMode(opt.id)}
                style={{
                  fontSize: 9, padding: '2px 7px', borderRadius: 3, cursor: 'pointer',
                  fontWeight: overrideMode === opt.id ? 700 : 400,
                  background: overrideMode === opt.id ? 'var(--white)' : 'transparent',
                  border: overrideMode === opt.id ? '1px solid var(--accent3)' : '1px solid transparent',
                  color: overrideMode === opt.id ? 'var(--dark-sky)' : 'var(--accent)',
                  fontFamily: 'inherit',
                }}
              >{opt.label}</button>
            ))}
          </div>
        </div>
        {/* Width preset */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 10, color: 'var(--accent)', fontWeight: 600 }}>Width:</span>
          <div style={{ display: 'flex', gap: 2 }}>
            {['card', 'document', 'full'].map(w => (
              <button key={w} onClick={() => setWidthPreset(w)} style={{
                fontSize: 9, padding: '2px 7px', borderRadius: 3, cursor: 'pointer',
                background: widthPreset === w ? 'var(--secondary)' : 'var(--bg-salt)',
                color: widthPreset === w ? 'var(--white)' : 'var(--accent)',
                border: '1px solid var(--accent3)', fontFamily: 'inherit', textTransform: 'capitalize',
              }}>{w}</button>
            ))}
          </div>
        </div>
        {/* PDF preview toggle — simulates has_local_pdf for split-view testing */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 10, color: 'var(--accent)', fontWeight: 600 }}>PDF:</span>
          <button
            onClick={() => setPdfPreview(v => !v)}
            style={{
              fontSize: 9, padding: '2px 9px', borderRadius: 3, cursor: 'pointer',
              fontWeight: pdfPreview ? 700 : 400,
              background: pdfPreview ? 'var(--positive-container, #F0FFF4)' : 'var(--bg-salt)',
              color: pdfPreview ? 'var(--positive)' : 'var(--accent)',
              border: pdfPreview ? '1px solid var(--positive)' : '1px solid var(--accent3)',
              fontFamily: 'inherit',
            }}
            title="Simulate a locally-uploaded PDF asset to test the split-view layout"
          >
            {pdfPreview ? '📎 Local PDF on' : 'Simulate PDF'}
          </button>
        </div>
        <span style={{ fontSize: 9, color: 'var(--accent3)' }}>
          {effectiveMode.replace('clinical_', '').replace('_document', '')}
        </span>
        {/* Source links if available */}
        {(p.source_url || p.pdf_url) && (
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
            {p.source_url && <a href={p.source_url} target="_blank" rel="noreferrer" style={{ fontSize: 10, color: 'var(--lab-blue)' }}>Source ↗</a>}
            {p.pdf_url && <a href={p.pdf_url} target="_blank" rel="noreferrer" style={{ fontSize: 10, color: 'var(--lab-blue)' }}>PDF ↗</a>}
          </div>
        )}
      </div>
      <div style={{ maxWidth: maxW, margin: '0 auto' }}>
        <AlgorithmFlowchart
          viz={viz}
          showSplitView={pdfPreview || p.showSplitView}
          defaultViewMode={pdfPreview ? 'split' : (p.defaultViewMode || 'rendered')}
        />
      </div>
    </div>
  )
}

/* ── Warning Block ──────────────────────────────────────────────────────── */
function PreviewWarningBlock({ props: p = {}, rules: r = {} }) {
  return (
    <div style={{
      background: r.bg_token || 'var(--warning-container, #FFF8E1)',
      borderLeft: `${r.border_left_width || 4}px solid ${r.border_left_color || 'var(--warning, #F59E0B)'}`,
      borderRadius: 'var(--card-radius)',
      padding: r.padding || '12px 16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 16 }}>{r.icon || '⚠'}</span>
        <span style={{ fontSize: 13, fontWeight: 600, color: r.label_color || 'var(--warning, #F59E0B)' }}>
          {p.title || 'Clinical Caution'}
        </span>
      </div>
      {(p.items || []).map((item, i) => (
        <div key={i} style={{
          fontSize: 12, color: r.text_color || 'var(--dark-sky)',
          lineHeight: 1.5, paddingLeft: 24,
        }}>
          • {item}
        </div>
      ))}
    </div>
  )
}

/* ── Info Block ─────────────────────────────────────────────────────────── */
function PreviewInfoBlock({ props: p = {}, rules: r = {} }) {
  return (
    <div style={{
      background: r.bg_token || 'var(--info-container, #E3EEF5)',
      border: `1px solid ${r.border_token || 'var(--info, #83A8C5)'}`,
      borderRadius: 'var(--card-radius)',
      padding: r.padding || '12px 16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 15 }}>{r.icon || 'ℹ'}</span>
        <span style={{ fontSize: 13, fontWeight: 600, color: r.text_token || 'var(--dark-sky)' }}>
          {p.title || 'Clinical Context'}
        </span>
      </div>
      {(p.items || []).map((item, i) => (
        <div key={i} style={{
          fontSize: 12, color: r.text_token || 'var(--accent2)',
          lineHeight: 1.5, paddingLeft: 24,
        }}>
          • {item}
        </div>
      ))}
    </div>
  )
}

/* ── Badge Group ────────────────────────────────────────────────────────── */
function PreviewBadgeGroup({ props: p = {}, rules: r = {} }) {
  const badges = p.badges || [
    { label: 'Algorithm', cls: 'badge badge-source-algo' },
    { label: 'Consult Topic', cls: 'badge badge-source-consult' },
    { label: 'Fact Sheet', cls: 'badge badge-source-fact' },
    { label: 'Test Directory', cls: 'badge badge-source-dir' },
  ]
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: '8px 0' }}>
      <div className="text-caption" style={{ color: 'var(--accent)', fontWeight: 600, marginBottom: 2 }}>
        {p.label || 'Evidence Coverage'}
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {badges.map((b, i) => (
          <span key={i} className={b.cls}>{b.label}</span>
        ))}
      </div>
    </div>
  )
}

/* ── Citation Table ─────────────────────────────────────────────────────── */
function PreviewCitationTable({ props: p = {}, rules: r = {} }) {
  const citations = p.citations || [
    { source_type: 'Algorithm', title: 'Celiac Disease Testing for Symptomatic Individuals', relevance_score: 0.95 },
    { source_type: 'Consult Topic', title: 'Celiac Disease — ARUP Consult', relevance_score: 0.87 },
    { source_type: 'Fact Sheet', title: 'tTG IgA Antibody Testing', relevance_score: 0.80 },
  ]
  const SOURCE_CFG = {
    'Algorithm':      { cls: 'badge badge-source-algo',    label: 'Algorithm' },
    'Consult Topic':  { cls: 'badge badge-source-consult', label: 'Consult' },
    'Fact Sheet':     { cls: 'badge badge-source-fact',    label: 'Fact Sheet' },
    'Test Directory': { cls: 'badge badge-source-dir',     label: 'Directory' },
    'General':        { cls: 'badge badge-source-general', label: 'General' },
  }

  return (
    <div>
      <div className="text-label" style={{ color: 'var(--accent)', marginBottom: 8 }}>
        {p.title || 'Supporting Sources'}
        <span style={{ marginLeft: 6, fontSize: 11, fontWeight: 400, color: 'var(--accent3)' }}>
          ({citations.length})
        </span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {citations.map((c, i) => {
          const cfg = SOURCE_CFG[c.source_type] || SOURCE_CFG['General']
          const pct = Math.round((c.relevance_score || 0) * 100)
          return (
            <div key={i} style={{
              display: 'flex', alignItems: 'flex-start', gap: 8,
              padding: '7px 10px',
              background: i % 2 === 0 ? 'var(--white)' : 'var(--bg-salt)',
              border: '1px solid var(--accent3)',
              borderRadius: 4,
            }}>
              <span className={cfg.cls} style={{ fontSize: 10, flexShrink: 0, marginTop: 1 }}>
                {cfg.label}
              </span>
              <span style={{ flex: 1, fontSize: 12, color: 'var(--lab-blue)', lineHeight: 1.3 }}>
                {c.title || c.document_name || 'Untitled source'}
              </span>
              {pct > 0 && (
                <span style={{
                  fontSize: 10, fontWeight: 700, color: 'var(--positive)',
                  background: 'var(--positive-container)',
                  padding: '1px 6px', borderRadius: '50vh', flexShrink: 0,
                }}>
                  {pct}%
                </span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

/* ── ComponentPreview — dispatcher ───────────────────────────────────────── */
function ComponentPreview({ component }) {
  if (!component) return null
  const { type, preview_props: p = {}, rules: r = {} } = component

  const wrapStyle = {
    background: 'var(--white)',
    border: '1px solid var(--accent3)',
    borderRadius: 'var(--card-radius)',
    padding: 16,
    minHeight: 80,
  }

  const inner =
    type === 'text_block'          ? <PreviewTextBlock props={p} rules={r} /> :
    type === 'recommendation_card' ? <PreviewRecommendationCard props={p} rules={r} /> :
    type === 'table'               ? <PreviewTable props={p} rules={r} /> :
    type === 'algorithm_flow'      ? <PreviewAlgorithmFlow props={p} rules={r} /> :
    type === 'warning_block'       ? <PreviewWarningBlock props={p} rules={r} /> :
    type === 'info_block'          ? <PreviewInfoBlock props={p} rules={r} /> :
    type === 'badge_group'         ? <PreviewBadgeGroup props={p} rules={r} /> :
    type === 'citation_table'      ? <PreviewCitationTable props={p} rules={r} /> :
    <div style={{ color: 'var(--accent)', fontSize: 12, fontStyle: 'italic' }}>
      No preview renderer for type: {type}
    </div>

  return <div style={wrapStyle}>{inner}</div>
}

/* ═══════════════════════════════════════════════════════════════════════════
   FeedbackChat — redesigned full-height conversation panel
   ═══════════════════════════════════════════════════════════════════════════ */
const SUGGESTION_CHIPS = [
  'Make the title more prominent',
  'Use outlined buttons instead of solid',
  'Reduce padding between title and body',
  'Increase contrast on decision nodes',
  'Add a subtle shadow to the card',
  'Make the border-left thicker',
]

function FeedbackChat({ component, api, onUpdated }) {
  const [input, setInput]           = useState('')
  const [loading, setLoading]       = useState(false)
  const [applyResult, setApplyResult] = useState(null)   // {summary, affected_rules, snapshot_before}
  const [showBeforeAfter, setShowBeforeAfter] = useState(false)
  const [error, setError]           = useState(null)
  const scrollRef                   = useRef(null)

  const conversation = component?.conversation || []

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [conversation.length, loading])

  const send = async () => {
    if (!input.trim() || loading) return
    setLoading(true)
    setError(null)
    setApplyResult(null)
    setShowBeforeAfter(false)
    const msg = input.trim()
    setInput('')
    try {
      const r = await fetch(`${api}/designer/library/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ component_id: component.id, feedback: msg }),
      })
      if (!r.ok) {
        const d = await r.json()
        throw new Error(d.detail || 'Feedback failed')
      }
      const d = await r.json()
      // Capture before snapshot from the component's current rules before reload
      const snapshotBefore = JSON.stringify(component.rules || {}, null, 2)
      onUpdated && await onUpdated(d)
      setApplyResult({
        summary:         d.change_summary || 'Changes applied successfully.',
        affected_rules:  d.affected_rules  || [],
        snapshot_before: snapshotBefore,
        snapshot_after:  JSON.stringify(d.updated_rules || {}, null, 2),
      })
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Message history */}
      <div
        ref={scrollRef}
        style={{ flex: 1, overflowY: 'auto', padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}
      >
        {conversation.length === 0 && (
          <div style={{ padding: '16px 0' }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--dark-sky)', marginBottom: 4 }}>
              Design feedback
            </div>
            <div style={{ fontSize: 12, color: 'var(--accent)', marginBottom: 14, lineHeight: 1.5 }}>
              Describe a change and the AI will apply it to this component's rules and preview.
              After applying, you'll see a summary of what changed.
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {SUGGESTION_CHIPS.map((chip, i) => (
                <button key={i} onClick={() => setInput(chip)} style={{
                  textAlign: 'left', padding: '6px 12px',
                  background: 'var(--bg-salt)', border: '1px solid var(--accent3)',
                  borderRadius: '50vh', fontSize: 11, color: 'var(--lab-blue)',
                  cursor: 'pointer', fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
                  transition: 'border-color .15s',
                }}
                  onMouseEnter={e => e.currentTarget.style.borderColor = 'var(--lab-blue)'}
                  onMouseLeave={e => e.currentTarget.style.borderColor = 'var(--accent3)'}
                >
                  {chip}
                </button>
              ))}
            </div>
          </div>
        )}

        {conversation.map((msg, i) => {
          const isUser = msg.role === 'user'
          return (
            <div key={i} style={{ display: 'flex', flexDirection: 'column', alignSelf: isUser ? 'flex-end' : 'flex-start', maxWidth: '88%' }}>
              <div style={{
                padding: '8px 12px',
                borderRadius: isUser ? '12px 12px 2px 12px' : '12px 12px 12px 2px',
                background: isUser ? 'var(--secondary)' : 'var(--bg-salt)',
                color: isUser ? 'var(--white)' : 'var(--dark-sky)',
                fontSize: 12, lineHeight: 1.5,
                border: isUser ? 'none' : '1px solid var(--accent3)',
              }}>
                {msg.content}
              </div>
              <span style={{ fontSize: 10, color: 'var(--accent3)', marginTop: 2, alignSelf: isUser ? 'flex-end' : 'flex-start' }}>
                {msg.role === 'user' ? 'Designer' : 'AI'}{msg.timestamp ? ` · ${new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}
              </span>
            </div>
          )
        })}

        {loading && (
          <div style={{
            alignSelf: 'flex-start', display: 'flex', alignItems: 'center', gap: 8,
            padding: '8px 14px', background: 'var(--info-container, #E3EEF5)',
            border: '1px solid var(--info, #83A8C5)', borderRadius: '12px 12px 12px 2px',
            fontSize: 12, color: 'var(--accent2)',
          }}>
            <span style={{ animation: 'spin 1s linear infinite', display: 'inline-block' }}>⟳</span>
            Applying changes…
          </div>
        )}

        {/* ── Applied result: summary + rule chips + before/after toggle ── */}
        {applyResult && (
          <div style={{
            border: '1px solid var(--positive)', borderRadius: 8,
            background: 'var(--white)', overflow: 'hidden',
          }}>
            {/* Summary header */}
            <div style={{
              padding: '10px 14px',
              background: 'var(--positive-container)',
              borderBottom: '1px solid var(--positive)',
              display: 'flex', alignItems: 'flex-start', gap: 8,
            }}>
              <span style={{ color: 'var(--positive)', fontSize: 16, flexShrink: 0 }}>✓</span>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--positive)', marginBottom: 2 }}>
                  Changes applied
                </div>
                <div style={{ fontSize: 11, color: 'var(--positive)', lineHeight: 1.5, opacity: 0.85 }}>
                  {applyResult.summary}
                </div>
              </div>
            </div>

            {/* Affected rule chips */}
            {applyResult.affected_rules?.length > 0 && (
              <div style={{ padding: '8px 14px', borderBottom: '1px solid var(--accent3)' }}>
                <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--accent2)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 5 }}>
                  Rules updated
                </div>
                <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                  {applyResult.affected_rules.map((rule, i) => (
                    <span key={i} style={{
                      fontSize: 10, padding: '2px 8px',
                      background: 'var(--bg-salt)',
                      border: '1px solid var(--accent)',
                      borderRadius: '50vh', color: 'var(--accent2)',
                      fontFamily: 'monospace',
                    }}>
                      {rule}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Before/After toggle */}
            {(applyResult.snapshot_before || applyResult.snapshot_after) && (
              <div style={{ padding: '6px 14px 10px' }}>
                <button
                  onClick={() => setShowBeforeAfter(v => !v)}
                  style={{
                    fontSize: 10, color: 'var(--lab-blue)', background: 'none',
                    border: 'none', cursor: 'pointer', padding: 0,
                    fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
                    textDecoration: 'underline',
                  }}
                >
                  {showBeforeAfter ? '▲ Hide' : '▼ Show'} before / after diff
                </button>
                {showBeforeAfter && (
                  <div style={{ marginTop: 8, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                    {[
                      { label: 'Before', content: applyResult.snapshot_before, color: 'var(--danger)' },
                      { label: 'After',  content: applyResult.snapshot_after,  color: 'var(--positive)' },
                    ].map(({ label, content, color }) => (
                      <div key={label} style={{ overflow: 'hidden', borderRadius: 4 }}>
                        <div style={{
                          fontSize: 9, fontWeight: 700, color, textTransform: 'uppercase',
                          letterSpacing: '0.06em', padding: '3px 8px',
                          background: 'var(--bg-salt)', borderBottom: `1px solid ${color}`,
                        }}>
                          {label}
                        </div>
                        <pre style={{
                          fontSize: 9, background: 'var(--bg-salt)', padding: '6px 8px',
                          margin: 0, overflow: 'auto', maxHeight: 140, whiteSpace: 'pre-wrap',
                          color: 'var(--accent2)', lineHeight: 1.5, fontFamily: 'monospace',
                        }}>
                          {content || '—'}
                        </pre>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {error && (
          <div style={{ padding: '6px 10px', background: 'var(--danger-container)', border: '1px solid var(--danger)', borderRadius: 4, fontSize: 11, color: 'var(--danger)' }}>
            {error}
          </div>
        )}
      </div>

      {/* Suggestion chips when conversation exists */}
      {conversation.length > 0 && !loading && (
        <div style={{ padding: '6px 14px', borderTop: '1px solid var(--accent3)', display: 'flex', gap: 6, flexWrap: 'wrap', flexShrink: 0, background: 'var(--bg-salt)' }}>
          {SUGGESTION_CHIPS.slice(0, 3).map((chip, i) => (
            <button key={i} onClick={() => setInput(chip)} style={{
              padding: '3px 10px', fontSize: 10, background: 'var(--white)',
              border: '1px solid var(--accent3)', borderRadius: '50vh',
              cursor: 'pointer', color: 'var(--lab-blue)',
              fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
            }}>
              {chip}
            </button>
          ))}
        </div>
      )}

      {/* Composer */}
      <div style={{ padding: '10px 14px', borderTop: '1px solid var(--accent3)', display: 'flex', gap: 8, alignItems: 'flex-end', flexShrink: 0, background: 'var(--white)' }}>
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Describe a design change… (Enter to send)"
          rows={2}
          style={{
            flex: 1, resize: 'none', border: '1px solid var(--accent3)', borderRadius: 6,
            padding: '7px 10px', fontSize: 12,
            fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
            lineHeight: 1.4, outline: 'none',
          }}
          onFocus={e => e.currentTarget.style.borderColor = 'var(--lab-blue)'}
          onBlur={e => e.currentTarget.style.borderColor = 'var(--accent3)'}
        />
        <button onClick={send} disabled={loading || !input.trim()} style={{
          padding: '8px 16px',
          background: loading || !input.trim() ? 'var(--accent3)' : 'var(--primary)',
          color: 'var(--white)', border: 'none', borderRadius: 'var(--btn-radius)',
          fontSize: 12, fontWeight: 700,
          cursor: loading || !input.trim() ? 'not-allowed' : 'pointer',
          whiteSpace: 'nowrap', fontFamily: "'Roboto', Helvetica, Arial, sans-serif", minHeight: 36,
        }}>
          {loading ? '…' : 'Send ↑'}
        </button>
      </div>
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════════════════════
   HistoryPanel — version history for a library component
   ═══════════════════════════════════════════════════════════════════════════ */
function HistoryPanel({ componentId, api, onClose }) {
  const [versions, setVersions] = useState([])
  const [loading, setLoading]   = useState(true)
  const [expanded, setExpanded] = useState({})

  useEffect(() => {
    fetch(`${api}/designer/library/history/${componentId}`)
      .then(r => r.json())
      .then(d => setVersions((d.versions || []).slice().reverse()))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [componentId, api])

  return (
    <div style={{
      position: 'absolute', inset: 0,
      background: 'var(--white)', zIndex: 10,
      display: 'flex', flexDirection: 'column',
    }}>
      {/* Header */}
      <div style={{
        padding: '12px 16px',
        background: 'var(--secondary)',
        display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0,
      }}>
        <span style={{ color: 'var(--white)', fontWeight: 700, fontSize: 14, flex: 1 }}>
          Version History
        </span>
        <button
          onClick={onClose}
          style={{
            color: 'rgba(255,255,255,0.8)', background: 'none',
            fontSize: 18, lineHeight: 1, padding: '2px 4px',
            borderRadius: 4, cursor: 'pointer',
          }}
        >
          ×
        </button>
      </div>

      {/* Versions list */}
      <div style={{ flex: 1, overflowY: 'auto', padding: 12 }}>
        {loading && (
          <p className="text-caption" style={{ color: 'var(--accent)', fontStyle: 'italic' }}>Loading…</p>
        )}
        {!loading && versions.length === 0 && (
          <p className="text-caption" style={{ color: 'var(--accent3)', fontStyle: 'italic' }}>No versions recorded.</p>
        )}
        {versions.map((v, i) => {
          const isOpen = expanded[v.version_id]
          return (
            <div key={v.version_id} style={{
              border: '1px solid var(--accent3)', borderRadius: 'var(--card-radius)',
              marginBottom: 8, overflow: 'hidden',
            }}>
              <div
                onClick={() => setExpanded(p => ({ ...p, [v.version_id]: !p[v.version_id] }))}
                style={{
                  padding: '8px 12px',
                  background: i === 0 ? 'var(--positive-container)' : (i % 2 === 0 ? 'var(--white)' : 'var(--bg-salt)'),
                  display: 'flex', alignItems: 'center', gap: 8,
                  cursor: 'pointer',
                }}
              >
                {i === 0 && (
                  <span style={{
                    fontSize: 9, fontWeight: 700, padding: '1px 5px',
                    background: 'var(--positive)', color: 'var(--white)',
                    borderRadius: '50vh',
                  }}>
                    LATEST
                  </span>
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--dark-sky)' }}>
                    {v.summary}
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--accent)', marginTop: 1 }}>
                    {v.timestamp ? new Date(v.timestamp).toLocaleString() : ''} · ID: {v.version_id}
                  </div>
                </div>
                <span style={{ fontSize: 10, color: 'var(--accent3)' }}>{isOpen ? '▲' : '▼'}</span>
              </div>
              {isOpen && (
                <div style={{ padding: '8px 12px', borderTop: '1px solid var(--accent3)', background: 'var(--white)' }}>
                  <div className="text-caption" style={{ color: 'var(--accent)', marginBottom: 4, fontWeight: 600 }}>
                    Rules snapshot
                  </div>
                  <pre style={{
                    fontSize: 10, fontFamily: 'monospace', color: 'var(--accent2)',
                    background: 'var(--bg-salt)', border: '1px solid var(--accent3)',
                    borderRadius: 4, padding: '6px 8px',
                    whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                    margin: 0, maxHeight: 200, overflowY: 'auto', lineHeight: 1.5,
                  }}>
                    {JSON.stringify(v.rules_snapshot, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════════════════════
   TokensPanel — displays all design-system token files from /designer/tokens
   ═══════════════════════════════════════════════════════════════════════════ */
function TokensPanel({ api, onClose }) {
  const [tokens, setTokens] = useState(null)
  const [loading, setLoading] = useState(true)
  const [activeFile, setActiveFile] = useState(null)

  useEffect(() => {
    fetch(`${api}/designer/tokens`)
      .then(r => r.json())
      .then(d => {
        setTokens(d.tokens || {})
        setActiveFile(Object.keys(d.tokens || {})[0] || null)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [api])

  return (
    <div style={{
      position: 'absolute', inset: 0,
      background: 'var(--white)', zIndex: 10,
      display: 'flex', flexDirection: 'column',
    }}>
      {/* Header */}
      <div style={{
        padding: '12px 16px',
        background: 'var(--secondary)',
        display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0,
      }}>
        <span style={{ color: 'var(--white)', fontWeight: 700, fontSize: 14, flex: 1 }}>
          Design Token Reference
        </span>
        <button
          onClick={onClose}
          style={{
            color: 'rgba(255,255,255,0.8)', background: 'none',
            fontSize: 18, lineHeight: 1, padding: '2px 4px',
            borderRadius: 4, cursor: 'pointer',
          }}
        >
          ×
        </button>
      </div>

      {loading && (
        <div style={{ padding: 20, color: 'var(--accent)', fontSize: 12 }}>Loading tokens…</div>
      )}

      {tokens && (
        <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
          {/* File tabs (left) */}
          <div style={{
            width: 130, flexShrink: 0, borderRight: '1px solid var(--accent3)',
            overflowY: 'auto', background: 'var(--bg-salt)',
          }}>
            {Object.keys(tokens).map(file => (
              <button
                key={file}
                onClick={() => setActiveFile(file)}
                style={{
                  display: 'block', width: '100%', textAlign: 'left',
                  padding: '8px 12px', background: 'none', border: 'none',
                  borderLeft: activeFile === file ? '3px solid var(--primary)' : '3px solid transparent',
                  color: activeFile === file ? 'var(--primary)' : 'var(--accent2)',
                  fontSize: 11, fontWeight: activeFile === file ? 700 : 400,
                  cursor: 'pointer',
                }}
              >
                {file.replace('.json', '')}
              </button>
            ))}
          </div>
          {/* JSON content (right) */}
          <div style={{ flex: 1, overflowY: 'auto', padding: 12 }}>
            {activeFile && tokens[activeFile] && (
              <pre style={{
                fontSize: 10, fontFamily: 'monospace', color: 'var(--accent2)',
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                margin: 0, lineHeight: 1.6,
              }}>
                {JSON.stringify(tokens[activeFile], null, 2)}
              </pre>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════════════════════
   Inline tab panels — Rules, Tokens, History (used inside workbench sub-tabs)
   ═══════════════════════════════════════════════════════════════════════════ */

function RulesTab({ component }) {
  if (!component) return null
  return (
    <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Source basis */}
      {component.source_basis && typeof component.source_basis === 'object' && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--accent)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Design Token References
          </div>
          <div style={{
            padding: '10px 12px',
            background: 'var(--bg-salt)', border: '1px solid var(--accent3)',
            borderRadius: 'var(--card-radius)', fontSize: 12, color: 'var(--accent2)',
            display: 'flex', flexDirection: 'column', gap: 4,
          }}>
            {component.source_basis.based_on && (
              <div><strong style={{ color: 'var(--dark-sky)' }}>Based on:</strong> {component.source_basis.based_on}</div>
            )}
            {component.source_basis.design_tokens_used?.length > 0 && (
              <div><strong style={{ color: 'var(--dark-sky)' }}>Tokens:</strong>{' '}
                {component.source_basis.design_tokens_used.join(', ')}
              </div>
            )}
            {component.source_basis.token_refs && Object.entries(component.source_basis.token_refs).map(([k, v]) => (
              <div key={k}><strong style={{ color: 'var(--dark-sky)' }}>{k}:</strong> {v}</div>
            ))}
            {component.source_basis.sample_assets_used?.length > 0 && (
              <div><strong style={{ color: 'var(--dark-sky)' }}>Assets:</strong>{' '}
                {component.source_basis.sample_assets_used.join(', ')}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Rules JSON */}
      <div>
        <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--accent)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          Component Rules
        </div>
        <pre style={{
          fontSize: 10, fontFamily: 'monospace', color: 'var(--accent2)',
          background: 'var(--bg-salt)', border: '1px solid var(--accent3)',
          borderRadius: 'var(--card-radius)', padding: '10px 12px',
          whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          lineHeight: 1.6, margin: 0, overflowY: 'auto',
        }}>
          {JSON.stringify(component.rules, null, 2)}
        </pre>
      </div>

      {/* Algorithm layout reference */}
      {component.type === 'algorithm_flow' && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--accent)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Layout Modes
          </div>
          <div style={{
            padding: '10px 12px', background: 'var(--bg-salt)',
            border: '1px solid var(--accent3)', borderRadius: 'var(--card-radius)',
            fontSize: 12, color: 'var(--accent2)', lineHeight: 1.6,
            display: 'flex', flexDirection: 'column', gap: 3,
          }}>
            <div><strong style={{ color: 'var(--dark-sky)' }}>vertical</strong> — linear BFS stack (simple flows)</div>
            <div><strong style={{ color: 'var(--dark-sky)' }}>tree</strong> — BFS with branches shown side-by-side</div>
            <div><strong style={{ color: 'var(--dark-sky)' }}>grouped_branch_layout</strong> — multi-column diagnostic lanes</div>
            <div><strong style={{ color: 'var(--dark-sky)' }}>image_guided</strong> — ARUP Consult image reference; rendered as grouped columns</div>
            <div style={{ marginTop: 6, color: 'var(--accent3)', fontSize: 11 }}>
              Set <code style={{ background: 'var(--white)', padding: '0 4px', borderRadius: 2, border: '1px solid var(--accent3)' }}>
                preview_props.layout_mode
              </code> to switch. Feedback can update this.
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function InlineTokensTab({ api }) {
  const [tokens, setTokens]       = useState(null)
  const [loading, setLoading]     = useState(true)
  const [activeFile, setActiveFile] = useState(null)

  useEffect(() => {
    fetch(`${api}/designer/tokens`)
      .then(r => r.json())
      .then(d => {
        setTokens(d.tokens || {})
        setActiveFile(Object.keys(d.tokens || {})[0] || null)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [api])

  if (loading) return (
    <div style={{ padding: 20, color: 'var(--accent)', fontSize: 12, fontStyle: 'italic' }}>
      Loading tokens…
    </div>
  )
  if (!tokens) return null

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* File tabs */}
      <div style={{
        width: 120, flexShrink: 0, borderRight: '1px solid var(--accent3)',
        overflowY: 'auto', background: 'var(--bg-salt)',
      }}>
        {Object.keys(tokens).map(file => (
          <button
            key={file}
            onClick={() => setActiveFile(file)}
            style={{
              display: 'block', width: '100%', textAlign: 'left',
              padding: '8px 12px', background: 'none', border: 'none',
              borderLeft: activeFile === file ? '3px solid var(--primary)' : '3px solid transparent',
              color: activeFile === file ? 'var(--primary)' : 'var(--accent2)',
              fontSize: 11, fontWeight: activeFile === file ? 700 : 400,
              cursor: 'pointer',
            }}
          >
            {file.replace('.json', '')}
          </button>
        ))}
      </div>
      {/* JSON content */}
      <div style={{ flex: 1, overflowY: 'auto', padding: 12 }}>
        {activeFile && tokens[activeFile] && (
          <pre style={{
            fontSize: 10, fontFamily: 'monospace', color: 'var(--accent2)',
            whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            margin: 0, lineHeight: 1.6,
          }}>
            {JSON.stringify(tokens[activeFile], null, 2)}
          </pre>
        )}
      </div>
    </div>
  )
}

function InlineHistoryTab({ componentId, api, onReloadFull }) {
  const [versions, setVersions]   = useState([])
  const [loading, setLoading]     = useState(true)
  const [expanded, setExpanded]   = useState({})
  const [restoring, setRestoring] = useState(null)

  useEffect(() => {
    fetch(`${api}/designer/library/history/${componentId}`)
      .then(r => r.json())
      .then(d => setVersions((d.versions || []).slice().reverse()))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [componentId, api])

  return (
    <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {loading && (
        <p className="text-caption" style={{ color: 'var(--accent)', fontStyle: 'italic' }}>Loading…</p>
      )}
      {!loading && versions.length === 0 && (
        <p className="text-caption" style={{ color: 'var(--accent3)', fontStyle: 'italic', padding: 8 }}>
          No version history yet. Feedback changes will appear here.
        </p>
      )}
      {versions.map((v, i) => {
        const isOpen = expanded[v.version_id]
        return (
          <div key={v.version_id} style={{
            border: '1px solid var(--accent3)', borderRadius: 'var(--card-radius)',
            overflow: 'hidden',
          }}>
            <div
              onClick={() => setExpanded(p => ({ ...p, [v.version_id]: !p[v.version_id] }))}
              style={{
                padding: '8px 12px',
                background: i === 0 ? 'var(--positive-container)' : (i % 2 === 0 ? 'var(--white)' : 'var(--bg-salt)'),
                display: 'flex', alignItems: 'center', gap: 8,
                cursor: 'pointer',
              }}
            >
              {i === 0 && (
                <span style={{
                  fontSize: 9, fontWeight: 700, padding: '1px 5px',
                  background: 'var(--positive)', color: 'var(--white)', borderRadius: '50vh',
                }}>LATEST</span>
              )}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--dark-sky)' }}>
                  {v.summary}
                </div>
                <div style={{ fontSize: 10, color: 'var(--accent)', marginTop: 1 }}>
                  {v.timestamp ? new Date(v.timestamp).toLocaleString() : ''} · v{v.version_id?.slice(-6)}
                </div>
              </div>
              <span style={{ fontSize: 10, color: 'var(--accent3)' }}>{isOpen ? '▲' : '▼'}</span>
            </div>
            {isOpen && (
              <div style={{ padding: '8px 12px', borderTop: '1px solid var(--accent3)', background: 'var(--white)' }}>
                <div className="text-caption" style={{ color: 'var(--accent)', marginBottom: 4, fontWeight: 600 }}>
                  Rules snapshot
                </div>
                <pre style={{
                  fontSize: 10, fontFamily: 'monospace', color: 'var(--accent2)',
                  background: 'var(--bg-salt)', border: '1px solid var(--accent3)',
                  borderRadius: 4, padding: '6px 8px',
                  whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                  margin: 0, maxHeight: 180, overflowY: 'auto', lineHeight: 1.5,
                }}>
                  {JSON.stringify(v.rules_snapshot, null, 2)}
                </pre>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

/* ── Sub-tab label constants ─────────────────────────────────────────────── */
const WORKBENCH_TABS = [
  { id: 'preview',  label: 'Preview',  icon: '◉' },
  { id: 'rules',    label: 'Rules',    icon: '⚙' },
  { id: 'tokens',   label: 'Tokens',   icon: '🎨' },
  { id: 'history',  label: 'History',  icon: '📋' },
  { id: 'feedback', label: 'Feedback', icon: '💬' },
]

const ALGO_LAYOUT_MODES = [
  { id: 'vertical',              label: 'Vertical'  },
  { id: 'tree',                  label: 'Tree'      },
  { id: 'grouped_branch_layout', label: 'Grouped'   },
  { id: 'image_guided',          label: 'Guided'    },
]

/* ═══════════════════════════════════════════════════════════════════════════
   DesignSystemLibrary — polished workbench with sub-tabs and tighter rail
   ═══════════════════════════════════════════════════════════════════════════ */
function DesignSystemLibrary({ api }) {
  /* ── State ─────────────────────────────────────────────────────────────── */
  const [components, setComponents]     = useState([])
  const [loading,    setLoading]        = useState(false)
  const [selected,   setSelected]       = useState(null)   // summary object
  const [fullComp,   setFullComp]       = useState(null)   // full record
  const [loadingComp, setLoadingComp]   = useState(false)
  const [filterStatus, setFilterStatus] = useState('all')
  const [searchText,   setSearchText]   = useState('')
  const [workTab,      setWorkTab]      = useState('preview') // preview|rules|tokens|history|feedback
  const [algoMode,     setAlgoMode]     = useState(null)     // local layout override for algorithm_flow
  const [showTokensOverlay, setShowTokensOverlay] = useState(false)  // tokens panel from footer

  const [approving, setApproving]       = useState(false)
  const [resetting, setResetting]       = useState(false)
  const [actionMsg, setActionMsg]       = useState(null)

  /* ── Load library ──────────────────────────────────────────────────────── */
  const loadLibrary = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${api}/designer/library`)
      const d = await r.json()
      setComponents(d.components || [])
    } catch {
      setActionMsg({ type: 'error', text: 'Could not load library.' })
    } finally {
      setLoading(false)
    }
  }, [api])

  useEffect(() => { loadLibrary() }, [loadLibrary])

  /* ── Select component ──────────────────────────────────────────────────── */
  const selectComponent = useCallback(async (comp) => {
    setSelected(comp)
    setFullComp(null)
    setLoadingComp(true)
    setActionMsg(null)
    setWorkTab('preview')
    setAlgoMode(null)
    try {
      const r = await fetch(`${api}/designer/library/${comp.id}`)
      const d = await r.json()
      setFullComp(d.component)
    } catch {
      setActionMsg({ type: 'error', text: `Could not load component ${comp.id}` })
    } finally {
      setLoadingComp(false)
    }
  }, [api])

  /* ── Reload (after feedback) ──────────────────────────────────────────── */
  const reloadFull = useCallback(async () => {
    if (!selected) return
    try {
      const r = await fetch(`${api}/designer/library/${selected.id}`)
      const d = await r.json()
      setFullComp(d.component)
      setComponents(cs => cs.map(c => c.id === selected.id ? { ...c, ...d.component } : c))
    } catch {}
  }, [api, selected])

  /* ── Approve ────────────────────────────────────────────────────────────── */
  const approveComponent = async () => {
    if (!selected) return
    setApproving(true)
    try {
      const r = await fetch(`${api}/designer/library/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ component_id: selected.id }),
      })
      if (!r.ok) throw new Error('Approve failed')
      setActionMsg({ type: 'success', text: `✓ ${selected.name} approved.` })
      await loadLibrary(); await reloadFull()
    } catch (e) {
      setActionMsg({ type: 'error', text: e.message })
    } finally {
      setApproving(false)
    }
  }

  /* ── Reset to base ─────────────────────────────────────────────────────── */
  const resetComponent = async () => {
    if (!selected || selected.status === 'system') return
    if (!window.confirm('Reset this component to its initial version?')) return
    setResetting(true)
    try {
      const r = await fetch(`${api}/designer/library/reset/${selected.id}`, { method: 'POST' })
      if (!r.ok) throw new Error('Reset failed')
      setActionMsg({ type: 'success', text: `↩ ${selected.name} reset to base.` })
      await reloadFull()
    } catch (e) {
      setActionMsg({ type: 'error', text: e.message })
    } finally {
      setResetting(false)
    }
  }

  /* ── Filtered + searched list ───────────────────────────────────────────── */
  const filtered = components
    .filter(c => filterStatus === 'all' || c.status === filterStatus)
    .filter(c => !searchText.trim() ||
      c.name.toLowerCase().includes(searchText.toLowerCase()) ||
      c.type.toLowerCase().includes(searchText.toLowerCase())
    )

  const counts = {
    all:      components.length,
    system:   components.filter(c => c.status === 'system').length,
    approved: components.filter(c => c.status === 'approved').length,
    draft:    components.filter(c => c.status === 'draft').length,
  }

  /* ── Preview component: merge local algo layout mode override ───────────── */
  const previewComp = fullComp && algoMode
    ? { ...fullComp, preview_props: { ...fullComp.preview_props, layout_mode: algoMode } }
    : fullComp

  /* ── Render ─────────────────────────────────────────────────────────────── */
  return (
    <div style={{ display: 'flex', height: '100%', position: 'relative' }}>

      {/* ══ Left rail: component browser ══════════════════════════════════ */}
      <div style={{
        width: 208, flexShrink: 0,
        borderRight: '1px solid var(--accent3)',
        display: 'flex', flexDirection: 'column',
        background: 'var(--bg-salt)', overflow: 'hidden',
      }}>
        {/* Rail header */}
        <div style={{
          padding: '10px 12px 8px',
          borderBottom: '1px solid var(--accent3)',
          flexShrink: 0, background: 'var(--white)',
        }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--dark-sky)', marginBottom: 7 }}>
            Components
            <span style={{ fontWeight: 400, color: 'var(--accent)', marginLeft: 5 }}>
              ({filtered.length}{filterStatus !== 'all' ? `/${counts.all}` : ''})
            </span>
          </div>

          {/* Search */}
          <input
            type="search"
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
            placeholder="Search…"
            style={{
              width: '100%', boxSizing: 'border-box',
              padding: '4px 8px', fontSize: 11,
              border: '1px solid var(--accent3)', borderRadius: 4,
              background: 'var(--bg-salt)', color: 'var(--dark-sky)',
              fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
              marginBottom: 6, outline: 'none',
            }}
          />

          {/* Filter row */}
          <div style={{ display: 'flex', gap: 3 }}>
            {['all', 'system', 'approved', 'draft'].map(f => (
              <button
                key={f}
                onClick={() => setFilterStatus(f)}
                style={{
                  flex: 1, padding: '2px 0', fontSize: 9, fontWeight: filterStatus === f ? 700 : 400,
                  background: filterStatus === f ? 'var(--primary)' : 'transparent',
                  color: filterStatus === f ? 'var(--white)' : 'var(--accent2)',
                  border: `1px solid ${filterStatus === f ? 'var(--primary)' : 'var(--accent3)'}`,
                  borderRadius: '50vh', cursor: 'pointer', textAlign: 'center',
                  fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
                }}
                title={`${f} (${counts[f]})`}
              >
                {f === 'all' ? `All ${counts.all}` : `${f.slice(0,3)} ${counts[f]}`}
              </button>
            ))}
          </div>
        </div>

        {/* Scrollable component list */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '6px 6px' }}>
          {loading && (
            <p style={{ fontSize: 11, color: 'var(--accent)', fontStyle: 'italic', padding: 8 }}>Loading…</p>
          )}
          {!loading && filtered.length === 0 && (
            <p style={{ fontSize: 11, color: 'var(--accent3)', fontStyle: 'italic', padding: 8 }}>
              {searchText ? 'No matches.' : 'No components.'}
            </p>
          )}
          {filtered.map(comp => {
            const isSelected = selected?.id === comp.id
            const ss = STATUS_STYLE[comp.status] || STATUS_STYLE.draft
            return (
              <div
                key={comp.id}
                onClick={() => selectComponent(comp)}
                style={{
                  padding: '6px 8px', marginBottom: 2,
                  background: isSelected ? 'var(--info-container, #E3EEF5)' : 'var(--white)',
                  border: `1px solid ${isSelected ? 'var(--lab-blue)' : 'var(--accent3)'}`,
                  borderLeft: isSelected ? '3px solid var(--primary)' : '1px solid var(--accent3)',
                  borderRadius: 'var(--card-radius)',
                  cursor: 'pointer', transition: 'border-color .15s, background .15s',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 2 }}>
                  <div style={{
                    flex: 1, fontSize: 11, fontWeight: isSelected ? 700 : 600,
                    color: isSelected ? 'var(--primary)' : 'var(--dark-sky)',
                    lineHeight: 1.2, minWidth: 0,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {comp.name}
                  </div>
                  <span style={{
                    fontSize: 8, padding: '1px 4px',
                    background: ss.bg, color: ss.color,
                    borderRadius: '50vh', fontWeight: 700, flexShrink: 0,
                  }}>
                    {ss.label}
                  </span>
                </div>
                <span className={TYPE_BADGE[comp.type] || 'badge badge-source-general'} style={{ fontSize: 8 }}>
                  {comp.type.replace(/_/g, ' ')}
                </span>
              </div>
            )
          })}
        </div>

        {/* Footer: Design Tokens shortcut */}
        <div style={{ padding: '7px 8px', borderTop: '1px solid var(--accent3)', flexShrink: 0 }}>
          <button
            onClick={() => setShowTokensOverlay(true)}
            style={{
              width: '100%', padding: '5px 8px',
              background: 'none', border: '1px solid var(--accent3)',
              borderRadius: 'var(--card-radius)', cursor: 'pointer',
              fontSize: 10, color: 'var(--accent2)',
              fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4,
            }}
          >
            🎨 Design Tokens
          </button>
        </div>
      </div>

      {/* ══ Right work area ═══════════════════════════════════════════════ */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>

        {/* Empty state */}
        {!selected && (
          <div style={{
            flex: 1, display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center', padding: 32,
            color: 'var(--accent3)', textAlign: 'center',
          }}>
            <div style={{ fontSize: 40, marginBottom: 14, opacity: 0.4 }}>⊞</div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--accent)', marginBottom: 6 }}>
              Select a component
            </div>
            <div style={{ fontSize: 12, lineHeight: 1.6, maxWidth: 220 }}>
              Choose from the library to see a live preview, inspect design rules, and iterate with AI feedback.
            </div>
          </div>
        )}

        {selected && (
          <>
            {/* ── Component header ─────────────────────────────────────── */}
            <div style={{
              padding: '12px 16px 10px',
              borderBottom: '1px solid var(--accent3)',
              background: 'var(--white)', flexShrink: 0,
            }}>
              {/* Row 1: name + pills + actions */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontSize: 15, fontWeight: 700, color: 'var(--dark-sky)',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {selected.name}
                  </div>
                </div>
                {/* Type pill */}
                <span className={TYPE_BADGE[selected.type] || 'badge badge-source-general'} style={{ fontSize: 9, flexShrink: 0 }}>
                  {selected.type.replace(/_/g, ' ')}
                </span>
                {/* Status pill */}
                {(() => {
                  const ss = STATUS_STYLE[selected.status] || STATUS_STYLE.draft
                  return (
                    <span style={{
                      fontSize: 9, padding: '2px 8px',
                      background: ss.bg, color: ss.color,
                      borderRadius: '50vh', fontWeight: 700, flexShrink: 0,
                    }}>
                      {ss.label}
                    </span>
                  )
                })()}
              </div>

              {/* Row 2: description + tags */}
              {selected.description && (
                <div style={{ fontSize: 11, color: 'var(--accent)', marginBottom: 5, lineHeight: 1.4 }}>
                  {selected.description}
                </div>
              )}
              {(selected.tags || []).length > 0 && (
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
                  {selected.tags.map(t => (
                    <span key={t} style={{
                      fontSize: 9, padding: '1px 5px',
                      background: 'var(--bg-salt)', border: '1px solid var(--accent3)',
                      borderRadius: '50vh', color: 'var(--accent2)',
                    }}>#{t}</span>
                  ))}
                </div>
              )}

              {/* Row 3: action buttons */}
              <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', alignItems: 'center' }}>
                {fullComp?.status !== 'approved' && fullComp?.status !== 'system' && (
                  <button
                    onClick={approveComponent}
                    disabled={approving}
                    style={{
                      padding: '4px 12px', fontSize: 11, fontWeight: 600,
                      background: 'var(--positive)', color: 'var(--white)',
                      border: 'none', borderRadius: 'var(--btn-radius)',
                      cursor: approving ? 'wait' : 'pointer',
                      fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
                    }}
                  >
                    {approving ? 'Approving…' : '✓ Approve'}
                  </button>
                )}
                {fullComp?.status !== 'system' && (
                  <button
                    onClick={resetComponent}
                    disabled={resetting}
                    style={{
                      padding: '4px 10px', fontSize: 11,
                      background: 'none', color: 'var(--accent2)',
                      border: '1px solid var(--accent3)', borderRadius: 'var(--btn-radius)',
                      cursor: resetting ? 'wait' : 'pointer',
                      fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
                    }}
                  >
                    {resetting ? 'Resetting…' : '↩ Reset'}
                  </button>
                )}
              </div>

              {/* Action message */}
              {actionMsg && (
                <div style={{
                  marginTop: 7, padding: '4px 10px',
                  background: actionMsg.type === 'success' ? 'var(--positive-container)' : 'var(--danger-container)',
                  border: `1px solid ${actionMsg.type === 'success' ? 'var(--positive)' : 'var(--danger)'}`,
                  borderRadius: 4, fontSize: 11,
                  color: actionMsg.type === 'success' ? 'var(--positive)' : 'var(--danger)',
                }}>
                  {actionMsg.text}
                </div>
              )}
            </div>

            {/* ── Sub-tab strip ────────────────────────────────────────── */}
            <div style={{
              display: 'flex',
              borderBottom: '1px solid var(--accent3)',
              background: 'var(--bg-salt)', flexShrink: 0,
            }}>
              {WORKBENCH_TABS.map(tab => {
                const isActive = workTab === tab.id
                return (
                  <button
                    key={tab.id}
                    onClick={() => setWorkTab(tab.id)}
                    style={{
                      flex: 1, padding: '7px 4px',
                      background: 'none', border: 'none',
                      borderBottom: isActive ? '2px solid var(--primary)' : '2px solid transparent',
                      color: isActive ? 'var(--primary)' : 'var(--accent)',
                      fontWeight: isActive ? 700 : 400,
                      fontSize: 10, cursor: 'pointer',
                      display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 3,
                      fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
                      transition: 'color .15s',
                    }}
                  >
                    <span>{tab.icon}</span>
                    {tab.label}
                  </button>
                )
              })}
            </div>

            {/* ── Tab content area ─────────────────────────────────────── */}
            <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>

              {/* Loading shimmer */}
              {loadingComp && (
                <div style={{
                  flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
                  color: 'var(--accent)', fontSize: 12, fontStyle: 'italic',
                }}>
                  Loading component…
                </div>
              )}

              {/* ── PREVIEW TAB ── */}
              {!loadingComp && workTab === 'preview' && (
                <div style={{ flex: 1, overflowY: 'auto', padding: '16px' }}>
                  {/* Algorithm layout chips */}
                  {fullComp?.type === 'algorithm_flow' && (
                    <div style={{ marginBottom: 12 }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--accent)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                        Layout mode
                      </div>
                      <div style={{ display: 'flex', gap: 4 }}>
                        {ALGO_LAYOUT_MODES.map(m => {
                          const current = algoMode || fullComp?.preview_props?.layout_mode || 'vertical'
                          const isActive = m.id === current
                          return (
                            <button
                              key={m.id}
                              onClick={() => setAlgoMode(m.id)}
                              style={{
                                padding: '4px 10px', fontSize: 10,
                                background: isActive ? 'var(--secondary)' : 'var(--white)',
                                color: isActive ? 'var(--white)' : 'var(--accent2)',
                                border: `1px solid ${isActive ? 'var(--secondary)' : 'var(--accent3)'}`,
                                borderRadius: '50vh', cursor: 'pointer',
                                fontWeight: isActive ? 700 : 400,
                                fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
                              }}
                            >
                              {m.label}
                            </button>
                          )
                        })}
                      </div>
                    </div>
                  )}

                  {/* Live preview card */}
                  <div style={{
                    background: 'var(--bg-salt)',
                    border: '1px solid var(--accent3)',
                    borderRadius: 'var(--card-radius)',
                    overflow: 'hidden',
                  }}>
                    {/* Preview card header */}
                    <div style={{
                      padding: '7px 12px',
                      borderBottom: '1px solid var(--accent3)',
                      background: 'var(--white)',
                      display: 'flex', alignItems: 'center', gap: 6,
                    }}>
                      <span style={{ fontSize: 10, color: 'var(--accent3)' }}>◉</span>
                      <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--accent)', flex: 1 }}>
                        Live Preview
                      </span>
                      <span style={{ fontSize: 10, color: 'var(--accent3)' }}>
                        preview_props
                      </span>
                    </div>
                    {/* Preview canvas */}
                    <div style={{
                      padding: '20px 16px',
                      minHeight: 180,
                      background: 'var(--bg-salt)',
                    }}>
                      {previewComp
                        ? <ComponentPreview component={previewComp} />
                        : <div style={{ color: 'var(--accent3)', fontSize: 12, fontStyle: 'italic', textAlign: 'center', paddingTop: 32 }}>
                            Select a component to preview
                          </div>
                      }
                    </div>
                  </div>
                </div>
              )}

              {/* ── RULES TAB ── */}
              {!loadingComp && workTab === 'rules' && (
                <div style={{ flex: 1, overflowY: 'auto' }}>
                  <RulesTab component={fullComp} />
                </div>
              )}

              {/* ── TOKENS TAB ── */}
              {!loadingComp && workTab === 'tokens' && (
                <div style={{ flex: 1, overflow: 'hidden' }}>
                  <InlineTokensTab api={api} />
                </div>
              )}

              {/* ── HISTORY TAB ── */}
              {!loadingComp && workTab === 'history' && selected && (
                <div style={{ flex: 1, overflowY: 'auto' }}>
                  <InlineHistoryTab
                    componentId={selected.id}
                    api={api}
                    onReloadFull={reloadFull}
                  />
                </div>
              )}

              {/* ── FEEDBACK TAB ── */}
              {!loadingComp && workTab === 'feedback' && (
                <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                  {fullComp
                    ? <FeedbackChat
                        component={fullComp}
                        api={api}
                        onUpdated={async () => {
                          await reloadFull()
                          setActionMsg({ type: 'success', text: '✓ Changes applied — preview updated.' })
                          setTimeout(() => setActionMsg(null), 4000)
                        }}
                      />
                    : (
                      <div style={{ padding: 16, color: 'var(--accent3)', fontSize: 12, fontStyle: 'italic' }}>
                        Loading component…
                      </div>
                    )
                  }
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {/* ── Tokens overlay (from footer button) ─────────────────────────── */}
      {showTokensOverlay && (
        <TokensPanel api={api} onClose={() => setShowTokensOverlay(false)} />
      )}
    </div>
  )
}


/* ─────────────────────────────────────────────────────────────────────────────
   SchemaInspector — live tree view of the last ui_schema  (unchanged from v1)
───────────────────────────────────────────────────────────────────────────── */
function SchemaInspector({ sessionId, api }) {
  const [schema,   setSchema]   = useState(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState(null)
  const [expanded, setExpanded] = useState({})

  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewResult,  setPreviewResult]  = useState(null)
  const [previewInput,   setPreviewInput]   = useState({
    answer: 'Sample clinical answer.',
    confidence_score: 75,
    intent_type: 'test_selection',
    recommendations: [],
    citations: [],
    conflicts_surfaced: [],
    evidence_gaps: [],
  })

  const loadSchema = useCallback(async () => {
    if (!sessionId) return
    setLoading(true)
    setError(null)
    try {
      const r = await fetch(`${api}/designer/schema/${sessionId}`)
      if (!r.ok) throw new Error(r.status === 404 ? 'No schema yet — run a query first.' : await r.text())
      const d = await r.json()
      setSchema(d.ui_schema)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [sessionId, api])

  const runPreview = async () => {
    setPreviewLoading(true)
    setPreviewResult(null)
    try {
      const r = await fetch(`${api}/designer/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(previewInput),
      })
      const d = await r.json()
      setPreviewResult(d)
    } catch (e) {
      setPreviewResult({ error: e.message })
    } finally {
      setPreviewLoading(false)
    }
  }

  const toggleExpand = (key) =>
    setExpanded(prev => ({ ...prev, [key]: !prev[key] }))

  const displaySchema = previewResult?.ui_schema || schema

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, height: '100%' }}>
      <div style={{
        padding: '12px 16px',
        background: 'var(--bg-salt)',
        border: '1px solid var(--accent3)',
        borderRadius: 'var(--card-radius)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <span className="text-label" style={{ color: 'var(--accent)', flex: 1 }}>Session Schema</span>
          <button
            onClick={loadSchema}
            disabled={!sessionId || loading}
            className="btn btn-outlined"
            style={{ padding: '3px 12px', fontSize: 12 }}
          >
            {loading ? 'Loading…' : 'Load'}
          </button>
        </div>
        {!sessionId && (
          <p className="text-caption" style={{ color: 'var(--accent)', fontStyle: 'italic' }}>
            Start a chat session to inspect its schema.
          </p>
        )}
        {error && (
          <p className="text-caption" style={{ color: 'var(--danger)', marginTop: 4 }}>{error}</p>
        )}
      </div>

      <div style={{
        padding: '12px 16px',
        background: 'var(--white)',
        border: '1px solid var(--accent3)',
        borderRadius: 'var(--card-radius)',
      }}>
        <div className="text-label" style={{ color: 'var(--accent)', marginBottom: 10 }}>
          Synthetic Preview
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <label style={{ fontSize: 12, color: 'var(--accent2)' }}>
            Confidence score (0–100)
            <input
              type="number" min={0} max={100}
              value={previewInput.confidence_score}
              onChange={e => setPreviewInput(p => ({ ...p, confidence_score: Number(e.target.value) }))}
              style={{
                marginLeft: 8, width: 60,
                border: '1px solid var(--accent3)', borderRadius: 4,
                padding: '2px 6px', fontSize: 12,
              }}
            />
          </label>
          <label style={{ fontSize: 12, color: 'var(--accent2)' }}>
            Intent type
            <select
              value={previewInput.intent_type}
              onChange={e => setPreviewInput(p => ({ ...p, intent_type: e.target.value }))}
              style={{
                marginLeft: 8,
                border: '1px solid var(--accent3)', borderRadius: 4,
                padding: '2px 6px', fontSize: 12, background: 'var(--white)',
              }}
            >
              {['test_selection','educational','diagnostic_support','ambiguous'].map(v => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          </label>
          <label style={{ fontSize: 12, color: 'var(--accent2)' }}>
            Recommendations (JSON array)
            <textarea
              rows={2}
              placeholder='[{"test_name":"TSH","rank":"primary","specimen":"Serum"}]'
              onChange={e => {
                try {
                  const v = JSON.parse(e.target.value || '[]')
                  setPreviewInput(p => ({ ...p, recommendations: v }))
                } catch {}
              }}
              style={{
                display: 'block', width: '100%', marginTop: 4,
                border: '1px solid var(--accent3)', borderRadius: 4,
                padding: '4px 8px', fontSize: 11, fontFamily: 'monospace',
                resize: 'vertical',
              }}
            />
          </label>
          <button
            onClick={runPreview}
            disabled={previewLoading}
            className="btn btn-primary"
            style={{ alignSelf: 'flex-start', padding: '5px 16px', fontSize: 12 }}
          >
            {previewLoading ? 'Generating…' : 'Run Preview'}
          </button>
        </div>
      </div>

      {displaySchema && (
        <div style={{ flex: 1, overflowY: 'auto' }}>
          <SchemaTree schema={displaySchema} expanded={expanded} onToggle={toggleExpand} />
        </div>
      )}
    </div>
  )
}

function SchemaTree({ schema, expanded, onToggle }) {
  const renderHints = schema.render_hints || {}
  return (
    <div>
      <div style={{
        padding: '8px 12px',
        background: 'var(--secondary)',
        borderRadius: '6px 6px 0 0',
        display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
      }}>
        <span className="text-caption" style={{ color: 'rgba(255,255,255,0.85)', fontWeight: 600 }}>
          Layout: {schema.layout}
        </span>
        <span style={{ width: 1, height: 12, background: 'rgba(255,255,255,0.3)' }} />
        <span className="text-caption" style={{ color: 'rgba(255,255,255,0.7)' }}>
          {schema.component_count} components
        </span>
        {schema.preferences_applied && (
          <span style={{
            fontSize: 10, fontWeight: 700, color: 'rgba(255,255,255,0.9)',
            background: 'rgba(255,255,255,0.15)', border: '1px solid rgba(255,255,255,0.3)',
            borderRadius: '50vh', padding: '1px 7px',
          }}>
            ✓ Preferences applied
          </span>
        )}
      </div>
      <div style={{
        padding: '8px 12px',
        background: 'var(--bg-salt)',
        borderBottom: '1px solid var(--accent3)',
        display: 'flex', gap: 6, flexWrap: 'wrap',
      }}>
        {Object.entries(renderHints).map(([k, v]) => (
          <span key={k} style={{
            fontSize: 10, padding: '2px 7px',
            background: 'var(--white)', border: '1px solid var(--accent3)',
            borderRadius: '50vh', color: 'var(--accent2)',
          }}>
            {k.replace(/_/g, ' ')}: <strong>{String(v)}</strong>
          </span>
        ))}
      </div>
      <div style={{ border: '1px solid var(--accent3)', borderTop: 'none', borderRadius: '0 0 6px 6px', overflow: 'hidden' }}>
        {(schema.components || []).map((comp, i) => {
          const key = `${comp.type}-${i}`
          const isOpen = expanded[key]
          return (
            <div key={key} style={{ borderBottom: i < schema.components.length - 1 ? '1px solid var(--accent3)' : 'none' }}>
              <div
                onClick={() => onToggle(key)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '8px 12px',
                  background: i % 2 === 0 ? 'var(--white)' : 'var(--bg-salt)',
                  cursor: 'pointer', userSelect: 'none',
                }}
              >
                <span style={{
                  width: 20, height: 20, borderRadius: '50%',
                  background: 'var(--secondary)', color: 'var(--white)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 10, fontWeight: 700, flexShrink: 0,
                }}>
                  {comp.priority}
                </span>
                <span className={TYPE_BADGE[comp.type] || 'badge badge-source-general'} style={{ fontSize: 10 }}>
                  {comp.type}
                </span>
                <span className="text-caption" style={{ color: 'var(--accent2)', fontStyle: 'italic' }}>
                  {comp.variant}
                </span>
                <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--accent3)' }}>
                  {isOpen ? '▲' : '▼'}
                </span>
              </div>
              {isOpen && (
                <div style={{
                  padding: '8px 12px 12px 40px',
                  background: 'var(--white)',
                  borderTop: '1px solid var(--accent3)',
                }}>
                  <pre style={{
                    fontSize: 11, fontFamily: 'monospace', color: 'var(--accent2)',
                    whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                    margin: 0, lineHeight: 1.6,
                    maxHeight: 240, overflowY: 'auto',
                  }}>
                    {JSON.stringify(comp.props, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}


/* ─────────────────────────────────────────────────────────────────────────────
   PreferencesEditor — form editor for formatting_rules.json  (unchanged from v1)
───────────────────────────────────────────────────────────────────────────── */
function PreferencesEditor({ api }) {
  const [prefs,     setPrefs]     = useState(null)
  const [loading,   setLoading]   = useState(false)
  const [saving,    setSaving]    = useState(false)
  const [message,   setMessage]   = useState(null)
  const [rawMode,   setRawMode]   = useState(false)
  const [rawJson,   setRawJson]   = useState('')
  const [jsonError, setJsonError] = useState(null)

  const loadPrefs = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${api}/designer/preferences`)
      const d = await r.json()
      setPrefs(d.preferences)
      setRawJson(JSON.stringify(d.preferences, null, 2))
    } catch (e) {
      setMessage({ type: 'error', text: `Load failed: ${e.message}` })
    } finally {
      setLoading(false)
    }
  }, [api])

  useEffect(() => { loadPrefs() }, [loadPrefs])

  const savePrefs = async (payload) => {
    setSaving(true)
    setMessage(null)
    try {
      const r = await fetch(`${api}/designer/preferences`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preferences: payload, updated_by: 'designer-panel' }),
      })
      const d = await r.json()
      if (d.success) {
        setMessage({ type: 'success', text: `Saved & hot-reloaded at ${new Date().toLocaleTimeString()}` })
        setPrefs(payload)
        setRawJson(JSON.stringify(payload, null, 2))
      } else {
        throw new Error(d.detail || 'Unknown error')
      }
    } catch (e) {
      setMessage({ type: 'error', text: `Save failed: ${e.message}` })
    } finally {
      setSaving(false)
    }
  }

  const handleRawSave = () => {
    try {
      const parsed = JSON.parse(rawJson)
      setJsonError(null)
      savePrefs(parsed)
    } catch (e) {
      setJsonError(`Invalid JSON: ${e.message}`)
    }
  }

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200 }}>
        <span className="text-body-sm" style={{ color: 'var(--accent)' }}>Loading preferences…</span>
      </div>
    )
  }

  if (!prefs) return null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="text-label" style={{ color: 'var(--accent)', flex: 1 }}>
          formatting_rules.json
        </span>
        <button
          onClick={() => setRawMode(m => !m)}
          className="btn btn-outlined"
          style={{ padding: '3px 10px', fontSize: 11 }}
        >
          {rawMode ? 'Form view' : 'Raw JSON'}
        </button>
      </div>

      {message && (
        <div style={{
          padding: '8px 12px',
          borderRadius: 'var(--card-radius)',
          background: message.type === 'success' ? 'var(--positive-container)' : 'var(--danger-container)',
          border: `1px solid ${message.type === 'success' ? 'var(--positive)' : 'var(--danger)'}`,
          fontSize: 12, color: message.type === 'success' ? 'var(--positive)' : 'var(--danger)',
          fontWeight: 600,
        }}>
          {message.type === 'success' ? '✓ ' : '✖ '}{message.text}
        </div>
      )}

      {rawMode ? (
        <div>
          <textarea
            value={rawJson}
            onChange={e => setRawJson(e.target.value)}
            rows={20}
            style={{
              display: 'block', width: '100%',
              border: `1px solid ${jsonError ? 'var(--danger)' : 'var(--accent3)'}`,
              borderRadius: 4, padding: '6px 8px',
              fontSize: 11, fontFamily: 'monospace', lineHeight: 1.5, resize: 'vertical',
            }}
          />
          {jsonError && (
            <p className="text-caption" style={{ color: 'var(--danger)', marginTop: 4 }}>{jsonError}</p>
          )}
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <button onClick={handleRawSave} disabled={saving} className="btn btn-primary" style={{ fontSize: 12 }}>
              {saving ? 'Saving…' : 'Save JSON'}
            </button>
            <button onClick={loadPrefs} className="btn btn-outlined" style={{ fontSize: 12 }}>
              Reload
            </button>
          </div>
        </div>
      ) : (
        <PrefsForm prefs={prefs} onSave={savePrefs} saving={saving} />
      )}
    </div>
  )
}

function PrefsForm({ prefs, onSave, saving }) {
  const [local, setLocal] = useState(() => JSON.parse(JSON.stringify(prefs)))

  const setNestedValue = (path, value) => {
    setLocal(prev => {
      const next = JSON.parse(JSON.stringify(prev))
      const keys = path.split('.')
      let cur = next
      for (let i = 0; i < keys.length - 1; i++) {
        cur[keys[i]] = cur[keys[i]] || {}
        cur = cur[keys[i]]
      }
      cur[keys[keys.length - 1]] = value
      return next
    })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Section title="Layout">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <label style={{ fontSize: 12, color: 'var(--accent2)' }}>
            Max components
            <input
              type="number" min={1} max={20}
              value={local.max_components ?? 8}
              onChange={e => setNestedValue('max_components', Number(e.target.value))}
              style={{
                marginLeft: 8, width: 60,
                border: '1px solid var(--accent3)', borderRadius: 4,
                padding: '2px 6px', fontSize: 12,
              }}
            />
          </label>
          <label style={{ fontSize: 12, color: 'var(--accent2)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <input
              type="checkbox"
              checked={local.prefer_table ?? false}
              onChange={e => setNestedValue('prefer_table', e.target.checked)}
            />
            Prefer table over cards for ≥3 recommendations
          </label>
        </div>
      </Section>

      <Section title="Table Rules">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <label style={{ fontSize: 12, color: 'var(--accent2)' }}>
            Use table when recommendations ≥
            <input
              type="number" min={1} max={10}
              value={local.table_rules?.min_recommendations_for_table ?? 3}
              onChange={e => setNestedValue('table_rules.min_recommendations_for_table', Number(e.target.value))}
              style={{
                marginLeft: 8, width: 50,
                border: '1px solid var(--accent3)', borderRadius: 4,
                padding: '2px 6px', fontSize: 12,
              }}
            />
          </label>
          <label style={{ fontSize: 12, color: 'var(--accent2)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <input
              type="checkbox"
              checked={local.table_rules?.zebra_stripe ?? true}
              onChange={e => setNestedValue('table_rules.zebra_stripe', e.target.checked)}
            />
            Zebra striping
          </label>
        </div>
      </Section>

      <Section title="Confidence Thresholds">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {['high', 'moderate', 'low'].map(tier => (
            <label key={tier} style={{ fontSize: 12, color: 'var(--accent2)' }}>
              {tier.charAt(0).toUpperCase() + tier.slice(1)} ≥
              <input
                type="number" min={0} max={100}
                value={local.confidence_thresholds?.[tier] ?? (tier === 'high' ? 70 : tier === 'moderate' ? 40 : 0)}
                onChange={e => setNestedValue(`confidence_thresholds.${tier}`, Number(e.target.value))}
                style={{
                  marginLeft: 8, width: 55,
                  border: '1px solid var(--accent3)', borderRadius: 4,
                  padding: '2px 6px', fontSize: 12,
                }}
              />
            </label>
          ))}
        </div>
      </Section>

      <Section title="Designer Notes">
        <textarea
          value={local.designer_notes || ''}
          onChange={e => setNestedValue('designer_notes', e.target.value)}
          rows={3}
          placeholder="Notes for the team…"
          style={{
            width: '100%', fontSize: 12, fontFamily: 'inherit',
            border: '1px solid var(--accent3)', borderRadius: 4,
            padding: '6px 8px', lineHeight: 1.5, resize: 'vertical',
          }}
        />
      </Section>

      <button
        onClick={() => onSave(local)}
        disabled={saving}
        className="btn btn-primary"
        style={{ marginTop: 4, width: '100%' }}
      >
        {saving ? 'Saving & hot-reloading…' : 'Save preferences'}
      </button>
    </div>
  )
}

function Section({ title, children }) {
  const [open, setOpen] = useState(true)
  return (
    <div style={{ marginBottom: 4 }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', width: '100%',
          padding: '6px 0', background: 'none', border: 'none',
          borderBottom: '1px solid var(--accent3)', cursor: 'pointer',
          gap: 6, marginBottom: open ? 10 : 6,
        }}
      >
        <span className="text-label" style={{ color: 'var(--accent)', flex: 1, textAlign: 'left' }}>
          {title}
        </span>
        <span style={{ fontSize: 10, color: 'var(--accent3)' }}>{open ? '▲' : '▼'}</span>
      </button>
      {open && children}
    </div>
  )
}

function FieldRow({ label, value, onChange, placeholder }) {
  return (
    <div>
      <label className="text-caption" style={{ color: 'var(--accent2)', fontWeight: 600 }}>{label}</label>
      <input
        type="text" value={value} placeholder={placeholder}
        onChange={e => onChange(e.target.value)}
        style={{
          display: 'block', width: '100%', marginTop: 4,
          border: '1px solid var(--accent3)', borderRadius: 4,
          padding: '4px 8px', fontSize: 12,
        }}
      />
    </div>
  )
}


/* ─────────────────────────────────────────────────────────────────────────────
   DesignerPanel — main export
   Renders as a right-side drawer.
   Width: 480px (schema/preferences) · 720px (library)
───────────────────────────────────────────────────────────────────────────── */
export default function DesignerPanel({ open, onClose, sessionId, api }) {
  const [activeTab, setActiveTab] = useState('schema')

  const panelWidth = activeTab === 'library' ? 720 : 480

  return (
    <>
      {/* Backdrop */}
      {open && (
        <div
          onClick={onClose}
          style={{
            position: 'fixed', inset: 0,
            background: 'rgba(0,0,0,0.15)',
            zIndex: 49,
          }}
        />
      )}

      {/* Drawer */}
      <aside style={{
        position: 'fixed', top: 0, right: 0, bottom: 0,
        width: panelWidth,
        transform: open ? 'translateX(0)' : 'translateX(100%)',
        transition: 'transform .25s cubic-bezier(.4,0,.2,1), width .2s ease',
        background: 'var(--white)',
        borderLeft: '1px solid var(--accent)',
        boxShadow: open ? '-4px 0 24px rgba(0,0,0,.12)' : 'none',
        zIndex: 50,
        display: 'flex',
        flexDirection: 'column',
        fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
      }}>

        {/* Panel header */}
        <div style={{
          background: 'var(--secondary)',
          padding: '14px 20px',
          display: 'flex', alignItems: 'center', gap: 10,
          flexShrink: 0,
        }}>
          <span style={{ fontSize: 20 }}>⬡</span>
          <div style={{ flex: 1 }}>
            <div style={{ color: 'var(--white)', fontSize: 14, fontWeight: 700, lineHeight: 1.2 }}>
              {activeTab === 'library' ? 'Design System Library' : 'Designer Panel'}
            </div>
            <div style={{ color: 'rgba(255,255,255,0.65)', fontSize: 11, marginTop: 1 }}>
              {activeTab === 'library'
                ? 'Component Workbench'
                : 'UX training & schema inspection'}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              color: 'rgba(255,255,255,0.7)', background: 'none',
              fontSize: 20, lineHeight: 1, padding: '2px 4px',
              borderRadius: 4, cursor: 'pointer',
            }}
            aria-label="Close designer panel"
          >
            ×
          </button>
        </div>

        {/* Tab strip */}
        <div style={{
          display: 'flex',
          borderBottom: '1px solid var(--accent3)',
          flexShrink: 0,
          background: 'var(--bg-salt)',
        }}>
          {TABS.map(tab => {
            const isActive = activeTab === tab.id
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                style={{
                  flex: 1, padding: '10px 6px',
                  background: 'none',
                  borderBottom: isActive
                    ? '2px solid var(--primary)'
                    : '2px solid transparent',
                  color: isActive ? 'var(--primary)' : 'var(--accent)',
                  fontWeight: isActive ? 700 : 400,
                  fontSize: 12, cursor: 'pointer',
                  transition: 'color .15s',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5,
                  fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
                }}
              >
                <span style={{ fontSize: 14 }}>{tab.icon}</span>
                {tab.label}
              </button>
            )
          })}
        </div>

        {/* Tab body
            Library tab: no padding, fill height, two-pane layout
            Other tabs:  20px padding, scrollable             */}
        <div style={{
          flex: 1,
          overflow: activeTab === 'library' ? 'hidden' : 'auto',
          padding: activeTab === 'library' ? 0 : '20px',
        }}>
          {activeTab === 'schema'      && <SchemaInspector sessionId={sessionId} api={api} />}
          {activeTab === 'preferences' && <PreferencesEditor api={api} />}
          {activeTab === 'library'     && <DesignSystemLibrary api={api} />}
        </div>

        {/* Panel footer */}
        <div style={{
          padding: '10px 20px',
          borderTop: '1px solid var(--accent3)',
          display: 'flex', alignItems: 'center', gap: 8,
          flexShrink: 0,
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%',
            background: 'var(--positive)', display: 'inline-block',
          }} />
          <span className="text-caption" style={{ color: 'var(--accent)' }}>
            {activeTab === 'library'
              ? 'Library changes persist to design_system_library.json'
              : 'Changes hot-reload instantly — no restart required'}
          </span>
        </div>
      </aside>
    </>
  )
}