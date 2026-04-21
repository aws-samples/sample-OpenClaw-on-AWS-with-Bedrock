# OpenClaw Enterprise on AgentCore

Most enterprise AI platforms give everyone the same generic assistant. OpenClaw Enterprise gives each employee **an AI agent with role-specific identity, memory, tools, and security boundaries** — while giving IT full governance control over the entire fleet.

Built on [OpenClaw](https://github.com/openclaw/openclaw) (open-source AI assistant) + AWS Bedrock AgentCore. **Zero modification to OpenClaw source code** — all enterprise capabilities are achieved through configuration files, workspace assembly, and AWS-native services.

---

## The Governance Problem

An enterprise with 500 employees across 12 roles wants to deploy AI agents. The challenges aren't technical — they're organizational:

- The **Finance Analyst** agent must never execute shell commands, but the **SDE** agent needs shell access daily
- When the **CISO updates a compliance policy**, all 500 agents must adopt it — immediately, without touching each one individually
- **IT needs to see** every tool call, every permission denial, every conversation — across all agents, all IM channels, all departments
- An employee who **changes departments** should automatically get a new agent identity, new tools, new knowledge — without a support ticket
- The **CEO's agent** should use Claude Sonnet 4.6 with full tool access; the **intern's agent** should use Nova Lite with web search only

ChatGPT Team and Microsoft Copilot can't do any of this. They give everyone the same agent with the same capabilities.

---

## Our Answer: Organization-Driven Agent Governance

### Three-Layer SOUL Identity

The core design: **one SOUL configuration per role, not per person.** 5 departments × 12 positions = governance over 500 agents.

```
┌─────────────────────────────────────────────────────────┐
│  Layer 1: GLOBAL (IT locked — CISO + CTO approval)      │
│  Company policies, security red lines, data handling     │
│  "Never share customer PII. Never execute rm -rf."       │
├─────────────────────────────────────────────────────────┤
│  Layer 2: POSITION (Department admin managed)            │
│  Role expertise, tool permissions, knowledge scope       │
│  "You are a Finance Analyst. Use excel-gen, not shell."  │
├─────────────────────────────────────────────────────────┤
│  Layer 3: PERSONAL (Employee self-service)               │
│  Communication preferences, custom instructions          │
│  "I prefer concise answers. Always respond in Chinese."  │
└─────────────────────────────────────────────────────────┘
                        ↓ merge
              Final SOUL.md (what the agent reads)
```

**No lower layer can override a higher one.** An employee who writes "Ignore all company rules" in their personal layer is still bound by the Global layer — it's prepended with a `CRITICAL IDENTITY OVERRIDE` marker that the model reads first.

### Five-Layer Security (Defense in Depth)

| Layer | Mechanism | Bypassed by prompt injection? |
|-------|-----------|-------------------------------|
| L1 — SOUL Rules | Prompt-level behavioral constraints | ⚠️ Theoretically possible |
| L2 — Tool Permissions (Plan A) | Per-position allowlist in DynamoDB, injected before SOUL | ⚠️ Depends on model compliance |
| **L3 — IAM** | **Per-runtime IAM role — Finance role has no S3 cross-dept access** | **Impossible** |
| **L4 — Compute Isolation** | **Firecracker microVM per agent (hardware boundary)** | **Impossible** |
| **L5 — Bedrock Guardrail** | **Content filter on every input + output (PII, topics, injection)** | **Impossible** |

L3-L5 are infrastructure boundaries. No prompt, no matter how clever, can bypass an IAM policy or escape a Firecracker VM.

### IT Governance Controls

| Control | What IT Gets |
|---------|-------------|
| **SOUL Editor** | Global rules locked by IT. Position SOUL managed by department admins. Employees edit only their personal layer. |
| **4-Tier Runtime Model** | Standard / Restricted / Engineering / Executive — each with its own model, IAM role, guardrail. Assign positions to tiers from UI. |
| **Audit Center** | Every invocation, tool call, permission denial, SOUL change, IM pairing, guardrail block → DynamoDB. 5 insight detectors surface patterns. |
| **Skill Governance** | 26 skills with position-level assignment. Employees can request access; IT approves/denies. |
| **Usage & Cost** | Per-employee, per-department, per-model breakdown. Model-aware pricing ($0.30–$75/1M tokens). Department budgets. |
| **IM Management** | Every employee's IM connections visible to admin. One-click revoke. Channel health + enrollment stats. |
| **RBAC** | Admin (full org) · Manager (department-scoped) · Employee (portal only). JWT-enforced on every API call. |

### Auto-Provisioning: Org Chart Drives Everything

```
Admin creates employee with positionId="pos-fa" (Finance Analyst)
  ↓ auto-provision:
  ① Agent created (inherits position's skills + default channel)
  ② 1:1 binding created (employee ↔ agent)
  ③ S3 workspace seeded (PERSONAL_SOUL.md, USER.md, MEMORY.md)
  ④ Audit entry written

Employee logs in → sees their Finance Analyst Agent → ready to chat.
Change position to pos-sde → agent reconfigured automatically.
```

---

## What Makes This Different

| Capability | ChatGPT Team | Microsoft Copilot | **OpenClaw Enterprise** |
|-----------|-------------|-------------------|-------------------|
| Per-role agent identity | ❌ Same for all | ❌ Same for all | ✅ 3-layer SOUL per position |
| Tool permissions per role | ❌ | ❌ | ✅ Plan A allowlist + IAM + Guardrail |
| Org-driven agent management | ❌ | ❌ | ✅ Department → Position → Employee hierarchy |
| IT audit trail | ❌ | Limited | ✅ Every action logged to DynamoDB |
| Self-hosted, data in your VPC | ❌ | ❌ | ✅ Bedrock in your account, zero data egress |
| IM integration (10 platforms) | ❌ | Teams only | ✅ Telegram, Slack, Discord, Feishu, WhatsApp... |
| Scheduled tasks / cron | ❌ | ❌ | ✅ EventBridge + Always-on agents |
| Cost: 50 employees | $1,250/mo | $1,500/mo | **~$160-220/mo** |
| Open source | ❌ | ❌ | ✅ OpenClaw + AWS native |

---

## Security: Additional Controls

- No public ports (SSM Session Manager only)
- IAM roles throughout, no hardcoded credentials
- Gateway token in SSM SecureString, never on disk
- VPC isolation between runtime tiers
- First-login forced password change (bcrypt hashed per employee)

For detailed compute isolation comparison across runtimes (AgentCore vs ECS vs EKS vs Kata), see [SECURITY.md](SECURITY.md#compute-isolation-enterprise-multi-tenant).

---

## Three Deployment Modes

Every agent uses the same Docker image. Admin chooses deployment mode per position — no code changes needed.

| | Serverless (AgentCore) | Always-on (ECS Fargate) | EKS (Kubernetes) |
|-|----------------------|------------------------|------------------|
| **Cold start** | ~10s first msg, ~3s warm | None — always running | None — pod always running |
| **Best for** | 90% of employees | Exec assistants, cron tasks, direct IM bots | Container-native infra, China regions |
| **Cost** | Pay per invocation | ~$17/mo per agent | Cluster cost + per-pod |
| **Storage** | S3 sync (60s watchdog) | EFS persistent volume | PVC |

**[→ EKS Deployment Guide (EN)](docs/DEPLOYMENT_EKS.md)** · **[→ EKS 部署指南 (中文)](docs/DEPLOYMENT_EKS_CN.md)**

### Flagship Features

| Feature | What It Does |
|---------|-------------|
| **Digital Twin** | Employee turns on a public link. Anyone with the URL can chat with their AI agent while they're away — agent responds using their SOUL, memory, and expertise. Twin sessions are isolated from the employee's main session |
| **Always-on Agents** | Admin toggles any agent to persistent ECS Fargate mode. Enables scheduled tasks (email every 3 min), direct IM bot connections, instant response. Same image, same SOUL — just a deployment mode switch |
| **Portal Agent Switcher** | Employees with both Serverless and Always-On agents see a sidebar toggle to switch between modes. Chat, IM binding, usage, and skills all respond to the selected agent type automatically |
| **Dual Agent Tabs** | Agent Detail shows [Serverless] and [Always-On] tabs with independent config, status, IM channels, and audit per mode. Position change detection warns admins when container needs restart |
| **Fargate Security Center** | Card-based Fargate management — Configure, New Template, cost summary bar, bulk Start All / Stop All. Per-tier runtime config with model, IAM role, guardrail assignment |
| **IM Credential Forms** | Always-On agents use per-channel credential input (Feishu app-id/secret, Telegram token, Slack bot/app token) instead of shared-bot pairing. Webhook URL auto-generated and displayed |
| **Session Storage** | AgentCore persists workspace files across microVM stop/resume cycles. No S3 re-download on session resume. Combined with `StopRuntimeSession` API for admin-triggered config refresh |
| **Three-Layer SOUL** | Global (IT) → Position (dept admin) → Personal (employee). 3 stakeholders, 3 layers, one merged identity. Same LLM — Finance Analyst vs SDE have completely different personalities and permissions |
| **Self-Service IM Pairing** | Employee scans QR code from Portal → connects Telegram / Feishu / Discord in 30 seconds. No IT ticket, no admin approval |
| **Multi-Runtime Architecture** | 4-tier system: Standard, Restricted, Engineering, Executive — each with its own model, IAM role, guardrail, and security group. Assign positions to tiers from Security Center UI |
| **Bedrock Guardrails (L5)** | Assign any Bedrock Guardrail to a Runtime from Security Center UI. Topic denial, PII filtering, and compliance policies wrap every user input and agent output — no OpenClaw source code changes needed. Standard employees get blocked; exec tier is unrestricted. Full block audit trail in Audit Center. |
| **Org Directory KB** | Company directory (every employee, R&R, contact, agent capabilities) seeded from org data and injected into every agent — agents know who to contact and can draft messages for you |
| **Position → Runtime Routing** | 3-tier routing chain: employee override → position rule → default. Assign positions to runtimes from Security Center UI, propagates to all members automatically |
| **Per-Employee Model Config** | Override model, context window, compaction settings, and response language at position OR employee level from Agent Factory → Configuration tab |
| **IM Channel Management** | Admin sees every employee's IM connections grouped by channel — when they paired, session count, last active, one-click disconnect with reason field for audit trail |
| **Org CRUD** | Full create/edit/delete for Departments, Positions, and Employees from Admin Console. Delete is guarded: blocks if employees or agent assignments exist, prompts force-cascade delete with Always-On cleanup details |
| **Security Center** | Live AWS resource browser — ECR images, IAM roles, VPC security groups with console links. Configure runtime images, IAM roles, and Fargate tier templates from the UI |
| **Session Storage + Memory** | Serverless: Session Storage persists workspace across microVM cycles + S3 writeback for admin visibility. Always-on: EFS workspace + Gateway compaction. Same memory across Discord, Telegram, Feishu, and Portal |
| **Dynamic Config, Zero Redeploy** | Change model, tool permissions, SOUL content, or KB assignments → propagates via config version poll (5 min) or instant via `StopRuntimeSession`. No container rebuild, no runtime update |

---

## Live Demo

> **https://openclaw.awspsa.com**
>
> A real running instance with 7 departments, 11 positions, 20+ employees, 20+ AI agents, IM channels (Telegram, Feishu, Discord + Portal), 4-tier runtime architecture (Standard/Restricted/Engineering/Executive), and always-on ECS Fargate agents — all backed by DynamoDB + S3 on AWS.
>
> **Everything here is real.** Every button works. Every chart reads from real data. Every agent runs on Bedrock AgentCore in isolated Firecracker microVMs.
>
> **Try the Digital Twin:** Login as any employee → Portal → My Profile → Toggle **Digital Twin** ON → get a public URL → open it in an incognito window and chat with the AI version of that employee.
>
> Need a demo account? Contact [wjiad@aws](mailto:wjiad@amazon.com) to get access.
>
> **Interactive UI Guide:** [ui-guide.html](https://aws-samples.github.io/sample-OpenClaw-on-AWS-with-Bedrock/ui-guide.html) — 25-page bilingual (EN/CN) documentation covering architecture, deployment, every admin and portal page, with screenshots.

### Screenshots

| Admin Dashboard | Agent Factory |
|:-:|:-:|
| ![Dashboard](enterprise/demo/images/new-01-dashboard.png) | ![Agent Factory](enterprise/demo/images/new-02-agent-factory.png) |

| Security Center — 4-Tier Runtime | Employee Portal Chat |
|:-:|:-:|
| ![Security Center](enterprise/demo/images/new-03-security-center.png) | ![Portal Chat](enterprise/demo/images/new-05-portal-chat.png) |

| Usage & Cost | Tools & Skills |
|:-:|:-:|
| ![Usage](enterprise/demo/images/new-04-usage.png) | ![Skills](enterprise/demo/images/new-06-skills.png) |

---

## Design Principles

### Design Principles

#### 1. Zero Invasion to OpenClaw

We don't fork, patch, or modify a single line of OpenClaw source code. Instead, we control agent behavior entirely through OpenClaw's native workspace file system:

```
workspace/
├── SOUL.md            ← Agent identity & rules (assembled from 3 layers)
├── AGENTS.md          ← Workflow definitions
├── TOOLS.md           ← Tool permissions
├── USER.md            ← Employee preferences
├── MEMORY.md          ← Persistent memory
├── memory/            ← Daily memory files (per-turn checkpoint)
├── knowledge/         ← Position-scoped + global documents (KB-injected)
├── skills/            ← Role-filtered skill packages
├── IDENTITY.md        ← Employee name + position (generated, not editable)
├── CHANNELS.md        ← Employee's bound IM channels (for outbound notifications)
└── SESSION_CONTEXT.md ← Access path + caller identity (written once at cold start)
```

The `workspace_assembler` merges Global + Position + Personal layers into these files before OpenClaw reads them. OpenClaw doesn't know it's running in an enterprise context — it just reads its workspace as usual.

`SESSION_CONTEXT.md` is the access path identity file. It is written **once per cold start** by `workspace_assembler` and encodes exactly which access path triggered this session, verified by the `session_id` prefix the Tenant Router assigns:

| Session Prefix | Access Path | Content Written |
|----------------|-------------|-----------------|
| `emp__emp-id__` | Employee Portal + all bound IM channels (shared session) | Authenticated user name, "Verification: Confirmed" |
| `pt__emp-id__` | Portal (legacy alias, same behavior as `emp__`) | Same as above |
| `pgnd__emp-id__` | Playground — IT admin testing as this employee | "Admin Test Session, read-only memory" |
| `twin__emp-id__` | Digital Twin — external caller, no auth required | "Caller unverified, conversations visible to employee in Portal" |
| `admin__...` | IT Admin Assistant | "Authorized IT Administrator" |
| `tg__`, `dc__`, etc. | Raw IM fallback (unresolved user, before pairing) | "Standard Session" |

**Why this matters:** Without SESSION_CONTEXT.md, the agent cannot distinguish Portal from Playground from Digital Twin — all three would access the same workspace and respond identically. With it, Playground explicitly tells the agent not to write back to employee memory, and Digital Twin tells the agent the caller is unverified and the conversation is visible to the represented employee.

#### 2. Serverless-First + Always-on Hybrid

**Default: Serverless.** Every agent runs in isolated Firecracker microVMs via Bedrock AgentCore. Session Storage persists workspace files across stop/resume — no S3 re-download on session resume.

**Admin toggle: Always-on.** Any agent can be switched to a persistent ECS Fargate container — same Docker image, same SOUL, same code path. The difference is infrastructure: the container stays alive, enabling scheduled tasks, direct IM connections, and instant response.

```
Request
  ↓
Tenant Router — 3-tier routing:
  1. Always-on check (SSM /tenants/{emp_id}/always-on-agent)
     → routes to ECS Fargate container (private VPC IP)
  2. Position rule (DynamoDB CONFIG#routing or SSM /positions/{pos_id}/runtime-id)
     → routes to AgentCore Runtime for that position
  3. Default AgentCore Runtime
```

| | Serverless (AgentCore) | Always-on (ECS Fargate) |
|-|----------------------|------------------------|
| Cold start | ~6s first message, ~2-3s session resume | None — container always running |
| Scheduled tasks | Deferred to next invocation | Fires on schedule (HEARTBEAT) |
| Direct IM bot | No — routes through Gateway EC2 | Yes — dedicated bot token in container |
| Idle cost | Memory only ($0.08/day per 1 GB session) | ~$0.55/day (0.5 vCPU + 1 GB Fargate) |
| Persistence | Session Storage (1 GB, auto-managed) | EFS (unlimited, durable) |
| Best for | Individual employees (majority) | Customer service, exec assistants, high-frequency cron |

**Every agent is "shared" by nature** — an employee's agent serves the employee themselves, their Digital Twin visitors, and potentially other assigned employees. "Shared vs personal" is just how many employees the admin assigns, not a separate infrastructure type.

#### 2.1 Multi-Runtime Architecture (Defense in Depth)

Different employee groups can be assigned to different AgentCore Runtimes, each backed by its own Docker image and IAM role:

```
Runtime: Standard (Sales / HR / Support)
  ├── Model:   Amazon Nova 2 Lite (cost-optimized)
  ├── IAM:     Own S3 workspace only · Own DynamoDB partition
  └── Guardrail: Moderate (PII filter + topic denial)

Runtime: Restricted (Finance / Legal)
  ├── Model:   DeepSeek v3.2 (balanced)
  ├── IAM:     Own workspace + department read
  └── Guardrail: Strict (PII + compliance + data sovereignty)

Runtime: Engineering (SDE / DevOps / QA)
  ├── Model:   Claude Sonnet 4.5 (coding-optimized)
  ├── IAM:     Own workspace + cross-dept engineering read
  └── Guardrail: None (full tool access)

Runtime: Executive (C-Suite / Senior Leadership)
  ├── Model:   Claude Sonnet 4.6 (highest capability)
  ├── IAM:     Full S3 access · Cross-department DynamoDB · All Bedrock models
  └── Guardrail: None (unrestricted)
```

Each runtime tier has its own IAM role and optional Bedrock Guardrail — see [Security](#security-hardware-level-isolation-at-every-layer) above for the full 5-layer model. Manage tiers from **Security Center → Runtimes** with card-based UI, Configure button, cost summary, and bulk operations.

#### 3. Digital Twin — AI Availability Beyond Office Hours

Every employee can generate a public shareable URL for their agent:

```
Employee toggles Digital Twin ON
  ↓
Gets: https://your-domain.com/twin/{secure-token}
  ↓
Anyone with the link can chat (no login required)
  ↓
Agent responds using employee's SOUL + memory + expertise
Agent introduces itself: "I'm [Name]'s AI assistant..."
  ↓
Employee turns it OFF → link immediately revoked
```

**Use cases:** Out-of-office assistant · Sales agent always available · Technical SME accessible to anyone · Async collaboration across timezones

#### 4. Three-Layer SOUL Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Layer 1: GLOBAL (IT locked — CISO + CTO approval)      │
│  Company policies, security red lines, data handling     │
│  "Never share customer PII. Never execute rm -rf."       │
├─────────────────────────────────────────────────────────┤
│  Layer 2: POSITION (Department admin managed)            │
│  Role expertise, tool permissions, knowledge scope       │
│  "You are a Finance Analyst. Use excel-gen, not shell."  │
├─────────────────────────────────────────────────────────┤
│  Layer 3: PERSONAL (Employee self-service)               │
│  Communication preferences, custom instructions          │
│  "I prefer concise answers. Always use TypeScript."      │
└─────────────────────────────────────────────────────────┘
                        ↓ merge
              Final SOUL.md (what OpenClaw reads)
```

#### 5. Knowledge Assembly at Session Start

When an agent starts a new session, `workspace_assembler` injects:

1. **Global KB** (org directory, company policies) — available to every agent
2. **Position KB** (Engineering docs for SAs, Finance docs for FAs) — scoped by role
3. **Employee KB** — individual overrides

The org directory KB (seeded via `seed_knowledge_docs.py`, refreshed by re-running the script after org changes) gives every agent the ability to answer: *"Who should I contact for X?"* and *"How do I reach [name]?"*

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Admin Console (React + FastAPI)                                 │
│  ├── 30+ pages: Dashboard, Agent Factory (dual Serverless/AO     │
│  │   tabs), Security Center (Fargate card management),            │
│  │   IM Channels, Monitor, Audit, Usage & Cost, Settings          │
│  ├── Employee Portal: Chat (agent mode switcher), Profile,        │
│  │   Skills, Requests, Connect IM (pairing + credential forms),   │
│  │   My Agents, Digital Twin toggle                                │
│  ├── 3-role RBAC (admin / manager / employee)                    │
│  └── IT Admin Assistant (Claude API, 10 whitelisted tools)       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  PATH 1: IT Admin Assistant                                      │
│  ┌────────────────────────────────────────────────────────┐      │
│  │  Admin Console floating chat bubble (admin role only)  │      │
│  │    session_id prefix: admin__                          │      │
│  │    SESSION_CONTEXT.md → "IT Admin Assistant"           │      │
│  │    Claude API direct (not AgentCore), 10 whitelisted   │      │
│  │    tools, no shell, no subprocess                      │      │
│  └────────────────────────────────────────────────────────┘      │
│                                                                  │
│  PATH 2: Playground (IT admin testing as employee)               │
│  ┌────────────────────────────────────────────────────────┐      │
│  │  Admin Console → Agents → Playground tab               │      │
│  │    session_id prefix: pgnd__emp-id__                   │      │
│  │    SESSION_CONTEXT.md → "Playground (Admin Test),      │      │
│  │      read-only with respect to memory"                 │      │
│  │    Reads employee's workspace; no write-back           │      │
│  └────────────────────────────────────────────────────────┘      │
│                                                                  │
│  PATH 3: Employee Portal (webchat, authenticated)                │
│  PATH 4: IM Channels (Telegram/Feishu/Discord/Slack — bound)    │
│  ┌────────────────────────────────────────────────────────┐      │
│  │  Paths 3 and 4 share the SAME AgentCore session        │      │
│  │    H2 Proxy enforces IM pairing: unpaired IM → rejected │      │
│  │    Tenant Router resolves channel user_id → emp_id     │      │
│  │    session_id prefix: emp__emp-id__  (both paths)      │      │
│  │    SESSION_CONTEXT.md → "Employee Session, Verified"   │      │
│  │    Full read/write to employee workspace               │      │
│  │    → 3-tier routing: always-on? → position? → default  │      │
│  │    → AgentCore (Firecracker microVM per emp-id)        │      │
│  │    → workspace_assembler: SOUL + IDENTITY + channels   │      │
│  │    → OpenClaw + Bedrock → Response                     │      │
│  └────────────────────────────────────────────────────────┘      │
│                                                                  │
│  PATH 5: Digital Twin (public URL, no auth)                      │
│  ┌────────────────────────────────────────────────────────┐      │
│  │  GET /twin/{token} → public HTML chat page             │      │
│  │  POST /public/twin/{token}/chat                        │      │
│  │    Lookup token → employee_id                          │      │
│  │    session_id prefix: twin__emp-id__                   │      │
│  │    SESSION_CONTEXT.md → "Digital Twin, caller          │      │
│  │      unverified, visible to employee in Portal"        │      │
│  │    Separate twin_workspace (not employee's main)       │      │
│  └────────────────────────────────────────────────────────┘      │
│                                                                  │
│  PATH C: Always-on Agents (ECS Fargate)                          │
│  ┌────────────────────────────────────────────────────────┐      │
│  │  Same Docker image, ECS Fargate task with:             │      │
│  │    SHARED_AGENT_ID={agent_id}                          │      │
│  │    EFS mount at /mnt/efs (per-employee workspace)      │      │
│  │    Optional: TELEGRAM_BOT_TOKEN for direct IM          │      │
│  │  Container self-registers VPC IP in SSM on startup     │      │
│  │  Tenant Router routes assigned employees to task IP    │      │
│  │  Supports scheduled tasks (HEARTBEAT), direct IM,      │      │
│  │    customer service bots, exec assistants               │      │
│  └────────────────────────────────────────────────────────┘      │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│  AWS Services                                                    │
│  ├── DynamoDB — org, agents, assignments, audit, usage, config,   │
│  │              Digital Twin tokens, KB assignments              │
│  ├── S3 — SOUL templates, skills, workspaces, knowledge,        │
│  │         org directory, per-employee memory, admin visibility  │
│  ├── SSM — tenant→position, position→runtime, user-mappings,    │
│  │          permissions, always-on endpoints                     │
│  ├── Bedrock — LLM inference (Nova 2 Lite default, Sonnet 4.6  │
│  │              for exec tier, per-position overrides supported) │
│  ├── AgentCore — Session Storage (1 GB/session, auto-managed)   │
│  ├── ECS Fargate — Always-on containers + EFS workspace         │
│  └── CloudWatch — agent invocation logs, runtime events         │
└─────────────────────────────────────────────────────────────────┘
```

## Gateway Architecture: One Bot, All Employees

The OpenClaw Gateway serves as the unified IM connection layer for the entire organization. In the reference deployment, it runs on a single EC2 instance; production environments can scale horizontally behind a load balancer.

```
IT Admin (one-time setup):
  Discord  → Create 1 Bot "ACME Agent" → Connect to Gateway
  Telegram → Create 1 Bot @acme_bot    → Connect to Gateway
  Feishu   → Create 1 Enterprise Bot   → Connect to Gateway

All employees use the same Bot, but each gets their own Agent:

  Carol DMs @ACME Agent → H2 Proxy extracts user_id → Tenant Router
    → pos-fa → Standard Runtime → Finance Analyst SOUL → Bedrock → reply

  WJD DMs @ACME Agent → H2 Proxy extracts user_id → Tenant Router
    → pos-exec → Executive Runtime → Sonnet 4.6 → full tools → reply
```

### Employee Self-Service IM Onboarding

```
Step 1: Employee opens Portal → Connect IM
Step 2: Selects channel (Telegram / Feishu / Discord)
Step 3: Scans QR code with their phone → bot opens automatically
Step 4: Bot sends /start TOKEN → paired instantly, no admin approval
Step 5: Employee chats with their AI agent directly in their IM app
```

Zero IT friction. Employees self-service in 30 seconds. Admins see all connections in IM Channels page and can revoke any connection.

## Key Features

| Feature | How It Works |
|---------|-------------|
| **Digital Twin** | Employee toggles ON → gets a public URL. Anyone chats with their AI agent, no login required. Agent uses employee's SOUL + memory. Toggle OFF revokes instantly |
| **Always-on Agents** | Admin toggles any agent to ECS Fargate mode via dual-tab UI. Same Docker image, persistent container with EFS workspace. 4-tier security model (Standard/Restricted/Engineering/Executive). Enables scheduled tasks, direct IM bot, instant response. Tenant Router routes to Fargate task VPC IP via SSM |
| **SOUL Injection** | 3-layer merge (Global + Position + Personal) at session start. Position SOUL warnings in editor when edits affect N agents |
| **Permission Control** | SOUL.md defines allowed/blocked tools per role. Plan A (pre-execution) + Plan E (post-audit). Exec profile bypasses Plan A entirely |
| **Multi-Runtime** | 4-tier system (Standard / Restricted / Engineering / Executive) with per-tier model, IAM, and guardrail. Assign positions to runtimes from Security Center UI |
| **Self-service IM Pairing** | QR code scan + `/start TOKEN` → SSM mapping written instantly. Supports Telegram, Feishu, Discord |
| **Org Directory KB** | Seeded from org data via `seed_knowledge_docs.py`. Injected into every agent's workspace. Agents know who to contact for what |
| **Per-employee Config** | Override model, `recentTurnsPreserve`, `maxTokens`, response language at position OR employee level. Zero redeploy |
| **Position → Runtime Routing** | 3-tier: employee SSM override → position SSM rule → default. UI in Security Center assigns positions |
| **Memory Persistence** | Serverless: Session Storage persists workspace across microVM cycles + S3 writeback for admin visibility. Always-on: EFS + Gateway compaction. Cross-channel memory shared (IM + Portal = same session) |
| **IM Channel Management** | Per-channel employee table: paired date, session count, last active, disconnect button |
| **Knowledge Base** | Markdown files in S3. Assign KBs to positions from Knowledge Base → Assignments tab. Injected at session start |
| **Skill Filtering** | 26 skills with `allowedRoles`/`blockedRoles`. Finance gets excel-gen, SDE gets github-pr, DevOps gets aws-cli |
| **Agent Config** | Memory compaction, context window, language per position → Agent Factory → Configuration tab |
| **IT Admin Assistant** | Floating chat bubble (admin only). Claude API + 10 whitelisted tools. No shell, no subprocess |
| **Security Center** | Live AWS resource browser: ECR images, IAM roles, VPC security groups with console deep-links |

## Security Model

| Layer | Mechanism | Detail |
|-------|-----------|--------|
| **Network** | No open ports | SSM port forwarding or CloudFront (origin restricted) |
| **Credentials** | AWS SSM SecureString | `ADMIN_PASSWORD`, `JWT_SECRET`, Digital Twin tokens encrypted in SSM |
| **Compute** | Firecracker microVM + ECS Fargate | Each serverless agent in its own microVM. Always-on agents in isolated ECS Fargate tasks with EFS |
| **IAM** | Least privilege + runtime tiers | Standard role: own S3/DynamoDB only. Executive role: cross-department. Can't escalate via prompt |
| **Data** | Role-based scoping | Admin: all. Manager: own dept (BFS rollup). Employee: own only. API-enforced |
| **Agent** | SOUL permission control | Plan A pre-execution allowlist. Plan E post-response audit. Exec profile opts out |
| **Audit** | Comprehensive logging | Every invocation, tool call, permission denial, SOUL change, IM pairing, guardrail blocks → DynamoDB |
| **Digital Twin** | Token-based access | Secure token in URL, stored in DynamoDB. Employee revokes instantly. View/chat counts tracked |
| **L5 Guardrail** | AWS Bedrock Guardrails | Per-Runtime content policy. `apply_guardrail()` called on INPUT before forwarding to OpenClaw and on OUTPUT before returning to user. Standard Runtime: topic denial + PII. Exec Runtime: no guardrail. Every block logged to DynamoDB `guardrail_block` audit event. |

## For AI Assistants (Kiro / Claude Code / Cursor / etc.)

> **User:** Copy this section and paste it to your AI to get hands-on deployment help.

---

**What you're deploying:** A multi-tenant enterprise AI platform — a gateway layer (Tenant Router + Admin Console) routes requests to isolated Firecracker microVMs via AWS Bedrock AgentCore. One Docker image serves both AgentCore (serverless) and ECS Fargate (always-on). 4 AgentCore Runtimes (Standard/Restricted/Engineering/Executive), one DynamoDB table, one S3 bucket, one ECS cluster with 4 tier services.

**Run Docker builds on the gateway EC2, not the user's local machine.** `deploy.sh` handles this automatically — packages source → S3 → EC2 builds via SSM (ARM64 Graviton). No local Docker required.

**Critical — these will silently break deployment if missed:**

1. **OpenClaw is pinned to `2026.3.24`** in `agent-container/Dockerfile` and `exec-agent/Dockerfile` — do not upgrade. Newer versions changed Gateway response delivery and break IM channel integration. (The single-user EC2 template supports `2026.4.5+` but Enterprise remains on `2026.3.24`.)
2. **Docker build takes 10–15 min** — `clawhub install` installs skills one by one. This is normal.
3. **After `update-agent-runtime`**, poll until `status: READY` before testing.
4. **DynamoDB is in `us-east-2` by default** (AgentCore is `us-east-1`). This is intentional — DynamoDB cross-region access is free.
5. **deploy.sh creates 4 Fargate tier services** with `desiredCount=0`. Admin activates per-employee always-on from Security Center UI.
6. **All 20 seed employees share one `ADMIN_PASSWORD`** — each must change on first login. Roles are seeded separately by `seed_roles.py`.

**Verify it works** (after deployment):
- Login as `emp-jiade` (admin) → Dashboard shows 13 depts, 11 positions, 20 employees
- Playground → Carol Zhang (Finance) → "run git status" → refused (Restricted tier)
- Playground → Ryan Park (SDE) → "run git status" → executes (Engineering tier)
- Portal → Carol Zhang → Chat → "Who are you?" → "Finance Analyst Agent at ACME Corp"
- Security Center → 4 runtimes all READY

**If AgentCore returns 500:** check CloudWatch group `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT` for `openclaw returned empty output` — wrong openclaw version. Rebuild with `openclaw@2026.3.24`.

---

## Quick Start

> **TL;DR** — Three commands to deploy:
> ```bash
> cd enterprise
> cp .env.example .env        # edit: STACK_NAME, REGION, ADMIN_PASSWORD
> bash deploy.sh              # ~15 min — infra + Docker build + seed
> ```
> Then follow **Step 4–6** below to deploy the Admin Console and Gateway services on EC2.

### Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| AWS CLI | v2.27+ | `bedrock-agentcore-control` requires 2.27+ |
| Node.js | 18+ | For Admin Console frontend build |
| Python | 3.10+ | For seed scripts and backend |
| SSM Plugin | Latest | [Install guide](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) |

> **No local Docker required** — the agent container image is built on the gateway EC2 (ARM64 Graviton) via SSM.

**AWS requirements:**
- Bedrock model access is automatic — no manual enablement required
- Bedrock AgentCore available in: `us-east-1`, `us-west-2`
- IAM permissions: `cloudformation:*`, `ec2:*`, `iam:*`, `ecr:*`, `s3:*`, `ssm:*`, `bedrock:*`, `dynamodb:*`, `ecs:*`, `efs:*`

### Step 1: Configure and Deploy

```bash
cd enterprise           # from repo root
cp .env.example .env    # copy config template
```

Open `.env` and fill in the required values:

```bash
STACK_NAME=openclaw-enterprise   # your stack name
REGION=us-east-1                 # us-east-1 or us-west-2 (AgentCore regions)
ADMIN_PASSWORD=your-password     # initial password (employees must change on first login)

# Optional: use existing VPC instead of creating a new one
# EXISTING_VPC_ID=vpc-0abc123
# EXISTING_SUBNET_ID=subnet-0abc123

# Optional: custom S3 bucket name — required when deploying multiple stacks in the same account
# (e.g. staging + production in the same AWS account)
# WORKSPACE_BUCKET_NAME=openclaw-tenants-123456789-staging
```

Then run the deploy script — it handles everything, **including the Docker build on the gateway EC2 (no local Docker required)**:

```bash
bash deploy.sh
# ~15 minutes total: CloudFormation → EC2 Docker build → AgentCore Runtime → DynamoDB seed
```

To re-deploy after code changes without rebuilding the Docker image or re-seeding:

```bash
bash deploy.sh --skip-build   # update infra only, skip Docker build
bash deploy.sh --skip-seed    # update infra + image, skip DynamoDB
```

**What `deploy.sh` does automatically (end-to-end):**
1. Validates prerequisites and deploys CloudFormation (EC2, ECR, S3, IAM, ECS cluster — creates or updates)
2. Packages source code → uploads to S3 → **triggers Docker build on the gateway EC2 via SSM** (ARM64 Graviton, no local Docker needed)
3. Creates or updates AgentCore Runtime(s)
4. Creates DynamoDB table if it doesn't exist
4.5. Sets up **ECS Fargate tier services** (Standard/Restricted/Engineering/Executive) with tier-specific models, task definitions, and `desiredCount=0` (admin activates via Security Center)
5. Uploads SOUL templates + skills to S3
6. Seeds org data (employees, positions, departments, knowledge docs)
7. Stores `ADMIN_PASSWORD` and `JWT_SECRET` in SSM SecureString
8. Builds Admin Console frontend → packages → deploys to EC2 via SSM
9. Deploys Gateway services (Tenant Router, Bedrock H2 Proxy) to EC2
10. Writes `/etc/openclaw/env` with all required variables (`STACK_NAME`, `DYNAMODB_TABLE`, `DYNAMODB_REGION`, ECS config, etc.)
11. Configures systemd services and starts all components
12. Adds ECS→SSM VPC endpoint security group rule (if VPC endpoints exist)

After deployment, get the instance ID and S3 bucket:

```bash
STACK_NAME="openclaw-enterprise"   # match your .env
REGION="us-east-1"

INSTANCE_ID=$(aws cloudformation describe-stacks --stack-name $STACK_NAME --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' --output text)
S3_BUCKET=$(aws cloudformation describe-stacks --stack-name $STACK_NAME --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`TenantWorkspaceBucketName`].OutputValue' --output text)
echo "EC2: $INSTANCE_ID  |  S3: $S3_BUCKET"
```

### Step 1.5: Build and Push Exec-Agent Image (Executive Tier)

The Executive Runtime uses a separate Docker image (`exec-agent/`) with all skills pre-installed and Claude Sonnet 4.6. `deploy.sh` builds the standard image automatically; the exec image must be pushed separately:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_EXEC="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${STACK_NAME}-exec-agent"

aws ecr get-login-password --region $REGION | \
  docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

docker build --platform linux/arm64 \
  -f enterprise/exec-agent/Dockerfile \
  -t "${ECR_EXEC}:latest" .

docker push "${ECR_EXEC}:latest"
```

Then update the Exec Runtime to pick up the new image:

```bash
EXEC_RUNTIME_ID=$(aws ssm get-parameter \
  --name "/openclaw/${STACK_NAME}/exec-runtime-id" \
  --query Parameter.Value --output text --region $REGION 2>/dev/null)

EXEC_ROLE=$(aws cloudformation describe-stacks --stack-name $STACK_NAME --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`AgentContainerExecutionRoleArn`].OutputValue' --output text)

aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id "$EXEC_RUNTIME_ID" \
  --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${ECR_EXEC}:latest\"}}" \
  --role-arn "$EXEC_ROLE" \
  --network-configuration '{"networkMode":"PUBLIC"}' \
  --environment-variables "{\"AWS_REGION\":\"${REGION}\",\"BEDROCK_MODEL_ID\":\"global.anthropic.claude-sonnet-4-6\",\"S3_BUCKET\":\"${S3_BUCKET}\",\"STACK_NAME\":\"${STACK_NAME}\",\"DYNAMODB_TABLE\":\"${STACK_NAME}\",\"DYNAMODB_REGION\":\"${DYNAMODB_REGION}\",\"SYNC_INTERVAL\":\"120\"}" \
  --region $REGION
```

> The standard agent image (`openclaw-multitenancy-multitenancy-agent`) is built automatically by `deploy.sh`. You only need this step for the executive tier.

### Step 2: DynamoDB Table

> **`deploy.sh` handles this automatically.** No manual steps needed.

<details><summary>Manual steps (only if not using deploy.sh)</summary>

```bash
# Create table (idempotent — safe to run if it already exists)
aws dynamodb create-table \
  --table-name $STACK_NAME \
  --attribute-definitions \
    AttributeName=PK,AttributeType=S AttributeName=SK,AttributeType=S \
    AttributeName=GSI1PK,AttributeType=S AttributeName=GSI1SK,AttributeType=S \
  --key-schema AttributeName=PK,KeyType=HASH AttributeName=SK,KeyType=RANGE \
  --global-secondary-indexes '[{"IndexName":"GSI1","KeySchema":[
    {"AttributeName":"GSI1PK","KeyType":"HASH"},{"AttributeName":"GSI1SK","KeyType":"RANGE"}
  ],"Projection":{"ProjectionType":"ALL"}}]' \
  --billing-mode PAY_PER_REQUEST \
  --region $DYNAMODB_REGION
```


</details>

### Step 3: Seed Sample Organization

> **`deploy.sh` handles this automatically.** To re-seed manually (e.g. after org changes):

<details><summary>Manual seed commands</summary>

```bash
cd enterprise/admin-console/server
pip install boto3 requests

DYNAMODB_REGION=us-east-2

python3 seed_dynamodb.py              --region $DYNAMODB_REGION
python3 seed_roles.py                 --region $DYNAMODB_REGION
python3 seed_settings.py              --region $DYNAMODB_REGION
python3 seed_audit_approvals.py       --region $DYNAMODB_REGION
python3 seed_usage.py                 --region $DYNAMODB_REGION
python3 seed_routing_conversations.py --region $DYNAMODB_REGION
python3 seed_ssm_tenants.py           --region $REGION --stack $STACK_NAME

export S3_BUCKET AWS_REGION=$REGION
python3 seed_skills_final.py
python3 seed_all_workspaces.py        --bucket $S3_BUCKET --region $REGION
python3 seed_knowledge_docs.py        --bucket $S3_BUCKET --region $REGION
```


</details>

### Steps 4-5: Admin Console + Gateway Services

> **`deploy.sh` handles Steps 4, 4.5, and 5 automatically.** It builds the Admin Console, deploys Gateway services, writes `/etc/openclaw/env`, and starts all systemd services.

<details><summary>Manual steps (only if not using deploy.sh)</summary>

**Step 4: Deploy Admin Console**

```bash
cd enterprise/admin-console
npm install && npm run build
cd ../..

COPYFILE_DISABLE=1 tar czf /tmp/admin-deploy.tar.gz -C enterprise/admin-console dist server start.sh
aws s3 cp /tmp/admin-deploy.tar.gz "s3://${S3_BUCKET}/_deploy/admin-deploy.tar.gz"

aws ssm send-command --instance-ids $INSTANCE_ID --region $REGION \
  --document-name AWS-RunShellScript \
  --parameters "{\"commands\":[
    \"python3 -m venv /opt/admin-venv\",
    \"/opt/admin-venv/bin/pip install fastapi uvicorn boto3 requests python-multipart anthropic\",
    \"aws s3 cp s3://${S3_BUCKET}/_deploy/admin-deploy.tar.gz /tmp/admin-deploy.tar.gz --region $REGION\",
    \"mkdir -p /opt/admin-console && tar xzf /tmp/admin-deploy.tar.gz -C /opt/admin-console\",
    \"chown -R ubuntu:ubuntu /opt/admin-console /opt/admin-venv\",
    \"chmod +x /opt/admin-console/start.sh\",
    \"systemctl daemon-reload && systemctl enable openclaw-admin && systemctl start openclaw-admin\"
  ]}"
