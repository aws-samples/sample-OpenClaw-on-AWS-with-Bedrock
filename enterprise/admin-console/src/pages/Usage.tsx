import { useState } from 'react';
import Chart from 'react-apexcharts';
import type { ApexOptions } from 'apexcharts';
import { DollarSign, TrendingUp, TrendingDown, Users, Bot, AlertTriangle, Download, Cpu, Edit3, Save, Zap } from 'lucide-react';
import { Card, StatCard, Badge, Button, PageHeader, Table, Tabs, Select, Modal } from '../components/ui';
import { useUsageSummary, useUsageByDepartment, useUsageByAgent, useUsageBySkill, useUsageSkillUsers, useUsageBudgets, useUsageTrend, useUsageByModel, useUsageFargateCost, useModelConfig, useUpdateModelConfig, useUpdateFallbackModel, useUpdateBudgets } from '../hooks/useApi';
import { useAuth } from '../contexts/AuthContext';

const costTrendOpts: ApexOptions = {
  chart: { type: 'area', toolbar: { show: false }, background: 'transparent' },
  colors: ['#22c55e', '#6366f1'],
  stroke: { curve: 'smooth', width: [2, 2] },
  fill: { type: 'gradient', gradient: { opacityFrom: 0.3, opacityTo: 0.05 } },
  grid: { borderColor: '#2e3039', strokeDashArray: 4 },
  xaxis: { labels: { style: { colors: '#64748b', fontSize: '12px' } }, axisBorder: { show: false }, axisTicks: { show: false } },
  yaxis: [
    { title: { text: 'Cost ($)', style: { color: '#64748b', fontSize: '11px' } }, labels: { style: { colors: '#64748b', fontSize: '12px' }, formatter: (v: number) => `$${v.toFixed(2)}` } },
    { opposite: true, title: { text: 'Requests', style: { color: '#64748b', fontSize: '11px' } }, labels: { style: { colors: '#64748b', fontSize: '12px' }, formatter: (v: number) => `${Math.round(v)}` } },
  ],
  tooltip: { theme: 'dark' },
  legend: { position: 'top', horizontalAlign: 'right', labels: { colors: '#94a3b8' } },
  dataLabels: { enabled: false },
};

