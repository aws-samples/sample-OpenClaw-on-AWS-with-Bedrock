# OpenClaw Enterprise Multi-Tenant — Sequence Diagrams

## Overview: How It Works (Simplified)

```mermaid
flowchart LR
    subgraph Users["Employees"]
        U1["📱 WhatsApp"]
        U2["📱 Telegram"]
        U3["📱 Slack"]
    end

    subgraph GW["EC2 Gateway (Always-On)"]
        OC_GW["🦞 OpenClaw Gateway\nChannels · Web UI · Cron"]
        TR["Tenant Router\nAuth · Route · Queue"]
        OC_GW --> TR
    end

    subgraph AC["AgentCore Runtime (Serverless)"]
        direction TB
        VM1["🔒 microVM\n(Sarah · Intern)"]
        VM2["🔒 microVM\n(Alex · Engineer)"]
        VM3["🔒 microVM\n(Carol · Finance)"]
    end

    subgraph Storage["AWS Services"]
        S3[("S3\nWorkspace\nper tenant")]
        SSM[("SSM\nPermissions\nSOUL templates")]
        BDR["🧠 Bedrock\nNova · Claude\nDeepSeek"]
        CW[("CloudWatch\nAudit Logs")]
    end

    U1 & U2 & U3 -->|"① Message"| OC_GW
    TR -->|"② Auth + Route"| AC
    AC -->|"③ Spin up microVM"| VM1 & VM2 & VM3
    VM1 & VM2 & VM3 <-->|"④ Pull/Push\nSOUL · MEMORY · Skills"| S3
    TR -->|"Read profiles"| SSM
    VM1 & VM2 & VM3 -->|"⑤ InvokeModel"| BDR
    BDR -->|"⑥ Response"| VM1 & VM2 & VM3
    VM1 & VM2 & VM3 -->|"Audit"| CW
    AC -->|"⑦ Return"| TR
    TR -->|"⑧ Forward"| OC_GW
    OC_GW -->|"⑨ Reply"| U1 & U2 & U3

    style GW fill:#E0F2F1,stroke:#4ECDC4,stroke-width:2px
    style AC fill:#E3F2FD,stroke:#45B7D1,stroke-width:2px,stroke-dasharray: 5 5
    style Storage fill:#FFF8E1,stroke:#F9A825,stroke-width:2px
    style Users fill:#FFEBEE,stroke:#FF6B6B,stroke-width:2px
```

**9 steps, one sentence each:**

| Step | What happens |
|------|-------------|
| ① | Employee sends message via WhatsApp/Telegram/Slack |
| ② | Gateway authenticates, derives `tenant_id`, checks permissions in SSM |
| ③ | AgentCore spins up an isolated Firecracker microVM for this tenant |
| ④ | entrypoint.sh pulls tenant's workspace from S3 (SOUL.md, MEMORY.md, Skills) |
| ⑤ | OpenClaw runs natively inside microVM, calls Bedrock for inference |
| ⑥ | Bedrock returns response, Plan E audits for policy violations |
| ⑦ | microVM returns response to Gateway, syncs workspace back to S3 |
| ⑧ | Gateway forwards response through the original channel |
| ⑨ | Employee receives reply. microVM released after idle timeout. |

> **Key insight:** OpenClaw runs 100% unmodified. All orchestration (auth, routing, S3 sync, audit) happens outside.

---

## Flow 1: Employee Sends a Message (Detailed)

