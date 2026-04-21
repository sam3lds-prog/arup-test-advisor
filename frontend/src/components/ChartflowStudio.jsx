/**
 * ChartflowStudio.jsx  v1.0.0
 * ────────────────────────────────────────────────────────────────────────────
 * ARUP Chartflow Studio
 * 
 * Designer workflow for extracting, previewing, and saving ARUP chartflow rules.
 * 
 * Workflow:
 *   1. Extract rules from hierarchy asset
 *   2. Preview 3 stages: Source → Interpretation → Normalized
 *   3. Save as draft or approve
 *   4. Version history and conversation
 */

import React, { useState, useEffect } from 'react';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8010';

export default function ChartflowStudio() {
  const [stage, setStage] = useState('initial'); // initial, extracting, preview, saving
  const [preview, setPreview] = useState(null);
  const [rulesSpec, setRulesSpec] = useState(null);
  const [currentRules, setCurrentRules] = useState(null);
  const [error, setError] = useState(null);
  const [activePreviewStage, setActivePreviewStage] = useState(0);

  // Load current approved rules on mount
  useEffect(() => {
    loadCurrentRules();
  }, []);

  const loadCurrentRules = async () => {
    try {
      const res = await fetch(`${API_BASE}/designer/chartflow/library/current`);
      if (res.ok) {
        const data = await res.json();
        setCurrentRules(data);
      }
    } catch (err) {
      console.log('No current chartflow rules found');
    }
  };

  const handleExtract = async () => {
    setStage('extracting');
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/designer/chartflow/extract`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          asset_id: 'hierarchy_algorithms_seed',
          asset_path: 'Hierarchy_Algorithms.png',
        }),
      });

      if (!res.ok) {
        throw new Error(`Extraction failed: ${res.statusText}`);
      }

      const data = await res.json();
      setRulesSpec(data.rules_spec);
      setPreview(data.preview);
      setStage('preview');
      setActivePreviewStage(0);
    } catch (err) {
      setError(err.message);
      setStage('initial');
    }
  };

  const handleSave = async () => {
    setStage('saving');
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/designer/chartflow/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          rules_spec: rulesSpec,
          summary: 'Updated ARUP chartflow rules from hierarchy asset',
        }),
      });

      if (!res.ok) {
        throw new Error(`Save failed: ${res.statusText}`);
      }

      const data = await res.json();
      alert('Chartflow rules saved as draft! Component ID: ' + data.component.id);
      loadCurrentRules();
      setStage('initial');
    } catch (err) {
      setError(err.message);
      setStage('preview');
    }
  };

  const handleApprove = async () => {
    if (!confirm('Approve current draft chartflow rules? This will make them active for algorithm rendering.')) {
      return;
    }

    try {
      const res = await fetch(`${API_BASE}/designer/chartflow/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });

      if (!res.ok) {
        throw new Error(`Approve failed: ${res.statusText}`);
      }

      alert('Chartflow rules approved!');
      loadCurrentRules();
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <div className="chartflow-studio" style={{
      padding: 20,
      maxWidth: 900,
    }}>
      {/* Header */}
      <div style={{
        marginBottom: 24,
        paddingBottom: 16,
        borderBottom: '2px solid var(--primary)',
      }}>
        <h2 style={{
          margin: 0,
          fontSize: 24,
          fontWeight: 600,
          color: 'var(--dark-sky)',
        }}>
          ARUP Chartflow Studio
        </h2>
        <p style={{
          margin: '8px 0 0 0',
          fontSize: 14,
          color: 'var(--granite)',
        }}>
          Extract, preview, and manage ARUP algorithm chartflow rendering rules
        </p>
      </div>

      {/* Current Rules Status */}
      {currentRules && (
        <div style={{
          padding: 16,
          background: 'var(--bg-salt)',
          borderLeft: '4px solid var(--lab-blue)',
          borderRadius: 4,
          marginBottom: 24,
        }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--lab-blue)', marginBottom: 4 }}>
            CURRENT CHARTFLOW RULES
          </div>
          <div style={{ fontSize: 14, color: 'var(--dark-sky)' }}>
            Status: <strong>{currentRules.status || 'draft'}</strong>
          </div>
          <div style={{ fontSize: 13, color: 'var(--granite)', marginTop: 4 }}>
            Schema: {currentRules.rules?.schema_version || 'unknown'}
          </div>
        </div>
      )}

      {/* Error Display */}
      {error && (
        <div style={{
          padding: 16,
          background: '#FFF8E1',
          borderLeft: '4px solid #F59E0B',
          borderRadius: 4,
          marginBottom: 24,
        }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#F59E0B', marginBottom: 4 }}>
            ERROR
          </div>
          <div style={{ fontSize: 14, color: 'var(--dark-sky)' }}>
            {error}
          </div>
        </div>
      )}

      {/* Initial State - Extract Button */}
      {stage === 'initial' && (
        <div>
          <button
            onClick={handleExtract}
            style={{
              padding: '12px 24px',
              background: 'var(--primary)',
              color: 'var(--white)',
              border: 'none',
              borderRadius: 'var(--card-radius)',
              fontSize: 15,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            Extract Rules from Hierarchy Asset
          </button>

          <div style={{
            marginTop: 24,
            padding: 16,
            background: '#EEF4FB',
            borderLeft: '4px solid var(--lab-blue)',
            borderRadius: 4,
          }}>
            <div style={{ fontSize: 13, color: 'var(--dark-sky)', lineHeight: 1.6 }}>
              <strong>What this does:</strong>
              <ul style={{ marginTop: 8, paddingLeft: 20 }}>
                <li>Extracts ARUP canonical chartflow taxonomy from Hierarchy_Algorithms.png</li>
                <li>Preserves critical category distinctions (entry/trigger vs input, binary vs multi-decision)</li>
                <li>Generates 3-stage preview: Source → Interpretation → Normalized</li>
                <li>Creates versioned design artifact for fidelity-first algorithm rendering</li>
              </ul>
            </div>
          </div>
        </div>
      )}

      {/* Extracting State */}
      {stage === 'extracting' && (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <div style={{ fontSize: 16, color: 'var(--granite)' }}>
            Extracting chartflow rules from hierarchy asset...
          </div>
        </div>
      )}

      {/* Preview State */}
      {stage === 'preview' && preview && (
        <div>
          {/* Stage Tabs */}
          <div style={{
            display: 'flex',
            gap: 8,
            marginBottom: 24,
            borderBottom: '1px solid var(--accent3)',
          }}>
            {preview.stages.map((stageData, idx) => (
              <button
                key={idx}
                onClick={() => setActivePreviewStage(idx)}
                style={{
                  padding: '10px 20px',
                  background: activePreviewStage === idx ? 'var(--primary)' : 'transparent',
                  color: activePreviewStage === idx ? 'var(--white)' : 'var(--dark-sky)',
                  border: 'none',
                  borderBottom: activePreviewStage === idx ? '2px solid var(--primary)' : '2px solid transparent',
                  fontSize: 14,
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                {idx + 1}. {stageData.title}
              </button>
            ))}
          </div>

          {/* Active Stage Content */}
          <div style={{
            padding: 20,
            background: 'var(--white)',
            border: '1px solid var(--accent3)',
            borderRadius: 'var(--card-radius)',
            minHeight: 300,
          }}>
            {renderPreviewStage(preview.stages[activePreviewStage])}
          </div>

          {/* Validation Summary */}
          <div style={{
            marginTop: 16,
            padding: 12,
            background: preview.validation_summary?.ready_for_save ? '#EEF4FB' : '#FFF8E1',
            borderLeft: `4px solid ${preview.validation_summary?.ready_for_save ? 'var(--lab-blue)' : '#F59E0B'}`,
            borderRadius: 4,
          }}>
            <div style={{ fontSize: 13, color: 'var(--dark-sky)' }}>
              <strong>Validation:</strong> {preview.validation_summary?.warnings_count || 0} warnings, {preview.validation_summary?.conflicts_count || 0} conflicts
              {preview.validation_summary?.ready_for_save && ' • Ready to save'}
            </div>
          </div>

          {/* Action Buttons */}
          <div style={{
            marginTop: 24,
            display: 'flex',
            gap: 12,
          }}>
            <button
              onClick={handleSave}
              disabled={!preview.validation_summary?.ready_for_save}
              style={{
                padding: '12px 24px',
                background: preview.validation_summary?.ready_for_save ? 'var(--primary)' : 'var(--granite)',
                color: 'var(--white)',
                border: 'none',
                borderRadius: 'var(--card-radius)',
                fontSize: 15,
                fontWeight: 600,
                cursor: preview.validation_summary?.ready_for_save ? 'pointer' : 'not-allowed',
              }}
            >
              Save as Draft
            </button>

            <button
              onClick={() => { setStage('initial'); setPreview(null); setRulesSpec(null); }}
              style={{
                padding: '12px 24px',
                background: 'transparent',
                color: 'var(--dark-sky)',
                border: '1px solid var(--accent3)',
                borderRadius: 'var(--card-radius)',
                fontSize: 15,
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Approve Button (if there's a draft) */}
      {currentRules?.status === 'draft' && stage === 'initial' && (
        <div style={{ marginTop: 24 }}>
          <button
            onClick={handleApprove}
            style={{
              padding: '12px 24px',
              background: 'var(--lab-blue)',
              color: 'var(--white)',
              border: 'none',
              borderRadius: 'var(--card-radius)',
              fontSize: 15,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            Approve Current Draft
          </button>
        </div>
      )}
    </div>
  );
}

function renderPreviewStage(stageData) {
  if (!stageData) return null;

  if (stageData.stage === 'source') {
    return (
      <div>
        <h3 style={{ marginTop: 0, fontSize: 16, fontWeight: 600 }}>{stageData.title}</h3>
        <p style={{ fontSize: 14, color: 'var(--granite)' }}>{stageData.description}</p>
        
        <div style={{ marginTop: 16 }}>
          {stageData.assets?.map((asset, idx) => (
            <div key={idx} style={{
              padding: 12,
              background: 'var(--bg-salt)',
              borderRadius: 4,
              marginBottom: 8,
            }}>
              <div style={{ fontSize: 14, fontWeight: 600 }}>{asset.filename}</div>
              <div style={{ fontSize: 12, color: 'var(--granite)', marginTop: 4 }}>
                {asset.asset_type} • {asset.purpose || 'Design asset'}
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (stageData.stage === 'interpretation') {
    return (
      <div>
        <h3 style={{ marginTop: 0, fontSize: 16, fontWeight: 600 }}>{stageData.title}</h3>
        <p style={{ fontSize: 14, color: 'var(--granite)' }}>{stageData.description}</p>

        <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12 }}>Categories:</div>
          {stageData.categories?.slice(0, 6).map((cat, idx) => (
            <div key={idx} style={{
              padding: 10,
              background: 'var(--bg-salt)',
              borderRadius: 4,
              marginBottom: 8,
            }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--primary)' }}>
                {cat.label}
              </div>
              <div style={{ fontSize: 13, color: 'var(--dark-sky)', marginTop: 4 }}>
                {cat.description}
              </div>
              {cat.distinct_from?.length > 0 && (
                <div style={{ fontSize: 12, color: 'var(--granite)', marginTop: 6 }}>
                  Distinct from: {cat.distinct_from.join(', ')}
                </div>
              )}
            </div>
          ))}
        </div>

        <div style={{ marginTop: 16, padding: 12, background: '#EEF4FB', borderRadius: 4 }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Preservation Notes:</div>
          {stageData.preservation_notes?.map((note, idx) => (
            <div key={idx} style={{ fontSize: 12, color: 'var(--dark-sky)' }}>• {note}</div>
          ))}
        </div>
      </div>
    );
  }

  if (stageData.stage === 'normalized') {
    return (
      <div>
        <h3 style={{ marginTop: 0, fontSize: 16, fontWeight: 600 }}>{stageData.title}</h3>
        <p style={{ fontSize: 14, color: 'var(--granite)' }}>{stageData.description}</p>

        <div style={{ marginTop: 16 }}>
          {stageData.notes?.map((note, idx) => (
            <div key={idx} style={{ fontSize: 13, color: 'var(--dark-sky)', marginBottom: 4 }}>
              • {note}
            </div>
          ))}
        </div>

        {stageData.validation?.warnings?.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Warnings:</div>
            {stageData.validation.warnings.slice(0, 5).map((warn, idx) => (
              <div key={idx} style={{
                padding: 8,
                background: '#FFF8E1',
                borderRadius: 4,
                marginBottom: 6,
                fontSize: 12,
              }}>
                {warn.warning}
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  return null;
}
