import { useState, useEffect } from 'react';
import {
  Shield, Cpu, Zap, Clock, Edit3, Save, X, Plus, ChevronRight,
  Package, Key, Network, Globe2, CheckCircle, AlertTriangle,
  FileText, Wrench, RefreshCw, ExternalLink, Lock, Unlock,
} from 'lucide-react';
import { Card, Badge, Button, PageHeader, Tabs, Modal } from '../components/ui';
import {
  usePositions, useSecurityRuntimes, useUpdateRuntimeLifecycle, useUpdateRuntimeConfig, useCreateRuntime,
  useGlobalSoul, useUpdateGlobalSoul,
  usePositionSoul, useUpdatePositionSoul,
  usePositionTools, useUpdatePositionTools,
  useInfrastructure, useEcrImages, useIamRoles, useVpcResources,
  useModelConfig, useUpdateModelConfig, useUpdateFallbackModel,
  useSetPositionModel, useRemovePositionModel,
  usePositionRuntimeMap, useSetPositionRuntime, useDeletePositionRuntime,
  useGuardrails, useServiceStatus, useFargateOverview,
} from '../hooks/useApi';
import { Select } from '../components/ui';

// ─── helpers ────────────────────────────────────────────────────────────────

function fmtTime(sec: number) {
  if (sec < 3600) return `${Math.round(sec / 60)} min`;
  return `${Math.round(sec / 3600)} hr`;
}

function fmtBytes(b: number) {
  if (b > 1e9) return `${(b / 1e9).toFixed(1)} GB`;
  if (b > 1e6) return `${(b / 1e6).toFixed(0)} MB`;
  return `${b} B`;
}

const ALL_TOOLS = ['web_search', 'shell', 'browser', 'file', 'file_write', 'code_execution'];
const TOOL_LABELS: Record<string, string> = {
  web_search: 'Web Search', shell: 'Shell', browser: 'Browser',
  file: 'File Read', file_write: 'File Write', code_execution: 'Code Execution',
};

// ─── Time Slider ─────────────────────────────────────────────────────────────

function TimeSlider({ label, value, onChange, min = 60, max = 28800 }: {
  label: string; value: number; onChange: (v: number) => void; min?: number; max?: number;
}) {
  return (
    <div>
      <div className="flex justify-between mb-1">
        <span className="text-xs text-text-muted">{label}</span>
        <span className="text-xs font-semibold text-text-primary">{fmtTime(value)}</span>
      </div>
      <input type="range" min={min} max={max} value={value}
        onChange={e => onChange(Number(e.target.value))}
        className="w-full accent-primary" />
      <div className="flex justify-between text-[10px] text-text-muted mt-0.5">
        <span>1 min</span><span>15 min</span><span>1 hr</span><span>4 hr</span><span>8 hr</span>
      </div>
    </div>
  );
}

// ─── Runtime Edit Modal ───────────────────────────────────────────────────────

