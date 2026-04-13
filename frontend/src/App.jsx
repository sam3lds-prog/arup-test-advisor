/**
 * App.jsx  v1.1.0
 * ─────────────────────────────────────────────────────────────────────────────
 * v1.1.0 additions:
 *   • Designer Panel toggle button (⬡) in the header
 *   • DesignerPanel drawer, passing current session_id from ChatWindow
 *   • session_id propagated up from ChatWindow via onSessionChange callback
 *
 * v1.0.0 retained (fully backward-compatible):
 *   • Sidebar with UploadPanel + indexed sources list
 *   • ChatWindow occupies the main area
 *   • /health status dot + document count badge
 */

import { useState, useEffect } from 'react'
import ChatWindow from './components/ChatWindow.jsx'
import UploadPanel from './components/UploadPanel.jsx'
import DesignerPanel from './components/DesignerPanel.jsx'

const API = '/api'

const SOURCE_CONFIG = {
  'Algorithm':      { badgeClass: 'badge badge-source-algo',    label: 'Algorithm' },
  'Consult Topic':  { badgeClass: 'badge badge-source-consult', label: 'Consult' },
  'Fact Sheet':     { badgeClass: 'badge badge-source-fact',    label: 'Fact Sheet' },
  'Test Directory': { badgeClass: 'badge badge-source-dir',     label: 'Directory' },
  'General':        { badgeClass: 'badge badge-source-general', label: 'General' },
}

