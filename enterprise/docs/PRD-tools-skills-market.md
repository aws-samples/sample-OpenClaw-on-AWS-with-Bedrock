# PRD: Tools & Skills Market

> Unified management page for all agent capabilities — built-in Tools + extension Skills.
> Reference: Amazon internal asbx AI Registry (MCP Registry) design patterns.

---

## 1. Problem Statement

### Current State
- **Tools** (web_search, shell, etc.) managed in Security Center → Security Policies — per-position whitelist
- **Skills** (jina-reader, excel-gen, etc.) managed in Skill Platform — flat list, no lifecycle
- Two separate pages, no connection between them
- No employee self-service: employees can't request, submit, or see what's available
- No security review workflow: skills go directly to "installed" with no vetting
- Layer 2 skills have manifest (skill.json) in S3 but most lack implementation (tool.js)
- skill_loader.py role check reads from obsolete SSM path, defaults to "employee" role
- No audit trail for skill assignment/unassignment
- API Key Vault is read-only — admin can't configure keys through the UI

### Target State
A unified **Tools & Skills** page that serves as the company's capability registry:
- Admin browses, assigns, and manages all Tools + Skills in one place
- Employees browse available capabilities, request access, submit custom skills
- Security review workflow with automated scanning (skill-vetter) + manual approval
- Tag system: Platform / Personal Config / Security Approved / Under Review / etc.
- 4 seed skills demonstrably working with real differentiation across positions

---

## 2. Design

### 2.1 Unified Data Model

Every capability (Tool or Skill) is represented as:

```
{
  "id": "web-search" | "sk-excel-gen",
  "name": "Web Search" | "Excel Generator",
  "type": "tool" | "skill",                    // NEW: distinguish tools from skills
  "description": "...",
  "author": "OpenClaw Core" | "ACME IT" | "emp-ryan",
  "category": "information" | "productivity" | "development" | ...,

  // Lifecycle
  "status": "official" | "approved" | "under_review" | "community" | "rejected",
  "submittedBy": null | "emp-ryan",            // null = platform-provided
  "submittedAt": null | "2026-04-13T...",
  "reviewedBy": null | "emp-jiade",
  "reviewedAt": null | "2026-04-13T...",
  "securityScan": null | { "passed": true, "scanner": "skill-vetter", "date": "..." },

  // Tags (displayed as badges on cards)
  "tags": [
    "platform",           // vs "community-submitted"
    "zero-config",        // vs "personal-key-required" vs "platform-key-required"
    "security-approved",  // vs "under-review" vs "not-reviewed"
    "built-in",           // vs "s3-loaded" vs "api-remote"
  ],

  // Existing fields
  "version": "1.0.0",
  "layer": 1 | 2 | 3,
  "scope": "global" | "department",
  "requires": {
    "env": [...],        // API keys needed
    "tools": [...]       // built-in tools required (e.g. skill needs "shell" tool)
  },
  "permissions": { "allowedRoles": [...], "blockedRoles": [...] },
  "setupGuide": "...",   // NEW: Markdown setup instructions
  "awsService": "..."    // if AWS-native
}
```

### 2.2 Tag System

| Tag | Color | Meaning |
|-----|-------|---------|
| `Platform` | Blue | Provided by the platform (admin-managed) |
| `Community` | Purple | Submitted by an employee |
| `Zero Config` | Green | Works immediately, no keys needed |
| `Personal Key` | Amber | Employee needs to configure their own credentials |
| `Platform Key` | Cyan | Admin configures one key for everyone |
| `Security Approved` | Green | Passed security vetting (automated + manual) |
| `Under Review` | Amber | Submitted, awaiting security assessment |
| `Not Reviewed` | Gray | No security scan performed |
| `Built-in` | Green | In Docker image (Layer 1) |
| `S3 Loaded` | Blue | Hot-loaded from S3 (Layer 2) |
| `AWS Native` | Orange | Uses AWS service via IAM role |
| `Requires: shell` | Red outline | Needs "shell" tool enabled for the position |

### 2.3 Lifecycle Tabs

