import { useParams, useNavigate } from 'react-router-dom';
import Chart from 'react-apexcharts';
import type { ApexOptions } from 'apexcharts';
import { ArrowLeft, Edit3, MessageSquare, Eye, Loader, FolderOpen, RefreshCw } from 'lucide-react';
import { Card, Badge, Button, PageHeader, StatusDot } from '../../components/ui';
import { useAgent, useAgents, usePositions, useBindings, useSessions, useAgentDailyUsage } from '../../hooks/useApi';
import { CHANNEL_LABELS } from '../../types';
import type { ChannelType } from '../../types';

const activityOpts: ApexOptions = {
  chart: { type: 'bar', toolbar: { show: false }, background: 'transparent' },
  colors: ['#6366f1'],
  plotOptions: { bar: { borderRadius: 3, columnWidth: '55%' } },
  grid: { borderColor: '#2e3039', strokeDashArray: 4 },
  xaxis: { labels: { style: { colors: '#64748b', fontSize: '11px' } }, axisBorder: { show: false }, axisTicks: { show: false } },
  yaxis: { labels: { style: { colors: '#64748b', fontSize: '11px' } } },
  tooltip: { theme: 'dark' },
  dataLabels: { enabled: false },
};

const tokenOpts: ApexOptions = {
  chart: { type: 'area', toolbar: { show: false }, background: 'transparent' },
  colors: ['#06b6d4', '#f59e0b'],
  stroke: { curve: 'smooth', width: 2 },
  fill: { type: 'gradient', gradient: { opacityFrom: 0.3, opacityTo: 0.05 } },
  grid: { borderColor: '#2e3039', strokeDashArray: 4 },
  xaxis: { labels: { style: { colors: '#64748b', fontSize: '11px' } }, axisBorder: { show: false }, axisTicks: { show: false } },
  yaxis: { labels: { style: { colors: '#64748b', fontSize: '11px' }, formatter: (v: number) => `${(v / 1000).toFixed(0)}k` } },
  tooltip: { theme: 'dark' },
  legend: { position: 'top', horizontalAlign: 'right', labels: { colors: '#94a3b8' } },
  dataLabels: { enabled: false },
};