export default function App() {
  const [documents,      setDocuments]      = useState([])
  const [status,         setStatus]         = useState(null)
  const [sidebarOpen,    setSidebarOpen]     = useState(true)
  const [designerOpen,   setDesignerOpen]   = useState(false)
  const [currentSession, setCurrentSession] = useState(null)

  const refreshDocs = async () => {
    try {
      const r = await fetch(`${API}/documents`)
      const d = await r.json()
      setDocuments(d.documents || [])
    } catch { /* backend not ready */ }
  }

  useEffect(() => {
    const init = async () => {
      try {
        const r = await fetch(`${API}/health`)
        setStatus(await r.json())
      } catch { setStatus(null) }
    }
    init()
    refreshDocs()
  }, [])

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden', fontFamily: "'Roboto', Helvetica, Arial, sans-serif" }}>

      {/* ── Sidebar ──────────────────────────────────── */}
      <aside style={{
        width: sidebarOpen ? 292 : 0,
        minWidth: sidebarOpen ? 292 : 0,
        transition: 'width .2s ease, min-width .2s ease',
        overflow: 'hidden',
        background: 'var(--white)',
        borderRight: '1px solid var(--accent)',
        display: 'flex',
        flexDirection: 'column',
        flexShrink: 0,
      }}>

        {/* ARUP brand bar */}
        <div style={{ background: 'var(--secondary)', padding: '20px 24px', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
            <div style={{
              width: 36, height: 36, borderRadius: 4,
              background: 'var(--white)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              flexShrink: 0,
            }}>
              <span style={{ color: 'var(--primary)', fontWeight: 700, fontSize: 14, letterSpacing: '-0.5px' }}>
                ARUP
              </span>
            </div>
            <div>
              <div style={{ color: 'var(--white)', fontSize: 14, fontWeight: 700, lineHeight: 1.2 }}>
                AI Test Advisor
              </div>
              <div style={{ color: 'rgba(255,255,255,0.65)', fontSize: 11, fontWeight: 400, marginTop: 1 }}>
                {status
                  ? `${status.documents_indexed} chunks indexed`
                  : 'Connecting to backend…'}
              </div>
            </div>
          </div>
        </div>

        {/* Scrollable body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '20px 20px 0' }}>
          <UploadPanel apiBase={API} onUploadComplete={refreshDocs} />

          {documents.length > 0 && (
            <div style={{ marginTop: 24 }}>
              <p className="text-label" style={{ color: 'var(--accent)', marginBottom: 10 }}>
                Indexed sources
              </p>
              {documents.map((doc, i) => (
                <DocRow key={i} doc={doc} config={SOURCE_CONFIG} />
              ))}
            </div>
          )}
        </div>

        {/* Sidebar footer */}
        <div style={{ padding: '12px 20px', borderTop: '1px solid var(--accent3)', flexShrink: 0 }}>
          <p className="text-caption" style={{ color: 'var(--accent)', lineHeight: 1.6 }}>
            ARUP sources only · citations required<br />
            Not for independent clinical use
          </p>
        </div>
      </aside>

      {/* ── Main area ─────────────────────────────────── */}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>

        {/* Top navigation bar */}
        <header style={{
          background: 'var(--white)',
          borderBottom: '1px solid var(--accent)',
          padding: '0 24px',
          height: 56,
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          flexShrink: 0,
        }}>
          {/* Sidebar toggle */}
          <button
            onClick={() => setSidebarOpen(o => !o)}
            style={{
              background: 'none', padding: '6px 8px',
              borderRadius: 4, color: 'var(--accent2)',
              fontSize: 18, lineHeight: 1, transition: 'background .1s',
            }}
            onMouseEnter={e => e.currentTarget.style.background = 'var(--neutral-200)'}
            onMouseLeave={e => e.currentTarget.style.background = 'none'}
            aria-label="Toggle sidebar"
          >
            ☰
          </button>

          <div style={{ width: 1, height: 24, background: 'var(--accent3)' }} />

          <span className="text-h4" style={{ color: 'var(--dark-sky)' }}>
            AI Test Advisor
          </span>

          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>

            {/* Backend status */}
            {status && (
              <span style={{
                display: 'flex', alignItems: 'center', gap: 5,
                fontSize: 12, color: 'var(--positive)', fontWeight: 500,
              }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--positive)', display: 'inline-block' }} />
                Backend connected
              </span>
            )}

            {/* Document count badge */}
            {documents.length > 0 && (
              <span className="badge badge-source-consult">
                {documents.length} document{documents.length !== 1 ? 's' : ''} loaded
              </span>
            )}

            {/* Divider */}
            <div style={{ width: 1, height: 24, background: 'var(--accent3)' }} />

            {/* ── Designer Panel toggle (new v1.1.0) ── */}
            <button
              onClick={() => setDesignerOpen(o => !o)}
              title="Open Designer Panel"
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '5px 12px',
                background: designerOpen ? 'var(--secondary)' : 'none',
                color: designerOpen ? 'var(--white)' : 'var(--accent2)',
                border: `1px solid ${designerOpen ? 'var(--secondary)' : 'var(--accent3)'}`,
                borderRadius: 'var(--btn-radius)',
                fontSize: 12, fontWeight: designerOpen ? 700 : 400,
                cursor: 'pointer',
                transition: 'all .15s',
              }}
              onMouseEnter={e => {
                if (!designerOpen) {
                  e.currentTarget.style.background = 'var(--bg-salt)'
                  e.currentTarget.style.borderColor = 'var(--accent)'
                }
              }}
              onMouseLeave={e => {
                if (!designerOpen) {
                  e.currentTarget.style.background = 'none'
                  e.currentTarget.style.borderColor = 'var(--accent3)'
                }
              }}
              aria-label="Toggle designer panel"
            >
              <span style={{ fontSize: 15, lineHeight: 1 }}>⬡</span>
              Designer
            </button>
          </div>
        </header>

        {/* Chat area */}
        <ChatWindow
          api={API}
          hasDocuments={documents.length > 0}
          onSessionChange={setCurrentSession}
        />
      </main>

      {/* ── Designer Panel drawer (new v1.1.0) ──────── */}
      <DesignerPanel
        open={designerOpen}
        onClose={() => setDesignerOpen(false)}
        sessionId={currentSession}
        api={API}
      />
    </div>
  )
}

function DocRow({ doc, config }) {
  const c = config[doc.source_type] || config['General']
  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: 8,
      padding: '8px 0', borderBottom: '1px solid var(--accent3)',
    }}>
      <span className={c.badgeClass} style={{ marginTop: 1 }}>{c.label}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="text-body-sm" style={{
          color: 'var(--dark-sky)', fontWeight: 500,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {doc.filename}
        </div>
        <div className="text-caption" style={{ color: 'var(--accent)', marginTop: 1 }}>
          {doc.chunks} chunks indexed
        </div>
      </div>
    </div>
  )
}