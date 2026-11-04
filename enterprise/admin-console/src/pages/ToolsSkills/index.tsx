import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Puzzle, Search, Key, Package, Cloud, CheckCircle, Lock, Shield,
  ArrowRight, AlertTriangle, Zap, X, Wrench, Globe, User, Eye,
} from 'lucide-react';
import { Card, StatCard, Badge, Button, PageHeader, Modal, Select, Tabs, Input } from '../../components/ui';
import { useSkills, useSkillKeys, useSetSkillKey, usePositions, useAssignSkill, useUnassignSkill, usePendingSkills, useReviewSkill } from '../../hooks/useApi';
import type { SkillManifest, SkillApiKey } from '../../hooks/useApi';

// ── Tag colors ────────────────────────────────────────────────────────────
const TAG_COLORS: Record<string, 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'default'> = {
  'Platform': 'primary',
  'Community': 'info',
  'Zero Config': 'success',
  'Personal Key': 'warning',
  'Platform Key': 'info',
  'Security Approved': 'success',
  'Under Review': 'warning',
  'Built-in': 'success',
  'S3 Loaded': 'primary',
  'AWS Native': 'warning',
  'Tool': 'default',
};

const CATEGORY_ICONS: Record<string, string> = {
  information: '🔍', communication: '📧', productivity: '⚡', 'project-management': '📋',
  crm: '💼', erp: '🏦', development: '💻', data: '📊', creative: '🎨',
  security: '🛡', memory: '🧠', tool: '🔧',
};

// ── Built-in Tools as data ────────────────────────────────────────────────
const BUILT_IN_TOOLS: SkillManifest[] = [
  { id: 'tool-web-search', name: 'web_search', version: '—', description: 'Search the web. Always enabled for all positions.', author: 'OpenClaw Core', layer: 0 as any, category: 'tool', scope: 'global', requires: { env: [], tools: [] }, permissions: { allowedRoles: ['*'], blockedRoles: [] } },
  { id: 'tool-file', name: 'file', version: '—', description: 'Read files from agent workspace.', author: 'OpenClaw Core', layer: 0 as any, category: 'tool', scope: 'global', requires: { env: [], tools: [] }, permissions: { allowedRoles: ['*'], blockedRoles: [] } },
  { id: 'tool-file-write', name: 'file_write', version: '—', description: 'Create and write files in agent workspace. Output goes to workspace/output/ directory.', author: 'OpenClaw Core', layer: 0 as any, category: 'tool', scope: 'global', requires: { env: [], tools: [] }, permissions: { allowedRoles: ['engineering', 'devops', 'qa', 'management'], blockedRoles: [] } },
  { id: 'tool-shell', name: 'shell', version: '—', description: 'Execute shell commands on the agent microVM.', author: 'OpenClaw Core', layer: 0 as any, category: 'tool', scope: 'global', requires: { env: [], tools: [] }, permissions: { allowedRoles: ['engineering', 'devops', 'qa', 'management'], blockedRoles: [] } },
  { id: 'tool-browser', name: 'browser', version: '—', description: 'Headless web browser for web interaction.', author: 'OpenClaw Core', layer: 0 as any, category: 'tool', scope: 'global', requires: { env: [], tools: [] }, permissions: { allowedRoles: ['engineering', 'devops', 'management'], blockedRoles: [] } },
  { id: 'tool-code-execution', name: 'code_execution', version: '—', description: 'Run Python/Node.js code in sandboxed environment.', author: 'OpenClaw Core', layer: 0 as any, category: 'tool', scope: 'global', requires: { env: [], tools: [] }, permissions: { allowedRoles: ['engineering', 'devops', 'qa', 'management'], blockedRoles: [] } },
];

function getTags(item: SkillManifest, apiKeys: SkillApiKey[]): string[] {
  const tags: string[] = [];
  const isTool = item.id?.startsWith('tool-');
  const ext = item as any; // extended fields not in base type

  if (isTool) {
    tags.push('Tool', 'Platform', 'Built-in', 'Zero Config', 'Security Approved');
    return tags;
  }

  tags.push(ext.submittedBy ? 'Community' : 'Platform');

  const envs = item.requires?.env || [];
  if (envs.length === 0) {
    tags.push('Zero Config');
  } else if (ext.awsService) {
    tags.push('Platform Key');
  } else {
    tags.push('Personal Key');
  }

  tags.push('Security Approved');

  if (item.layer === 1) tags.push('Built-in');
  else if (item.layer === 2) tags.push('S3 Loaded');
  if (ext.awsService) tags.push('AWS Native');

  return tags;
}