```
┌─────────────┬──────────────────┬──────────────────┬───────────────────┐
│ Official (X) │ Approved (Y)     │ Under Review (Z) │ Community (W)     │
├─────────────┴──────────────────┴──────────────────┴───────────────────┤
│                                                                       │
│  [Card] [Card] [Card]                                                │
│  [Card] [Card] [Card]                                                │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

- **Official** — Platform-provided, Admin-recommended. Includes built-in Tools + pre-installed Skills.
- **Approved** — Passed security review. Employees can request access. Admin can assign to positions.
- **Under Review** — Submitted by employee or team, awaiting security assessment.
- **Community** — In development, visible for preview but not usable.

### 2.4 Built-in Tools as First-Class Items

The 6 built-in tools appear in the "Official" tab alongside skills:

| Tool | Description | Default Positions | Tags |
|------|-------------|-------------------|------|
| web_search | Search the web | ALL (always enabled) | Platform, Zero Config, Built-in |
| file | Read files from workspace | ALL | Platform, Zero Config, Built-in |
| file_write | Create/write files | SDE, DevOps, QA, Exec, SA | Platform, Zero Config, Built-in |
| shell | Execute shell commands | SDE, DevOps, QA, Exec, SA | Platform, Zero Config, Built-in |
| browser | Headless web browser | SDE, DevOps, SA | Platform, Zero Config, Built-in |
| code_execution | Run Python/Node.js | SDE, DevOps, QA, Exec, SA | Platform, Zero Config, Built-in |

Clicking a tool shows: description, which positions have it enabled, "Manage in Security Center" link.

### 2.5 Skill Detail Page / Modal

When clicking a Skill card:

```
┌─────────────────────────────────────────────────────────────┐
│ Excel Generator                              v1.0.0         │
│ Generate Excel spreadsheets with formulas, charts, pivots   │
│                                                             │
│ [Platform] [Zero Config] [Security Approved] [S3 Loaded]    │
│ Author: ACME IT · Category: data · Scope: department        │
│                                                             │
│ ─── Setup Guide ──────────────────────────────────────────  │
│ No configuration needed. This skill is ready to use.        │
│ Your agent can generate .xlsx files when you ask for        │
│ spreadsheets, reports, or data analysis.                    │
│                                                             │
│ ─── Assigned Positions ───────────────────────────────────  │
│ [Finance Analyst ✓] [Executive ✓]                           │
│ [+ Assign to Position ▾]  [Assign]                          │
│                                                             │
│ ─── Requires ─────────────────────────────────────────────  │
│ Tools: file_write (check: ✓ FA has it? NO → warning)        │
│ API Keys: none                                              │
│                                                             │
│ ─── Security ─────────────────────────────────────────────  │
│ Scan: ✓ Passed (skill-vetter, 2026-04-13)                   │
│ Reviewed by: JiaDe Wang (2026-04-13)                        │
└─────────────────────────────────────────────────────────────┘
```

### 2.6 Employee Flows

#### Flow A: Employee requests a Skill (Portal)

```
Employee → Portal → "Tools & Skills" → browses Approved skills
  → clicks "Request Access" on a skill
  → APPROVAL# created (type: skill_access_request)
  → Admin sees in Approvals page → Approve
  → EMP#.personalSkills updated → skill loaded on next cold start
```

#### Flow B: Employee submits a custom Skill (Portal)

```
Employee → Portal → "Submit Skill" button
  → Upload: skill.json + tool.js (or .zip directory)
  → Files stored to S3: _pending/skills/{skill-name}/
  → APPROVAL# created (type: skill_submit, status: pending)
  → Skill appears in "Under Review" tab
  → Admin clicks → sees code + manifest + auto-scan results
  → Admin runs skill-vetter scan (or it runs automatically)
  → Admin approves → files moved: _pending/ → _shared/skills/
  → Skill status → "approved", appears in "Approved" tab
  → Admin rejects → status → "rejected", reason recorded
```

#### Flow C: Admin assigns Skill to Position

```
Admin → Tools & Skills → clicks skill → "Assign to Position"
  → POS#.defaultSkills updated
  → bump_config_version()
  → AUDIT# entry (skill_assignment)
  → All employees in that position get the skill on next cold start
```

### 2.7 API Key Strategy (Simplified)

No per-employee key management backend. Three types, handled differently:

| Type | Example | Who Configures | How |
|------|---------|----------------|-----|
| **IAM Role** | Nova Canvas, S3, SES, Transcribe | Nobody | AWS IAM role on AgentCore execution role provides access. Zero config. |
| **Platform Key** | BEDROCK_KB_ID, FIRECRAWL_API_KEY (company subscription) | Admin | Admin Console → Tools & Skills → API Keys tab → SSM SecureString. One key, all employees use it. |
| **Personal Credential** | GITHUB_TOKEN, IMAP_PASS, JIRA_API_TOKEN | Employee | **No backend.** Employee reads the skill's Setup Guide, then tells their Agent the credential in conversation. Agent stores it in MEMORY.md for future sessions. |

**Setup Guide replaces key management UI for personal credentials.** Each skill has a `setup-guide.md` in S3 alongside `skill.json` and `tool.js`:

```markdown
# GitHub Skill — Setup Guide

## What you need
A GitHub Personal Access Token with `repo` + `read:org` scope.

## How to get it
1. Go to github.com → Settings → Developer settings → Personal access tokens → Fine-grained tokens
2. Generate a new token, select your organization, grant `repo` read/write
3. Copy the token (starts with `ghp_`)

## How to configure
Tell your agent: "My GitHub token is ghp_xxxx"
Your agent will remember it for future sessions.

## What this skill can do
- List and search GitHub issues
- Create and update pull requests
- Check CI/CD run status
- Review code changes
```

This is displayed in the Skill Detail Modal under "Setup Guide" section.

### 2.8 Skill Output Directory Convention

**All output-producing skills MUST write files to `workspace/output/`.**

This is a hard requirement enforced during security review:

```javascript
// REQUIRED pattern in tool.js for any skill that generates files:
const workspace = process.env.OPENCLAW_WORKSPACE || '/root/.openclaw/workspace';
const outputDir = path.join(workspace, 'output');
fs.mkdirSync(outputDir, { recursive: true });
const outputPath = path.join(outputDir, filename);
```

**Why `output/` specifically:**
- Watchdog syncs `output/` to S3 → Portal "My Workspace" shows these files
- Cold start does NOT sync `output/` back from S3 → workspace stays lean
- Session Storage restores `output/` but entrypoint.sh cleans it → no accumulation
- Clear semantic boundary: "output/ = skill-generated files, everything else = system files"

**Lifecycle of a skill output file:**

```
Agent generates report.xlsx → workspace/output/report.xlsx
  ↓
Watchdog (60s) → S3 {emp_id}/workspace/output/report.xlsx  ← persisted permanently
  ↓
