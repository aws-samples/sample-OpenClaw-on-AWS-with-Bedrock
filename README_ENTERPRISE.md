# OpenClaw Enterprise on AgentCore

Turn [OpenClaw](https://github.com/openclaw/openclaw) from a personal AI assistant into an enterprise-grade digital workforce platform — without modifying a single line of OpenClaw source code.

---

## Serverless Economics: Pay Only When Agents Think

Most enterprise AI deployments either charge per seat or run dedicated compute per employee. AgentCore Firecracker microVMs change the economics entirely — **you don't pre-allocate CPU or memory. You don't pick instance sizes. AgentCore provisions exactly what each invocation needs and bills per second.**

**AgentCore pricing (us-west-2):**
- CPU: $0.0895 / vCPU-hour — **$0 when idle** (no CPU charge between invocations)
- Memory: $0.00945 / GB-hour — the only idle cost, and it's tiny

**50 employees, 8-hour workday sessions (us-west-2):**

| | Dedicated EC2 per Employee | ChatGPT Team | **OpenClaw on AgentCore** |
|---|---|---|---|
| 50 employees | 50 × $52 = **$2,600/mo** | 50 × $25 = **$1,250/mo** | **~$100-150/mo** |
| What you pay for | 24/7, whether anyone's chatting or not | Per seat, fixed | Only invocation CPU + idle session memory |
| Idle cost per employee | $52/mo (full EC2 running) | $25/mo (subscription) | **~$0.08/day** (1 GB memory × 8 hr) |

**The math:** 50 employees × 22 workdays × $0.08 idle/day = ~$88/mo in memory. Add CPU during actual conversations (~$20-50/mo) = **$100-150/mo total AgentCore cost.** Add gateway infrastructure (see [Cost Estimate](#cost-estimate) below) for the complete picture.

---

## Two Deployment Modes: Serverless + Always-on

Every agent uses the same Docker image. Admin chooses the deployment mode per agent based on the use case — no code changes, no separate builds.

### Serverless (AgentCore) — Default

| | Behavior |
|-|---------|
| **Cold start** | ~6s first message — Firecracker microVM + SOUL assembly + Bedrock |
| **Session resume** | ~2-3s — Session Storage restores workspace, skips S3 download |
| **Warm session** | Near-instant — microVM stays active during a conversation |
| **Idle cost** | Memory only ($0.00945/GB-hour). CPU = $0 when idle |
| **Session Storage** | Workspace files persist across microVM stop/resume (1 GB per session). No S3 sync needed for agent-side persistence |
| **Best for** | Individual employee agents — scales to zero, pay-per-use |

### Always-on (ECS Fargate) — Admin Toggle

| | Behavior |
|-|---------|
| **Cold start** | None — container is always running |
| **Scheduled tasks** | HEARTBEAT fires on schedule (email check every 3 min, daily reports) |
| **Direct IM** | Container connects directly to Telegram/Discord (dedicated bot token) |
| **Persistence** | EFS-backed workspace — durable across container restarts |
| **Best for** | Customer service bots, executive assistants with frequent cron tasks, high-traffic Digital Twins |

Admin selects deployment mode when creating an agent in **Agent Factory**. Any agent can be always-on — whether it serves one employee or multiple. An always-on agent gets a dedicated ECS Fargate container with auto-restart.

---

## Security: Hardware-Level Isolation at Every Layer

Every agent invocation runs in an isolated Firecracker microVM — the same hypervisor technology powering AWS Lambda. No amount of prompt engineering can break L3 or L4.

| Layer | Mechanism | Bypassed by prompt injection? |
|-------|-----------|-------------------------------|
| L1 — Prompt | SOUL.md rules ("Finance never uses shell") | ⚠️ Theoretically possible |
| L2 — Application | Skills manifest `allowedRoles`/`blockedRoles` | ⚠️ Code bug risk |
| **L3 — IAM** | **Runtime role has no permission on target resource** | **Impossible** |
| **L4 — Compute** | **Firecracker microVM per invocation, isolated at hypervisor level** | **Impossible** |
| **L5 — Guardrail** | **Bedrock Guardrail checks every input + output: topic denial, PII filtering, compliance policies** | **Impossible — AWS-managed, semantic AI layer** |

Each runtime tier has its own Docker image, its own IAM role, its own Firecracker boundary, and an optional Bedrock Guardrail. An intern's agent IAM role literally cannot read the exec S3 bucket — even if the LLM tries. And even if it could, the Guardrail blocks the output before it reaches the user.

Additional controls: no public ports (SSM only) · IAM roles throughout, no hardcoded credentials · gateway token in SSM SecureString, never on disk · VPC isolation between runtimes.

---

## Auditable and Governed from Day One

| Control | What IT Gets |
|---------|-------------|
| **SOUL Editor** | Global rules locked by IT. Finance cannot touch shell. Engineering cannot leak PII. Employees cannot override the global layer. |
| **Skill Governance** | 26 skills with `allowedRoles`/`blockedRoles`. Employees cannot install unapproved skills. |
| **Audit Center** | Every invocation, tool call, permission denial, SOUL change, and IM pairing → DynamoDB |
| **Usage & Cost** | Per-employee, per-department breakdown. Daily/weekly/monthly trends with model pricing |
| **IM Management** | Every employee's connected IM accounts visible to admin. One-click revoke. |
| **Security Center** | Live ECR images, IAM roles, VPC security groups with AWS Console deep links |
| **RBAC** | Admin (full org) · Manager (department-scoped) · Employee (portal only) |

---

## What Makes This Different

> Most enterprise AI platforms give everyone the same generic assistant.
> This one gives each employee **a personal AI agent with their own identity, memory, tools, and boundaries** — while giving IT the governance controls above.

### Flagship Features

| Feature | What It Does |
|---------|-------------|
| **Digital Twin** | Employee turns on a public link. Anyone with the URL can chat with their AI agent while they're away — agent responds using their SOUL, memory, and expertise. Twin sessions are isolated from the employee's main session |
| **Always-on Agents** | Admin toggles any agent to persistent ECS Fargate mode. Enables scheduled tasks (email every 3 min), direct IM bot connections, instant response. Same image, same SOUL — just a deployment mode switch |
| **Session Storage** | AgentCore persists workspace files across microVM stop/resume cycles. No S3 re-download on session resume. Combined with `StopRuntimeSession` API for admin-triggered config refresh |
| **Three-Layer SOUL** | Global (IT) → Position (dept admin) → Personal (employee). 3 stakeholders, 3 layers, one merged identity. Same LLM — Finance Analyst vs SDE have completely different personalities and permissions |
| **Self-Service IM Pairing** | Employee scans QR code from Portal → connects Telegram / Feishu / Discord in 30 seconds. No IT ticket, no admin approval |
| **Multi-Runtime Architecture** | Standard tier (Nova 2 Lite, scoped IAM) vs Executive tier (Claude Sonnet 4.6, full access). Different Docker images, different models, different IAM roles — infrastructure-level isolation |
| **Bedrock Guardrails (L5)** | Assign any Bedrock Guardrail to a Runtime from Security Center UI. Topic denial, PII filtering, and compliance policies wrap every user input and agent output — no OpenClaw source code changes needed. Standard employees get blocked; exec tier is unrestricted. Full block audit trail in Audit Center. |
| **Org Directory KB** | Company directory (every employee, R&R, contact, agent capabilities) seeded from org data and injected into every agent — agents know who to contact and can draft messages for you |
| **Position → Runtime Routing** | 3-tier routing chain: employee override → position rule → default. Assign positions to runtimes from Security Center UI, propagates to all members automatically |
| **Per-Employee Model Config** | Override model, context window, compaction settings, and response language at position OR employee level from Agent Factory → Configuration tab |
| **IM Channel Management** | Admin sees every employee's IM connections grouped by channel — when they paired, session count, last active, one-click disconnect |
| **Org CRUD** | Full create/edit/delete for Departments, Positions, and Employees from Admin Console. Delete is guarded: blocks if employees or agent assignments exist, prompts force-cascade delete |
| **Security Center** | Live AWS resource browser — ECR images, IAM roles, VPC security groups with console links. Configure runtime images and IAM roles from the UI |
| **Session Storage + Memory** | Serverless: Session Storage persists workspace across microVM cycles + S3 writeback for admin visibility. Always-on: EFS workspace + Gateway compaction. Same memory across Discord, Telegram, Feishu, and Portal |
| **Dynamic Config, Zero Redeploy** | Change model, tool permissions, SOUL content, or KB assignments → propagates via config version poll (5 min) or instant via `StopRuntimeSession`. No container rebuild, no runtime update |

---

## Live Demo

> **https://openclaw.awspsa.com**
>
> A real running instance with 15 departments, 12 positions, 27 employees, 29 AI agents, 5 IM channels (Telegram, Feishu, Discord + Portal), multi-runtime architecture, and always-on ECS Fargate agents — all backed by DynamoDB + S3 on AWS.
>
> **Everything here is real.** Every button works. Every chart reads from real data. Every agent runs on Bedrock AgentCore in isolated Firecracker microVMs.
>
> **Try the Digital Twin:** Login as any employee → Portal → My Profile → Toggle **Digital Twin** ON → get a public URL → open it in an incognito window and chat with the AI version of that employee.
>
> Need a demo account? Contact [wjiad@aws](mailto:wjiad@amazon.com) to get access.

### Screenshots

| Admin Dashboard | Employee Portal + Digital Twin |
|:-:|:-:|
| ![Admin Dashboard](enterprise/demo/images/04-admin-dashboard.jpeg) | ![Portal Chat](enterprise/demo/images/01-portal-chat-permission-denied.jpeg) |

| Agent Factory — Configuration | IM Channels — Per-Channel Management |
|:-:|:-:|
| ![Agent Factory](enterprise/demo/images/03-agent-factory-list.jpeg) | ![SOUL Editor](enterprise/demo/images/05-workspace-manager-soul.jpeg) |

| Usage & Cost — Model Pricing | Security Center — Runtime Management |
|:-:|:-:|
| ![Usage & Cost](enterprise/demo/images/02-usage-cost-dashboard.jpeg) | ![Skill Platform](enterprise/demo/images/08-skill-platform-catalog.jpeg) |

---

## The Problem

OpenClaw is one of the most capable open-source AI agent platforms (200k+ GitHub stars). It excels at personal productivity: connecting AI to WhatsApp, Telegram, Discord, running browser automation, managing calendars. But enterprise deployments need:

- **Multi-tenant isolation** — each employee gets their own agent with separate identity, memory, and permissions
- **Role-based access control** — interns can't run shell commands, finance can't access engineering data
- **Centralized governance** — IT controls agent behavior, skills, and model selection across the organization
- **Audit & compliance** — every agent action logged, PII detection, data sovereignty
- **Cost management** — per-department budgets, model routing, usage tracking

## The Solution

A management layer that wraps OpenClaw with enterprise controls, deployed on AWS Bedrock AgentCore. No fork, no patch, no vendor lock-in — just configuration files and AWS-native services.

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
Runtime: Standard (Engineering / Sales / HR)
  ├── Docker:  standard-agent:latest
  │   └── Skills: web-search, jina-reader, deep-research, github-pr, slack
  ├── Model:   Amazon Nova 2 Lite (cost-optimized)
  └── IAM:     Own S3 workspace only · Own DynamoDB partition

Runtime: Executive (C-Suite / Senior Leadership)
  ├── Docker:  exec-agent:latest (all skills pre-installed)
  ├── Model:   Claude Sonnet 4.6 (highest capability)
  └── IAM:     Full S3 access · Cross-department DynamoDB read · All Bedrock models
```

Each runtime tier has its own Docker image, IAM role, and optional Bedrock Guardrail — see [Security](#security-hardware-level-isolation-at-every-layer) above for the full 5-layer model.

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
│  ├── 25+ pages: Dashboard, Agent Factory, Security Center,       │
│  │   IM Channels, Monitor, Audit, Usage & Cost, Settings         │
│  ├── Employee Portal: Chat, Profile, Skills, Requests, Connect   │
│  │   IM, Digital Twin toggle                                      │
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
| **Always-on Agents** | Admin toggles any agent to ECS Fargate mode. Same Docker image, persistent container with EFS workspace. Enables scheduled tasks, direct IM bot, instant response. Tenant Router routes assigned employees to Fargate task VPC IP via SSM |
| **SOUL Injection** | 3-layer merge (Global + Position + Personal) at session start. Position SOUL warnings in editor when edits affect N agents |
| **Permission Control** | SOUL.md defines allowed/blocked tools per role. Plan A (pre-execution) + Plan E (post-audit). Exec profile bypasses Plan A entirely |
| **Multi-Runtime** | Standard (Nova 2 Lite, scoped IAM) and Executive (Sonnet 4.6, full IAM) runtimes. Assign positions to runtimes from Security Center UI |
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

**What you're deploying:** A multi-tenant enterprise AI platform — a gateway layer (Tenant Router + Admin Console) routes requests to isolated Firecracker microVMs via AWS Bedrock AgentCore. Two Docker images, two Runtimes (Standard + Executive), one DynamoDB table, one S3 bucket. Optional ECS Fargate always-on agents for scheduled tasks and direct IM.

**Run Docker builds on the gateway EC2, not the user's local machine.** After Step 1 creates the EC2, use SSM to build on it — the EC2 is ARM64 Graviton, has Docker pre-installed, and has fast internal network to ECR. Building ARM64 images locally via QEMU emulation is slow and error-prone. Use `enterprise/agent-container/build-on-ec2.sh` for the standard image; adapt the same pattern for exec-agent (Step 1.5).

**Critical — these will silently break deployment if missed:**

1. **The `openclaw` npm package is pinned to `2026.3.24`** in both `agent-container/Dockerfile` and `exec-agent/Dockerfile` — do not upgrade. Newer versions changed the Gateway response delivery mechanism and break IM channel integration.
2. **Build both Docker images** — `agent-container/` (standard) and `exec-agent/` (executive). Don't skip exec even if only standard agents are needed initially.
3. **Docker build takes 10–15 min** — `clawhub install` installs skills one by one. This is normal.
4. **After `update-agent-runtime`**, poll until `status: READY` before testing.
5. **DynamoDB is in `us-east-2` by default** (AgentCore is `us-east-1`). This is intentional — DynamoDB cross-region access is free and `us-east-2` avoids hitting AgentCore's region during heavy load.

**Verify it works** (after deployment):
- Playground → Carol Zhang (Finance) → "run git status" → refused ✓
- Playground → Ryan Park (SDE) → "run git status" → executes ✓
- Playground → Rachel Li (Legal) → "what new tokens are going live?" → Guardrail blocks ✓
- Playground → WJD (Executive) → same question → answers freely ✓

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
- Bedrock model access: Nova 2 Lite (default) + Anthropic Claude (exec tier + Admin Assistant)
- Bedrock AgentCore available in: `us-east-1`, `us-west-2`
- IAM permissions: `cloudformation:*`, `ec2:*`, `iam:*`, `ecr:*`, `s3:*`, `ssm:*`, `bedrock:*`, `dynamodb:*`

### Step 1: Configure and Deploy

```bash
cd enterprise           # from repo root
cp .env.example .env    # copy config template
```

Open `.env` and fill in the required values:

```bash
STACK_NAME=openclaw-enterprise   # your stack name
REGION=us-east-1                 # us-east-1 or us-west-2 (AgentCore regions)
ADMIN_PASSWORD=your-password     # admin console login password

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

**What `deploy.sh` does automatically:**
1. Deploys CloudFormation (EC2, ECR, S3, IAM — creates or updates)
2. Packages source code → uploads to S3 → **triggers Docker build on the gateway EC2 via SSM** (ARM64 Graviton, no local Docker needed)
3. Creates or updates AgentCore Runtime
4. Creates DynamoDB table if it doesn't exist
5. Seeds org data (employees, positions, departments, SOUL templates, knowledge docs)
6. Stores `ADMIN_PASSWORD` and `JWT_SECRET` in SSM SecureString
7. Configures the EC2 gateway via SSM

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

> The standard agent image (`openclaw-multitenancy-multitenancy-agent`) is built automatically by `deploy-multitenancy.sh`. You only need this step for the executive tier.

### Step 2: DynamoDB Table

> **`deploy.sh` handles this automatically.** The table is created if it doesn't exist, then seeded with org data in one step.

To create or re-seed manually:

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

### Step 3: Seed Sample Organization

> **`deploy.sh` handles this automatically.** To re-seed manually (e.g. after org changes):

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

### Step 4: Deploy Admin Console

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
    \"printf '[Unit]\\\\nDescription=OpenClaw Admin Console\\\\nAfter=network.target\\\\n[Service]\\\\nType=simple\\\\nUser=ubuntu\\\\nExecStart=/opt/admin-console/start.sh\\\\nRestart=always\\\\nRestartSec=5\\\\n[Install]\\\\nWantedBy=multi-user.target' > /etc/systemd/system/openclaw-admin.service\",
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

### Step 4.5: Allow ECS Tasks to Reach SSM VPC Endpoint

If your stack has SSM VPC endpoints (created when `CreateVPCEndpoints=true`), the always-on ECS Fargate tasks need permission to reach them. This is a one-time manual step because the SSM endpoint security group is not managed by this stack's CloudFormation template.

```bash
# Get the SSM endpoint security group
SSM_ENDPOINT_SG=$(aws ec2 describe-vpc-endpoints --region $REGION \
  --filters "Name=service-name,Values=com.amazonaws.${REGION}.ssm" \
  --query 'VpcEndpoints[0].Groups[0].GroupId' --output text)

ECS_TASK_SG=$(aws cloudformation describe-stacks --stack-name $STACK_NAME --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`AlwaysOnTaskSecurityGroupId`].OutputValue' --output text)

# Allow HTTPS from ECS tasks to SSM endpoint
aws ec2 authorize-security-group-ingress \
  --group-id $SSM_ENDPOINT_SG \
  --protocol tcp --port 443 \
  --source-group $ECS_TASK_SG \
  --region $REGION
```

> Skip this step if you deployed with `CreateVPCEndpoints=false` (ECS tasks reach SSM over the internet directly).

### Step 5: Deploy and Start Gateway Services

```bash
# Upload gateway files to S3 (run from repo root)
aws s3 cp enterprise/gateway/tenant_router.py       "s3://${S3_BUCKET}/_deploy/tenant_router.py"
aws s3 cp enterprise/gateway/bedrock_proxy_h2.js    "s3://${S3_BUCKET}/_deploy/bedrock_proxy_h2.js"
aws s3 cp enterprise/gateway/bedrock-proxy-h2.service "s3://${S3_BUCKET}/_deploy/bedrock-proxy-h2.service"
aws s3 cp enterprise/gateway/tenant-router.service  "s3://${S3_BUCKET}/_deploy/tenant-router.service"

# Install gateway files on EC2 and start services
aws ssm send-command --instance-ids $INSTANCE_ID --region $REGION \
  --document-name AWS-RunShellScript \
  --parameters "{\"commands\":[
    \"mkdir -p /etc/openclaw && printf 'STACK_NAME=${STACK_NAME}\\nAWS_REGION=${REGION}\\nGATEWAY_INSTANCE_ID=${INSTANCE_ID}\\nECS_CLUSTER_NAME=${STACK_NAME}-always-on\\nECS_SUBNET_ID=$(aws cloudformation describe-stacks --stack-name ${STACK_NAME} --region ${REGION} --query Stacks[0].Outputs[?OutputKey==\\'AlwaysOnSubnetId\\'].OutputValue --output text)\\nECS_TASK_SG_ID=$(aws cloudformation describe-stacks --stack-name ${STACK_NAME} --region ${REGION} --query Stacks[0].Outputs[?OutputKey==\\'AlwaysOnTaskSecurityGroupId\\'].OutputValue --output text)\\n' > /etc/openclaw/env\",
    \"pip3 install boto3 requests\",
    \"aws s3 cp s3://${S3_BUCKET}/_deploy/tenant_router.py /home/ubuntu/tenant_router.py --region $REGION\",
    \"aws s3 cp s3://${S3_BUCKET}/_deploy/bedrock_proxy_h2.js /home/ubuntu/bedrock_proxy_h2.js --region $REGION\",
    \"aws s3 cp s3://${S3_BUCKET}/_deploy/bedrock-proxy-h2.service /etc/systemd/system/bedrock-proxy-h2.service --region $REGION\",
    \"aws s3 cp s3://${S3_BUCKET}/_deploy/tenant-router.service /etc/systemd/system/tenant-router.service --region $REGION\",
    \"chown ubuntu:ubuntu /home/ubuntu/tenant_router.py /home/ubuntu/bedrock_proxy_h2.js\",
    \"systemctl daemon-reload && systemctl enable bedrock-proxy-h2 tenant-router && systemctl start bedrock-proxy-h2 tenant-router\"
  ]}"
```

### Step 6: Access Admin Console

```bash
aws ssm start-session --target $INSTANCE_ID --region $REGION \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8099"],"localPortNumber":["8199"]}'
```

Open **http://localhost:8199** → login with Employee ID `emp-jiade` (admin) and `ADMIN_PASSWORD` from your `.env`.

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
Login as **Carol Zhang** (Finance) → Chat → "Who are you?" → **"ACME Corp Finance Analyst"**
Login as **Ryan Park** (SDE) → Chat → "Who are you?" → **"ACME Corp Software Engineer"**
Same LLM. Completely different identities.

### 2. Digital Twin
Login as any employee → **Portal → My Profile → Digital Twin toggle**
Turn ON → copy the URL → open in incognito → chat with the AI version of that employee
Turn OFF → incognito tab gets 404 immediately

### 3. Org Directory (Knowledge Base)
Ask any agent: *"Who should I contact for a code review?"* or *"What does Marcus Bell do?"*
→ Agent reads `kb-org-directory` (seeded into every position) and answers with the right person's name, role, IM channel, and agent capabilities
→ Works out-of-box after running `seed_knowledge_docs.py` — no manual KB assignment needed

### 4. Permission Boundaries
Carol Zhang: "Run git status" → **Refused** (Finance, no shell)
Ryan Park: "Run git status" → **Executed** (SDE, has shell)
WJD / Ada: Any command → **Executed** (Executive tier, zero restrictions, Sonnet 4.6)

### 5. Multi-Runtime
Login as **Ada** or **WJD** → these route to the Executive AgentCore Runtime:
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

## Demo Accounts

> **Executive accounts (Ada, WJD)** run on the Executive AgentCore Runtime with Claude Sonnet 4.6, zero tool restrictions, and a full-access IAM role.

| Employee ID | Name | Role | Runtime | What They Experience |
|-------------|------|------|---------|---------------------|
| **emp-ada** | **Ada** | **Executive** | **exec-agent · Sonnet 4.6** | **All tools · Full IAM · Feishu + Telegram 🔓** |
| **emp-wjd** | **WJD** | **Executive** | **exec-agent · Sonnet 4.6** | **All tools · Full IAM · Feishu + Telegram 🔓** |
| emp-jiade | JiaDe Wang | Admin | standard | Discord → SA Agent ✨ |
| emp-chris | Chris Morgan | Admin | standard | DevOps Agent (shell + infra tools) |
| emp-peter | Peter Wu | Manager | standard | Portal/Discord → Executive Agent ✨ |
| emp-alex | Alex Rivera | Manager | standard | Product dept manager view |
| emp-mike | Mike Johnson | Manager | standard | Sales dept manager · CRM tools |
| emp-ryan | Ryan Park | Employee | standard | Slack/Discord → SDE Agent (shell/code) |
| emp-carol | Carol Zhang | Employee | standard | Telegram → Finance Agent |
| emp-david | David Park | Employee | standard | Slack → Finance Agent ✨ |
| **emp-admin** | **Demo Admin** | **Employee** | **exec-agent** | **Unrestricted test account · All tools · install_skill** |

> 🔓 = No tool restrictions · ✨ = Cross-session memory via S3

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ADMIN_PASSWORD` | Yes | Login password. Production: store in SSM SecureString |
| `JWT_SECRET` | Yes | JWT signing key. Generate: `openssl rand -hex 32` |
| `AWS_REGION` | Yes | Deployment region for EC2, SSM, ECR, AgentCore (default: `us-east-1`) |
| `GATEWAY_INSTANCE_ID` | Yes | EC2 instance ID — required for always-on container start/stop via SSM. Set in `/etc/openclaw/env`. Falls back to IMDSv2 if not set. |
| `PUBLIC_URL` | No | Base URL for Digital Twin links (default: `https://openclaw.awspsa.com`) — **set this** for correct twin URLs |
| `AGENT_ECR_IMAGE` | No | ECR image URI for always-on containers. Auto-built from `$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$STACK_NAME-multitenancy-agent:latest` if not set. |
| `CONSOLE_PORT` | No | Admin Console port (default: `8099`) |
| `TENANT_ROUTER_URL` | No | Tenant Router URL (default: `http://localhost:8090`) |
| `DYNAMODB_TABLE` | No | Table name — **must equal STACK_NAME** (IAM policy scoped to `table/${StackName}`). Default: same as STACK_NAME |
| `DYNAMODB_REGION` | No | DynamoDB region if different from `AWS_REGION` (default: `us-east-2`) |

## Sample Organization

| | Count | Details |
|-|-------|---------|
| Departments | 15 | 7 top-level + 8 sub-departments including Admin Lab |
| Positions | 12 | SA, SDE, DevOps, QA, AE, PM, FA, HR, CSM, Legal, Executive, Platform Admin |
| Employees | 27 | Each with workspace files in S3 |
| Agents | 29 | 28 serverless + 1 always-on |
| IM Channels | 5 | Telegram, Feishu, Discord, Portal, + always-on |
| Skills | 26 | Role-scoped skill packages |
| Knowledge Docs | 14 | 11 topic KBs + company-directory.md (org directory, auto-assigned to all positions) |
| SOUL Templates | 12 | 1 global + 11 position-specific |
| RBAC Roles | 3 | Admin, Manager, Employee |

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

## How It Compares

| Capability | ChatGPT Team | Microsoft Copilot | OpenClaw Enterprise |
|-----------|-------------|-------------------|-------------------|
| Per-employee identity | ❌ Same for all | ❌ Same for all | ✅ 3-layer SOUL per role |
| Tool permissions per role | ❌ | ❌ | ✅ Plan A + Plan E + L3 IAM |
| Scheduled tasks / cron | ❌ | ❌ | ✅ Always-on agents (ECS Fargate) |
| Direct IM bot connection | ❌ | ❌ | ✅ Per-agent Telegram/Discord bot |
| Digital Twin (public agent URL) | ❌ | ❌ | ✅ Shareable, revocable, isolated session |
| Session persistence | ❌ Session only | ❌ | ✅ Session Storage + S3 cross-session |
| Self-service IM pairing | ❌ | ❌ | ✅ QR code, 30 seconds |
| Self-hosted, data in your VPC | ❌ | ❌ | ✅ Bedrock in your account |
| Open source | ❌ | ❌ | ✅ OpenClaw + AWS native |

## Project Structure

```
enterprise/
├── README.md
├── deploy.sh                       # One-click deployment
├── clawdbot-bedrock-agentcore-multitenancy.yaml  # CloudFormation
├── admin-console/
│   ├── src/pages/
│   │   ├── Dashboard.tsx           # Setup checklist + real-time stats
│   │   ├── AgentFactory/           # Agent list + Configuration tab
│   │   ├── SecurityCenter.tsx      # Runtime config + ECR/IAM/VPC browser
│   │   ├── IMChannels.tsx          # Per-channel employee management
│   │   ├── Knowledge/index.tsx     # KB management + Assignments tab
│   │   ├── Usage.tsx               # Billing + model pricing
│   │   ├── TwinChat.tsx            # Public Digital Twin page (no auth)
│   │   └── portal/
│   │       ├── Chat.tsx            # Employee chat + warmup indicator
│   │       └── Profile.tsx         # USER.md + memory view + Digital Twin toggle
│   └── server/
│       ├── main.py                 # App bootstrap — routes in routers/
│       ├── shared.py               # Auth helpers, config, SSM/DDB helpers
│       ├── routers/                # 16 domain routers (127 API endpoints)
│       │   ├── org.py agents.py bindings.py knowledge.py
│       │   ├── portal.py playground.py monitor.py audit.py
│       │   ├── usage.py settings.py security.py
│       │   ├── admin_im.py admin_ai.py admin_always_on.py
│       │   ├── gateway_proxy.py twin.py
│       │   └── __init__.py
│       ├── db.py                   # DynamoDB single-table + Digital Twin CRUD
│       └── seed_*.py               # Sample data scripts
├── agent-container/                # AgentCore Docker image
│   ├── server.py                   # Workspace assembly + twin/always-on detection
│   ├── workspace_assembler.py      # 3-layer SOUL merge + KB injection
│   └── permissions.py              # SSM permission profiles (base_id extraction)
├── exec-agent/                     # Executive tier Docker image
│   └── Dockerfile                  # All skills pre-installed, Sonnet 4.6
└── gateway/
    ├── bedrock_proxy_h2.js         # H2 Proxy (channel detection, pairing intercept)
    └── tenant_router.py            # 3-tier routing + always-on container support
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
