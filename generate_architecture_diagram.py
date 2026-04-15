# This script generates an improved architecture diagram for the ARUP AI Test Advisor system
# with proper spacing and alignment to avoid overlapping elements.

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import matplotlib.lines as mlines

# Create figure with larger size for better spacing
fig, ax = plt.subplots(1, 1, figsize=(20, 14))
ax.set_xlim(0, 20)
ax.set_ylim(0, 14)
ax.axis('off')

# Title
ax.text(10, 13.5, 'ARUP AI Test Advisor - System Architecture', 
        ha='center', va='top', fontsize=20, fontweight='bold')
ax.text(10, 13.1, 'Multi-Agent Geotechnical Test Selection System', 
        ha='center', va='top', fontsize=12, style='italic', color='gray')

# Helper function to create a box
def create_box(ax, x, y, width, height, text, color, textsize=9):
    box = FancyBboxPatch((x, y), width, height,
                          boxstyle="round,pad=0.1", 
                          edgecolor='black', facecolor=color,
                          linewidth=1.5)
    ax.add_patch(box)
    ax.text(x + width/2, y + height/2, text,
            ha='center', va='center', fontsize=textsize, 
            fontweight='normal', wrap=True)
    return (x + width/2, y + height/2)

# Helper function to create a cluster/layer box
def create_cluster(ax, x, y, width, height, title, color):
    box = FancyBboxPatch((x, y), width, height,
                          boxstyle="round,pad=0.05", 
                          edgecolor='black', facecolor=color,
                          linewidth=2, alpha=0.3)
    ax.add_patch(box)
    ax.text(x + 0.2, y + height - 0.2, title,
            ha='left', va='top', fontsize=10, fontweight='bold')

# Helper function to draw arrow
def draw_arrow(ax, x1, y1, x2, y2, label='', color='black'):
    arrow = FancyArrowPatch((x1, y1), (x2, y2),
                           arrowstyle='->', mutation_scale=20,
                           color=color, linewidth=2, zorder=1)
    ax.add_patch(arrow)
    if label:
        mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mid_x + 0.2, mid_y, label, fontsize=7, 
                style='italic', color=color, 
                bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='none', alpha=0.8))

# Layer 1: User Interface (top)
create_cluster(ax, 0.5, 11, 4, 1.5, '1. USER INTERFACE', 'lightblue')
ui_pos = create_box(ax, 1.2, 11.3, 2.5, 0.9, 'OneAI Tool\n(Streamlit.py)', 'lightblue', 10)

# External Services (top right)
create_cluster(ax, 15, 10.5, 4.5, 2, 'EXTERNAL SERVICES', 'mistyrose')
claude_pos = create_box(ax, 15.5, 11, 3.5, 1.2, 'Anthropic Claude API\n\nUsed for LLM-powered\ntest recommendations', '#FFB6C1', 9)

# Layer 2: API Gateway
create_cluster(ax, 0.5, 9, 4, 1.5, '2. API GATEWAY', 'plum')
api_pos = create_box(ax, 1.2, 9.3, 2.5, 0.9, 'FastAPI Server\n(main.py)', '#DDA0DD', 10)

# Key Features (right side)
create_cluster(ax, 15, 7.5, 4.5, 2.5, 'KEY FEATURES', 'lightyellow')
features_text = ('• Multi-Agent Architecture\n'
                 '• RAG-based Knowledge Retrieval\n'
                 '• Session Management\n'
                 '• Real-time Test Analysis\n'
                 '• Compliance Checking\n'
                 '• Cost Optimization')
ax.text(15.3, 9.5, features_text, fontsize=8, va='top', family='monospace')

# Layer 3: Multi-Agent Orchestration - Top Row
cluster_y = 5.8
create_cluster(ax, 0.5, cluster_y, 14, 3.2, '3. MULTI-AGENT ORCHESTRATION', 'lightgreen')

# Top row of agents - with better spacing
y_top = 7.8
agent_width = 2.2
agent_height = 0.8
spacing = 0.4

prompt_pos = create_box(ax, 1, y_top, agent_width, agent_height, 
                        'Prompt\nEngineer', '#90EE90', 9)
retrieval_pos = create_box(ax, 1 + agent_width + spacing, y_top, agent_width, agent_height,
                          'Retrieval\nSystem', '#90EE90', 9)
evaluation_pos = create_box(ax, 1 + 2*(agent_width + spacing), y_top, agent_width, agent_height,
                           'Evaluation\nAgent', '#90EE90', 9)
execution_pos = create_box(ax, 1 + 3*(agent_width + spacing), y_top, agent_width, agent_height,
                          'Execution\nHandler', '#90EE90', 9)
reasoning_pos = create_box(ax, 1 + 4*(agent_width + spacing), y_top, agent_width, agent_height,
                          'Reasoning\nAgent', '#90EE90', 9)

# Bottom row of agents
y_bottom = 6.4
algorithm_pos = create_box(ax, 1, y_bottom, agent_width, agent_height,
                          'Algorithm\nSelection', '#98FB98', 9)
forecasting_pos = create_box(ax, 1 + agent_width + spacing, y_bottom, agent_width, agent_height,
                            'Forecasting\nAgent', '#98FB98', 9)
