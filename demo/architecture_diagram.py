#!/usr/bin/env python3
"""OpenClaw Enterprise Multi-Tenant Architecture Diagram — Large Font"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

fig, ax = plt.subplots(1, 1, figsize=(36, 24))
ax.set_xlim(0, 36)
ax.set_ylim(0, 24)
ax.axis('off')
fig.patch.set_facecolor('#FAFAFA')

C_USER = '#FF6B6B'
C_GW = '#4ECDC4'
C_AC = '#45B7D1'
C_S3 = '#F9A825'
C_SSM = '#AB47BC'
C_CW = '#66BB6A'
C_ECR = '#EF5350'
C_DDB = '#FF7043'
C_BDR = '#37474F'

def box(x, y, w, h, color, lines, alpha=0.85):
    b = FancyBboxPatch((x,y), w, h, boxstyle="round,pad=0.2",
                       facecolor=color, edgecolor=C_BDR, linewidth=2, alpha=alpha)
    ax.add_patch(b)
    total = len(lines)
    for i, (txt, sz, fw) in enumerate(lines):
        offset = (total-1)/2 - i
        ax.text(x+w/2, y+h/2+offset*0.38, txt, ha='center', va='center',
                fontsize=sz, fontweight=fw, color='white')

def arrow(x1,y1,x2,y2, label=None, color='#546E7A', lw=2.5):
    ax.annotate('', xy=(x2,y2), xytext=(x1,y1),
                arrowprops=dict(arrowstyle='->', color=color, lw=lw,
                                mutation_scale=20))
    if label:
        mx,my = (x1+x2)/2, (y1+y2)/2
        ax.text(mx, my+0.35, label, ha='center', va='center', fontsize=13,
                color=color, style='italic',
                bbox=dict(boxstyle='round,pad=0.2', fc='white', ec='none', alpha=0.9))

def region(x,y,w,h, label):
    r = FancyBboxPatch((x,y), w, h, boxstyle="round,pad=0.4",
                       facecolor='white', edgecolor='#232F3E', linewidth=3, alpha=0.3)
    ax.add_patch(r)
    if label:
        ax.text(x+0.5, y+h-0.5, label, fontsize=15, color='#232F3E',
                fontweight='bold', alpha=0.7)

# ── Title ──
ax.text(18, 23.3, 'OpenClaw Enterprise Multi-Tenant Architecture', ha='center',
        fontsize=28, fontweight='bold', color='#212121')
ax.text(18, 22.7, 'EC2 Gateway + AgentCore Runtime + S3 Workspace Sync  |  Zero OpenClaw Modification',
        ha='center', fontsize=15, color='#78909C')

# ── Users ──
for name, x in [('WhatsApp',2),('Telegram',8),('Discord',14),('Slack',20),('Feishu',26)]:
    box(x, 20.5, 3.5, 1.2, C_USER, [(name, 16, 'bold')])

# ── AWS Cloud ──
region(0.5, 0.8, 35, 19.2, 'AWS Cloud')

# ── EC2 Gateway ──
region(1.2, 13.0, 16.6, 6.5, '')
ax.text(9.5, 19.0, 'EC2 Gateway (Always-On)', ha='center', fontsize=20,
        fontweight='bold', color=C_GW)

box(1.6, 16.0, 7.5, 2.2, C_GW, [
    ('OpenClaw Gateway', 16, 'bold'),
    ('Node.js  |  port 18789', 13, 'normal'),
    ('Channels  |  Web UI  |  Cron', 13, 'normal'),
])
box(9.8, 16.0, 7.5, 2.2, C_GW, [
    ('Tenant Router', 16, 'bold'),
    ('Python  |  port 8090', 13, 'normal'),
    ('Auth  |  tenant_id  |  Queue', 13, 'normal'),
])
box(1.6, 13.4, 7.5, 2.0, '#26A69A', [
    ('Admin Console', 16, 'bold'),
    ('Org Tree  |  Permissions  |  Audit', 12, 'normal'),
])
box(9.8, 13.4, 7.5, 2.0, '#26A69A', [
    ('Auth Agent', 16, 'bold'),
    ('Risk Assessment  |  Approval  |  30min TTL', 12, 'normal'),
])

# ── AgentCore Runtime ──
region(1.2, 4.5, 16.6, 7.8, '')
ax.text(9.5, 11.8, 'AgentCore Runtime  (Serverless, On-Demand)', ha='center',
        fontsize=18, fontweight='bold', color=C_AC)

# microVM 1
box(1.6, 4.9, 7.5, 6.2, C_AC, [], alpha=0.15)
ax.text(5.35, 10.7, 'Firecracker microVM', ha='center', fontsize=15,
        fontweight='bold', color=C_AC)
ax.text(5.35, 10.2, 'tenant: wa__sarah', ha='center', fontsize=13, color='#546E7A')
box(2.0, 8.6, 6.7, 1.2, '#0288D1', [
    ('entrypoint.sh', 15, 'bold'),
    ('S3 pull > start > watchdog > flush', 12, 'normal'),
])
box(2.0, 7.1, 6.7, 1.2, '#0277BD', [
    ('server.py (wrapper)', 15, 'bold'),
    ('Plan A inject  |  Plan E audit', 12, 'normal'),
])
box(2.0, 5.2, 6.7, 1.6, '#01579B', [
    ('OpenClaw (native)', 16, 'bold'),
    ('Unmodified  |  Zero invasion', 13, 'normal'),
    ('SOUL.md  |  MEMORY.md  |  Skills', 12, 'normal'),
])

# microVM 2
box(9.8, 4.9, 7.5, 6.2, C_AC, [], alpha=0.15)
ax.text(13.55, 10.7, 'Firecracker microVM', ha='center', fontsize=15,
        fontweight='bold', color=C_AC)
ax.text(13.55, 10.2, 'tenant: tg__alex', ha='center', fontsize=13, color='#546E7A')
box(10.2, 8.6, 6.7, 1.2, '#0288D1', [
    ('entrypoint.sh', 15, 'bold'),
    ('S3 pull > start > watchdog > flush', 12, 'normal'),
])
box(10.2, 7.1, 6.7, 1.2, '#0277BD', [
    ('server.py (wrapper)', 15, 'bold'),
    ('Plan A inject  |  Plan E audit', 12, 'normal'),
])
box(10.2, 5.2, 6.7, 1.6, '#01579B', [
    ('OpenClaw (native)', 16, 'bold'),
    ('Unmodified  |  Zero invasion', 13, 'normal'),
    ('SOUL.md  |  MEMORY.md  |  Skills', 12, 'normal'),
])

# ── AWS Services (right side) ──
SX = 19.5
SW = 7.2
SH = 2.8

box(SX, 16.5, SW, SH, C_S3, [
    ('S3', 18, 'bold'),
    ('openclaw-tenants/', 13, 'normal'),
    ('{tenant_id}/workspace/', 13, 'normal'),
    ('_shared/skills/  |  templates/', 13, 'normal'),
])
box(SX+SW+0.6, 16.5, SW, SH, C_SSM, [
    ('SSM Parameter Store', 16, 'bold'),
    ('Permission profiles', 13, 'normal'),
    ('SOUL templates  |  Tokens', 13, 'normal'),
    ('Runtime ID  |  Config', 13, 'normal'),
])
box(SX, 13.0, SW, SH, C_ECR, [
    ('ECR', 18, 'bold'),
    ('Agent Container image', 13, 'normal'),
    ('OpenClaw + Python wrapper', 13, 'normal'),
    ('Pre-installed shared skills', 13, 'normal'),
])
box(SX+SW+0.6, 13.0, SW, SH, '#FF8F00', [
    ('Amazon Bedrock', 16, 'bold'),
    ('Nova  |  Claude  |  DeepSeek', 13, 'normal'),
    ('IAM auth (no API keys)', 13, 'normal'),
    ('Per-tenant metering', 13, 'normal'),
])
box(SX, 9.5, SW, SH, C_CW, [
    ('CloudWatch Logs', 16, 'bold'),
    ('Structured JSON per tenant', 13, 'normal'),
    ('Filter by tenant_id', 13, 'normal'),
    ('Compliance audit trail', 13, 'normal'),
])
box(SX+SW+0.6, 9.5, SW, SH, C_DDB, [
    ('DynamoDB (optional)', 16, 'bold'),
    ('Chat history (multi-turn)', 13, 'normal'),
    ('Cron task config', 13, 'normal'),
    ('Approval tokens w/ TTL', 13, 'normal'),
])
box(SX, 6.0, SW, SH, '#78909C', [
    ('IAM Roles', 16, 'bold'),
    ('Least privilege per microVM', 13, 'normal'),
    ('S3 path isolation', 13, 'normal'),
    ('Bedrock InvokeModel only', 13, 'normal'),
])
box(SX+SW+0.6, 6.0, SW, SH, '#90A4AE', [
    ('CloudTrail', 16, 'bold'),
    ('Every Bedrock API call', 13, 'normal'),
    ('Full audit chain', 13, 'normal'),
    ('SOC2 / HIPAA ready', 13, 'normal'),
])

# ── Arrows ──
for _, x in [('W',2),('T',8),('D',14),('S',20),('F',26)]:
    arrow(x+1.75, 20.5, 9.5, 19.2, color=C_USER, lw=1.5)

arrow(9.1, 17.1, 9.8, 17.1, 'route', color=C_GW, lw=2)
arrow(13.55, 16.0, 5.35, 11.0, 'invoke(sessionId)', color=C_AC, lw=3)
arrow(13.55, 16.0, 13.55, 11.0, '', color=C_AC, lw=3)
arrow(8.7, 7.5, SX, 17.8, 'S3 sync (pull/push)', color=C_S3, lw=2)
arrow(16.9, 7.5, SX, 17.3, '', color=C_S3, lw=2)
arrow(17.3, 14.4, SX+SW+0.6, 17.8, 'read profiles', color=C_SSM, lw=1.5)
arrow(16.9, 6.0, SX+SW+0.6, 14.0, 'InvokeModel', color='#FF8F00', lw=2)
arrow(8.7, 5.5, SX, 10.8, 'audit logs', color=C_CW, lw=1.5)
arrow(SX, 14.0, 16.9, 9.5, 'pull image', color=C_ECR, lw=1.5)

# ── Legend ──
ly = 3.0
ax.text(1.5, ly+0.6, 'Data Flow:', fontsize=15, fontweight='bold', color='#212121')
legends = [
    ('1. Message -> EC2 Gateway (channel long-connection)', C_USER),
    ('2. Gateway auth + derive tenant_id -> invoke AgentCore', C_GW),
    ('3. microVM starts -> entrypoint.sh pulls workspace from S3', C_S3),
    ('4. OpenClaw runs natively -> Bedrock inference -> Plan A/E enforcement', C_AC),
    ('5. Response -> Gateway -> forward to user  |  workspace sync back to S3', '#546E7A'),
]
for i, (txt, c) in enumerate(legends):
    col = i % 3
    row = i // 3
    ax.text(1.5 + col*11.5, ly - row*0.6, txt, fontsize=12, color=c, fontweight='bold')

# ── Key Insight ──
ax.text(18, 1.3, 'KEY: OpenClaw runs 100% unmodified inside each microVM. All orchestration happens outside.',
        ha='center', fontsize=16, fontweight='bold', color='#01579B',
        bbox=dict(boxstyle='round,pad=0.4', fc='#E3F2FD', ec='#01579B', alpha=0.8))

plt.tight_layout()
plt.savefig('images/architecture-multitenant.png', dpi=150, bbox_inches='tight',
            facecolor=fig.get_facecolor())
print("Done: images/architecture-multitenant.png")
plt.close()