Session ends → Session Storage snapshots workspace including output/report.xlsx
  ↓
Next session starts → Session Storage restores → entrypoint.sh cleans output/
  rm -rf "$WORKSPACE/output" && mkdir -p "$WORKSPACE/output"
  ↓
workspace/output/ is empty (fresh session)
S3 {emp_id}/workspace/output/report.xlsx still there ← Portal reads from here
  ↓
Cold start S3 sync: aws s3 sync ... --exclude "output/*" ← NOT pulled back
```

**Result:** Session Storage never accumulates output files. S3 has complete history. Portal shows everything.

### 2.9 Agent Tool Chaining (not Skill-to-Skill calls)

**Skills cannot call each other.** Each skill's `tool.js` is an independent process — `node tool.js '{"input":"..."}'` → stdout JSON output. No inter-skill API.

**Agent autonomously chains tools** when it has multiple capabilities. Example:

```
Employee: "Generate Q2 budget report and email it to the team"

Agent thinks: I have excel-gen + aws-ses-mailer
  Step 1: Call excel-gen → workspace/output/Q2-budget.xlsx
  Step 2: Call aws-s3-docs share → get pre-signed URL
  Step 3: Call aws-ses-mailer → send email with download link
  Step 4: Reply to employee with confirmation
```

This works because:
- Agent sees all available tools in its context (SOUL + tool manifests)
- Bedrock Converse handles multi-step tool use natively
- No special "orchestration" code needed — the LLM figures out the sequence

**Implication for skill design:** Skills should be **single-purpose** (do one thing, return result). Complex workflows are the Agent's job, not the skill's.

### 2.10 install_skill — Blocked by Default

```
alwaysBlocked: ["install_skill", "load_extension", "eval"]
```

OpenClaw natively supports `openclaw skills install xxx` — employees can ask their Agent to install skills from ClawHub marketplace. **This is blocked in enterprise deployments.**

Reasons:
- Unvetted code execution inside the microVM
- No security scan before installation
- Bypasses admin approval workflow
- Installed skills disappear on session end (serverless) — confusing UX

**Instead:** Employees submit custom skills through Portal → Admin reviews → skill enters the catalog. This is Flow B in Section 2.6.

### 2.11 Security Review Flow

```
Submission received
  ↓
Auto-scan: skill-vetter analyzes skill.json + tool.js
  - Check: undeclared env access
  - Check: hidden network calls (fetch/http/https outside declared scope)
  - Check: obfuscated code (eval, Function(), encoded strings)
  - Check: filesystem access outside workspace
  - Check: shell command injection patterns
  ↓
Scan result stored: securityScan: { passed: true/false, findings: [...] }
  ↓
If auto-pass (0 findings) → status: "approved" automatically
If findings → status: "under_review", Admin manually reviews
  ↓
Admin approves/rejects with comments
  ↓