```mermaid
sequenceDiagram
    autonumber
    actor Employee as Employee (WhatsApp)
    participant GW as EC2 Gateway<br/>OpenClaw Gateway
    participant TR as Tenant Router<br/>(Python, port 8090)
    participant SSM as SSM Parameter Store
    participant AC as AgentCore Runtime
    participant VM as Firecracker microVM
    participant S3 as S3<br/>openclaw-tenants/
    participant OC as OpenClaw (native)
    participant BR as Amazon Bedrock

    Employee->>GW: "Help me check Tokyo weather"
    GW->>TR: Forward message + channel=whatsapp, user_id=8613800138000

    Note over TR: Step 2: Derive tenant_id<br/>wa__8613800138000

    TR->>SSM: Read permission profile for wa__8613800138000
    SSM-->>TR: {tools: ["web_search"], blocked: ["shell","code_execution",...]}

    TR->>AC: invoke(runtimeId, sessionId="wa__8613800138000", message)

    Note over AC: Step 5: Spin up Firecracker microVM<br/>Isolated kernel, filesystem, network

    AC->>VM: Start Agent Container

    Note over VM: Step 6: entrypoint.sh executes

    VM->>S3: aws s3 sync s3://openclaw-tenants/wa__8613800138000/workspace/ → /tmp/workspace/
    S3-->>VM: SOUL.md, USER.md, MEMORY.md, memory/*.md, .memory-index.sqlite

    VM->>S3: aws s3 sync s3://openclaw-tenants/_shared/skills/ → /tmp/workspace/skills/_shared/
    S3-->>VM: Shared skills (Jira, S3-files, etc.)

    Note over VM: Step 7: server.py starts<br/>Plan A: Build system prompt<br/>"Allowed: web_search. Blocked: shell, file, code_execution..."

    VM->>OC: Start openclaw subprocess<br/>OPENCLAW_WORKSPACE=/tmp/workspace

    Note over OC: Step 8: OpenClaw loads<br/>SOUL.md → personality<br/>MEMORY.md → long-term memory<br/>memory/2026-03-09.md → today's context<br/>Rebuild vector index if needed

    OC->>BR: POST /v1/chat/completions<br/>{system: Plan A prompt, user: "Tokyo weather"}
    BR-->>OC: "Based on current data, Tokyo is 18°C partly cloudy..."

    Note over VM: Step 9: Plan E audit<br/>Scan response for blocked tool patterns<br/>Result: PASS (no violations)

    OC-->>VM: Response text
    VM-->>AC: Return response
    AC-->>TR: Return response
    TR-->>GW: {tenant_id: "wa__8613800138000", response: "Tokyo is 18°C..."}
    GW-->>Employee: "Tokyo is 18°C, partly cloudy today"

    Note over VM: Step 10: Watchdog (background)<br/>Every 60s: sync workspace back to S3<br/>New daily log entries, updated MEMORY.md

    Note over AC: Step 11: Session idle timeout<br/>SIGTERM → OpenClaw writes final memory<br/>→ entrypoint.sh flushes workspace to S3<br/>→ microVM released
```

## Flow 2: Permission Denied → Approval Flow

```mermaid
sequenceDiagram
    autonumber
    actor Sarah as Sarah (Intern, WhatsApp)
    participant GW as EC2 Gateway
    participant TR as Tenant Router
    participant VM as microVM (Sarah)
    participant OC as OpenClaw
    participant BR as Bedrock
    participant AUTH as Auth Agent
    actor Admin as Admin (Jordan, Discord)

    Sarah->>GW: "Run ls -la to check server logs"
    GW->>TR: Forward message
    TR->>VM: invoke(sessionId="wa__sarah")

    Note over VM: Plan A injected:<br/>"Allowed: web_search ONLY.<br/>You MUST NOT use: shell, file, code_execution"

    VM->>OC: {system: Plan A, user: "Run ls -la"}
    OC->>BR: Request with Plan A constraints
    BR-->>OC: "I don't have permission to execute shell commands.<br/>Contact your administrator."
    OC-->>VM: Response

    Note over VM: Plan E audit: PASS<br/>(OpenClaw correctly refused)

    VM-->>TR: Response: "I don't have permission..."
    TR-->>GW: Forward
    GW-->>Sarah: "I don't have permission to execute shell commands."

    Note over Sarah: Sarah requests shell access

    Sarah->>GW: "I need shell access for production issue P-1234"
    GW->>TR: Forward
    TR->>AUTH: Permission request: {tenant: wa__sarah, tool: shell, reason: "P-1234"}

    Note over AUTH: Risk assessment:<br/>Tool: shell → HIGH risk<br/>Tenant: intern → LOW trust<br/>Overall: HIGH

    AUTH->>Admin: "Sarah (Intern) requests SHELL access.<br/>Reason: production issue P-1234<br/>Risk: HIGH. Approve? (30min timeout)"
    
    Note over Admin: Reviews and approves

    Admin->>AUTH: "approve temporary 2h"
    AUTH->>SSM: Update profile: add "shell" to wa__sarah tools (TTL: 2h)

    AUTH-->>Sarah: "Shell access approved for 2 hours."

    Note over Sarah: Next message uses updated permissions

    Sarah->>GW: "Run ls -la"
    GW->>TR: Forward
    TR->>VM: invoke (new microVM, fresh permissions from SSM)

    Note over VM: Plan A now includes shell<br/>"Allowed: web_search, shell"

    VM->>OC: {system: updated Plan A, user: "Run ls -la"}
    OC->>BR: Request
    BR-->>OC: "[shell] ls -la\ntotal 24\ndrwxr-xr-x 3 ubuntu..."
    OC-->>VM: Response with shell output

    Note over VM: Plan E audit: PASS<br/>(shell is now allowed)

    VM-->>GW: Response
    GW-->>Sarah: "total 24\ndrwxr-xr-x 3 ubuntu ubuntu 4096 .\n-rw-r--r-- 1 ubuntu ubuntu 256 README.md"
```

