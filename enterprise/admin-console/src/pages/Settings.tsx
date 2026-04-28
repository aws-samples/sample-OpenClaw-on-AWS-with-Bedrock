import { useState, useEffect } from 'react';
import {
  User, Globe, MessageSquare, Server,
  Eye, EyeOff, Check, X, RefreshCw, HardDrive,
  Cpu, MemoryStick, Wifi, WifiOff,
  Terminal, Trash2, RotateCw, Copy, History, KeyRound,
} from 'lucide-react';
import { Card, Badge, Button, PageHeader, Tabs, Select } from '../components/ui';
import {
  useAdminAssistant, useUpdateAdminAssistant,
  useChangeAdminPassword, useSystemStats, useServiceStatus, useModelConfig,
  usePlatformAccess, usePlatformLogs, useAdminHistory, useClearAdminHistory, useRestartService,
} from '../hooks/useApi';
import { useAuth } from '../contexts/AuthContext';

// ─── helpers ────────────────────────────────────────────────────────────────

function fmtBytes(b: number) {
  if (b > 1e9) return `${(b / 1e9).toFixed(1)} GB`;
  if (b > 1e6) return `${(b / 1e6).toFixed(0)} MB`;
  return `${b} B`;
}

function ProgressBar({ pct }: { pct: number }) {
  const barColor = pct > 85 ? 'bg-danger' : pct > 65 ? 'bg-warning' : 'bg-success';
  return (
    <div className="h-1.5 w-full rounded-full bg-dark-border/40 overflow-hidden">
      <div className={`h-full rounded-full transition-all ${barColor}`} style={{ width: `${Math.min(pct, 100)}%` }} />
    </div>
  );
}

function copyToClipboard(text: string) {
  navigator.clipboard.writeText(text).catch(() => {});
}

// ─── Account Tab ─────────────────────────────────────────────────────────────