export default function AgentDetail() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const { data: agent, isLoading } = useAgent(agentId || '');
  const { data: allAgents = [] } = useAgents();
  const { data: positions = [] } = usePositions();
  const { data: allBindings = [] } = useBindings();
  const { data: allSessions = [] } = useSessions();
  const { data: dailyUsage = [] } = useAgentDailyUsage(agentId || '');

  if (isLoading) {
    return <div className="flex items-center justify-center py-20"><Loader size={24} className="animate-spin text-primary" /></div>;
  }

  if (!agent) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <p className="text-lg text-text-muted mb-4">Agent Not Found</p>
        <Button variant="primary" onClick={() => navigate('/agents')}>Back to Agent List</Button>
      </div>
    );
  }

  const position = positions.find(p => p.id === agent.positionId);
  const bindings = allBindings.filter(b => b.agentId === agent.id);
  const sessions = allSessions.filter(s => s.agentId === agent.id);

  return (
    <div>
      <PageHeader
        title={agent.name}
        description={`${agent.positionName} · ${agent.employeeName}${agent.createdAt ? ` · Created ${new Date(agent.createdAt).toLocaleDateString()}` : ''}`}
        actions={
          <div className="flex gap-2">
            <Button variant="default" onClick={() => navigate('/agents')}><ArrowLeft size={16} /> Back</Button>
            <Button variant="default" onClick={() => navigate(`/agents/${agent.id}/soul`)}><Edit3 size={16} /> Edit SOUL</Button>
            <Button variant="default" onClick={() => navigate(`/playground?agent=${agent.id}`)}><MessageSquare size={16} /> Playground</Button>
            <Button variant="default" onClick={() => navigate(`/workspace?agent=${agent.id}`)}><FolderOpen size={16} /> Workspace</Button>
            <Button variant="default" onClick={async () => {
              try {
                await fetch(`/api/v1/admin/refresh-agent/${agent.employeeId}`, {
                  method: 'POST', headers: { Authorization: `Bearer ${localStorage.getItem('openclaw_token')}` }
                });
                alert('Agent session terminated. Next message will trigger fresh assembly.');
              } catch { alert('Refresh failed'); }
            }}><RefreshCw size={16} /> Refresh</Button>
          </div>
        }
      />

      {/* Overview cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-6 mb-6">
        <Card>
          <p className="text-xs text-text-muted">Status</p>
          <div className="mt-1"><StatusDot status={agent.status} /></div>
        </Card>
        <Card>
          <p className="text-xs text-text-muted">Quality Score</p>
          <p className="mt-1 text-xl font-bold text-warning">⭐ {agent.qualityScore || '—'}</p>
        </Card>
        <Card>
          <p className="text-xs text-text-muted">Skills</p>
          <p className="mt-1 text-xl font-bold">{(agent.skills || []).length}</p>
        </Card>
        <Card>
          <p className="text-xs text-text-muted">Channels</p>
          <p className="mt-1 text-xl font-bold">{(agent.channels || []).length}</p>
        </Card>
        <Card>
          <p className="text-xs text-text-muted">Active Sessions</p>
          <p className="mt-1 text-xl font-bold text-success">{sessions.length}</p>
        </Card>
        <Card>
          <p className="text-xs text-text-muted">Bindings</p>
          <p className="mt-1 text-xl font-bold">{bindings.length}</p>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3 mb-6">
        {/* Activity Stats */}
        <Card>
          <h3 className="text-lg font-semibold text-text-primary mb-4">Activity Summary</h3>
          <div className="space-y-3">
            {[
              { label: 'Requests (7 days)', value: dailyUsage.reduce((s, d) => s + d.requests, 0), color: 'text-primary' },
              { label: 'Input tokens', value: `${(dailyUsage.reduce((s, d) => s + (d.inputTokens || 0), 0) / 1000).toFixed(1)}k`, color: 'text-info' },
              { label: 'Output tokens', value: `${(dailyUsage.reduce((s, d) => s + (d.outputTokens || 0), 0) / 1000).toFixed(1)}k`, color: 'text-warning' },
              { label: 'Est. cost', value: `$${dailyUsage.reduce((s, d) => s + (d.cost || 0), 0).toFixed(4)}`, color: 'text-success' },
              { label: 'Active sessions', value: sessions.length, color: 'text-success' },
              { label: 'Bindings', value: bindings.length, color: 'text-text-primary' },
              { label: 'Skills loaded', value: agent.skills?.length || 0, color: 'text-text-primary' },
              { label: 'Quality score', value: agent.qualityScore ? `${agent.qualityScore}/5` : '—', color: 'text-warning' },
            ].map(r => (
              <div key={r.label} className="flex justify-between rounded-lg bg-dark-bg px-3 py-2">
                <span className="text-xs text-text-muted">{r.label}</span>
                <span className={`text-sm font-semibold ${r.color}`}>{r.value}</span>
              </div>
            ))}
          </div>
        </Card>

        {/* Daily Conversations */}
        <Card className="lg:col-span-2">
          <h3 className="text-lg font-semibold text-text-primary mb-1">Daily Conversations (7 days)</h3>
          <p className="text-sm text-text-secondary mb-3">Requests per day from DynamoDB records</p>
          {dailyUsage.length === 0 ? (
            <div className="flex items-center justify-center h-48 text-text-muted text-sm">No conversation data yet</div>
          ) : (
            <Chart
              options={{ ...activityOpts, xaxis: { ...activityOpts.xaxis, categories: dailyUsage.map(d => d.date?.slice(5) || '') } }}
              series={[{ name: 'Conversations', data: dailyUsage.map(d => d.requests) }]}
              type="bar" height={260}
            />
          )}
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2 mb-6">
        {/* Token Usage */}
        <Card>
          <h3 className="text-lg font-semibold text-text-primary mb-2">Token Usage (7 days)</h3>
          {dailyUsage.length === 0 ? (
            <div className="flex items-center justify-center h-36 text-text-muted text-sm">No token data yet</div>
          ) : (
            <Chart
              options={{ ...tokenOpts, xaxis: { ...tokenOpts.xaxis, categories: dailyUsage.map(d => d.date?.slice(5) || '') } }}
              series={[
                { name: 'Input Tokens', data: dailyUsage.map(d => d.inputTokens || 0) },
                { name: 'Output Tokens', data: dailyUsage.map(d => d.outputTokens || 0) },
              ]}
              type="area" height={240}
            />
          )}
        </Card>

        {/* Configuration Summary */}
        <Card>
          <h3 className="text-lg font-semibold text-text-primary mb-4">Configuration</h3>
          <div className="space-y-4">
            <div>
              <p className="text-xs text-text-muted mb-1">Position</p>
              <p className="text-sm font-medium">{agent.positionName}</p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">Employee</p>
              <p className="text-sm font-medium">{agent.employeeName}</p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1.5">SOUL Versions</p>
              <div className="flex gap-1.5">
                <Badge>Global v{agent.soulVersions?.global ?? 0}</Badge>
                <Badge color="primary">Position v{agent.soulVersions?.position ?? 0}</Badge>
                <Badge color="success">Personal v{agent.soulVersions?.personal ?? 0}</Badge>
              </div>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1.5">Channels</p>
              <div className="flex gap-1.5">{(agent.channels || []).map(c => <Badge key={c} color="info">{CHANNEL_LABELS[c as ChannelType]}</Badge>)}</div>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1.5">Skills ({(agent.skills || []).length})</p>
              <div className="flex flex-wrap gap-1.5">{(agent.skills || []).map(s => <Badge key={s} color="success">{s}</Badge>)}</div>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1.5">Tool Permissions</p>
              <div className="flex flex-wrap gap-1.5">{(position?.toolAllowlist || []).map(t => <Badge key={t} color="info">{t}</Badge>)}</div>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">Last Updated</p>
              <p className="text-sm text-text-secondary">{agent.updatedAt ? new Date(agent.updatedAt).toLocaleString() : '—'}</p>
            </div>
          </div>
        </Card>
      </div>

      {/* Active Sessions */}
      {sessions.length > 0 && (
        <Card>
          <h3 className="text-lg font-semibold text-text-primary mb-4">Active Sessions ({sessions.length})</h3>
          <div className="space-y-2">
            {sessions.map(s => (
              <div key={s.id} className="flex items-center justify-between rounded-lg bg-dark-bg p-3">
                <div className="flex items-center gap-3">
                  <div className="h-2.5 w-2.5 rounded-full bg-success animate-pulse" />
                  <div>
                    <p className="text-sm font-medium">{s.employeeName}</p>
                    <p className="text-xs text-text-muted">{(s.lastMessage || '').slice(0, 60)}{s.lastMessage?.length > 60 ? '...' : ''}</p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <Badge color="info">{CHANNEL_LABELS[s.channel as ChannelType]}</Badge>
                  <span className="text-xs text-text-muted">{s.turns} turns</span>
                  <Button variant="ghost" size="sm" onClick={() => navigate(`/monitor?session=${s.id}`)}><Eye size={14} /></Button>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