```

Store secrets in SSM:
```bash
aws ssm put-parameter --name "/openclaw/${STACK_NAME}/admin-password" \
  --value "<YOUR_PASSWORD>" --type SecureString --overwrite --region $REGION
aws ssm put-parameter --name "/openclaw/${STACK_NAME}/jwt-secret" \
  --value "$(openssl rand -hex 32)" --type SecureString --overwrite --region $REGION
```

**Step 5: Deploy Gateway Services**

```bash
aws s3 cp enterprise/gateway/tenant_router.py       "s3://${S3_BUCKET}/_deploy/tenant_router.py"
aws s3 cp enterprise/gateway/bedrock_proxy_h2.js    "s3://${S3_BUCKET}/_deploy/bedrock_proxy_h2.js"
aws s3 cp enterprise/gateway/bedrock-proxy-h2.service "s3://${S3_BUCKET}/_deploy/bedrock-proxy-h2.service"
aws s3 cp enterprise/gateway/tenant-router.service  "s3://${S3_BUCKET}/_deploy/tenant-router.service"

aws ssm send-command --instance-ids $INSTANCE_ID --region $REGION \
  --document-name AWS-RunShellScript \
  --parameters "{\"commands\":[
    \"pip3 install boto3 requests\",
    \"aws s3 cp s3://${S3_BUCKET}/_deploy/tenant_router.py /home/ubuntu/tenant_router.py --region $REGION\",
    \"aws s3 cp s3://${S3_BUCKET}/_deploy/bedrock_proxy_h2.js /home/ubuntu/bedrock_proxy_h2.js --region $REGION\",
    \"aws s3 cp s3://${S3_BUCKET}/_deploy/bedrock-proxy-h2.service /etc/systemd/system/bedrock-proxy-h2.service --region $REGION\",
    \"aws s3 cp s3://${S3_BUCKET}/_deploy/tenant-router.service /etc/systemd/system/tenant-router.service --region $REGION\",
    \"systemctl daemon-reload && systemctl enable bedrock-proxy-h2 tenant-router && systemctl start bedrock-proxy-h2 tenant-router\"
  ]}"
