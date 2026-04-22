import { useState, useEffect, useRef, Fragment } from 'react'
import ArupDocumentFidelityRenderer from './ArupDocumentFidelityRenderer'
import { jsPDF } from 'jspdf'

/* ═══════════════════════════════════════════════════════════════════════════
   MessageBubble.jsx  v2.0.0
   ───────────────────────────────────────────────────────────────────────────
   What's new in v2.0.0 — Schema-driven rendering:
     • UISchemaRenderer: consumes d.ui_schema from FormattingAgent (backend)
     • Dispatches each schema component to the correct React sub-component
       by type + variant, respecting clinical priority ordering
     • New RecommendationsTable: rich paginated multi-test comparison table
       with expandable rationale rows and evidence coverage badges
     • New TextBlockRenderer: handles text_block answer_narrative + fallback
     • New BadgeGroupBlock: renders evidence coverage signal pill rows
     • All rendering adapts schema props → existing component interfaces
     • Full backward-compat fallback: uses legacy rendering when ui_schema absent
     • Confidence bar, follow-up questions, and disclaimer always rendered
       outside the schema (they are not schema-managed components)

   Component type dispatch table (schema type → React component):
     text_block          → TextBlockRenderer
     recommendation_card → RecommendationCard (via schemaPropsToRec adapter)
     table               → RecommendationsTable
     algorithm_flow      → AlgorithmFlowchart
     badge_group         → BadgeGroupBlock
     warning_block       → ConflictsBlock
     info_block          → EvidenceGapsBlock
     citation_table      → CitationsTable (via row adapter)

   Rendering mode priority:
     1. hasClarification → ClarificationBlock (bypasses schema entirely)
     2. d.ui_schema      → UISchemaRenderer (schema-driven)
     3. fallback         → legacy hardcoded render (backward compat)
   ═══════════════════════════════════════════════════════════════════════════ */

/* ── Clinical colour tokens (thyroid UX layout spec) ─────────────────── */
// These are used exclusively inside the algorithm renderer to stay consistent
// with the published ARUP clinical document spec.  All other UI continues to
// use CSS design-system tokens.
const CL = {
  granite:    '#77787B',  // connector lines, col arrows, granite text
  badlands:   '#DEDFE0',  // entry arrows, node borders, separators
  darkSky:    '#171717',  // primary text
  granite2:   '#414042',  // secondary text
  graniteM:   '#77787B',  // muted text / labels
  nodeNeutral:'#F9F7F6',  // node surface for result / finding / outcome / terminal / decision
  primary:    '#AE132A',  // primary red (critical borders, decision dot)
  secondary:  '#6D0020',  // start node bg, Zone C accent
  labBlue:    '#306385',  // cross-algorithm links
  aspen:      '#C6B1A1',  // Zone A accent, result/finding border
  white:      '#FFFFFF',
}

/* Keyword tier → colour mapping (ORDER = primary, CONSIDER = secondary, etc.) */
const KW_COLOR = {
  primary:   CL.secondary,  // #6D0020
  secondary: CL.granite,    // #77787B
  critical:  CL.primary,    // #AE132A
  ORDER:    CL.secondary,
  PERFORM:  CL.secondary,
  CONSIDER: CL.granite,
  REPEAT:   CL.granite,
  OBTAIN:   CL.granite,
  EVALUATE: CL.granite,
  PROCEED:  CL.granite,
  SELECT:   CL.granite,
  'ROUTE BY': CL.granite,
}

/* ── Design-system constants ───────────────────────────────────────────── */

/* Source type → badge config  (mirrors SOURCE_BADGE_CFG in formatting_agent) */
const SOURCE_CFG = {
  'Algorithm':      { cls: 'badge badge-source-algo',    label: 'Algorithm' },
  'Consult Topic':  { cls: 'badge badge-source-consult', label: 'Consult Topic' },
  'Fact Sheet':     { cls: 'badge badge-source-fact',    label: 'Fact Sheet' },
  'Test Directory': { cls: 'badge badge-source-dir',     label: 'Directory' },
  'General':        { cls: 'badge badge-source-general', label: 'General' },
}

/* Recommendation rank (mirrors RANK_CFG in formatting_agent) */
const RANK_CFG = {
  primary:   { accentColor: 'var(--primary)',  label: 'Primary',   badgeCls: 'badge-danger' },
  secondary: { accentColor: 'var(--accent)',   label: 'Secondary', badgeCls: 'badge-info' },
  reflex:    { accentColor: 'var(--positive)', label: 'Reflex',    badgeCls: 'badge-positive' },
}

/* ── Clarification resolver (v0.7.0 backward compat) ─────────────────── */
function resolveClarification(d) {
  if (d.clarification?.needed && d.clarification?.question) return d.clarification
  if (Array.isArray(d.clarification_questions) && d.clarification_questions.length > 0) {
    const first = d.clarification_questions[0]
    if (first?.question) return { needed: true, question: first.question, options: first.options || [] }
  }
  return null
}


/* ═══════════════════════════════════════════════════════════════════════════
   AlgorithmFlowchart — renders algorithm_visualization from the backend
   ═══════════════════════════════════════════════════════════════════════════ */

/* NODE_CFG removed in v2.0.0 — algorithm nodes now rendered by ClinicalNodeCard
   using the thyroid UX layout spec colour system (CL tokens above).
   The legacy NodeCard (tree/vertical) continues to work via ClinicalNodeCard. */

/* ── KeywordBadge — ARUP action keyword pill (used in nodes) ────────────── */
function KeywordBadge({ keyword, tier }) {
  if (!keyword) return null
  // tier: 'primary' | 'secondary' | 'critical'
  // fall back to keyword name if no tier
  const color = KW_COLOR[tier] || KW_COLOR[keyword] || CL.granite
  return (
    <span style={{
      display: 'inline-block', marginBottom: 5,
      fontSize: 9.5, fontWeight: tier === 'primary' || tier === 'critical' ? 700 : 600,
      letterSpacing: '0.04em', textTransform: 'uppercase',
      color, lineHeight: 1,
    }}>
      {keyword}
    </span>
  )
}

/* ── EntryFlowArrow — light arrow for entry section (badlands line) ──────── */
function EntryFlowArrow() {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      padding: '4px 0', width: '100%',
    }}>
      <div style={{ width: 1, height: 16, background: CL.badlands }} />
      <span style={{ fontSize: 11, color: CL.granite, lineHeight: 1 }}>▾</span>
    </div>
  )
}

/* ── ColArrow — in-column arrow (granite, 1.5px, 6px padding) ───────────── */
function ColArrow() {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      justifyContent: 'center', width: '100%',
      padding: '6px 0',
    }}>
      <div style={{ width: 1.5, height: 12, background: CL.granite }} />
      <div style={{
        width: 0, height: 0,
        borderLeft: '4px solid transparent',
        borderRight: '4px solid transparent',
        borderTop: `5px solid ${CL.granite}`,
      }} />
    </div>
  )
}

/* ── RoutingLabel — uppercase caption with flanking hairlines ─────────────── */
function RoutingLabel({ text }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      width: '100%', maxWidth: 520,
    }}>
      <div style={{ flex: 1, height: 1, background: CL.badlands }} />
      <span style={{
        fontSize: 9, fontWeight: 700, letterSpacing: '0.10em',
        textTransform: 'uppercase', color: CL.graniteM,
        whiteSpace: 'nowrap', userSelect: 'none',
      }}>
        {text}
      </span>
      <div style={{ flex: 1, height: 1, background: CL.badlands }} />
    </div>
  )
}

/* ── SharedActionCard — multi-row PERFORM/CONSIDER entry card ────────────── */
function SharedActionCard({ node }) {
  // Renders a single action node as a multi-row card.
  // In the entry section, adjacent action nodes are rendered individually
  // using this component with their keyword and label.
  const kw    = node.keyword
  const tier  = kw === 'ORDER' || kw === 'PERFORM' ? 'primary' : 'secondary'
  return (
    <div style={{
      background: CL.white, border: `1.5px solid ${CL.badlands}`,
      borderRadius: 8, width: '100%', overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex', alignItems: 'baseline', gap: 10,
        padding: '9px 16px',
      }}>
        {kw && (
          <span style={{
            fontSize: 9.5, fontWeight: tier === 'primary' ? 700 : 600,
            color: tier === 'primary' ? CL.secondary : CL.granite,
            textTransform: 'uppercase', letterSpacing: '0.04em', flexShrink: 0,
          }}>
            {kw}
          </span>
        )}
        <span style={{ fontSize: 11.5, color: CL.darkSky, lineHeight: 1.4 }}>
          {node.label}
        </span>
      </div>
      {node.description && (
        <div style={{
          borderTop: `1px solid ${CL.badlands}`,
          padding: '8px 16px',
          fontSize: 10.5, color: CL.graniteM, lineHeight: 1.5,
        }}>
          {node.description}
        </div>
      )}
    </div>
  )
}

/* ── ZoneHeader — zone column header per thyroid UX spec ────────────────── */
function ZoneHeader({ label, subtitle, accentHex }) {
  return (
    <div style={{
      width: '100%',
      background: CL.nodeNeutral,
      borderLeft: `3px solid ${accentHex}`,
      borderBottom: `1px solid ${CL.badlands}`,
      padding: '20px 18px 16px 14px',
      // No margin-bottom — spacing handled by ColArrow padding only
    }}>
      <div style={{
        fontSize: 13, fontWeight: 700, color: CL.darkSky,
        letterSpacing: '-0.1px', lineHeight: 1.25,
      }}>
        {label}
      </div>
      {subtitle && (
        <div style={{
          fontSize: 10.5, color: CL.graniteM,
          marginTop: 6, lineHeight: 1.55,
        }}>
          {subtitle}
        </div>
      )}
    </div>
  )
}

/* ── TJunctionFork — t-junction split within a zone column ──────────────── */
function TJunctionFork() {
  return (
    <div style={{ position: 'relative', width: '100%', height: 22 }}>
      {/* Incoming vertical drop from above */}
      <div style={{
        position: 'absolute', top: 0, left: '50%',
        transform: 'translateX(-50%)',
        width: 1.5, height: 11, background: CL.granite,
      }} />
      {/* Horizontal bar spanning left-center to right-center */}
      <div style={{
        position: 'absolute', top: 11,
        left: '25%', right: '25%',
        height: 1.5, background: CL.granite,
      }} />
      {/* Two vertical drops */}
      <div style={{
        position: 'absolute', top: 11, left: 0, right: 0,
        display: 'grid', gridTemplateColumns: '1fr 1fr',
      }}>
        {[0, 1].map(i => (
          <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <div style={{ width: 1.5, height: 6, background: CL.granite }} />
            <div style={{
              width: 0, height: 0,
              borderLeft: '4px solid transparent',
              borderRight: '4px solid transparent',
              borderTop: `5px solid ${CL.granite}`,
            }} />
          </div>
        ))}
      </div>
    </div>
  )
}