function AccountTab() {
  const { user, authMode } = useAuth();
  const changePw = useChangeAdminPassword();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const handleChange = async () => {
    if (next.length < 8) { setMsg({ ok: false, text: 'Password must be at least 8 characters' }); return; }
    if (next !== confirm) { setMsg({ ok: false, text: 'Passwords do not match' }); return; }
    try {
      await changePw.mutateAsync(next);
      setMsg({ ok: true, text: 'Password updated successfully' });
      setCurrent(''); setNext(''); setConfirm('');
    } catch {
      setMsg({ ok: false, text: 'Failed to update password' });
    }
  };

  return (
    <div className="space-y-6 max-w-lg">
      <Card>
        <h3 className="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
          <User size={16} className="text-primary" /> Admin Profile
        </h3>
        <div className="space-y-3">
          {[
            { label: 'Name', value: user?.name || 'Admin' },
            { label: 'Role', value: 'Administrator' },
            { label: 'Employee ID', value: user?.id || '---' },
            { label: 'Email', value: user?.email || '---' },
            { label: 'Auth Mode', value: authMode === 'sso' ? 'Single Sign-On (OIDC)' : 'Password' },
          ].map(f => (
            <div key={f.label} className="flex items-center justify-between rounded-xl bg-surface-dim px-4 py-3">
              <span className="text-xs text-text-muted">{f.label}</span>
              <span className="text-sm text-text-primary font-medium">{f.value}</span>
            </div>
          ))}
        </div>
      </Card>

      {authMode === 'sso' ? (
        <div className="rounded-xl bg-info/5 border border-info/20 px-4 py-3 text-xs text-info">
          Authenticated via Single Sign-On (OIDC). To change your password, visit your identity provider's portal.
        </div>
      ) : (
        <Card>
          <h3 className="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
            <User size={16} className="text-primary" /> Change Password
          </h3>
          <div className="space-y-3">
            {[
              { label: 'Current password', value: current, set: setCurrent },
              { label: 'New password', value: next, set: setNext },
              { label: 'Confirm new password', value: confirm, set: setConfirm },
            ].map(f => (
              <div key={f.label}>
                <label className="mb-1.5 block text-xs font-medium text-text-secondary">{f.label}</label>
                <div className="relative">
                  <input
                    type={showPw ? 'text' : 'password'}
                    value={f.value}
                    onChange={e => f.set(e.target.value)}
                    className="w-full rounded-xl border border-dark-border/60 bg-surface-dim px-4 py-2.5 pr-10 text-sm text-text-primary focus:border-primary/60 focus:outline-none"
                  />
                  <button onClick={() => setShowPw(s => !s)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary">
                    {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
              </div>
            ))}
            {msg && (
              <div className={`flex items-center gap-2 rounded-xl px-3 py-2.5 text-xs ${msg.ok ? 'bg-success/10 text-success' : 'bg-danger/10 text-danger'}`}>
                {msg.ok ? <Check size={14} /> : <X size={14} />} {msg.text}
              </div>
            )}
            <Button variant="primary" className="w-full" disabled={changePw.isPending}
              onClick={handleChange}>
              {changePw.isPending ? 'Updating...' : 'Update Password'}
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}

// ─── Platform Access Tab ─────────────────────────────────────────────────────

function PlatformAccessTab() {
  const { data: access, isLoading } = usePlatformAccess();

  if (isLoading) return <div className="flex justify-center py-12"><RefreshCw size={20} className="animate-spin text-text-muted" /></div>;

  return (
    <div className="space-y-6 max-w-2xl">
      <Card>
        <h3 className="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
          <Terminal size={16} className="text-primary" /> SSM Session Manager Access
        </h3>
        <p className="text-xs text-text-muted mb-4">Connect to the EC2 instance via AWS Systems Manager. No SSH key required.</p>

        {access ? (
          <div className="space-y-4">
            <div className="rounded-xl bg-surface-dim p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-text-muted">Instance ID</span>
                <span className="text-sm font-mono text-text-primary">{access.instanceId}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-text-muted">Region</span>
                <span className="text-sm font-mono text-text-primary">{access.region}</span>
              </div>
            </div>

            <div>
              <label className="text-xs text-text-muted block mb-1.5">Start SSM Session</label>
              <div className="flex gap-2">
                <code className="flex-1 rounded-lg bg-dark-bg border border-dark-border px-3 py-2 text-xs font-mono text-text-primary overflow-x-auto">
                  {access.ssmCommand}
                </code>
                <Button variant="default" size="sm" onClick={() => copyToClipboard(access.ssmCommand)}>
                  <Copy size={13} />
                </Button>
              </div>
            </div>

            <div>
              <label className="text-xs text-text-muted block mb-1.5">Port Forward (Admin Console)</label>
              <div className="flex gap-2">
                <code className="flex-1 rounded-lg bg-dark-bg border border-dark-border px-3 py-2 text-xs font-mono text-text-primary overflow-x-auto">
                  {access.portForwardCommand}
                </code>
                <Button variant="default" size="sm" onClick={() => copyToClipboard(access.portForwardCommand)}>
                  <Copy size={13} />
                </Button>
              </div>
            </div>
          </div>
        ) : (
          <p className="text-sm text-text-muted">Platform access information not available.</p>
        )}
      </Card>
    </div>
  );
}

// ─── Platform Logs Tab ───────────────────────────────────────────────────────

function PlatformLogsTab() {
  const [service, setService] = useState('openclaw-admin');
  const [lines, setLines] = useState(50);
  const { data: logsData, refetch, isFetching } = usePlatformLogs(service, lines);
  const restartSvc = useRestartService();
  const [restartMsg, setRestartMsg] = useState('');

  const serviceOptions = [
    { label: 'Admin Console (openclaw-admin)', value: 'openclaw-admin' },
    { label: 'Tenant Router', value: 'tenant-router' },
    { label: 'Bedrock H2 Proxy', value: 'bedrock-proxy-h2' },
    { label: 'Fargate Containers (CloudWatch)', value: 'fargate-containers' },
  ];

  const handleRestart = async (svcName: string) => {
    setRestartMsg('');
    try {
      await restartSvc.mutateAsync(svcName);
      setRestartMsg(`${svcName} restarted successfully`);
      setTimeout(() => setRestartMsg(''), 3000);
    } catch {
      setRestartMsg(`Failed to restart ${svcName}`);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Select label="" value={service} onChange={setService} options={serviceOptions} placeholder="Select service" />
        <select value={lines} onChange={e => setLines(Number(e.target.value))}
          className="rounded-lg border border-dark-border bg-dark-bg px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none">
          <option value={25}>25 lines</option>
          <option value={50}>50 lines</option>
          <option value={100}>100 lines</option>
          <option value={200}>200 lines</option>
        </select>
        <Button variant="default" size="sm" onClick={() => refetch()} disabled={isFetching}>
          {isFetching ? <RefreshCw size={13} className="animate-spin" /> : <RefreshCw size={13} />} Fetch Logs
        </Button>
        <Button variant="default" size="sm" onClick={() => handleRestart(service)} disabled={restartSvc.isPending}>
          <RotateCw size={13} /> Restart Service
        </Button>
      </div>

      {restartMsg && (
        <div className={`rounded-lg px-3 py-2 text-xs ${restartMsg.includes('success') ? 'bg-success/10 text-success' : 'bg-danger/10 text-danger'}`}>
          {restartMsg}
        </div>
      )}

      <Card>
        {logsData ? (
          <pre className="text-xs font-mono text-text-secondary whitespace-pre-wrap overflow-x-auto max-h-[500px] overflow-y-auto">
            {logsData.logs || '(no logs)'}
          </pre>
        ) : (
          <div className="text-center py-8 text-text-muted">
            <Terminal size={24} className="mx-auto mb-2 opacity-50" />
            <p className="text-sm">Click "Fetch Logs" to load journalctl output</p>
          </div>
        )}
      </Card>
    </div>
  );
}

// ─── Admin Assistant Tab ──────────────────────────────────────────────────────

function AdminAssistantTab() {
  const { data: cfg } = useAdminAssistant();
  const update = useUpdateAdminAssistant();
  const { data: mc } = useModelConfig();
  const [model, setModel] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [maxHistory, setMaxHistory] = useState(20);
  const [maxTokens, setMaxTokens] = useState(4096);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (cfg) {
      setModel(cfg.model || '');
      setSystemPrompt(cfg.systemPrompt || '');
      setMaxHistory(cfg.maxHistoryTurns || 20);
      setMaxTokens(cfg.maxTokens || 4096);
    }
  }, [cfg]);

  const handleSave = async () => {
    await update.mutateAsync({ model, systemPrompt, maxHistoryTurns: maxHistory, maxTokens });
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const modelOptions = (mc?.availableModels || [])
    .filter(m => m.enabled)
    .map(m => ({ label: `${m.modelName} ($${m.inputRate}/$${m.outputRate})`, value: m.modelId }));

  return (
    <div className="space-y-6 max-w-2xl">
      <Card>
        <h3 className="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
          <MessageSquare size={16} className="text-primary" /> Admin Assistant Model
        </h3>
        <p className="text-xs text-text-muted mb-4">
          The model used by the floating chat assistant (bottom-right corner).
          Uses Bedrock Converse with whitelisted tools (agentic loop).
        </p>
        <Select label="Model" value={model} onChange={setModel}
          options={modelOptions} placeholder="Select model..." />
      </Card>

      <Card>
        <h3 className="text-sm font-semibold text-text-primary mb-3">System Prompt</h3>
        <p className="text-xs text-text-muted mb-3">Custom system prompt for the Admin Assistant. Leave empty to use the default.</p>
        <textarea
          value={systemPrompt}
          onChange={e => setSystemPrompt(e.target.value)}
          rows={4}
          placeholder="e.g. Always respond in English. Focus on operational queries about employees and agents."
          className="w-full rounded-xl border border-dark-border/60 bg-surface-dim px-4 py-3 text-sm text-text-primary placeholder:text-text-muted focus:border-primary/60 focus:outline-none resize-y"
        />
      </Card>

      <Card>
        <h3 className="text-sm font-semibold text-text-primary mb-3">Conversation Settings</h3>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="text-xs text-text-muted block mb-1">Max History Turns</label>
            <input type="number" value={maxHistory} onChange={e => setMaxHistory(Number(e.target.value))}
              min={1} max={50}
              className="w-full rounded-lg border border-dark-border/60 bg-surface-dim px-3 py-2 text-sm text-text-primary focus:border-primary/60 focus:outline-none" />
            <p className="text-[10px] text-text-muted mt-1">Number of conversation turns kept in context</p>
          </div>
          <div>
            <label className="text-xs text-text-muted block mb-1">Max Tokens</label>
            <input type="number" value={maxTokens} onChange={e => setMaxTokens(Number(e.target.value))}
              min={256} max={8192} step={256}
              className="w-full rounded-lg border border-dark-border/60 bg-surface-dim px-3 py-2 text-sm text-text-primary focus:border-primary/60 focus:outline-none" />
            <p className="text-[10px] text-text-muted mt-1">Max response tokens per turn</p>
          </div>
        </div>
      </Card>

      <Button variant="primary" onClick={handleSave} disabled={update.isPending}>
        {saved ? <><Check size={14} /> Saved</> : update.isPending ? 'Saving...' : 'Save Changes'}
      </Button>
    </div>
  );
}

// ─── Admin History Tab ───────────────────────────────────────────────────────

function AdminHistoryTab() {
  const { data: historyData, refetch } = useAdminHistory();
  const clearHistory = useClearAdminHistory();
  const [cleared, setCleared] = useState(false);

  const history = historyData?.history || [];

  const handleClear = async () => {
    await clearHistory.mutateAsync();
    setCleared(true);
    setTimeout(() => setCleared(false), 2000);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-text-secondary">Admin Assistant conversation history ({history.length} turns)</p>
        <div className="flex gap-2">
          <Button variant="default" size="sm" onClick={() => refetch()}><RefreshCw size={13} /></Button>
          <Button variant="default" size="sm" onClick={handleClear} disabled={clearHistory.isPending || history.length === 0}>
            <Trash2 size={13} /> {cleared ? 'Cleared' : clearHistory.isPending ? 'Clearing...' : 'Clear History'}
          </Button>
        </div>
      </div>

      {history.length === 0 ? (
        <div className="text-center py-8 text-text-muted">
          <History size={24} className="mx-auto mb-2 opacity-50" />
          <p className="text-sm">No admin assistant history</p>
        </div>
      ) : (
        <div className="space-y-2 max-h-[500px] overflow-y-auto">
          {history.map((turn: any, i: number) => (
            <div key={i} className={`rounded-lg px-4 py-2.5 text-sm ${
              turn.role === 'user' ? 'bg-primary/5 border border-primary/10' :
              turn.role === 'assistant' ? 'bg-dark-bg' : 'bg-dark-hover'
            }`}>
              <div className="flex items-center gap-2 mb-1">
                <Badge color={turn.role === 'user' ? 'primary' : turn.role === 'assistant' ? 'success' : 'default'}>{turn.role}</Badge>
                {turn.timestamp && <span className="text-[10px] text-text-muted">{new Date(turn.timestamp).toLocaleString()}</span>}
              </div>
              <p className="text-text-secondary whitespace-pre-wrap">{typeof turn.content === 'string' ? turn.content.slice(0, 500) : JSON.stringify(turn.content).slice(0, 500)}{turn.content?.length > 500 ? '...' : ''}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Fargate Config Tab ──────────────────────────────────────────────────────

function FargateConfigTab() {
  const { data: services } = useServiceStatus();
  const ecs = (services as any)?.ecs || {};

  return (
    <div className="space-y-6 max-w-2xl">
      <Card>
        <h3 className="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
          <Server size={16} className="text-primary" /> ECS Fargate Configuration
        </h3>
        <p className="text-xs text-text-muted mb-4">
          Always-On agents run as ECS Fargate tasks. These settings are managed via CloudFormation and deploy.sh.
        </p>
        <div className="space-y-3">
          {[
            { label: 'ECS Cluster', value: ecs.cluster || 'Not configured' },
            { label: 'Running Tasks', value: `${ecs.runningTasks || 0}` },
            { label: 'Task Definition', value: ecs.taskDefinition || '—' },
            { label: 'Subnets', value: ecs.subnets || '—' },
            { label: 'Security Group', value: ecs.securityGroup || '—' },
            { label: 'EFS File System', value: ecs.efsId || '—' },
          ].map(f => (
            <div key={f.label} className="flex items-center justify-between rounded-xl bg-surface-dim px-4 py-3">
              <span className="text-xs text-text-muted">{f.label}</span>
              <span className="text-sm text-text-primary font-mono">{f.value}</span>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <h3 className="text-sm font-semibold text-text-primary mb-4">Tier Defaults</h3>
        <p className="text-xs text-text-muted mb-4">
          Tier configurations are defined in Security Center runtime templates. Each tier maps to a runtime with specific model, IAM role, and guardrail settings.
        </p>
        <div className="grid grid-cols-2 gap-3">
          {['standard', 'restricted', 'engineering', 'executive'].map(tier => (
            <div key={tier} className="rounded-xl bg-dark-bg border border-dark-border/40 px-4 py-3">
              <p className="text-sm font-medium text-text-primary capitalize">{tier}</p>
              <p className="text-xs text-text-muted mt-1">
                {tier === 'standard' ? 'HTTPS outbound, basic model' :
                 tier === 'restricted' ? 'AWS services only, no internet' :
                 tier === 'engineering' ? 'Full outbound, advanced model' :
                 'Full outbound, premium model'}
              </p>
            </div>
          ))}
        </div>
      </Card>

      <div className="rounded-xl bg-info/5 border border-info/20 px-4 py-3 text-xs text-info">
        To modify Fargate configuration, update the CloudFormation template parameters or run <code className="bg-info/10 px-1.5 py-0.5 rounded">deploy.sh</code> with updated environment variables.
      </div>
    </div>
  );
}

// ─── System Tab ───────────────────────────────────────────────────────────────

function SystemTab() {
  const { data: stats, isLoading: statsLoading } = useSystemStats();
  const { data: services } = useServiceStatus();

  const svc = services || { gateway: { status: 'unknown', port: 0, uptime: '', requestsToday: 0 }, auth_agent: { status: 'unknown', uptime: '', approvalsProcessed: 0 }, bedrock: { status: 'unknown', region: '', latencyMs: 0, vpcEndpoint: false }, dynamodb: { status: 'unknown', table: '', itemCount: 0 }, s3: { status: 'unknown', bucket: '' } };

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card>
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2"><Cpu size={16} className="text-primary" /><span className="text-sm font-semibold">CPU</span></div>
            {statsLoading ? <RefreshCw size={14} className="animate-spin text-text-muted" /> : null}
          </div>
          <p className="text-3xl font-bold text-text-primary mb-2">{stats?.cpu?.pct ?? '—'}<span className="text-sm font-normal text-text-muted ml-1">%</span></p>
          <ProgressBar pct={stats?.cpu?.pct || 0} />
        </Card>

        <Card>
          <div className="flex items-center gap-2 mb-3">
            <MemoryStick size={16} className="text-primary" /><span className="text-sm font-semibold">Memory</span>
          </div>
          <p className="text-3xl font-bold text-text-primary mb-1">
            {stats?.memory?.pct ?? '—'}<span className="text-sm font-normal text-text-muted ml-1">%</span>
          </p>
          <ProgressBar pct={stats?.memory?.pct || 0} />
          <p className="text-xs text-text-muted mt-1.5">
            {stats?.memory ? `${fmtBytes(stats.memory.used)} / ${fmtBytes(stats.memory.total)}` : '—'}
          </p>
        </Card>

        <Card>
          <div className="flex items-center gap-2 mb-3">
            <HardDrive size={16} className="text-primary" /><span className="text-sm font-semibold">Disk</span>
          </div>
          <p className="text-3xl font-bold text-text-primary mb-1">
            {stats?.disk?.pct ?? '—'}<span className="text-sm font-normal text-text-muted ml-1">%</span>
          </p>
          <ProgressBar pct={stats?.disk?.pct || 0} />
          <p className="text-xs text-text-muted mt-1.5">
            {stats?.disk ? `${fmtBytes(stats.disk.used)} used · ${fmtBytes(stats.disk.free)} free` : '—'}
          </p>
        </Card>
      </div>

      <Card>
        <h3 className="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
          <Wifi size={16} className="text-primary" /> Port Status
        </h3>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {(stats?.ports || []).map(p => (
            <div key={p.port} className={`rounded-xl px-4 py-3 border ${p.listening ? 'bg-success/5 border-success/20' : 'bg-danger/5 border-danger/20'}`}>
              <div className="flex items-center gap-1.5 mb-1">
                {p.listening ? <Wifi size={13} className="text-success" /> : <WifiOff size={13} className="text-danger" />}
                <span className="text-xs font-mono font-bold">{p.port}</span>
              </div>
              <p className="text-xs text-text-secondary">{p.name}</p>
              <Badge color={p.listening ? 'success' : 'danger'}>{p.listening ? 'Listening' : 'Down'}</Badge>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {[
          { name: 'Gateway Proxy', status: svc.gateway.status, details: [`Port ${svc.gateway.port}`, `Uptime: ${svc.gateway.uptime}`, `Requests today: ${svc.gateway.requestsToday}`] },
          { name: 'Auth Agent', status: svc.auth_agent.status, details: [`Uptime: ${svc.auth_agent.uptime}`, `Approvals: ${svc.auth_agent.approvalsProcessed}`] },
          { name: 'Bedrock', status: svc.bedrock.status, details: [`Region: ${svc.bedrock.region}`, `Latency: ${svc.bedrock.latencyMs}ms`, svc.bedrock.vpcEndpoint ? 'VPC Endpoint' : 'Public endpoint'] },
          { name: 'DynamoDB', status: svc.dynamodb.status, details: [svc.dynamodb.table, `${svc.dynamodb.itemCount} items`] },
          { name: 'S3', status: svc.s3.status, details: [svc.s3.bucket] },
          { name: 'ECS Fargate', status: (svc as any).ecs?.status || 'not configured', details: [(svc as any).ecs?.cluster || 'No cluster', `${(svc as any).ecs?.runningTasks || 0} running tasks`] },
        ].map(s => {
          const ok = ['running', 'healthy', 'connected', 'active'].includes(s.status);
          return (
            <Card key={s.name}>
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium">{s.name}</span>
                <div className={`h-2.5 w-2.5 rounded-full ${ok ? 'bg-success animate-pulse' : 'bg-warning'}`} />
              </div>
              <div className="space-y-0.5">
                {s.details.map((d, i) => <p key={i} className="text-xs text-text-muted">{d}</p>)}
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

// ─── SSO Tab ──────────────────────────────────────────────────────────────────

function SsoTab() {
  const [cfg, setCfg] = useState<any>({
    enabled: false, issuer: '', clientId: '', scopes: 'openid profile email', autoRedirect: false,
  });
  const [testResult, setTestResult] = useState<{ ok: boolean; detail: string } | null>(null);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  const token = localStorage.getItem('openclaw_token') || '';
  const authHeader = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };

  useEffect(() => {
    fetch('/api/v1/settings/sso', { headers: authHeader })
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(data => setCfg((prev: any) => ({ ...prev, ...data })))
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleTest = async () => {
    if (!cfg.issuer) { setTestResult({ ok: false, detail: 'Fill in Issuer first' }); return; }
    setTesting(true);
    setTestResult(null);
    try {
      const resp = await fetch('/api/v1/settings/sso/test', {
        method: 'POST', headers: authHeader,
        body: JSON.stringify({ issuer: cfg.issuer }),
      });
      const data = await resp.json();
      if (data.ok) {
        setTestResult({ ok: true, detail: `Reachable. Token endpoint: ${data.tokenEndpoint}` });
      } else {
        setTestResult({ ok: false, detail: data.error || 'Unknown error' });
      }
    } catch (e: any) {
      setTestResult({ ok: false, detail: e.message });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveMsg(null);
    try {
      const resp = await fetch('/api/v1/settings/sso', {
        method: 'PUT', headers: authHeader, body: JSON.stringify(cfg),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || 'Save failed');
      }
      const saved = await resp.json();
      setCfg((prev: any) => ({ ...prev, ...saved }));
      setSaveMsg({ ok: true, text: 'SSO configuration saved.' });
    } catch (e: any) {
      setSaveMsg({ ok: false, text: e.message });
    } finally {
      setSaving(false);
    }
  };

  const callbackUrl = `${window.location.origin}/sso/callback`;
  const initiateUrl = `${window.location.origin}/login`;

  return (
    <div className="space-y-6 max-w-2xl">
      <Card>
        <h3 className="text-sm font-semibold text-text-primary mb-1 flex items-center gap-2">
          <KeyRound size={16} className="text-primary" /> Single Sign-On (OIDC)
        </h3>
        <p className="text-xs text-text-muted mb-5">
          Connect any OIDC-compatible identity provider: Alibaba Cloud IDaaS, Microsoft Entra ID (Azure AD),
          Okta, Keycloak, etc. Employees can sign in through your existing IdP without a separate password.
        </p>

        {/* 必要的回调 URL 提示 */}
        <div className="rounded-xl bg-surface-dim px-4 py-3 mb-5 space-y-2">
          <p className="text-xs text-text-muted">When creating the OIDC application in your IdP console, use:</p>
          <div className="flex items-center justify-between gap-2">
            <div className="flex-1 min-w-0">
              <p className="text-xs text-text-muted">Redirect URI</p>
              <p className="text-xs font-mono text-text-primary truncate">{callbackUrl}</p>
            </div>
            <button onClick={() => copyToClipboard(callbackUrl)}
              className="text-text-muted hover:text-text-primary flex-shrink-0" title="Copy">
              <Copy size={14} />
            </button>
          </div>
          <div className="flex items-center justify-between gap-2 pt-1 border-t border-dark-border">
            <div className="flex-1 min-w-0">
              <p className="text-xs text-text-muted">Initiate Login URI (optional)</p>
              <p className="text-xs font-mono text-text-primary truncate">{initiateUrl}</p>
            </div>
            <button onClick={() => copyToClipboard(initiateUrl)}
              className="text-text-muted hover:text-text-primary flex-shrink-0" title="Copy">
              <Copy size={14} />
            </button>
          </div>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm text-text-muted mb-1">Issuer URL</label>
            <input
              type="text" value={cfg.issuer}
              onChange={e => setCfg({ ...cfg, issuer: e.target.value })}
              placeholder="https://xxx.aliyunidaas.com/api/v2/iauths_system/oauth2"
              className="w-full rounded-lg border border-dark-border bg-dark-bg px-4 py-2.5 text-sm text-text-primary font-mono focus:border-primary focus:outline-none"
            />
            <p className="text-xs text-text-muted mt-1">
              Azure AD example: <span className="font-mono">https://login.microsoftonline.com/{'{tenant_id}'}/v2.0</span>
            </p>
          </div>

          <div>
            <label className="block text-sm text-text-muted mb-1">Client ID</label>
            <input
              type="text" value={cfg.clientId}
              onChange={e => setCfg({ ...cfg, clientId: e.target.value })}
              placeholder="app_xxxxxxxxxx"
              className="w-full rounded-lg border border-dark-border bg-dark-bg px-4 py-2.5 text-sm text-text-primary font-mono focus:border-primary focus:outline-none"
            />
          </div>

          <div>
            <label className="block text-sm text-text-muted mb-1">Scopes</label>
            <input
              type="text" value={cfg.scopes}
              onChange={e => setCfg({ ...cfg, scopes: e.target.value })}
              placeholder="openid profile email"
              className="w-full rounded-lg border border-dark-border bg-dark-bg px-4 py-2.5 text-sm text-text-primary font-mono focus:border-primary focus:outline-none"
            />
          </div>

          <label className="flex items-center justify-between rounded-xl bg-surface-dim px-4 py-3 cursor-pointer">
            <div>
              <p className="text-sm text-text-primary">Enable SSO</p>
              <p className="text-xs text-text-muted">Show the "Sign in with SSO" button on the login page.</p>
            </div>
            <input type="checkbox" checked={!!cfg.enabled}
              onChange={e => setCfg({ ...cfg, enabled: e.target.checked })}
              className="w-4 h-4 accent-primary" />
          </label>

          <label className="flex items-center justify-between rounded-xl bg-surface-dim px-4 py-3 cursor-pointer">
            <div>
              <p className="text-sm text-text-primary">Auto-redirect on login page</p>
              <p className="text-xs text-text-muted">
                Automatically trigger SSO login when employees land on <span className="font-mono">/login</span>.
                Required for IdP-initiated flow (clicking the app icon in IdP workspace).
              </p>
            </div>
            <input type="checkbox" checked={!!cfg.autoRedirect}
              onChange={e => setCfg({ ...cfg, autoRedirect: e.target.checked })}
              className="w-4 h-4 accent-primary" />
          </label>
        </div>

        {/* 测试连接结果 */}
        {testResult && (
          <div className={`mt-4 rounded-lg px-3 py-2 text-sm flex items-start gap-2 ${
            testResult.ok
              ? 'bg-success/10 border border-success/20 text-success'
              : 'bg-danger/10 border border-danger/20 text-danger'
          }`}>
            {testResult.ok ? <Check size={16} className="mt-0.5" /> : <X size={16} className="mt-0.5" />}
            <span className="break-all">{testResult.detail}</span>
          </div>
        )}

        {/* 保存结果 */}
        {saveMsg && (
          <div className={`mt-4 rounded-lg px-3 py-2 text-sm flex items-start gap-2 ${
            saveMsg.ok
              ? 'bg-success/10 border border-success/20 text-success'
              : 'bg-danger/10 border border-danger/20 text-danger'
          }`}>
            {saveMsg.ok ? <Check size={16} className="mt-0.5" /> : <X size={16} className="mt-0.5" />}
            <span>{saveMsg.text}</span>
          </div>
        )}

        <div className="flex gap-3 mt-6">
          <Button onClick={handleTest} disabled={testing || !cfg.issuer} variant="secondary">
            {testing ? 'Testing…' : 'Test Connection'}
          </Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </div>

        {cfg.updatedAt && (
          <p className="text-xs text-text-muted mt-4">
            Last updated by <span className="font-medium">{cfg.updatedBy}</span> at {new Date(cfg.updatedAt).toLocaleString()}
          </p>
        )}
      </Card>
    </div>
  );
}

// ─── Main ─────────────────────────────────────────────────────────────────────

export default function Settings() {
  const [tab, setTab] = useState('account');

  return (
    <div>
      <PageHeader title="Settings" description="Admin account, platform access, logs, admin assistant, and system health" />

      <Tabs
        tabs={[
          { id: 'account', label: 'Account' },
          { id: 'platform-access', label: 'Platform Access' },
          { id: 'platform-logs', label: 'Platform Logs' },
          { id: 'assistant', label: 'Admin Assistant' },
          { id: 'admin-history', label: 'Admin History' },
          { id: 'fargate', label: 'Fargate' },
          { id: 'interface', label: 'Interface' },
          { id: 'system', label: 'System' },
          { id: 'sso', label: 'SSO' },
        ]}
        activeTab={tab}
        onChange={setTab}
      />

      <div className="mt-6">
        {tab === 'account' && <AccountTab />}
        {tab === 'platform-access' && <PlatformAccessTab />}
        {tab === 'platform-logs' && <PlatformLogsTab />}
        {tab === 'assistant' && <AdminAssistantTab />}
        {tab === 'admin-history' && <AdminHistoryTab />}
        {tab === 'fargate' && <FargateConfigTab />}
        {tab === 'interface' && (
          <div className="space-y-6 max-w-lg">
            <Card>
              <h3 className="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
                <Globe size={16} className="text-primary" /> Language
              </h3>
              <div className="space-y-2">
                {[
                  { code: 'en', name: 'English', flag: 'US', available: true },
                  { code: 'zh', name: 'Chinese (Simplified)', flag: 'CN', available: false },
                  { code: 'ja', name: 'Japanese', flag: 'JP', available: false },
                ].map(l => (
                  <label key={l.code}
                    className={`flex items-center justify-between rounded-xl px-4 py-3 transition-colors ${l.available ? 'bg-primary/10 border border-primary/30 cursor-pointer' : 'bg-surface-dim border border-transparent opacity-40 cursor-not-allowed'}`}>
                    <div className="flex items-center gap-3">
                      <span className="text-sm font-medium">{l.flag}</span>
                      <span className={`text-sm font-medium ${l.available ? 'text-text-primary' : 'text-text-muted'}`}>{l.name}</span>
                      {!l.available && <Badge color="default">Coming soon</Badge>}
                    </div>
                    <div className={`h-4 w-4 rounded-full border-2 flex items-center justify-center ${l.available ? 'border-primary bg-primary' : 'border-dark-border'}`}>
                      {l.available && <div className="h-2 w-2 rounded-full bg-white" />}
                    </div>
                  </label>
                ))}
              </div>
            </Card>
          </div>
        )}
        {tab === 'system' && <SystemTab />}
        {tab === 'sso' && <SsoTab />}
      </div>
    </div>
  );
}