```

</details>

### Step 6: Access Admin Console

```bash
aws ssm start-session --target $INSTANCE_ID --region $REGION \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8099"],"localPortNumber":["8199"]}'
```

Open **http://localhost:8199** → login with Employee ID `emp-jiade` (admin) and `ADMIN_PASSWORD` from your `.env`. First login requires setting a personal password.

> **Public access:** Use CloudFront with an Elastic IP on the EC2. Set `PUBLIC_URL` in `/etc/openclaw/env` (e.g. `PUBLIC_URL=https://your-domain.com`) for correct Digital Twin URLs — the admin console reads this file via `EnvironmentFile` in the systemd service.

### Step 7: Connect IM Channels (Optional)

```bash
# Get gateway token
aws ssm get-parameter --name "/openclaw/${STACK_NAME}/gateway-token" \
  --with-decryption --query Parameter.Value --output text --region $REGION

# Open gateway UI
aws ssm start-session --target $INSTANCE_ID --region $REGION \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'
# http://localhost:18789/?token=<token>
```

Employees self-service pair via Portal → Connect IM (QR code). No admin approval needed.

---

## What to Test

### 1. SOUL Injection (core differentiator)
Login as **Carol Zhang** (emp-carol, Finance) → Chat → "Who are you?" → **"ACME Corp Finance Analyst"**
Login as **Ryan Park** (emp-ryan, SDE) → Chat → "Who are you?" → **"ACME Corp Software Engineer"**
Same LLM. Completely different identities.