## Flow 3: Scheduled Task (Cron)

```mermaid
sequenceDiagram
    autonumber
    participant GW as EC2 Gateway<br/>OpenClaw (Always-On)
    participant CRON as Gateway Cron Scheduler
    participant AC as AgentCore Runtime
    participant VM as Firecracker microVM
    participant S3 as S3
    participant OC as OpenClaw (native)
    participant BR as Bedrock
    actor Alex as Alex (Telegram)

    Note over CRON: Monday 8:00 AM<br/>Cron fires for tenant tg__alex<br/>Task: "Generate weekly engineering summary"

    CRON->>AC: invoke(sessionId="tg__alex",<br/>message="Generate weekly engineering summary")

    AC->>VM: Start microVM for tg__alex

    VM->>S3: Pull workspace (SOUL.md, MEMORY.md, skills)
    S3-->>VM: Workspace files

    VM->>OC: Start OpenClaw with workspace
    
    Note over OC: Loads memory from past week<br/>memory/2026-03-03.md through 2026-03-09.md

    OC->>BR: "Summarize this week's engineering activities<br/>based on my memory and daily logs"
    BR-->>OC: "Weekly Summary:\n- Completed auth module refactor\n- Fixed 3 production bugs\n- Started API v2 design..."

    OC-->>VM: Summary text

    Note over VM: Sync updated workspace to S3<br/>(new daily log entry with summary)

    VM->>S3: Sync workspace
    VM-->>AC: Return summary
    AC-->>GW: Return summary

    GW->>Alex: "Weekly Engineering Summary:\n- Completed auth module refactor\n- Fixed 3 production bugs\n- Started API v2 design..."

    Note over VM: microVM released after response
```

## Flow 4: Shared Skill with Bundled Credentials

```mermaid
sequenceDiagram
    autonumber
    actor Carol as Carol (Finance, Slack)
    participant GW as EC2 Gateway
    participant TR as Tenant Router
    participant SSM as SSM Parameter Store
    participant VM as microVM (Carol)
    participant OC as OpenClaw
    participant JIRA as Jira Skill<br/>(bundled API key)
    participant BR as Bedrock

    Carol->>GW: "Create a Jira ticket for Q1 budget review"
    GW->>TR: Forward

    TR->>SSM: Read profile for sl__carol
    SSM-->>TR: {tools: ["web_search","file"], skills: ["jira-integration"]}

    TR->>VM: invoke(sessionId="sl__carol")

    Note over VM: entrypoint.sh:<br/>1. Pull Carol's workspace from S3<br/>2. Pull shared Jira skill from S3<br/>3. Inject JIRA_API_KEY from SSM as env var<br/>4. Start OpenClaw

    VM->>SSM: Get /openclaw/skills/jira/api-key
    SSM-->>VM: (SecureString) xxxxxxxxxxx

    Note over VM: export JIRA_API_KEY=xxx<br/>Start OpenClaw<br/>Then: unset JIRA_API_KEY<br/>(prevent /proc/self/environ leak)

    VM->>OC: Start with Jira skill loaded
    OC->>BR: "Create Jira ticket: Q1 budget review, assignee: Carol"
    BR-->>OC: Tool call: jira.createTicket({summary: "Q1 budget review"})

    OC->>JIRA: createTicket(summary, assignee)<br/>Uses bundled API key from env
    JIRA-->>OC: Created: FIN-1234

    OC-->>VM: "Created Jira ticket FIN-1234: Q1 budget review"

    Note over VM: Plan E: PASS<br/>Carol is authorized for jira skill<br/>Carol never sees the Jira API key

    VM-->>GW: Response
    GW-->>Carol: "Done! Created Jira ticket FIN-1234: Q1 budget review"
```
