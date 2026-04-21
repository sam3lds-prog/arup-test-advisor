/**
 * ArupDocumentFidelityRenderer.jsx  v1.1.0
 * ────────────────────────────────────────────────────────────────────────────
 * ARUP Document Fidelity Renderer
 * 
 * REFINEMENTS in v1.1.0:
 *   - Horizontal two-column layout support (like AKI/CKD algorithm)
 *   - Better visual hierarchy matching ARUP PDFs
 *   - Enhanced node styling with proper shape rendering
 *   - Improved spacing and alignment
 * 
 * Renders ARUP algorithms using fidelity-first approach that preserves:
 *   - Original chartflow structure and shape
 *   - Horizontal splits (parallel pathways side-by-side)
 *   - Entry/trigger vs input distinctions
 *   - Binary vs multi-decision distinctions
 *   - Section flow lines as grouping boundaries
 *   - Footer content (footnotes, references, legend)
 *   - Routing labels as edge annotations
 * 
 * Applies ARUP design tokens for consistent branding.
 */

import React from 'react';

export default function ArupDocumentFidelityRenderer({ data }) {
  if (!data || data.render_mode !== 'arup_document_fidelity') {
    return null;
  }

  const {
    nodes = [],
    edges = [],
    footer_blocks = [],
    routing_labels = [],
    entry_nodes = [],
    columns = [],
    layout_mode = 'vertical_linear',
    stats = {},
  } = data;

  return (
    <div className="arup-fidelity-algorithm" style={{
      background: 'var(--white)',
      border: '1px solid var(--accent3)',
      borderRadius: 'var(--card-radius)',
      padding: 'var(--card-padding)',
      marginTop: 16,
    }}>
      {/* Header */}
      <div style={{
        marginBottom: 20,
        paddingBottom: 12,
        borderBottom: '2px solid var(--primary)',
      }}>
        <h3 style={{
          margin: 0,
          fontSize: 18,
          fontWeight: 600,
          color: 'var(--dark-sky)',
        }}>
          Algorithm Document
        </h3>
        <div style={{
          marginTop: 6,
          fontSize: 12,
          color: 'var(--granite)',
        }}>
          Fidelity-first • {layout_mode.replace(/_/g, ' ')} • {stats.total_nodes || 0} nodes
        </div>
      </div>

      {/* Render based on layout mode */}
      {layout_mode === 'horizontal_two_column' ? (
        <HorizontalTwoColumnLayout
          nodes={nodes}
          edges={edges}
          columns={columns}
          entry_nodes={entry_nodes}
          routing_labels={routing_labels}
        />
      ) : (
        <VerticalLayout
          nodes={nodes}
          edges={edges}
          entry_nodes={entry_nodes}
          routing_labels={routing_labels}
        />
      )}

      {/* Footer blocks */}
      {footer_blocks.length > 0 && (
        <div className="algorithm-footer" style={{
          marginTop: 32,
          paddingTop: 24,
          borderTop: '1px solid var(--accent3)',
        }}>
          {footer_blocks.map((block) => (
            <FooterBlockRenderer key={block.id} block={block} />
          ))}
        </div>
      )}
    </div>
  );
}