### 2. Digital Twin
Login as any employee → **Portal → My Profile → Digital Twin toggle**
Turn ON → copy the URL → open in incognito → chat with the AI version of that employee
Turn OFF → incognito tab gets 404 immediately

### 3. Org Directory (Knowledge Base)
Ask any agent: *"Who should I contact for a code review?"* or *"What does Marcus Bell do?"*
→ Agent reads `kb-org-directory` (seeded into every position) and answers with the right person's name, role, IM channel, and agent capabilities

### 4. Permission Boundaries (4-tier)
Carol Zhang (Restricted): "Run git status" → **Refused** (Finance, no shell)
Ryan Park (Engineering): "Run git status" → **Executed** (SDE, has shell)
Peter Wu (Executive): Any command → **Executed** (Executive tier, Sonnet 4.6)

### 5. Multi-Runtime
Login as **Peter Wu** (emp-peter) or **JiaDe Wang** (emp-jiade) → these route to the Executive AgentCore Runtime:
- Model: Claude Sonnet 4.6 (vs Nova 2 Lite for standard)
- Tools: all unlocked
- IAM: full S3, all Bedrock models, cross-dept DynamoDB

### 6. Memory Persistence
Chat as **JiaDe Wang** (Discord) → come back after 15 min → **agent recalls previous conversation**
Same memory shared across Discord, Telegram, and Portal.

