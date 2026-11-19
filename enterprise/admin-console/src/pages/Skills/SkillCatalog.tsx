import { useState } from 'react';
import {
  Puzzle, Search, Key, Package, Cloud, CheckCircle, Lock,
  ArrowRight, AlertTriangle, Zap, X,
} from 'lucide-react';
import { Card, StatCard, Badge, Button, PageHeader, Table, Modal, Select, Tabs } from '../../components/ui';
import { useSkills, useSkillKeys, usePositions, useAssignSkill, useUnassignSkill } from '../../hooks/useApi';
import type { SkillManifest, SkillApiKey } from '../../hooks/useApi';

const layerColor = (l: number): 'primary' | 'success' | 'info' => l === 1 ? 'primary' : l === 2 ? 'success' : 'info';
const statusColor = (s: string): 'success' | 'warning' | 'danger' | 'info' | 'default' => {
  switch (s) { case 'installed': return 'success'; case 'pending_review': return 'warning'; case 'building': return 'info'; case 'error': return 'danger'; default: return 'default'; }
};
const categoryIcon: Record<string, string> = {
  information: '🔍', communication: '📧', collaboration: '📝', 'project-management': '📋',
  crm: '💼', erp: '🏦', development: '💻', data: '📊', productivity: '⚡', utility: '🔧',
  creative: '🎨',
};

// ─── Skill Detail Modal (redesigned with assign action) ─────────────────────

