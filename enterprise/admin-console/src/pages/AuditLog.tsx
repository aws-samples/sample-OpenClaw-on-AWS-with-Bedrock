import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Shield, Download, Search, AlertTriangle, CheckCircle, XCircle, Info, Clock, Brain, Scan, Loader, Sparkles, ShieldAlert, Filter } from 'lucide-react';
import { Card, Badge, Button, PageHeader, Table, StatCard, Tabs } from '../components/ui';
import { useAuditEntries, useAuditInsights, useRunAuditScan, useGuardrailEvents } from '../hooks/useApi';
import type { AuditEntry } from '../types';

const eventTypeOptions = [
  { label: 'All Events', value: 'all' },
  { label: 'Guardrail Block', value: 'guardrail_block' },
  { label: 'Agent Invocation', value: 'agent_invocation' },
  { label: 'Tool Execution', value: 'tool_execution' },
  { label: 'Permission Denied', value: 'permission_denied' },
  { label: 'Config Change', value: 'config_change' },
  { label: 'Approval Decision', value: 'approval_decision' },
  { label: 'Session Start', value: 'session_start' },
  { label: 'Session End', value: 'session_end' },
];

const badgeColor = (type: string): 'success' | 'danger' | 'warning' | 'info' | 'default' => {
  switch (type) {
    case 'agent_invocation': return 'success';
    case 'permission_denied': return 'danger';
    case 'config_change': return 'warning';
    case 'approval_decision': return 'info';
    case 'tool_execution': return 'success';
    default: return 'default';
  }
};

const statusIcon = (s: string) => {
  switch (s) {
    case 'success': return <CheckCircle size={14} className="text-green-400" />;
    case 'blocked': return <XCircle size={14} className="text-red-400" />;
    case 'warning': return <AlertTriangle size={14} className="text-amber-400" />;
    default: return <Info size={14} className="text-blue-400" />;
  }
};