/* ── PathNodesGrid — side-by-side paths after a t-junction ──────────────── */
function PathNodesGrid({ paths, nodeMap }) {
  if (!paths || paths.length === 0) return null
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: `repeat(${Math.min(paths.length, 2)}, 1fr)`,
      gap: 8, width: '100%',
    }}>
      {paths.map(path => {
        const pathNodes = (path.node_ids || []).map(id => nodeMap[id]).filter(Boolean)
        return (
          <div key={path.path_id} style={{
            background: CL.white, border: `1.5px solid ${CL.badlands}`,
            borderRadius: 8, padding: '10px 12px',
            minHeight: 120, display: 'flex', flexDirection: 'column',
          }}>
            {/* Path label */}
            <div style={{
              fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
              textTransform: 'uppercase', color: CL.graniteM,
              marginBottom: 5,
            }}>
              {path.label}
            </div>
            {/* Condition */}
            {path.condition && (
              <div style={{
                fontSize: 10.5, fontStyle: 'italic', color: CL.graniteM,
                marginBottom: 5, paddingBottom: 5,
                borderBottom: `1px solid ${CL.badlands}`,
                lineHeight: 1.4,
              }}>
                {path.condition}
              </div>
            )}
            {/* Steps */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1 }}>
              {pathNodes.map((node, i) => (
                <div key={node.id} style={{
                  display: 'flex', alignItems: 'flex-start', gap: 5,
                  fontSize: 11, color: CL.darkSky, lineHeight: 1.35,
                }}>
                  <span style={{ color: CL.badlands, flexShrink: 0, fontWeight: 700 }}>→</span>
                  <div>
                    {node.keyword && (
                      <span style={{
                        fontSize: 9, fontWeight: 700,
                        color: KW_COLOR[node.keyword] || CL.granite,
                        textTransform: 'uppercase', marginRight: 4,
                      }}>
                        {node.keyword}
                      </span>
                    )}
                    {node.label}
                  </div>
                </div>
              ))}
              {pathNodes.length === 0 && path.condition && (
                <div style={{ fontSize: 10.5, color: CL.graniteM, fontStyle: 'italic' }}>
                  {path.condition}
                </div>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

/* ── ClinicalNodeCard — dispatches to the correct node archetype ─────────── */
function ClinicalNodeCard({ node }) {
  const v    = node.variant || 'actionNode'
  const kw   = node.keyword
  const emph = node.emphasis || 'secondary'

  /* ── startNode — dark maroon (#6D0020) indications pill ── */
  if (v === 'startNode') {
    return (
      <div style={{
        background: CL.secondary, color: CL.white,
        borderRadius: 6, padding: '14px 24px',
        width: '100%', maxWidth: 520, textAlign: 'center',
      }}>
        <div style={{
          fontSize: 9, fontWeight: 600, letterSpacing: '0.10em',
          textTransform: 'uppercase', color: 'rgba(255,255,255,0.60)',
          marginBottom: 6,
        }}>
          {(node.raw_label || '').toUpperCase().includes('INDICATION')
            ? 'INDICATIONS FOR TESTING'
            : 'START'}
        </div>
        <div style={{ fontSize: 12.5, fontWeight: 500, lineHeight: 1.45, color: CL.white }}>
          {node.label}
        </div>
      </div>
    )
  }

  /* ── resultNode — small auto-width pill, centered in column ── */
  if (v === 'resultNode') {
    return (
      <div style={{
        display: 'inline-block',
        background: CL.nodeNeutral, border: `1.5px solid ${CL.aspen}`,
        borderRadius: 20, padding: '5px 18px',
        fontSize: 11, fontWeight: 500, color: CL.darkSky,
        whiteSpace: 'nowrap', textAlign: 'center',
      }}>
        {node.label}
        {node.description && (
          <span style={{ fontSize: 9, color: CL.graniteM, marginLeft: 5 }}>
            {node.description}
          </span>
        )}
      </div>
    )
  }

  /* ── findingNode — full-width card with Aspen border, centered text ── */
  if (v === 'findingNode') {
    return (
      <div style={{
        background: CL.nodeNeutral, border: `1.5px solid ${CL.aspen}`,
        borderRadius: 8, padding: '9px 14px',
        width: '100%', textAlign: 'center',
      }}>
        <div style={{ fontSize: 12, fontWeight: 500, color: CL.granite2, lineHeight: 1.4 }}>
          {node.label}
        </div>
        {node.description && (
          <div style={{ fontSize: 10, color: CL.graniteM, marginTop: 3, lineHeight: 1.35, fontStyle: 'italic' }}>
            {node.description}
          </div>
        )}
      </div>
    )
  }

  /* ── decisionNode — neutral card, red dot indicator, uppercase label ── */
  if (v === 'decisionNode') {
    return (
      <div style={{
        background: CL.nodeNeutral, border: `1.5px solid ${CL.badlands}`,
        borderRadius: 8, padding: '9px 14px', width: '100%',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: node.label ? 4 : 0 }}>
          <div style={{ width: 6, height: 6, borderRadius: '50%', background: CL.primary, flexShrink: 0 }} />
          <div style={{
            fontSize: 9, fontWeight: 700, letterSpacing: '0.08em',
            textTransform: 'uppercase', color: CL.primary,
          }}>
            {node.label}
          </div>
        </div>
        {node.description && (
          <div style={{ fontSize: 12, fontWeight: 500, color: CL.darkSky, lineHeight: 1.4, marginTop: 2 }}>
            {node.description}
          </div>
        )}
      </div>
    )
  }

  /* ── terminalNode — muted neutral card, italic text ── */
  if (v === 'terminalNode') {
    return (
      <div style={{
        background: CL.nodeNeutral, border: `1.5px solid ${CL.badlands}`,
        borderRadius: 8, padding: '11px 14px', width: '100%',
      }}>
        <div style={{ fontSize: 10, fontWeight: 600, color: CL.graniteM, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 3 }}>
          Outcome — Terminal
        </div>
        <div style={{ fontSize: 11.5, fontStyle: 'italic', color: CL.graniteM, lineHeight: 1.4 }}>
          {node.label}
        </div>
      </div>
    )
  }

  /* ── outcomeNode — neutral card with context-aware eyebrow label ── */
  if (v === 'outcomeNode') {
    const sub = node.outcome_subtype || 'general'
    const eyebrow = sub === 'refer' ? 'Outcome — Refer'
      : sub === 'monitor' ? 'Outcome — Monitor'
      : 'Outcome'
    const isRefer = sub === 'refer'
    return (
      <div style={{
        background: CL.nodeNeutral, border: `1.5px solid ${CL.badlands}`,
        borderRadius: 8, padding: '11px 14px', width: '100%',
      }}>
        <div style={{
          fontSize: 9, fontWeight: 700, color: CL.graniteM,
          textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4,
        }}>
          {eyebrow}
        </div>
        <div style={{ fontSize: 12, fontWeight: 500, color: isRefer ? CL.labBlue : CL.darkSky, lineHeight: 1.4 }}>
          {node.label}{isRefer && ' ↗'}
        </div>
        {node.description && (
          <div style={{ fontSize: 10.5, fontStyle: 'italic', color: CL.graniteM, marginTop: 4, lineHeight: 1.35 }}>
            {node.description}
          </div>
        )}
      </div>
    )
  }

  /* ── criticalNode — white card with strong maroon border ── */
  if (v === 'criticalNode') {
    return (
      <div style={{
        background: CL.white, border: `2px solid ${CL.primary}`,
        borderRadius: 8, padding: '10px 14px', width: '100%',
      }}>
        <KeywordBadge keyword={kw} tier="critical" />
        <div style={{ fontSize: 12.5, fontWeight: 500, color: CL.darkSky, lineHeight: 1.4, marginTop: kw ? 3 : 0 }}>
          {node.label}
        </div>
        {node.description && (
          <div style={{ fontSize: 10.5, color: CL.graniteM, marginTop: 5, lineHeight: 1.4, fontStyle: 'italic' }}>
            {node.description}
          </div>
        )}
      </div>
    )
  }

  /* ── infoNode — muted supporting card ── */
  if (v === 'infoNode') {
    return (
      <div style={{
        background: CL.nodeNeutral, border: `1px solid ${CL.badlands}`,
        borderRadius: 6, padding: '8px 12px', width: '100%',
      }}>
        <div style={{ fontSize: 11, color: CL.granite2, lineHeight: 1.4 }}>
          {node.label}
        </div>
        {node.description && (
          <div style={{ fontSize: 10, color: CL.graniteM, marginTop: 3, lineHeight: 1.35, fontStyle: 'italic' }}>
            {node.description}
          </div>
        )}
      </div>
    )
  }

  /* ── actionNode — default white card with keyword ── */
  return (
    <div style={{
      background: CL.white, border: `1.5px solid ${CL.badlands}`,
      borderRadius: 8, padding: '10px 14px', width: '100%',
    }}>
      {kw && <KeywordBadge keyword={kw} tier={emph === 'critical' ? 'critical' : (kw === 'ORDER' || kw === 'PERFORM' ? 'primary' : 'secondary')} />}
      <div style={{
        fontSize: 12.5, fontWeight: 400, color: CL.darkSky,
        lineHeight: 1.4, marginTop: kw ? 3 : 0,
      }}>
        {node.label}
      </div>
      {node.description && (
        <div style={{ fontSize: 10.5, color: CL.graniteM, marginTop: 5, lineHeight: 1.4 }}>
          {node.description}
        </div>
      )}
    </div>
  )
}


/* ── NodeCard (vertical/tree layout) — kept for non-clinical-doc layouts ─── */
function NodeCard({ node, isHighlighted }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2, maxWidth: 300, width: '100%' }}>
      {node.branch && (
        <div style={{
          fontSize: 10, fontWeight: 700, letterSpacing: '0.04em',
          color: CL.granite, textTransform: 'uppercase',
          border: `1px solid ${CL.badlands}`,
          borderRadius: '50vh', padding: '1px 8px', marginBottom: 2,
        }}>
          {node.branch}
        </div>
      )}
      <div style={{ width: '100%' }}>
        <ClinicalNodeCard node={node} />
      </div>
      {node.has_children && (
        <ColArrow />
      )}
    </div>
  )
}


/* ═══════════════════════════════════════════════════════════════════════════
   ClinicalLinearDocumentLayout — centered single-column clinical document
   ─────────────────────────────────────────────────────────────────────────
   Used for: mostly linear algorithms with 0–1 late fork.

   Layout:
     • Centered spine, max 560px
     • Nodes in level order with ColArrow connectors
     • resultNode rendered auto-width centered
     • If branch_splits exist: shows a compact forked section below the spine
   ═══════════════════════════════════════════════════════════════════════════ */
function ClinicalLinearDocumentLayout({ nodes, branchSplits }) {
  const nodeMap = {}
  nodes.forEach(n => { nodeMap[n.id] = n })

  // Build a set of all node IDs that are inside a branch
  const branchNodeIds = new Set()
  ;(branchSplits || []).forEach(split => {
    split.branches.forEach(b => b.node_ids.forEach(id => branchNodeIds.add(id)))
  })

  // Spine: nodes NOT in any branch, sorted by level
  const spineNodes = nodes.filter(n => !branchNodeIds.has(n.id))

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      padding: '24px 24px 28px', width: '100%',
    }}>
      {/* ── Spine ── */}
      {spineNodes.map((node, i) => (
        <Fragment key={node.id}>
          {i > 0 && <ColArrow />}
          {node.variant === 'resultNode' ? (
            <div style={{ display: 'flex', justifyContent: 'center', width: '100%', maxWidth: 560 }}>
              <ClinicalNodeCard node={node} />
            </div>
          ) : (
            <div style={{ width: '100%', maxWidth: 560 }}>
              <ClinicalNodeCard node={node} />
            </div>
          )}
        </Fragment>
      ))}

      {/* ── Late fork section ── */}
      {(branchSplits || []).map(split => (
        <Fragment key={split.from_node_id}>
          <ColArrow />
          {/* Branch row — compact side-by-side boxes */}
          <div style={{
            display: 'flex', gap: 12, width: '100%', maxWidth: 560,
            alignItems: 'flex-start',
          }}>
            {split.branches.map(branch => {
              const branchNodes = branch.node_ids.map(id => nodeMap[id]).filter(Boolean)
              return (
                <div key={branch.to_node_id} style={{
                  flex: '1 1 0', minWidth: 0,
                  display: 'flex', flexDirection: 'column',
                  background: CL.nodeNeutral, borderRadius: 8,
                  border: `1px solid ${CL.badlands}`,
                  padding: '10px 12px',
                }}>
                  {branch.edge_label && (
                    <div style={{
                      fontSize: 8, fontWeight: 700, letterSpacing: '0.07em',
                      textTransform: 'uppercase', color: CL.graniteM,
                      marginBottom: 8, paddingBottom: 6,
                      borderBottom: `1px solid ${CL.badlands}`,
                    }}>
                      {branch.edge_label}
                    </div>
                  )}
                  {branchNodes.map((node, i) => (
                    <Fragment key={node.id}>
                      {i > 0 && <ColArrow />}
                      <div style={{ width: '100%' }}>
                        <ClinicalNodeCard node={node} />
                      </div>
                    </Fragment>
                  ))}
                </div>
              )
            })}
          </div>
        </Fragment>
      ))}
    </div>
  )
}


/* ═══════════════════════════════════════════════════════════════════════════
   ClinicalTreeDocumentLayout — centered spine with local branch splits
   ─────────────────────────────────────────────────────────────────────────
   Used for: algorithms with yes/no binary branches or local decision trees.

   Layout:
     • Centered main spine, max 640px
     • Spine rendered vertically with ColArrow connectors
     • At each branch split point: branches shown side-by-side beneath
       the decision node, with edge labels as branch headers
     • Each branch column uses the same clinical node card system
   ═══════════════════════════════════════════════════════════════════════════ */