> **How it works:** Each turn is synced to S3 immediately after the response (not just on session end). The next microVM downloads the workspace at session start and has full context. If memory doesn't appear, re-run `seed_all_workspaces.py` to reset S3 workspace state.

### 7. IM Channel Management (Admin)
Admin Console → **IM Channels** → select Discord tab → see JiaDe, David, Peter connected
→ view pairing date, session count, last active
→ click **Disconnect** on any employee

### 8. Security Center
Security Center → **Infrastructure tab** → see real ECR images, IAM roles, VPC security groups
Security Center → **Runtimes → Position Assignments** → change which runtime a position routes to

### 9. Agent Configuration
Agent Factory → **Configuration tab** → set Sonnet 4.5 for Solutions Architect
→ set `recentTurnsPreserve: 20` for Executive positions
→ set `language: 中文` for any position → agents default to Chinese

### 11. Bedrock Guardrails (L5 Content Policy)

Standard Runtime has `GUARDRAIL_ID` set as an environment variable. Every invocation goes through two checks in `server.py`: `apply_guardrail(source=INPUT)` before forwarding to OpenClaw, and `apply_guardrail(source=OUTPUT)` before returning the response. If either check returns `GUARDRAIL_INTERVENED`, the user gets the configured `blockedMessaging` instead of the agent's answer — OpenClaw is never even invoked for blocked inputs.

