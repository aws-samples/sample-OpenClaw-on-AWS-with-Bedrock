import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft, CheckCircle, X, Zap, Shield, Key, Wrench, Puzzle,
  ChevronRight, AlertTriangle, ExternalLink, Clock, Users, Code,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { Card, Badge, Button, Select, Tabs } from '../../components/ui';
import { useSkills, useSkillKeys, usePositions, useAssignSkill, useUnassignSkill } from '../../hooks/useApi';
import type { SkillManifest, SkillApiKey } from '../../hooks/useApi';

// ── Built-in Tools data ───────────────────────────────────────────────────
const TOOL_DETAILS: Record<string, { description: string; guide: string; positions: string }> = {
  'web_search': {
    description: 'Search the web using multiple search engines. This is the most fundamental agent capability — it allows agents to find up-to-date information, research topics, and verify facts in real-time.\n\nWeb search is always enabled for all positions. It cannot be disabled.',
    guide: '## No Setup Required\n\nWeb search is a built-in capability that works out of the box. Your agent uses it automatically when it needs to find information online.\n\n## How it works\n\n1. Agent receives a question that requires external knowledge\n2. Agent formulates a search query\n3. Results are retrieved and synthesized into the response\n4. Sources are cited when available\n\n## Examples\n\n- "What are the latest AWS pricing changes?"\n- "Find competitor analysis for our Q2 report"\n- "Research best practices for microservice architecture"',
    positions: 'ALL positions (always enabled)',
  },
  'file': {
    description: 'Read files from the agent workspace. Agents can read any file in their workspace directory including documents, code, configuration files, and data files.\n\nThis tool is enabled for all positions by default. It provides read-only access — agents cannot modify files with this tool alone.',
    guide: '## No Setup Required\n\nFile reading is enabled by default. Agents can read:\n\n- Markdown documents (.md)\n- Code files (.py, .js, .ts, etc.)\n- Data files (.csv, .json, .yaml)\n- Configuration files\n\n## Workspace Structure\n\nAgents read from their workspace which includes:\n- `SOUL.md` — Agent identity and rules\n- `MEMORY.md` — Cross-session memory\n- `USER.md` — Employee preferences\n- `output/` — Generated files from skills\n- `knowledge/` — Assigned knowledge base documents',
    positions: 'ALL positions',
  },
  'file_write': {
    description: 'Create and write files in the agent workspace. This enables agents to generate code, documents, reports, and other files. Output files are automatically synced to S3 and visible in the employee Portal under "My Workspace".\n\nFile write is restricted to technical and management positions. Finance and Legal positions do not have this tool — their file output comes through specific skills like excel-gen.',
    guide: '## Security Note\n\nFile write access means the agent can create any file in its workspace. Files in `workspace/output/` are automatically synced to S3 for the employee to download.\n\n## Manage Access\n\nTool permissions are configured in **Security Center → Security Policies** per position.\n\nTo enable/disable for a position, go to Security Center and edit the position\'s tool whitelist.',
    positions: 'SDE, DevOps, QA, Executive, SA',
  },
  'shell': {
    description: 'Execute shell commands on the agent microVM. This is the most powerful tool — it gives agents access to the full Linux command line including git, npm, python, docker, aws cli, and any installed utilities.\n\nShell access is restricted to engineering and management positions. It is the primary differentiator between basic and advanced agent profiles.',
    guide: '## Security Implications\n\nShell access allows the agent to:\n- Run any Linux command\n- Install packages (pip, npm)\n- Execute scripts\n- Access network services\n- Interact with AWS via CLI\n\n## IAM Boundary\n\nThe agent runs with the AgentCore execution role. Even with shell access, it cannot:\n- Access resources outside its IAM role scope\n- Reach other tenants\' data\n- Modify infrastructure\n\nThis is **Layer 3 (IAM) security** — cannot be bypassed by prompt injection.\n\n## Manage Access\n\nSecurity Center → Security Policies → Position tool whitelist.',
    positions: 'SDE, DevOps, QA, Executive, SA',
  },
  'browser': {
    description: 'Headless web browser for web scraping, form interaction, and JavaScript-heavy pages. Unlike web_search which returns text snippets, browser gives agents full DOM access to interact with web applications.',
    guide: '## Use Cases\n\n- Scrape data from web applications\n- Fill out forms programmatically\n- Take screenshots of web pages\n- Interact with JavaScript-heavy SPAs\n\n## Limitations\n\n- Runs headless (no visual output)\n- Subject to website rate limits and bot detection\n- Cannot access authenticated sessions (no cookie persistence across sessions)',
    positions: 'SDE, DevOps, SA',
  },
  'code_execution': {
    description: 'Run Python and Node.js code in a sandboxed environment within the agent microVM. This enables agents to perform data analysis, run calculations, prototype solutions, and execute test scripts.',
    guide: '## Supported Languages\n\n- **Python 3.12** — full standard library + pip packages\n- **Node.js 24.16.0** — full runtime + npm packages\n\n## Pre-installed Packages\n\n- Python: boto3, requests, pandas, numpy, openpyxl\n- Node.js: aws-sdk, axios\n\n## Security\n\nCode runs inside the agent microVM with the same IAM role. Network access is available for API calls. Filesystem access is scoped to the workspace directory.',
    positions: 'SDE, DevOps, QA, Executive, SA',
  },
};