function HorizontalTwoColumnLayout({ nodes, edges, columns, entry_nodes, routing_labels }) {
  // Build node map
  const nodeMap = {};
  nodes.forEach(n => { nodeMap[n.id] = n; });

  // Find entry node
  const entryNode = nodes.find(n => entry_nodes.includes(n.id));

  return (
    <div className="horizontal-layout">
      {/* Entry node at top (full width) */}
      {entryNode && (
        <div style={{ marginBottom: 24 }}>
          <NodeRenderer node={entryNode} isEntry={true} />
        </div>
      )}

      {/* Two-column split */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gap: 24,
        marginTop: 20,
      }}>
        {columns.map((column, idx) => (
          <div key={column.column_id} className="algorithm-column" style={{
            padding: 16,
            background: 'var(--bg-salt)',
            borderRadius: 8,
            border: '1px solid var(--accent3)',
          }}>
            {/* Column header */}
            {column.title && (
              <div style={{
                fontSize: 14,
                fontWeight: 600,
                color: 'var(--lab-blue)',
                marginBottom: 16,
                textAlign: 'center',
                padding: '8px 12px',
                background: 'var(--white)',
                borderRadius: 6,
              }}>
                {column.title}
              </div>
            )}

            {/* Nodes in this column */}
            <div className="column-flow" style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 16,
            }}>
              {column.node_ids.map(nodeId => {
                const node = nodeMap[nodeId];
                if (!node) return null;
                return (
                  <NodeRenderer
                    key={nodeId}
                    node={node}
                    isEntry={false}
                  />
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {/* Routing labels summary */}
      {routing_labels.length > 0 && (
        <div style={{
          marginTop: 16,
          padding: 12,
          background: 'var(--bg-salt)',
          borderRadius: 6,
          fontSize: 12,
        }}>
          <strong>Decision Paths:</strong> {routing_labels.map(l => l.label).join(' • ')}
        </div>
      )}
    </div>
  );
}

function VerticalLayout({ nodes, edges, entry_nodes, routing_labels }) {
  return (
    <div className="vertical-layout">
      {/* Flow nodes */}
      <div className="flow-nodes" style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
      }}>
        {nodes.map((node) => (
          <NodeRenderer
            key={node.id}
            node={node}
            isEntry={entry_nodes.includes(node.id)}
          />
        ))}
      </div>

      {/* Routing labels */}
      {routing_labels.length > 0 && (
        <div className="routing-labels" style={{
          marginTop: 12,
          padding: 12,
          background: 'var(--bg-salt)',
          borderRadius: 4,
        }}>
          <div style={{
            fontSize: 12,
            fontWeight: 600,
            color: 'var(--granite)',
            marginBottom: 8,
          }}>
            Decision Paths:
          </div>
          {routing_labels.map((label, idx) => (
            <div
              key={`label-${idx}`}
              style={{
                fontSize: 13,
                color: label.visual?.color || 'var(--lab-blue)',
                fontWeight: label.visual?.font_weight || 600,
                marginLeft: 12,
              }}
            >
              • {label.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function NodeRenderer({ node, isEntry }) {
  const { category, title, body, visual = {} } = node;

  // Get shape style based on ARUP PDF patterns
  const getShapeStyle = () => {
    const baseStyle = {
      padding: visual.padding || '12px 16px',
      background: visual.background_color || 'transparent',
      color: visual.text_color || 'var(--dark-sky)',
      border: visual.border_width ? `${visual.border_width} ${visual.border_style || 'solid'} ${visual.border_color || 'var(--granite)'}` : 'none',
      marginBottom: 12,
      position: 'relative',
    };

    // Shape-specific styles matching ARUP PDFs
    if (visual.shape === 'pill' || category === 'singular_decision_point_binary') {
      // Purple oval shape for binary decisions (like "Yes" / "No")
      baseStyle.borderRadius = '50vh';
      baseStyle.background = visual.background_color || '#D8D3E8'; // Light purple
      baseStyle.textAlign = 'center';
      baseStyle.padding = '8px 24px';
      baseStyle.display = 'inline-block';
      baseStyle.minWidth = '100px';
    } else if (category === 'entry_trigger_point') {
      // Green entry box (like INDICATIONS FOR TESTING)
      baseStyle.borderRadius = '8px';
      baseStyle.background = '#4A7C59'; // ARUP green
      baseStyle.color = '#FFFFFF';
      baseStyle.padding = '16px 20px';
      baseStyle.textAlign = 'center';
      baseStyle.fontWeight = 600;
    } else if (visual.shape === 'rounded_rectangle') {
      baseStyle.borderRadius = '12px';
    } else if (visual.shape === 'rectangle') {
      baseStyle.borderRadius = '6px';
    }

    return baseStyle;
  };

  // Get category badge
  const getCategoryBadge = () => {
    const badges = {
      entry_trigger_point: { label: 'Entry', color: 'var(--primary)' },
      input_point: { label: 'Input', color: 'var(--lab-blue)' },
      singular_decision_point_binary: { label: 'Decision', color: '#8B7BA8' },
      multi_decision_consideration_point: { label: 'Consider', color: 'var(--lab-blue)' },
      exit_termination_point: { label: 'Exit', color: 'var(--granite)' },
      process_step_node: { label: 'Process', color: 'var(--accent3)' },
    };

    const badge = badges[category];
    if (!badge || category === 'singular_decision_point_binary') return null; // Don't show badge for decision ovals

    return (
      <span style={{
        display: 'inline-block',
        padding: '2px 8px',
        fontSize: 10,
        fontWeight: 600,
        color: 'var(--white)',
        background: badge.color,
        borderRadius: '4px',
        marginRight: 8,
        textTransform: 'uppercase',
        letterSpacing: '0.5px',
      }}>
        {badge.label}
      </span>
    );
  };

  const shapeStyle = getShapeStyle();

  return (
    <div className={`algorithm-node node-${category}`} style={shapeStyle}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        marginBottom: title && body ? 8 : 0,
      }}>
        {getCategoryBadge()}
        {isEntry && category !== 'entry_trigger_point' && (
          <span style={{
            fontSize: 10,
            fontWeight: 600,
            color: 'var(--primary)',
            textTransform: 'uppercase',
            letterSpacing: '0.5px',
          }}>
            ⚡ Start
          </span>
        )}
      </div>

      {title && (
        <div style={{
          fontWeight: category === 'entry_trigger_point' ? 700 : 600,
          fontSize: category === 'entry_trigger_point' ? 16 : 15,
          marginBottom: body ? 6 : 0,
          textAlign: shapeStyle.textAlign || 'left',
        }}>
          {title}
        </div>
      )}

      {body && (
        <div style={{
          fontSize: 14,
          lineHeight: 1.5,
          opacity: 0.9,
          textAlign: shapeStyle.textAlign || 'left',
        }}>
          {body}
        </div>
      )}

      {/* Show out-degree indicator for decision nodes */}
      {category.includes('decision') && node.out_degree > 0 && (
        <div style={{
          marginTop: 8,
          fontSize: 12,
          color: 'var(--granite)',
          textAlign: 'center',
        }}>
          → {node.out_degree} {node.out_degree === 2 ? 'paths' : 'options'}
        </div>
      )}
    </div>
  );
}

function FooterBlockRenderer({ block }) {
  const { category, title, body, visual = {} } = block;

  const getSectionStyle = () => {
    const baseStyle = {
      marginBottom: 16,
      padding: '12px 16px',
      background: 'var(--bg-salt)',
      borderRadius: '4px',
    };

    if (category === 'footnotes') {
      baseStyle.fontStyle = visual.font_style || 'italic';
      baseStyle.fontSize = visual.font_size || '14px';
      baseStyle.color = visual.text_color || 'var(--granite)';
    } else if (category === 'references') {
      baseStyle.fontSize = visual.font_size || '12px';
      baseStyle.color = visual.text_color || 'var(--granite)';
      baseStyle.lineHeight = visual.line_height || '1.4';
    } else if (category === 'abbreviations_legend') {
      baseStyle.fontSize = visual.font_size || '13px';
      baseStyle.color = visual.text_color || 'var(--dark-sky)';
    }

    return baseStyle;
  };

  const getSectionLabel = () => {
    const labels = {
      footnotes: 'Footnotes',
      references: 'References',
      abbreviations_legend: 'Abbreviations & Legend',
    };
    return labels[category] || 'Additional Information';
  };

  return (
    <div className={`footer-${category}`} style={getSectionStyle()}>
      {!title && (
        <div style={{
          fontSize: 11,
          fontWeight: 600,
          color: 'var(--granite)',
          textTransform: 'uppercase',
          letterSpacing: '0.5px',
          marginBottom: 8,
        }}>
          {getSectionLabel()}
        </div>
      )}

      {title && (
        <div style={{
          fontWeight: 600,
          fontSize: 13,
          marginBottom: 6,
        }}>
          {title}
        </div>
      )}

      {body && (
        <div style={{
          fontSize: 13,
          lineHeight: 1.5,
        }}>
          {body}
        </div>
      )}
    </div>
  );
}