function SkillDetailModal({ skill, apiKeys, onClose }: {
  skill: SkillManifest; apiKeys: SkillApiKey[]; onClose: () => void;
}) {
  const { data: positions = [] } = usePositions();
  const assignSkill = useAssignSkill();
  const unassignSkill = useUnassignSkill();
  const [assignPos, setAssignPos] = useState('');
  const [assignResult, setAssignResult] = useState<string | null>(null);

  const skillName = skill.name || skill.id?.replace('sk-', '') || '';

  // Which positions already have this skill?
  const assignedPositions = positions.filter(p =>
    (p.defaultSkills || []).includes(skillName)
  );

  // Prerequisites check
  const requiredEnvs = skill.requires?.env || [];
  const keyStatuses = requiredEnvs.map(env => {
    const key = apiKeys.find(k => k.skillName === skillName && k.envVar === env);
    return { env, status: key?.status || 'not-configured', awsService: key?.awsService || '', note: key?.note || '' };
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
        setAssignResult(`Assigned to ${posName} — ${data.agentsAffected} agent(s) updated`);
        setAssignPos('');
      },
    });
  };

  const handleUnassign = (posId: string) => {
    unassignSkill.mutate({ skillName, positionId: posId });
  };

  return (
    <Modal open={true} onClose={onClose} title={skill.name || skillName} size="lg">
      <div className="space-y-5">
        {/* Header info */}
        <div className="flex items-start gap-4">
          <span className="text-3xl">{categoryIcon[skill.category] || '🧩'}</span>
          <div className="flex-1">
            <p className="text-sm text-text-secondary mb-2">{skill.description}</p>
            <div className="flex flex-wrap gap-2">
              <Badge color={layerColor(skill.layer)}>Layer {skill.layer}</Badge>
              <Badge>{skill.category}</Badge>
              <Badge color={skill.scope === 'global' ? 'info' : 'default'}>{skill.scope}</Badge>
              <Badge color={statusColor(skill.status || 'installed')} dot>{skill.status || 'installed'}</Badge>
              <span className="text-xs text-text-muted">v{skill.version} · {skill.author}</span>
            </div>
          </div>
        </div>

        {/* Step 1: Prerequisites */}
        <div className="rounded-xl border border-dark-border/50 bg-surface-dim px-4 py-3">
          <div className="flex items-center gap-2 mb-2">
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary/20 text-primary text-xs font-bold">1</span>
            <p className="text-sm font-medium text-text-primary">Prerequisites</p>
            {allKeysReady ? (
              <Badge color="success" dot>Ready</Badge>
            ) : (
              <Badge color="warning" dot>Action needed</Badge>
            )}
          </div>
          {requiredEnvs.length === 0 ? (
            <p className="text-xs text-text-muted ml-7">No API keys or external credentials needed.</p>
          ) : (
            <div className="ml-7 space-y-1.5">
              {keyStatuses.map(k => (
                <div key={k.env} className="flex items-center justify-between rounded-lg bg-dark-bg px-3 py-2">
                  <code className="text-xs text-primary-light">{k.env}</code>
                  <div className="flex items-center gap-2">
                    {k.status === 'iam-role' ? (
                      <Badge color="success" dot>IAM Role</Badge>
                    ) : k.status === 'active' ? (
                      <Badge color="success" dot>Configured</Badge>
                    ) : (
                      <Badge color="danger" dot>Missing</Badge>
                    )}
                    <span className="text-[10px] text-text-muted">{k.note}</span>
                  </div>
                </div>
              ))}
              {!allKeysReady && (
                <p className="text-xs text-warning mt-1 flex items-center gap-1">
                  <AlertTriangle size={11} /> Configure missing keys in API Key Vault tab before assigning.
                </p>
              )}
            </div>
          )}
        </div>

        {/* Step 2: Assign to Position */}
        <div className="rounded-xl border border-dark-border/50 bg-surface-dim px-4 py-3">
          <div className="flex items-center gap-2 mb-2">
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary/20 text-primary text-xs font-bold">2</span>
            <p className="text-sm font-medium text-text-primary">Assign to Position</p>
          </div>

          {/* Already assigned */}
          {assignedPositions.length > 0 && (
            <div className="ml-7 mb-3">
              <p className="text-xs text-text-muted mb-1.5">Currently assigned to:</p>
              <div className="flex flex-wrap gap-2">
                {assignedPositions.map(p => (
                  <div key={p.id} className="flex items-center gap-1.5 rounded-lg bg-success/10 border border-success/20 px-2.5 py-1">
                    <CheckCircle size={12} className="text-success" />
                    <span className="text-xs font-medium text-text-primary">{p.name}</span>
                    <button onClick={() => handleUnassign(p.id)} className="text-text-muted hover:text-danger ml-1" title="Remove from position">
                      <X size={11} />
                    </button>
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

          {/* Assign new */}
          <div className="ml-7 flex items-end gap-2">
            <div className="flex-1">
              <Select
                label=""
                value={assignPos}
                onChange={setAssignPos}
                options={unassignedPositions.map(p => ({ label: `${p.name} (${p.departmentName})`, value: p.id }))}
                placeholder="Select position to assign..."
              />
            </div>
            <Button variant="primary" size="sm" disabled={!assignPos || !allKeysReady || assignSkill.isPending} onClick={handleAssign}>
              <Zap size={12} /> {assignSkill.isPending ? 'Assigning...' : 'Assign'}
            </Button>
          </div>
          {!allKeysReady && (
            <p className="ml-7 text-[10px] text-text-muted mt-1">Complete prerequisites first to enable assignment.</p>
          )}
        </div>

        {/* Step 3: What happens next */}
        <div className="rounded-xl border border-dark-border/50 bg-surface-dim px-4 py-3">
          <div className="flex items-center gap-2 mb-2">
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary/20 text-primary text-xs font-bold">3</span>
            <p className="text-sm font-medium text-text-primary">Agents auto-load</p>
          </div>
          <p className="text-xs text-text-muted ml-7">
            All agents in the assigned position will load this skill at their next session start.
            Employees can then use it via any IM channel or Portal chat — no action needed on their side.
          </p>
        </div>

        {/* Access control */}
        {(skill.permissions?.blockedRoles?.length > 0 || skill.approvalRequired) && (
          <div className="rounded-lg bg-warning/5 border border-warning/20 px-3 py-2 text-xs text-text-muted">
            {skill.approvalRequired && <p className="text-warning mb-1">This skill requires admin approval for each use.</p>}
            {skill.permissions.blockedRoles.length > 0 && (
              <p>Blocked roles: {skill.permissions.blockedRoles.map(r => <Badge key={r} color="danger">{r}</Badge>)}</p>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

// ─── Main Page ──────────────────────────────────────────────────────────────

export default function SkillCatalog() {
  const { data: skills = [] } = useSkills();
  const { data: apiKeys = [] } = useSkillKeys();
  const { data: positions = [] } = usePositions();
  const [activeTab, setActiveTab] = useState('catalog');
  const [filterLayer, setFilterLayer] = useState('all');
  const [filterCategory, setFilterCategory] = useState('all');
  const [filterText, setFilterText] = useState('');
  const [selectedSkill, setSelectedSkill] = useState<SkillManifest | null>(null);
  const [showGuide, setShowGuide] = useState(true);

  const installed = skills.filter(s => (s.status || 'installed') === 'installed');
  const l1 = installed.filter(s => s.layer === 1);
  const l2 = installed.filter(s => s.layer === 2);
  const l3 = installed.filter(s => s.layer === 3);
  const categories = [...new Set(skills.map(s => s.category))].sort();

  // Count skills that are assigned to at least one position
  const assignedCount = installed.filter(s => {
    const name = s.name || s.id?.replace('sk-', '') || '';
    return positions.some(p => (p.defaultSkills || []).includes(name));
  }).length;

  const filtered = skills.filter(s => {
    const matchText = !filterText || s.name.includes(filterText.toLowerCase()) || s.description.toLowerCase().includes(filterText.toLowerCase());
    const matchLayer = filterLayer === 'all' || s.layer === Number(filterLayer);
    const matchCat = filterCategory === 'all' || s.category === filterCategory;
    return matchText && matchLayer && matchCat;
  });

  return (
    <div>
      <PageHeader
        title="Skill Platform"
        description={`${installed.length} skills installed · ${assignedCount} assigned to positions · ${apiKeys.filter(k => k.status === 'not-configured').length} keys need config`}
        actions={
          <div className="flex gap-2">
            <Button variant="default" onClick={() => setActiveTab('keys')}><Key size={16} /> API Keys</Button>
            <Button variant="primary" onClick={() => setShowGuide(!showGuide)}>
              {showGuide ? 'Hide Guide' : 'How to Use'}
            </Button>
          </div>
        }
      />

      {/* Quick Start Guide */}
      {showGuide && (
        <div className="mb-6 rounded-xl border-2 border-primary/30 bg-primary/5 px-5 py-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-base font-semibold text-text-primary flex items-center gap-2">
              <Zap size={18} className="text-primary" /> How Skills Work
            </h3>
            <button onClick={() => setShowGuide(false)} className="text-text-muted hover:text-text-primary"><X size={16} /></button>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-3">
            <div className="rounded-lg bg-dark-bg border border-dark-border/50 px-3 py-2.5">
              <div className="flex items-center gap-2 mb-1">
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary/20 text-primary text-xs font-bold">1</span>
                <p className="text-sm font-medium text-text-primary">Check Prerequisites</p>
              </div>
              <p className="text-xs text-text-muted">
                AWS-native skills (Bedrock, SES, S3) use the EC2 IAM role — zero config.
                Third-party skills (GitHub, Jira) need an API key in the Vault first.
              </p>
            </div>
            <div className="rounded-lg bg-dark-bg border border-dark-border/50 px-3 py-2.5">
              <div className="flex items-center gap-2 mb-1">
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary/20 text-primary text-xs font-bold">2</span>
                <p className="text-sm font-medium text-text-primary">Assign to Position</p>
              </div>
              <p className="text-xs text-text-muted">
                Click any skill below <ArrowRight size={10} className="inline" /> "Assign to Position" <ArrowRight size={10} className="inline" /> select a position.
                All agents in that position get the skill automatically.
              </p>
            </div>
            <div className="rounded-lg bg-dark-bg border border-dark-border/50 px-3 py-2.5">
              <div className="flex items-center gap-2 mb-1">
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary/20 text-primary text-xs font-bold">3</span>
                <p className="text-sm font-medium text-text-primary">Employees Use It</p>
              </div>
              <p className="text-xs text-text-muted">
                Agents load assigned skills at session start. Employees just chat normally —
                the agent decides when to use each skill based on the conversation.
              </p>
            </div>
          </div>
          <div className="rounded-lg bg-dark-bg border border-dark-border/40 px-3 py-2.5 text-xs text-text-secondary">
            <strong className="text-text-primary">Try it now:</strong> Click{' '}
            <button onClick={() => {
              const kb = skills.find(s => s.name?.includes('bedrock-kb'));
              if (kb) setSelectedSkill(kb);
            }} className="text-primary-light hover:underline">aws-bedrock-kb-search</button>{' '}
            below — it uses IAM role (no API key needed), so you can assign it to any position right away.
            Employees can then ask their agent to search your Bedrock Knowledge Base.
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-5 mb-6">
        <StatCard title="Total Installed" value={installed.length} icon={<Puzzle size={22} />} color="primary" />
        <StatCard title="Layer 1 (Docker)" value={l1.length} icon={<Package size={22} />} color="primary" subtitle="Built into image" />
        <StatCard title="Layer 2 (S3)" value={l2.length} icon={<Cloud size={22} />} color="success" subtitle="Hot-loaded scripts" />
        <StatCard title="Assigned" value={assignedCount} subtitle={`of ${installed.length} skills`} icon={<CheckCircle size={22} />} color={assignedCount > 0 ? 'success' : 'warning'} />
        <StatCard title="API Keys" value={apiKeys.length} icon={<Key size={22} />} color="warning" subtitle={`${apiKeys.filter(k => k.status === 'iam-role').length} via IAM, ${apiKeys.filter(k => k.status === 'not-configured').length} need config`} />
      </div>

      <Card>
        <Tabs
          tabs={[
            { id: 'catalog', label: 'Skill Catalog', count: skills.length },
            { id: 'keys', label: 'API Key Vault', count: apiKeys.length },
            { id: 'permissions', label: 'Role Permissions' },
          ]}
          activeTab={activeTab}
          onChange={setActiveTab}
        />

        {activeTab === 'catalog' && (
          <div className="mt-4">
            <div className="mb-4 flex flex-wrap items-center gap-3">
              <div className="relative flex-1 max-w-xs">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
                <input type="text" value={filterText} onChange={e => setFilterText(e.target.value)} placeholder="Search skills..."
                  className="w-full rounded-lg border border-dark-border bg-dark-bg py-2 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none" />
              </div>
              <select value={filterLayer} onChange={e => setFilterLayer(e.target.value)} className="rounded-lg border border-dark-border bg-dark-bg px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none appearance-none">
                <option value="all">All Layers</option>
                <option value="1">Layer 1 — Docker</option>
                <option value="2">Layer 2 — S3</option>
                <option value="3">Layer 3 — Bundle</option>
              </select>
              <select value={filterCategory} onChange={e => setFilterCategory(e.target.value)} className="rounded-lg border border-dark-border bg-dark-bg px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none appearance-none">
                <option value="all">All Categories</option>
                {categories.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>

            <Table
              columns={[
                { key: 'name', label: 'Skill', render: (s: SkillManifest) => (
                  <div className="flex items-center gap-3">
                    <span className="text-lg">{categoryIcon[s.category] || '🧩'}</span>
                    <div>
                      <button onClick={() => setSelectedSkill(s)} className="text-sm font-medium text-primary-light hover:underline">{s.name}</button>
                      <p className="text-xs text-text-muted">v{s.version} · {s.author}</p>
                    </div>
                  </div>
                )},
                { key: 'layer', label: 'Layer', render: (s: SkillManifest) => <Badge color={layerColor(s.layer)}>L{s.layer}</Badge> },
                { key: 'category', label: 'Category', render: (s: SkillManifest) => <Badge>{s.category}</Badge> },
                { key: 'desc', label: 'Description', render: (s: SkillManifest) => <span className="text-xs text-text-secondary">{s.description}</span> },
                { key: 'prereqs', label: 'Prerequisites', render: (s: SkillManifest) => {
                  const envs = s.requires?.env || [];
                  if (envs.length === 0) return <Badge color="success">None</Badge>;
                  const allOk = envs.every(env => {
                    const k = apiKeys.find(ak => ak.skillName === (s.name || s.id?.replace('sk-', '')) && ak.envVar === env);
                    return k && (k.status === 'iam-role' || k.status === 'active');
                  });
                  return allOk ? <Badge color="success" dot>Ready</Badge> : <Badge color="warning" dot>{envs.length} key{envs.length > 1 ? 's' : ''}</Badge>;
                }},
                { key: 'assigned', label: 'Assigned To', render: (s: SkillManifest) => {
                  const name = s.name || s.id?.replace('sk-', '') || '';
                  const assigned = positions.filter(p => (p.defaultSkills || []).includes(name));
                  if (assigned.length === 0) return <span className="text-xs text-text-muted">—</span>;
                  return (
                    <div className="flex flex-wrap gap-1">
                      {assigned.slice(0, 2).map(p => <Badge key={p.id} color="info">{p.name}</Badge>)}
                      {assigned.length > 2 && <Badge>+{assigned.length - 2}</Badge>}
                    </div>
                  );
                }},
                { key: 'status', label: 'Status', render: (s: SkillManifest) => <Badge color={statusColor(s.status || 'installed')} dot>{(s.status || 'installed').replace('_', ' ')}</Badge> },
              ]}
              data={filtered}
            />
          </div>
        )}

        {activeTab === 'keys' && (
          <div className="mt-4">
            <div className="mb-4 rounded-lg bg-warning/5 border border-warning/20 px-4 py-3 text-sm text-warning">
              <div className="flex items-center gap-2 mb-1"><Lock size={14} /> API keys are stored as SecureString in SSM Parameter Store (KMS encrypted)</div>
              <p className="text-xs text-text-muted">Keys marked "IAM Role" need no configuration — the EC2 instance role provides access automatically. Only third-party skills need manual key setup.</p>
            </div>
            <Table
              columns={[
                { key: 'skill', label: 'Skill', render: (k: SkillApiKey) => <span className="font-medium">{k.skillName}</span> },
                { key: 'env', label: 'Env Variable', render: (k: SkillApiKey) => <code className="text-xs bg-dark-bg px-1.5 py-0.5 rounded text-primary-light">{k.envVar}</code> },
                { key: 'ssm', label: 'SSM Path', render: (k: SkillApiKey) => <span className="text-xs text-text-muted font-mono">{k.ssmPath}</span> },
                { key: 'status', label: 'Status', render: (k: SkillApiKey) => <Badge color={k.status === 'iam-role' ? 'success' : k.status === 'active' ? 'success' : 'warning'} dot>{k.status === 'iam-role' ? 'IAM Role' : k.status === 'not-configured' ? 'Needs Config' : k.status}</Badge> },
                { key: 'note', label: 'Note', render: (k: SkillApiKey) => <span className="text-xs text-text-muted">{k.note || ''}</span> },
              ]}
              data={apiKeys}
            />
          </div>
        )}

        {activeTab === 'permissions' && (
          <div className="mt-4">
            <p className="text-sm text-text-secondary mb-4">Role-based skill access matrix. Skills are filtered at microVM startup based on tenant role.</p>
            <div className="overflow-x-auto rounded-xl border border-dark-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-dark-border bg-dark-bg/50">
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-muted">Skill</th>
                    {['engineering', 'sales', 'finance', 'product', 'hr', 'csm', 'legal', 'intern'].map(role => (
                      <th key={role} className="px-3 py-3 text-center text-xs font-medium uppercase tracking-wider text-text-muted">{role}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-dark-border">
                  {installed.map(s => (
                    <tr key={s.id || s.name} className="bg-dark-card hover:bg-dark-hover">
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-2">
                          <Badge color={layerColor(s.layer)}>L{s.layer}</Badge>
                          <span className="font-medium text-xs">{s.name}</span>
                        </div>
                      </td>
                      {['engineering', 'sales', 'finance', 'product', 'hr', 'csm', 'legal', 'intern'].map(role => {
                        const blocked = s.permissions.blockedRoles.includes(role);
                        const allowed = s.permissions.allowedRoles.includes('*') || s.permissions.allowedRoles.includes(role);
                        return (
                          <td key={role} className="px-3 py-2.5 text-center">
                            {allowed && !blocked ? <CheckCircle size={16} className="inline text-success" /> : <span className="inline-block h-4 w-4 rounded-full bg-dark-border" />}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Card>

      {/* Skill Detail Modal */}
      {selectedSkill && (
        <SkillDetailModal skill={selectedSkill} apiKeys={apiKeys} onClose={() => setSelectedSkill(null)} />
      )}
    </div>
  );
}