const SKILL_GUIDES: Record<string, { description: string; guide: string }> = {
  'excel-gen': {
    description: 'Generate professional Excel spreadsheets from structured data. Supports multiple sheets, formatted headers (blue with white text), alternating row colors, auto-filter, frozen headers, and configurable column widths.\n\nOutput files are saved to `workspace/output/` and automatically synced to S3. Employees can download them from the Portal "My Workspace" page.\n\n## What it can do\n\n- Multi-sheet workbooks with different data sets\n- Formatted headers with corporate styling\n- Auto-calculated column widths\n- Frozen header rows for easy scrolling\n- Data validation and auto-filters\n\n## Example prompts\n\n- "Generate a Q2 budget comparison spreadsheet with columns for Department, Budget, Actual, Variance"\n- "Create an Excel report of all employees by department with their agent status"\n- "Build a financial model with Revenue, Costs, and Profit sheets"',
    guide: '## No Setup Required\n\nThis skill works out of the box. No API keys or configuration needed.\n\n## How it works\n\n1. You ask your agent to create a spreadsheet\n2. Agent structures the data and calls excel-gen\n3. Excel file is generated in `workspace/output/`\n4. Watchdog syncs the file to S3 within 60 seconds\n5. You can download it from Portal → My Workspace → Output\n\n## Technical Details\n\n- Uses Python `openpyxl` library\n- Output format: `.xlsx` (Excel 2007+)\n- Max rows: limited by workspace memory (~100K rows)\n- Styling: corporate blue headers, alternating row shading',
  },
  'aws-nova-canvas': {
    description: 'Generate and edit images using Amazon Nova Canvas via Bedrock. Supports text-to-image generation with customizable dimensions, quality settings, and negative prompts.\n\nImages are saved to `workspace/output/` and synced to S3. Useful for creating diagrams, illustrations, marketing materials, and visual aids.\n\n## Capabilities\n\n- Text-to-image generation (1024x1024 default)\n- Configurable dimensions (512-2048px)\n- Standard and premium quality modes\n- Negative prompts to exclude unwanted elements\n\n## Example prompts\n\n- "Create a diagram showing our 3-tier architecture with load balancer, app servers, and database"\n- "Generate a professional illustration of a team collaboration workspace"\n- "Draw a flowchart of the employee onboarding process"',
    guide: '## No Setup Required\n\nThis skill uses Amazon Bedrock Nova Canvas via the IAM role. No API keys needed.\n\n## How it works\n\n1. You describe the image you want\n2. Agent calls Nova Canvas via Bedrock API\n3. Image is generated and saved as PNG in `workspace/output/`\n4. Synced to S3 for download\n\n## Model\n\n`amazon.nova-canvas-v1:0` — Amazon\'s image generation model.\n\n## Cost\n\nBedrock standard pricing applies (~$0.04 per image at 1024x1024).',
  },
  'aws-s3-docs': {
    description: 'Save, retrieve, list, and share documents via Amazon S3. Create documents in your agent\'s dedicated S3 space, generate pre-signed URLs for secure sharing with colleagues, and manage your document library.\n\n## Actions\n\n- **Save**: Write a document to S3\n- **List**: Browse your saved documents\n- **Get**: Retrieve a document\'s content\n- **Share**: Generate a time-limited download link\n- **Delete**: Remove a document\n\n## Example prompts\n\n- "Save this architecture proposal as a document and give me a share link"\n- "List all my saved documents"\n- "Share the Q2 report with a link that expires in 24 hours"',
    guide: '## No Setup Required\n\nUses the IAM role for S3 access. Documents are stored in your tenant\'s S3 space.\n\n## Storage Path\n\nDocuments are saved to: `s3://{bucket}/{employee_id}/docs/`\n\nThis is separate from the workspace — documents here are permanent and not affected by workspace cleanup.\n\n## Share Links\n\nPre-signed URLs expire after the specified time (default: 1 hour). Anyone with the link can download — no authentication required.',
  },
  'aws-transcribe-notes': {
    description: 'Transcribe meeting recordings using Amazon Transcribe, then generate structured meeting notes with action items, decisions, and key discussion points.\n\nSupports audio files uploaded to the workspace. The transcription is processed by Amazon Transcribe, then the agent synthesizes structured notes.\n\n## Example prompts\n\n- "Transcribe this meeting recording and create meeting notes"\n- "Generate action items from the engineering standup recording"\n- "Create a summary of the Q2 planning session recording"',
    guide: '## No Setup Required\n\nUses IAM role for Amazon Transcribe access.\n\n## Supported Formats\n\nMP3, MP4, WAV, FLAC, OGG, AMR, WebM\n\n## How it works\n\n1. Upload audio file to your workspace\n2. Ask agent to transcribe and summarize\n3. Agent calls Amazon Transcribe → gets raw transcript\n4. Agent synthesizes structured notes with sections',
  },
  'aws-bedrock-kb-search': {
    description: 'Search enterprise knowledge bases using Amazon Bedrock Knowledge Bases (RAG). Perform semantic search across indexed documents with source attribution.\n\nThis skill connects to a pre-configured Bedrock Knowledge Base and retrieves the most relevant passages for the agent\'s query. Results include the source document and relevance score.\n\n## Example prompts\n\n- "Search our knowledge base for the refund policy"\n- "Find architecture standards related to database design"\n- "What does our security baseline say about encryption?"',
    guide: '## Platform Key Required\n\nThis skill requires the Admin to configure the Bedrock Knowledge Base ID.\n\n### For Admin\n\n1. Create a Bedrock Knowledge Base in the AWS Console\n2. Index your documents (S3 data source)\n3. Copy the Knowledge Base ID\n4. Go to **Tools & Skills → API Keys tab**\n5. Configure `BEDROCK_KB_ID` with the Knowledge Base ID\n\n### For Employees\n\nOnce configured by Admin, just ask your agent to search. No personal setup needed.\n\n## How it works\n\n1. Agent formulates a search query from your question\n2. Calls Bedrock Knowledge Base retrieve API\n3. Gets top-N relevant passages with source attribution\n4. Synthesizes answer citing the sources',
  },
};