export default function AuditLog() {
  const [filterText, setFilterText] = useState('');
  const [eventType, setEventType] = useState('all');
  const [activeTab, setActiveTab] = useState('insights');
  const [currentPage, setCurrentPage] = useState(1);
  const pageSize = 10;
  const navigate = useNavigate();

  const { data: AUDIT_ENTRIES = [] } = useAuditEntries({ limit: 50, eventType: eventType !== 'all' ? eventType : undefined });
  const { data: insightsData, refetch: refetchInsights } = useAuditInsights();
  const { data: guardrailData } = useGuardrailEvents(50);
  const runScan = useRunAuditScan();
  const insights = insightsData?.insights || [];
  const insightsSummary = insightsData?.summary;
  const guardrailEvents = guardrailData?.events || [];

  // Compute stats
  const stats = useMemo(() => {
    const total = AUDIT_ENTRIES.length;
    const blocked = AUDIT_ENTRIES.filter(e => e.status === 'blocked').length;
    const invocations = AUDIT_ENTRIES.filter(e => e.eventType === 'agent_invocation').length;
    const toolExecs = AUDIT_ENTRIES.filter(e => e.eventType === 'tool_execution').length;
    const configChanges = AUDIT_ENTRIES.filter(e => e.eventType === 'config_change').length;
    const byType: Record<string, number> = {};
    AUDIT_ENTRIES.forEach(e => { byType[e.eventType] = (byType[e.eventType] || 0) + 1; });
    const byActor: Record<string, number> = {};
    AUDIT_ENTRIES.forEach(e => { byActor[e.actorName] = (byActor[e.actorName] || 0) + 1; });
    const topActors = Object.entries(byActor).sort((a, b) => b[1] - a[1]).slice(0, 5);
    return { total, blocked, invocations, toolExecs, configChanges, byType, topActors };
  }, [AUDIT_ENTRIES]);

  const filtered = AUDIT_ENTRIES.filter(e => {
    const matchesText = !filterText ||
      e.actorName.toLowerCase().includes(filterText.toLowerCase()) ||
      e.detail.toLowerCase().includes(filterText.toLowerCase());
    const matchesType = eventType === 'all' || e.eventType === eventType;
    return matchesText && matchesType;
  });

  const paginated = filtered.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const totalPages = Math.ceil(filtered.length / pageSize);

  return (
    <div>
      <PageHeader
        title="Audit Center"
        description="Conversation audit, sensitive operation logs, and compliance tracking"
        actions={<Button variant="default" onClick={() => {
          const csv = ['Timestamp,Event Type,Actor,Target,Detail,Status', ...AUDIT_ENTRIES.map(e =>
            `"${e.timestamp}","${e.eventType}","${e.actorName}","${e.targetType}","${e.detail.replace(/"/g, '""')}","${e.status}"`)].join('\n');
          const blob = new Blob([csv], { type: 'text/csv' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a'); a.href = url; a.download = `audit-export-${new Date().toISOString().slice(0,10)}.csv`; a.click();
          URL.revokeObjectURL(url);
        }}><Download size={16} /> Export CSV</Button>}
      />

      {/* Summary Stats */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-6 mb-6">
        <StatCard title="Total Events" value={stats.total} icon={<Shield size={22} />} color="primary" />
        <StatCard title="Agent Invocations" value={stats.invocations} icon={<CheckCircle size={22} />} color="success" />
        <StatCard title="Tool Executions" value={stats.toolExecs} icon={<Clock size={22} />} color="info" />
        <StatCard title="Permission Denied" value={stats.blocked} icon={<XCircle size={22} />} color="danger" />
        <StatCard title="Guardrail Blocks" value={guardrailEvents.length} icon={<ShieldAlert size={22} />} color="warning" />
        <StatCard title="Config Changes" value={stats.configChanges} icon={<AlertTriangle size={22} />} color="warning" />
      </div>

      <Card>
        <Tabs
          tabs={[
            { id: 'insights', label: 'AI Insights', count: insightsSummary?.high || undefined },
            { id: 'timeline', label: 'Event Timeline', count: filtered.length },
            { id: 'breakdown', label: 'Breakdown' },
            { id: 'security', label: 'Security Alerts', count: stats.blocked || undefined },
            { id: 'guardrail', label: 'Guardrail Events', count: guardrailEvents.length || undefined },
          ]}
          activeTab={activeTab}
          onChange={setActiveTab}
        />

        <div className="mt-4">
          {activeTab === 'insights' && (
            <div>
              {/* Scan Status Banner */}
              <div className="flex items-center justify-between rounded-lg bg-primary/5 border border-primary/20 px-4 py-3 mb-5">
                <div className="flex items-center gap-3">
                  <Brain size={20} className="text-primary-light" />
                  <div>
                    <p className="text-sm font-medium text-text-primary">AI Security Scanner</p>
                    <p className="text-xs text-text-muted">
                      Last scan: {insightsSummary?.lastScanAt ? new Date(insightsSummary.lastScanAt).toLocaleString() : '—'}
                      {insightsSummary?.scanSources && ` · Sources: ${insightsSummary.scanSources.join(', ')}`}
                    </p>
                  </div>
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="default" size="sm"
                    disabled={runScan.isPending}
                    onClick={() => runScan.mutate()}
                  >
                    {runScan.isPending ? <Loader size={14} className="animate-spin" /> : <Scan size={14} />}
                    {runScan.isPending ? 'Scanning...' : 'Run Scan'}
                  </Button>
                  <Button
                    variant="default" size="sm"
                    className="opacity-60 cursor-not-allowed"
                    onClick={() => {}}
                  >
                    <Sparkles size={14} />
                    Analyze Memories
                    <span className="ml-1 text-[10px] bg-primary/20 text-primary px-1.5 py-0.5 rounded-full">Soon</span>
                  </Button>
                </div>
              </div>

              {/* Severity Summary */}
              <div className="grid grid-cols-3 gap-4 mb-5">
                {[
                  { label: 'High', count: insightsSummary?.high || 0, color: '#ef4444', bg: 'bg-red-500/5 border-red-500/20' },
                  { label: 'Medium', count: insightsSummary?.medium || 0, color: '#f59e0b', bg: 'bg-amber-500/5 border-amber-500/20' },
                  { label: 'Low', count: insightsSummary?.low || 0, color: '#06b6d4', bg: 'bg-cyan-500/5 border-cyan-500/20' },
                ].map(s => (
                  <div key={s.label} className={`rounded-lg border p-4 text-center ${s.bg}`}>
                    <p className="text-3xl font-bold" style={{ color: s.color }}>{s.count}</p>
                    <p className="text-xs text-text-muted uppercase tracking-wider mt-1">{s.label} Severity</p>
                  </div>
                ))}
              </div>

              {/* Insight Cards */}
              {insights.length === 0 ? (
                <div className="text-center py-12 text-text-muted">
                  <Brain size={32} className="mx-auto mb-3 text-text-muted" />
                  <p className="text-sm">No insights yet. Run a scan or wait for the next scheduled analysis.</p>
                </div>
              ) : (
              <div className="space-y-3">
                {insights.map(insight => {
                  const sevColors: Record<string, { border: string; bg: string; text: string; icon: typeof AlertTriangle }> = {
                    high: { border: 'border-red-500/20', bg: 'bg-red-500/5', text: 'text-red-400', icon: XCircle },
                    medium: { border: 'border-amber-500/20', bg: 'bg-amber-500/5', text: 'text-amber-400', icon: AlertTriangle },
                    low: { border: 'border-cyan-500/20', bg: 'bg-cyan-500/5', text: 'text-cyan-400', icon: Info },
                  };
                  const sev = sevColors[insight.severity] || sevColors.low;
                  const SevIcon = sev.icon;
                  return (
                    <div key={insight.id} className={`rounded-lg border ${sev.border} ${sev.bg} p-4`}>
                      <div className="flex items-start gap-3">
                        <SevIcon size={18} className={`${sev.text} mt-0.5 shrink-0`} />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap mb-1">
                            <span className={`text-sm font-semibold ${sev.text}`}>{insight.title}</span>
                            <Badge color={insight.severity === 'high' ? 'danger' : insight.severity === 'medium' ? 'warning' : 'info'}>{insight.severity}</Badge>
                            <Badge>{insight.category.replace(/_/g, ' ')}</Badge>
                          </div>
                          <p className="text-sm text-text-secondary mb-2">{insight.description}</p>
                          <div className="rounded-lg bg-dark-bg/50 px-3 py-2 mb-2">
                            <p className="text-xs text-text-muted mb-0.5">Recommendation</p>
                            <p className="text-sm text-text-primary">{insight.recommendation}</p>
                          </div>
                          <div className="flex items-center gap-4 text-xs text-text-muted">
                            <span>Affected: {insight.affectedUsers.map((u, i) => (
                              <span key={u}>{i > 0 && ', '}<button onClick={() => navigate('/org/employees')} className="text-primary-light hover:underline">{u}</button></span>
                            ))}</span>
                            <span>Source: {insight.source.replace(/_/g, ' ')}</span>
                            <span>{new Date(insight.detectedAt).toLocaleTimeString()}</span>
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
              )}
            </div>
          )}

          {activeTab === 'timeline' && (
            <>
              {/* Filters */}
              <div className="mb-4 flex flex-wrap items-center gap-3">
                <div className="relative flex-1 max-w-sm">
                  <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
                  <input type="text" value={filterText} onChange={e => { setFilterText(e.target.value); setCurrentPage(1); }}
                    placeholder="Search by actor or detail..."
                    className="w-full rounded-lg border border-dark-border bg-dark-bg py-2 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none" />
                </div>
                <select value={eventType} onChange={e => { setEventType(e.target.value); setCurrentPage(1); }}
                  className="rounded-lg border border-dark-border bg-dark-bg px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none">
                  {eventTypeOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>

              {/* Timeline View */}
              <div className="space-y-1">
                {paginated.map((e, i) => (
                  <div key={e.id} className="flex items-start gap-4 rounded-lg px-4 py-3 hover:bg-dark-hover transition-colors">
                    <div className="mt-0.5">{statusIcon(e.status)}</div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium text-sm text-text-primary">{e.actorName}</span>
                        <Badge color={badgeColor(e.eventType)}>{e.eventType.replace(/_/g, ' ')}</Badge>
                        <Badge>{e.targetType}</Badge>
                      </div>
                      <p className="text-sm text-text-secondary mt-0.5">{e.detail}</p>
                    </div>
                    <div className="text-right shrink-0">
                      <p className="text-xs text-text-muted">{new Date(e.timestamp).toLocaleTimeString()}</p>
                      <p className="text-[10px] text-text-muted">{new Date(e.timestamp).toLocaleDateString()}</p>
                    </div>
                  </div>
                ))}
              </div>

              {totalPages > 1 && (
                <div className="mt-4 flex items-center justify-between">
                  <p className="text-sm text-text-muted">Page {currentPage} of {totalPages} ({filtered.length} events)</p>
                  <div className="flex gap-2">
                    <Button variant="default" size="sm" onClick={() => setCurrentPage(p => Math.max(1, p - 1))} disabled={currentPage === 1}>Previous</Button>
                    <Button variant="default" size="sm" onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))} disabled={currentPage === totalPages}>Next</Button>
                  </div>
                </div>
              )}
            </>
          )}

          {activeTab === 'breakdown' && (
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              {/* Event Type Distribution */}
              <div>
                <h3 className="text-sm font-semibold text-text-primary mb-4">Events by Type</h3>
                <div className="space-y-3">
                  {Object.entries(stats.byType).sort((a, b) => b[1] - a[1]).map(([type, count]) => {
                    const pct = stats.total > 0 ? Math.round((count / stats.total) * 100) : 0;
                    const colors: Record<string, string> = {
                      agent_invocation: '#10b981', tool_execution: '#06b6d4', permission_denied: '#ef4444',
                      config_change: '#f59e0b', approval_decision: '#6366f1', session_start: '#8b5cf6', session_end: '#64748b',
                    };
                    return (
                      <div key={type}>
                        <div className="flex justify-between text-sm mb-1">
                          <span className="text-text-secondary">{type.replace(/_/g, ' ')}</span>
                          <span className="text-text-muted">{count} ({pct}%)</span>
                        </div>
                        <div className="h-2.5 rounded-full bg-dark-bg overflow-hidden">
                          <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, backgroundColor: colors[type] || '#6366f1' }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Top Actors */}
              <div>
                <h3 className="text-sm font-semibold text-text-primary mb-4">Most Active Users</h3>
                <div className="space-y-2">
                  {stats.topActors.map(([name, count], i) => (
                    <div key={name} className="flex items-center justify-between rounded-lg bg-dark-bg px-4 py-3">
                      <div className="flex items-center gap-3">
                        <span className="text-xs text-text-muted font-mono w-5">#{i + 1}</span>
                        <span className="text-sm font-medium">{name}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <div className="w-20 h-2 rounded-full bg-dark-hover overflow-hidden">
                          <div className="h-full rounded-full bg-primary" style={{ width: `${(count / stats.topActors[0][1]) * 100}%` }} />
                        </div>
                        <span className="text-xs text-text-muted w-8 text-right">{count}</span>
                      </div>
                    </div>
                  ))}
                </div>

                <h3 className="text-sm font-semibold text-text-primary mt-6 mb-4">Status Distribution</h3>
                <div className="grid grid-cols-2 gap-3">
                  {[
                    { label: 'Success', count: AUDIT_ENTRIES.filter(e => e.status === 'success').length, color: '#10b981', icon: <CheckCircle size={16} /> },
                    { label: 'Blocked', count: AUDIT_ENTRIES.filter(e => e.status === 'blocked').length, color: '#ef4444', icon: <XCircle size={16} /> },
                    { label: 'Warning', count: AUDIT_ENTRIES.filter(e => e.status === 'warning').length, color: '#f59e0b', icon: <AlertTriangle size={16} /> },
                    { label: 'Info', count: AUDIT_ENTRIES.filter(e => e.status === 'info').length, color: '#6366f1', icon: <Info size={16} /> },
                  ].map(s => (
                    <div key={s.label} className="rounded-lg bg-dark-bg p-3 flex items-center gap-3">
                      <div style={{ color: s.color }}>{s.icon}</div>
                      <div>
                        <p className="text-lg font-semibold" style={{ color: s.color }}>{s.count}</p>
                        <p className="text-[10px] text-text-muted uppercase tracking-wider">{s.label}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {activeTab === 'guardrail' && (
            <div>
              <div className="rounded-xl bg-warning/5 border border-warning/20 px-4 py-3 text-xs text-warning mb-4 flex items-center gap-2">
                <ShieldAlert size={16} />
                <span>Bedrock Guardrail intercepts — every blocked user input and filtered agent output. Standard Runtime only. Exec Runtime has no guardrail restrictions.</span>
              </div>

              {guardrailEvents.length === 0 ? (
                <div className="text-center py-12 text-text-muted">
                  <ShieldAlert size={32} className="mx-auto mb-3 opacity-30" />
                  <p className="text-sm">No guardrail blocks yet</p>
                  <p className="text-xs mt-1">Blocks appear here when Standard Runtime agents trigger topic denial or PII rules.</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {guardrailEvents.map(e => (
                    <div key={e.id} className="flex items-start gap-4 rounded-xl bg-amber-500/5 border border-amber-500/20 px-4 py-3">
                      <ShieldAlert size={18} className="text-amber-400 mt-0.5 shrink-0" />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap mb-1">
                          <span className="text-sm font-semibold text-amber-300">{e.actorName}</span>
                          <Badge color="warning">{e.guardrailSource === 'INPUT' ? 'Input blocked' : 'Output filtered'}</Badge>
                          {e.guardrailPolicy && <Badge color="default">{e.guardrailPolicy.replace(/-/g, ' ')}</Badge>}
                          <span className="text-xs font-mono text-text-muted">{e.guardrailId} v{e.guardrailVersion}</span>
                        </div>
                        <p className="text-sm text-text-secondary truncate">{e.detail}</p>
                        <p className="text-xs text-text-muted mt-1">{new Date(e.timestamp).toLocaleString()}</p>
                      </div>
                      <Badge color="danger">Blocked</Badge>
                    </div>
                  ))}
                </div>
              )}

              <div className="mt-4 rounded-xl bg-dark-bg p-4">
                <h4 className="text-sm font-medium text-text-primary mb-2">Guardrail Enforcement Summary</h4>
                <div className="grid grid-cols-3 gap-4 text-center">
                  <div>
                    <p className="text-2xl font-semibold text-amber-400">{guardrailEvents.length}</p>
                    <p className="text-xs text-text-muted">Total Blocks</p>
                  </div>
                  <div>
                    <p className="text-2xl font-semibold text-text-primary">{guardrailEvents.filter(e => e.guardrailSource === 'INPUT').length}</p>
                    <p className="text-xs text-text-muted">Input Blocked</p>
                  </div>
                  <div>
                    <p className="text-2xl font-semibold text-text-primary">{guardrailEvents.filter(e => e.guardrailSource === 'OUTPUT').length}</p>
                    <p className="text-xs text-text-muted">Output Filtered</p>
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'security' && (
            <div>
              <p className="text-sm text-text-secondary mb-4">Permission denials and security-relevant events. These indicate policy enforcement working correctly, or potential unauthorized access attempts.</p>
              <div className="space-y-2">
                {AUDIT_ENTRIES.filter(e => e.status === 'blocked' || e.eventType === 'permission_denied').map(e => (
                  <div key={e.id} className="flex items-start gap-4 rounded-lg bg-red-500/5 border border-red-500/10 px-4 py-3">
                    <XCircle size={18} className="text-red-400 mt-0.5 shrink-0" />
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-sm text-red-300">{e.actorName}</span>
                        <Badge color="danger">{e.eventType.replace(/_/g, ' ')}</Badge>
                      </div>
                      <p className="text-sm text-text-secondary mt-0.5">{e.detail}</p>
                      <p className="text-xs text-text-muted mt-1">{new Date(e.timestamp).toLocaleString()}</p>
                    </div>
                    <Badge color="danger">Blocked</Badge>
                  </div>
                ))}
                {AUDIT_ENTRIES.filter(e => e.status === 'blocked').length === 0 && (
                  <div className="text-center py-8 text-text-muted">
                    <Shield size={32} className="mx-auto mb-2 text-green-400" />
                    <p className="text-sm">No security alerts — all clear</p>
                  </div>
                )}
              </div>
              <div className="mt-4 rounded-lg bg-dark-bg p-4">
                <h4 className="text-sm font-medium text-text-primary mb-2">Policy Enforcement Summary</h4>
                <div className="grid grid-cols-3 gap-4 text-center">
                  <div>
                    <p className="text-2xl font-semibold text-green-400">{AUDIT_ENTRIES.filter(e => e.status === 'success').length}</p>
                    <p className="text-xs text-text-muted">Allowed</p>
                  </div>
                  <div>
                    <p className="text-2xl font-semibold text-red-400">{AUDIT_ENTRIES.filter(e => e.status === 'blocked').length}</p>
                    <p className="text-xs text-text-muted">Blocked</p>
                  </div>
                  <div>
                    <p className="text-2xl font-semibold text-blue-400">{stats.total > 0 ? ((1 - AUDIT_ENTRIES.filter(e => e.status === 'blocked').length / stats.total) * 100).toFixed(1) : 100}%</p>
                    <p className="text-xs text-text-muted">Compliance Rate</p>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