function ClinicalTreeDocumentLayout({ nodes, edges, branchSplits }) {
  const nodeMap = {}
  nodes.forEach(n => { nodeMap[n.id] = n })

  // Build split lookup: from_node_id → split data
  const splitByFrom = {}
  ;(branchSplits || []).forEach(split => {
    splitByFrom[split.from_node_id] = split
  })

  // All branch-interior nodes (not rendered in the main spine pass)
  const branchInteriorIds = new Set()
  ;(branchSplits || []).forEach(split => {
    split.branches.forEach(b => b.node_ids.forEach(id => branchInteriorIds.add(id)))
  })

  // Spine: nodes not inside any branch, in level order
  const spineNodes = nodes.filter(n => !branchInteriorIds.has(n.id))

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      padding: '24px 20px 28px', width: '100%',
    }}>
      {spineNodes.map((node, i) => {
        const split = splitByFrom[node.id]
        return (
          <Fragment key={node.id}>
            {i > 0 && <ColArrow />}
            {/* ── Spine node ── */}
            {node.variant === 'resultNode' ? (
              <div style={{ display: 'flex', justifyContent: 'center', width: '100%', maxWidth: 620 }}>
                <ClinicalNodeCard node={node} />
              </div>
            ) : (
              <div style={{ width: '100%', maxWidth: 620 }}>
                <ClinicalNodeCard node={node} />
              </div>
            )}

            {/* ── Branch row below this node ── */}
            {split && (
              <>
                {/* Mini branch bar */}
                <div style={{
                  position: 'relative', width: '100%', maxWidth: 620, height: 20, marginTop: 2,
                }}>
                  <div style={{
                    position: 'absolute', top: 0,
                    left:  `${100 / (2 * split.branches.length)}%`,
                    right: `${100 / (2 * split.branches.length)}%`,
                    height: 1.5, background: CL.granite,
                  }} />
                  {split.branches.map((b, bi) => (
                    <div key={b.to_node_id} style={{
                      position: 'absolute', top: 0,
                      left: `${(bi + 0.5) * 100 / split.branches.length}%`,
                      transform: 'translateX(-50%)',
                      display: 'flex', flexDirection: 'column', alignItems: 'center',
                    }}>
                      <div style={{ width: 1.5, height: 14, background: CL.granite }} />
                      <div style={{ width: 0, height: 0, borderLeft: '4px solid transparent', borderRight: '4px solid transparent', borderTop: `5px solid ${CL.granite}` }} />
                    </div>
                  ))}
                </div>

                {/* Branch columns */}
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: `repeat(${split.branches.length}, 1fr)`,
                  gap: '1px', background: CL.badlands,
                  border: `1px solid ${CL.badlands}`,
                  borderRadius: 8, overflow: 'hidden',
                  width: '100%', maxWidth: 620,
                }}>
                  {split.branches.map(branch => {
                    const branchNodes = branch.node_ids.map(id => nodeMap[id]).filter(Boolean)
                    return (
                      <div key={branch.to_node_id} style={{
                        background: CL.white,
                        display: 'flex', flexDirection: 'column', alignItems: 'center',
                        padding: '8px 14px 20px',
                      }}>
                        {/* Branch label header */}
                        {branch.edge_label && (
                          <div style={{
                            width: '100%',
                            borderBottom: `1px solid ${CL.badlands}`,
                            padding: '4px 0 8px',
                            marginBottom: 4,
                          }}>
                            <div style={{
                              fontSize: 8, fontWeight: 700, letterSpacing: '0.08em',
                              textTransform: 'uppercase', color: CL.graniteM, textAlign: 'center',
                            }}>
                              {branch.edge_label}
                            </div>
                          </div>
                        )}
                        {/* Branch nodes */}
                        {branchNodes.map((bnode, bi) => (
                          <Fragment key={bnode.id}>
                            {bi > 0 && <ColArrow />}
                            {bnode.variant === 'resultNode' ? (
                              <div style={{ display: 'flex', justifyContent: 'center', width: '100%' }}>
                                <ClinicalNodeCard node={bnode} />
                              </div>
                            ) : (
                              <div style={{ width: '100%' }}>
                                <ClinicalNodeCard node={bnode} />
                              </div>
                            )}
                          </Fragment>
                        ))}
                        {branchNodes.length === 0 && (
                          <div style={{ fontSize: 10, color: CL.graniteM, fontStyle: 'italic', padding: 8 }}>
                            {branch.edge_label}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </>
            )}
          </Fragment>
        )
      })}
    </div>
  )
}


/* ═══════════════════════════════════════════════════════════════════════════
   ClinicalMultiZoneDocumentLayout — thyroid-spec multi-zone layout
   ─────────────────────────────────────────────────────────────────────────
   Used for: true parallel-zone algorithms with a shared entry section,
   routing label, branch bar, and equal-width zone columns.
   Matches the UX thyroid-cancer-flowchart-layout.json spec.
   ═══════════════════════════════════════════════════════════════════════════ */
function ClinicalMultiZoneDocumentLayout({ nodes, groups, edges, routingLabel, entryNodeIds }) {
  const nodeMap = {}
  nodes.forEach(n => { nodeMap[n.id] = n })

  const groupedIds  = new Set(groups.flatMap(g => g.node_ids))
  const colCount    = Math.max(groups.length, 1)
  const rLabel      = routingLabel || 'ROUTE BY RESULT'

  // Entry section: use entryNodeIds if provided, else nodes not in any group
  const entryNodes = entryNodeIds?.length
    ? entryNodeIds.map(id => nodeMap[id]).filter(Boolean)
    : nodes.filter(n => !groupedIds.has(n.id))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', width: '100%' }}>

      {/* ── Entry section ── */}
      {entryNodes.length > 0 && (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          padding: '28px 28px 20px', background: CL.white,
        }}>
          {entryNodes.map((node, i) => (
            <Fragment key={node.id}>
              {i > 0 && <EntryFlowArrow />}
              <div style={{ width: '100%', maxWidth: 520 }}>
                {node.variant === 'startNode'
                  ? <ClinicalNodeCard node={node} />
                  : node.variant === 'routingLabel'
                    ? <RoutingLabel text={node.raw_label || rLabel} />
                    : <SharedActionCard node={node} />}
              </div>
            </Fragment>
          ))}
          <EntryFlowArrow />
          <RoutingLabel text={rLabel} />
        </div>
      )}

      {entryNodes.length === 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '20px 28px 16px', background: CL.white }}>
          <RoutingLabel text={rLabel} />
        </div>
      )}

      {/* ── Branch bar ── */}
      {groups.length > 0 && (
        <div style={{ position: 'relative', width: '100%', height: 24, background: CL.white }}>
          <div style={{
            position: 'absolute', top: 0,
            left: `${100 / (2 * colCount)}%`,
            right: `${100 / (2 * colCount)}%`,
            height: 1.5, background: CL.granite,
          }} />
          {groups.map((g, i) => (
            <div key={g.group_id} style={{
              position: 'absolute', top: 0,
              left: `${(i + 0.5) * 100 / colCount}%`,
              transform: 'translateX(-50%)',
              display: 'flex', flexDirection: 'column', alignItems: 'center',
            }}>
              <div style={{ width: 1.5, height: 18, background: CL.granite }} />
              <div style={{ width: 0, height: 0, borderLeft: '4px solid transparent', borderRight: '4px solid transparent', borderTop: `5px solid ${CL.granite}` }} />
            </div>
          ))}
        </div>
      )}

      {/* ── Zone grid ── */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${colCount}, 1fr)`,
        gap: '1px', background: CL.badlands,
        border: `1px solid ${CL.badlands}`, borderTop: 'none',
      }}>
        {groups.map((group) => {
          const accent     = group.accent || { hex: CL.aspen }
          const zoneNodes  = group.node_ids.map(id => nodeMap[id]).filter(Boolean)
          const nestedFork = group.nested_fork
          const forkNodeId = nestedFork?.fork_node_id

          const preForkNodes = forkNodeId
            ? zoneNodes.filter(n => !nestedFork.paths.flatMap(p => p.node_ids).includes(n.id) && n.id !== forkNodeId)
            : zoneNodes
          const forkDecision = forkNodeId ? nodeMap[forkNodeId] : null

          return (
            <div key={group.group_id} style={{
              background: CL.white,
              display: 'flex', flexDirection: 'column', alignItems: 'center',
              padding: '10px 20px 28px',
            }}>
              <ZoneHeader label={group.zone_label || group.label} subtitle={group.zone_subtitle} accentHex={accent.hex} />

              {preForkNodes.map((node) => (
                <Fragment key={node.id}>
                  <ColArrow />
                  {node.variant === 'resultNode' ? (
                    <div style={{ width: '100%', display: 'flex', justifyContent: 'center' }}>
                      <ClinicalNodeCard node={node} />
                    </div>
                  ) : (
                    <div style={{ width: '100%' }}>
                      <ClinicalNodeCard node={node} />
                    </div>
                  )}
                </Fragment>
              ))}

              {nestedFork && forkDecision && (
                <>
                  <ColArrow />
                  <div style={{ width: '100%' }}><ClinicalNodeCard node={forkDecision} /></div>
                  <div style={{ width: '100%', paddingTop: 4 }}><TJunctionFork /></div>
                  <PathNodesGrid paths={nestedFork.paths} nodeMap={nodeMap} />
                </>
              )}

              {zoneNodes.length === 0 && (
                <div style={{ fontSize: 11, color: CL.graniteM, fontStyle: 'italic', padding: '16px 4px', textAlign: 'center' }}>
                  No steps in this path
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

/* Backward-compat aliases */
function ClinicalDocumentLayout(props) { return <ClinicalMultiZoneDocumentLayout {...props} /> }
function GroupedBranchLayout(props) { return <ClinicalMultiZoneDocumentLayout {...props} /> }


/* ═══════════════════════════════════════════════════════════════════════════
   AlgorithmFlowchart — v2.1.0
   ─────────────────────────────────────────────────────────────────────────
   New in v2.1.0:
     • 'split' view mode: rendered algorithm left, embedded PDF right
     • Default to 'split' when has_local_pdf && pdf_url && screen ≥ 1100px
     • defaultViewMode / showSplitView props forwarded from FormattingAgent
     • Window-width tracking via ResizeObserver — auto-downgrade on narrow
     • Rendered and PDF content extracted into helper fns to avoid duplication
     • All existing 'rendered' / 'pdf' / 'source' modes preserved unchanged
   ═══════════════════════════════════════════════════════════════════════════ */
function AlgorithmFlowchart({ viz, defaultViewMode, showSplitView }) {
  // ── Width tracking — 1100px is the split-view breakpoint ────────────────
  const containerRef = useRef(null)
  const [isWide, setIsWide] = useState(
    () => typeof window !== 'undefined' && window.innerWidth >= 1100
  )
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    // Prefer ResizeObserver (accurate container width) over window width
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) {
        setIsWide(entry.contentRect.width >= 840) // container ≥840px ≈ window≥1100
      }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // ── State ────────────────────────────────────────────────────────────────
  const [expanded, setExpanded] = useState(true)
  const [showTests, setShowTests] = useState(false)

  // viewMode initialised once; uses formatting hints when present
  const [viewMode, setViewMode] = useState(() => {
    const wantSplit    = showSplitView !== false  // default true if prop absent
    const localPdf     = Boolean(viz?.has_local_pdf && viz?.pdf_url)
    const wideEnough   = typeof window !== 'undefined' && window.innerWidth >= 1100
    if (localPdf && wantSplit && wideEnough) return 'split'
    // Honour explicit defaultViewMode from FormattingAgent when it is 'rendered'
    if (defaultViewMode === 'rendered') return 'rendered'
    return 'rendered'
  })

  // Auto-downgrade from split when container becomes too narrow
  useEffect(() => {
    if (!isWide && viewMode === 'split') setViewMode('rendered')
  }, [isWide])   // eslint-disable-line react-hooks/exhaustive-deps

  // ── Guard ────────────────────────────────────────────────────────────────
  if (!viz) return null

  // ── Fidelity-first renderer path ──────────────────────────────────────────
  // If render_mode is 'arup_document_fidelity', use the new fidelity renderer
  // instead of the clinical renderer
  if (viz.render_mode === 'arup_document_fidelity') {
    return <ArupDocumentFidelityRenderer data={viz} />
  }

  const {
    title, source_url, layout, layout_mode, render_mode,
    nodes, edges, referenced_tests, stats, groups,
    routing_label, reviewed_date, updated_date,
    entry_section_node_ids, branch_splits, spine_node_ids,
    is_multi_zone, pdf_url, source_page_url, source_asset_type,
    footer_blocks,
    // v4 fields
    has_local_pdf, pdf_origin, pdf_asset_id,
  } = viz

  const effectiveLayout = render_mode || layout_mode || layout || 'clinical_linear_document'
  const isMultiZone = effectiveLayout === 'clinical_multi_zone_document'
                   || effectiveLayout === 'clinical_document_flowchart'
                   || effectiveLayout === 'grouped_branch_layout'
  const isTree      = effectiveLayout === 'clinical_tree_document' || effectiveLayout === 'tree'

  const layoutLabel = isMultiZone ? 'Multi-zone clinical pathway'
    : isTree ? 'Decision tree'
    : 'Linear pathway'

  const byLevel = {}
  nodes.forEach(n => { (byLevel[n.level] = byLevel[n.level] || []).push(n) })

  // ── PDF availability logic ───────────────────────────────────────────────
  // has_local_pdf: true  → pdf_url is /api/assets/pdf/… — same-origin, safe to embed
  // has_local_pdf: false → pdf_url is remote — NOT safe to embed, show fallback
  const canEmbedPdf     = Boolean(has_local_pdf && pdf_url)
  const hasRemotePdfUrl = Boolean(!has_local_pdf && pdf_url)
  const hasPdf          = Boolean(pdf_url)
  const hasSource       = Boolean(source_url || source_page_url)
  const sourceLink      = source_page_url || source_url || ''
  const canSplit        = canEmbedPdf && isWide

  // Origin badge text
  const pdfOriginLabel = pdf_origin === 'local_uploaded'  ? '📎 Local PDF'
    : pdf_origin === 'local_cached'   ? '💾 Cached PDF'
    : pdf_origin === 'remote_direct'  ? '🌐 Remote PDF'
    : null

  // ── Reusable rendered-algorithm pane ─────────────────────────────────────
  const renderAlgorithmPane = (opts = {}) => {
    const { maxH, borderRight } = opts
    const panelMaxH = maxH ?? (isMultiZone ? 900 : 680)
    return (
      <div style={{
        overflowX: 'auto', overflowY: 'auto', background: '#F5F5F5',
        maxHeight: panelMaxH,
        ...(borderRight ? { borderRight: `1px solid ${CL.badlands}` } : {}),
        flex: 1, minWidth: 0,
      }}>
        <div style={{ padding: isMultiZone ? '16px 16px 0' : 0 }}>
          {isMultiZone && groups?.length > 0 ? (
            <div style={{ background: CL.white, border: `1px solid ${CL.badlands}`, borderRadius: 8, overflow: 'hidden', marginBottom: 16 }}>
              <ClinicalMultiZoneDocumentLayout nodes={nodes} groups={groups} edges={edges} routingLabel={routing_label} entryNodeIds={entry_section_node_ids} />
            </div>
          ) : isTree ? (
            <ClinicalTreeDocumentLayout nodes={nodes} edges={edges} branchSplits={branch_splits} />
          ) : (
            <ClinicalLinearDocumentLayout nodes={nodes} branchSplits={branch_splits} />
          )}
        </div>
        {!isMultiZone && (
          <div style={{ margin: '4px 24px 16px', paddingTop: 12, borderTop: `1px solid ${CL.badlands}`, display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
            <span className="text-caption" style={{ color: CL.graniteM, fontWeight: 600, marginRight: 4 }}>Legend:</span>
            {[
              { label: 'Action',   bg: CL.white,       border: CL.badlands },
              { label: 'Critical', bg: CL.white,       border: CL.primary },
              { label: 'Result',   bg: CL.nodeNeutral, border: CL.aspen, radius: 20 },
              { label: 'Outcome',  bg: CL.nodeNeutral, border: CL.badlands },
            ].map(({ label, bg, border, radius }) => (
              <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <div style={{ width: 12, height: 12, background: bg, border: `1.5px solid ${border}`, borderRadius: radius || 2, flexShrink: 0 }} />
                <span className="text-caption" style={{ color: CL.granite2 }}>{label}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }

  // ── Reusable embedded-PDF pane (same-origin only) ────────────────────────
  const renderEmbeddedPdfPane = (opts = {}) => {
    const { height } = opts
    const pdfH = height ?? 680
    return (
      <div style={{ display: 'flex', flexDirection: 'column', background: '#F5F5F5', flex: 1, minWidth: 0 }}>
        {pdfOriginLabel && (
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: 5, margin: '10px 12px 0',
            fontSize: 10, color: CL.graniteM, background: CL.nodeNeutral,
            padding: '3px 10px', borderRadius: 20, border: `1px solid ${CL.badlands}`,
            alignSelf: 'flex-start',
          }}>
            {pdfOriginLabel}
          </div>
        )}
        <div style={{
          flex: 1, margin: '10px 12px 0',
          background: CL.white, border: `1px solid ${CL.badlands}`,
          borderRadius: 6, overflow: 'hidden',
        }}>
          <iframe
            src={pdf_url}
            title={`${title} — PDF`}
            style={{ width: '100%', height: pdfH, border: 'none', display: 'block' }}
          />
        </div>
        <div style={{ padding: '6px 12px 10px', textAlign: 'center' }}>
          <a href={pdf_url} target="_blank" rel="noreferrer"
            style={{ fontSize: 11, color: 'var(--lab-blue)' }}>
            Open PDF in new tab ↗
          </a>
        </div>
      </div>
    )
  }

  // ── Footer blocks (abbreviations, footnotes) — shared across rendered & split
  const renderFooterBlocks = () => {
    if (!footer_blocks?.length) return null
    return (
      <div style={{ borderTop: `1px solid ${CL.badlands}`, background: CL.nodeNeutral, padding: '12px 20px', display: 'flex', flexDirection: 'column', gap: 10 }}>
        {footer_blocks.map((fb, i) => (
          <div key={i}>
            {fb.type === 'footnote' && (
              <div style={{ fontSize: 10.5, color: CL.graniteM, lineHeight: 1.6 }}>
                {fb.marker && <sup style={{ marginRight: 4, fontWeight: 700 }}>{fb.marker}</sup>}
                {fb.content}
              </div>
            )}
            {fb.type === 'abbreviations' && fb.items?.length > 0 && (
              <div>
                <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: CL.graniteM, marginBottom: 6 }}>Abbreviations</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 20px' }}>
                  {fb.items.map((a, ai) => (
                    <div key={ai} style={{ fontSize: 10.5, color: CL.granite2 }}>
                      <strong style={{ color: CL.darkSky }}>{a.key}</strong>
                      {a.definition && <span style={{ color: CL.graniteM }}> — {a.definition}</span>}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    )
  }

  // ── Tab list — build dynamically based on capabilities ──────────────────
  const tabs = [
    ...(canSplit ? [{ id: 'split', label: '⊟ Split' }] : []),
    { id: 'rendered', label: 'Rendered' },
    ...(hasPdf    ? [{ id: 'pdf',    label: 'PDF' }]     : []),
    ...(hasSource ? [{ id: 'source', label: 'Source ↗' }] : []),
  ]

  return (
    <div ref={containerRef} style={{
      marginTop: 16,
      border: `1px solid ${CL.badlands}`,
      borderRadius: 8,
      overflow: 'hidden',
      background: CL.white,
      width: '100%',
    }}>
      {/* ── Clinical document header ── */}
      <div style={{ background: CL.white, borderBottom: `1px solid ${CL.badlands}` }}>
        {/* Title row */}
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', padding: '14px 20px 8px', gap: 12 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: CL.darkSky, letterSpacing: '-0.2px', lineHeight: 1.25 }}>
              {title || 'Clinical Algorithm'}
            </div>
            <div style={{ fontSize: 11, color: CL.graniteM, marginTop: 2 }}>
              ARUP Laboratories — Clinical Decision Support
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3, flexShrink: 0 }}>
            {sourceLink && (
              <a href={sourceLink} target="_blank" rel="noreferrer" style={{ fontSize: 11, color: 'var(--lab-blue)', textDecoration: 'none' }}>
                arupconsult.com ↗
              </a>
            )}
            {hasPdf && !has_local_pdf && (
              <a href={pdf_url} target="_blank" rel="noreferrer"
                style={{ fontSize: 11, color: CL.graniteM, textDecoration: 'none' }}>
                PDF ↗
              </a>
            )}
            {has_local_pdf && (
              <span style={{
                fontSize: 9, fontWeight: 700, letterSpacing: '0.04em',
                textTransform: 'uppercase', color: 'var(--positive)',
                background: 'var(--positive-container, #F0FFF4)',
                border: '1px solid var(--positive)',
                borderRadius: '50vh', padding: '2px 7px',
              }}>
                📎 Local PDF
              </span>
            )}
            {(reviewed_date || updated_date) && (
              <div style={{ fontSize: 10, color: CL.graniteM, textAlign: 'right', lineHeight: 1.7 }}>
                {reviewed_date && <div>Content Reviewed: {reviewed_date}</div>}
                {updated_date  && <div>Last Updated: {updated_date}</div>}
              </div>
            )}
          </div>
        </div>

        {/* Controls row */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 20px 10px', gap: 12 }}>
          <span style={{ fontSize: 10, color: 'var(--accent2)', fontWeight: 500 }}>
            {stats.total_nodes} steps · {layoutLabel}
            {stats.decision_count > 0 && ` · ${stats.decision_count} decision${stats.decision_count > 1 ? 's' : ''}`}
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {tabs.length > 1 && (
              <div style={{ display: 'flex', gap: 1, background: CL.nodeNeutral, borderRadius: 6, padding: 2 }}>
                {tabs.map(tab => (
                  <button key={tab.id}
                    onClick={e => {
                      e.stopPropagation()
                      if (tab.id === 'source') { window.open(sourceLink, '_blank', 'noreferrer') }
                      else { setViewMode(tab.id); if (!expanded) setExpanded(true) }
                    }}
                    style={{
                      fontSize: 10, fontWeight: viewMode === tab.id ? 700 : 400,
                      color: viewMode === tab.id ? CL.darkSky : CL.graniteM,
                      background: viewMode === tab.id ? CL.white : 'transparent',
                      border: viewMode === tab.id ? `1px solid ${CL.badlands}` : '1px solid transparent',
                      borderRadius: 4, padding: '3px 8px', cursor: 'pointer', lineHeight: 1.4,
                      // Accent the Split button slightly when available
                      ...(tab.id === 'split' && viewMode !== 'split' ? { color: 'var(--positive)', fontWeight: 600 } : {}),
                    }}
                  >{tab.label}</button>
                ))}
              </div>
            )}
            {referenced_tests.length > 0 && (
              <button onClick={e => { e.stopPropagation(); setShowTests(t => !t) }}
                style={{ fontSize: 11, fontWeight: 600, color: 'var(--secondary)', background: 'var(--bg-salt)', border: `1px solid ${CL.badlands}`, borderRadius: '50vh', padding: '3px 10px', cursor: 'pointer' }}>
                {referenced_tests.length} test{referenced_tests.length > 1 ? 's' : ''}
              </button>
            )}
            <span onClick={() => setExpanded(e => !e)}
              style={{ fontSize: 14, color: 'var(--accent2)', cursor: 'pointer', transform: expanded ? 'none' : 'rotate(-90deg)', transition: 'transform .2s', display: 'inline-block', userSelect: 'none' }}>▾</span>
          </div>
        </div>
      </div>

      {/* Referenced tests table */}
      {showTests && referenced_tests.length > 0 && (
        <div style={{ padding: '10px 16px', background: '#F9F0F2', borderBottom: `1px solid ${CL.badlands}` }}>
          <div className="text-label" style={{ color: 'var(--secondary)', marginBottom: 6 }}>Referenced ARUP Tests</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead><tr>
              {['Code', 'Test Name', 'Section'].map(h => (
                <th key={h} style={{ textAlign: 'left', padding: '3px 8px', color: 'var(--accent)', fontWeight: 600, borderBottom: `1px solid ${CL.badlands}` }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {referenced_tests.map((t, i) => (
                <tr key={i} style={{ background: i % 2 === 0 ? CL.white : CL.nodeNeutral }}>
                  <td style={{ padding: '4px 8px', fontWeight: 600, color: 'var(--secondary)', fontFamily: 'monospace' }}>{t.test_code || '—'}</td>
                  <td style={{ padding: '4px 8px', color: CL.darkSky }}>
                    {t.test_url ? <a href={t.test_url} target="_blank" rel="noreferrer" style={{ color: 'var(--lab-blue)' }}>{t.test_name || '—'}</a> : (t.test_name || '—')}
                  </td>
                  <td style={{ padding: '4px 8px', color: CL.graniteM, fontStyle: 'italic' }}>{t.section || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Flow body ─────────────────────────────────────────────────────── */}
      {expanded && (
        <div>

          {/* ── SPLIT VIEW — rendered algorithm (left) + embedded PDF (right) ── */}
          {viewMode === 'split' && canSplit && (
            <>
              <div style={{ display: 'flex', alignItems: 'stretch', width: '100%', minHeight: 560 }}>
                {renderAlgorithmPane({ maxH: isMultiZone ? 900 : 680, borderRight: true })}
                {renderEmbeddedPdfPane({ height: isMultiZone ? 900 : 680 })}
              </div>
              {renderFooterBlocks()}
            </>
          )}

          {/* ── PDF-ONLY VIEW ── */}
          {viewMode === 'pdf' && hasPdf && (
            <div style={{ background: '#F5F5F5', padding: 16 }}>
              {canEmbedPdf ? (
                /* Same-origin local PDF — safe to embed */
                <div>
                  {pdfOriginLabel && (
                    <div style={{
                      display: 'inline-flex', alignItems: 'center', gap: 5,
                      fontSize: 10, color: CL.graniteM, marginBottom: 10,
                      background: CL.nodeNeutral, padding: '3px 10px',
                      borderRadius: 20, border: `1px solid ${CL.badlands}`,
                    }}>
                      {pdfOriginLabel}
                    </div>
                  )}
                  <div style={{
                    background: CL.white, border: `1px solid ${CL.badlands}`,
                    borderRadius: 6, overflow: 'hidden',
                  }}>
                    <iframe
                      src={pdf_url}
                      title={`${title} — PDF`}
                      style={{ width: '100%', height: 720, border: 'none', display: 'block' }}
                    />
                  </div>
                  <div style={{ marginTop: 8, textAlign: 'center' }}>
                    <a href={pdf_url} target="_blank" rel="noreferrer"
                      style={{ fontSize: 11, color: 'var(--lab-blue)' }}>
                      Open PDF in new tab ↗
                    </a>
                  </div>
                </div>
              ) : (
                /* Remote PDF URL — cannot reliably embed, show fallback */
                <div style={{
                  background: CL.white, border: `1px solid ${CL.badlands}`,
                  borderRadius: 8, padding: '28px 24px',
                  textAlign: 'center', display: 'flex', flexDirection: 'column',
                  alignItems: 'center', gap: 16,
                }}>
                  <div style={{ fontSize: 32 }}>📄</div>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 600, color: CL.darkSky, marginBottom: 6 }}>
                      PDF available from ARUP Consult
                    </div>
                    <div style={{ fontSize: 12, color: CL.graniteM, lineHeight: 1.6, maxWidth: 380 }}>
                      Remote PDFs cannot be embedded directly due to browser security restrictions.
                      Upload the algorithm PDF to enable embedded split-view.
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', justifyContent: 'center' }}>
                    <a href={pdf_url} target="_blank" rel="noreferrer"
                      className="btn btn-primary"
                      style={{ fontSize: 12, padding: '8px 18px', textDecoration: 'none' }}>
                      Open PDF ↗
                    </a>
                    {sourceLink && (
                      <a href={sourceLink} target="_blank" rel="noreferrer"
                        className="btn btn-outlined"
                        style={{ fontSize: 12, padding: '8px 18px', textDecoration: 'none' }}>
                        View source page ↗
                      </a>
                    )}
                  </div>
                  <div style={{
                    fontSize: 10, color: CL.graniteM, fontStyle: 'italic',
                    maxWidth: 380, lineHeight: 1.5,
                  }}>
                    Tip: Upload algorithm PDFs via the Upload tab — the app will automatically
                    link them and show embedded split-view previews.
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ── RENDERED-ONLY VIEW ── */}
          {viewMode === 'rendered' && (
            <>
              {renderAlgorithmPane()}
              {renderFooterBlocks()}
            </>
          )}

        </div>
      )}
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════════════════════
   NEW v2.0.0 — Schema-driven components
   ═══════════════════════════════════════════════════════════════════════════ */

/* ── TextBlockRenderer ───────────────────────────────────────────────────
   Renders text_block schema components.
   variant "answer_narrative"  → plain paragraph (first block, no top margin)
   variant "fallback_card"     → card-wrapped paragraph
── */
function TextBlockRenderer({ variant, props, isFirst }) {
  const content = props?.content || ''
  const color   = props?.color   || 'var(--dark-sky)'

  if (variant === 'fallback_card') {
    return (
      <div style={{
        padding: props?.padding || 'var(--card-padding)',
        background: 'var(--white)',
        border: '1px solid var(--accent3)',
        borderRadius: 'var(--card-radius)',
        marginTop: isFirst ? 0 : 12,
      }}>
        <p className="text-body" style={{ color, whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
          {content}
        </p>
      </div>
    )
  }

  // Default: answer_narrative
  return (
    <p className="text-body" style={{ color, whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
      {content}
    </p>
  )
}


/* ── BadgeGroupBlock ─────────────────────────────────────────────────────
   Renders badge_group schema components — evidence coverage pills.
── */
function BadgeGroupBlock({ props }) {
  const badges = props?.badges || []
  const title  = props?.title  || ''
  if (!badges.length) return null
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
      {title && (
        <span className="text-label" style={{ color: 'var(--accent)', marginRight: 4 }}>{title}</span>
      )}
      {badges.map((b, i) => (
        <span key={i} className={b.cls}>{b.label}</span>
      ))}
    </div>
  )
}


/* ── RecommendationsTable ────────────────────────────────────────────────
   Renders the "table" schema component (≥ 3 recommendations).
   Features:
   • Columns: Rank | Test | Code | Specimen | TAT | Evidence
   • Click-to-expand row → shows rationale beneath
   • Pagination when tableProps.paginate is true
   • Zebra-striped via arup-table CSS
── */
function RecommendationsTable({ tableProps }) {
  const {
    columns    = [],
    rows       = [],
    paginate   = false,
    page_size  = 8,
  } = tableProps

  const [page, setPage]       = useState(0)
  const [expanded, setExpanded] = useState({})

  const toggleExpand = (idx) =>
    setExpanded(prev => ({ ...prev, [idx]: !prev[idx] }))

  const displayRows = paginate
    ? rows.slice(page * page_size, (page + 1) * page_size)
    : rows
  const totalPages = paginate ? Math.ceil(rows.length / page_size) : 1

  // Rank config local (mirrors RANK_CFG)
  const RANK_LOCAL = {
    primary:   { badgeCls: 'badge-danger',   label: 'Primary' },
    secondary: { badgeCls: 'badge-info',     label: 'Secondary' },
    reflex:    { badgeCls: 'badge-positive', label: 'Reflex' },
  }

  return (
    <div style={{ marginTop: 20 }}>

      {/* Table header label */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span className="text-label" style={{ color: 'var(--accent)' }}>
          Test recommendations ({rows.length})
        </span>
        {rows.length > 1 && (
          <span className="text-caption" style={{ color: 'var(--accent)', fontStyle: 'italic' }}>
            — click row to see rationale
          </span>
        )}
      </div>

      <div style={{
        overflowX: 'auto',
        border: '1px solid var(--accent3)',
        borderRadius: 'var(--card-radius)',
      }}>
        <table className="arup-table" style={{ margin: 0, fontSize: 13 }}>
          <thead>
            <tr>
              {/* Expand toggle column */}
              <th style={{ width: 28, padding: '8px 6px' }} />
              {columns.map(col => (
                <th key={col.key} style={{ width: col.width || undefined }}>
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {displayRows.flatMap((row, i) => {
              const rankCfg  = RANK_LOCAL[row.rank] || RANK_LOCAL.secondary
              const isExpand = expanded[row.index]
              const bg       = row.is_even ? 'var(--bg-salt)' : 'var(--bg-primary)'
              const hasRat   = Boolean(row.rationale)

              const dataRow = (
                <tr
                  key={`row-${row.index}`}
                  onClick={() => hasRat && toggleExpand(row.index)}
                  style={{ cursor: hasRat ? 'pointer' : 'default' }}
                >
                  {/* Expand toggle cell */}
                  <td style={{ background: bg, textAlign: 'center', padding: '6px 4px', verticalAlign: 'middle' }}>
                    {hasRat && (
                      <span style={{
                        fontSize: 9,
                        color: 'var(--accent)',
                        display: 'inline-block',
                        transform: isExpand ? 'rotate(90deg)' : 'none',
                        transition: 'transform .15s',
                        lineHeight: 1,
                      }}>▶</span>
                    )}
                  </td>

                  {/* Rank badge */}
                  <td style={{ background: bg, verticalAlign: 'middle' }}>
                    <span className={`badge ${rankCfg.badgeCls}`} style={{ fontSize: 11 }}>
                      {rankCfg.label}
                    </span>
                  </td>

                  {/* Test name */}
                  <td style={{ background: bg, fontWeight: 600, color: 'var(--dark-sky)', verticalAlign: 'middle' }}>
                    {row.test_name}
                  </td>

                  {/* Test code monospace */}
                  <td style={{ background: bg, verticalAlign: 'middle' }}>
                    {row.test_code ? (
                      <code style={{
                        fontSize: 11, fontFamily: "'Roboto Mono', monospace",
                        padding: '2px 7px', borderRadius: 3,
                        background: 'var(--bg-tertiary)', color: 'var(--accent2)',
                        border: '1px solid var(--accent3)',
                      }}>
                        {row.test_code}
                      </code>
                    ) : <span style={{ color: 'var(--accent3)' }}>—</span>}
                  </td>

                  {/* Specimen */}
                  <td style={{ background: bg, color: 'var(--accent2)', fontSize: 12, verticalAlign: 'middle' }}>
                    {row.specimen || <span style={{ color: 'var(--accent3)' }}>—</span>}
                  </td>

                  {/* TAT */}
                  <td style={{ background: bg, color: 'var(--accent2)', fontSize: 12, verticalAlign: 'middle' }}>
                    {row.tat || <span style={{ color: 'var(--accent3)' }}>—</span>}
                  </td>

                  {/* Evidence coverage badges */}
                  <td style={{ background: bg, verticalAlign: 'middle' }}>
                    <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
                      {(row.evidence || []).map((b, bi) => (
                        <span key={bi} className={b.cls} style={{ fontSize: 10 }}>{b.label}</span>
                      ))}
                      {(!row.evidence || row.evidence.length === 0) && (
                        <span style={{ color: 'var(--accent3)', fontSize: 12 }}>—</span>
                      )}
                    </div>
                  </td>
                </tr>
              )

              const expandRow = isExpand && hasRat ? (
                <tr key={`exp-${row.index}`}>
                  <td
                    colSpan={columns.length + 1}
                    style={{
                      background: '#EEF4FB',
                      padding: '10px 16px',
                      borderTop: '1px solid var(--accent3)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                      <span style={{ color: 'var(--lab-blue)', fontSize: 13, flexShrink: 0, marginTop: 1 }}>ℹ</span>
                      <p className="text-body-sm" style={{ color: 'var(--accent2)', lineHeight: 1.6, fontStyle: 'italic' }}>
                        {row.rationale}
                      </p>
                    </div>
                  </td>
                </tr>
              ) : null

              return expandRow ? [dataRow, expandRow] : [dataRow]
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {paginate && totalPages > 1 && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          gap: 10, marginTop: 10,
        }}>
          <button
            disabled={page === 0}
            onClick={() => setPage(p => p - 1)}
            className="btn btn-outlined"
            style={{ padding: '4px 16px', fontSize: 12 }}
          >
            ← Prev
          </button>
          <span className="text-caption" style={{ color: 'var(--accent)' }}>
            Page {page + 1} of {totalPages} &nbsp;·&nbsp; {rows.length} tests
          </span>
          <button
            disabled={page >= totalPages - 1}
            onClick={() => setPage(p => p + 1)}
            className="btn btn-outlined"
            style={{ padding: '4px 16px', fontSize: 12 }}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  )
}


/* ── Adapter: schema recommendation_card props → RecommendationCard rec ─ */
function schemaPropsToRec(props) {
  const coverageBadges = props.coverage_badges || []
  return {
    test_name:  props.test_name  || '',
    test_code:  props.test_code  || '',
    test_url:   props.test_url   || '',
    rank:       props.rank       || 'secondary',
    rationale:  props.rationale  || '',
    specimen:   props.specimen   || '',
    tat:        props.tat        || '',
    source_count: props.source_count || 0,
    appears_in_algorithm: props.appears_in_algorithm || false,
    evidence_coverage: {
      has_algorithm:  coverageBadges.some(b => b.label === 'Algorithm'),
      has_consult:    coverageBadges.some(b => b.label === 'Consult Topic'),
      has_directory:  coverageBadges.some(b => b.label === 'Test Directory'),
      has_fact_sheet: coverageBadges.some(b => b.label === 'Fact Sheet'),
    },
  }
}

/* ── Adapter: schema citation_table rows → CitationsTable citations ────── */
function schemaCitationRows(props) {
  return (props.rows || []).map(r => ({
    number:      r.number,
    source_type: r.source_type,
    source_role: r.source_role,
    document:    r.document,
    excerpt:     r.excerpt,
  }))
}

/* ── Spacing constants between schema component types ─────────────────── */
const COMPONENT_TOP_MARGIN = {
  text_block:          0,
  recommendation_card: 12,
  table:               20,
  algorithm_flow:       0,
  fidelity_report:     12,
  badge_group:          8,
  warning_block:       14,
  review_concerns:     14,
  info_block:          10,
  citation_table:      20,
}


/* ── UISchemaRenderer ────────────────────────────────────────────────────
   Central dispatcher: takes ui_schema from FormattingAgent output and
   renders each component in clinical priority order.

   Schema shape expected:
   {
     layout:         "card_stack" | "mixed_layout",
     component_count: number,
     render_hints:   { show_algorithm_first, use_table_for_recommendations, ... },
     components:     [{ type, variant, props, priority }]
   }
── */
function UISchemaRenderer({ schema }) {
  if (!schema?.components?.length) return null

  // Sort by priority ascending (1 = render first)
  const sorted = [...schema.components].sort((a, b) => a.priority - b.priority)

  // Group consecutive recommendation_card components to wrap in a column flex
  // so they stack with uniform gap rather than individual marginTop per card
  const recIndices = new Set(
    sorted
      .map((c, i) => c.type === 'recommendation_card' ? i : -1)
      .filter(i => i >= 0)
  )

  const rendered = []
  let i = 0

  while (i < sorted.length) {
    const comp = sorted[i]

    // Collect a consecutive run of recommendation_cards
    if (comp.type === 'recommendation_card') {
      const cards = []
      while (i < sorted.length && sorted[i].type === 'recommendation_card') {
        cards.push(sorted[i])
        i++
      }
      rendered.push(
        <div key={`rec-group-${cards[0].priority}`} style={{ marginTop: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
          {cards.map((c, ci) => (
            <RecommendationCard key={ci} rec={schemaPropsToRec(c.props)} />
          ))}
        </div>
      )
      continue
    }

    const marginTop = i === 0 ? 0 : (COMPONENT_TOP_MARGIN[comp.type] ?? 16)

    switch (comp.type) {

      case 'text_block':
        rendered.push(
          <div key={`c-${i}`} style={{ marginTop }}>
            <TextBlockRenderer variant={comp.variant} props={comp.props} isFirst={i === 0} />
          </div>
        )
        break

      case 'table':
        rendered.push(
          <div key={`c-${i}`} style={{ marginTop }}>
            <RecommendationsTable tableProps={comp.props} />
          </div>
        )
        break

      case 'algorithm_flow':
        rendered.push(
          <div key={`c-${i}`} style={{ marginTop: 16 }}>
            <AlgorithmFlowchart
              viz={comp.props.graph}
              defaultViewMode={comp.props.defaultViewMode}
              showSplitView={comp.props.showSplitView}
            />
          </div>
        )
        break

      case 'badge_group':
        rendered.push(
          <div key={`c-${i}`} style={{ marginTop }}>
            <BadgeGroupBlock props={comp.props} />
          </div>
        )
        break

      case 'warning_block':
        rendered.push(
          <div key={`c-${i}`} style={{ marginTop }}>
            <ConflictsBlock conflicts={comp.props.items || []} />
          </div>
        )
        break

      case 'info_block':
        rendered.push(
          <div key={`c-${i}`} style={{ marginTop }}>
            <EvidenceGapsBlock gaps={comp.props.items || []} />
          </div>
        )
        break

      case 'citation_table':
        rendered.push(
          <div key={`c-${i}`} style={{ marginTop }}>
            <CitationsTable citations={schemaCitationRows(comp.props)} />
          </div>
        )
        break

      case 'review_concerns':
        rendered.push(
          <div key={`c-${i}`} style={{ marginTop }}>
            <ReviewConcernsBlock props={comp.props} />
          </div>
        )
        break

      case 'fidelity_report':
        rendered.push(
          <div key={`c-${i}`} style={{ marginTop }}>
            <FidelityReportBlock props={comp.props} />
          </div>
        )
        break

      default:
        // Unknown component type — skip silently (forward compat)
        break
    }

    i++
  }

  return <>{rendered}</>
}


/* ═══════════════════════════════════════════════════════════════════════════
   Existing sub-components (unchanged from v1.x)
   ═══════════════════════════════════════════════════════════════════════════ */

/* ── Clarification block (v0.7.0) ─────────────────────────────────────── */
function ClarificationBlock({ clarification, onAnswer }) {
  const [chosen, setChosen]     = useState(null)
  const [freeText, setFreeText] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const { question, options = [], reason } = clarification

  const handleChoice = (opt) => {
    if (submitted) return
    setChosen(opt)
    setSubmitted(true)
    // Submit ONLY the answer value — not the echoed question
    if (onAnswer) onAnswer(opt)
  }

  const handleFreeSubmit = () => {
    const val = freeText.trim()
    if (!val || submitted) return
    setSubmitted(true)
    if (onAnswer) onAnswer(val)
  }

  return (
    <div style={{
      marginTop: 16, padding: '16px 20px',
      background: '#FFF5F5',
      border: '1px solid var(--primary)',
      borderRadius: 'var(--card-radius)',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 14 }}>
        <span style={{ fontSize: 18, flexShrink: 0 }}>🔍</span>
        <div>
          <p className="text-body-sm" style={{ fontWeight: 700, color: 'var(--dark-sky)', marginBottom: 2 }}>
            {question}
          </p>
          {reason && (
            <p className="text-caption" style={{ color: 'var(--accent)', fontStyle: 'italic', marginTop: 2 }}>
              {reason}
            </p>
          )}
        </div>
      </div>

      {/* Submitted confirmation */}
      {submitted && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '10px 14px',
          background: 'var(--positive-container, #F0FFF4)',
          border: '1px solid var(--positive)',
          borderRadius: 'var(--card-radius)',
          marginTop: 4,
        }}>
          <span style={{ color: 'var(--positive)', fontSize: 16 }}>✓</span>
          <span className="text-caption" style={{ color: 'var(--positive)', fontWeight: 600 }}>
            Answer submitted: {chosen || freeText}
          </span>
        </div>
      )}

      {/* Option buttons — answer choices, not echoed questions */}
      {!submitted && options.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
          {options.map((opt, oIdx) => (
            <button
              key={oIdx}
              onClick={() => handleChoice(opt)}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '10px 16px',
                borderRadius: 'var(--card-radius)',
                border: '1px solid var(--accent)',
                background: 'var(--white)',
                color: 'var(--dark-sky)',
                cursor: 'pointer',
                textAlign: 'left', fontSize: 14,
                fontWeight: 400, lineHeight: 1.4,
                transition: 'all .15s',
                fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
              }}
              onMouseEnter={e => {
                e.currentTarget.style.borderColor = 'var(--primary)'
                e.currentTarget.style.background  = '#FFF5F5'
              }}
              onMouseLeave={e => {
                e.currentTarget.style.borderColor = 'var(--accent)'
                e.currentTarget.style.background  = 'var(--white)'
              }}
            >
              <span style={{
                width: 22, height: 22, borderRadius: '50%',
                background: 'var(--bg-salt)',
                border: '1px solid var(--accent3)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 11, fontWeight: 700, color: 'var(--accent2)', flexShrink: 0,
              }}>
                {String.fromCharCode(65 + oIdx)}
              </span>
              {opt}
            </button>
          ))}
        </div>
      )}

      {/* Free-text input — when no options provided */}
      {!submitted && options.length === 0 && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', marginTop: 4 }}>
          <textarea
            value={freeText}
            onChange={e => setFreeText(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleFreeSubmit() } }}
            placeholder="Type your answer…"
            rows={2}
            style={{
              flex: 1, resize: 'none', padding: '8px 12px',
              border: '1px solid var(--primary)', borderRadius: 'var(--card-radius)',
              fontSize: 14, fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
              color: 'var(--dark-sky)', background: 'var(--white)',
              lineHeight: 1.5,
            }}
          />
          <button
            className="btn btn-primary"
            onClick={handleFreeSubmit}
            disabled={!freeText.trim()}
            style={{ padding: '8px 18px', flexShrink: 0 }}
          >
            Submit
          </button>
        </div>
      )}

      {!submitted && (
        <p className="text-caption" style={{ color: 'var(--accent)', marginTop: 12, fontStyle: 'italic' }}>
          Select an answer above, or type additional context in the message box below.
        </p>
      )}
    </div>
  )
}


/* ── Recommendation card ─────────────────────────────────────────────── */
function RecommendationCard({ rec }) {
  const [copied, setCopied] = useState(false)
  const rank = RANK_CFG[rec.rank] || RANK_CFG.secondary

  const copyCode = (e) => {
    e.stopPropagation()
    if (!rec.test_code) return
    navigator.clipboard?.writeText(rec.test_code).then(() => {
      setCopied(true); setTimeout(() => setCopied(false), 1500)
    }).catch(() => {})
  }

  return (
    <div style={{
      background: 'var(--white)',
      border: '1px solid var(--accent3)',
      borderLeft: `4px solid ${rank.accentColor}`,
      borderRadius: 'var(--card-radius)',
      padding: '14px 18px',
    }}>
      {/* Title row */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="text-h4" style={{ color: 'var(--dark-sky)', fontWeight: 600, lineHeight: 1.25 }}>
            {rec.test_url
              ? <a href={rec.test_url} target="_blank" rel="noreferrer" style={{ color: 'var(--dark-sky)', textDecoration: 'none' }}>
                  {rec.test_name} <span style={{ fontSize: 11, color: 'var(--lab-blue)' }}>↗</span>
                </a>
              : rec.test_name}
          </div>
          {rec.test_code && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginTop: 5 }}>
              <code style={{
                fontSize: 11, fontFamily: "'Roboto Mono', monospace",
                padding: '2px 8px', borderRadius: 4,
                background: 'var(--bg-salt)', color: 'var(--accent2)',
                border: '1px solid var(--accent3)',
              }}>
                {rec.test_code}
              </code>
              <button onClick={copyCode} style={{
                fontSize: 10, color: copied ? 'var(--positive)' : 'var(--accent)',
                background: 'none', border: 'none', cursor: 'pointer', padding: '1px 4px',
                fontFamily: 'inherit',
              }} title="Copy order code">
                {copied ? '✓ Copied' : 'Copy'}
              </button>
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 5, flexShrink: 0, alignItems: 'flex-start', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <span className={`badge ${rank.badgeCls}`}>{rank.label}</span>
          {rec.appears_in_algorithm && (
            <span className="badge badge-source-algo" style={{ fontSize: 9 }}>In Algorithm</span>
          )}
        </div>
      </div>

      {/* Rationale */}
      {rec.rationale && (
        <p className="text-body-sm" style={{ color: 'var(--accent2)', marginTop: 9, lineHeight: 1.55 }}>
          {rec.rationale}
        </p>
      )}

      {/* Specimen / TAT — operational row */}
      {(rec.specimen || rec.tat) && (
        <div style={{ display: 'flex', gap: 16, marginTop: 10, flexWrap: 'wrap' }}>
          {rec.specimen && (
            <div style={{ fontSize: 12 }}>
              <span style={{ color: 'var(--accent)', fontWeight: 600, marginRight: 4 }}>Specimen:</span>
              <span style={{ color: 'var(--dark-sky)' }}>{rec.specimen}</span>
            </div>
          )}
          {rec.tat && (
            <div style={{ fontSize: 12 }}>
              <span style={{ color: 'var(--accent)', fontWeight: 600, marginRight: 4 }}>TAT:</span>
              <span style={{ color: 'var(--dark-sky)' }}>{rec.tat}</span>
            </div>
          )}
        </div>
      )}

      {/* Evidence coverage + source count footer */}
      <div style={{ display: 'flex', gap: 5, marginTop: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        {rec.evidence_coverage?.has_algorithm  && <span className="badge badge-source-algo"    style={{ fontSize: 10 }}>Algorithm</span>}
        {rec.evidence_coverage?.has_consult    && <span className="badge badge-source-consult" style={{ fontSize: 10 }}>Consult</span>}
        {rec.evidence_coverage?.has_directory  && <span className="badge badge-source-dir"     style={{ fontSize: 10 }}>Directory</span>}
        {rec.evidence_coverage?.has_fact_sheet && <span className="badge badge-source-fact"    style={{ fontSize: 10 }}>Fact Sheet</span>}
        {rec.source_count > 0 && (
          <span style={{ fontSize: 10, color: 'var(--accent3)', marginLeft: 'auto' }}>
            {rec.source_count} source{rec.source_count !== 1 ? 's' : ''}
          </span>
        )}
      </div>
    </div>
  )
}


/* ── Conflicts block ─────────────────────────────────────────────────── */
function ConflictsBlock({ conflicts }) {
  return (
    <div style={{
      marginTop: 14, padding: '12px 16px',
      background: '#FFF8F0', border: '1px solid #F5A623',
      borderRadius: 'var(--card-radius)',
    }}>
      <p className="text-body-sm" style={{ fontWeight: 700, color: '#8B5E00', marginBottom: 6 }}>
        ⚠ Source conflicts detected
      </p>
      {conflicts.map((c, i) => (
        <p key={i} className="text-caption" style={{ color: '#8B5E00', lineHeight: 1.5 }}>• {c}</p>
      ))}
    </div>
  )
}


/* ── Evidence gaps block ─────────────────────────────────────────────── */
function EvidenceGapsBlock({ gaps }) {
  return (
    <div style={{
      marginTop: 10, padding: '10px 14px',
      background: 'var(--bg-salt)', border: '1px solid var(--accent3)',
      borderRadius: 'var(--card-radius)',
    }}>
      <p className="text-caption" style={{ fontWeight: 700, color: 'var(--accent2)', marginBottom: 4 }}>
        Evidence gaps
      </p>
      {gaps.map((g, i) => (
        <p key={i} className="text-caption" style={{ color: 'var(--accent2)', lineHeight: 1.5 }}>• {g}</p>
      ))}
    </div>
  )
}


/* ══════════════════════════════════════════════════════════════════════════
   ReviewConcernsBlock (v1.2.0) — CriticAgent / ReviewPanel output
   ──────────────────────────────────────────────────────────────────────────
   Renders the clinical reviewer concerns with severity pills and reviewer
   attribution.  Collapsed by default unless the verdict is "escalate".
   ══════════════════════════════════════════════════════════════════════════ */
function ReviewConcernsBlock({ props }) {
  const {
    title = 'Clinical review',
    consensus_label,
    verdict,
    verdict_badge_cls,
    verdict_badge_label,
    review_summary,
    reviewer_count = 1,
    concerns = [],
    concern_count = 0,
    default_expanded = false,
    bg_token = 'var(--bg-salt)',
    border_token = 'var(--accent3)',
    show_attribution = true,
  } = props || {}

  const [open, setOpen] = useState(!!default_expanded)

  const SEVERITY_COLORS = {
    high:   { bg: '#FEE2E2', fg: '#7F1D1D', cls: 'badge-danger',   label: 'High' },
    medium: { bg: '#FEF3C7', fg: '#78350F', cls: 'badge-info',     label: 'Medium' },
    low:    { bg: '#F1F5F9', fg: '#334155', cls: 'badge-source-general', label: 'Low' },
  }

  return (
    <div style={{
      marginTop: 14,
      background: bg_token,
      border: `1px solid ${border_token}`,
      borderRadius: 'var(--card-radius)',
      padding: '12px 16px',
    }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', width: '100%', alignItems: 'center', gap: 10,
          background: 'none', border: 'none', padding: 0, cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <span className="text-label" style={{ color: 'var(--dark-sky)', fontWeight: 700 }}>
          {title}
        </span>
        {verdict_badge_label && (
          <span className={`badge ${verdict_badge_cls || 'badge-info'}`}>
            {verdict_badge_label}
          </span>
        )}
        {concern_count > 0 && (
          <span className="text-caption" style={{ color: 'var(--accent2)' }}>
            {concern_count} concern{concern_count === 1 ? '' : 's'}
          </span>
        )}
        <span className="text-caption" style={{ marginLeft: 'auto', color: 'var(--accent)' }}>
          {reviewer_count} reviewer{reviewer_count === 1 ? '' : 's'} · {consensus_label || ''} {open ? '▲' : '▼'}
        </span>
      </button>

      {review_summary && (
        <p className="text-caption" style={{ marginTop: 6, color: 'var(--accent2)', fontStyle: 'italic' }}>
          {review_summary}
        </p>
      )}

      {open && concerns.length > 0 && (
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {concerns.map((c, i) => {
            const sev = SEVERITY_COLORS[c.severity] || SEVERITY_COLORS.low
            return (
              <div
                key={i}
                style={{
                  background: sev.bg,
                  borderLeft: `3px solid ${sev.fg}`,
                  borderRadius: 4,
                  padding: '8px 10px',
                }}
              >
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginBottom: 4 }}>
                  <span className={`badge ${sev.cls}`}>{sev.label}</span>
                  {c.category && (
                    <span className="text-caption" style={{ color: sev.fg, textTransform: 'capitalize' }}>
                      {String(c.category).replace(/_/g, ' ')}
                    </span>
                  )}
                  {show_attribution && c.reviewer && (
                    <span className="text-caption" style={{ color: sev.fg, opacity: 0.75 }}>
                      · {c.reviewer}
                    </span>
                  )}
                </div>
                <p className="text-body-sm" style={{ color: sev.fg, lineHeight: 1.45, margin: 0 }}>
                  {c.claim}
                </p>
                {c.suggested_fix && (
                  <p className="text-caption" style={{ color: sev.fg, lineHeight: 1.45, marginTop: 4, opacity: 0.9 }}>
                    <strong>Fix:</strong> {c.suggested_fix}
                  </p>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}


/* ══════════════════════════════════════════════════════════════════════════
   FidelityReportBlock (v1.2.0) — AlgorithmFidelityCritic output
   ──────────────────────────────────────────────────────────────────────────
   Renders the fidelity score, tier badge, missing elements, per-check
   breakdown, and optional vision result. Collapses by default for match,
   expands for partial / divergent.
   ══════════════════════════════════════════════════════════════════════════ */
function FidelityReportBlock({ props }) {
  const {
    title = 'Algorithm fidelity',
    score = 0,
    tier = 'none',
    tier_label,
    tier_icon,
    tier_badge_cls,
    bg_token,
    border_token,
    recommendation,
    missing_elements = [],
    checks = [],
    vision,
    default_expanded = false,
    source_pdf_asset_id,
    show_source_pdf_cta,
    /* v1.2.0 multimodal fields — optional; legacy payloads omit them */
    vision_consensus,
    vision_by_provider,
  } = props || {}

  const [open, setOpen] = useState(!!default_expanded)

  const pdfLink = source_pdf_asset_id
    ? `/api/assets/pdf/${source_pdf_asset_id}`
    : null

  /* Consensus chip config — surfaces "both-models" comparison status */
  const CONSENSUS_CHIP = {
    agreement:    { cls: 'badge badge-positive', label: '✓ Two-model agreement',   color: 'var(--positive)' },
    partial:      { cls: 'badge badge-info',     label: '◐ Partial agreement',     color: 'var(--lab-blue)' },
    disagreement: { cls: 'badge badge-danger',   label: '⚠ Models disagree',       color: 'var(--danger)'   },
    error:        { cls: 'badge badge-source-general', label: 'Vision unavailable', color: 'var(--accent2)'  },
  }
  const consensusChip = vision_consensus ? CONSENSUS_CHIP[vision_consensus] : null

  return (
    <div style={{
      marginTop: 12,
      background: bg_token || 'var(--bg-salt)',
      border: `1px solid ${border_token || 'var(--accent3)'}`,
      borderRadius: 'var(--card-radius)',
      padding: '12px 16px',
    }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', width: '100%', alignItems: 'center', gap: 10,
          background: 'none', border: 'none', padding: 0, cursor: 'pointer',
          textAlign: 'left', flexWrap: 'wrap',
        }}
      >
        <span style={{ fontSize: 16 }}>{tier_icon || '·'}</span>
        <span className="text-label" style={{ color: 'var(--dark-sky)', fontWeight: 700 }}>
          {title}
        </span>
        <span className={`badge ${tier_badge_cls || 'badge-info'}`}>
          {tier_label || tier} · {score}/100
        </span>
        {consensusChip && (
          <span className={consensusChip.cls}>
            {consensusChip.label}
          </span>
        )}
        <span className="text-caption" style={{ marginLeft: 'auto', color: 'var(--accent)' }}>
          {open ? '▲' : '▼'}
        </span>
      </button>

      {/* Prominent disagreement banner — always visible, not gated on 'open' */}
      {vision_consensus === 'disagreement' && (
        <div style={{
          marginTop: 8, padding: '8px 10px',
          background: 'var(--danger-container)',
          border: '1px solid var(--danger)',
          borderRadius: 4,
        }}>
          <p className="text-body-sm" style={{ color: 'var(--danger)', fontWeight: 600, margin: 0, lineHeight: 1.45 }}>
            ⚠ Claude and GPT-4o disagree about whether the rendered flowchart preserves the source structure.
          </p>
          <p className="text-caption" style={{ color: 'var(--danger)', marginTop: 4, lineHeight: 1.45 }}>
            Two independent models returned different verdicts — we recommend reviewing the source ARUP PDF before acting on this algorithm.
          </p>
        </div>
      )}

      {missing_elements.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <p className="text-caption" style={{ color: 'var(--accent2)', fontWeight: 600, marginBottom: 2 }}>
            Missing from rendered flowchart:
          </p>
          {missing_elements.slice(0, 4).map((m, i) => (
            <p key={i} className="text-caption" style={{ color: 'var(--accent2)', lineHeight: 1.4 }}>
              · {m}
            </p>
          ))}
          {missing_elements.length > 4 && (
            <p className="text-caption" style={{ color: 'var(--accent2)', fontStyle: 'italic' }}>
              … and {missing_elements.length - 4} more
            </p>
          )}
        </div>
      )}

      {show_source_pdf_cta && pdfLink && (
        <div style={{ marginTop: 10 }}>
          <a
            href={pdfLink}
            target="_blank"
            rel="noopener noreferrer"
            className="text-body-sm"
            style={{
              display: 'inline-block',
              color: 'var(--lab-blue)',
              textDecoration: 'underline',
              fontWeight: 600,
            }}
          >
            Open source ARUP PDF →
          </a>
        </div>
      )}

      {open && (
        <div style={{ marginTop: 10 }}>
          <p className="text-caption" style={{ color: 'var(--accent2)', fontWeight: 600, marginBottom: 6 }}>
            Per-check breakdown:
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: 4, fontSize: 12 }}>
            <div className="text-caption" style={{ fontWeight: 600 }}>Check</div>
            <div className="text-caption" style={{ fontWeight: 600, textAlign: 'right' }}>Earned</div>
            <div className="text-caption" style={{ fontWeight: 600, textAlign: 'right' }}>Weight</div>
            {checks.map((c, i) => (
              <Fragment key={i}>
                <div className="text-caption" style={{ color: c.pass ? 'var(--positive)' : 'var(--danger)' }}>
                  {c.pass ? '✓' : '✗'} {String(c.check).replace(/_/g, ' ')}
                </div>
                <div className="text-caption" style={{ textAlign: 'right', color: 'var(--dark-sky)' }}>
                  {c.earned}
                </div>
                <div className="text-caption" style={{ textAlign: 'right', color: 'var(--accent2)' }}>
                  {c.weight}
                </div>
              </Fragment>
            ))}
          </div>

          {vision && (
            <div style={{
              marginTop: 12, padding: '8px 10px',
              background: 'var(--bg-salt)',
              border: '1px dashed var(--accent3)',
              borderRadius: 4,
            }}>
              <p className="text-caption" style={{ fontWeight: 600, color: 'var(--lab-blue)', marginBottom: 4 }}>
                Visual check vs source PDF{vision_by_provider ? ' (merged from 2 models)' : ''}
              </p>
              {vision.error ? (
                <p className="text-caption" style={{ color: 'var(--accent2)' }}>
                  (vision check unavailable: {vision.error})
                </p>
              ) : (
                <>
                  {vision.structural_notes && (
                    <p className="text-caption" style={{ color: 'var(--dark-sky)', lineHeight: 1.45 }}>
                      {vision.structural_notes}
                    </p>
                  )}
                  {vision.missing_branches && vision.missing_branches.length > 0 && (
                    <p className="text-caption" style={{ color: 'var(--accent2)', marginTop: 4 }}>
                      Missing branches: {vision.missing_branches.join('; ')}
                    </p>
                  )}
                  {vision.missing_tests && vision.missing_tests.length > 0 && (
                    <p className="text-caption" style={{ color: 'var(--accent2)' }}>
                      Missing tests: {vision.missing_tests.join(', ')}
                    </p>
                  )}
                </>
              )}

              {/* Per-provider breakdown — only when two models ran */}
              {vision_by_provider && (
                <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {['claude', 'openai'].map(p => {
                    const pr = vision_by_provider[p]
                    if (!pr) return null
                    const providerLabel = p === 'claude' ? 'Claude Haiku' : 'GPT-4o-mini'
                    const hasError = Boolean(pr._error)
                    return (
                      <div
                        key={p}
                        style={{
                          padding: '6px 8px',
                          background: 'var(--bg-primary)',
                          border: '1px solid var(--accent3)',
                          borderRadius: 3,
                        }}
                      >
                        <p className="text-caption" style={{ fontWeight: 600, color: 'var(--dark-sky)', marginBottom: 2 }}>
                          {providerLabel}:{' '}
                          <span style={{
                            color: hasError
                              ? 'var(--accent2)'
                              : (pr.preserved_structure ? 'var(--positive)' : 'var(--danger)'),
                            fontWeight: 500,
                          }}>
                            {hasError
                              ? 'unavailable'
                              : (pr.preserved_structure ? 'preserved' : 'NOT preserved')}
                          </span>
                        </p>
                        {hasError ? (
                          <p className="text-caption" style={{ color: 'var(--accent2)', lineHeight: 1.4 }}>
                            {pr._error}
                          </p>
                        ) : (
                          <>
                            {pr.structural_notes && (
                              <p className="text-caption" style={{ color: 'var(--dark-sky)', lineHeight: 1.4 }}>
                                {pr.structural_notes}
                              </p>
                            )}
                            {(pr.missing_branches?.length > 0 || pr.missing_tests?.length > 0) && (
                              <p className="text-caption" style={{ color: 'var(--accent2)', lineHeight: 1.4 }}>
                                Missing: {[
                                  ...(pr.missing_branches || []),
                                  ...(pr.missing_tests || []).map(t => `test ${t}`),
                                ].join('; ')}
                              </p>
                            )}
                          </>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {recommendation && recommendation !== 'proceed' && (
            <p className="text-caption" style={{ marginTop: 10, color: 'var(--accent2)', fontStyle: 'italic' }}>
              Recommendation: {String(recommendation).replace(/_/g, ' ')}
            </p>
          )}
        </div>
      )}
    </div>
  )
}


/* ── Citations table ─────────────────────────────────────────────────── */
function CitationsTable({ citations }) {
  const [open, setOpen] = useState(true)
  return (
    <div style={{ marginTop: 20 }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', width: '100%', alignItems: 'center', gap: 8,
          padding: '8px 0', background: 'none', cursor: 'pointer',
          borderBottom: '1px solid var(--accent)',
        }}
      >
        <span style={{
          width: 18, height: 18, borderRadius: 4,
          background: 'var(--secondary)', color: 'var(--white)',
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 10, fontWeight: 700, flexShrink: 0,
        }}>✓</span>
        <span className="text-body-sm" style={{ fontWeight: 700, color: 'var(--dark-sky)' }}>
          Supporting sources ({citations.length}) — ARUP authoritative
        </span>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--accent)' }}>
          {open ? '▲' : '▼'}
        </span>
      </button>

      {open && (
        <table className="arup-table" style={{ marginTop: 0, fontSize: 13 }}>
          <thead>
            <tr>
              <th style={{ width: 28 }}>#</th>
              <th style={{ width: 110 }}>Source type</th>
              <th>Document</th>
              <th>Excerpt</th>
            </tr>
          </thead>
          <tbody>
            {citations.map((cit, i) => {
              const src    = SOURCE_CFG[cit.source_type] || SOURCE_CFG['General']
              const isEven = i % 2 !== 0
              return (
                <tr key={i}>
                  <td style={{ fontWeight: 700, color: 'var(--accent2)', background: isEven ? 'var(--bg-salt)' : 'var(--bg-primary)' }}>
                    {cit.number}
                  </td>
                  <td style={{ background: isEven ? 'var(--bg-salt)' : 'var(--bg-primary)' }}>
                    <span className={src.cls}>{src.label}</span>
                    {cit.source_role && (
                      <span style={{ display: 'block', fontSize: 10, color: 'var(--accent)', marginTop: 2, fontStyle: 'italic' }}>
                        {cit.source_role}
                      </span>
                    )}
                  </td>
                  <td style={{ fontWeight: 500, color: 'var(--dark-sky)', background: isEven ? 'var(--bg-salt)' : 'var(--bg-primary)', fontSize: 12 }}>
                    {cit.document}
                  </td>
                  <td style={{ background: isEven ? 'var(--bg-salt)' : 'var(--bg-primary)', fontStyle: 'italic', color: 'var(--accent2)', fontSize: 12, lineHeight: 1.5 }}>
                    {cit.excerpt && `"${cit.excerpt}"`}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}


/* ── Confidence bar ──────────────────────────────────────────────────── */
function ConfidenceBar({ confidence }) {
  const pct        = confidence.score || 0
  const barColor   = pct >= 70 ? 'var(--positive)' : pct >= 40 ? 'var(--primary)' : 'var(--danger)'
  const textColor  = pct >= 70 ? 'var(--positive)' : pct >= 40 ? 'var(--primary)' : 'var(--danger)'
  const levelLabel = pct >= 70 ? 'High confidence' : pct >= 40 ? 'Moderate confidence' : 'Low confidence'

  return (
    <div style={{ marginTop: 16, padding: '12px 16px', background: 'var(--bg-salt)', border: '1px solid var(--accent3)', borderRadius: 'var(--card-radius)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span className="text-caption" style={{ color: 'var(--accent2)', whiteSpace: 'nowrap', fontWeight: 600 }}>
          Recommendation confidence
        </span>
        <div style={{ flex: 1, height: 6, borderRadius: 3, background: 'var(--accent3)', overflow: 'hidden' }}>
          <div style={{ height: '100%', width: `${pct}%`, background: barColor, borderRadius: 3, transition: 'width .5s ease' }} />
        </div>
        <span style={{ fontSize: 13, fontWeight: 700, color: textColor, minWidth: 34, textAlign: 'right' }}>{pct}%</span>
        <span className="text-caption" style={{ color: textColor, whiteSpace: 'nowrap', fontWeight: 600 }}>{levelLabel}</span>
        {confidence.needs_review && (
          <span className="badge badge-danger" style={{ flexShrink: 0 }}>Review recommended</span>
        )}
      </div>

      {confidence.factors?.length > 0 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
          {confidence.factors
            .filter(f => !f.includes('questions asked'))
            .map((f, i) => (
              <span key={i} className="badge badge-neutral" style={{
                background: 'var(--white)',
                border: '1px solid var(--accent3)',
                color: f.startsWith('⚠') ? 'var(--danger)' : 'var(--accent2)',
                fontSize: 11,
                fontWeight: f.startsWith('⚠') ? 600 : 400,
              }}>
                {f}
              </span>
            ))}
        </div>
      )}

      {/* Per-test scores */}
      {confidence.per_test_scores?.length > 0 && (
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 4 }}>
          {confidence.per_test_scores.map((ts, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className="text-caption" style={{ color: 'var(--accent2)', minWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {ts.test_name}
              </span>
              <div style={{ flex: 1, height: 4, borderRadius: 2, background: 'var(--accent3)', overflow: 'hidden' }}>
                <div style={{
                  height: '100%', width: `${ts.score}%`,
                  background: ts.score >= 60 ? 'var(--positive)' : ts.score >= 35 ? 'var(--primary)' : 'var(--danger)',
                  borderRadius: 2,
                }} />
              </div>
              <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--accent2)', minWidth: 32 }}>{ts.level}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}


/* ── Follow-up questions — populate input, do NOT auto-send ──────────── */
function FollowUpBlock({ questions, onDraft }) {
  const [drafted, setDrafted] = useState(null)

  const handleDraft = (q) => {
    setDrafted(q)
    if (onDraft) onDraft(q)
  }

  return (
    <div style={{ marginTop: 16 }}>
      <p className="text-label" style={{ color: 'var(--accent)', marginBottom: 8 }}>
        Suggested follow-up questions:
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        {questions.map((q, i) => {
          const isActive = drafted === q
          return (
            <button
              key={i}
              onClick={() => handleDraft(q)}
              style={{
                display: 'flex', alignItems: 'flex-start', gap: 8,
                padding: '7px 14px',
                background: isActive ? 'var(--bg-salt)' : 'transparent',
                border: `1px solid ${isActive ? 'var(--accent)' : 'var(--accent3)'}`,
                borderRadius: 'var(--card-radius)',
                cursor: 'pointer', textAlign: 'left',
                fontSize: 13, color: 'var(--accent2)', lineHeight: 1.45,
                fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
                transition: 'all .12s',
              }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.background = 'var(--bg-salt)' }}
              onMouseLeave={e => { if (!isActive) { e.currentTarget.style.borderColor = 'var(--accent3)'; e.currentTarget.style.background = 'transparent' } }}
              title="Click to copy this question to the input box"
            >
              <span style={{ fontSize: 10, marginTop: 3, opacity: 0.5, flexShrink: 0 }}>✎</span>
              <span style={{ flex: 1 }}>{q}</span>
              {isActive && (
                <span style={{ fontSize: 10, color: 'var(--positive)', fontWeight: 600, flexShrink: 0, marginTop: 2 }}>
                  In input ↓
                </span>
              )}
            </button>
          )
        })}
      </div>
      <p className="text-caption" style={{ color: 'var(--accent3)', marginTop: 6, fontStyle: 'italic' }}>
        Click a question to copy it to the message input for editing before sending.
      </p>
    </div>
  )
}


/* ── ThumbsUpIcon ────────────────────────────────────────────────────────── */
function ThumbsUpIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M7 22V11M2 13v7a2 2 0 002 2h11.5a2 2 0 001.97-1.67l1.5-9A2 2 0 0017 9h-5V5a3 3 0 00-3-3L7 11"
        stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

/* ── ThumbsDownIcon ──────────────────────────────────────────────────────── */
function ThumbsDownIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M17 2H6.5a2 2 0 00-1.97 1.67l-1.5 9A2 2 0 005 15h5v4a3 3 0 003 3l2-6h6M22 2v9a2 2 0 01-2 2h-3"
        stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

/* ── FeedbackButton — thumbs up / down with single-selection state ───────── */
function FeedbackButton({ sessionId, messageIndex }) {
  const [voted, setVoted] = useState(null) // null | 1 | -1

  const handleVote = async (rating) => {
    if (voted !== null || !sessionId) return
    setVoted(rating)
    try {
      await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, message_index: messageIndex, rating }),
      })
    } catch { /* fire-and-forget */ }
  }

  const btnStyle = (rating) => ({
    display: 'flex', alignItems: 'center',
    padding: '3px 6px',
    background: 'none', border: 'none',
    borderRadius: 4,
    cursor: voted !== null ? 'default' : 'pointer',
    color: voted === rating
      ? (rating === 1 ? 'var(--positive)' : 'var(--danger)')
      : 'var(--accent)',
    transition: 'color .15s, background .15s',
    flexShrink: 0,
  })

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
      <button
        onClick={() => handleVote(1)}
        title="Helpful"
        aria-label="Mark as helpful"
        style={btnStyle(1)}
        onMouseEnter={e => { if (voted === null) e.currentTarget.style.background = 'var(--neutral-200)' }}
        onMouseLeave={e => { e.currentTarget.style.background = 'none' }}
      >
        <ThumbsUpIcon />
      </button>
      <button
        onClick={() => handleVote(-1)}
        title="Not helpful"
        aria-label="Mark as not helpful"
        style={btnStyle(-1)}
        onMouseEnter={e => { if (voted === null) e.currentTarget.style.background = 'var(--neutral-200)' }}
        onMouseLeave={e => { e.currentTarget.style.background = 'none' }}
      >
        <ThumbsDownIcon />
      </button>
    </div>
  )
}

/* ── CopyIcon — two overlapping rectangles (ChatGPT-style clipboard) ─── */
function CopyIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true">
      {/* back page — offset top-right */}
      <rect x="4" y="0" width="9" height="9" rx="1.5" stroke="currentColor" strokeWidth="1.25"/>
      {/* front page — offset bottom-left, white fill covers the overlap */}
      <rect x="0" y="4" width="9" height="9" rx="1.5" fill="white" stroke="currentColor" strokeWidth="1.25"/>
    </svg>
  )
}

/* ── CheckIcon — success tick ──────────────────────────────────────────── */
function CheckIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true">
      <polyline
        points="1.5,7 5,10.5 11.5,2.5"
        stroke="currentColor" strokeWidth="1.7"
        strokeLinecap="round" strokeLinejoin="round"
      />
    </svg>
  )
}

/* ── CopyButton — clipboard action with lightweight "Copied" feedback ─── */
function CopyButton({ getTextFn }) {
  const [copyState, setCopyState] = useState('idle') // 'idle' | 'copied' | 'error'

  const handleCopy = async () => {
    if (copyState !== 'idle') return
    const text = typeof getTextFn === 'function' ? getTextFn() : ''
    let next = 'error'
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
      } else {
        // Legacy execCommand fallback for browsers without Clipboard API
        const ta = document.createElement('textarea')
        ta.value = text
        ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0'
        document.body.appendChild(ta)
        ta.focus()
        ta.select()
        const ok = document.execCommand('copy')
        document.body.removeChild(ta)
        if (!ok) throw new Error('execCommand copy failed')
      }
      next = 'copied'
    } catch { /* next stays 'error' */ }
    setCopyState(next)
    setTimeout(() => setCopyState('idle'), 2000)
  }

  const isCopied = copyState === 'copied'
  const isError  = copyState === 'error'

  return (
    <button
      onClick={handleCopy}
      title={isCopied ? 'Copied!' : isError ? 'Copy failed — please try again' : 'Copy response'}
      aria-label={isCopied ? 'Copied!' : 'Copy response'}
      style={{
        display: 'flex', alignItems: 'center', gap: 4,
        padding: '3px 6px',
        background: 'none', border: 'none',
        borderRadius: 4,
        cursor: isCopied ? 'default' : 'pointer',
        color: isCopied ? 'var(--positive)' : isError ? 'var(--danger)' : 'var(--accent)',
        transition: 'color .15s, background .15s',
        flexShrink: 0,
      }}
      onMouseEnter={e => { if (copyState === 'idle') e.currentTarget.style.background = 'var(--neutral-200)' }}
      onMouseLeave={e => { e.currentTarget.style.background = 'none' }}
    >
      {isCopied ? <CheckIcon /> : <CopyIcon />}
      {isCopied && (
        <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.02em', lineHeight: 1 }}>
          Copied
        </span>
      )}
    </button>
  )
}

/* ── PdfIcon — document icon for PDF export ────────────────────────────── */
function PdfIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"
        stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"
        stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

/* ── Scrollable expansion helpers for PDF export ─────────────────────────
   html2canvas only captures what's visible inside scroll containers. Before
   we rasterise, we walk every descendant and — for any element that is
   actually scrolling (scrollHeight > clientHeight) or has a max-height /
   overflow that could clip content — we temporarily set it to unconstrained
   dimensions. After capture we restore every mutated style exactly.

   Also briefly expands common collapsible buttons (clicks them open) so the
   citations table, review concerns, and fidelity report are included in full.
   ════════════════════════════════════════════════════════════════════════ */
function expandScrollablesAndCollapsibles(root) {
  if (!root) return { saved: [], clicked: [] }

  const saved = []
  const clicked = []

  // ── 1. Expand scroll containers ──────────────────────────────────────
  // Walk every descendant element and temporarily remove scroll-clipping.
  const all = root.querySelectorAll('*')
  all.forEach(el => {
    try {
      const cs = window.getComputedStyle(el)
      const overflowY = cs.overflowY
      const overflowX = cs.overflowX
      const overflow  = cs.overflow
      const isScrolling =
        el.scrollHeight > el.clientHeight + 1 ||
        el.scrollWidth  > el.clientWidth  + 1
      const hasClipping =
        /(auto|scroll|hidden)/.test(overflowY) ||
        /(auto|scroll|hidden)/.test(overflowX) ||
        /(auto|scroll|hidden)/.test(overflow)
      const hasMaxHeight =
        cs.maxHeight !== 'none' && cs.maxHeight !== '0px'

      if (!isScrolling && !hasClipping && !hasMaxHeight) return

      // Snapshot what we're about to mutate
      saved.push({
        el,
        overflow:       el.style.overflow,
        overflowX:      el.style.overflowX,
        overflowY:      el.style.overflowY,
        maxHeight:      el.style.maxHeight,
        maxWidth:       el.style.maxWidth,
        height:         el.style.height,
      })

      // Unclip
      el.style.overflow  = 'visible'
      el.style.overflowX = 'visible'
      el.style.overflowY = 'visible'
      el.style.maxHeight = 'none'
      el.style.maxWidth  = 'none'
      // If there was a hard height constraint forcing a scroll, grow to fit
      if (isScrolling && el.scrollHeight > el.clientHeight) {
        el.style.height = el.scrollHeight + 'px'
      }
    } catch (_) { /* defensive — never fail the export over one element */ }
  })

  // ── 2. Open collapsibles (citations / review concerns / fidelity card) ──
  // Anything with an aria-expanded="false" or a ▼/▶/▶︎ chevron inside a
  // button is clicked open. React state updates are batched so we wait a
  // frame afterwards before taking the snapshot.
  const buttons = root.querySelectorAll('button')
  buttons.forEach(btn => {
    try {
      const ariaExp = btn.getAttribute('aria-expanded')
      const textContent = (btn.textContent || '').trim()
      const hasCollapsedMarker =
        ariaExp === 'false' ||
        textContent.endsWith('▼') ||
        textContent.endsWith('▶') ||
        textContent.endsWith('▶︎')
      if (!hasCollapsedMarker) return
      btn.click()
      clicked.push(btn)
    } catch (_) { /* ignore */ }
  })

  return { saved, clicked }
}

function restoreScrollablesAndCollapsibles({ saved, clicked }) {
  // Re-close collapsibles we opened (so the UI returns to its prior state)
  ;(clicked || []).forEach(btn => { try { btn.click() } catch (_) { /* ignore */ } })
  // Restore every mutated style
  ;(saved || []).forEach(({ el, overflow, overflowX, overflowY, maxHeight, maxWidth, height }) => {
    try {
      el.style.overflow  = overflow
      el.style.overflowX = overflowX
      el.style.overflowY = overflowY
      el.style.maxHeight = maxHeight
      el.style.maxWidth  = maxWidth
      el.style.height    = height
    } catch (_) { /* ignore */ }
  })
}


/* ── ExportPdfButton — export response as a rich visual PDF ────────────────
   v1.2.0: produces a pixel-accurate rendering of the response bubble with
   colors, badges, algorithm flowchart, review concerns, and fidelity cards
   fully preserved. Uses html2canvas to rasterise the DOM and jsPDF to wrap
   the image (multi-page if needed). html2canvas is dynamically imported so
   it doesn't bloat the main bundle — it only loads when the user clicks
   export. Falls back to text-only rendering if the visual capture fails.
   ═══════════════════════════════════════════════════════════════════════════ */
function ExportPdfButton({ getElementFn, getTextFn, messageIndex, sessionId }) {
  // States: 'idle' | 'exporting' | 'exported' | 'error'
  const [exportState, setExportState] = useState('idle')

  const buildFilename = () => {
    const dateStr = new Date().toISOString().slice(0, 10)
    const timeStr = new Date().toTimeString().slice(0, 8).replace(/:/g, '-')
    const idPart = sessionId ? `_${sessionId.slice(0, 8)}` : ''
    const msgPart = messageIndex !== undefined ? `_msg${messageIndex}` : ''
    return `arup-response${idPart}${msgPart}_${dateStr}_${timeStr}.pdf`
  }

  // Legacy text fallback — used if the rich visual render fails
  const exportAsText = (text) => {
    const doc = new jsPDF()
    const pageWidth = doc.internal.pageSize.getWidth()
    const margin = 15
    const maxWidth = pageWidth - (margin * 2)
    doc.setFontSize(14)
    doc.setFont(undefined, 'bold')
    doc.text('ARUP AI Test Advisor Response', margin, margin)
    doc.setFontSize(9)
    doc.setFont(undefined, 'normal')
    doc.setTextColor(100)
    doc.text(new Date().toLocaleString(), margin, margin + 7)
    doc.setFontSize(10)
    doc.setTextColor(0)
    const lines = doc.splitTextToSize(text, maxWidth)
    doc.text(lines, margin, margin + 15)
    doc.save(buildFilename())
  }

  const handleExport = async () => {
    if (exportState !== 'idle') return

    const element = typeof getElementFn === 'function' ? getElementFn() : null
    if (!element) {
      setExportState('error')
      setTimeout(() => setExportState('idle'), 2000)
      return
    }

    setExportState('exporting')

    try {
      // Lazy import — loads html2canvas only on first click (~45KB gzipped)
      const html2canvasMod = await import('html2canvas')
      const html2canvas = html2canvasMod.default || html2canvasMod

      // ── v1.2.1 — Expand any scrollable/clipped containers BEFORE capture ──
      // This ensures the algorithm flowchart (which lives inside a scrollable
      // frame on screen) is rendered in full, along with any collapsed
      // citations/review/fidelity sections.
      const snapshot = expandScrollablesAndCollapsibles(element)

      // Let the DOM reflow twice — once for our style mutations, once for any
      // React re-renders triggered by the clicked collapsibles.
      await new Promise(requestAnimationFrame)
      await new Promise(requestAnimationFrame)

      let canvas
      try {
        // Rasterise the DOM node — scale: 2 gives retina-quality output,
        // backgroundColor ensures the PDF background is clean white even when
        // the bubble itself has a tinted surface via CSS variables.
        canvas = await html2canvas(element, {
          scale: 2,
          useCORS: true,
          allowTaint: false,
          backgroundColor: '#ffffff',
          logging: false,
          imageTimeout: 15000,
          // Fix html2canvas quirks with transparent overlays + shadows
          removeContainer: true,
          // Preserve any inline SVG (the algorithm flowchart) at full fidelity
          foreignObjectRendering: false,
          // Explicitly size the canvas to the element's FULL unclipped height
          // so we don't rely on html2canvas inferring it from layout.
          windowWidth:  document.documentElement.scrollWidth,
          windowHeight: Math.max(
            document.documentElement.scrollHeight,
            element.scrollHeight,
          ),
          height: element.scrollHeight,
          width:  element.scrollWidth,
        })
      } finally {
        // Always restore, even if html2canvas threw
        restoreScrollablesAndCollapsibles(snapshot)
      }

      // Build the PDF — US Letter portrait, points as the unit so jsPDF's
      // built-in fonts size predictably against html2canvas pixel output.
      const doc = new jsPDF({
        orientation: 'portrait',
        unit: 'pt',
        format: 'letter',
        compress: true,
      })
      const pageWidth  = doc.internal.pageSize.getWidth()   // 612pt
      const pageHeight = doc.internal.pageSize.getHeight()  // 792pt
      const margin = 36                                     // 0.5" margin

      // Header on page 1 — title + timestamp
      doc.setFontSize(14)
      doc.setFont(undefined, 'bold')
      doc.setTextColor(20, 20, 20)
      doc.text('ARUP AI Test Advisor Response', margin, margin + 4)

      doc.setFontSize(9)
      doc.setFont(undefined, 'normal')
      doc.setTextColor(110, 110, 110)
      doc.text(new Date().toLocaleString(), margin, margin + 20)
      if (sessionId) {
        doc.text(`Session: ${sessionId.slice(0, 12)}`,
          pageWidth - margin - doc.getTextWidth(`Session: ${sessionId.slice(0, 12)}`),
          margin + 20)
      }

      // Horizontal rule below header
      doc.setDrawColor(174, 19, 42)       // ARUP primary red
      doc.setLineWidth(1.2)
      doc.line(margin, margin + 28, pageWidth - margin, margin + 28)

      // Image layout — full-width, aspect-preserving
      const contentTopFirstPage = margin + 40
      const contentTopOtherPages = margin
      const contentBottom = pageHeight - margin

      const pdfContentWidth = pageWidth - (margin * 2)
      const canvasAspect    = canvas.height / canvas.width
      const scaledImgHeight = pdfContentWidth * canvasAspect

      // Single-page fast path
      if (scaledImgHeight <= (contentBottom - contentTopFirstPage)) {
        const imgData = canvas.toDataURL('image/png')
        doc.addImage(imgData, 'PNG',
          margin, contentTopFirstPage,
          pdfContentWidth, scaledImgHeight,
          undefined, 'FAST')
      } else {
        // Multi-page slicing — split the canvas into page-sized chunks.
        //
        // We compute how many canvas pixels fit in one PDF page's vertical
        // content area, then iterate slicing from top to bottom. First page
        // has a smaller content area because of the header.
        const firstPageContentHeightPdf = contentBottom - contentTopFirstPage
        const otherPageContentHeightPdf = contentBottom - contentTopOtherPages

        // Canvas pixels per PDF point (same ratio in both axes because of
        // aspect-preserving scale-to-width)
        const canvasPxPerPdfPoint = canvas.width / pdfContentWidth

        const firstPagePxHeight = Math.floor(firstPageContentHeightPdf * canvasPxPerPdfPoint)
        const otherPagePxHeight = Math.floor(otherPageContentHeightPdf * canvasPxPerPdfPoint)

        let cursorPx = 0
        let pageIndex = 0
        while (cursorPx < canvas.height) {
          const isFirst = pageIndex === 0
          const slicePxCapacity = isFirst ? firstPagePxHeight : otherPagePxHeight
          const actualSlicePx = Math.min(slicePxCapacity, canvas.height - cursorPx)

          // Render this slice to its own canvas
          const sliceCanvas = document.createElement('canvas')
          sliceCanvas.width = canvas.width
          sliceCanvas.height = actualSlicePx
          const ctx = sliceCanvas.getContext('2d')
          ctx.fillStyle = '#ffffff'
          ctx.fillRect(0, 0, sliceCanvas.width, sliceCanvas.height)
          ctx.drawImage(canvas, 0, -cursorPx)

          const slicePdfHeight = actualSlicePx / canvasPxPerPdfPoint
          const sliceImgData = sliceCanvas.toDataURL('image/png')

          if (!isFirst) doc.addPage()
          const yOffset = isFirst ? contentTopFirstPage : contentTopOtherPages
          doc.addImage(sliceImgData, 'PNG',
            margin, yOffset,
            pdfContentWidth, slicePdfHeight,
            undefined, 'FAST')

          cursorPx += actualSlicePx
          pageIndex += 1
        }
      }

      // Footer on every page — page number + disclaimer
      const totalPages = doc.internal.getNumberOfPages()
      for (let p = 1; p <= totalPages; p++) {
        doc.setPage(p)
        doc.setFontSize(8)
        doc.setFont(undefined, 'italic')
        doc.setTextColor(140, 140, 140)
        const footer = 'Grounded in uploaded ARUP content. This tool supports — but does not replace — independent clinical review.'
        doc.text(footer, margin, pageHeight - (margin / 2), { maxWidth: pdfContentWidth - 60 })
        const pageLabel = `Page ${p} of ${totalPages}`
        doc.text(pageLabel,
          pageWidth - margin - doc.getTextWidth(pageLabel),
          pageHeight - (margin / 2))
      }

      doc.save(buildFilename())
      setExportState('exported')
    } catch (err) {
      console.warn('Visual PDF export failed, attempting text fallback:', err)
      // Fallback: text-only export (preserves the previous behaviour)
      try {
        const text = typeof getTextFn === 'function' ? getTextFn() : ''
        if (text && text.trim()) {
          exportAsText(text)
          setExportState('exported')
        } else {
          setExportState('error')
        }
      } catch (fallbackErr) {
        console.error('Text fallback also failed:', fallbackErr)
        setExportState('error')
      }
    }
    setTimeout(() => setExportState('idle'), 2500)
  }

  const isExporting = exportState === 'exporting'
  const isExported  = exportState === 'exported'
  const isError     = exportState === 'error'

  const tooltipLabel = isExporting ? 'Rendering PDF…'
                    : isExported  ? 'Exported!'
                    : isError     ? 'Export failed — please try again'
                                  : 'Export as PDF'

  return (
    <button
      onClick={handleExport}
      disabled={isExporting}
      title={tooltipLabel}
      aria-label={tooltipLabel}
      style={{
        display: 'flex', alignItems: 'center', gap: 4,
        padding: '3px 6px',
        background: 'none', border: 'none',
        borderRadius: 4,
        cursor: isExporting ? 'wait' : (isExported ? 'default' : 'pointer'),
        color: isExported  ? 'var(--positive)'
            : isError     ? 'var(--danger)'
            : isExporting ? 'var(--accent2)'
                          : 'var(--accent)',
        opacity: isExporting ? 0.7 : 1,
        transition: 'color .15s, background .15s, opacity .15s',
        flexShrink: 0,
      }}
      onMouseEnter={e => { if (exportState === 'idle') e.currentTarget.style.background = 'var(--neutral-200)' }}
      onMouseLeave={e => { e.currentTarget.style.background = 'none' }}
    >
      {isExported  ? <CheckIcon />
       : isExporting ? <SpinnerIcon />
       : <PdfIcon />}
      {(isExported || isExporting) && (
        <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.02em', lineHeight: 1 }}>
          {isExporting ? 'Rendering' : 'Exported'}
        </span>
      )}
    </button>
  )
}

/* ── SpinnerIcon — small CSS-rotating indicator for PDF export ──────────── */
function SpinnerIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true"
      style={{ animation: 'arup-spin 0.8s linear infinite' }}>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2.5"
        strokeLinecap="round" strokeDasharray="14 40" />
      <style>{`@keyframes arup-spin { to { transform: rotate(360deg); } }`}</style>
    </svg>
  )
}


/* ═══════════════════════════════════════════════════════════════════════════
   MessageBubble — main export
   ─────────────────────────────────────────────────────────────────────────
   Rendering mode (mutually exclusive, in priority order):

   1. hasClarification  → ClarificationBlock (schema bypassed entirely)
   2. d.ui_schema       → UISchemaRenderer   (schema-driven, v2.0)
   3. fallback          → legacy hardcoded render (backward compat)

   Outside all modes (always rendered on full answers):
   • ConfidenceBar
   • FollowUpBlock
   • Disclaimer
   ═══════════════════════════════════════════════════════════════════════════ */
export default function MessageBubble({ message, sessionId, messageIndex, onClarificationAnswer, onFollowUpDraft, onFollowUp }) {
  // Ref for the card body — used by CopyButton to extract plain-text response
  const cardBodyRef = useRef(null)

  /* ── User bubble ── */
  if (message.role === 'user') {
    return (
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <div style={{
          maxWidth: '72%',
          background: 'var(--white)',
          border: '1px solid var(--accent)',
          borderRadius: '8px 8px 2px 8px',
          padding: '12px 16px',
          color: 'var(--dark-sky)',
          fontSize: 16, lineHeight: 1.5,
          whiteSpace: 'pre-wrap',
          fontFamily: "'Roboto', Helvetica, Arial, sans-serif",
        }}>
          {message.content}
        </div>
      </div>
    )
  }

  /* ── Assistant bubble ── */
  const d = message.data || {}

  const clarification    = resolveClarification(d)
  const hasClarification = clarification !== null

  const clar_factors = (d.confidence?.factors || []).filter(f => f.includes('questions asked'))
  const clar_progress = clar_factors[0] || null

  // Detect schema-driven mode
  const hasSchema = Boolean(d.ui_schema?.components?.length)

  // Backward-compat: if only onFollowUp is provided, use it for both roles
  const handleClarificationAnswer = onClarificationAnswer || onFollowUp
  const handleFollowUpDraft       = onFollowUpDraft       || onFollowUp

  return (
    <div style={{ display: 'flex', justifyContent: 'flex-start', maxWidth: '100%' }}>
      <div className="card" style={{
        width: '100%',
        maxWidth: hasClarification ? 760 : (() => {
          const mode = d.algorithm_visualization?.render_mode
                    || d.algorithm_visualization?.layout_mode
                    || d.algorithm_visualization?.layout
          const isMultiZone = mode === 'clinical_multi_zone_document'
                           || mode === 'clinical_document_flowchart'
                           || mode === 'grouped_branch_layout'
          return isMultiZone ? 1040 : 800
        })(),
        padding: 0,
        minHeight: 'unset',
        overflow: 'hidden',
        border: d.isWelcome
          ? '1px solid var(--accent3)'
          : hasClarification
            ? '1px solid var(--primary)'
            : 'var(--card-border)',
      }}>

        {/* Card header (skipped on welcome) */}
        {!d.isWelcome && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '10px 24px',
            background: hasClarification ? '#FFF5F5' : 'var(--bg-salt)',
            borderBottom: '1px solid var(--accent3)',
          }}>
            <span style={{
              width: 8, height: 8, borderRadius: '50%',
              background: hasClarification ? 'var(--primary)' : 'var(--positive)',
              display: 'inline-block', flexShrink: 0,
            }} />
            <span className="text-caption" style={{ color: 'var(--accent2)', fontWeight: 500 }}>
              AI Test Advisor — grounded in ARUP sources
            </span>

            {/* Right-aligned actions: status badges + copy button */}
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
              {hasClarification && (
                <>
                  <span className="badge badge-danger">
                    Context needed
                  </span>
                  {clar_progress && (
                    <span className="text-caption" style={{ color: 'var(--accent)', fontSize: 11, whiteSpace: 'nowrap' }}>
                      {clar_progress}
                    </span>
                  )}
                </>
              )}
              {/* Schema mode indicator (subtle, non-clinical) */}
              {!hasClarification && hasSchema && (
                <span
                  title={`Schema v2 · ${d.ui_schema.component_count} components · ${d.ui_schema.layout}`}
                  style={{
                    fontSize: 10, color: 'var(--accent3)',
                    cursor: 'default', userSelect: 'none',
                    letterSpacing: '0.03em',
                  }}
                >
                  ⬡
                </span>
              )}
              {/* Feedback — thumbs up / down */}
              {!hasClarification && sessionId && (
                <FeedbackButton sessionId={sessionId} messageIndex={messageIndex} />
              )}
              {/* Export response as PDF */}
              {!hasClarification && !d.isWelcome && (
                <ExportPdfButton
                  getElementFn={() => cardBodyRef.current}
                  getTextFn={() => cardBodyRef.current?.innerText ?? ''}
                  messageIndex={messageIndex}
                  sessionId={sessionId}
                />
              )}
              {/* Copy full response to clipboard */}
              <CopyButton getTextFn={() => cardBodyRef.current?.innerText ?? ''} />
            </div>
          </div>
        )}

        {/* Card body — ref used by CopyButton to extract visible plain text */}
        <div ref={cardBodyRef} style={{ padding: 'var(--card-padding)' }}>

          {/* ─── Rendering mode selection ─────────────────────────── */}

          {hasClarification ? (
            /* MODE 1: Clarification — answer form, not echo */
            <>
              <p className="text-body" style={{ color: 'var(--dark-sky)', whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
                {d.answer}
              </p>
              <ClarificationBlock clarification={clarification} onAnswer={handleClarificationAnswer} />
            </>
          ) : hasSchema ? (
            /* MODE 2: Schema-driven — UISchemaRenderer owns all clinical content */
            <UISchemaRenderer schema={d.ui_schema} />
          ) : (
            /* MODE 3: Legacy fallback — hardcoded render from raw response fields */
            <>
              <p className="text-body" style={{ color: 'var(--dark-sky)', whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
                {d.answer}
              </p>

              {d.recommendations?.length > 0 && (
                <div style={{ marginTop: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
                  {d.recommendations.map((rec, i) => (
                    <RecommendationCard key={i} rec={rec} />
                  ))}
                </div>
              )}

              {d.conflicts_surfaced?.length > 0 && (
                <ConflictsBlock conflicts={d.conflicts_surfaced} />
              )}

              {d.evidence_gaps?.length > 0 && (
                <EvidenceGapsBlock gaps={d.evidence_gaps} />
              )}

              {d.citations?.length > 0 && (
                <CitationsTable citations={d.citations} />
              )}

              {d.algorithm_visualization && (
                <AlgorithmFlowchart viz={d.algorithm_visualization} />
              )}
            </>
          )}

          {/* ─── Always-rendered (all modes, all full answers) ──── */}

          {d.confidence && !d.isWelcome && (
            <ConfidenceBar confidence={d.confidence} />
          )}

          {/* Follow-up: ONLY shown on non-clarification turns; uses draft callback */}
          {!hasClarification && d.follow_up_questions?.length > 0 && (
            <FollowUpBlock questions={d.follow_up_questions} onDraft={handleFollowUpDraft} />
          )}

          {d.disclaimer && (
            <>
              <hr className="divider-light" style={{ margin: '16px 0 12px' }} />
              <p className="text-caption" style={{ color: 'var(--accent)', lineHeight: 1.6 }}>
                {d.disclaimer}
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/* Named export so DesignerPanel can use the same renderer */
export { AlgorithmFlowchart }