export default function ToolsSkillsDetail() {
  const { itemId } = useParams<{ itemId: string }>();
  const navigate = useNavigate();
  const { data: skills = [] } = useSkills();
  const { data: apiKeys = [] } = useSkillKeys();
  const { data: positions = [] } = usePositions();
  const assignSkill = useAssignSkill();
  const unassignSkill = useUnassignSkill();
  const [activeTab, setActiveTab] = useState('description');
  const [assignPos, setAssignPos] = useState('');
  const [assignResult, setAssignResult] = useState('');

  const isTool = itemId?.startsWith('tool-') || false;
  const toolName = isTool ? (itemId || '').replace('tool-', '') : '';
  const skill = isTool ? null : skills.find(s => s.id === itemId || s.name === itemId || `sk-${s.name}` === itemId);
  const name = isTool ? toolName : (skill?.name || itemId || '');
  const details = isTool ? TOOL_DETAILS[toolName] : SKILL_GUIDES[name];

  if (!isTool && !skill && skills.length > 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <p className="text-lg text-text-muted mb-4">Skill not found: {itemId}</p>
        <Button variant="primary" onClick={() => navigate('/skills')}>Back to Tools & Skills</Button>
      </div>
    );
  }

  const assignedPositions = positions.filter(p =>
    isTool ? (p.toolAllowlist || []).includes(name) : (p.defaultSkills || []).includes(name)
  );

  const envs = skill?.requires?.env || [];
  const keyStatuses = envs.map(env => {
    const k = apiKeys.find(ak => ak.skillName === name && ak.envVar === env);
    return { env, status: k?.status || 'not-configured', note: k?.note || '' };
  });

  const ext = (skill || {}) as any;
  const tags: string[] = [];
  if (isTool) {
    tags.push('Tool', 'Platform', 'Built-in', 'Zero Config', 'Security Approved');
  } else {
    tags.push('Platform', 'Security Approved');
    if (envs.length === 0) tags.push('Zero Config');
    else if (ext.awsService) tags.push('Platform Key');
    else tags.push('Personal Key');
    if (skill?.layer === 1) tags.push('Built-in');
    else if (skill?.layer === 2) tags.push('S3 Loaded');
    if (ext.awsService) tags.push('AWS Native');
  }

  const TAG_COLORS: Record<string, string> = {
    'Platform': 'bg-primary/10 text-primary',
    'Tool': 'bg-surface-container-highest/60 text-text-secondary',
    'Built-in': 'bg-success/10 text-success',
    'S3 Loaded': 'bg-primary/10 text-primary',
    'Zero Config': 'bg-success/10 text-success',
    'Platform Key': 'bg-info/10 text-info',
    'Personal Key': 'bg-warning/10 text-warning',
    'Security Approved': 'bg-success/10 text-success',
    'AWS Native': 'bg-warning/10 text-warning',
  };

  const handleAssign = () => {
    if (!assignPos || isTool) return;
    assignSkill.mutate({ skillName: name, positionId: assignPos }, {
      onSuccess: (data) => {
        const posName = positions.find(p => p.id === assignPos)?.name || assignPos;
        setAssignResult(`Assigned to ${posName}`);
        setAssignPos('');
      },
    });
  };

  return (
    <div>
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm mb-4">
        <button onClick={() => navigate('/skills')} className="text-primary-light hover:underline">Tools & Skills</button>
        <ChevronRight size={14} className="text-text-muted" />
        <span className="text-text-primary font-medium">{name}</span>
      </div>

      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-text-primary mb-2">{name}</h1>
          <div className="flex flex-wrap gap-1.5 mb-2">
            {tags.map(t => (
              <span key={t} className={`text-xs px-2 py-0.5 rounded-full font-medium ${TAG_COLORS[t] || 'bg-surface-container-highest/60 text-text-secondary'}`}>{t}</span>
            ))}
          </div>
          {!isTool && skill && (
            <p className="text-xs text-text-muted">v{skill.version} · {skill.author} · {skill.category}</p>
          )}
        </div>
        <Button variant="default" onClick={() => navigate('/skills')}><ArrowLeft size={16} /> Back</Button>
      </div>

      {/* Tabs */}
      <Tabs
        tabs={[
          { id: 'description', label: 'Description' },
          { id: 'configuration', label: isTool ? 'Positions' : 'Configuration' },
          { id: 'details', label: 'Details' },
        ]}
        activeTab={activeTab}
        onChange={setActiveTab}
      />

      <div className="mt-6">
        {/* ── Description Tab ── */}
        {activeTab === 'description' && (
          <Card>
            <div className="prose prose-invert prose-sm max-w-none [&_h2]:text-lg [&_h2]:font-semibold [&_h2]:text-text-primary [&_h2]:mt-6 [&_h2]:mb-3 [&_h3]:text-base [&_h3]:font-medium [&_p]:text-text-secondary [&_li]:text-text-secondary [&_code]:bg-dark-bg [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded [&_pre]:bg-dark-bg [&_pre]:p-4 [&_pre]:rounded-xl [&_strong]:text-text-primary">
              <h2>Description</h2>
              <ReactMarkdown>{details?.description || skill?.description || 'No description available.'}</ReactMarkdown>

              {details?.guide && (
                <>
                  <h2>Setup Guide</h2>
                  <ReactMarkdown>{details.guide}</ReactMarkdown>
                </>
              )}
            </div>
          </Card>
        )}

        {/* ── Configuration / Positions Tab ── */}
        {activeTab === 'configuration' && (
          <div className="space-y-6">
            {/* Assigned positions */}
            <Card>
              <h3 className="text-sm font-semibold text-text-primary mb-3">
                {isTool ? 'Enabled for Positions' : 'Assigned to Positions'}
              </h3>
              {isTool && (
                <p className="text-xs text-text-muted mb-3">
                  Tool permissions are managed in Security Center → Security Policies. {(details as any)?.positions || ''}
                </p>
              )}
              {assignedPositions.length > 0 ? (
                <div className="flex flex-wrap gap-2 mb-4">
                  {assignedPositions.map(p => (
                    <div key={p.id} className="flex items-center gap-1.5 rounded-lg bg-success/10 border border-success/20 px-3 py-1.5">
                      <CheckCircle size={13} className="text-success" />
                      <span className="text-sm font-medium text-text-primary">{p.name}</span>
                      <span className="text-xs text-text-muted">{p.departmentName}</span>
                      {!isTool && (
                        <button onClick={() => unassignSkill.mutate({ skillName: name, positionId: p.id })}
                          className="text-text-muted hover:text-danger ml-1"><X size={12} /></button>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-text-muted mb-4">Not assigned to any position yet.</p>
              )}

              {/* Assign (skills only) */}
              {!isTool && (
                <div className="flex items-end gap-3 pt-3 border-t border-dark-border/30">
                  <div className="flex-1 max-w-sm">
                    <Select label="Assign to position" value={assignPos} onChange={setAssignPos}
                      options={positions.filter(p => !(p.defaultSkills || []).includes(name)).map(p => ({ label: `${p.name} (${p.departmentName})`, value: p.id }))}
                      placeholder="Select position..." />
                  </div>
                  <Button variant="primary" disabled={!assignPos || assignSkill.isPending} onClick={handleAssign}>
                    <Zap size={14} /> {assignSkill.isPending ? 'Assigning...' : 'Assign'}
                  </Button>
                </div>
              )}
              {assignResult && (
                <div className="mt-3 rounded-lg bg-success/10 border border-success/20 px-3 py-2 text-xs text-success">
                  <CheckCircle size={12} className="inline mr-1" /> {assignResult}
                </div>
              )}
            </Card>

            {/* Prerequisites (skills only) */}
            {!isTool && envs.length > 0 && (
              <Card>
                <h3 className="text-sm font-semibold text-text-primary mb-3">API Key Prerequisites</h3>
                <div className="space-y-2">
                  {keyStatuses.map(k => (
                    <div key={k.env} className="flex items-center justify-between rounded-lg bg-surface-dim px-4 py-3">
                      <div className="flex items-center gap-3">
                        <Key size={14} className={k.status === 'not-configured' ? 'text-warning' : 'text-success'} />
                        <div>
                          <code className="text-sm text-primary-light">{k.env}</code>
                          <p className="text-xs text-text-muted">{k.note || 'Configure in API Keys tab'}</p>
                        </div>
                      </div>
                      <Badge color={k.status === 'iam-role' ? 'success' : k.status === 'active' ? 'success' : 'danger'} dot>
                        {k.status === 'iam-role' ? 'IAM Role' : k.status === 'active' ? 'Configured' : 'Not Configured'}
                      </Badge>
                    </div>
                  ))}
                </div>
              </Card>
            )}

            {/* Tool dependencies */}
            {!isTool && ((skill as any)?.requires?.tools || []).length > 0 && (
              <Card>
                <h3 className="text-sm font-semibold text-text-primary mb-3">Required Tools</h3>
                <p className="text-xs text-text-muted mb-3">This skill requires the following tools to be enabled for the position:</p>
                <div className="flex flex-wrap gap-2">
                  {((skill as any)?.requires?.tools || []).map((t: string) => (
                    <Badge key={t} color="warning">{t}</Badge>
                  ))}
                </div>
              </Card>
            )}
          </div>
        )}

        {/* ── Details Tab ── */}
        {activeTab === 'details' && (
          <div className="space-y-6">
            <Card>
              <h3 className="text-sm font-semibold text-text-primary mb-3">Security</h3>
              <div className="flex items-center gap-3 rounded-lg bg-success/5 border border-success/20 px-4 py-3">
                <Shield size={18} className="text-success" />
                <div>
                  <p className="text-sm font-medium text-success">Security Approved</p>
                  <p className="text-xs text-text-muted">
                    {isTool ? 'Built-in tool. Part of the OpenClaw runtime.' : 'Platform-provided skill. Pre-vetted by the platform team.'}
                  </p>
                </div>
              </div>
            </Card>

            <Card>
              <h3 className="text-sm font-semibold text-text-primary mb-3">Metadata</h3>
              <div className="space-y-2">
                {[
                  { label: 'Type', value: isTool ? 'Built-in Tool' : 'Extension Skill' },
                  { label: 'Name', value: name },
                  ...(skill ? [
                    { label: 'Version', value: skill.version },
                    { label: 'Author', value: skill.author },
                    { label: 'Category', value: skill.category },
                    { label: 'Scope', value: skill.scope },
                    { label: 'Layer', value: `Layer ${skill.layer} (${skill.layer === 1 ? 'Docker built-in' : 'S3 hot-loaded'})` },
                  ] : []),
                  { label: 'Status', value: 'Installed' },
                ].map(row => (
                  <div key={row.label} className="flex items-center justify-between rounded-lg bg-surface-dim px-4 py-2.5">
                    <span className="text-xs text-text-muted">{row.label}</span>
                    <span className="text-sm text-text-primary">{row.value}</span>
                  </div>
                ))}
              </div>
            </Card>

            {!isTool && ext.awsService && (
              <Card>
                <h3 className="text-sm font-semibold text-text-primary mb-3">AWS Service</h3>
                <div className="rounded-lg bg-warning/5 border border-warning/20 px-4 py-3 flex items-center gap-3">
                  <span className="text-warning text-lg">AWS</span>
                  <div>
                    <p className="text-sm font-medium text-text-primary">{ext.awsService}</p>
                    <p className="text-xs text-text-muted">Access provided via IAM execution role on the AgentCore runtime.</p>
                  </div>
                </div>
              </Card>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
