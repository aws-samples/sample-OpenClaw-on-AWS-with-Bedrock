"""
ClawForge Blog — Chart & Diagram Generation
Generates all PNG visualizations for the blog post.
Run: python3 enterprise/blog/generate_charts.py
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
import numpy as np
import os

OUT = os.path.join(os.path.dirname(__file__), "charts")
os.makedirs(OUT, exist_ok=True)

# Color palette (matches ClawForge dark theme)
BG = '#0f1117'
CARD = '#1a1d27'
BORDER = '#2a2d3a'
PRIMARY = '#6366f1'
ORANGE = '#f97316'
GREEN = '#22c55e'
RED = '#ef4444'
YELLOW = '#f59e0b'
CYAN = '#06b6d4'
TEXT = '#e2e8f0'
MUTED = '#94a3b8'
WHITE = '#ffffff'


def setup_dark(fig, ax):
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(CARD)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.spines['bottom'].set_color(BORDER)
    ax.spines['left'].set_color(BORDER)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_color(MUTED)


# ============================================================
# Chart 1: Three-Layer SOUL Architecture
# ============================================================
def chart_soul_layers():
    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.axis('off')

    layers = [
        (0.5, 3.8, 'LAYER 1: GLOBAL (IT Locked)', '#ef4444', 
         'Company policies · Security red lines · Data handling\n"Never share customer PII. Never execute rm -rf."'),
        (0.5, 2.4, 'LAYER 2: POSITION (Dept Admin)', '#3b82f6',
         'Role expertise · Tool permissions · Knowledge scope\n"You are a Finance Analyst. Use excel-gen, not shell."'),
        (0.5, 1.0, 'LAYER 3: PERSONAL (Employee)', '#22c55e',
         'Communication preferences · Custom instructions\n"I prefer concise answers. Always use TypeScript."'),
    ]

    for x, y, title, color, desc in layers:
        box = FancyBboxPatch((x, y), 9, 1.0, boxstyle="round,pad=0.1",
                             facecolor=color + '15', edgecolor=color, linewidth=2)
        ax.add_patch(box)
        ax.text(x + 0.3, y + 0.7, title, fontsize=11, fontweight='bold', color=color, family='monospace')
        ax.text(x + 0.3, y + 0.2, desc, fontsize=9, color=MUTED)

    # Arrow down to merged
    ax.annotate('', xy=(5, 0.6), xytext=(5, 0.9),
                arrowprops=dict(arrowstyle='->', color=ORANGE, lw=2))
    ax.text(3.2, 0.15, 'MERGED SOUL.md', fontsize=12, fontweight='bold', color=ORANGE, family='monospace')
    ax.text(6.5, 0.15, '(what OpenClaw reads)', fontsize=9, color=MUTED)

    ax.set_xlim(0, 10.5)
    ax.set_ylim(-0.2, 5.2)
    fig.tight_layout()
    fig.savefig(f'{OUT}/01-soul-three-layers.png', dpi=150, bbox_inches='tight')
    plt.close()
    print("  01-soul-three-layers.png")


# ============================================================
# Chart 2: System Architecture
# ============================================================
def chart_system_architecture():
    fig, ax = plt.subplots(figsize=(12, 7))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.axis('off')

    def draw_box(x, y, w, h, label, sublabel, color, filled=False):
        fc = color + '20' if not filled else color
        box = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.08",
                             facecolor=fc, edgecolor=color, linewidth=1.5)
        ax.add_patch(box)
        tc = WHITE if filled else color
        ax.text(x + w/2, y + h*0.65, label, fontsize=9, fontweight='bold', color=tc, ha='center', family='monospace')
        if sublabel:
            ax.text(x + w/2, y + h*0.25, sublabel, fontsize=7, color=MUTED, ha='center')

    # User touchpoints
    draw_box(0.5, 6, 11, 0.8, 'USER TOUCHPOINTS', 'WhatsApp · Telegram · Slack · Discord · Web Portal · Admin Console', CYAN)

    # Gateway EC2
    draw_box(0.5, 3.8, 11, 1.8, '', '', MUTED)
    ax.text(1, 5.3, 'GATEWAY EC2 (~$52/mo)', fontsize=10, fontweight='bold', color=TEXT)
    draw_box(0.8, 4.0, 2.3, 0.9, 'OpenClaw GW', ':18789', PRIMARY)
    draw_box(3.4, 4.0, 2.3, 0.9, 'H2 Proxy', ':8091', PRIMARY)
    draw_box(6.0, 4.0, 2.5, 0.9, 'Tenant Router', ':8090', PRIMARY)
    draw_box(8.8, 4.0, 2.5, 0.9, 'Admin Console', ':8099 React+FastAPI', PRIMARY)

    # AgentCore
    draw_box(0.5, 1.5, 11, 1.8, '', '', ORANGE)
    ax.text(1, 3.0, 'BEDROCK AGENTCORE RUNTIME', fontsize=10, fontweight='bold', color=ORANGE)
    draw_box(0.8, 1.7, 3.2, 0.9, 'Firecracker microVM', 'per tenant, per request', ORANGE)
    draw_box(4.3, 1.7, 2.5, 0.9, 'workspace_assembler', '3-layer SOUL merge', ORANGE)
    draw_box(7.1, 1.7, 2.0, 0.9, 'OpenClaw CLI', 'Bedrock LLM', ORANGE)
    draw_box(9.4, 1.7, 1.9, 0.9, 'Watchdog', 'S3 sync 60s', ORANGE)

    # AWS Services
    services = [('DynamoDB', 'org, audit\nusage'), ('S3', 'SOUL, skills\nmemory'), 
                ('SSM', 'tenant map\nAPI keys'), ('Bedrock', 'Nova 2 Lite\nSonnet, Pro'), ('CloudWatch', 'agent logs')]
    for i, (name, desc) in enumerate(services):
        x = 0.8 + i * 2.2
        draw_box(x, 0.1, 1.9, 0.9, name, desc, GREEN)

    # Arrows
    ax.annotate('', xy=(5.5, 5.8), xytext=(5.5, 6.0), arrowprops=dict(arrowstyle='->', color=MUTED, lw=1.5))
    ax.annotate('', xy=(5.5, 3.6), xytext=(5.5, 3.8), arrowprops=dict(arrowstyle='->', color=MUTED, lw=1.5))
    ax.annotate('', xy=(5.5, 1.3), xytext=(5.5, 1.5), arrowprops=dict(arrowstyle='->', color=MUTED, lw=1.5))

    ax.set_xlim(0, 12)
    ax.set_ylim(-0.3, 7.2)
    fig.tight_layout()
    fig.savefig(f'{OUT}/02-system-architecture.png', dpi=150, bbox_inches='tight')
    plt.close()
    print("  02-system-architecture.png")


# ============================================================
# Chart 3: Request Flow (Tenant ID Resolution)
# ============================================================
def chart_request_flow():
    fig, ax = plt.subplots(figsize=(12, 4))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.axis('off')

    steps = [
        ('Telegram\nMessage', CYAN),
        ('OpenClaw\nGateway', PRIMARY),
        ('H2 Proxy\n(intercept)', PRIMARY),
        ('Tenant\nRouter', PRIMARY),
        ('AgentCore\nmicroVM', ORANGE),
        ('workspace\nassembler', ORANGE),
        ('OpenClaw\nCLI', ORANGE),
        ('Bedrock\nNova 2 Lite', GREEN),
    ]

    for i, (label, color) in enumerate(steps):
        x = i * 1.4 + 0.3
        box = FancyBboxPatch((x, 0.5), 1.1, 1.2, boxstyle="round,pad=0.08",
                             facecolor=color + '20', edgecolor=color, linewidth=1.5)
        ax.add_patch(box)
        ax.text(x + 0.55, 1.1, label, fontsize=8, fontweight='bold', color=color, ha='center', va='center', family='monospace')
        if i < len(steps) - 1:
            ax.annotate('', xy=(x + 1.25, 1.1), xytext=(x + 1.1, 1.1),
                        arrowprops=dict(arrowstyle='->', color=MUTED, lw=1.2))

    # Labels below
    labels = ['Carol says\n"Run git status"', '', 'extract\nchannel+user', 'derive\ntenant_id', 'Firecracker\nisolation', 'merge 3\nSOUL layers', 'Plan A:\nDENY shell', 'Agent refuses\nshell access']
    for i, lbl in enumerate(labels):
        if lbl:
            x = i * 1.4 + 0.85
            ax.text(x, 0.2, lbl, fontsize=7, color=MUTED, ha='center', va='top')

    ax.set_xlim(0, 12)
    ax.set_ylim(-0.3, 2.2)
    fig.tight_layout()
    fig.savefig(f'{OUT}/03-request-flow.png', dpi=150, bbox_inches='tight')
    plt.close()
    print("  03-request-flow.png")


# ============================================================
# Chart 4: Cost Comparison
# ============================================================
def chart_cost_comparison():
    fig, ax = plt.subplots(figsize=(8, 4))
    setup_dark(fig, ax)

    products = ['ChatGPT\nTeam', 'Microsoft\nCopilot', 'ClawForge\non AgentCore']
    costs = [500, 600, 65]
    colors = [MUTED, MUTED, ORANGE]

    bars = ax.barh(products, costs, color=colors, height=0.5, edgecolor='none')
    for bar, cost in zip(bars, costs):
        ax.text(bar.get_width() + 10, bar.get_y() + bar.get_height()/2,
                f'${cost}/mo', va='center', fontsize=11, fontweight='bold',
                color=ORANGE if cost == 65 else MUTED)

    ax.set_xlabel('Monthly Cost (20 users)', color=MUTED, fontsize=10)
    ax.set_title('Cost Comparison: 20 AI Agents', color=TEXT, fontsize=13, fontweight='bold', pad=12)
    ax.set_xlim(0, 750)
    ax.invert_yaxis()

    # Savings annotation
    ax.text(400, 2.3, '85% cheaper', fontsize=14, fontweight='bold', color=GREEN, ha='center')

    fig.tight_layout()
    fig.savefig(f'{OUT}/04-cost-comparison.png', dpi=150, bbox_inches='tight')
    plt.close()
    print("  04-cost-comparison.png")


# ============================================================
# Chart 5: Permission System (Plan A + Plan E)
# ============================================================
def chart_permission_system():
    fig, ax = plt.subplots(figsize=(10, 4.5))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.axis('off')

    # Plan A box
    draw_rounded = lambda x, y, w, h, c, lbl, sub: (
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.08",
                     facecolor=c + '15', edgecolor=c, linewidth=1.5)),
        ax.text(x + w/2, y + h*0.7, lbl, fontsize=10, fontweight='bold', color=c, ha='center'),
        ax.text(x + w/2, y + h*0.3, sub, fontsize=8, color=MUTED, ha='center')
    )

    # Timeline
    ax.text(0.5, 3.8, 'PERMISSION ENFORCEMENT PIPELINE', fontsize=11, fontweight='bold', color=TEXT)

    draw_rounded(0.5, 2.5, 2.5, 1.0, CYAN, 'Message In', 'Carol: "Run git status"')
    draw_rounded(3.5, 2.5, 2.5, 1.0, '#ef4444', 'PLAN A', 'Pre-Execution Check\nSOUL.md: DENY shell')
    draw_rounded(6.5, 2.5, 2.5, 1.0, ORANGE, 'LLM Response', 'Agent self-enforces\nbased on SOUL rules')
    draw_rounded(3.5, 0.8, 2.5, 1.0, '#f59e0b', 'PLAN E', 'Post-Execution Audit\nScan for blocked tools')
    draw_rounded(6.5, 0.8, 2.5, 1.0, GREEN, 'DynamoDB Log', 'Audit trail written\nAI Insights analysis')

    # Arrows
    for x1, y1, x2, y2 in [(3.0, 3.0, 3.5, 3.0), (6.0, 3.0, 6.5, 3.0), (7.75, 2.5, 7.75, 1.8), (6.0, 1.3, 6.5, 1.3)]:
        ax.annotate('', xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle='->', color=MUTED, lw=1.2))

    # Blocked path
    ax.text(4.75, 2.1, 'BLOCKED', fontsize=9, fontweight='bold', color=RED, ha='center')
    ax.annotate('', xy=(4.75, 0.8), xytext=(4.75, 2.5), arrowprops=dict(arrowstyle='->', color=RED, lw=1.5, linestyle='dashed'))

    ax.set_xlim(0, 9.5)
    ax.set_ylim(0.3, 4.2)
    fig.tight_layout()
    fig.savefig(f'{OUT}/05-permission-system.png', dpi=150, bbox_inches='tight')
    plt.close()
    print("  05-permission-system.png")


# ============================================================
# Chart 6: RBAC Data Scoping
# ============================================================
def chart_rbac_scoping():
    fig, ax = plt.subplots(figsize=(9, 4))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.axis('off')

    roles = [
        ('ADMIN', '#ef4444', 'Zhang San', 'All 20 employees\nAll 22 agents\nAll departments\nFull settings access', 0.5),
        ('MANAGER', '#f59e0b', 'Lin Xiaoyu', 'Product dept only (3 emp)\nOwn dept agents\nBFS sub-dept rollup\nNo settings', 3.5),
        ('EMPLOYEE', '#22c55e', 'Carol Zhang', 'Own data only\nPortal: Chat, Profile\nUsage, Skills, Requests\nNo admin pages', 6.5),
    ]

    for label, color, name, desc, x in roles:
        box = FancyBboxPatch((x, 0.5), 2.5, 3.0, boxstyle="round,pad=0.1",
                             facecolor=color + '10', edgecolor=color, linewidth=2)
        ax.add_patch(box)
        ax.text(x + 1.25, 3.2, label, fontsize=12, fontweight='bold', color=color, ha='center', family='monospace')
        ax.text(x + 1.25, 2.6, name, fontsize=9, color=TEXT, ha='center')
        ax.text(x + 1.25, 1.5, desc, fontsize=8, color=MUTED, ha='center', va='center', linespacing=1.5)

    # Arrows showing scope reduction
    ax.annotate('', xy=(3.3, 2.0), xytext=(3.0, 2.0), arrowprops=dict(arrowstyle='->', color=MUTED, lw=1.5))
    ax.annotate('', xy=(6.3, 2.0), xytext=(6.0, 2.0), arrowprops=dict(arrowstyle='->', color=MUTED, lw=1.5))
    ax.text(3.15, 2.4, 'scope\nnarrows', fontsize=7, color=MUTED, ha='center')
    ax.text(6.15, 2.4, 'scope\nnarrows', fontsize=7, color=MUTED, ha='center')

    ax.set_xlim(0, 9.5)
    ax.set_ylim(0, 4.0)
    fig.tight_layout()
    fig.savefig(f'{OUT}/06-rbac-scoping.png', dpi=150, bbox_inches='tight')
    plt.close()
    print("  06-rbac-scoping.png")


# ============================================================
# Generate all
# ============================================================
if __name__ == '__main__':
    print("Generating ClawForge blog charts...")
    chart_soul_layers()
    chart_system_architecture()
    chart_request_flow()
    chart_cost_comparison()
    chart_permission_system()
    chart_rbac_scoping()
    print(f"\nDone! {len(os.listdir(OUT))} charts in {OUT}/")