export default function Usage() {
  const { isAdmin } = useAuth();
  const [activeTab, setActiveTab] = useState('department');
  const [timeRange, setTimeRange] = useState<'1d' | '7d' | '30d' | 'mtd'>('7d');
  const { data: summary } = useUsageSummary(timeRange);
  const { data: byDept = [] } = useUsageByDepartment(timeRange);
  const { data: byAgent = [] } = useUsageByAgent(timeRange);
  const { data: bySkill = [] } = useUsageBySkill(timeRange);
  const { data: byModel = [] } = useUsageByModel(timeRange);
  const { data: budgets = [] } = useUsageBudgets(timeRange);
  const { data: trend = [] } = useUsageTrend(timeRange);
  const { data: fargateCost } = useUsageFargateCost(isAdmin);
  const { data: mc } = useModelConfig();
  const updateDefault = useUpdateModelConfig();
  const updateFallback = useUpdateFallbackModel();
  const updateBudgets = useUpdateBudgets();
  const [modelModal, setModelModal] = useState<'default' | 'fallback' | null>(null);
  const [selectedModelId, setSelectedModelId] = useState('');
  const [budgetModal, setBudgetModal] = useState(false);
  const [budgetDraft, setBudgetDraft] = useState<Record<string, string>>({});
  const [selectedSkillId, setSelectedSkillId] = useState('');
  const { data: skillUsers = [], isLoading: skillUsersLoading } = useUsageSkillUsers(selectedSkillId, timeRange);

  const s = summary || { totalInputTokens: 0, totalOutputTokens: 0, totalCost: 0, totalRequests: 0, tenantCount: 0 };
  const rangeLabel = timeRange === '1d' ? 'Today' : timeRange === '7d' ? 'Last 7 days' : timeRange === '30d' ? 'Last 30 days' : 'Month to date';
  const m = mc || { default: { modelId: '', modelName: '—', inputRate: 0, outputRate: 0 }, fallback: { modelId: '', modelName: '', inputRate: 0, outputRate: 0 }, positionOverrides: {}, employeeOverrides: {}, availableModels: [] };
  const modelOptions = m.availableModels.map((mo: any) => ({ label: `${mo.modelName}  ($${mo.inputRate} in / $${mo.outputRate} out per 1M tokens)`, value: mo.modelId }));
  const findModel = (id: string) => m.availableModels.find((mo: any) => mo.modelId === id);
  const handleModelSave = () => {
    if (!isAdmin) return;
    const model = findModel(selectedModelId); if (!model) return;
    if (modelModal === 'default') updateDefault.mutate({ modelId: model.modelId, modelName: model.modelName, inputRate: model.inputRate, outputRate: model.outputRate });
    else if (modelModal === 'fallback') updateFallback.mutate({ modelId: model.modelId, modelName: model.modelName, inputRate: model.inputRate, outputRate: model.outputRate });
    setModelModal(null); setSelectedModelId('');
  };

  const handleBudgetSave = () => {
    if (!isAdmin) return;
    const departments: Record<string, number> = {};
    for (const [dept, val] of Object.entries(budgetDraft)) {
      const n = parseFloat(val);
      if (!isNaN(n) && n > 0) departments[dept] = n;
    }
    updateBudgets.mutate({ departments }, {
      onSuccess: () => setBudgetModal(false),
    });
  };

  const buildDeptBarOpts = (depts: typeof byDept): ApexOptions => ({
    chart: { type: 'bar', toolbar: { show: false }, background: 'transparent', stacked: true },
    colors: ['#6366f1', '#22c55e'],
    plotOptions: { bar: { borderRadius: 3, barHeight: '60%', horizontal: true } },
    grid: { borderColor: '#2e3039', strokeDashArray: 4, padding: { left: 10 } },
    xaxis: {
      categories: depts.map(d => d.department),
      labels: { style: { colors: '#64748b', fontSize: '11px' }, formatter: (v: string) => `${(Number(v) / 1000).toFixed(0)}k` },
      axisBorder: { show: false }, axisTicks: { show: false },
    },
    yaxis: { labels: { style: { colors: '#94a3b8', fontSize: '12px' }, maxWidth: 130 } },
    tooltip: {
      theme: 'dark',
      custom: ({ seriesIndex, dataPointIndex }: { seriesIndex: number; dataPointIndex: number }) => {
        const d = depts[dataPointIndex];
        if (!d) return '';
        return `<div class="p-2 text-xs bg-dark-card border border-dark-border rounded-lg shadow-lg">
          <p class="font-semibold mb-1">${d.department}</p>
          <p>Input: ${(d.inputTokens/1000).toFixed(1)}k tokens</p>
          <p>Output: ${(d.outputTokens/1000).toFixed(1)}k tokens</p>
          <p class="text-green-400 mt-1">Cost: $${d.cost.toFixed(2)}</p>
        </div>`;
      },
    },
    legend: { position: 'top', horizontalAlign: 'right', labels: { colors: '#94a3b8' } },
    dataLabels: { enabled: false },
  });

  return (
    <div>
      <PageHeader
        title="Usage & Cost"
        description="Token consumption, cost tracking, budget management, and multi-dimension analysis"
        actions={
          <div className="flex items-center gap-3">
            <div className="flex rounded-lg border border-dark-border overflow-hidden">
              {[
                { label: 'Today', value: '1d' },
                { label: '7 Days', value: '7d' },
                { label: '30 Days', value: '30d' },
                { label: 'MTD', value: 'mtd' },
              ].map(r => (
                <button key={r.value} onClick={() => setTimeRange(r.value)}
                  className={`px-3 py-1.5 text-xs font-medium transition-colors ${timeRange === r.value ? 'bg-primary text-white' : 'bg-dark-card text-text-muted hover:text-text-primary'}`}>
                  {r.label}
                </button>
              ))}
            </div>
            <Button variant="default" size="sm" onClick={() => {
              const csv = ['Agent,Employee,Position,Requests,Input Tokens,Output Tokens,Cost', ...byAgent.map(a =>
                `"${a.agentName}","${a.employeeName}","${a.positionName}",${a.requests},${a.inputTokens},${a.outputTokens},${a.cost}`)].join('\n');
              const blob = new Blob([csv], { type: 'text/csv' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a'); a.href = url; a.download = `usage-export-${new Date().toISOString().slice(0,10)}.csv`; a.click();
              URL.revokeObjectURL(url);
            }}><Download size={14} /> Export</Button>
          </div>
        }
      />

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-6 mb-6">
        <StatCard title="Bedrock Cost" value={`$${s.totalCost.toFixed(2)}`} subtitle={`${s.totalRequests} requests`} icon={<DollarSign size={22} />} color="success" />
        <StatCard title="Fargate Cost" value={isAdmin ? `~$${(fargateCost?.totalMonthly || 0).toFixed(2)}/mo` : 'Admin only'} subtitle={isAdmin ? `${fargateCost?.count || 0} containers` : 'Restricted estimate'} icon={<Zap size={22} />} color="cyan" />
        <StatCard title="Input Tokens" value={`${(s.totalInputTokens / 1000).toFixed(0)}k`} subtitle={rangeLabel} icon={<TrendingUp size={22} />} color="primary" />
        <StatCard title="Output Tokens" value={`${(s.totalOutputTokens / 1000).toFixed(0)}k`} subtitle={rangeLabel} icon={<TrendingDown size={22} />} color="info" />
        <StatCard title="Active Tenants" value={s.tenantCount} subtitle="With agents" icon={<Users size={22} />} color="warning" />
        <StatCard title="Avg Cost/Req" value={s.totalRequests > 0 ? `$${(s.totalCost / s.totalRequests).toFixed(4)}` : '—'} subtitle="Per invocation" icon={<Bot size={22} />} color="info" />
      </div>

      {/* Cost trend chart */}
      <Card className="mb-6">
        <h3 className="text-lg font-semibold text-text-primary mb-1">Cost & Request Trend</h3>
        <p className="text-sm text-text-secondary mb-4">Daily Bedrock cost and request volume ({rangeLabel})</p>
        {(() => (
            <Chart
              options={{...costTrendOpts, xaxis: { ...costTrendOpts.xaxis, categories: trend.map(t => t.date.slice(5)) }}}
              series={[
                { name: 'Cost ($)', type: 'area', data: trend.map(t => t.openclawCost) },
                { name: 'Requests', type: 'column', data: trend.map(t => t.totalRequests || 0) },
              ]}
              type="line" height={280}
            />
        ))()}
      </Card>

      <Card>
        <Tabs
          tabs={[
            { id: 'department', label: 'By Department', count: byDept.length },
            { id: 'agent', label: 'By Agent', count: byAgent.length },
            { id: 'skill', label: 'By Skill', count: bySkill.length || undefined },
            { id: 'model', label: 'By Model' },
            { id: 'pricing', label: 'Models & Pricing' },
            { id: 'budget', label: 'Budget Management', count: budgets.filter(b => b.status !== 'ok').length || undefined },
          ]}
          activeTab={activeTab}
          onChange={setActiveTab}
        />

        <div className="mt-4">
          {activeTab === 'department' && (
            <div className="space-y-6">
              <Chart
                options={buildDeptBarOpts(byDept)}
                series={[
                  { name: 'Input Tokens', data: byDept.map(d => d.inputTokens) },
                  { name: 'Output Tokens', data: byDept.map(d => d.outputTokens) },
                ]}
                type="bar" height={Math.max(byDept.length * 44 + 80, 300)}
              />
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-dark-border text-left">
                    <th className="pb-2 text-xs font-medium text-text-muted uppercase tracking-wider">Department</th>
                    <th className="pb-2 text-xs font-medium text-text-muted uppercase tracking-wider text-right">Agents</th>
                    <th className="pb-2 text-xs font-medium text-text-muted uppercase tracking-wider text-right">Requests</th>
                    <th className="pb-2 text-xs font-medium text-text-muted uppercase tracking-wider text-right">Tokens</th>
                    <th className="pb-2 text-xs font-medium text-text-muted uppercase tracking-wider text-right">Cost</th>
                    <th className="pb-2 text-xs font-medium text-text-muted uppercase tracking-wider w-36">Share</th>
                  </tr>
                </thead>
                <tbody>
                  {byDept.map((d) => {
                    const pct = s.totalCost > 0 ? (d.cost / s.totalCost * 100) : 0;
                    const maxCost = byDept[0]?.cost || 1;
                    return (
                      <tr key={d.department} className="border-b border-dark-border/40 hover:bg-dark-hover transition-colors">
                        <td className="py-2.5 font-medium text-text-primary">{d.department}</td>
                        <td className="py-2.5 text-right text-text-secondary">{d.agents}</td>
                        <td className="py-2.5 text-right text-text-secondary">{d.requests}</td>
                        <td className="py-2.5 text-right text-text-secondary">{((d.inputTokens + d.outputTokens) / 1000).toFixed(0)}k</td>
                        <td className="py-2.5 text-right font-medium text-text-primary">${d.cost.toFixed(2)}</td>
                        <td className="py-2.5">
                          <div className="flex items-center gap-2">
                            <div className="flex-1 h-1.5 rounded-full bg-dark-bg overflow-hidden">
                              <div className="h-full rounded-full bg-primary/70" style={{ width: `${(d.cost / maxCost) * 100}%` }} />
                            </div>
                            <span className="text-xs text-text-muted w-8 text-right">{pct.toFixed(0)}%</span>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {activeTab === 'agent' && (
            <Table
              columns={[
                { key: 'agent', label: 'Agent', render: (a: typeof byAgent[0]) => (
                  <div><p className="font-medium">{a.agentName}</p><p className="text-xs text-text-muted">{a.employeeName}</p></div>
                )},
                { key: 'mode', label: 'Mode', render: (a: typeof byAgent[0]) => (
                  a.deployMode === 'always-on-ecs'
                    ? <Badge color="success"><Zap size={10} className="inline mr-0.5" />Fargate</Badge>
                    : <Badge color="default">Serverless</Badge>
                )},
                { key: 'position', label: 'Position', render: (a: typeof byAgent[0]) => <Badge>{a.positionName}</Badge> },
                { key: 'requests', label: 'Requests', render: (a: typeof byAgent[0]) => a.requests },
                { key: 'input', label: 'Input', render: (a: typeof byAgent[0]) => `${(a.inputTokens / 1000).toFixed(1)}k` },
                { key: 'output', label: 'Output', render: (a: typeof byAgent[0]) => `${(a.outputTokens / 1000).toFixed(1)}k` },
                { key: 'cost', label: 'Cost', render: (a: typeof byAgent[0]) => `$${a.cost.toFixed(2)}` },
                { key: 'share', label: 'Share', render: (a: typeof byAgent[0]) => {
                  const pct = s.totalCost > 0 ? (a.cost / s.totalCost * 100).toFixed(1) : '0';
                  return <Badge color="info">{pct}%</Badge>;
                }},
              ]}
              data={byAgent}
            />
          )}

          {activeTab === 'skill' && (
            <div>
              <div className="rounded-lg bg-info/5 border border-info/20 p-3 text-xs text-info mb-4">
                Calls are counted per turn from validated Skill usage receipts. A direct SKILL.md read is used only as a compatibility fallback. Dynamic and removed Skills retain historical usage.
              </div>
              <Table
                columns={[
                  { key: 'skill', label: 'Skill', render: (item: typeof bySkill[0]) => (
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{item.skillId}</span>
                      <Badge color={item.status === 'installed' ? 'success' : 'default'}>{item.status}</Badge>
                    </div>
                  ) },
                  { key: 'calls', label: 'Calls', render: (item: typeof bySkill[0]) => item.calls },
                  { key: 'users', label: 'Active Users', render: (item: typeof bySkill[0]) => item.activeUsers },
                  { key: 'serverless', label: 'Serverless', render: (item: typeof bySkill[0]) => item.serverlessCalls },
                  { key: 'alwaysOn', label: 'Always-On', render: (item: typeof bySkill[0]) => item.alwaysOnCalls },
                  { key: 'lastUsed', label: 'Last Used', render: (item: typeof bySkill[0]) => item.lastUsedAt ? new Date(item.lastUsedAt).toLocaleString() : '—' },
                  { key: 'details', label: '', render: (item: typeof bySkill[0]) => (
                    <Button variant="ghost" size="sm" onClick={() => setSelectedSkillId(item.skillId)}>Users</Button>
                  ) },
                ]}
                data={bySkill}
              />
            </div>
          )}

          {activeTab === 'model' && (
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div>
                <h3 className="text-sm font-semibold text-text-primary mb-4">Token Distribution by Model</h3>
                <Chart
                  options={{
                    chart: { type: 'donut', background: 'transparent' },
                    colors: ['#22c55e', '#6366f1', '#f59e0b', '#06b6d4', '#ef4444'],
                    labels: byModel.map(m => m.model.split('/').pop()?.split(':')[0] || m.model),
                    legend: { position: 'bottom', labels: { colors: '#94a3b8' } },
                    plotOptions: { pie: { donut: { size: '65%', labels: { show: true, total: { show: true, label: 'Total Tokens', color: '#94a3b8', formatter: () => `${(byModel.reduce((s, m) => s + m.inputTokens + m.outputTokens, 0) / 1000).toFixed(0)}k` } } } } },
                    dataLabels: { enabled: false },
                    tooltip: { theme: 'dark' },
                  }}
                  series={byModel.map(m => m.inputTokens + m.outputTokens)}
                  type="donut" height={300}
                />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-text-primary mb-4">Cost by Model</h3>
                <div className="space-y-4">
                  {(() => {
                    const colors = ['#22c55e', '#6366f1', '#f59e0b', '#06b6d4', '#ef4444'];
                    const totalModelCost = byModel.reduce((s, m) => s + m.cost, 0);
                    return byModel.map((m, i) => {
                      const shortName = m.model.split('/').pop()?.split(':')[0] || m.model;
                      const pct = totalModelCost > 0 ? Math.round(m.cost / totalModelCost * 100) : 0;
                      return (
                        <div key={m.model} className="rounded-lg bg-dark-bg p-4">
                          <div className="flex items-center justify-between mb-2">
                            <div className="flex items-center gap-2">
                              <div className="w-3 h-3 rounded-full" style={{ backgroundColor: colors[i % colors.length] }} />
                              <span className="text-sm font-medium">{shortName}</span>
                            </div>
                            <span className="text-sm font-semibold" style={{ color: colors[i % colors.length] }}>${m.cost.toFixed(4)}</span>
                          </div>
                          <div className="grid grid-cols-3 gap-3 text-xs text-text-muted">
                            <div><span className="block text-text-secondary">{m.requests}</span>requests</div>
                            <div><span className="block text-text-secondary">{((m.inputTokens + m.outputTokens) / 1000).toFixed(1)}k</span>tokens</div>
                            <div><span className="block text-text-secondary">{pct}%</span>of total cost</div>
                          </div>
                        </div>
                      );
                    });
                  })()}
                </div>
                {byModel.length === 0 && (
                  <p className="text-sm text-text-muted text-center py-8">No model usage data available</p>
                )}
                {byModel.length > 0 && (
                  <div className="mt-4 rounded-lg bg-success/5 border border-success/20 p-3 text-xs text-success">
                    Data from DynamoDB usage records. Real token counts from AgentCore invocations.
                  </div>
                )}
              </div>
            </div>
          )}

          {activeTab === 'budget' && (
            <div>
              <div className="flex items-center justify-between mb-4">
                <div className="rounded-xl bg-warning/10 border border-warning/30 px-4 py-3 flex items-start gap-2 flex-1 mr-4">
                  <AlertTriangle size={15} className="text-warning mt-0.5 shrink-0" />
                  <p className="text-xs text-warning">
                    <strong>Tracking only — budgets are not enforced.</strong> Projected cost uses the selected range's active-day average x 30.
                  </p>
                </div>
                {isAdmin && (
                  <Button variant="primary" size="sm" onClick={() => {
                    const draft: Record<string, string> = {};
                    budgets.forEach(b => { draft[b.department] = String(b.budget); });
                    setBudgetDraft(draft);
                    setBudgetModal(true);
                  }}>
                    <Edit3 size={14} /> Edit Budgets
                  </Button>
                )}
              </div>

              <Table
                columns={[
                  { key: 'dept', label: 'Department', render: (b: typeof budgets[0]) => <span className="font-medium">{b.department}</span> },
                  { key: 'budget', label: 'Monthly Budget', render: (b: typeof budgets[0]) => `$${b.budget.toFixed(0)}` },
                  { key: 'used', label: `Used (${rangeLabel})`, render: (b: typeof budgets[0]) => `$${b.used.toFixed(2)}` },
                  { key: 'projected', label: 'Projected Monthly', render: (b: typeof budgets[0]) => `$${b.projected.toFixed(2)}` },
                  { key: 'utilization', label: 'Utilization', render: (b: typeof budgets[0]) => {
                    const pct = Math.round(b.projected / b.budget * 100);
                    return (
                      <div className="flex items-center gap-2">
                        <div className="h-2 w-24 rounded-full bg-dark-bg">
                          <div className={`h-full rounded-full ${pct > 100 ? 'bg-danger' : pct > 80 ? 'bg-warning' : 'bg-success'}`} style={{ width: `${Math.min(pct, 100)}%` }} />
                        </div>
                        <span className="text-xs text-text-muted">{pct}%</span>
                      </div>
                    );
                  }},
                  { key: 'status', label: 'Status', render: (b: typeof budgets[0]) => (
                    <Badge color={b.status === 'ok' ? 'success' : b.status === 'warning' ? 'warning' : 'danger'} dot>
                      {b.status === 'ok' ? 'On track' : b.status === 'warning' ? 'Near limit' : 'Over budget'}
                    </Badge>
                  )},
                ]}
                data={budgets}
              />
              <div className="mt-4 rounded-lg bg-info/5 border border-info/20 p-3 text-xs text-info">
                Budget resolution: Employee (individual) → Department → Global. Selected-range active-day average x 30 for projection.
              </div>
            </div>
          )}

          {activeTab === 'pricing' && (
            <div className="space-y-6">
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {[
                  { label: 'Default Model', model: m.default, action: () => { setModelModal('default'); setSelectedModelId(m.default.modelId); }, badge: 'primary' as const },
                  { label: 'Fallback Model', model: m.fallback, action: () => { setModelModal('fallback'); setSelectedModelId(m.fallback.modelId); }, badge: 'warning' as const },
                ].map(r => (
                  <Card key={r.label}>
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="text-sm font-semibold text-text-primary flex items-center gap-2"><Cpu size={15} className="text-primary" />{r.label}</h3>
                      {isAdmin && <Button variant="default" size="sm" onClick={r.action}>Change</Button>}
                    </div>
                    <div className="rounded-xl bg-surface-dim p-3 space-y-1.5">
                      <p className="text-base font-semibold text-text-primary">{r.model.modelName || '—'}</p>
                      <p className="text-[10px] font-mono text-text-muted">{r.model.modelId}</p>
                      <div className="flex gap-2">
                        <Badge color={r.badge}>In: ${r.model.inputRate}/1M tokens</Badge>
                        <Badge color={r.badge}>Out: ${r.model.outputRate}/1M tokens</Badge>
                      </div>
                    </div>
                  </Card>
                ))}
              </div>

              <div className="rounded-xl border border-dark-border/40 bg-surface-dim px-4 py-3 flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-text-primary">Per-Position & Per-Employee Model Overrides</p>
                  <p className="text-xs text-text-muted">Configure in Agent Factory → Configuration tab</p>
                </div>
                <Badge color="info">{Object.keys({...m.positionOverrides,...(m.employeeOverrides||{})}).length} overrides active</Badge>
              </div>

              <Card>
                <h3 className="text-sm font-semibold text-text-primary mb-1">Available Models — Unit Pricing</h3>
                <p className="text-xs text-text-muted mb-4">Prices per 1 million tokens. Switch default model from the cards above.</p>
                <div className="space-y-2">
                  {m.availableModels.map((mo: any) => {
                    const isDefault = mo.modelId === m.default.modelId;
                    const isFallback = mo.modelId === m.fallback.modelId;
                    const modelSpend = byModel.find(bm => bm.model?.includes(mo.modelId?.split('/').pop()?.split(':')[0] || '??'));
                    return (
                      <div key={mo.modelId} className={`flex items-center gap-4 rounded-xl px-4 py-3 ${isDefault ? 'bg-primary/5 border border-primary/20' : isFallback ? 'bg-warning/5 border border-warning/20' : 'bg-surface-dim'}`}>
                        <div className={`w-2 h-2 rounded-full shrink-0 ${mo.enabled ? 'bg-success' : 'bg-text-muted'}`} />
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-text-primary">{mo.modelName}</p>
                          <p className="text-[10px] font-mono text-text-muted">{mo.modelId}</p>
                        </div>
                        <div className="text-right shrink-0 space-y-0.5">
                          <p className="text-xs text-text-muted">${mo.inputRate} in · ${mo.outputRate} out <span className="text-text-muted/60">per 1M</span></p>
                          {modelSpend && <p className="text-xs text-success font-medium">${modelSpend.cost?.toFixed(4)} spent · {(modelSpend.inputTokens+modelSpend.outputTokens)/1000}k tokens</p>}
                        </div>
                        {isDefault && <Badge color="primary">Default</Badge>}
                        {isFallback && <Badge color="warning">Fallback</Badge>}
                        {isAdmin && !isDefault && !isFallback && mo.enabled && (
                          <Button variant="ghost" size="sm" onClick={() => updateDefault.mutate({ modelId: mo.modelId, modelName: mo.modelName, inputRate: mo.inputRate, outputRate: mo.outputRate })}>Set Default</Button>
                        )}
                      </div>
                    );
                  })}
                </div>
              </Card>

              <div className="rounded-xl bg-info/5 border border-info/20 px-4 py-3 text-xs text-info">
                Model changes take effect on the next agent cold start (~15 min idle timeout). Actual Bedrock billing may differ slightly from estimated costs.
              </div>
            </div>
          )}
        </div>
      </Card>

      {/* Skill user drill-down */}
      {selectedSkillId && (
        <Modal open={true} onClose={() => setSelectedSkillId('')}
          title={`Skill Calls — ${selectedSkillId}`}
          footer={<div className="flex justify-end"><Button variant="default" onClick={() => setSelectedSkillId('')}>Close</Button></div>}>
          {skillUsersLoading ? (
            <p className="py-6 text-center text-sm text-text-muted">Loading usage...</p>
          ) : (
            <Table
              columns={[
                { key: 'employee', label: 'Employee', render: (item: typeof skillUsers[0]) => <span className="font-medium">{item.employeeName}</span> },
                { key: 'department', label: 'Department', render: (item: typeof skillUsers[0]) => item.departmentName || '—' },
                { key: 'calls', label: 'Calls', render: (item: typeof skillUsers[0]) => item.calls },
                { key: 'serverless', label: 'Serverless', render: (item: typeof skillUsers[0]) => item.serverlessCalls },
                { key: 'alwaysOn', label: 'Always-On', render: (item: typeof skillUsers[0]) => item.alwaysOnCalls },
                { key: 'lastUsed', label: 'Last Used', render: (item: typeof skillUsers[0]) => item.lastUsedAt ? new Date(item.lastUsedAt).toLocaleString() : '—' },
              ]}
              data={skillUsers}
            />
          )}
        </Modal>
      )}

      {/* Model change modal */}
      {isAdmin && modelModal && (
        <Modal open={true} onClose={() => setModelModal(null)}
          title={modelModal === 'default' ? 'Change Default Model' : 'Change Fallback Model'}
          footer={<div className="flex justify-end gap-3"><Button variant="default" onClick={() => setModelModal(null)}>Cancel</Button><Button variant="primary" onClick={handleModelSave}>Apply</Button></div>}>
          <div className="space-y-4">
            <Select label="Model" value={selectedModelId} onChange={setSelectedModelId} options={modelOptions} placeholder="Select model..." />
          </div>
        </Modal>
      )}

      {/* Budget edit modal */}
      {isAdmin && budgetModal && (
        <Modal open={true} onClose={() => setBudgetModal(false)}
          title="Edit Department Budgets"
          footer={<div className="flex justify-end gap-3">
            <Button variant="default" onClick={() => setBudgetModal(false)}>Cancel</Button>
            <Button variant="primary" onClick={handleBudgetSave} disabled={updateBudgets.isPending}>
              <Save size={13} /> {updateBudgets.isPending ? 'Saving...' : 'Save Budgets'}
            </Button>
          </div>}>
          <p className="text-xs text-text-muted mb-4">Set monthly budget (USD) per department. Changes are audited.</p>
          <div className="space-y-3">
            {budgets.map(b => (
              <div key={b.department} className="flex items-center gap-3">
                <span className="text-sm text-text-primary w-40 shrink-0">{b.department}</span>
                <div className="flex items-center gap-1">
                  <span className="text-text-muted">$</span>
                  <input
                    type="number"
                    value={budgetDraft[b.department] || ''}
                    onChange={e => setBudgetDraft(prev => ({ ...prev, [b.department]: e.target.value }))}
                    className="w-28 rounded-lg border border-dark-border bg-dark-bg px-3 py-1.5 text-sm text-text-primary focus:border-primary focus:outline-none"
                    placeholder={String(b.budget)}
                  />
                </div>
                <span className="text-xs text-text-muted">Current: ${b.budget}</span>
              </div>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}
