import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bot, Plus, Users, Star, Zap, Edit3, Play, Settings, Eye, Search, Filter, Cpu, SlidersHorizontal, Trash2, RefreshCw, Check, Cloud } from 'lucide-react';
import { Card, StatCard, Badge, Button, PageHeader, Table as DataTable, Modal, Input, Select, StatusDot, Tabs } from '../../components/ui';
import { useAgents, usePositions, useEmployees, useCreateAgent, useModelConfig, useUpdateModelConfig, useUpdateFallbackModel, useSetPositionModel, useRemovePositionModel, useSetEmployeeModel, useRemoveEmployeeModel, useAgentConfig, useSetPositionAgentConfig, useSetEmployeeAgentConfig, useEksCluster, useEksInstances } from '../../hooks/useApi';
import { EksInstancesTab } from '../EKSCluster';
import { CHANNEL_LABELS } from '../../types';
import type { Agent, ChannelType } from '../../types';

export default function AgentList() {
  const navigate = useNavigate();
  const { data: AGENTS = [], isLoading } = useAgents();
  const { data: POSITIONS = [] } = usePositions();
  // Configuration state
  const { data: mc } = useModelConfig();
  const { data: agentCfgData } = useAgentConfig();
  const setPositionModel = useSetPositionModel();
  const removePositionModel = useRemovePositionModel();
  const setEmployeeModel = useSetEmployeeModel();
  const removeEmployeeModel = useRemoveEmployeeModel();
  const setPositionAgentCfg = useSetPositionAgentConfig();
  const setEmployeeAgentCfg = useSetEmployeeAgentConfig();
  const [cfgTarget, setCfgTarget] = useState<{ type: 'pos'|'emp'; id: string; name: string; kind: 'model'|'memory' } | null>(null);
  const [modelDraft, setModelDraft] = useState('');
  const [modelReason, setModelReason] = useState('');
  const [memoryCfgDraft, setMemoryCfgDraft] = useState<Record<string,any>>({});
  const [cfgSaved, setCfgSaved] = useState(false);

  const m = mc || { default: { modelId:'', modelName:'—', inputRate:0, outputRate:0 }, positionOverrides:{}, employeeOverrides:{}, availableModels:[] };
  const agentCfg = agentCfgData || { positionConfig:{}, employeeConfig:{} };
  const modelOptions = m.availableModels.map((mo:any) => ({ label: `${mo.modelName} ($${mo.inputRate}/$${mo.outputRate})`, value: mo.modelId }));
  const findModel = (id:string) => m.availableModels.find((mo:any) => mo.modelId === id);

  const handleSaveCfg = async () => {
    if (!cfgTarget) return;
    if (cfgTarget.kind === 'model') {
      const model = findModel(modelDraft); if (!model) return;
      if (cfgTarget.type === 'pos') setPositionModel.mutate({ posId: cfgTarget.id, modelId: model.modelId, modelName: model.modelName, inputRate: model.inputRate, outputRate: model.outputRate, reason: modelReason });
      else setEmployeeModel.mutate({ empId: cfgTarget.id, modelId: model.modelId, modelName: model.modelName, inputRate: model.inputRate, outputRate: model.outputRate, reason: modelReason });
    } else {
      if (cfgTarget.type === 'pos') setPositionAgentCfg.mutate({ posId: cfgTarget.id, config: memoryCfgDraft });
      else setEmployeeAgentCfg.mutate({ empId: cfgTarget.id, config: memoryCfgDraft });
    }
    setCfgSaved(true); setTimeout(() => { setCfgSaved(false); setCfgTarget(null); }, 1500);
  };
  const { data: EMPLOYEES = [] } = useEmployees();
  const { data: eksCluster } = useEksCluster();
  const eksAvailable = !!(eksCluster?.configured && eksCluster?.operator?.installed);
  const createAgent = useCreateAgent();
  const [showCreate, setShowCreate] = useState(false);
  const [createStep, setCreateStep] = useState(0);
  const [newName, setNewName] = useState('');
  const [newPos, setNewPos] = useState('');
  const [newEmp, setNewEmp] = useState('');
  const [newChannels, setNewChannels] = useState<string[]>(['discord']);
  const [newDeployMode, setNewDeployMode] = useState<'serverless' | 'always-on-ecs' | 'eks'>('serverless');
  const [filterText, setFilterText] = useState('');
  const [filterDept, setFilterDept] = useState('all');
  const [filterStatus, setFilterStatus] = useState('all');
  const [activeTab, setActiveTab] = useState('serverless');

  const posOptions = POSITIONS.map(p => ({ label: p.name, value: p.id }));
  // Filter to unbound employees; if a position is selected, further filter by that position
  const empOptions = EMPLOYEES
    .filter(e => !e.agentId && (!newPos || e.positionId === newPos))
    .map(e => ({ label: `${e.name} (${e.positionName})`, value: e.id }));
  const qualityAgents = AGENTS.filter(a => a.qualityScore);
  const avgQuality = qualityAgents.length > 0
    ? qualityAgents.reduce((s, a) => s + (a.qualityScore || 0), 0) / qualityAgents.length
    : null;

  const serverlessAgents = AGENTS.filter(a => !a.deployMode || a.deployMode === 'serverless');
  const ecsAgents = AGENTS.filter(a => a.deployMode === 'always-on-ecs');
  const eksAgents = AGENTS.filter(a => a.deployMode === 'eks');
  const { data: eksInstancesData } = useEksInstances();
  const eksInstanceCount = eksInstancesData?.instances?.length ?? eksAgents.length;

  const currentList = activeTab === 'serverless' ? serverlessAgents : activeTab === 'ecs' ? ecsAgents : activeTab === 'all' ? AGENTS : AGENTS;

  // Unique departments from agents
  const deptSet = new Set(AGENTS.map(a => a.positionName));
  const deptOptions = [{ label: 'All Positions', value: 'all' }, ...Array.from(deptSet).map(d => ({ label: d, value: d }))];

  const filtered = currentList.filter(a => {
    const matchText = !filterText || a.name.toLowerCase().includes(filterText.toLowerCase()) || (a.employeeName || '').toLowerCase().includes(filterText.toLowerCase()) || (a.positionName || '').toLowerCase().includes(filterText.toLowerCase());
    const matchDept = filterDept === 'all' || a.positionName === filterDept;
    const matchStatus = filterStatus === 'all' || a.status === filterStatus;
    return matchText && matchDept && matchStatus;
  });

  return (
    <div>
      <PageHeader
        title="Agent Factory"
        description={`${AGENTS.length} agents across ${POSITIONS.length} positions · ${EMPLOYEES.filter(e => !e.agentId).length} employees unbound`}
        actions={<Button variant="primary" onClick={() => { setShowCreate(true); setCreateStep(0); }}><Plus size={16} /> Create Agent</Button>}
      />

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-5 mb-6">
        <StatCard title="Total Agents" value={AGENTS.length} icon={<Bot size={22} />} color="primary" />
        <StatCard title="Serverless" value={serverlessAgents.length} icon={<Users size={22} />} color="info" />
        <StatCard title="ECS" value={ecsAgents.length} icon={<Zap size={22} />} color="cyan" />
        <StatCard title="EKS" value={eksAvailable ? eksInstanceCount : 'Not connected'} icon={<Cloud size={22} />} color={eksAvailable ? 'success' : 'info'} />
        <StatCard title="Avg Quality" value={avgQuality !== null ? `⭐ ${avgQuality.toFixed(1)}` : '—'} icon={<Star size={22} />} color="warning" />
      </div>

      <Card>
        <Tabs
          tabs={[
            { id: 'serverless', label: 'Serverless', count: serverlessAgents.length },
            { id: 'ecs', label: 'ECS (Fargate)', count: ecsAgents.length },
            { id: 'eks', label: 'EKS', count: eksInstanceCount },
            { id: 'all', label: 'All', count: AGENTS.length },
            { id: 'config', label: 'Configuration' },
          ]}
          activeTab={activeTab}
          onChange={setActiveTab}
        />

        {/* EKS tab — live OpenClawInstance CRDs from K8s */}
        {activeTab === 'eks' && <EksInstancesTab agents={AGENTS} />}

        {/* Configuration tab — model & memory settings per position/employee */}
        {activeTab === 'config' && (
          <div className="mt-4 space-y-6">
            {/* Per-Position Model */}
            <div>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-text-primary flex items-center gap-2"><Cpu size={15} className="text-primary" /> Model per Position</h3>
                <span className="text-xs text-text-muted">Default: {m.default.modelName || '—'}</span>
              </div>
              <div className="space-y-2">
                {POSITIONS.map(pos => {
                  const ov = (m.positionOverrides as any)[pos.id];
                  return (
                    <div key={pos.id} className={`flex items-center gap-3 rounded-xl px-4 py-3 ${ov ? 'bg-primary/5 border border-primary/20' : 'bg-surface-dim'}`}>
                      <div className="flex-1">
                        <p className="text-sm font-medium">{pos.name}</p>
                        <p className="text-xs text-text-muted">{ov ? `${ov.modelName} · ${ov.reason || ''}` : 'Uses default'}</p>
                      </div>
                      <div className="flex items-center gap-1.5">
                        {ov && <Badge color="primary">{ov.modelName?.split(' ').slice(-2).join(' ')}</Badge>}
                        <Button size="sm" variant="ghost" onClick={() => { setCfgTarget({ type:'pos', id:pos.id, name:pos.name, kind:'model' }); setModelDraft(ov?.modelId || m.default.modelId); setModelReason(ov?.reason || ''); }}>
                          <Settings size={12} />
                        </Button>
                        {ov && <Button size="sm" variant="ghost" className="text-danger" onClick={() => removePositionModel.mutate(pos.id)}><Trash2 size={12} /></Button>}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Per-Employee Model Overrides */}
            <div>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-text-primary flex items-center gap-2"><Cpu size={15} className="text-warning" /> Model per Employee <Badge color="warning">Highest priority</Badge></h3>
                <Button size="sm" variant="ghost" onClick={() => { setCfgTarget({ type:'emp', id:'', name:'', kind:'model' }); setModelDraft(''); setModelReason(''); }}>
                  <Plus size={12} /> Add Override
                </Button>
              </div>
              {Object.keys(m.employeeOverrides || {}).length === 0 ? (
                <p className="text-xs text-text-muted text-center py-4">No employee-level overrides. Position overrides apply.</p>
              ) : (
                <div className="space-y-2">
                  {Object.entries(m.employeeOverrides || {}).map(([empId, ov]: [string,any]) => {
                    const emp = EMPLOYEES.find(e => e.id === empId);
                    return (
                      <div key={empId} className="flex items-center justify-between rounded-xl bg-warning/5 border border-warning/20 px-4 py-3">
                        <div>
                          <p className="text-sm font-medium">{emp?.name || empId}</p>
                          <p className="text-xs text-text-muted">{ov.modelName} · {ov.reason}</p>
                        </div>
                        <Button size="sm" variant="ghost" className="text-danger" onClick={() => removeEmployeeModel.mutate(empId)}><Trash2 size={12} /></Button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Memory & Context per Position */}
            <div>
              <h3 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2"><SlidersHorizontal size={15} className="text-primary" /> Memory & Context per Position</h3>
              <div className="space-y-2">
                {POSITIONS.map(pos => {
                  const cfg = (agentCfg.positionConfig as any)[pos.id] || {};
                  const hasCfg = Object.keys(cfg).length > 0;
                  return (
                    <div key={pos.id} className={`flex items-center gap-3 rounded-xl px-4 py-3 ${hasCfg ? 'bg-info/5 border border-info/20' : 'bg-surface-dim'}`}>
                      <div className="flex-1">
                        <p className="text-sm font-medium">{pos.name}</p>
                        <p className="text-xs text-text-muted">
                          {hasCfg
                            ? [cfg.recentTurnsPreserve && `Memory: ${cfg.recentTurnsPreserve} turns`, cfg.maxTokens && `Max tokens: ${cfg.maxTokens}`, cfg.language && `Lang: ${cfg.language}`].filter(Boolean).join(' · ')
                            : 'Default — 10 turns, 16384 tokens'}
                        </p>
                      </div>
                      <Button size="sm" variant="ghost" onClick={() => { setCfgTarget({ type:'pos', id:pos.id, name:pos.name, kind:'memory' }); setMemoryCfgDraft((agentCfg.positionConfig as any)[pos.id] || {}); }}>
                        <SlidersHorizontal size={12} /> Configure
                      </Button>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="rounded-xl bg-info/5 border border-info/20 px-4 py-3 text-xs text-info">
              All changes take effect on the next agent cold start (~15 min idle). Current warm sessions are not affected.
            </div>
          </div>
        )}

        {/* Always-on agents — shown when always-on tab is active */}
        {activeTab === 'always-on' && (
          <div className="mt-4 mb-4">
            <div className="rounded-xl bg-cyan/5 border border-cyan/20 px-4 py-3 mb-4 flex items-start gap-3">
              <Zap size={16} className="text-cyan mt-0.5 shrink-0" />
              <div>
                <p className="text-sm font-semibold text-text-primary">Always-on · Powered by ECS Fargate</p>
                <p className="text-xs text-text-muted mt-0.5">Always-on agents run as persistent ECS Fargate containers with EFS workspace. Enables scheduled tasks (email every 3 min), direct IM bot connections, and instant response. Same Docker image — just a deployment mode toggle. Auto-restart on crash.</p>
              </div>
            </div>
            <div className="space-y-3">
              {ecsAgents.map(a => {
                const isOn = a.deployMode === 'always-on-ecs';
                const isStarting = a.containerStatus === 'starting' || a.containerStatus === 'reloading';
                return (
                  <div key={a.id} className={`rounded-xl border px-4 py-3 flex items-center gap-3 ${isOn ? 'border-cyan/30 bg-cyan/5' : 'border-dark-border/40 bg-surface-dim'}`}>
                    <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${isOn ? 'bg-cyan/15 text-cyan' : 'bg-dark-hover text-text-muted'}`}>
                      <Bot size={18} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-semibold text-text-primary">{a.name}</p>
                        {isOn && <Badge color="info" dot>{isStarting ? 'Starting…' : 'Always-on'}</Badge>}
                        {a.employeeId && <Badge color="default">{a.employeeName || a.employeeId}</Badge>}
                      </div>
                      <p className="text-xs text-text-muted">{a.positionName} · ECS Fargate{a.ecsServiceName ? ` · ${a.ecsServiceName}` : ''}</p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <Button size="sm" variant="ghost" onClick={() => navigate(`/agents/${a.id}`)}>
                        <Eye size={13} /> View
                      </Button>
                      {isOn ? (
                        <Button size="sm" variant="ghost" className="text-danger"
                          onClick={async () => {
                            await fetch(`/api/v1/admin/always-on/${a.id}/stop`, { method: 'POST', headers: { Authorization: `Bearer ${localStorage.getItem('openclaw_token')}` } });
                            window.location.reload();
                          }}>
                          <Trash2 size={13} /> Stop
                        </Button>
                      ) : (
                        <Button size="sm" variant="primary"
                          onClick={async () => {
                            await fetch(`/api/v1/admin/always-on/${a.id}/start`, { method: 'POST', headers: { Authorization: `Bearer ${localStorage.getItem('openclaw_token')}` } });
                            window.location.reload();
                          }}>
                          <Zap size={13} /> Start
                        </Button>
                      )}
                    </div>
                  </div>
                );
              })}
              {ecsAgents.length === 0 && (
                <div className="text-center py-8 text-text-muted">
                  <Bot size={28} className="mx-auto mb-2 opacity-30" />
                  <p className="text-sm">No always-on agents</p>
                  <p className="text-xs mt-1">Toggle any agent to always-on mode from the agent detail page for scheduled tasks and instant response</p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Filters */}
        <div className={`${activeTab === 'config' || activeTab === 'shared' || activeTab === 'eks' ? 'hidden' : ''} mt-4 mb-4 flex flex-wrap items-center gap-3`}>
          <div className="relative flex-1 max-w-xs">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              type="text" value={filterText} onChange={e => setFilterText(e.target.value)}
              placeholder="Search agent, employee, position..."
              className="w-full rounded-lg border border-dark-border bg-dark-bg py-2 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none"
            />
          </div>
          <select value={filterDept} onChange={e => setFilterDept(e.target.value)} className="rounded-lg border border-dark-border bg-dark-bg px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none appearance-none">
            {deptOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)} className="rounded-lg border border-dark-border bg-dark-bg px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none appearance-none">
            <option value="all">All Status</option>
            <option value="active">Active</option>
            <option value="idle">Idle</option>
            <option value="error">Error</option>
          </select>
          <Badge color="info">{filtered.length} agents</Badge>
        </div>

        {activeTab !== 'config' && activeTab !== 'shared' && activeTab !== 'eks' && <DataTable
          columns={[
            { key: 'name', label: 'Agent', render: (a: Agent) => (
              <div className="flex items-center gap-3">
                <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${a.employeeId ? 'bg-primary/10 text-primary' : 'bg-cyan/10 text-cyan'}`}>
                  <Bot size={16} />
                </div>
                <div>
                  <button onClick={() => navigate(`/agents/${a.id}`)} className="text-sm font-medium text-primary-light hover:underline">{a.name}</button>
                  <p className="text-xs text-text-muted">{a.deployMode === 'always-on-ecs' ? '⚡ ECS' : a.deployMode === 'eks' ? '☸ EKS' : 'Serverless'}{a.employeeName ? ` · ${a.employeeName}` : ''}</p>
                </div>
              </div>
            )},
            { key: 'employee', label: 'Employee', render: (a: Agent) => <span className="text-sm">{a.employeeName}</span> },
            { key: 'position', label: 'Position', render: (a: Agent) => <Badge>{a.positionName}</Badge> },
            { key: 'channels', label: 'Channels', render: (a: Agent) => (
              <div className="flex flex-wrap gap-1">{(a.channels || []).map(c => <Badge key={c} color="info">{CHANNEL_LABELS[c as ChannelType]}</Badge>)}</div>
            )},
            { key: 'skills', label: 'Skills', render: (a: Agent) => <span className="text-sm text-text-secondary">{(a.skills || []).length}</span> },
            { key: 'quality', label: 'Quality', render: (a: Agent) => (
              <span className={`text-sm font-medium ${(a.qualityScore || 0) >= 4.5 ? 'text-success' : (a.qualityScore || 0) >= 4.0 ? 'text-warning' : 'text-danger'}`}>
                ⭐ {a.qualityScore?.toFixed(1) || '—'}
              </span>
            )},
            { key: 'soul', label: 'SOUL', render: (a: Agent) => (
              <div className="flex gap-1 text-xs">
                <span className="text-text-muted">G:v{a.soulVersions?.global ?? '?'}</span>
                <span className="text-primary">P:v{a.soulVersions?.position ?? '?'}</span>
                <span className="text-success">U:v{a.soulVersions?.personal ?? '?'}</span>
              </div>
            )},
            { key: 'status', label: 'Status', render: (a: Agent) => <StatusDot status={a.status} /> },
            { key: 'updated', label: 'Updated', render: (a: Agent) => <span className="text-xs text-text-muted">{new Date(a.updatedAt).toLocaleDateString()}</span> },
            { key: 'actions', label: '', render: (a: Agent) => (
              <div className="flex gap-1">
                <Button variant="ghost" size="sm" onClick={() => navigate(`/agents/${a.id}`)}><Eye size={14} /></Button>
                <Button variant="ghost" size="sm" onClick={() => navigate(`/agents/${a.id}/soul`)}><Edit3 size={14} /></Button>
              </div>
            )},
          ]}
          data={filtered}
        />}
      </Card>

      {/* Create Agent Modal */}
      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="Create Agent" size="lg"
        footer={
          <div className="flex justify-between">
            <div className="flex gap-1">
              {[0, 1, 2].map(i => (
                <div key={i} className={`h-1.5 w-8 rounded-full ${i <= createStep ? 'bg-primary' : 'bg-dark-border'}`} />
              ))}
            </div>
            <div className="flex gap-3">
              {createStep > 0 && <Button variant="default" onClick={() => setCreateStep(s => s - 1)}>Back</Button>}
              {createStep < 2 ? (
                <Button variant="primary" onClick={() => setCreateStep(s => s + 1)}>Next</Button>
              ) : (
                <Button variant="primary" onClick={() => {
                  if (newName && newPos) {
                    const pos = POSITIONS.find(p => p.id === newPos);
                    const emp = EMPLOYEES.find(e => e.id === newEmp);
                    const defaultCh = pos?.defaultChannel || 'discord';
                    createAgent.mutate({
                      id: `agent-${newPos.replace('pos-', '')}-${newEmp.replace('emp-', '')}`,
                      name: newName,
                      employeeId: newEmp || null,
                      employeeName: emp?.name || '',
                      positionId: newPos,
                      positionName: pos?.name || '',
                      channels: [defaultCh],
                      defaultChannel: defaultCh,
                      skills: pos?.defaultSkills || [],
                      deployMode: newDeployMode,
                    } as any);
                  }
                  setShowCreate(false); setNewName(''); setNewPos(''); setNewEmp(''); setNewDeployMode('serverless'); setCreateStep(0);
                }}>Create Agent</Button>
              )}
            </div>
          </div>
        }
      >
        {createStep === 0 && (
          <div className="space-y-4">
            <h4 className="text-sm font-medium text-text-primary">Step 1: Basic Configuration</h4>
            <Select label="Position Template" value={newPos} onChange={v => {
              setNewPos(v);
              setNewEmp(''); // reset employee when position changes
              const pos = POSITIONS.find(p => p.id === v);
              if (pos) setNewName(`${pos.name} Agent`);
            }} options={posOptions} placeholder="Select position" description="Inherits SOUL, Skills, and tool permissions" />
            <Select label="Assign Employee" value={newEmp} onChange={v => {
              setNewEmp(v);
              const pos = POSITIONS.find(p => p.id === newPos);
              const emp = EMPLOYEES.find(e => e.id === v);
              if (pos && emp) setNewName(`${pos.name} Agent - ${emp.name}`);
            }} options={empOptions} placeholder="Select employee (only showing unassigned)" />
            <Input label="Agent Name" value={newName} onChange={setNewName} placeholder="Auto-generated from position + employee" description="Auto-filled — edit if you want a custom name" />
            <div>
              <label className="mb-1.5 block text-xs font-medium text-text-secondary">Deployment Mode</label>
              <div className="grid grid-cols-3 gap-3">
                <button
                  className={`rounded-xl border p-3 text-left transition-all ${newDeployMode === 'serverless' ? 'border-primary bg-primary/5' : 'border-dark-border/40 bg-surface-dim hover:border-dark-border'}`}
                  onClick={() => setNewDeployMode('serverless')}
                >
                  <p className="text-sm font-medium text-text-primary">Serverless</p>
                  <p className="text-xs text-text-muted mt-0.5">AgentCore microVM. Scales to zero, pay-per-use.</p>
                </button>
                <button
                  className={`rounded-xl border p-3 text-left transition-all ${newDeployMode === 'always-on-ecs' ? 'border-primary bg-primary/5' : 'border-dark-border/40 bg-surface-dim hover:border-dark-border'}`}
                  onClick={() => setNewDeployMode('always-on-ecs')}
                >
                  <p className="text-sm font-medium text-text-primary">ECS (Fargate)</p>
                  <p className="text-xs text-text-muted mt-0.5">Persistent container. Scheduled tasks, direct IM.</p>
                </button>
                <button
                  className={`rounded-xl border p-3 text-left transition-all ${newDeployMode === 'eks' ? 'border-primary bg-primary/5' : eksAvailable ? 'border-dark-border/40 bg-surface-dim hover:border-dark-border' : 'border-dark-border/20 bg-surface-dim/50 opacity-50 cursor-not-allowed'}`}
                  onClick={() => eksAvailable && setNewDeployMode('eks')}
                  disabled={!eksAvailable}
                >
                  <p className="text-sm font-medium text-text-primary">EKS (Kubernetes)</p>
                  <p className="text-xs text-text-muted mt-0.5">{eksAvailable ? 'Operator-managed pod. EFS, Helm, China support.' : 'Configure EKS cluster in Settings → EKS first.'}</p>
                </button>
              </div>
            </div>
          </div>
        )}
        {createStep === 1 && (
          <div className="space-y-4">
            <h4 className="text-sm font-medium text-text-primary">Step 2: SOUL Preview</h4>
            <div className="rounded-lg bg-info/5 border border-info/20 p-3 text-sm text-info">
              You can fine-tune the three-layer SOUL configuration in the SOUL Editor after creation
            </div>
            {newPos && (
              <>
                <div>
                  <p className="text-xs text-text-muted mb-2">Position SOUL Template</p>
                  <pre className="rounded-lg bg-dark-bg border-l-2 border-primary p-4 text-sm text-text-secondary whitespace-pre-wrap font-mono">
                    {POSITIONS.find(p => p.id === newPos)?.soulTemplate || '(empty)'}
                  </pre>
                </div>
                <div>
                  <p className="text-xs text-text-muted mb-2">Inherited Skills</p>
                  <div className="flex flex-wrap gap-1">
                    {(POSITIONS.find(p => p.id === newPos)?.defaultSkills || []).map(s => <Badge key={s} color="success">{s}</Badge>)}
                  </div>
                </div>
              </>
            )}
          </div>
        )}
        {createStep === 2 && (
          <div className="space-y-4">
            <h4 className="text-sm font-medium text-text-primary">Step 3: Review & Create</h4>
            <div className="grid grid-cols-2 gap-4 rounded-lg bg-dark-bg p-4">
              <div><p className="text-xs text-text-muted">Agent Name</p><p className="text-sm font-medium">{newName || '(not set)'}</p></div>
              <div><p className="text-xs text-text-muted">Position</p><p className="text-sm font-medium">{POSITIONS.find(p => p.id === newPos)?.name || '(not selected)'}</p></div>
              <div><p className="text-xs text-text-muted">Employee</p><p className="text-sm font-medium">{EMPLOYEES.find(e => e.id === newEmp)?.name || '(not selected)'}</p></div>
              <div><p className="text-xs text-text-muted">Default Channel</p><p className="text-sm font-medium">{POSITIONS.find(p => p.id === newPos)?.defaultChannel || 'discord'}</p></div>
              <div><p className="text-xs text-text-muted">Deployment</p><p className="text-sm font-medium">{newDeployMode === 'eks' ? '☸ EKS (Kubernetes)' : newDeployMode === 'always-on-ecs' ? '⚡ ECS (Fargate)' : 'Serverless (AgentCore)'}</p></div>
            </div>
          </div>
        )}
      </Modal>

      {/* Config Modal — model or memory */}
      {cfgTarget && (
        <Modal open={true} onClose={() => setCfgTarget(null)}
          title={cfgTarget.kind === 'model'
            ? `Model Override — ${cfgTarget.name || 'New Employee Override'}`
            : `Memory & Context — ${cfgTarget.name}`}
          footer={
            <div className="flex justify-end gap-3">
              <Button variant="default" onClick={() => setCfgTarget(null)}>Cancel</Button>
              <Button variant="primary" disabled={cfgSaved} onClick={handleSaveCfg}>
                {cfgSaved ? <><Check size={13} /> Saved</> : 'Save'}
              </Button>
            </div>
          }>
          {cfgTarget.kind === 'model' ? (
            <div className="space-y-4">
              {cfgTarget.type === 'emp' && cfgTarget.id === '' && (
                <Select label="Employee" value={cfgTarget.id}
                  onChange={id => setCfgTarget(t => t ? {...t, id, name: EMPLOYEES.find(e=>e.id===id)?.name||id} : t)}
                  options={EMPLOYEES.map(e => ({ label: `${e.name} — ${e.positionName}`, value: e.id }))}
                  placeholder="Select employee..." />
              )}
              <Select label="Model" value={modelDraft} onChange={setModelDraft} options={modelOptions} placeholder="Select model..." />
              <div>
                <label className="mb-1.5 block text-xs font-medium text-text-secondary">Reason (optional)</label>
                <input value={modelReason} onChange={e => setModelReason(e.target.value)}
                  className="w-full rounded-xl border border-dark-border/60 bg-surface-dim px-4 py-2.5 text-sm text-text-primary focus:border-primary/60 focus:outline-none"
                  placeholder="e.g. Needs Sonnet 4.5 for architecture reviews" />
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              <p className="text-xs text-text-muted">Leave blank to use platform defaults.</p>
              {[
                { key: 'recentTurnsPreserve', label: 'Memory: recent turns to preserve', placeholder: '10', hint: 'How many recent turns are kept during compaction (default 10)' },
                { key: 'maxTokens', label: 'Max output tokens', placeholder: '16384', hint: 'Max tokens per response (model dependent, default 16384)' },
                { key: 'language', label: 'Default response language', placeholder: 'e.g. English, 中文', hint: 'Agent defaults to this language unless user writes in another' },
              ].map(f => (
                <div key={f.key}>
                  <label className="mb-1 block text-xs font-medium text-text-secondary">{f.label}</label>
                  <input value={memoryCfgDraft[f.key] || ''} onChange={e => setMemoryCfgDraft(d => ({ ...d, [f.key]: e.target.value }))}
                    placeholder={f.placeholder}
                    className="w-full rounded-xl border border-dark-border/60 bg-surface-dim px-4 py-2.5 text-sm text-text-primary focus:border-primary/60 focus:outline-none" />
                  <p className="text-[10px] text-text-muted mt-1">{f.hint}</p>
                </div>
              ))}
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
