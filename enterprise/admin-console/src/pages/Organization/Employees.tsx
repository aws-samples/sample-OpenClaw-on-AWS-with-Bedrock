import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Users, Plus, Search, Zap, Bot, MessageSquare, Clock, Pencil, Trash2, AlertTriangle } from 'lucide-react';
import { Card, Badge, Button, PageHeader, Modal, Input, Select, StatusDot, StatCard } from '../../components/ui';
import { useEmployees, usePositions, useAgents, useBindings, useCreateEmployee, useUpdateEmployee, useDeleteEmployee, useEmployeeActivities } from '../../hooks/useApi';
import { CHANNEL_LABELS } from '../../types';
import type { Employee, ChannelType } from '../../types';

const STATUS_COLORS: Record<string, string> = { online: '#10b981', idle: '#f59e0b', offline: '#6b7280' };

function timeAgo(isoDate: string): string {
  if (!isoDate) return 'Never';
  const now = Date.now();
  const then = new Date(isoDate).getTime();
  if (isNaN(then)) return 'Unknown';
  const diff = now - then;
  const mins = Math.floor(diff / 60000);
  if (mins < 0) return 'just now'; // future date (seed data)
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} hr ago`;
  return `${Math.floor(hrs / 24)} day ago`;
}

function Initials({ name, size = 32 }: { name: string; size?: number }) {
  const initials = name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
  const hue = name.split('').reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
  return (
    <div
      className="rounded-full flex items-center justify-center font-medium text-white shrink-0"
      style={{ width: size, height: size, fontSize: size * 0.38, backgroundColor: `hsl(${hue}, 55%, 45%)` }}
    >{initials}</div>
  );
}

export default function Employees() {
  const { data: EMPLOYEES = [] } = useEmployees();
  const { data: POSITIONS = [] } = usePositions();
  const { data: AGENTS = [] } = useAgents();
  const { data: BINDINGS = [] } = useBindings();
  const { data: activityList = [] } = useEmployeeActivities();
  const createEmployee = useCreateEmployee();
  const updateEmployee = useUpdateEmployee();
  const deleteEmployee = useDeleteEmployee();
  const navigate = useNavigate();
  const [editingEmp, setEditingEmp] = useState<Employee | null>(null);
  const [deletingEmp, setDeletingEmp] = useState<Employee | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [deleteBlockInfo, setDeleteBlockInfo] = useState<{ agentBindings: number; imMappings: number } | null>(null);

  // Build lookup maps from API data
  const activityMap = useMemo(() => {
    const map: Record<string, any> = {};
    activityList.forEach(a => { if (a.employeeId) map[a.employeeId] = a; });
    return map;
  }, [activityList]);
  const [filterText, setFilterText] = useState('');
  const [filterDept, setFilterDept] = useState('all');
  const [filterStatus, setFilterStatus] = useState('all');
  const [showCreate, setShowCreate] = useState(false);
  const [selected, setSelected] = useState<Employee | null>(null);
  const [newName, setNewName] = useState('');
  const [newEmail, setNewEmail] = useState('');
  const [newNo, setNewNo] = useState('');
  const [newPos, setNewPos] = useState('');
  const [newChannels, setNewChannels] = useState<string[]>(['slack']);

  const posOptions = POSITIONS.map(p => ({ label: `${p.name} (${p.departmentName})`, value: p.id }));
  const departments = useMemo(() => {
    const set = new Set(EMPLOYEES.map(e => e.departmentName));
    return Array.from(set).sort();
  }, [EMPLOYEES]);

  const bound = EMPLOYEES.filter(e => e.agentId).length;
  const unbound = EMPLOYEES.length - bound;
  const totalMessages = activityList.reduce((s, a) => s + (a.messagesThisWeek || 0), 0);
  // Active agents: based on agent status (active = invoked within 15 min)
  const onlineNow = AGENTS.filter(a => a.status === 'active').length
    || activityList.filter(a => {
      const cs = a.channelStatus || {};
      return Object.values(cs).some(s => s === 'online');
    }).length;

  const filtered = EMPLOYEES.filter(e => {
    if (filterText && !e.name.toLowerCase().includes(filterText.toLowerCase()) && !e.positionName.toLowerCase().includes(filterText.toLowerCase())) return false;
    if (filterDept !== 'all' && e.departmentName !== filterDept) return false;
    if (filterStatus === 'bound' && !e.agentId) return false;
    if (filterStatus === 'unbound' && e.agentId) return false;
    if (filterStatus === 'online' && !Object.values((activityMap[e.id]?.channelStatus) || {}).some((s: any) => s === 'online')) return false;
    return true;
  });

  return (
    <div>
      <PageHeader
        title="Employee Management"
        description="Employee profiles, agent bindings, channel activity, and engagement metrics"
        actions={<Button variant="primary" onClick={() => setShowCreate(true)}><Plus size={16} /> Add Employee</Button>}
      />

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-5 mb-6">
        <StatCard title="Total Employees" value={EMPLOYEES.length} icon={<Users size={22} />} color="primary" />
        <StatCard title="Agent Bound" value={bound} icon={<Bot size={22} />} color="success" />
        <StatCard title="Unbound" value={unbound} icon={<Zap size={22} />} color={unbound > 0 ? 'warning' : 'success'} />
        <StatCard title="Online Now" value={onlineNow} icon={<MessageSquare size={22} />} color="cyan" />
        <StatCard title="Messages/Week" value={totalMessages} icon={<Clock size={22} />} color="info" />
      </div>

      {/* Filters */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
          <input type="text" value={filterText} onChange={e => setFilterText(e.target.value)}
            placeholder="Search name or position..."
            className="w-full rounded-lg border border-dark-border bg-dark-card py-2 pl-9 pr-4 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none" />
        </div>
        <select value={filterDept} onChange={e => setFilterDept(e.target.value)}
          className="rounded-lg border border-dark-border bg-dark-card px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none">
          <option value="all">All Departments</option>
          {departments.map(d => <option key={d} value={d}>{d}</option>)}
        </select>
        <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)}
          className="rounded-lg border border-dark-border bg-dark-card px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none">
          <option value="all">All Status</option>
          <option value="bound">Bound</option>
          <option value="unbound">Unbound</option>
          <option value="online">Online Now</option>
        </select>
        <Badge color="info">{filtered.length} results</Badge>
      </div>

      {/* Employee Table */}
      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-dark-border text-left">
                <th className="pb-3 pl-4 text-xs font-medium text-text-muted uppercase tracking-wider">Employee</th>
                <th className="pb-3 text-xs font-medium text-text-muted uppercase tracking-wider">Position</th>
                <th className="pb-3 text-xs font-medium text-text-muted uppercase tracking-wider">Channels</th>
                <th className="pb-3 text-xs font-medium text-text-muted uppercase tracking-wider">Agent</th>
                <th className="pb-3 text-xs font-medium text-text-muted uppercase tracking-wider">Activity</th>
                <th className="pb-3 text-xs font-medium text-text-muted uppercase tracking-wider">Last Active</th>
                <th className="pb-3 pr-4 w-16" />
              </tr>
            </thead>
            <tbody>
              {filtered.map(e => {
                const agent = AGENTS.find(a => a.id === e.agentId);
                const activity = activityMap[e.id];
                const channelStatus = activity?.channelStatus || {};
                return (
                  <tr key={e.id} onClick={() => setSelected(e)} className="group/row border-b border-dark-border/50 hover:bg-dark-hover cursor-pointer transition-colors">
                    <td className="py-3 pl-4">
                      <div className="flex items-center gap-3">
                        <div className="relative">
                          <Initials name={e.name} size={36} />
                          {Object.values(channelStatus).some(s => s === 'online') && (
                            <div className="absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full bg-green-500 border-2 border-dark-card" />
                          )}
                        </div>
                        <div>
                          <p className="font-medium text-text-primary">{e.name}</p>
                          <p className="text-xs text-text-muted">{e.employeeNo} · {e.departmentName}</p>
                        </div>
                      </div>
                    </td>
                    <td className="py-3"><Badge>{e.positionName}</Badge></td>
                    <td className="py-3">
                      <div className="flex items-center gap-1.5">
                        {(e.channels || []).map(c => {
                          const st = channelStatus[c] || 'offline';
                          return (
                            <div key={c} className="flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px]" style={{ backgroundColor: STATUS_COLORS[st] + '15', color: STATUS_COLORS[st] }}>
                              <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: STATUS_COLORS[st] }} />
                              {CHANNEL_LABELS[c as ChannelType]}
                            </div>
                          );
                        })}
                      </div>
                    </td>
                    <td className="py-3">
                      {agent ? (
                        <button onClick={(ev) => { ev.stopPropagation(); navigate(`/agents/${agent.id}`); }} className="flex items-center gap-1.5 text-primary-light hover:underline">
                          <Bot size={14} className="text-green-400" />
                          <span className="text-xs truncate max-w-[120px]">{agent.name.split(' - ')[0]}</span>
                          {agent.qualityScore && <span className="text-[10px] text-amber-400">⭐{agent.qualityScore}</span>}
                        </button>
                      ) : (
                        <Badge color="warning">Unbound</Badge>
                      )}
                    </td>
                    <td className="py-3">
                      {activity ? (
                        <div className="flex items-center gap-2">
                          <div className="w-16 h-4 flex items-end gap-px">
                            {/* Mini bar chart for weekly activity */}
                            {[0.3, 0.5, 0.8, 0.6, 1.0, 0.7, 0.4].map((v, i) => (
                              <div key={i} className="flex-1 rounded-t-sm bg-primary/40" style={{ height: `${v * 100}%` }} />
                            ))}
                          </div>
                          <span className="text-xs text-text-muted">{activity.messagesThisWeek}/wk</span>
                        </div>
                      ) : (
                        <span className="text-xs text-text-muted">—</span>
                      )}
                    </td>
                    <td className="py-3">
                      {activity ? (
                        <span className={`text-xs ${timeAgo(activity.lastActive).includes('min') ? 'text-green-400' : timeAgo(activity.lastActive).includes('hr') ? 'text-text-secondary' : 'text-text-muted'}`}>
                          {timeAgo(activity.lastActive)}
                        </span>
                      ) : (
                        <span className="text-xs text-text-muted">Never</span>
                      )}
                    </td>
                    <td className="py-3 pr-4">
                      <div className="flex items-center gap-1 opacity-0 group-hover/row:opacity-100 transition-opacity">
                        <button onClick={ev => { ev.stopPropagation(); setEditingEmp(e); }} className="p-1.5 rounded hover:bg-dark-bg text-text-muted hover:text-text-primary"><Pencil size={13} /></button>
                        <button onClick={ev => { ev.stopPropagation(); setDeletingEmp(e); setDeleteError(''); setDeleteBlockInfo(null); }} className="p-1.5 rounded hover:bg-dark-bg text-text-muted hover:text-danger"><Trash2 size={13} /></button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Detail Modal */}
      <Modal open={!!selected} onClose={() => setSelected(null)} title="" size="lg">
        {selected && (() => {
          const agent = AGENTS.find(a => a.id === selected.agentId);
          const empBindings = BINDINGS.filter(b => b.employeeId === selected.id);
          const activity = activityMap[selected.id];
          const channelStatus = activity?.channelStatus || {};
          return (
            <div className="space-y-5">
              {/* Header */}
              <div className="flex items-center gap-4">
                <Initials name={selected.name} size={56} />
                <div>
                  <h2 className="text-lg font-semibold text-text-primary">{selected.name}</h2>
                  <p className="text-sm text-text-secondary">{selected.positionName} · {selected.departmentName}</p>
                  <p className="text-xs text-text-muted">{selected.employeeNo}{selected.email ? ` · ${selected.email}` : ''}</p>
                </div>
              </div>

              {/* Activity KPIs */}
              {activity && (
                <div className="grid grid-cols-4 gap-3">
                  {[
                    { label: 'Messages/Week', value: activity.messagesThisWeek, color: 'text-primary-light' },
                    { label: 'Avg Response', value: `${parseFloat(activity.avgResponseSec || '0')}s`, color: 'text-blue-400' },
                    { label: 'Top Tool', value: activity.topTool, color: 'text-text-secondary' },
                    { label: 'Satisfaction', value: `⭐ ${parseFloat(activity.satisfaction || '0')}`, color: 'text-amber-400' },
                  ].map(kpi => (
                    <div key={kpi.label} className="rounded-lg bg-dark-bg p-3 text-center">
                      <p className={`text-lg font-semibold ${kpi.color}`}>{kpi.value}</p>
                      <p className="text-[10px] text-text-muted uppercase tracking-wider">{kpi.label}</p>
                    </div>
                  ))}
                </div>
              )}

              {/* Channel Status */}
              <div>
                <p className="text-xs font-medium text-text-muted uppercase tracking-wider mb-2">Channel Status</p>
                <div className="flex flex-wrap gap-2">
                  {(selected.channels || []).map(c => {
                    const st = channelStatus[c] || 'offline';
                    return (
                      <div key={c} className="flex items-center gap-2 rounded-lg bg-dark-bg px-3 py-2">
                        <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: STATUS_COLORS[st] }} />
                        <span className="text-sm font-medium">{CHANNEL_LABELS[c as ChannelType]}</span>
                        <span className="text-xs text-text-muted capitalize">{st}</span>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Agent Info */}
              {agent ? (
                <div className="rounded-lg bg-green-500/5 border border-green-500/20 p-4">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <Bot size={18} className="text-green-400" />
                      <span className="font-medium">{agent.name}</span>
                      <StatusDot status={agent.status} />
                    </div>
                    {agent.qualityScore && <span className="text-sm text-amber-400">⭐ {agent.qualityScore}</span>}
                  </div>
                  <div className="grid grid-cols-3 gap-3 mt-3">
                    <div className="text-center">
                      <p className="text-sm font-medium">{agent.skills.length}</p>
                      <p className="text-[10px] text-text-muted">Skills</p>
                    </div>
                    <div className="text-center">
                      <p className="text-sm font-medium">{agent.channels.length}</p>
                      <p className="text-[10px] text-text-muted">Channels</p>
                    </div>
                    <div className="text-center">
                      <p className="text-sm font-medium">v{agent.soulVersions.global}.{agent.soulVersions.position}.{agent.soulVersions.personal}</p>
                      <p className="text-[10px] text-text-muted">SOUL Ver</p>
                    </div>
                  </div>
                  <div className="flex gap-2 mt-3">
                    <Button variant="default" size="sm" onClick={() => { setSelected(null); navigate(`/agents/${agent.id}`); }}>View Agent</Button>
                    <Button variant="default" size="sm" onClick={() => { setSelected(null); navigate(`/agents/${agent.id}/soul`); }}>Edit SOUL</Button>
                  </div>
                </div>
              ) : (
                <div className="rounded-lg bg-amber-500/5 border border-amber-500/20 p-4">
                  <p className="text-sm text-amber-400 mb-2">No agent bound</p>
                  <p className="text-xs text-text-muted mb-3">This employee doesn't have an AI agent yet. Create one in Agent Factory to enable AI-assisted work.</p>
                  <Button variant="primary" size="sm" onClick={() => {
                    setSelected(null);
                    navigate('/agents');
                    // Pre-fill would require state sharing; navigate to Agent Factory instead
                  }}><Bot size={14} /> Create Agent for {selected.name.split(' ')[0]}</Button>
                </div>
              )}

              {/* Bindings */}
              {empBindings.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-text-muted uppercase tracking-wider mb-2">Bindings ({empBindings.length})</p>
                  <div className="space-y-1.5">
                    {empBindings.map(b => (
                      <div key={b.id} className="flex items-center justify-between rounded-lg bg-dark-bg px-3 py-2">
                        <div className="flex items-center gap-2">
                          <span className="text-sm">{b.agentName}</span>
                          <Badge color="success">{CHANNEL_LABELS[b.channel as ChannelType]}</Badge>
                        </div>
                        <div className="flex items-center gap-2">
                          <Badge color="info">{CHANNEL_LABELS[b.channel as ChannelType]}</Badge>
                          {(b as any).source?.startsWith('auto') && <span className="text-[10px] text-text-muted">auto</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Recent Activity Timeline */}
              {activity && (
                <div>
                  <p className="text-xs font-medium text-text-muted uppercase tracking-wider mb-2">Recent Activity</p>
                  <div className="space-y-2">
                    {[
                      { time: timeAgo(activity.lastActive), action: `Used ${activity.topTool}`, channel: selected.channels[0] },
                    ].filter(Boolean).map((item, i) => (
                      <div key={i} className="flex items-start gap-3">
                        <div className="mt-1.5 w-1.5 h-1.5 rounded-full bg-text-muted shrink-0" />
                        <div className="flex-1">
                          <p className="text-sm text-text-secondary">{item.action}</p>
                          <div className="flex items-center gap-2 mt-0.5">
                            <span className="text-[10px] text-text-muted">{item.time}</span>
                            <span className="text-[10px] text-text-muted">via {CHANNEL_LABELS[item.channel as ChannelType]}</span>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          );
        })()}
      </Modal>

      {/* Edit Employee Modal */}
      <Modal open={!!editingEmp} title={editingEmp ? `Edit: ${editingEmp.name}` : ''} onClose={() => setEditingEmp(null)} footer={
        <><Button variant="ghost" onClick={() => setEditingEmp(null)}>Cancel</Button>
        <Button variant="primary" disabled={updateEmployee.isPending || !editingEmp?.name?.trim()} onClick={() => {
          if (!editingEmp) return;
          const pos = POSITIONS.find(p => p.id === editingEmp.positionId);
          updateEmployee.mutate({ id: editingEmp.id, name: editingEmp.name, email: editingEmp.email, positionId: editingEmp.positionId,
            positionName: pos?.name || editingEmp.positionName, departmentId: pos?.departmentId || editingEmp.departmentId,
            departmentName: pos?.departmentName || editingEmp.departmentName, channels: editingEmp.channels, role: editingEmp.role },
            { onSuccess: () => setEditingEmp(null) });
        }}>{updateEmployee.isPending ? 'Saving…' : 'Save'}</Button></>
      }>
        {editingEmp && <div className="space-y-4">
          <Input label="Name" value={editingEmp.name} onChange={v => setEditingEmp({ ...editingEmp, name: v })} />
          <Input label="Email (optional)" value={editingEmp.email || ''} onChange={v => setEditingEmp({ ...editingEmp, email: v })}
            placeholder="jane.doe@company.com" />
          <p className="text-[10px] text-text-muted -mt-2">Required for SSO login — must match the identity in your IdP. Leave empty for non-SSO employees.</p>
          <Select label="Position" value={editingEmp.positionId} onChange={v => {
            const pos = POSITIONS.find(p => p.id === v);
            setEditingEmp({ ...editingEmp, positionId: v, positionName: pos?.name || '', departmentId: pos?.departmentId || '', departmentName: pos?.departmentName || '' });
          }} options={posOptions} />
          <Select label="Role" value={editingEmp.role || 'employee'} onChange={v => setEditingEmp({ ...editingEmp, role: v as 'admin' | 'manager' | 'employee' })}
            options={[{label:'Employee',value:'employee'},{label:'Manager',value:'manager'},{label:'Admin',value:'admin'}]} />
          <div>
            <label className="block text-xs font-medium text-text-secondary mb-1.5">IM Channels</label>
            <div className="flex flex-wrap gap-2">
              {(['slack','discord','telegram','whatsapp','feishu','portal'] as ChannelType[]).map(ch => (
                <button key={ch} onClick={() => setEditingEmp({ ...editingEmp, channels: (editingEmp.channels as string[]).includes(ch) ? editingEmp.channels.filter(c => c !== ch) : [...editingEmp.channels, ch] as ChannelType[] })}
                  className={`rounded-lg px-3 py-1.5 text-xs font-medium border transition-colors ${(editingEmp.channels as string[]).includes(ch) ? 'bg-primary/10 border-primary/40 text-primary-light' : 'border-dark-border text-text-muted hover:border-text-muted'}`}>
                  {CHANNEL_LABELS[ch]}
                </button>
              ))}
            </div>
          </div>
        </div>}
      </Modal>

      {/* Delete Employee Modal */}
      <Modal open={!!deletingEmp} title="Delete Employee" onClose={() => setDeletingEmp(null)} footer={
        <><Button variant="ghost" onClick={() => setDeletingEmp(null)}>Cancel</Button>
        {deleteBlockInfo ? (
          <Button variant="danger" disabled={deleteEmployee.isPending} onClick={() => {
            if (!deletingEmp) return;
            deleteEmployee.mutate({ empId: deletingEmp.id, force: true }, {
              onSuccess: () => setDeletingEmp(null),
              onError: (err: any) => setDeleteError(String(err?.response?.data?.message || err?.response?.data?.detail || err?.message || 'Delete failed')),
            });
          }}>{deleteEmployee.isPending ? 'Deleting…' : 'Force Delete (cascade)'}</Button>
        ) : (
          <Button variant="danger" disabled={deleteEmployee.isPending || !!deleteError} onClick={() => {
            if (!deletingEmp) return;
            setDeleteError('');
            deleteEmployee.mutate({ empId: deletingEmp.id, force: false }, {
              onSuccess: () => setDeletingEmp(null),
              onError: (err: any) => {
                const data = err?.response?.data;
                if (data?.error === 'employee_has_bindings') {
                  setDeleteBlockInfo({ agentBindings: data.agentBindings, imMappings: data.imMappings });
                  setDeleteError(String(data.message || 'Employee has active bindings'));
                } else {
                  setDeleteError(String(data?.message || data?.detail || err?.message || 'Delete failed'));
                }
              },
            });
          }}>{deleteEmployee.isPending ? 'Checking…' : 'Delete'}</Button>
        )}</>
      }>
        <div className="space-y-3">
          <p className="text-sm text-text-primary">Delete employee <strong>{deletingEmp?.name}</strong>?</p>
          {/* Always-On cleanup warning */}
          {(() => {
            const empAgent = AGENTS.find(a => a.employeeId === deletingEmp?.id);
            const isAlwaysOn = empAgent?.deployMode === 'always-on-ecs';
            return isAlwaysOn ? (
              <div className="rounded-lg bg-warning/10 border border-warning/30 px-3 py-2.5 text-xs space-y-1">
                <p className="font-semibold text-warning">Always-On agent will be cleaned up:</p>
                <ul className="list-disc list-inside text-text-secondary space-y-0.5">
                  <li>Stop & delete ECS Fargate service</li>
                  <li>Delete EFS Access Point & workspace files</li>
                  <li>Remove IM credentials (DynamoDB)</li>
                  <li>Deregister SSM endpoint</li>
                  <li>Delete S3 serverless workspace</li>
                  <li>Remove all session, conversation, and usage records</li>
                </ul>
              </div>
            ) : null;
          })()}
          {deleteError && (
            <div className="flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2.5">
              <AlertTriangle size={16} className="text-danger mt-0.5 shrink-0" />
              <div>
                <p className="text-sm text-danger">{deleteError}</p>
                {deleteBlockInfo && (
                  <p className="text-xs text-danger/80 mt-1">
                    Click "Force Delete" to remove {deleteBlockInfo.agentBindings} agent binding(s) and {deleteBlockInfo.imMappings} IM pairing(s) along with the employee.
                  </p>
                )}
              </div>
            </div>
          )}
          {!deleteError && (
            <p className="text-xs text-text-muted">This will check for active bindings first. IM pairings and agent bindings will be listed if present.</p>
          )}
        </div>
      </Modal>

      {/* Create Modal */}
      <Modal
        open={showCreate} onClose={() => setShowCreate(false)} title="Add Employee"
        footer={<div className="flex justify-end gap-3"><Button variant="default" onClick={() => setShowCreate(false)}>Cancel</Button><Button variant="primary" disabled={!newName.trim() || !newPos} onClick={() => {
          if (newName && newPos) {
            const pos = POSITIONS.find(p => p.id === newPos);
            createEmployee.mutate({
              name: newName, email: newEmail.trim() || undefined, employeeNo: newNo || `EMP-${Date.now()}`,
              positionId: newPos, positionName: pos?.name || '',
              departmentId: pos?.departmentId || '', departmentName: pos?.departmentName || '',
              channels: newChannels, agentId: null, agentStatus: 'idle',
            } as any);
          }
          setShowCreate(false); setNewName(''); setNewEmail(''); setNewNo(''); setNewPos(''); setNewChannels(['slack']);
        }}>Add & Auto-Provision</Button></div>}
      >
        <div className="space-y-4">
          <Input label="Name" value={newName} onChange={setNewName} />
          <Input label="Email (optional)" value={newEmail} onChange={setNewEmail} placeholder="jane.doe@company.com" />
          <p className="text-[10px] text-text-muted -mt-2">Required for SSO login — must match the identity in your IdP. Leave empty for non-SSO employees.</p>
          <Input label="Employee No" value={newNo} onChange={setNewNo} placeholder="EMP-022" />
          <Select label="Position" value={newPos} onChange={setNewPos} options={posOptions} placeholder="Select position" />
          <div>
            <label className="block text-xs font-medium text-text-secondary mb-1.5">IM Channels</label>
            <div className="flex flex-wrap gap-2">
              {['slack', 'discord', 'telegram', 'whatsapp', 'feishu', 'portal'].map(ch => (
                <button key={ch} onClick={() => setNewChannels(prev => prev.includes(ch) ? prev.filter(c => c !== ch) : [...prev, ch])}
                  className={`rounded-lg px-3 py-1.5 text-xs font-medium border transition-colors ${newChannels.includes(ch) ? 'bg-primary/10 border-primary/40 text-primary-light' : 'border-dark-border text-text-muted hover:border-text-muted'}`}>
                  {CHANNEL_LABELS[ch as ChannelType]}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-text-muted mt-1">Select which IM platforms this employee will use</p>
          </div>
          <div className="rounded-lg bg-primary/5 border border-primary/20 p-3 text-xs text-text-secondary">
            <Zap size={14} className="inline mr-1 text-primary-light" />
            A Serverless agent will be auto-provisioned based on the position's SOUL template and default skills. The employee can start chatting immediately via Portal or after IM pairing.
          </div>
        </div>
      </Modal>
    </div>
  );
}