function getStatus(item: SkillManifest): 'official' | 'approved' | 'under_review' | 'community' {
  const ext = item as any;
  if (item.id?.startsWith('tool-')) return 'official';
  if (item.status === 'under_review') return 'under_review';
  if (ext.submittedBy) return ext.securityScan ? 'approved' : 'community';
  return 'official';
}

// ── Detail Modal ──────────────────────────────────────────────────────────
function DetailModal({ item, apiKeys, onClose }: { item: SkillManifest; apiKeys: SkillApiKey[]; onClose: () => void }) {
  const { data: positions = [] } = usePositions();
  const assignSkill = useAssignSkill();
  const unassignSkill = useUnassignSkill();
  const [assignPos, setAssignPos] = useState('');
  const [assignResult, setAssignResult] = useState<string | null>(null);

  const isTool = item.id?.startsWith('tool-');
  const skillName = item.name || item.id?.replace('sk-', '') || '';
  const tags = getTags(item, apiKeys);

  const assignedPositions = positions.filter(p =>
    isTool
      ? (p.toolAllowlist || []).includes(skillName)
      : (p.defaultSkills || []).includes(skillName)
  );

  const requiredEnvs = item.requires?.env || [];
  const keyStatuses = requiredEnvs.map(env => {
    const key = apiKeys.find(k => k.skillName === skillName && k.envVar === env);
    return { env, status: key?.status || 'not-configured', note: key?.note || '' };
  });
  const allKeysReady = requiredEnvs.length === 0 || keyStatuses.every(k => k.status === 'iam-role' || k.status === 'active');

  const unassignedPositions = positions.filter(p =>
    !(p.defaultSkills || []).includes(skillName)
  );

  const handleAssign = () => {
    if (!assignPos) return;
    assignSkill.mutate({ skillName, positionId: assignPos }, {
      onSuccess: (data) => {
        const posName = positions.find(p => p.id === assignPos)?.name || assignPos;
        setAssignResult(`Assigned to ${posName} — ${(data as any).agentsPropagated || (data as any).agentsAffected || 0} agent(s) affected`);
        setAssignPos('');
      },
    });
  };

  return (
    <Modal open={true} onClose={onClose} title={item.name || skillName} size="lg">
      <div className="space-y-5">
        {/* Header */}
        <div className="flex items-start gap-4">
          <span className="text-3xl">{CATEGORY_ICONS[item.category] || '🧩'}</span>
          <div className="flex-1">
            <p className="text-sm text-text-secondary mb-2">{item.description}</p>
            <div className="flex flex-wrap gap-1.5">
              {tags.map(t => <Badge key={t} color={TAG_COLORS[t] || 'default'}>{t}</Badge>)}
              <span className="text-xs text-text-muted ml-1">v{item.version} · {item.author}</span>
            </div>
          </div>
        </div>

        {/* Setup Guide */}
        {isTool ? (
          <div className="rounded-xl border border-dark-border/50 bg-surface-dim px-4 py-3">
            <p className="text-sm font-medium text-text-primary mb-1">Configuration</p>
            <p className="text-xs text-text-muted">
              Tool permissions are managed in <strong>Security Center → Security Policies</strong>. Each position has a tool whitelist (Plan A).
            </p>
          </div>
        ) : (
          <>
            {/* Prerequisites */}
            <div className="rounded-xl border border-dark-border/50 bg-surface-dim px-4 py-3">
              <div className="flex items-center gap-2 mb-2">
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary/20 text-primary text-xs font-bold">1</span>
                <p className="text-sm font-medium text-text-primary">Prerequisites</p>
                {allKeysReady ? <Badge color="success" dot>Ready</Badge> : <Badge color="warning" dot>Action needed</Badge>}
              </div>
              {requiredEnvs.length === 0 ? (
                <p className="text-xs text-text-muted ml-7">No API keys needed. Ready to use.</p>
              ) : (
                <div className="ml-7 space-y-1.5">
                  {keyStatuses.map(k => (
                    <div key={k.env} className="flex items-center justify-between rounded-lg bg-dark-bg px-3 py-2">
                      <code className="text-xs text-primary-light">{k.env}</code>
                      <Badge color={k.status === 'iam-role' ? 'success' : k.status === 'active' ? 'success' : 'danger'} dot>
                        {k.status === 'iam-role' ? 'IAM Role' : k.status === 'active' ? 'Configured' : 'Not Configured'}
                      </Badge>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Assign */}
            <div className="rounded-xl border border-dark-border/50 bg-surface-dim px-4 py-3">
              <div className="flex items-center gap-2 mb-2">
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary/20 text-primary text-xs font-bold">2</span>
                <p className="text-sm font-medium text-text-primary">Assign to Position</p>
              </div>
              {assignedPositions.length > 0 && (
                <div className="ml-7 mb-3">
                  <p className="text-xs text-text-muted mb-1.5">Currently assigned:</p>
                  <div className="flex flex-wrap gap-2">
                    {assignedPositions.map(p => (
                      <div key={p.id} className="flex items-center gap-1.5 rounded-lg bg-success/10 border border-success/20 px-2.5 py-1">
                        <CheckCircle size={12} className="text-success" />
                        <span className="text-xs font-medium text-text-primary">{p.name}</span>
                        {!isTool && (
                          <button onClick={() => unassignSkill.mutate({ skillName, positionId: p.id })} className="text-text-muted hover:text-danger ml-1"><X size={11} /></button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {assignResult && (
                <div className="ml-7 mb-3 rounded-lg bg-success/10 border border-success/20 px-3 py-2 text-xs text-success">
                  <CheckCircle size={12} className="inline mr-1" /> {assignResult}
                </div>
              )}
              {!isTool && (
                <div className="ml-7 flex items-end gap-2">
                  <div className="flex-1">
                    <Select label="" value={assignPos} onChange={setAssignPos}
                      options={unassignedPositions.map(p => ({ label: `${p.name} (${p.departmentName})`, value: p.id }))}
                      placeholder="Select position..." />
                  </div>
                  <Button variant="primary" size="sm" disabled={!assignPos || !allKeysReady || assignSkill.isPending} onClick={handleAssign}>
                    <Zap size={12} /> {assignSkill.isPending ? 'Assigning...' : 'Assign'}
                  </Button>
                </div>
              )}
            </div>

            {/* Security */}
            <div className="rounded-xl border border-dark-border/50 bg-surface-dim px-4 py-3">
              <div className="flex items-center gap-2 mb-1">
                <Shield size={14} className="text-success" />
                <p className="text-sm font-medium text-text-primary">Security</p>
                <Badge color="success" dot>Approved</Badge>
              </div>
              <p className="text-xs text-text-muted ml-6">Platform-provided skill. Pre-vetted by the platform team.</p>
            </div>
          </>
        )}

        {/* Tool dependencies */}
        {(item.requires?.tools || []).length > 0 && (
          <div className="rounded-lg bg-warning/5 border border-warning/20 px-3 py-2 text-xs text-text-muted">
            <strong>Requires tools:</strong> {item.requires.tools.join(', ')} — position must have these tools enabled in Security Center.
          </div>
        )}
      </div>
    </Modal>
  );
}

// ── Skill Card (MCP Registry style) ───────────────────────────────────────
function SkillCard({ item, apiKeys, onClick }: { item: SkillManifest; apiKeys: SkillApiKey[]; onClick: () => void }) {
  const tags = getTags(item, apiKeys);
  const isTool = item.id?.startsWith('tool-');
  const ext = item as any;

  return (
    <div onClick={onClick} className="cursor-pointer rounded-xl border border-dark-border/60 bg-dark-card px-5 py-4 hover:border-primary/50 transition-all group flex flex-col">
      {/* Title */}
      <h3 className="text-base font-bold text-primary-light group-hover:underline mb-2">{item.name}</h3>

      {/* Description — 4 lines max */}
      <p className="text-sm text-text-secondary leading-relaxed line-clamp-4 mb-3 flex-1">{item.description}</p>

      {/* Team / Category */}
      <p className="text-xs text-primary-light mb-3">
        {isTool ? 'OpenClaw / Built-in Tools' : `${item.author} / ${item.category}`}
      </p>

      {/* Tags */}
      <div className="flex flex-wrap gap-1.5">
        {tags.slice(0, 3).map(t => (
          <span key={t} className={`text-[11px] px-2 py-0.5 rounded font-medium ${
            t === 'Built-in' || t === 'Security Approved' || t === 'Zero Config' ? 'bg-success/20 text-success' :
            t === 'Platform' || t === 'S3 Loaded' ? 'bg-primary/20 text-primary-light' :
            t === 'Platform Key' || t === 'AWS Native' ? 'bg-warning/20 text-warning' :
            t === 'Tool' ? 'bg-info/20 text-info' :
            t === 'Under Review' ? 'bg-amber-500/20 text-amber-400' :
            t === 'Community' ? 'bg-purple-500/20 text-purple-400' :
            'bg-surface-container-highest/60 text-text-secondary'
          }`}>{t}</span>
        ))}
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────
export default function ToolsSkills() {
  const { data: skills = [] } = useSkills();
  const { data: apiKeys = [] } = useSkillKeys();
  const setSkillKey = useSetSkillKey();
  const { data: positions = [] } = usePositions();
  const { data: pendingSkills = [] } = usePendingSkills();
  const reviewSkill = useReviewSkill();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState('tools');
  const [skillFilter, setSkillFilter] = useState<'all' | 'official' | 'approved' | 'under_review' | 'community'>('all');
  const [filterText, setFilterText] = useState('');
  const [keyTarget, setKeyTarget] = useState<SkillApiKey | null>(null);
  const [keyValue, setKeyValue] = useState('');
  const [keyError, setKeyError] = useState('');

  const toolCount = BUILT_IN_TOOLS.length;
  const skillCount = skills.length;
  const assignedCount = skills.filter(s => {
    const name = s.name || s.id?.replace('sk-', '') || '';
    return positions.some(p => (p.defaultSkills || []).includes(name));
  }).length;
  const keysNeeded = apiKeys.filter(k => k.status === 'not-configured').length;

  // Filter skills by lifecycle status
  const filteredSkills = skills.filter(s => {
    const status = getStatus(s);
    if (skillFilter !== 'all' && status !== skillFilter) return false;
    if (!filterText) return true;
    const q = filterText.toLowerCase();
    return (s.name || '').toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q);
  });

  // Filter tools by search
  const filteredTools = BUILT_IN_TOOLS.filter(t => {
    if (!filterText) return true;
    const q = filterText.toLowerCase();
    return t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q);
  });

  return (
    <div>
      <PageHeader
        title="Tools & Skills"
        description={`${toolCount} built-in tools + ${skillCount} skills · ${assignedCount} assigned to positions`}
      />

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 mb-6">
        <StatCard title="Built-in Tools" value={toolCount} icon={<Wrench size={22} />} color="primary" subtitle="Plan A managed" />
        <StatCard title="Skills" value={skillCount} icon={<Puzzle size={22} />} color="success" subtitle={`${assignedCount} assigned`} />
        <StatCard title="API Keys" value={apiKeys.length} icon={<Key size={22} />} color={keysNeeded > 0 ? 'warning' : 'success'} subtitle={keysNeeded > 0 ? `${keysNeeded} need config` : 'All configured'} />
        <StatCard title="Security" value="Approved" icon={<Shield size={22} />} color="success" subtitle="All platform skills vetted" />
      </div>

      <Card>
        <Tabs
          tabs={[
            { id: 'tools', label: 'Tools', count: toolCount },
            { id: 'skills', label: 'Skills', count: skillCount },
            { id: 'review', label: 'Under Review', count: pendingSkills.length || undefined },
            { id: 'keys', label: 'API Keys', count: keysNeeded || undefined },
          ]}
          activeTab={activeTab}
          onChange={setActiveTab}
        />

        {/* ── Tools Tab ── */}
        {activeTab === 'tools' && (
          <div className="mt-4">
            <p className="text-sm text-text-secondary mb-4">
              Built-in agent capabilities managed by <strong>Security Center → Plan A</strong>. Each position has a tool whitelist — tools not in the whitelist are blocked at runtime.
            </p>
            <div className="mb-4">
              <div className="relative max-w-md">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
                <input type="text" value={filterText} onChange={e => setFilterText(e.target.value)}
                  placeholder="Search tools..."
                  className="w-full rounded-lg border border-dark-border bg-dark-bg py-2 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none" />
              </div>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {filteredTools.map(item => (
                <SkillCard key={item.id} item={item} apiKeys={apiKeys} onClick={() => navigate(`/skills/${item.id || item.name}`)} />
              ))}
            </div>
          </div>
        )}

        {/* ── Skills Tab ── */}
        {activeTab === 'skills' && (
          <div className="mt-4">
            <div className="flex flex-wrap items-center gap-3 mb-4">
              <div className="relative flex-1 max-w-md">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
                <input type="text" value={filterText} onChange={e => setFilterText(e.target.value)}
                  placeholder="Search skills..."
                  className="w-full rounded-lg border border-dark-border bg-dark-bg py-2 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none" />
              </div>
              <div className="flex rounded-lg border border-dark-border overflow-hidden">
                {([
                  { label: 'All', value: 'all' as const },
                  { label: 'Official', value: 'official' as const },
                  { label: 'Approved', value: 'approved' as const },
                  { label: 'Under Review', value: 'under_review' as const },
                  { label: 'Community', value: 'community' as const },
                ] as const).map(f => (
                  <button key={f.value} onClick={() => setSkillFilter(f.value)}
                    className={`px-3 py-1.5 text-xs font-medium transition-colors ${
                      skillFilter === f.value ? 'bg-primary text-white' : 'bg-dark-card text-text-muted hover:text-text-primary'
                    }`}>{f.label}</button>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {filteredSkills.map(item => (
                <SkillCard key={item.id || item.name} item={item} apiKeys={apiKeys} onClick={() => navigate(`/skills/${item.id || item.name}`)} />
              ))}
            </div>
            {filteredSkills.length === 0 && (
              <div className="text-center py-12 text-text-muted">
                <Puzzle size={32} className="mx-auto mb-3 opacity-30" />
                <p className="text-sm">{skillFilter === 'all' ? 'No skills found' : `No ${skillFilter.replace('_', ' ')} skills`}</p>
              </div>
            )}
          </div>
        )}

        {/* Under Review Tab */}
        {activeTab === 'review' && (
          <div className="mt-4">
            <p className="text-sm text-text-secondary mb-4">
              Skills submitted by employees awaiting security review and admin approval.
            </p>
            {pendingSkills.length === 0 ? (
              <div className="text-center py-12 text-text-muted">
                <CheckCircle size={32} className="mx-auto mb-3 text-green-400" />
                <p className="text-sm">No skills pending review</p>
              </div>
            ) : (
              <div className="space-y-3">
                {pendingSkills.map((s: any) => (
                  <div key={s.name} className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-5 py-4">
                    <div className="flex items-start justify-between mb-2">
                      <div>
                        <h3 className="text-base font-bold text-text-primary">{s.name}</h3>
                        <p className="text-sm text-text-secondary mt-1">{s.description}</p>
                        <p className="text-xs text-text-muted mt-2">
                          Submitted by: {s.submittedBy || 'unknown'} · {s.submittedAt ? new Date(s.submittedAt).toLocaleDateString() : ''} · {s.category}
                        </p>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        <span className="text-[11px] px-2 py-0.5 rounded font-medium bg-amber-500/20 text-amber-400">Under Review</span>
                        <span className="text-[11px] px-2 py-0.5 rounded font-medium bg-purple-500/20 text-purple-400">Community</span>
                        {s.hasTool && <span className="text-[11px] px-2 py-0.5 rounded font-medium bg-success/20 text-success">Has Code</span>}
                      </div>
                    </div>
                    <div className="flex items-center gap-2 mt-3 pt-3 border-t border-dark-border/30">
                      <Button variant="default" size="sm" onClick={() => navigate(`/skills/pending-${s.name}`)}>
                        <Eye size={13} /> Review Code
                      </Button>
                      <Button variant="primary" size="sm" disabled={reviewSkill.isPending}
                        onClick={() => reviewSkill.mutate({ skillName: s.name, action: 'approve' })}>
                        <CheckCircle size={13} /> Approve
                      </Button>
                      <Button variant="default" size="sm" disabled={reviewSkill.isPending}
                        onClick={() => reviewSkill.mutate({ skillName: s.name, action: 'reject', reason: 'Rejected by admin' })}>
                        <X size={13} /> Reject
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* API Keys Tab */}
        {activeTab === 'keys' && (
          <div className="mt-4">
            <p className="text-sm text-text-secondary mb-4">
              Platform-level API keys for skills that need external service access. Configure once — all employees use the same key.
            </p>
            {apiKeys.length === 0 ? (
              <div className="text-center py-8 text-text-muted">
                <Key size={24} className="mx-auto mb-2 opacity-50" />
                <p className="text-sm">No API keys required. All installed skills use IAM roles or are zero-config.</p>
              </div>
            ) : (
              <div className="space-y-2">
                {apiKeys.map(k => (
                  <div key={k.id} className="flex items-center justify-between rounded-lg bg-surface-dim px-4 py-3">
                    <div className="flex items-center gap-3">
                      <Key size={14} className={k.status === 'iam-role' ? 'text-success' : k.status === 'active' ? 'text-success' : 'text-warning'} />
                      <div>
                        <p className="text-sm font-medium text-text-primary">{k.skillName}</p>
                        <code className="text-xs text-text-muted">{k.envVar}</code>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge color={k.status === 'iam-role' ? 'success' : k.status === 'active' ? 'success' : 'danger'} dot>
                        {k.status === 'iam-role' ? 'IAM Role' : k.status === 'active' ? 'Configured' : 'Not Configured'}
                      </Badge>
                      <span className="text-[10px] text-text-muted">{k.note}</span>
                      {k.status !== 'iam-role' && (
                        <Button size="sm" variant="default" onClick={() => {
                          setKeyTarget(k);
                          setKeyValue('');
                          setKeyError('');
                        }}>
                          {k.status === 'active' ? 'Rotate' : 'Configure'}
                        </Button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </Card>

      <Modal
        open={!!keyTarget}
        onClose={() => { setKeyTarget(null); setKeyValue(''); setKeyError(''); }}
        title={keyTarget?.status === 'active' ? 'Rotate API Key' : 'Configure API Key'}
        size="sm"
        footer={<div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => { setKeyTarget(null); setKeyValue(''); setKeyError(''); }}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!keyValue.trim() || setSkillKey.isPending}
            onClick={() => {
              if (!keyTarget) return;
              setKeyError('');
              setSkillKey.mutate({
                skillName: keyTarget.skillName,
                envVar: keyTarget.envVar,
                value: keyValue,
              }, {
                onSuccess: () => { setKeyTarget(null); setKeyValue(''); },
                onError: (error: any) => setKeyError(error?.message || 'Failed to save API key'),
              });
            }}
          >
            {setSkillKey.isPending ? 'Saving…' : 'Save Securely'}
          </Button>
        </div>}
      >
        {keyTarget && <div className="space-y-4">
          <div className="rounded-lg bg-surface-dim px-3 py-2 text-xs text-text-muted">
            <p><span className="text-text-secondary">Skill:</span> {keyTarget.skillName}</p>
            <p><span className="text-text-secondary">Variable:</span> <code>{keyTarget.envVar}</code></p>
            <p className="mt-1">Stored as an encrypted SSM SecureString. Existing values are never displayed.</p>
          </div>
          <Input
            label="API Key"
            type="password"
            value={keyValue}
            onChange={setKeyValue}
            placeholder="Enter a new value"
          />
          {keyError && <p className="text-sm text-danger">{keyError}</p>}
        </div>}
      </Modal>

      {/* Detail Modal */}    </div>
  );
}