function RuntimeEditModal({ rt, models, onClose, awsRegion = 'us-east-1' }: { rt: any; models: any[]; onClose: () => void; awsRegion?: string }) {
  const { data: ecrData } = useEcrImages();
  const { data: iamData } = useIamRoles();
  const { data: vpcData } = useVpcResources();
  const { data: guardrailsData } = useGuardrails();
  const updateConfig = useUpdateRuntimeConfig();

  const [containerUri, setContainerUri] = useState(rt.containerUri || '');
  const [roleArn, setRoleArn] = useState(rt.roleArn || '');
  const [modelId, setModelId] = useState(rt.model || '');
  const [networkMode, setNetworkMode] = useState(rt.networkMode || 'PUBLIC');
  const [securityGroupIds, setSecurityGroupIds] = useState<string[]>(rt.securityGroupIds || []);
  const [subnetIds, setSubnetIds] = useState<string[]>(rt.subnetIds || []);
  const [idle, setIdle] = useState(rt.idleTimeoutSec || 900);
  const [maxLife, setMaxLife] = useState(rt.maxLifetimeSec || 28800);
  const [guardrailId, setGuardrailId] = useState(rt.guardrailId || '');
  const [guardrailVersion, setGuardrailVersion] = useState(rt.guardrailVersion || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const ecrImages = ecrData?.images || [];
  const iamRoles = iamData?.roles || [];
  const securityGroups = vpcData?.securityGroups || [];
  const subnets = vpcData?.subnets || [];
  const modelOptions = models.map(m => ({ label: `${m.modelName}`, value: m.modelId }));
  const imageOptions = ecrImages.map(i => ({ label: `${i.repo}:${i.tag} (${fmtBytes(i.sizeBytes)})`, value: i.uri }));
  const roleOptions = [
    ...iamRoles.filter(r => r.relevant).map(r => ({ label: `${r.name} ★`, value: r.arn })),
    ...iamRoles.filter(r => !r.relevant).map(r => ({ label: r.name, value: r.arn })),
  ];
  const sgOptions = securityGroups.map(sg => ({ label: `${sg.name} (${sg.id}) — ${sg.description.slice(0,40)}`, value: sg.id }));
  const subnetOptions = subnets.map(s => ({ label: `${s.id} — ${s.az} ${s.cidr}`, value: s.id }));

  const toggleSg = (id: string) => setSecurityGroupIds(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]);
  const toggleSubnet = (id: string) => setSubnetIds(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]);

  const handleSave = async () => {
    setSaving(true); setError('');
    try {
      await updateConfig.mutateAsync({
        runtimeId: rt.id, containerUri, roleArn, modelId,
        networkMode, securityGroupIds, subnetIds, idleTimeoutSec: idle, maxLifetimeSec: maxLife,
        guardrailId, guardrailVersion: guardrailVersion || 'DRAFT',
      });
      onClose();
    } catch (e: any) {
      setError(e?.message || 'Update failed');
    }
    setSaving(false);
  };

  return (
    <Modal open={true} onClose={onClose} title={`Configure Runtime — ${rt.name}`}
      footer={
        <div className="flex items-center justify-between w-full">
          {error && <p className="text-xs text-danger">{error}</p>}
          <div className="flex gap-3 ml-auto">
            <Button variant="default" onClick={onClose}>Cancel</Button>
            <Button variant="primary" disabled={saving} onClick={handleSave}>
              {saving ? <><RefreshCw size={13} className="animate-spin" /> Saving…</> : <><Save size={13} /> Save & Update Runtime</>}
            </Button>
          </div>
        </div>
      }>
      <div className="space-y-5">
        {/* Container Image */}
        <div>
          <label className="mb-1.5 block text-xs font-semibold text-text-secondary">Container Image (ECR)</label>
          {ecrImages.length > 0 ? (
            <Select label="" value={containerUri} onChange={setContainerUri} options={imageOptions} placeholder="Select ECR image..." />
          ) : (
            <input value={containerUri} onChange={e => setContainerUri(e.target.value)}
              className="w-full rounded-xl border border-dark-border/60 bg-surface-dim px-4 py-2.5 text-sm font-mono text-text-primary focus:border-primary/60 focus:outline-none"
              placeholder="263168716248.dkr.ecr.us-east-1.amazonaws.com/repo:tag" />
          )}
          <p className="mt-1 text-[10px] text-text-muted font-mono truncate">{containerUri}</p>
        </div>

        {/* IAM Role */}
        <div>
          <label className="mb-1.5 block text-xs font-semibold text-text-secondary">IAM Execution Role</label>
          {iamRoles.length > 0 ? (
            <Select label="" value={roleArn} onChange={setRoleArn} options={roleOptions} placeholder="Select IAM role..." />
          ) : (
            <input value={roleArn} onChange={e => setRoleArn(e.target.value)}
              className="w-full rounded-xl border border-dark-border/60 bg-surface-dim px-4 py-2.5 text-xs font-mono text-text-primary focus:border-primary/60 focus:outline-none"
              placeholder="arn:aws:iam::ACCOUNT:role/role-name" />
          )}
          <p className="mt-1 text-[10px] text-text-muted font-mono truncate">{roleArn}</p>
        </div>

        {/* Default Model */}
        <Select label="Default Model" value={modelId} onChange={setModelId} options={modelOptions} placeholder="Select model..." />

        {/* Lifecycle */}
        <div className="space-y-3 rounded-xl bg-surface-dim p-4">
          <p className="text-xs font-semibold text-text-secondary flex items-center gap-1.5"><Clock size={12} /> Lifecycle</p>
          <TimeSlider label="Idle timeout (no msg → microVM released)" value={idle} onChange={setIdle} />
          <TimeSlider label="Max lifetime (force restart ceiling)" value={maxLife} onChange={setMaxLife} />
        </div>

        {/* Guardrail Binding */}
        <div>
          <label className="mb-1.5 block text-xs font-semibold text-text-secondary flex items-center gap-1.5">
            <Shield size={12} className="text-warning" /> Bedrock Guardrail (L5 Content Policy)
          </label>
          {guardrailsData?.guardrails && guardrailsData.guardrails.length > 0 ? (
            <select
              value={guardrailId}
              onChange={e => {
                setGuardrailId(e.target.value);
                const g = guardrailsData.guardrails.find(g => g.id === e.target.value);
                setGuardrailVersion(g?.version || '1');
              }}
              className="w-full rounded-xl border border-dark-border/60 bg-surface-dim px-4 py-2.5 text-sm text-text-primary focus:border-primary/60 focus:outline-none"
            >
              <option value="">— No Guardrail (unrestricted) —</option>
              {guardrailsData.guardrails.map(g => (
                <option key={g.id} value={g.id}>{g.name} (v{g.version}) · {g.id}</option>
              ))}
            </select>
          ) : (
            <input value={guardrailId} onChange={e => setGuardrailId(e.target.value)}
              className="w-full rounded-xl border border-dark-border/60 bg-surface-dim px-4 py-2.5 text-xs font-mono text-text-primary focus:border-primary/60 focus:outline-none"
              placeholder="Guardrail ID (e.g. b44c26tk2kds) — leave blank for none" />
          )}
          {guardrailId && (
            <div className="mt-2">
              <label className="mb-1 block text-xs text-text-muted">Version</label>
              <input value={guardrailVersion} onChange={e => setGuardrailVersion(e.target.value)}
                className="w-full rounded-xl border border-dark-border/60 bg-surface-dim px-4 py-2 text-xs font-mono text-text-primary focus:border-primary/60 focus:outline-none"
                placeholder="1" />
            </div>
          )}
          <p className="mt-1.5 text-[10px] text-text-muted">
            Guardrail checks user input and agent output on every invocation. Exec Runtime leave blank — no restriction.
          </p>
        </div>

        {/* Network Mode */}
        <div>
          <label className="mb-1.5 block text-xs font-semibold text-text-secondary">Network Mode</label>
          <div className="flex gap-2">
            {['PUBLIC', 'VPC'].map(m => (
              <button key={m} onClick={() => setNetworkMode(m)}
                className={`flex-1 rounded-xl px-4 py-2.5 text-sm font-medium border transition-colors ${networkMode === m ? 'border-primary bg-primary/10 text-primary-light' : 'border-dark-border bg-surface-dim text-text-muted hover:border-primary/40'}`}>
                {m === 'PUBLIC' ? 'Public (no VPC)' : 'VPC'}
              </button>
            ))}
          </div>
        </div>

        {/* VPC config — only shown for VPC mode */}
        {networkMode === 'VPC' && (
          <>
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-xs font-semibold text-text-secondary">Security Groups</label>
                <a href={`https://console.aws.amazon.com/vpc/home?region=${awsRegion}#SecurityGroups:`} target="_blank" rel="noreferrer">
                  <Button size="sm" variant="ghost"><ExternalLink size={12} /> Create in AWS</Button>
                </a>
              </div>
              {securityGroups.length === 0
                ? <p className="text-xs text-text-muted">Loading security groups...</p>
                : <div className="space-y-1.5 max-h-36 overflow-y-auto">
                  {securityGroups.map(sg => (
                    <label key={sg.id} className={`flex items-center gap-2.5 rounded-lg px-3 py-2 cursor-pointer ${securityGroupIds.includes(sg.id) ? 'bg-primary/10 border border-primary/30' : 'bg-surface-dim hover:bg-dark-hover'}`}>
                      <input type="checkbox" checked={securityGroupIds.includes(sg.id)} onChange={() => toggleSg(sg.id)} className="accent-primary" />
                      <span className="text-xs font-medium">{sg.name}</span>
                      <span className="text-[10px] text-text-muted font-mono">{sg.id}</span>
                      {sg.relevant && <Badge color="primary">AgentCore</Badge>}
                    </label>
                  ))}
                </div>
              }
            </div>
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-xs font-semibold text-text-secondary">Subnets</label>
                <a href={`https://console.aws.amazon.com/vpc/home?region=${awsRegion}#subnets:`} target="_blank" rel="noreferrer">
                  <Button size="sm" variant="ghost"><ExternalLink size={12} /> View in AWS</Button>
                </a>
              </div>
              {subnets.length === 0
                ? <p className="text-xs text-text-muted">Loading subnets...</p>
                : <div className="space-y-1.5 max-h-36 overflow-y-auto">
                  {subnets.map(s => (
                    <label key={s.id} className={`flex items-center gap-2.5 rounded-lg px-3 py-2 cursor-pointer ${subnetIds.includes(s.id) ? 'bg-primary/10 border border-primary/30' : 'bg-surface-dim hover:bg-dark-hover'}`}>
                      <input type="checkbox" checked={subnetIds.includes(s.id)} onChange={() => toggleSubnet(s.id)} className="accent-primary" />
                      <span className="text-xs font-medium font-mono">{s.id}</span>
                      <span className="text-[10px] text-text-muted">{s.az} · {s.cidr}</span>
                    </label>
                  ))}
                </div>
              }
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

// ─── Create Runtime Modal ─────────────────────────────────────────────────────

function CreateRuntimeModal({ models, onClose, awsRegion = 'us-east-1' }: { models: any[]; onClose: () => void; awsRegion?: string }) {
  const { data: ecrData } = useEcrImages();
  const { data: iamData } = useIamRoles();
  const { data: vpcData } = useVpcResources();
  const createRuntime = useCreateRuntime();

  const [name, setName] = useState('');
  const [containerUri, setContainerUri] = useState('');
  const [roleArn, setRoleArn] = useState('');
  const [modelId, setModelId] = useState('global.amazon.nova-2-lite-v1:0');
  const [networkMode, setNetworkMode] = useState('PUBLIC');
  const [idle, setIdle] = useState(900);
  const [maxLife, setMaxLife] = useState(28800);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const ecrImages = ecrData?.images || [];
  const iamRoles = iamData?.roles || [];
  const modelOptions = models.map(m => ({ label: m.modelName, value: m.modelId }));
  const imageOptions = ecrImages.map(i => ({ label: `${i.repo}:${i.tag}`, value: i.uri }));
  const roleOptions = [
    ...iamRoles.filter(r => r.relevant).map(r => ({ label: `${r.name} ★`, value: r.arn })),
    ...iamRoles.filter(r => !r.relevant).map(r => ({ label: r.name, value: r.arn })),
  ];

  const handleCreate = async () => {
    if (!name || !containerUri || !roleArn) { setError('Name, container image, and IAM role are required'); return; }
    setSaving(true); setError('');
    try {
      await createRuntime.mutateAsync({ name, containerUri, roleArn, modelId, networkMode, securityGroupIds: [], subnetIds: [], idleTimeoutSec: idle, maxLifetimeSec: maxLife });
      onClose();
    } catch (e: any) {
      setError(e?.message || 'Create failed');
    }
    setSaving(false);
  };

  return (
    <Modal open={true} onClose={onClose} title="Create New AgentCore Runtime"
      footer={
        <div className="flex items-center justify-between w-full">
          {error && <p className="text-xs text-danger flex-1">{error}</p>}
          <div className="flex gap-3 ml-auto">
            <Button variant="default" onClick={onClose}>Cancel</Button>
            <Button variant="primary" disabled={saving} onClick={handleCreate}>
              {saving ? <><RefreshCw size={13} className="animate-spin" /> Creating…</> : <><Plus size={13} /> Create Runtime</>}
            </Button>
          </div>
        </div>
      }>
      <div className="space-y-4">
        <div>
          <label className="mb-1.5 block text-xs font-semibold text-text-secondary">Runtime Name</label>
          <input value={name} onChange={e => setName(e.target.value)}
            className="w-full rounded-xl border border-dark-border/60 bg-surface-dim px-4 py-2.5 text-sm text-text-primary focus:border-primary/60 focus:outline-none"
            placeholder="e.g. openclaw_multitenancy_standard_runtime" />
        </div>
        {imageOptions.length > 0
          ? <Select label="Container Image (ECR)" value={containerUri} onChange={setContainerUri} options={imageOptions} placeholder="Select image..." />
          : <div><label className="mb-1.5 block text-xs font-semibold text-text-secondary">Container Image URI</label>
              <input value={containerUri} onChange={e => setContainerUri(e.target.value)}
                className="w-full rounded-xl border border-dark-border/60 bg-surface-dim px-4 py-2.5 text-xs font-mono text-text-primary focus:border-primary/60 focus:outline-none"
                placeholder="263168716248.dkr.ecr.us-east-1.amazonaws.com/repo:tag" />
            </div>}
        {roleOptions.length > 0
          ? <Select label="IAM Execution Role" value={roleArn} onChange={setRoleArn} options={roleOptions} placeholder="Select role..." />
          : <div><label className="mb-1.5 block text-xs font-semibold text-text-secondary">IAM Role ARN</label>
              <input value={roleArn} onChange={e => setRoleArn(e.target.value)}
                className="w-full rounded-xl border border-dark-border/60 bg-surface-dim px-4 py-2.5 text-xs font-mono text-text-primary focus:border-primary/60 focus:outline-none"
                placeholder="arn:aws:iam::ACCOUNT:role/..." />
            </div>}
        <Select label="Default Model" value={modelId} onChange={setModelId} options={modelOptions} />
        <div className="space-y-3 rounded-xl bg-surface-dim p-4">
          <p className="text-xs font-semibold text-text-secondary flex items-center gap-1.5"><Clock size={12} /> Lifecycle</p>
          <TimeSlider label="Idle timeout" value={idle} onChange={setIdle} />
          <TimeSlider label="Max lifetime" value={maxLife} onChange={setMaxLife} />
        </div>
        <div className="rounded-xl bg-info/5 border border-info/20 px-3 py-2 text-xs text-info">
          After creation, assign positions to this runtime in Security Center → Policies, and update SSM:
          <code className="block mt-1 font-mono text-[10px]">/openclaw/STACK/tenants/EMP_ID/runtime-id</code>
        </div>
      </div>
    </Modal>
  );
}

// ─── Runtime Card ─────────────────────────────────────────────────────────────

function RuntimeCard({ rt, models, awsRegion = 'us-east-1', runtimeMap = {}, positions = [] }: { rt: any; models: any[]; awsRegion?: string; runtimeMap?: Record<string, string>; positions?: any[] }) {
  const [showEdit, setShowEdit] = useState(false);
  const isExec = rt.name?.toLowerCase().includes('exec') || rt.containerUri?.includes('exec');
  const imageTag = rt.containerUri?.split('/').pop() || 'unknown';
  const roleName = rt.roleArn?.split('/').pop() || '—';
  const modelName = models.find(m => m.modelId === rt.model)?.modelName || rt.model?.split('/').pop()?.split(':')[0] || '—';
  const networkMode = rt.networkMode || 'PUBLIC';

  return (
    <>
      <Card className={`${isExec ? 'border-warning/30' : ''}`}>
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className={`flex h-10 w-10 items-center justify-center rounded-xl ${isExec ? 'bg-warning/10' : 'bg-primary/10'}`}>
              {isExec ? <Zap size={20} className="text-warning" /> : <Cpu size={20} className="text-primary" />}
            </div>
            <div>
              <h3 className="text-sm font-semibold text-text-primary">{rt.name}</h3>
              <p className="text-xs text-text-muted">v{rt.version || '1'} · {rt.id?.slice(-8)}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge color={rt.status === 'READY' ? 'success' : 'warning'} dot>{rt.status || 'UNKNOWN'}</Badge>
            <Button size="sm" variant="primary" onClick={() => setShowEdit(true)}>
              <Edit3 size={12} /> Configure
            </Button>
          </div>
        </div>

        <div className="space-y-2 mb-4">
          {[
            { label: 'Container Image', value: imageTag,
              extra: <a href={`https://console.aws.amazon.com/ecr/repositories?region=${awsRegion}`} target="_blank" rel="noreferrer"><ExternalLink size={11} className="text-text-muted hover:text-primary" /></a> },
            { label: 'Default Model', value: modelName,
              extra: isExec ? <Badge color="warning">Executive</Badge> : null },
            { label: 'IAM Role', value: roleName,
              extra: <><Badge color={isExec ? 'danger' : 'info'}>{isExec ? 'Full Access' : 'Scoped'}</Badge>
                      <a href={`https://console.aws.amazon.com/iam/home#/roles/${roleName}`} target="_blank" rel="noreferrer"><ExternalLink size={11} className="text-text-muted hover:text-primary ml-1" /></a></> },
            { label: 'Network', value: networkMode, extra: null },
            { label: 'Guardrail (L5)',
              value: rt.guardrailId ? `${rt.guardrailId} v${rt.guardrailVersion || '1'}` : '—',
              extra: rt.guardrailId
                ? <Badge color="warning"><Shield size={10} className="mr-0.5" />Active</Badge>
                : <Badge color="default">None</Badge> },
          ].map(row => (
            <div key={row.label} className="flex items-center justify-between rounded-xl bg-surface-dim px-3 py-2">
              <span className="text-xs text-text-muted">{row.label}</span>
              <div className="flex items-center gap-1.5">
                <span className="text-xs font-mono text-text-secondary">{row.value}</span>
                {row.extra}
              </div>
            </div>
          ))}
        </div>

        <div className="border-t border-dark-border/30 pt-3 grid grid-cols-2 gap-2">
          {[
            { label: 'Idle timeout', value: fmtTime(rt.idleTimeoutSec || 900), sub: 'No msg → microVM released' },
            { label: 'Max lifetime', value: fmtTime(rt.maxLifetimeSec || 28800), sub: 'Force restart ceiling' },
          ].map(s => (
            <div key={s.label} className="rounded-xl bg-surface-dim px-3 py-2.5">
              <p className="text-[10px] text-text-muted">{s.label}</p>
              <p className="text-base font-bold text-text-primary">{s.value}</p>
              <p className="text-[10px] text-text-muted">{s.sub}</p>
            </div>
          ))}
        </div>

        {/* Assigned Positions */}
        {(() => {
          const assignedPositions = Object.entries(runtimeMap)
            .filter(([_, rid]) => rid === rt.id)
            .map(([posId]) => positions.find(p => p.id === posId))
            .filter(Boolean);
          if (assignedPositions.length === 0) return null;
          return (
            <div className="border-t border-dark-border/30 pt-3 mt-3">
              <p className="text-[10px] text-text-muted uppercase tracking-wider mb-2">Assigned Positions ({assignedPositions.length})</p>
              <div className="flex flex-wrap gap-1.5">
                {assignedPositions.map((p: any) => (
                  <Badge key={p.id} color={isExec ? 'warning' : 'primary'}>{p.name}</Badge>
                ))}
              </div>
            </div>
          );
        })()}
      </Card>

      {showEdit && <RuntimeEditModal rt={rt} models={models} onClose={() => setShowEdit(false)} awsRegion={awsRegion} />}
    </>
  );
}

// ─── Position Policy Row ──────────────────────────────────────────────────────

function PositionPolicyRow({ pos, onEditSoul, onEditTools }: {
  pos: any;
  onEditSoul: (pos: any) => void;
  onEditTools: (pos: any) => void;
}) {
  const { data: tools } = usePositionTools(pos.id);
  const allowedCount = tools?.tools?.length || 0;

  return (
    <div className="flex items-center gap-3 px-4 py-3 hover:bg-dark-hover/30 transition-colors rounded-xl">
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-text-primary">{pos.name}</p>
        <p className="text-xs text-text-muted">{pos.departmentName}</p>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <Badge color={allowedCount > 3 ? 'warning' : 'info'}>{allowedCount} tools</Badge>
        <Button size="sm" variant="ghost" onClick={() => onEditSoul(pos)}>
          <FileText size={12} /> SOUL
        </Button>
        <Button size="sm" variant="ghost" onClick={() => onEditTools(pos)}>
          <Wrench size={12} /> Tools
        </Button>
      </div>
    </div>
  );
}

// ─── SOUL Edit Modal ──────────────────────────────────────────────────────────

function SoulEditModal({ pos, onClose }: { pos: any | null; onClose: () => void }) {
  const isGlobal = !pos;
  const { data: globalSoul } = useGlobalSoul();
  const { data: posSoul } = usePositionSoul(pos?.id || '');
  const updateGlobal = useUpdateGlobalSoul();
  const updatePos = useUpdatePositionSoul();
  const [content, setContent] = useState('');
  const [saving, setSaving] = useState(false);

  const soul = isGlobal ? globalSoul : posSoul;
  useEffect(() => { if (soul?.content !== undefined) setContent(soul.content); }, [soul?.content]);

  const handleSave = async () => {
    setSaving(true);
    try {
      if (isGlobal) await updateGlobal.mutateAsync(content);
      else await updatePos.mutateAsync({ posId: pos.id, content });
      onClose();
    } catch {}
    setSaving(false);
  };

  return (
    <Modal open={true} onClose={onClose}
      title={isGlobal ? 'Global SOUL.md — All Agents' : `SOUL.md — ${pos?.name}`}
      footer={
        <div className="flex justify-end gap-3">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={saving} onClick={handleSave}>
            {saving ? 'Saving…' : 'Save to S3'}
          </Button>
        </div>
      }
    >
      <p className="text-xs text-text-muted mb-3">
        {isGlobal
          ? 'This SOUL.md applies to ALL agents as the base layer. Position and personal SOUL layers are merged on top.'
          : `This SOUL.md applies to all agents with position "${pos?.name}". Merged above the global layer.`}
      </p>
      <div className="text-[10px] font-mono text-text-muted mb-1">{soul?.key}</div>
      <textarea
        value={content}
        onChange={e => setContent(e.target.value)}
        rows={18}
        className="w-full rounded-xl border border-dark-border/60 bg-dark-bg px-4 py-3 text-sm font-mono text-text-primary placeholder:text-text-muted focus:border-primary/60 focus:outline-none resize-y"
        placeholder="# ACME Corp — Digital Employee Policy&#10;&#10;You are a digital employee of ACME Corp..."
      />
    </Modal>
  );
}

// ─── Tools Edit Modal ─────────────────────────────────────────────────────────

function ToolsEditModal({ pos, onClose }: { pos: any; onClose: () => void }) {
  const { data: current } = usePositionTools(pos.id);
  const updateTools = useUpdatePositionTools();
  const [selected, setSelected] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (current?.tools) setSelected(current.tools);
  }, [current?.tools]);

  const toggle = (t: string) => setSelected(s => s.includes(t) ? s.filter(x => x !== t) : [...s, t]);

  const profile = selected.length === ALL_TOOLS.length ? 'exec'
    : selected.includes('shell') ? 'advanced' : 'basic';

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateTools.mutateAsync({ posId: pos.id, profile, tools: selected });
      onClose();
    } catch {}
    setSaving(false);
  };

  return (
    <Modal open={true} onClose={onClose} title={`Tool Permissions — ${pos.name}`}
      footer={
        <div className="flex items-center justify-between">
          <span className="text-xs text-text-muted">Propagates to all employees in this position</span>
          <div className="flex gap-3">
            <Button variant="default" onClick={onClose}>Cancel</Button>
            <Button variant="primary" disabled={saving} onClick={handleSave}>
              {saving ? 'Saving…' : 'Save & Propagate'}
            </Button>
          </div>
        </div>
      }
    >
      <p className="text-xs text-text-muted mb-4">
        Select which tools agents in <strong className="text-text-primary">{pos.name}</strong> may use.
        Writing saves to SSM for every employee in this position.
      </p>
      <div className="space-y-2">
        {ALL_TOOLS.map(t => {
          const on = selected.includes(t);
          const alwaysOn = t === 'web_search';
          return (
            <label key={t}
              className={`flex items-center gap-3 rounded-xl px-4 py-3 cursor-pointer transition-colors ${on ? 'bg-primary/10 border border-primary/30' : 'bg-surface-dim border border-transparent hover:border-dark-border/50'} ${alwaysOn ? 'opacity-70 cursor-not-allowed' : ''}`}>
              <input type="checkbox" checked={on} disabled={alwaysOn}
                onChange={() => !alwaysOn && toggle(t)} className="accent-primary" />
              <div className="flex-1">
                <p className="text-sm font-medium text-text-primary">{TOOL_LABELS[t]}</p>
                <p className="text-xs text-text-muted">
                  {t === 'web_search' && 'Always enabled — required for basic functionality'}
                  {t === 'shell' && 'Execute shell commands on the agent microVM'}
                  {t === 'browser' && 'Headless browser for web scraping and form interaction'}
                  {t === 'file' && 'Read files from agent workspace'}
                  {t === 'file_write' && 'Create and write files in agent workspace'}
                  {t === 'code_execution' && 'Run Python/Node.js code in sandboxed environment'}
                </p>
              </div>
              {on ? <CheckCircle size={16} className="text-primary shrink-0" /> : <div className="w-4 h-4 rounded-full border border-dark-border shrink-0" />}
            </label>
          );
        })}
      </div>
      <div className="mt-3 rounded-xl bg-surface-dim px-3 py-2 flex items-center gap-2">
        <span className="text-xs text-text-muted">Effective profile:</span>
        <Badge color={profile === 'exec' ? 'warning' : profile === 'advanced' ? 'primary' : 'default'}>{profile}</Badge>
      </div>
    </Modal>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

// ─── LLM Provider Tab ────────────────────────────────────────────────────────

function LLMProviderTab({ positions }: { positions: any[] }) {
  const { data: mc } = useModelConfig();
  const updateDefault = useUpdateModelConfig();
  const updateFallback = useUpdateFallbackModel();
  const setPositionModel = useSetPositionModel();
  const removePositionModel = useRemovePositionModel();
  const [showModal, setShowModal] = useState<'default' | 'fallback' | 'override' | null>(null);
  const [selectedModelId, setSelectedModelId] = useState('');
  const [overridePosId, setOverridePosId] = useState('');
  const [overrideReason, setOverrideReason] = useState('');

  const m = mc || { default: { modelId: '', modelName: 'Loading...', inputRate: 0, outputRate: 0 }, fallback: { modelId: '', modelName: '', inputRate: 0, outputRate: 0 }, positionOverrides: {}, availableModels: [] };
  const modelOptions = m.availableModels.map((mo: any) => ({ label: `${mo.modelName} ($${mo.inputRate}/$${mo.outputRate})`, value: mo.modelId }));
  const findModel = (id: string) => m.availableModels.find((mo: any) => mo.modelId === id);

  const handleSave = () => {
    const model = findModel(selectedModelId);
    if (!model) return;
    if (showModal === 'default') updateDefault.mutate({ modelId: model.modelId, modelName: model.modelName, inputRate: model.inputRate, outputRate: model.outputRate });
    else if (showModal === 'fallback') updateFallback.mutate({ modelId: model.modelId, modelName: model.modelName, inputRate: model.inputRate, outputRate: model.outputRate });
    else if (showModal === 'override' && overridePosId) setPositionModel.mutate({ posId: overridePosId, modelId: model.modelId, modelName: model.modelName, inputRate: model.inputRate, outputRate: model.outputRate, reason: overrideReason || 'Custom model for position' });
    setShowModal(null); setSelectedModelId(''); setOverridePosId(''); setOverrideReason('');
  };

  return (
    <div className="space-y-6">
      <div className="rounded-xl bg-info/5 border border-info/20 px-4 py-3 text-xs text-info">
        Model changes take effect on the next agent cold start (~15 min idle timeout).
      </div>
      {/* Default + Fallback */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {[
          { title: 'Default Model', model: m.default, action: () => { setShowModal('default'); setSelectedModelId(m.default.modelId); }, color: 'primary' as const },
          { title: 'Fallback Model', model: m.fallback, action: () => { setShowModal('fallback'); setSelectedModelId(m.fallback.modelId); }, color: 'warning' as const },
        ].map(r => (
          <Card key={r.title}>
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold">{r.title}</h3>
              <Button variant="primary" size="sm" onClick={r.action}>Change</Button>
            </div>
            <div className="rounded-xl bg-surface-dim p-3 space-y-1.5">
              <p className="text-base font-semibold text-text-primary">{r.model.modelName || '—'}</p>
              <p className="text-xs font-mono text-text-muted">{r.model.modelId}</p>
              <div className="flex gap-2 mt-1">
                <Badge color={r.color}>In: ${r.model.inputRate}/1M</Badge>
                <Badge color={r.color}>Out: ${r.model.outputRate}/1M</Badge>
              </div>
            </div>
          </Card>
        ))}
      </div>
      {/* Per-Position Overrides */}
      <Card>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-sm font-semibold">Per-Position Model Overrides</h3>
            <p className="text-xs text-text-muted">Override the default model for specific positions</p>
          </div>
          <Button variant="primary" size="sm" onClick={() => setShowModal('override')}><Plus size={14} /> Add Override</Button>
        </div>
        {Object.keys(m.positionOverrides).length === 0 ? (
          <p className="text-sm text-text-muted text-center py-6">No overrides — all positions use the default model</p>
        ) : (
          <div className="divide-y divide-dark-border/30">
            {Object.entries(m.positionOverrides).map(([posId, ov]: [string, any]) => (
              <div key={posId} className="flex items-center justify-between py-3">
                <div>
                  <p className="text-sm font-medium">{positions.find(p => p.id === posId)?.name || posId}</p>
                  <p className="text-xs text-text-muted">{ov.modelName} · {ov.reason}</p>
                </div>
                <Button variant="ghost" size="sm" onClick={() => removePositionModel.mutate(posId)}>Remove</Button>
              </div>
            ))}
          </div>
        )}
      </Card>
      {/* Available Models */}
      <Card>
        <h3 className="text-sm font-semibold mb-4">Available Models</h3>
        <div className="space-y-2">
          {m.availableModels.map((mo: any) => {
            const isDefault = mo.modelId === m.default.modelId;
            const isFallback = mo.modelId === m.fallback.modelId;
            return (
              <div key={mo.modelId} className={`flex items-center justify-between rounded-xl px-4 py-3 ${isDefault ? 'bg-primary/5 border border-primary/20' : isFallback ? 'bg-warning/5 border border-warning/20' : 'bg-surface-dim'}`}>
                <div className="flex items-center gap-3">
                  <div className={`w-2 h-2 rounded-full ${mo.enabled ? 'bg-success' : 'bg-text-muted'}`} />
                  <div>
                    <p className="text-sm font-medium">{mo.modelName}</p>
                    <p className="text-xs font-mono text-text-muted">{mo.modelId}</p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-text-muted">${mo.inputRate} / ${mo.outputRate}</span>
                  {isDefault && <Badge color="primary">Default</Badge>}
                  {isFallback && <Badge color="warning">Fallback</Badge>}
                  {!isDefault && !isFallback && mo.enabled && (
                    <Button variant="ghost" size="sm" onClick={() => updateDefault.mutate({ modelId: mo.modelId, modelName: mo.modelName, inputRate: mo.inputRate, outputRate: mo.outputRate })}>Set Default</Button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </Card>
      {/* Modal */}
      {showModal && (
        <Modal open={true} onClose={() => setShowModal(null)}
          title={showModal === 'default' ? 'Change Default Model' : showModal === 'fallback' ? 'Change Fallback Model' : 'Add Position Override'}
          footer={<div className="flex justify-end gap-3"><Button variant="default" onClick={() => setShowModal(null)}>Cancel</Button><Button variant="primary" onClick={handleSave}>Apply</Button></div>}>
          <div className="space-y-4">
            {showModal === 'override' && (
              <Select label="Position" value={overridePosId} onChange={setOverridePosId}
                options={positions.filter(p => !(m?.positionOverrides as any)?.[p.id]).map(p => ({ label: p.name, value: p.id }))}
                placeholder="Select position" />
            )}
            <Select label="Model" value={selectedModelId} onChange={setSelectedModelId} options={modelOptions} placeholder="Select model" />
            {showModal === 'override' && (
              <div>
                <label className="mb-1.5 block text-xs font-medium text-text-secondary">Reason</label>
                <input value={overrideReason} onChange={e => setOverrideReason(e.target.value)}
                  className="w-full rounded-xl border border-dark-border/60 bg-surface-dim px-4 py-2.5 text-sm text-text-primary focus:border-primary/60 focus:outline-none"
                  placeholder="e.g. Needs reasoning for architecture review" />
              </div>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}

// ─── Position Runtime Table ───────────────────────────────────────────────────

function rtLabel(rt: any): string {
  const name: string = rt?.name || rt?.id || '';
  if (name.toLowerCase().includes('exec')) return 'Executive Runtime';
  if (name.toLowerCase().includes('standard') || name.includes('runtime-ol')) return 'Standard Runtime';
  return name.replace(/^openclaw_\w+_/, '').replace(/_runtime.*/, ' Runtime');
}

function PositionRuntimeRow({ pos, assignedId, runtimes, runtimeOptions, onAssign, onRemove, saving }: {
  pos: any; assignedId: string; runtimes: any[]; runtimeOptions: { label: string; value: string }[];
  onAssign: (runtimeId: string) => void; onRemove: () => void; saving: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [pendingId, setPendingId] = useState('');
  const [saved, setSaved] = useState(false);
  const assignedRt = runtimes.find(r => r.id === assignedId);
  const isExec = assignedRt?.containerUri?.includes('exec') || assignedRt?.name?.toLowerCase().includes('exec');

  const handleSave = () => {
    if (!pendingId || pendingId === assignedId) { setEditing(false); return; }
    onAssign(pendingId);
    setEditing(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

  return (
    <div className="flex items-center gap-3 rounded-xl bg-surface-dim px-4 py-3">
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-text-primary">{pos.name}</p>
        <p className="text-xs text-text-muted">{pos.departmentName}</p>
      </div>

      {editing ? (
        <div className="flex items-center gap-2 shrink-0">
          <select
            value={pendingId || assignedId}
            onChange={e => setPendingId(e.target.value)}
            className="rounded-xl border border-primary/40 bg-dark-bg px-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-primary"
          >
            <option value="">— Default (Standard) —</option>
            {runtimeOptions.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <Button size="sm" variant="primary" disabled={saving} onClick={handleSave}>
            {saving ? <RefreshCw size={12} className="animate-spin" /> : <Save size={12} />} Save
          </Button>
          <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setPendingId(''); }}>
            <X size={12} />
          </Button>
        </div>
      ) : (
        <div className="flex items-center gap-2 shrink-0">
          {saved && <span className="text-[10px] text-success flex items-center gap-1"><CheckCircle size={12} /> Saved</span>}
          {assignedRt ? (
            <Badge color={isExec ? 'warning' : 'info'}>{rtLabel(assignedRt)}</Badge>
          ) : (
            <Badge color="default">Default (Standard)</Badge>
          )}
          <Button size="sm" variant="ghost" onClick={() => { setEditing(true); setPendingId(assignedId); }}>
            <Edit3 size={12} /> Change
          </Button>
        </div>
      )}
    </div>
  );
}

function PositionRuntimeTable({ positions, runtimes, runtimeMap, runtimeOptions, onAssign, onRemove, saving }: {
  positions: any[]; runtimes: any[]; runtimeMap: Record<string, string>;
  runtimeOptions: { label: string; value: string }[];
  onAssign: (posId: string, runtimeId: string) => void;
  onRemove: (posId: string) => void;
  saving: boolean;
}) {
  return (
    <div className="space-y-2">
      {positions.map(pos => (
        <PositionRuntimeRow
          key={pos.id}
          pos={pos}
          assignedId={runtimeMap[pos.id] || ''}
          runtimes={runtimes}
          runtimeOptions={runtimeOptions.map(o => ({ ...o, label: rtLabel(runtimes.find(r => r.id === o.value) || { name: o.label }) }))}
          onAssign={(rid) => onAssign(pos.id, rid)}
          onRemove={() => onRemove(pos.id)}
          saving={saving}
        />
      ))}
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function SecurityCenter() {
  const [tab, setTab] = useState('runtimes');
  const { data: runtimesData, isLoading: rtLoading } = useSecurityRuntimes();
  const { data: positions = [] } = usePositions();
  const { data: modelConfig } = useModelConfig();
  const { data: infra } = useInfrastructure();
  const { data: serviceData } = useServiceStatus();
  const awsRegion = serviceData?.bedrock?.region || (serviceData as any)?.platform?.awsRegion || 'us-east-1';

  const [soulTarget, setSoulTarget] = useState<any | null | undefined>(undefined);
  const [toolsTarget, setToolsTarget] = useState<any | null>(null);
  const [showCreateRuntime, setShowCreateRuntime] = useState(false);
  const { data: posRuntimeMap, isLoading: mapLoading } = usePositionRuntimeMap();
  const setPositionRuntime = useSetPositionRuntime();
  const deletePositionRuntime = useDeletePositionRuntime();

  const runtimes = runtimesData?.runtimes || [];
  const models = modelConfig?.availableModels || [];
  const runtimeMap = posRuntimeMap?.map || {};

  const runtimeOptions = runtimes.map(rt => ({
    label: rt.name || rt.id,
    value: rt.id,
  }));

  return (
    <div>
      <PageHeader
        title="Security Center"
        description="Configure agent runtimes, security policies, and AWS infrastructure for the entire platform"
      />

      <Tabs
        tabs={[
          { id: 'runtimes', label: 'Agent Runtimes' },
          { id: 'fargate', label: 'Fargate Agents' },
          { id: 'policies', label: 'Security Policies' },
          { id: 'infrastructure', label: 'Infrastructure' },
        ]}
        activeTab={tab}
        onChange={setTab}
      />

      <div className="mt-6">

        {/* ── Runtimes ── */}
        {tab === 'runtimes' && (
          <div className="space-y-6">
            <div className="flex items-center justify-between">
              <div className="rounded-xl bg-info/5 border border-info/20 px-4 py-3 text-xs text-info flex-1 mr-4">
                Each Runtime has its own Docker image, IAM role, and lifecycle settings.
                Employees route to runtimes based on their position.
                IAM constraints cannot be bypassed by prompt injection.
              </div>
              <Button variant="primary" onClick={() => setShowCreateRuntime(true)}>
                <Plus size={15} /> New Runtime
              </Button>
            </div>

            {rtLoading ? (
              <div className="flex justify-center py-12">
                <RefreshCw size={24} className="animate-spin text-text-muted" />
              </div>
            ) : runtimes.length === 0 ? (
              <Card>
                <div className="text-center py-8 text-text-muted">
                  <Cpu size={32} className="mx-auto mb-3 opacity-30" />
                  <p className="text-sm">No runtimes found</p>
                  <p className="text-xs mt-1">{runtimesData?.error || 'Check AWS credentials and region'}</p>
                </div>
              </Card>
            ) : (
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {runtimes.map(rt => (
                  <RuntimeCard key={rt.id} rt={rt} models={models} awsRegion={awsRegion} runtimeMap={runtimeMap} positions={positions} />
                ))}
              </div>
            )}

            {/* Defense Layers */}
            <Card>
              <div className="flex items-center gap-2 mb-4">
                <Shield size={18} className="text-primary" />
                <h3 className="text-sm font-semibold text-text-primary">Defense in Depth — Access Control Layers</h3>
              </div>
              <div className="space-y-2">
                {[
                  { layer: 'L1', name: 'Prompt', color: 'warning', desc: 'SOUL.md rules ("never access finance data")', note: 'Prompt-level · Can be bypassed by injection', strong: false },
                  { layer: 'L2', name: 'Application', color: 'warning', desc: 'Skills manifest allowedRoles / blockedRoles', note: 'App-level · Code bug risk', strong: false },
                  { layer: 'L3', name: 'IAM Role', color: 'success', desc: 'Runtime execution role has no permission on target resource', note: 'Infrastructure · Cannot be bypassed', strong: true },
                  { layer: 'L4', name: 'Network', color: 'success', desc: 'VPC isolation between Runtimes', note: 'Infrastructure · Cannot be bypassed', strong: true },
                  { layer: 'L5', name: 'Bedrock Guardrail', color: 'success', desc: 'Content policy: topic denial, PII filtering, compliance guardrails on every input + output', note: 'AWS-managed · Semantically aware · Cannot be bypassed', strong: true },
                ].map(l => (
                  <div key={l.layer} className={`flex items-center gap-4 rounded-xl px-4 py-3 ${l.strong ? 'bg-success/5 border border-success/20' : 'bg-surface-dim border border-transparent'}`}>
                    <div className={`w-2 h-2 rounded-full ${l.strong ? 'bg-success' : 'bg-warning'} shrink-0`} />
                    <div className="flex-1 min-w-0">
                      <span className={`text-sm font-semibold ${l.strong ? 'text-success' : 'text-text-primary'}`}>{l.layer} — {l.name}</span>
                      <span className="text-xs text-text-muted ml-2">{l.desc}</span>
                    </div>
                    <span className={`text-xs shrink-0 ${l.strong ? 'text-success' : 'text-text-muted'}`}>{l.note}</span>
                  </div>
                ))}
              </div>
            </Card>

            {/* ── Position → Runtime Mapping Table ── */}
            <Card>
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h3 className="text-sm font-semibold text-text-primary">Position → Runtime Assignments</h3>
                  <p className="text-xs text-text-muted">
                    Each position routes to its assigned runtime. Employee-level override takes precedence.
                    Changes propagate to all employees in the position via SSM.
                  </p>
                </div>
                {mapLoading && <RefreshCw size={14} className="animate-spin text-text-muted" />}
              </div>
              <PositionRuntimeTable
                positions={positions}
                runtimes={runtimes}
                runtimeMap={runtimeMap}
                runtimeOptions={runtimeOptions}
                onAssign={(posId, runtimeId) => setPositionRuntime.mutate({ posId, runtimeId })}
                onRemove={(posId) => deletePositionRuntime.mutate(posId)}
                saving={setPositionRuntime.isPending}
              />
              <div className="mt-3 rounded-xl bg-info/5 border border-info/20 px-3 py-2 text-xs text-info">
                Takes effect on the next agent cold start (~15 min idle). Currently active microVMs are not affected.
              </div>
            </Card>
          </div>
        )}

        {/* ── Fargate Agents ── */}
        {tab === 'fargate' && <FargateOverviewPanel />}

        {/* ── Policies ── */}
        {tab === 'policies' && (
          <div className="space-y-6">
            {/* Global SOUL */}
            <Card>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Globe2 size={18} className="text-primary" />
                  <div>
                    <h3 className="text-sm font-semibold text-text-primary">Global SOUL.md</h3>
                    <p className="text-xs text-text-muted">Applies to every agent as the base layer · S3: _shared/soul/global/SOUL.md</p>
                  </div>
                </div>
                <Button variant="primary" size="sm" onClick={() => setSoulTarget(null)}>
                  <Edit3 size={13} /> Edit Global SOUL
                </Button>
              </div>
              <div className="rounded-xl bg-surface-dim px-4 py-3 text-xs text-text-muted">
                Layer 1 of 3: Global → Position → Personal. All employees inherit the global SOUL.
                Override per-position below for role-specific policies.
              </div>
            </Card>

            {/* Per-Position Policies */}
            <Card>
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h3 className="text-sm font-semibold text-text-primary">Per-Position Security Policies</h3>
                  <p className="text-xs text-text-muted">Configure SOUL layer and tool permissions for each position</p>
                </div>
              </div>
              <div className="divide-y divide-dark-border/30">
                {positions.map(pos => (
                  <PositionPolicyRow key={pos.id} pos={pos}
                    onEditSoul={p => setSoulTarget(p)}
                    onEditTools={p => setToolsTarget(p)}
                  />
                ))}
              </div>
            </Card>

            {/* Always-Blocked */}
            <Card>
              <div className="flex items-center gap-2 mb-3">
                <Lock size={16} className="text-danger" />
                <h3 className="text-sm font-semibold text-text-primary">Always Blocked — Hard Limits</h3>
              </div>
              <p className="text-xs text-text-muted mb-3">
                These tools are blocked in code for ALL roles. They cannot be unlocked via SOUL, permissions, or Admin approval.
              </p>
              <div className="flex flex-wrap gap-2">
                {['install_skill', 'load_extension', 'eval'].map(t => (
                  <div key={t} className="flex items-center gap-2 rounded-full bg-danger/10 border border-danger/20 px-3 py-1">
                    <Lock size={11} className="text-danger" />
                    <span className="text-xs font-mono text-danger">{t}</span>
                  </div>
                ))}
              </div>
            </Card>
          </div>
        )}

        {/* ── Infrastructure ── */}
        {tab === 'infrastructure' && (
          <div className="space-y-6">
            {/* ECR Images */}
            <Card>
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <Package size={18} className="text-primary" />
                  <h3 className="text-sm font-semibold text-text-primary">Docker Images (ECR)</h3>
                  <Badge color="info">{infra?.ecrImages?.length || 0} images</Badge>
                </div>
                <a href={`https://console.aws.amazon.com/ecr/repositories?region=${awsRegion}`} target="_blank" rel="noreferrer">
                  <Button size="sm" variant="ghost"><ExternalLink size={13} /> Open ECR Console</Button>
                </a>
              </div>
              {!infra ? (
                <div className="flex items-center justify-center py-6"><RefreshCw size={18} className="animate-spin text-text-muted" /></div>
              ) : (infra.ecrImages || []).length === 0 ? (
                <p className="text-xs text-text-muted py-4 text-center">No ECR images found</p>
              ) : (
                <div className="space-y-2">
                  {(infra.ecrImages || []).map((img: any, i: number) => (
                    <div key={i} className="flex items-center justify-between rounded-xl bg-surface-dim px-4 py-3">
                      <div className="min-w-0">
                        <p className="text-sm font-mono text-text-primary">{img.repo}:{img.tag}</p>
                        <p className="text-xs text-text-muted font-mono">{img.digest} · {fmtBytes(img.sizeBytes)} · pushed {img.pushedAt?.slice(0, 10)}</p>
                        <p className="text-[10px] text-text-muted mt-0.5 truncate">{img.uri}</p>
                      </div>
                      <div className="flex items-center gap-2 ml-4">
                        <Badge color="success">Available</Badge>
                        <a href={`https://console.aws.amazon.com/ecr/repositories/private/${img.uri?.split('/')[0]?.split('.')[0]}/${img.repo}?region=${awsRegion}`} target="_blank" rel="noreferrer">
                          <Button size="sm" variant="ghost"><ExternalLink size={11} /></Button>
                        </a>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>

            {/* IAM Roles */}
            <Card>
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <Key size={18} className="text-primary" />
                  <h3 className="text-sm font-semibold text-text-primary">IAM Roles</h3>
                  <Badge color="info">{(infra?.iamRoles || []).filter((r: any) => r.relevant).length} AgentCore</Badge>
                </div>
                <a href="https://console.aws.amazon.com/iam/home#/roles" target="_blank" rel="noreferrer">
                  <Button size="sm" variant="ghost"><ExternalLink size={13} /> Open IAM Console</Button>
                </a>
              </div>
              {!infra ? (
                <div className="flex items-center justify-center py-6"><RefreshCw size={18} className="animate-spin text-text-muted" /></div>
              ) : (
                <div className="space-y-1.5">
                  {/* AgentCore roles first */}
                  {(infra.iamRoles || []).filter((r: any) => r.relevant).map((r: any) => (
                    <div key={r.arn} className="flex items-center justify-between rounded-xl bg-primary/5 border border-primary/20 px-4 py-3">
                      <div>
                        <p className="text-sm font-medium text-text-primary">{r.name}</p>
                        <p className="text-[10px] text-text-muted font-mono">{r.arn}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        <Badge color={r.name.includes('exec') ? 'danger' : 'info'}>{r.name.includes('exec') ? 'Full Access' : 'Scoped'}</Badge>
                        <a href={`https://console.aws.amazon.com/iam/home#/roles/${r.name}`} target="_blank" rel="noreferrer">
                          <Button size="sm" variant="ghost"><ExternalLink size={11} /></Button>
                        </a>
                      </div>
                    </div>
                  ))}
                  {/* Other roles collapsed */}
                  {(infra.iamRoles || []).filter((r: any) => !r.relevant).length > 0 && (
                    <details className="group">
                      <summary className="text-xs text-text-muted cursor-pointer px-4 py-2 hover:text-text-primary">
                        + {(infra.iamRoles || []).filter((r: any) => !r.relevant).length} other roles
                      </summary>
                      <div className="space-y-1 mt-1">
                        {(infra.iamRoles || []).filter((r: any) => !r.relevant).map((r: any) => (
                          <div key={r.arn} className="flex items-center justify-between rounded-xl bg-surface-dim px-4 py-2.5">
                            <div>
                              <p className="text-sm text-text-secondary">{r.name}</p>
                              <p className="text-[10px] text-text-muted font-mono">{r.arn}</p>
                            </div>
                            <a href={`https://console.aws.amazon.com/iam/home#/roles/${r.name}`} target="_blank" rel="noreferrer">
                              <Button size="sm" variant="ghost"><ExternalLink size={11} /></Button>
                            </a>
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                </div>
              )}
            </Card>

            {/* VPC / Security Groups */}
            <Card>
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <Network size={18} className="text-primary" />
                  <h3 className="text-sm font-semibold text-text-primary">VPC & Security Groups</h3>
                </div>
                <div className="flex gap-2">
                  <a href={`https://console.aws.amazon.com/vpc/home?region=${awsRegion}#SecurityGroups:`} target="_blank" rel="noreferrer">
                    <Button size="sm" variant="ghost"><ExternalLink size={13} /> Create SG</Button>
                  </a>
                  <a href={`https://console.aws.amazon.com/vpc/home?region=${awsRegion}#vpcs:`} target="_blank" rel="noreferrer">
                    <Button size="sm" variant="ghost"><ExternalLink size={13} /> VPC Console</Button>
                  </a>
                </div>
              </div>
              {!infra ? (
                <div className="flex items-center justify-center py-6"><RefreshCw size={18} className="animate-spin text-text-muted" /></div>
              ) : (
                <div className="space-y-3">
                  {/* VPCs */}
                  <div>
                    <p className="text-xs font-medium text-text-muted mb-2">VPCs ({(infra.vpcs || []).length})</p>
                    <div className="space-y-1.5">
                      {(infra.vpcs || []).map((v: any) => (
                        <div key={v.id} className={`flex items-center justify-between rounded-xl px-4 py-2.5 ${v.isDefault ? 'bg-surface-dim' : 'bg-primary/5 border border-primary/20'}`}>
                          <div>
                            <p className="text-sm font-medium text-text-primary">{v.name}</p>
                            <p className="text-xs text-text-muted font-mono">{v.id} · {v.cidr}</p>
                          </div>
                          <Badge color={v.isDefault ? 'default' : 'primary'}>{v.isDefault ? 'Default' : 'Custom'}</Badge>
                        </div>
                      ))}
                    </div>
                  </div>
                  {/* Security Groups */}
                  <div>
                    <p className="text-xs font-medium text-text-muted mb-2">Security Groups ({(infra.securityGroups || []).length})</p>
                    <div className="space-y-1.5">
                      {(infra.securityGroups || []).map((sg: any) => (
                        <div key={sg.id} className={`flex items-center justify-between rounded-xl px-4 py-2.5 ${sg.relevant ? 'bg-primary/5 border border-primary/20' : 'bg-surface-dim'}`}>
                          <div>
                            <p className="text-sm font-medium text-text-primary">{sg.name}</p>
                            <p className="text-xs text-text-muted">{sg.id} · {sg.description?.slice(0, 50)}</p>
                          </div>
                          <div className="flex items-center gap-2">
                            {sg.relevant && <Badge color="primary">AgentCore</Badge>}
                            <a href={`https://console.aws.amazon.com/vpc/home?region=${awsRegion}#SecurityGroup:groupId=${sg.id}`} target="_blank" rel="noreferrer">
                              <Button size="sm" variant="ghost"><ExternalLink size={11} /></Button>
                            </a>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </Card>
          </div>
        )}
      </div>

      {/* SOUL Edit Modal */}
      {soulTarget !== undefined && (
        <SoulEditModal pos={soulTarget} onClose={() => setSoulTarget(undefined)} />
      )}
      {/* Tools Edit Modal */}
      {toolsTarget && (
        <ToolsEditModal pos={toolsTarget} onClose={() => setToolsTarget(null)} />
      )}
      {/* Create Runtime Modal */}
      {showCreateRuntime && (
        <CreateRuntimeModal models={models} onClose={() => setShowCreateRuntime(false)} awsRegion={awsRegion} />
      )}
    </div>
  );
}


// ── Fargate Overview Panel ────────────────────────────────────────────────

function FargateOverviewPanel() {
  const { data, isLoading } = useFargateOverview() as { data: any; isLoading: boolean };
  const agents = data?.alwaysOnAgents || [];

  const tierColors: Record<string, string> = {
    standard: 'info', restricted: 'warning', engineering: 'success', executive: 'primary',
  };

  return (
    <div className="space-y-6">
      <Card>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-lg font-semibold text-text-primary">Always-On Agents (Fargate)</h3>
            <p className="text-sm text-text-muted mt-1">
              Per-employee Fargate containers with dedicated Gateway, IM direct connection, and HEARTBEAT support
            </p>
          </div>
          <Badge color="primary">{agents.length} active</Badge>
        </div>

        {isLoading ? (
          <div className="flex justify-center py-8 text-text-muted">Loading...</div>
        ) : agents.length === 0 ? (
          <div className="rounded-lg bg-dark-bg p-6 text-center">
            <p className="text-text-muted">No always-on agents configured</p>
            <p className="text-xs text-text-muted mt-1">
              Enable always-on in Agent Factory → employee detail → Enable Always-On
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-dark-border">
                  <th className="text-left py-2 px-3 text-xs text-text-muted font-medium">Employee</th>
                  <th className="text-left py-2 px-3 text-xs text-text-muted font-medium">Position</th>
                  <th className="text-left py-2 px-3 text-xs text-text-muted font-medium">Tier</th>
                  <th className="text-left py-2 px-3 text-xs text-text-muted font-medium">Status</th>
                  <th className="text-left py-2 px-3 text-xs text-text-muted font-medium">IM Channels</th>
                  <th className="text-left py-2 px-3 text-xs text-text-muted font-medium">Service</th>
                </tr>
              </thead>
              <tbody>
                {agents.map((a: any) => (
                  <tr key={a.employeeId} className="border-b border-dark-border/50 hover:bg-dark-hover">
                    <td className="py-2 px-3 font-medium">{a.employeeName}</td>
                    <td className="py-2 px-3 text-text-secondary">{a.positionName}</td>
                    <td className="py-2 px-3">
                      <Badge color={(tierColors[a.tier] || 'default') as any}>{a.tier}</Badge>
                    </td>
                    <td className="py-2 px-3">
                      <Badge color={a.status === 'starting' || a.status === 'running' ? 'success' : 'danger'}>
                        {a.status}
                      </Badge>
                    </td>
                    <td className="py-2 px-3">
                      {(a.imChannels || []).length === 0
                        ? <span className="text-text-muted">—</span>
                        : (a.imChannels || []).map((ch: string) => (
                            <Badge key={ch} color="info">{ch}</Badge>
                          ))
                      }
                    </td>
                    <td className="py-2 px-3 font-mono text-xs text-text-muted">{a.serviceName}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