Exec Runtime has no `GUARDRAIL_ID` — the checks are skipped entirely. Same question, two different runtimes, two different outcomes. Every block is written to DynamoDB as a `guardrail_block` audit event visible in **Audit Center → Guardrail Events**.

To assign a guardrail to any runtime: **Security Center → Runtimes → Configure** → select from the Guardrail dropdown. To create a new guardrail: `aws bedrock create-guardrail ...` then it appears in the dropdown automatically.

### 10. Knowledge Base Assignments
Knowledge Base → **Assignments tab** → all positions are pre-assigned these KBs by default:

| KB | Scope | What agents get |
|----|-------|----------------|
| `kb-org-directory` | All | Full employee directory — who does what, how to reach them |
| `kb-policies` | All | Data handling, security baseline, code of conduct |
| `kb-onboarding` | All | New hire checklist, setup guide |
| `kb-arch` / `kb-runbooks` | Engineering | Architecture standards, runbooks |
| `kb-finance` | Finance | Financial reports and policies |
| `kb-hr` | HR | HR policies |

To add a new KB: Admin Console → Knowledge Base → upload Markdown → Assignments tab → assign to positions → agents pick it up on next cold start.

## Seed Accounts

> The seed data creates 20 employees across 11 positions. Roles are assigned by `seed_roles.py`: 2 admins, 3 managers, 15 employees. All share the initial `ADMIN_PASSWORD` and must change it on first login.

| Employee ID | Name | Role | Position | Dept | Runtime Tier | Channels |
|-------------|------|------|----------|------|-------------|----------|
| **emp-jiade** | **JiaDe Wang** | **admin** | Solutions Architect | Engineering | Executive | Discord, Slack |
| **emp-chris** | **Chris Morgan** | **admin** | DevOps Engineer | Platform Team | Engineering | Slack, Telegram |
| emp-alex | Alex Rivera | manager | Product Manager | Product | Standard | Slack |
| emp-mike | Mike Johnson | manager | Account Executive | Enterprise Sales | Standard | WhatsApp, Slack |
| emp-jenny | Jenny Liu | manager | HR Specialist | HR & Admin | Standard | Slack |
| emp-peter | Peter Wu | employee | Executive | Engineering | Executive | Discord |
| emp-ryan | Ryan Park | employee | Software Engineer | Backend Team | Engineering | Slack, Discord |
| emp-carol | Carol Zhang | employee | Finance Analyst | Finance | Restricted | Slack, Telegram |
| emp-rachel | Rachel Li | employee | Legal Counsel | Legal & Compliance | Restricted | Slack |
| emp-emma | Emma Chen | employee | Customer Success Manager | Customer Success | Standard | Slack, WhatsApp |
| emp-marcus | Marcus Bell | employee | Solutions Architect | Engineering | Executive | Slack, Telegram |
| emp-sophie | Sophie Turner | employee | Software Engineer | Backend Team | Engineering | Slack |
| emp-nathan | Nathan Brooks | employee | Software Engineer | Frontend Team | Engineering | Slack |
| emp-lisa | Lisa Chen | employee | DevOps Engineer | Platform Team | Engineering | Slack |
| emp-tony | Tony Reed | employee | QA Engineer | QA Team | Engineering | Slack |
| emp-sarah | Sarah Kim | employee | Account Executive | Enterprise Sales | Standard | WhatsApp |
| emp-tom | Tom Wilson | employee | Account Executive | SMB Sales | Standard | Slack |
| emp-priya | Priya Patel | employee | Product Manager | Product | Standard | Slack, Discord |
| emp-david | David Park | employee | Finance Analyst | Finance | Restricted | Slack |
| emp-daniel | Daniel Kim | employee | Solutions Architect | Engineering | Executive | Slack |

**Runtime tier assignment** (via Security Center → Position → Runtime mapping):
- **Executive**: Solutions Architect (pos-sa)
- **Engineering**: Software Engineer (pos-sde), DevOps Engineer (pos-devops), QA Engineer (pos-qa)
- **Restricted**: Finance Analyst (pos-fa), Legal Counsel (pos-legal)
- **Standard**: Account Executive (pos-ae), Product Manager (pos-pm), HR Specialist (pos-hr), Customer Success Manager (pos-csm), Executive (pos-exec)

## Environment Variables