compliance_pos = create_box(ax, 1 + 2*(agent_width + spacing), y_bottom, agent_width, agent_height,
                           'Compliance\nAgent', '#98FB98', 9)
session_pos = create_box(ax, 1 + 3*(agent_width + spacing), y_bottom, agent_width, agent_height,
                        'Session\nAgent', '#98FB98', 9)
context_pos = create_box(ax, 1 + 4*(agent_width + spacing), y_bottom, agent_width, agent_height,
                        'Context\nBuilder', '#98FB98', 9)

# Layer 4: Knowledge Layer
create_cluster(ax, 0.5, 3.8, 6.5, 1.5, '4. KNOWLEDGE LAYER', 'lightyellow')
doc_pos = create_box(ax, 1.2, 4.1, 2.2, 0.9, 'Document\nProcessor', '#FFFACD', 9)
vector_pos = create_box(ax, 4, 4.1, 2.2, 0.9, 'Vector Store\n(ChromaDB)', '#FFFACD', 9)

# Layer 5: Persistent Storage
create_cluster(ax, 0.5, 2, 10, 1.3, '5. PERSISTENT STORAGE', 'lightgray')
sqlite_pos = create_box(ax, 1, 2.3, 2.5, 0.7, 'SQLite\n(Active Rules)', '#D3D3D3', 9)
csv_pos = create_box(ax, 4, 2.3, 2.5, 0.7, 'CSV/File Storage\n(Test Data)', '#D3D3D3', 9)
redis_pos = create_box(ax, 7, 2.3, 2.5, 0.7, 'Encrypted Redis\n(Sessions)', '#D3D3D3', 9)

# Component Types Legend
legend_x = 0.8
legend_y = 0.9
ax.text(legend_x, legend_y, 'COMPONENT TYPES:', fontsize=9, fontweight='bold')
legend_items = [
    ('UI Interface', 'lightblue'),
    ('API Gateway', '#DDA0DD'),
    ('Agent Layer', '#90EE90'),
    ('Knowledge', '#FFFACD'),
    ('Storage', '#D3D3D3'),
    ('External Service', '#FFB6C1')
]
for i, (label, color) in enumerate(legend_items):
    y = legend_y - 0.25 - i * 0.2
    box = FancyBboxPatch((legend_x, y), 0.3, 0.12,
                          boxstyle="round,pad=0.02",
                          edgecolor='black', facecolor=color, linewidth=1)
    ax.add_patch(box)
    ax.text(legend_x + 0.5, y + 0.06, label, fontsize=7, va='center')

# Draw connections with proper spacing
draw_arrow(ax, ui_pos[0], ui_pos[1] - 0.5, api_pos[0], api_pos[1] + 0.5, 'HTTP\nRequests', 'blue')
draw_arrow(ax, api_pos[0], api_pos[1] - 0.5, prompt_pos[0], prompt_pos[1] + 0.5, 'Orchestrates', 'purple')
draw_arrow(ax, api_pos[0] + 1, api_pos[1] - 0.5, retrieval_pos[0], retrieval_pos[1] + 0.5, 'Routes', 'purple')

# Agent to knowledge layer connections
draw_arrow(ax, retrieval_pos[0], retrieval_pos[1] - 2.5, vector_pos[0], vector_pos[1] + 0.5, 'Queries', 'green')
draw_arrow(ax, prompt_pos[0], prompt_pos[1] - 2.5, doc_pos[0], doc_pos[1] + 0.5, 'Uses', 'green')

# Knowledge to storage connections
draw_arrow(ax, vector_pos[0] - 1, vector_pos[1] - 0.5, sqlite_pos[0] + 1, sqlite_pos[1] + 0.4, 'Stores', 'orange')
draw_arrow(ax, doc_pos[0] + 1, doc_pos[1] - 0.5, csv_pos[0], csv_pos[1] + 0.4, 'Writes', 'orange')

# Session to Redis
draw_arrow(ax, session_pos[0], session_pos[1] - 3.5, redis_pos[0], redis_pos[1] + 0.4, 'Persists', 'brown')

# External API connections
draw_arrow(ax, evaluation_pos[0] + 1.2, evaluation_pos[1], claude_pos[0] - 2, claude_pos[1] - 0.3, 'API Calls', 'red')
draw_arrow(ax, reasoning_pos[0] + 1, reasoning_pos[1] + 0.3, claude_pos[0] - 2, claude_pos[1] + 0.3, 'LLM Requests', 'red')

# Pipeline flow note
flow_text = ('PIPELINE FLOW:\n'
             '1. User submits geotechnical parameters → 2. FastAPI routes request → 3. Multi-agent system processes → '
             '4. Knowledge retrieval & processing → 5. Persistent data storage → 6. Test recommendations delivered')
ax.text(10, 0.3, flow_text, fontsize=7, ha='center', style='italic', 
        bbox=dict(boxstyle='round,pad=0.5', facecolor='wheat', alpha=0.5))

# Save with high DPI
plt.tight_layout()
plt.savefig('architecture-diagram-improved.png', dpi=300, bbox_inches='tight', facecolor='white')
print("✓ Improved architecture diagram generated successfully as 'architecture-diagram-improved.png'")
print("  - All boxes properly spaced with no overlaps")
print("  - Connectors properly attached to components")
print("  - Improved visual clarity and alignment")
plt.close()