AUDIT# entry: skill_review_decision
```

---

## 3. Current Layer Status (What Works Today)

Three layers of skill loading exist. Understanding what's real vs placeholder is critical for implementation planning.

### Layer 1 — Docker Built-in (`clawhub install` at build time)

**Status: WORKING.** 13 skills pre-installed in Docker image:

```
jina-reader, deep-research-pro, transcript, gog, summarize, notion,
github, himalaya, slack, firecrawl, self-improving-agent, api-gateway, skill-vetter
```

- Installed to `/root/.openclaw/skills/` in Docker image
- Available to ALL agents regardless of position (no permission filtering)
- These are OpenClaw community npm packages with full tool implementations
- **Problem:** No Plan A permission filtering — Finance Analyst can use `github` skill

### Layer 2 — S3 Hot-load (enterprise custom skills)

**Status: PARTIALLY WORKING.** skill_loader.py code works, but S3 data is incomplete.

| Skill | skill.json in S3? | tool.js in S3? | tool.js exists in source? | Can it work? |
|-------|-------------------|----------------|--------------------------|-------------|
| excel-gen | YES (seed) | NO | YES (agent-container/skills/) | **Upload needed** |
| aws-nova-canvas | YES (seed) | NO | YES (agent-container/skills/) | **Upload needed** |
| aws-s3-docs | YES (seed) | NO | YES (agent-container/skills/) | **Upload needed** |
| aws-ses-mailer | YES (seed) | NO | YES (agent-container/skills/) | **Upload needed** |
| aws-sns-notify | YES (seed) | NO | YES (agent-container/skills/) | **Upload needed** |
| aws-transcribe-notes | YES (seed) | NO | YES (agent-container/skills/) | **Upload needed** |
| aws-bedrock-kb-search | YES (seed) | NO | YES (agent-container/skills/) | **Upload + KB ID config** |
| crm-query | YES (seed) | NO | YES (agent-container/skills/) | **Upload + SF key config** |
| pptx-creator | YES (seed) | NO | NO | Manifest only |
| sap-connector | YES (seed) | NO | NO | Manifest only |
| calendar-check | YES (seed) | NO | NO | Manifest only |
| jira-query | YES (seed) | NO | NO | Manifest only |
| aws-nova-sonic-caller | YES (seed) | NO | NO | Manifest only |
| (13 others) | YES (seed) | NO | NO (Layer 1 covers these via clawhub) | N/A |

**Fix:** Upload 8 tool.js files from `agent-container/skills/` to `S3 _shared/skills/{name}/tool.js`.

### Layer 3 — Pre-built Bundles (tar.gz)

**Status: NOT IMPLEMENTED.** Code skeleton exists in skill_loader.py but:
- No SSM skill-catalog entries
- No S3 `_shared/skill-bundles/*.tar.gz` files
- **Not needed for Phase 1.** Layer 1 + Layer 2 cover all use cases.

### skill_loader.py Issues

1. **Role matching broken:** Reads from SSM `/openclaw/{stack}/tenants/{id}/roles` which is never written. Defaults to `["employee"]`. Most department-scoped skills filtered out.
2. **No DynamoDB integration:** Should read employee's position → department → map to role for permission filtering.
3. **Layer 1 bypass:** Docker built-in skills are NOT filtered by skill_loader (they're pre-installed in the image). Only Layer 2 skills go through permission check.

---

## 4. API Changes

### New Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | /api/v1/tools-skills | Any | Unified list: 6 tools + all skills, with tags + status |
| GET | /api/v1/tools-skills/{id} | Any | Detail: setup guide, assignments, security scan |
| POST | /api/v1/tools-skills/{id}/assign | Admin | Assign to position (works for both tools and skills) |
| DELETE | /api/v1/tools-skills/{id}/assign | Admin | Remove from position |
| POST | /api/v1/portal/skills/submit | Employee | Upload custom skill (skill.json + tool.js) |
| POST | /api/v1/portal/skills/{id}/request | Employee | Request access to an approved skill |
| GET | /api/v1/tools-skills/pending | Admin | List skills under review |
| POST | /api/v1/tools-skills/{id}/review | Admin | Approve/reject with scan results |
| POST | /api/v1/tools-skills/{id}/scan | Admin | Trigger skill-vetter security scan |
| PUT | /api/v1/skills/keys/{skill}/{env_var} | Admin | Configure platform-level API key |

### Modified Endpoints

| Method | Path | Change |
|--------|------|--------|
| POST | /api/v1/skills/{name}/assign | Add: bump_config_version() + AUDIT# + force refresh |
| DELETE | /api/v1/skills/{name}/assign | Add: bump_config_version() + AUDIT# |

### Data Model Changes

| Record | Field | Change |
|--------|-------|--------|
| S3 `_shared/skills/{name}/` | `setup-guide.md` | NEW: Markdown setup instructions |
| S3 `_shared/skills/{name}/` | `tool.js` | Ensure present for all working skills |
| S3 `_pending/skills/{name}/` | All files | NEW: staging area for submitted skills |
| DynamoDB `EMP#` | `personalSkills: string[]` | NEW: employee's personally-approved skills |
| DynamoDB `APPROVAL#` | `type: "skill_submit"` | NEW: skill submission approval |
| DynamoDB `APPROVAL#` | `type: "skill_access_request"` | NEW: employee access request |
| DynamoDB `CONFIG#` | `skill-reviews: {...}` | NEW: scan results cache |

---

## 4. Frontend Changes

### 4.1 Replace Skill Platform page → "Tools & Skills"

**Remove:** `src/pages/Skills/SkillCatalog.tsx` (current flat list)

**New:** `src/pages/ToolsSkills/index.tsx`

Layout (reference: asbx AI Registry):
- Header: "Tools & Skills" + search bar + "Submit Skill" button
- Filter row: Internal / Community toggle + category filter + search
- Tabs: Official (count) | Approved (count) | Under Review (count) | Community (count) | API Keys
- Card grid (3 columns):
  - Card: name, description, author, team/org, tags (colored badges), click → detail modal

### 4.2 Card Design (reference: MCP Registry cards)

```
┌────────────────────────────────────────────┐
│ Excel Generator                            │
│ Generate Excel spreadsheets with formulas, │
│ charts, and multiple sheets.               │
│                                            │
│ ACME IT / Finance                          │
│ [Platform] [Security Approved]             │
│ [Zero Config] [S3 Loaded]                  │
└────────────────────────────────────────────┘
```

### 4.3 Portal "My Tools & Skills" page

Employee view:
- "My Active" — tools + skills currently available to my agent
- "Browse" — all approved tools & skills I can request
- "My Requests" — pending/approved/rejected requests
- "Submit" — upload custom skill for review

### 4.4 Portal "My Workspace" page

Employee sees their Agent's complete S3 workspace as a file browser:

```
My Workspace
├── Identity/                            [read-only section]
│   ├── SOUL.md        (merged, view-only)
│   ├── PERSONAL_SOUL.md  (editable)
│   └── USER.md           (editable)
├── Memory/                              [read-only section]
│   ├── MEMORY.md      (synthesized memory)
│   └── Daily/
│       ├── 2026-04-10.md
│       ├── 2026-04-11.md
│       └── ...
├── Output/                              [skill-generated files — read/download]
│   ├── Q2-budget.xlsx
│   ├── architecture-diagram.png
│   └── competitive-analysis.md
├── Skills/                              [metadata only — shows assigned skills]
│   ├── excel-gen (Platform, v1.0.0)
│   ├── deep-research (Built-in, v1.3.0)
│   └── ...
└── Knowledge/                           [metadata only — shows assigned KBs]
    ├── Company Policies (12 docs)
    └── Architecture Standards (8 docs)
```

**Backend:** `GET /portal/workspace-tree`

This is NOT a flat S3 listing. It merges multiple data sources:

| Section | Source | Writable? |
|---------|--------|-----------|
| Identity/ | S3 `{emp}/workspace/` (SOUL, USER.md, PERSONAL_SOUL) + pipeline API for merged SOUL | PERSONAL_SOUL.md, USER.md only |
| Memory/ | S3 `{emp}/workspace/MEMORY.md` + `{emp}/workspace/memory/` | Read-only |
| Output/ | S3 `{emp}/workspace/output/` | Download only (employee can delete) |
| Skills/ | DynamoDB POS#.defaultSkills + EMP#.personalSkills (metadata, not files) | Read-only |
| Knowledge/ | DynamoDB KB assignments (metadata, not files) | Read-only |

**File actions:**
- View: Markdown/text files rendered inline
- Download: any file via pre-signed URL
- Edit: USER.md and PERSONAL_SOUL.md only (saves to S3)
- Delete: output files only (employee can clean up their own output)

**Workspace usage indicator:**
```
Workspace Usage: 42 MB / 100 MB  [████████░░░░░░░░] 42%
Output files: 15 files, 38 MB
Oldest file: 2026-03-15 (auto-cleanup in 15 days)
```

### 4.5 Security Center integration

Security Center → Security Policies tab:
- Per-position card shows: "Tools: [web_search, file]  Skills: [excel-gen, deep-research]"
- "Manage" button → navigates to Tools & Skills filtered by that position
- Tool/Skill changes from Tools & Skills page automatically update the position whitelist

### 4.5 Sidebar navigation change

```
Before:                    After:
├── Skill Platform         ├── Tools & Skills
```

---

## 5. Seed Skills (4 working skills for demo)

### 5.1 excel-gen → Finance Analyst

**Assign to:** pos-fa, pos-exec
**Action:** Upload tool.js from agent-container/skills/excel-gen/ to S3
**Demo:** Carol asks "Generate a Q2 budget vs actual comparison spreadsheet for Engineering"
**Tags:** Platform, Zero Config, Security Approved, S3 Loaded

### 5.2 deep-research (Layer 1, Docker built-in)

**Assign to:** pos-sa, pos-pm, pos-legal
**Action:** Already in Docker image. Just assign to positions.
**Demo:** JiaDe asks "Research Bedrock AgentCore vs LangGraph — full comparison"
**Tags:** Platform, Zero Config, Security Approved, Built-in

### 5.3 aws-nova-canvas → Sales, PM

**Assign to:** pos-ae, pos-pm, pos-csm
**Action:** Upload tool.js from agent-container/skills/aws-nova-canvas/ to S3
**Demo:** Mike asks "Create a visual diagram of our platform architecture for the client deck"
**Tags:** Platform, Zero Config, Security Approved, AWS Native, S3 Loaded

### 5.4 self-improving-agent (Layer 1, Docker built-in)

**Assign to:** ALL positions (global scope)
**Action:** Already in Docker image. Assign globally.
**Demo:** Any employee corrects their agent → next session remembers
**Tags:** Platform, Zero Config, Security Approved, Built-in

---

## 6. Backend Fixes (prerequisites)

### 6.1 skill_loader.py — fix role matching

**Current:** Reads roles from SSM `/openclaw/{stack}/tenants/{id}/roles` → defaults to ["employee"]
**Fix:** Read employee's position → department from DynamoDB. Map department name to role for permission filtering.

### 6.2 Upload 8 tool.js files to S3

The 8 skills in `agent-container/skills/` have tool.js but it's not in S3:
```
aws-bedrock-kb-search, aws-nova-canvas, aws-s3-docs, aws-ses-mailer,
aws-sns-notify, aws-transcribe-notes, crm-query, excel-gen
```
Action: Upload `skill.json` + `tool.js` for each to `_shared/skills/{name}/`

### 6.3 Assign endpoint — add audit + config bump

```python
# Current (agents.py assign_skill_to_position):
skills.append(skill_name)
db.update_position(position_id, {"defaultSkills": skills})

# After:
skills.append(skill_name)
db.update_position(position_id, {"defaultSkills": skills})
bump_config_version()
_audit("skill_assignment", user, position_id, f"Assigned {skill_name}")
# force refresh affected employees
threading.Thread(target=_refresh_position_employees, args=(position_id,)).start()
```

### 6.4 API Key write endpoint

```python
@router.put("/api/v1/skills/keys/{skill_name}/{env_var}")
def set_skill_key(skill_name: str, env_var: str, body: dict, authorization=Header(default="")):
    require_role(authorization, roles=["admin"])
    ssm.put_parameter(
        Name=f"/openclaw/{stack}/skill-keys/{skill_name}/{env_var}",
        Value=body["value"], Type="SecureString", Overwrite=True)
    audit(...)
    return {"configured": True}
```

---

## 7. Workspace Storage Model & Deployment Guidance

### 7.1 The Constraint

> **UPDATE 2026-04-14: Session Storage is no longer used.** See design-session-storage.md.
> The 100MB budget is kept as good hygiene for AgentCore microVM disk,
> but the 1GB Session Storage limit and 3-way state issues no longer apply.
> Fargate + EFS has unlimited storage.

~~AgentCore Session Storage is a **black box**.~~ Every cold start rebuilds workspace from S3 (source of truth). No caching between sessions.

**Workspace budget: 100MB** (keeps microVM disk lean; not a hard platform limit anymore).

### 7.2 How Skill Output Works

```
Employee: "Generate a Q2 budget spreadsheet"
  ↓
Agent calls excel-gen → writes /root/.openclaw/workspace/Q2-budget.xlsx (200KB)
  ↓
Watchdog (every 60s) → aws s3 sync workspace/ → S3 {emp_id}/workspace/Q2-budget.xlsx
  ↓
Portal "My Workspace" → employee sees and downloads Q2-budget.xlsx
  ↓
Next cold start → S3 sync back → file still there
```

All skill output goes through workspace → S3 sync. No special routing needed.

### 7.3 The 100MB Budget

Enforced in `workspace_assembler.py` at cold start:

```
Total workspace size check:
  system files (SOUL, MEMORY, USER.md, etc.)     ~1MB   — never cleaned
  skills/ (loaded by skill_loader)                ~5MB   — managed by platform
  knowledge/ (loaded by workspace_assembler)      ~10MB  — managed by platform
  user files (Agent-generated: .xlsx, .py, etc.)  ≤100MB — cleaned when exceeded
  ─────────────────────────────────────────────────
  Total:                                          ≤116MB — well within 1GB Session Storage
```

When user files exceed 100MB:
1. Delete files older than 30 days (preserve MEMORY.md, USER.md, PERSONAL_SOUL.md, memory/)
2. If still over 100MB, delete oldest files until under limit
3. Log warning to AUDIT#: "workspace_cleanup: deleted N files, freed X MB"

### 7.4 Two Deployment Modes

Not all workloads fit in 100MB serverless workspace. The platform offers two modes:

| | Serverless (AgentCore) | Always-On (Fargate + EFS) |
|---|---|---|
| **Workspace** | 100MB limit (Session Storage ~1GB) | **Unlimited** (EFS) |
| **Cold start** | 6s first time, 0.5s resume | **None** (always running) |
| **Idle behavior** | microVM released after 15 min | Container stays running |
| **Cost** | Pay per session (~$0.001/invocation) | Pay 24/7 (~$30/month per agent) |
| **Best for** | Chat, research, light file generation, most employees | Bulk file processing, large datasets, dev environments, executives |
| **Skill output** | Via workspace → S3 sync (100MB cap) | Direct to EFS (unlimited), synced to S3 on shutdown |

**Admin Decision Guide:**

| Scenario | Recommended Mode | Why |
|----------|-----------------|-----|
| Sales AE doing outreach emails | Serverless | Light text output, short sessions |
| Finance Analyst generating weekly Excel reports | Serverless | 1-2 files per session, well within 100MB |
| DevOps running CI/CD scripts with large logs | **Always-On** | May generate 100MB+ logs per session |
| SA building multi-file architecture proposals | Serverless (borderline) | Multiple docs but manageable |
| Data team processing CSV datasets | **Always-On** | Datasets can be GB-scale |
| Executive with dedicated IM bot | **Always-On** | Always-available, no cold start, VIP experience |

This guidance should be visible in:
1. **Security Center → Runtime Assignment**: when Admin assigns a position to a runtime, show a recommendation badge
2. **Settings → Agent Config**: per-position "Deploy Mode" toggle (serverless / always-on)
3. **Portal → My Agent**: employee sees their agent's deploy mode and workspace usage

### 7.5 Product Documentation (visible to users)

**For Admin (in Tools & Skills page header or docs tab):**

> **Workspace Storage**
>
> Each agent has a 100MB workspace for files generated during conversations (spreadsheets, reports, scripts, images). Files are automatically synced to S3 and visible in the employee's Portal under "My Workspace".
>
> Files older than 30 days are automatically cleaned to stay within the limit. Employees should download important files promptly.
>
> For workloads that require more storage (large datasets, bulk processing), switch the position to **Always-On mode** in Security Center → Runtime Settings. Always-On agents use EFS with unlimited storage.

**For Employee (in Portal → My Workspace):**

> **Your Agent's Workspace**
>
> Files your agent creates (Excel reports, code, documents) are saved here. You can view, download, and share them.
>
> - Workspace has a 100MB storage limit
> - Files older than 30 days are automatically cleaned
> - Download important files to keep them permanently
> - If you need more space, ask your admin about Always-On mode

---

## 8. Implementation Order

```
Phase 1: Make existing skills actually work (1 session)
  ├── 6.1 Fix skill_loader.py role matching
  ├── 6.2 Upload 8 tool.js to S3
  ├── 6.3 Fix assign endpoint (audit + bump + refresh)
  ├── 6.4 API key write endpoint
  ├── Assign 4 seed skills to positions
  ├── Real conversation verification
  └── Unit tests

Phase 2: Unified "Tools & Skills" page (1 session)
  ├── New unified API: GET /tools-skills
  ├── Frontend: replace SkillCatalog → ToolsSkills
  ├── Card grid with tags (reference MCP Registry)
  ├── Lifecycle tabs: Official / Approved / Under Review / Community
  ├── Detail modal with setup guide + assignments + security
  ├── Security Center integration (cross-link)
  └── Sidebar rename

Phase 3: Employee submission + review flow (1 session)
  ├── POST /portal/skills/submit (upload to _pending/)
  ├── POST /portal/skills/{id}/request (access request)
  ├── Admin review UI: code viewer + scan results + approve/reject
  ├── Security scan integration (skill-vetter)
  ├── Portal "My Tools & Skills" page
  ├── EMP#.personalSkills persistence
  └── workspace_assembler reads personalSkills
```

---

## 8. TODO Checklist

### Phase 1: Make skills work + clean up (1 session)

**Backend fixes:**
- [ ] Fix skill_loader.py: DynamoDB role lookup instead of SSM
- [ ] Fix assign endpoint: add audit + bump_config_version + force refresh
- [ ] Fix unassign endpoint: add audit + bump_config_version
- [ ] Add PUT /skills/keys/{skill}/{env} endpoint (Admin configures platform keys)
- [ ] Modify excel-gen tool.js: output to `workspace/output/` instead of `/tmp/`
- [ ] Modify aws-nova-canvas tool.js: output to `workspace/output/`
- [ ] Add `_enforce_workspace_budget()` to workspace_assembler.py
- [ ] Add entrypoint.sh: `rm -rf $WORKSPACE/output && mkdir -p $WORKSPACE/output` on session restore
- [ ] Add entrypoint.sh: cold start S3 sync `--exclude "output/*"` (don't pull back output files)

**Seed cleanup:**
- [ ] Rewrite seed_skills_final.py: reduce from 26 to 5 real skills
- [ ] Delete 21 manifest-only skills from S3 `_shared/skills/`
- [ ] Upload 5 tool.js + skill.json + setup-guide.md to S3

**Skill assignment + demo:**
- [ ] Assign: excel-gen → pos-fa, pos-exec
- [ ] Assign: deep-research → pos-sa, pos-pm, pos-legal (Layer 1, just POS#.defaultSkills)
- [ ] Assign: aws-nova-canvas → pos-ae, pos-pm, pos-csm
- [ ] Assign: self-improving-agent → all positions (global)
- [ ] Assign: aws-bedrock-kb-search → pos-legal, pos-exec (shows "not configured" state)
- [ ] Write setup-guide.md for each of the 5 skills
- [ ] Add File Output Policy to Global SOUL (see Section 2.8)
- [ ] Verify: Carol generates Excel → appears in output/ → sync to S3
- [ ] Verify: JiaDe uses deep-research → multi-source report
- [ ] Verify: Mike uses nova-canvas → image generated
- [ ] Unit tests for assign/unassign/key endpoints

### Phase 2: Unified "Tools & Skills" page (1 session)

**Backend:**
- [ ] New API: GET /tools-skills (merge 6 built-in tools + all skills, add tags + status)
- [ ] New API: GET /tools-skills/{id} (detail: setup guide, assignments, security scan, requires)
- [ ] New API: GET /portal/workspace-tree (merged virtual file tree — Section 4.4)

**Frontend — Admin:**
- [ ] New page: ToolsSkills/index.tsx — card grid + lifecycle tabs + filters
- [ ] Card component with tag badges (reference: MCP Registry style)
- [ ] Detail modal: setup guide + assign to position + security scan results + requires check
- [ ] Security Center → Policies tab: show assigned skills per position, "Manage" → link to Tools & Skills
- [ ] Sidebar: rename "Skill Platform" → "Tools & Skills"

**Frontend — Portal:**
- [ ] Portal "My Workspace" page (Section 4.4 — Identity, Memory, Output, Skills, Knowledge)
- [ ] Workspace usage indicator (42 MB / 100 MB progress bar)
- [ ] Output file browser: view, download, delete

### Phase 3: Employee submission + review flow (1 session)

**Backend:**
- [ ] API: POST /portal/skills/submit (upload skill.json + tool.js to S3 _pending/ + APPROVAL#)
- [ ] API: POST /portal/skills/{id}/request (APPROVAL# skill_access_request)
- [ ] API: POST /tools-skills/{id}/review (Admin approve/reject → move _pending/ → _shared/)
- [ ] API: POST /tools-skills/{id}/scan (trigger skill-vetter security scan)
- [ ] Backend: workspace_assembler reads EMP#.personalSkills + POS#.defaultSkills (merge)
- [ ] Backend: skill-vetter scan checks (write outside workspace → reject, eval/Function → reject)

**Frontend — Admin:**
- [ ] Review UI: code viewer (tool.js syntax highlighted) + scan results + approve/reject buttons
- [ ] Under Review tab shows pending submissions with scan status badge

**Frontend — Portal:**
- [ ] Portal "My Tools & Skills": My Active + Browse + My Requests + Submit
- [ ] Submit Skill form: upload skill.json + tool.js + description
- [ ] Request Access button on approved skills → creates APPROVAL#

**Seed data:**
- [ ] 2-3 "community" skills for demo (visible in Under Review tab)

---

## 9. Storage Safety — Final Design

> Full storage model documented in Section 7. This section covers implementation details.

### 9.1 Single Rule

**Workspace user-writable space is capped at 100MB.** This is the only guardrail needed.

- Session Storage is a black box (~1GB limit), no API to control
- skills/ (~5MB) + knowledge/ (~10MB) stay in Session Storage (saves re-pull time)
- Total Session Storage usage: ~15MB platform files + ≤100MB user files = ~115MB max
- Well within 1GB limit

### 9.2 Skill Output Constraint

All output-producing skill tool.js MUST write files to `workspace/output/`, not /tmp or arbitrary paths.

```javascript
// REQUIRED pattern in tool.js for any skill that generates files:
const workspace = process.env.OPENCLAW_WORKSPACE || '/root/.openclaw/workspace';
const outputDir = path.join(workspace, 'output');
fs.mkdirSync(outputDir, { recursive: true });
const outputPath = path.join(outputDir, filename);
// ... generate file to outputPath ...
```

This is a **hard requirement** enforced at security review time:
- **Skill submission rules**: documented in skill development guide ("output files go to workspace/output/")
- **Security review (skill-vetter)**: checks that tool.js doesn't write outside workspace; rejects writes to /tmp, /home, or absolute paths
- **Admin approval**: rejects skills that write to arbitrary paths
- **entrypoint.sh**: cleans `workspace/output/` on every Session Storage restore (see Section 2.8)

### 9.3 Workspace Cleanup

In `workspace_assembler.py` at cold start:

```python
def _enforce_workspace_budget(workspace: str, max_mb: int = 100):
    """Clean old user files if workspace exceeds budget."""
    PROTECTED = {"SOUL.md", "PERSONAL_SOUL.md", "USER.md", "MEMORY.md",
                 "IDENTITY.md", "AGENTS.md", "TOOLS.md", "HEARTBEAT.md"}
    PROTECTED_DIRS = {"memory", "skills", "knowledge"}

    total = sum(f.stat().st_size for f in Path(workspace).rglob('*')
                if f.is_file() and f.name not in PROTECTED
                and not any(p in f.parts for p in PROTECTED_DIRS))

    if total <= max_mb * 1024 * 1024:
        return

    # Sort by mtime, delete oldest first until under budget
    user_files = sorted(
        [f for f in Path(workspace).rglob('*')
         if f.is_file() and f.name not in PROTECTED
         and not any(p in f.parts for p in PROTECTED_DIRS)],
        key=lambda f: f.stat().st_mtime
    )
    freed = 0
    for f in user_files:
        if total - freed <= max_mb * 1024 * 1024:
            break
        freed += f.stat().st_size
        f.unlink()
    # Audit log
    log.info(f"workspace_cleanup: freed {freed // 1024}KB, {len(cleaned)} files deleted")
```

### 9.4 When 100MB Is Not Enough → Always-On

See Section 7.4 for deployment mode decision guide. Admin switches position to Fargate + EFS for unlimited storage.

### 9.5 Portal "My Workspace"

See Section 4.4 for complete design. Output files are read from S3 `{emp}/workspace/output/`.

---

## 10. Seed Skills — Keep Only What Works

### 10.1 Seed Skills to KEEP (5 — assigned to positions, demonstrated in real conversations)

| # | Skill | Layer | Assign To | Tags | Key? | Demo |
|---|-------|-------|-----------|------|------|------|
| 1 | excel-gen | L2 S3 | FA, Exec | Platform, Zero Config, S3 Loaded | No | Carol generates budget Excel to output/ |
| 2 | deep-research | L1 Docker | SA, PM, Legal | Platform, Zero Config, Built-in | No | JiaDe researches AgentCore vs LangGraph |
| 3 | aws-nova-canvas | L2 S3 | AE, PM, CSM | Platform, Zero Config, AWS Native | No | Mike creates architecture diagram |
| 4 | self-improving-agent | L1 Docker | ALL (global) | Platform, Zero Config, Built-in | No | Any employee corrects agent → remembers |
| 5 | aws-bedrock-kb-search | L2 S3 | Legal, Exec | Platform, Platform Key, AWS Native | BEDROCK_KB_ID | Shows "needs config" state |

### 10.2 Seed Skills to DELETE from S3

The following 21 skills were seeded as manifest-only (skill.json without tool.js). They show up in the Skill Platform as "installed" but do nothing. **Remove from seed_skills_final.py and S3 on next deploy:**

```
DELETE from seed (no tool.js, misleading):
  web-search          → built-in TOOL, not a skill (already in Plan A)
  jina-reader         → Layer 1 Docker (already works, no need for S3 manifest)
  deep-research       → Layer 1 Docker (same)
  summarize           → Layer 1 Docker (same)
  self-improving-agent→ Layer 1 Docker (same)
  s3-files            → no tool.js, no implementation
  transcript          → Layer 1 Docker (same)
  skill-vetter        → Layer 1 Docker (same)
  pptx-creator        → no tool.js, manifest only
  sap-connector       → no tool.js, needs SAP credentials
  calendar-check      → no tool.js, needs Google credentials
  jira-query          → no tool.js, needs Jira credentials
  aws-nova-sonic-caller → no tool.js
  crm-query           → has tool.js but needs Salesforce OAuth
  himalaya            → Layer 1 Docker, needs personal email creds
  notion              → Layer 1 Docker, needs personal API key
  github              → Layer 1 Docker, needs personal token
  gog                 → Layer 1 Docker, needs Google key
  firecrawl           → Layer 1 Docker, needs API key
  aws-ses-mailer      → has tool.js but needs SES config (FROM email)
  aws-sns-notify      → has tool.js but needs SNS topic ARN
```

**After cleanup, S3 `_shared/skills/` should contain only:**
```
_shared/skills/
  excel-gen/          skill.json + tool.js + setup-guide.md
  aws-nova-canvas/    skill.json + tool.js + setup-guide.md
  aws-s3-docs/        skill.json + tool.js + setup-guide.md
  aws-transcribe-notes/ skill.json + tool.js + setup-guide.md
  aws-bedrock-kb-search/ skill.json + tool.js + setup-guide.md
```

5 skills with real implementations. Layer 1 Docker skills (jina-reader, deep-research, etc.) are already in the Docker image and don't need S3 manifests.

### 10.3 Updated seed_skills_final.py

Reduce from 26 skills to 5. Only seed skills that have real tool.js AND will be assigned to positions. Layer 1 Docker built-in skills are handled by OpenClaw's native skill discovery — the Skill Platform UI reads them from the Docker image's skill directory, not from S3.