### .env (deploy.sh input)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `STACK_NAME` | Yes | `openclaw-enterprise` | Names ALL AWS resources. Unique per account/region. |
| `REGION` | Yes | `us-east-1` | AWS region (must have Bedrock + AgentCore: `us-east-1` or `us-west-2`) |
| `ADMIN_PASSWORD` | Yes | — | Initial password for all accounts. Stored in SSM SecureString. Employees must change on first login. |
| `JWT_SECRET` | No | auto-generated | JWT signing key. Auto-generated with `openssl rand -hex 32` if empty. |
| `MODEL` | No | `global.amazon.nova-2-lite-v1:0` | Default Bedrock model ID for standard agents |
| `INSTANCE_TYPE` | No | `c7g.large` | EC2 Graviton ARM instance type |
| `KEY_PAIR` | No | — | EC2 key pair name for emergency SSH |
| `EXISTING_VPC_ID` | No | — | Reuse existing VPC instead of creating new |
| `EXISTING_SUBNET_ID` | No | — | Reuse existing subnet |
| `CREATE_VPC_ENDPOINTS` | No | `false` | Add Bedrock/SSM VPC endpoints (~$22/mo) |
| `DYNAMODB_TABLE` | No | same as STACK_NAME | Table name — **must equal STACK_NAME** (IAM policy scoped to `table/${StackName}`) |
| `DYNAMODB_REGION` | No | `us-east-2` | DynamoDB region if different from `REGION` |
| `WORKSPACE_BUCKET_NAME` | No | auto | S3 bucket name — set if multi-stack same account |
| `SKIP_DOCKER_BUILD` | No | `false` | Skip Docker build step (use existing image) |
| `SKIP_SEED` | No | `false` | Skip DynamoDB seeding |

### Runtime variables (written to /etc/openclaw/env by deploy.sh)

| Variable | Description |
|----------|-------------|
| `PUBLIC_URL` | Base URL for Digital Twin links — **set this** for correct twin URLs |
| `GATEWAY_INSTANCE_ID` | EC2 instance ID for always-on container management. Falls back to IMDSv2. |
| `CONSOLE_PORT` | Admin Console port (default: `8099`) |
| `TENANT_ROUTER_URL` | Tenant Router URL (default: `http://localhost:8090`) |
| `ECS_CLUSTER_NAME` | ECS cluster for Fargate always-on agents |
| `ECS_TASK_DEFINITION` | Fargate task definition ARN |
| `ECS_SUBNET_ID` | Subnet for Fargate tasks |
| `ECS_TASK_SG_ID` | Security group for Fargate tasks |

## Sample Organization

| | Count | Details |
|-|-------|---------|
| Departments | 13 | 5 Engineering (Platform/Backend/Frontend/QA), 3 Sales (Enterprise/SMB), Product, Finance, HR, CS, Legal |
| Positions | 11 | SA, SDE, DevOps, QA, AE, PM, FA, HR, CSM, Legal, Executive |
| Employees | 20 | 2 admin, 3 manager, 15 employee — each with workspace files in S3 |
| Agents | 20 | 1:1 bound to employees, serverless (default) + always-on (admin toggle) |
| Runtimes | 4 | Standard, Restricted, Engineering, Executive (per-tier model + IAM + guardrail) |
| IM Channels | 4 | Slack (most employees), Discord, Telegram, WhatsApp |
| Skills | 5 | S3-based skill packages (jina-reader, deep-research, etc.) |
| Knowledge Docs | 11 | Topic KBs (org-directory, policies, onboarding, arch, runbooks, finance, HR) |
| SOUL Templates | 12 | 1 global + 11 position-specific |
| RBAC Roles | 3 | Admin (2), Manager (3), Employee (15) |
| Seed Scripts | 11 | seed_dynamodb, seed_roles, seed_settings, seed_knowledge, seed_skills, etc. |

## Cost Estimate

### AgentCore Cost (50 employees, serverless)

| Component | Monthly Cost | Notes |
|-----------|-------------|-------|
| AgentCore sessions | ~$100-150 | Session memory idle ($88) + invocation CPU (~$20-50) |
| DynamoDB | ~$1 | Pay-per-request |
| S3 | < $1 | Workspaces, KBs, org directory |
| Bedrock (Nova 2 Lite) | ~$5-15 | ~100 conversations/day |

### Always-on Agents (ECS Fargate, optional)

| Component | Monthly Cost | Notes |
|-----------|-------------|-------|
| Fargate per agent | ~$17 | 0.5 vCPU + 1 GB, ARM64 Graviton, 24/7 |
| EFS | ~$7 | Elastic throughput + storage |

### Gateway Infrastructure

The gateway layer (Tenant Router, H2 Proxy, Admin Console) runs on EC2 or equivalent compute. A single `c7g.large` (~$52/mo) is sufficient for development and small deployments. Production environments should use HA architecture (ALB + Auto Scaling Group or ECS) based on the customer's availability requirements.

### Total Estimate

| Scenario | AgentCore | Always-on | Gateway | Bedrock | **Total** |
|----------|-----------|-----------|---------|---------|-----------|
| 50 employees, serverless only | $100-150 | — | ~$52+ | ~$10 | **~$160-220/mo** |
| + 2 always-on agents | $100-150 | $48 | ~$52+ | ~$10 | **~$210-260/mo** |

vs ChatGPT Team ($25 × 50 = $1,250/mo) or Copilot ($30 × 50 = $1,500/mo).

**AgentCore pricing advantage:** you don't pre-allocate CPU or memory — no instance sizing decisions. Idle sessions cost only memory ($0.00945/GB-hour). CPU is $0 when no one is chatting.

## Project Structure

```
enterprise/
├── README.md
├── TESTING.md                      # Comprehensive test plan (62+ test cases)
├── deploy.sh                       # One-click deployment (8 steps + Fargate tier setup)
├── clawdbot-bedrock-agentcore-multitenancy.yaml  # CloudFormation
├── admin-console/
│   ├── src/
│   │   ├── types/index.ts          # TypeScript types (DeployMode, Tier, AlwaysOnStatus, etc.)
│   │   ├── contexts/
│   │   │   └── PortalAgentContext.tsx  # Global agent type switcher (Serverless / Always-On)
│   │   └── pages/
│   │       ├── Dashboard.tsx           # Setup checklist + real-time stats
│   │       ├── AgentFactory/           # Dual Agent tabs + Configuration
│   │       ├── SecurityCenter.tsx      # Fargate card management + runtime config
│   │       ├── IMChannels.tsx          # Per-channel employee management
│   │       ├── Knowledge/index.tsx     # KB management + Assignments tab
│   │       ├── Usage.tsx               # Billing + Fargate cost card
│   │       ├── Settings.tsx            # Account, Logs, Assistant, Fargate config tab
│   │       ├── TwinChat.tsx            # Public Digital Twin page (no auth)
│   │       └── portal/
│   │           ├── Chat.tsx            # Agent mode badge + warmup indicator
│   │           ├── BindIM.tsx          # Pairing (serverless) + credential forms (always-on)
│   │           ├── MyAgents.tsx        # Dual agent cards
│   │           ├── MySkills.tsx        # agent_type-aware skill listing
│   │           ├── MyUsage.tsx         # agent_type-aware usage
│   │           ├── MyRequests.tsx      # Tool/skill access requests
│   │           └── Profile.tsx         # USER.md + memory + Digital Twin + deploy mode
│   └── server/
│       ├── main.py                 # App bootstrap — CORS, auth middleware
│       ├── auth.py                 # JWT auth + UserContext
│       ├── db.py                   # DynamoDB single-table + Digital Twin CRUD
│       ├── password.py             # bcrypt password hashing
│       ├── routers/                # 17 domain routers (130+ API endpoints)
│       │   ├── org.py agents.py bindings.py knowledge.py
│       │   ├── portal.py playground.py monitor.py audit.py
│       │   ├── usage.py settings.py security.py
│       │   ├── admin_im.py admin_ai.py admin_always_on.py
│       │   ├── gateway_proxy.py twin.py
│       │   └── __init__.py
│       └── seed_*.py               # Sample data scripts
├── agent-container/                # AgentCore Docker image (OpenClaw 2026.3.24)
│   ├── Dockerfile                  # Pinned openclaw@2026.3.24 + 4 skills
│   ├── server.py                   # HTTP server: workspace assembly + invocation + usage tracking
│   ├── entrypoint.sh               # Container startup: SSM registration, IM auto-connect
│   ├── workspace_assembler.py      # 3-layer SOUL merge + KB injection + identity
│   ├── permissions.py              # SSM permission profiles (base_id extraction)
│   ├── skill_loader.py             # DynamoDB role lookup → skill filtering
│   ├── identity.py                 # Employee identity injection
│   ├── memory.py                   # Per-turn memory checkpoint
│   ├── observability.py            # CloudWatch metrics
│   ├── safety.py                   # Guardrail enforcement
│   ├── openclaw.json               # Agent config (deny built-in cron/gateway)
│   └── skills/                     # Enterprise skills (eventbridge-cron, etc.)
├── exec-agent/                     # Executive tier Docker image
│   ├── Dockerfile                  # Pinned openclaw@2026.3.24 + 20 skills, Sonnet 4.6
│   └── openclaw.json               # Executive config (full tool permissions)
├── auth-agent/                     # Permission/approval agent
│   ├── server.py                   # Approval workflow HTTP server
│   └── permission_request.py       # Permission request handler
├── gateway/
│   ├── bedrock_proxy_h2.js         # H2 Proxy (channel detection, pairing intercept)
│   └── tenant_router.py            # 3-tier routing + always-on container support
└── docs/
    ├── environments.md             # Environment registry (prod, test, legacy)
    └── worklog-*.md                # Development session logs
```

## Operational Notes

### Always-on Agent Management (ECS Fargate)

Always-on agents run as **ECS Fargate Services** with EFS-backed persistent workspace and auto-restart on crash. Each task self-registers its private VPC IP in SSM on startup; the Tenant Router reads that SSM entry to route requests. Admin selects deployment mode (Serverless or Always-on) when creating an agent in Agent Factory.

Start/stop from **Agent Factory → agent detail → deployment mode toggle**, or manually:

```bash
# Read ECS config from CloudFormation outputs (one-time setup)
ECS_CLUSTER=$(aws cloudformation describe-stacks --stack-name $STACK_NAME --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`AlwaysOnEcsClusterName`].OutputValue' --output text)
ECS_TASK_DEF=$(aws cloudformation describe-stacks --stack-name $STACK_NAME --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`AlwaysOnTaskDefinitionArn`].OutputValue' --output text)
ECS_SUBNET=$(aws cloudformation describe-stacks --stack-name $STACK_NAME --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`AlwaysOnSubnetId`].OutputValue' --output text)
ECS_SG=$(aws cloudformation describe-stacks --stack-name $STACK_NAME --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`AlwaysOnTaskSecurityGroupId`].OutputValue' --output text)

# Write to /etc/openclaw/env so the Admin Console can use them
aws ssm send-command --instance-ids $INSTANCE_ID --region $REGION \
  --document-name AWS-RunShellScript \
  --parameters "{\"commands\":[
    \"echo 'ECS_CLUSTER_NAME=${ECS_CLUSTER}' >> /etc/openclaw/env\",
    \"echo 'ECS_TASK_DEFINITION=${ECS_TASK_DEF}' >> /etc/openclaw/env\",
    \"echo 'ECS_SUBNET_ID=${ECS_SUBNET}' >> /etc/openclaw/env\",
    \"echo 'ECS_TASK_SG_ID=${ECS_SG}' >> /etc/openclaw/env\",
    \"systemctl restart openclaw-admin\"
  ]}"

# Manual ECS RunTask (if UI unavailable)
aws ecs run-task \
  --cluster $ECS_CLUSTER \
  --task-definition $ECS_TASK_DEF \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$ECS_SUBNET],securityGroups=[$ECS_SG],assignPublicIp=ENABLED}" \
  --overrides "{\"containerOverrides\":[{\"name\":\"always-on-agent\",\"environment\":[
    {\"name\":\"SHARED_AGENT_ID\",\"value\":\"agent-helpdesk\"},
    {\"name\":\"SESSION_ID\",\"value\":\"shared__agent-helpdesk\"},
    {\"name\":\"S3_BUCKET\",\"value\":\"$S3_BUCKET\"},
    {\"name\":\"STACK_NAME\",\"value\":\"$STACK_NAME\"},
    {\"name\":\"AWS_REGION\",\"value\":\"$REGION\"}
  ]}]}" \
  --region $REGION
```

The task's private IP is automatically registered in SSM as `/openclaw/{stack}/always-on/{agent_id}/endpoint` by `entrypoint.sh` once healthy (~30s). The Tenant Router picks it up within 60s (SSM cache TTL).

### Digital Twin Public URL

Set `PUBLIC_URL` in `/etc/openclaw/env` — the admin console systemd service reads this file automatically:
```bash
echo "PUBLIC_URL=https://your-domain.com" >> /etc/openclaw/env
sudo systemctl restart openclaw-admin
```

### Updating Agent Docker Image

After every build, update the AgentCore Runtime to resolve the new `:latest` digest:

```bash
aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id "$RUNTIME_ID" \
  --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${ECR_URI}\"}}" \
  --role-arn "$EXECUTION_ROLE_ARN" \
  --network-configuration '{"networkMode":"PUBLIC"}' \
  --environment-variables "{\"BEDROCK_MODEL_ID\":\"global.amazon.nova-2-lite-v1:0\", ...}" \
  --region $REGION
```

**Always pass `--environment-variables`** — AgentCore clears env vars if the field is omitted.

**Session Storage warning:** `update-agent-runtime` wipes all Session Storage for that runtime. All employees' sessions will bootstrap from S3 on their next invocation (~6s cold start instead of ~2-3s session resume). This is expected and handled automatically — S3 is always the source of truth for admin-managed files.

### Reminders and Scheduled Tasks

OpenClaw's reminder system writes a `HEARTBEAT.md` to the agent's workspace and sends the notification through the active channel at the scheduled time.

| Deployment Mode | Reminder Behavior |
|----------------|-----------------|
| **Always-on (ECS Fargate)** | Fully supported — container is persistent, heartbeat fires on schedule. Delivery channel is read from `CHANNELS.md` in the workspace (auto-injected at session start from IM pairings). **This is the primary use case for always-on mode** — customer service polling, email checks every 3 minutes, daily report generation. |
| **Serverless (AgentCore)** | Heartbeat is set, `HEARTBEAT.md` persisted in Session Storage and synced to S3. Fires on the **next session start** when the microVM resumes. If no new message arrives before the scheduled time, the reminder is deferred to the next interaction. |

**For reliable scheduled tasks:** toggle the agent to always-on mode from Agent Factory. This is the recommended approach for any agent that needs to run background tasks (email monitoring, ticket scanning, periodic reports).

`CHANNELS.md` is automatically written to each employee's workspace during session assembly (reverse-lookup of their SSM IM pairings). No manual configuration needed once the user has paired an IM channel.

### H2 Proxy and Tenant Router — systemd Services

```bash
sudo cp gateway/bedrock-proxy-h2.service /etc/systemd/system/
sudo cp gateway/tenant-router.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bedrock-proxy-h2 tenant-router
sudo systemctl start bedrock-proxy-h2 tenant-router
```

## Troubleshooting

### CloudFormation stack deletion fails on PrivateSubnet

**Symptom:** `aws cloudformation delete-stack` gets stuck, then reports `DELETE_FAILED` with:
```
The subnet 'subnet-xxx' has dependencies and cannot be deleted.
```

**Cause:** AWS GuardDuty automatically creates managed VPC endpoints in every subnet it monitors. These endpoints block subnet deletion.

**Fix:** Find and delete the GuardDuty-managed endpoints before retrying:

```bash
# Find GuardDuty endpoints in the stack's VPC
VPC_ID=$(aws ec2 describe-vpcs \
  --filters "Name=tag:aws:cloudformation:stack-name,Values=${STACK_NAME}" \
  --region $REGION --query 'Vpcs[0].VpcId' --output text)

ENDPOINTS=$(aws ec2 describe-vpc-endpoints \
  --filters "Name=vpc-id,Values=$VPC_ID" \
  --region $REGION \
  --query 'VpcEndpoints[?State!=`deleted`].VpcEndpointId' --output text)

aws ec2 delete-vpc-endpoints --vpc-endpoint-ids $ENDPOINTS --region $REGION

# Retry stack deletion
aws cloudformation delete-stack --stack-name $STACK_NAME --region $REGION
```

> **Note:** This does not disable GuardDuty — it only removes the endpoint ENIs that were blocking deletion. GuardDuty will recreate them in any new subnets automatically.

> **Prevention:** Deploying with `CreateVPCEndpoints=false` (default) avoids creating a PrivateSubnet, which is the only subnet GuardDuty consistently attaches to in this template. The CloudFormation template has been updated to skip PrivateSubnet creation when VPC endpoints are disabled.

### `deploy.sh` fails: ECR repo is empty after `--skip-build`

**Symptom:** AgentCore runtime creation fails with "specified image identifier does not exist."

**Cause:** `--skip-build` skips the Docker build, but if this is the first deploy of a new stack, the ECR repo will be empty.

**Fix:** Run without `--skip-build` on first deploy. The script builds on the gateway EC2 via SSM — no local Docker needed.

### AgentCore returns HTTP 500 on every message

**Cause:** Almost always a wrong `openclaw` npm package version inside the container.

**Check:**
```bash
aws logs tail /aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT --follow
# Look for: "openclaw returned empty output"
```

**Fix:** Rebuild the Docker image. Both `agent-container/Dockerfile` and `exec-agent/Dockerfile` must install `openclaw@2026.3.24` exactly — do not upgrade.

---

Built by [wjiad@aws](mailto:wjiad@amazon.com) · [aws-samples](https://github.com/aws-samples) · Contributions welcome
