import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Briefcase, Users, Bot, Plus, Zap, Pencil, Trash2, AlertTriangle,
  FileText, Lock, Edit3, User, ArrowRight, Save, ExternalLink,
} from 'lucide-react';
import { Card, StatCard, Badge, Button, PageHeader, Table, Modal, Input, Select, Textarea } from '../../components/ui';
import {
  usePositions, useDepartments, useAgents, useEmployees,
  useCreatePosition, useUpdatePosition, useDeletePosition, useBulkProvision,
  useGlobalSoul, usePositionSoul, useUpdatePositionSoul,
} from '../../hooks/useApi';
import { CHANNEL_LABELS } from '../../types';
import type { Position, ChannelType } from '../../types';

// ─── helpers ────────────────────────────────────────────────────────────────

function wordCount(text: string): number {
  return text?.trim().split(/\s+/).filter(Boolean).length || 0;
}

function SoulStatusBadge({ text }: { text: string | undefined }) {
  const wc = wordCount(text || '');
  if (wc === 0) return <Badge color="warning">Empty</Badge>;
  return <Badge color="success">{wc} words</Badge>;
}

// ─── SOUL Inheritance Chain (visual) ────────────────────────────────────────

function SoulInheritanceChain({ globalContent, positionContent, positionName, empCount, onEditGlobal, onEditPosition, onViewEmployees }: {
  globalContent: string; positionContent: string; positionName: string;
  empCount: number; onEditGlobal: () => void; onEditPosition: () => void; onViewEmployees: () => void;
}) {
  const globalWc = wordCount(globalContent);
  const posWc = wordCount(positionContent);

  return (
    <div className="rounded-xl border border-dark-border/50 bg-surface-dim px-4 py-3">
      <p className="text-xs font-medium text-text-muted uppercase tracking-wider mb-3">SOUL Inheritance Chain</p>

      {/* Layer 1: Global */}
      <div className="flex items-start gap-3 mb-1">
        <div className="flex flex-col items-center">
          <div className="flex h-6 w-6 items-center justify-center rounded-full bg-text-muted/10 border border-text-muted/30">
            <Lock size={11} className="text-text-muted" />
          </div>
          <div className="w-px h-5 bg-dark-border/60" />
        </div>
        <div className="flex-1 min-w-0 pt-0.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-text-secondary">Global SOUL.md</span>
            <span className="text-[10px] text-text-muted">(base layer for ALL agents)</span>
          </div>
          <div className="flex items-center gap-2 mt-1">
            {globalWc > 0 ? (
              <span className="text-xs text-text-muted">{globalWc} words</span>
            ) : (
              <span className="text-xs text-warning">Not configured</span>
            )}
            <span className="text-text-muted">·</span>
            <button onClick={onEditGlobal} className="text-xs text-primary-light hover:underline flex items-center gap-1">
              Edit in Security Center <ExternalLink size={10} />
            </button>
          </div>
        </div>
      </div>

      {/* Layer 2: Position */}
      <div className="flex items-start gap-3 mb-1 ml-3">
        <div className="flex flex-col items-center">
          <div className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/10 border border-primary/30">
            <Edit3 size={11} className="text-primary" />
          </div>
          <div className="w-px h-5 bg-dark-border/60" />
        </div>
        <div className="flex-1 min-w-0 pt-0.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-primary">Position SOUL</span>
            <Badge color="primary">{positionName}</Badge>
          </div>
          <div className="flex items-center gap-2 mt-1">
            {posWc > 0 ? (
              <span className="text-xs text-text-muted">{posWc} words</span>
            ) : (
              <span className="text-xs text-warning">Not configured</span>
            )}
            <span className="text-text-muted">·</span>
            <button onClick={onEditPosition} className="text-xs text-primary-light hover:underline flex items-center gap-1">
              <Edit3 size={10} /> Edit below
            </button>
          </div>
        </div>
      </div>

      {/* Layer 3: Personal */}
      <div className="flex items-start gap-3 ml-6">
        <div className="flex flex-col items-center">
          <div className="flex h-6 w-6 items-center justify-center rounded-full bg-success/10 border border-success/30">
            <User size={11} className="text-success" />
          </div>
        </div>
        <div className="flex-1 min-w-0 pt-0.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-success">Personal SOUL</span>
            <span className="text-[10px] text-text-muted">(per employee)</span>
          </div>
          <div className="flex items-center gap-2 mt-1">
            <span className="text-xs text-text-muted">{empCount} employee{empCount !== 1 ? 's' : ''}</span>
            <span className="text-text-muted">·</span>
            <button onClick={onViewEmployees} className="text-xs text-success hover:underline flex items-center gap-1">
              Edit via Agent SOUL Editor <ExternalLink size={10} />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Inline Position SOUL Editor ────────────────────────────────────────────

function InlinePositionSoulEditor({ posId, posName }: { posId: string; posName: string }) {
  const { data: posSoul, isLoading } = usePositionSoul(posId);
  const updateSoul = useUpdatePositionSoul();
  const [content, setContent] = useState('');
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (posSoul?.content !== undefined) {
      setContent(posSoul.content);
      setDirty(false);
    }
  }, [posSoul?.content]);

  const handleSave = async () => {
    await updateSoul.mutateAsync({ posId, content });
    setDirty(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  if (isLoading) {
    return <div className="py-4 text-center text-xs text-text-muted">Loading SOUL...</div>;
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Edit3 size={14} className="text-primary" />
          <p className="text-xs font-medium text-text-muted uppercase tracking-wider">Position SOUL Editor</p>
        </div>
        <div className="flex items-center gap-2">
          {dirty && <span className="text-[10px] text-warning">Unsaved changes</span>}
          <span className="text-[10px] text-text-muted font-mono">{wordCount(content)} words</span>
          <Button variant="primary" size="sm" disabled={!dirty || updateSoul.isPending} onClick={handleSave}>
            <Save size={12} /> {saved ? 'Saved' : updateSoul.isPending ? 'Saving...' : 'Save to S3'}
          </Button>
        </div>
      </div>
      <textarea
        value={content}
        onChange={e => { setContent(e.target.value); setDirty(true); }}
        rows={8}
        placeholder={`Define the behavior and capabilities for ${posName} agents...\n\nExample:\nYou are a ${posName} at ACME Corp. You specialize in...\n- Always reference official documentation\n- Provide actionable recommendations\n- Ask clarifying questions before making assumptions`}
        className="w-full rounded-xl border border-dark-border/60 bg-dark-bg px-4 py-3 text-sm font-mono text-text-primary placeholder:text-text-muted/50 focus:border-primary/60 focus:outline-none resize-y leading-relaxed"
      />
      {!content && (
        <div className="mt-2 rounded-lg bg-warning/5 border border-warning/20 px-3 py-2 text-xs text-warning flex items-start gap-2">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <p>
            Position SOUL is empty. Agents in this position will only inherit the Global SOUL.
            Define role-specific behavior here so agents know their professional identity.
          </p>
        </div>
      )}
    </div>
  );
}

// ─── Main Page ──────────────────────────────────────────────────────────────

export default function Positions() {
  const navigate = useNavigate();
  const { data: POSITIONS = [] } = usePositions();
  const { data: DEPARTMENTS = [] } = useDepartments();
  const { data: AGENTS = [] } = useAgents();
  const { data: EMPLOYEES = [] } = useEmployees();
  const { data: globalSoul } = useGlobalSoul();
  const createPosition = useCreatePosition();
  const updatePosition = useUpdatePosition();
  const deletePosition = useDeletePosition();
  const bulkProvision = useBulkProvision();
  const [showCreate, setShowCreate] = useState(false);
  const [editingPos, setEditingPos] = useState<Position | null>(null);
  const [deletingPos, setDeletingPos] = useState<Position | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [selected, setSelected] = useState<Position | null>(null);
  const [provisionResult, setProvisionResult] = useState<any>(null);
  const [detailTab, setDetailTab] = useState<'soul' | 'config' | 'employees'>('soul');
  const [newName, setNewName] = useState('');
  const [newDept, setNewDept] = useState('');
  const [newSoul, setNewSoul] = useState('');
  const [newChannel, setNewChannel] = useState('slack');
  const [newTools, setNewTools] = useState<string[]>(['web_search']);
  const [newSkills, setNewSkills] = useState<string[]>([]);

  const deptOptions = DEPARTMENTS.filter(d => !d.parentId).map(d => ({ label: d.name, value: d.id }));
  const totalMembers = POSITIONS.reduce((s, p) => s + (p.memberCount || 0), 0);
  const totalUnbound = EMPLOYEES.filter(e => !e.agentId).length;
  const emptySoulCount = POSITIONS.filter(p => !p.soulTemplate?.trim()).length;

  const getProvisionStats = (posId: string) => {
    const posEmps = EMPLOYEES.filter(e => e.positionId === posId);
    const bound = posEmps.filter(e => e.agentId).length;
    return { total: posEmps.length, bound, unbound: posEmps.length - bound };
  };

  const handleProvision = (posId: string, channel?: string) => {
    bulkProvision.mutate(
      { positionId: posId, defaultChannel: channel || 'slack' },
      { onSuccess: (data) => setProvisionResult(data) }
    );
  };

  return (
    <div>
      <PageHeader
        title="Position Management"
        description="Each position defines the SOUL identity, skills, and tool permissions inherited by all agents in that role"
        actions={<Button variant="primary" onClick={() => setShowCreate(true)}><Plus size={16} /> Create Position</Button>}
      />

      {/* SOUL guidance banner — shown when there are positions with empty SOULs */}
      {POSITIONS.length > 0 && emptySoulCount > 0 && (
        <div className="mb-6 rounded-xl border border-primary/20 bg-primary/5 px-5 py-4">
          <div className="flex items-start gap-3">
            <FileText size={20} className="text-primary mt-0.5 shrink-0" />
            <div className="flex-1">
              <p className="text-sm font-medium text-text-primary mb-1">
                {emptySoulCount} position{emptySoulCount !== 1 ? 's have' : ' has'} empty SOUL — agents won't know their role
              </p>
              <p className="text-xs text-text-secondary mb-3">
                The SOUL defines who each agent <em>is</em>. It's a 3-layer inheritance system:
              </p>
              <div className="flex items-center gap-2 text-xs flex-wrap">
                <span className="flex items-center gap-1 bg-dark-bg border border-dark-border/50 rounded px-2 py-1">
                  <Lock size={10} className="text-text-muted" /> Global
                </span>
                <ArrowRight size={12} className="text-text-muted" />
                <span className="flex items-center gap-1 bg-primary/10 border border-primary/30 rounded px-2 py-1 text-primary">
                  <Edit3 size={10} /> Position
                </span>
                <ArrowRight size={12} className="text-text-muted" />
                <span className="flex items-center gap-1 bg-success/10 border border-success/30 rounded px-2 py-1 text-success">
                  <User size={10} /> Personal
                </span>
                <span className="text-text-muted ml-1">= Merged SOUL at runtime</span>
              </div>
              <p className="text-xs text-text-muted mt-2">
                Click any position below to configure its SOUL. Global SOUL is managed in{' '}
                <button onClick={() => navigate('/security')} className="text-primary-light hover:underline">Security Center</button>.
              </p>
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 mb-6">
        <StatCard title="Positions" value={POSITIONS.length} icon={<Briefcase size={22} />} color="primary" />
        <StatCard title="Total Members" value={totalMembers} icon={<Users size={22} />} color="info" />
        <StatCard title="Total Agents" value={AGENTS.length} icon={<Bot size={22} />} color="success" />
        <StatCard title="SOUL Configured" value={POSITIONS.length - emptySoulCount} subtitle={`of ${POSITIONS.length} positions`} icon={<FileText size={22} />} color={emptySoulCount > 0 ? 'warning' : 'success'} />
      </div>

      <Table
        columns={[
          { key: 'name', label: 'Position', render: (p: Position) => (
            <button onClick={() => { setSelected(p); setDetailTab('soul'); }} className="text-primary-light hover:underline font-medium">{p.name}</button>
          )},
          { key: 'dept', label: 'Department', render: (p: Position) => <span className="text-text-secondary">{p.departmentName}</span> },
          { key: 'soul', label: 'SOUL', render: (p: Position) => <SoulStatusBadge text={p.soulTemplate} /> },
          { key: 'skills', label: 'Skills', render: (p: Position) => (
            <div className="flex flex-wrap gap-1">
              {(p.defaultSkills || []).slice(0, 3).map(s => <Badge key={s} color="success">{s}</Badge>)}
              {(p.defaultSkills || []).length > 3 && <Badge>{`+${(p.defaultSkills || []).length - 3}`}</Badge>}
            </div>
          )},
          { key: 'provision', label: 'Provision', render: (p: Position) => {
            const stats = getProvisionStats(p.id);
            if (stats.total === 0) return <span className="text-xs text-text-muted">No members</span>;
            if (stats.unbound === 0) return <Badge color="success">{stats.bound}/{stats.total}</Badge>;
            return (
              <div className="flex items-center gap-2">
                <Badge color="warning">{stats.bound}/{stats.total}</Badge>
                <button
                  onClick={(e) => { e.stopPropagation(); handleProvision(p.id, p.defaultChannel); }}
                  className="text-xs text-primary-light hover:underline flex items-center gap-1"
                  disabled={bulkProvision.isPending}
                >
                  <Zap size={12} /> +{stats.unbound}
                </button>
              </div>
            );
          }},
          { key: 'actions', label: '', render: (p: Position) => (
            <div className="flex items-center gap-1">
              <button onClick={e => { e.stopPropagation(); setEditingPos(p); }} className="p-1.5 rounded hover:bg-dark-hover text-text-muted hover:text-text-primary" title="Edit position settings"><Pencil size={13} /></button>
              <button onClick={e => { e.stopPropagation(); setDeletingPos(p); setDeleteError(''); }} className="p-1.5 rounded hover:bg-dark-hover text-text-muted hover:text-danger" title="Delete position"><Trash2 size={13} /></button>
            </div>
          )},
        ]}
        data={POSITIONS}
      />

      {/* ── Detail Modal (redesigned with SOUL-centric UX) ── */}
      <Modal open={!!selected} onClose={() => { setSelected(null); setProvisionResult(null); }} title={selected?.name || ''} size="lg">
        {selected && (() => {
          const stats = getProvisionStats(selected.id);
          const posEmps = EMPLOYEES.filter(e => e.positionId === selected.id);
          const boundEmps = posEmps.filter(e => e.agentId);
          return (
            <div className="space-y-5">
              {/* Header stats */}
              <div className="grid grid-cols-4 gap-4">
                <div><p className="text-xs text-text-muted">Department</p><p className="text-sm font-medium">{selected.departmentName}</p></div>
                <div><p className="text-xs text-text-muted">Members</p><p className="text-sm font-medium">{stats.total}</p></div>
                <div><p className="text-xs text-text-muted">Agents Bound</p><p className="text-sm font-medium text-green-400">{stats.bound}</p></div>
                <div><p className="text-xs text-text-muted">Unbound</p><p className="text-sm font-medium text-warning">{stats.unbound}</p></div>
              </div>

              {/* Provision prompt */}
              {stats.unbound > 0 && !provisionResult && (
                <div className="rounded-lg bg-warning/5 border border-warning/20 p-3 flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-warning">{stats.unbound} employee(s) without agents</p>
                    <p className="text-xs text-text-muted">Auto-create Serverless agents with this position's SOUL and default skills</p>
                  </div>
                  <Button variant="primary" size="sm" onClick={() => handleProvision(selected.id, selected.defaultChannel)} disabled={bulkProvision.isPending}>
                    <Zap size={14} /> {bulkProvision.isPending ? 'Provisioning...' : `Provision All (${stats.unbound})`}
                  </Button>
                </div>
              )}
              {provisionResult && (
                <div className="rounded-lg bg-green-500/10 border border-green-500/20 p-3">
                  <p className="text-sm font-medium text-green-400">Provisioned {provisionResult.provisioned} agent(s)</p>
                  {provisionResult.details?.map((d: any, i: number) => (
                    <p key={i} className="text-xs text-text-muted mt-1">• {d.employee} → {d.agent}</p>
                  ))}
                </div>
              )}

              {/* Tab navigation */}
              <div className="flex gap-1 border-b border-dark-border/50">
                {([
                  { id: 'soul' as const, label: 'SOUL', icon: <FileText size={13} /> },
                  { id: 'config' as const, label: 'Skills & Tools', icon: <Zap size={13} /> },
                  { id: 'employees' as const, label: `Employees (${posEmps.length})`, icon: <Users size={13} /> },
                ]).map(t => (
                  <button key={t.id} onClick={() => setDetailTab(t.id)}
                    className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                      detailTab === t.id
                        ? 'border-primary text-primary'
                        : 'border-transparent text-text-muted hover:text-text-secondary'
                    }`}>
                    {t.icon} {t.label}
                  </button>
                ))}
              </div>

              {/* SOUL tab */}
              {detailTab === 'soul' && (
                <div className="space-y-4">
                  <SoulInheritanceChain
                    globalContent={globalSoul?.content || ''}
                    positionContent={selected.soulTemplate || ''}
                    positionName={selected.name}
                    empCount={boundEmps.length}
                    onEditGlobal={() => { setSelected(null); navigate('/security'); }}
                    onEditPosition={() => {}} // already showing editor below
                    onViewEmployees={() => setDetailTab('employees')}
                  />
                  <InlinePositionSoulEditor posId={selected.id} posName={selected.name} />
                </div>
              )}

              {/* Config tab */}
              {detailTab === 'config' && (
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <p className="mb-2 text-xs font-medium text-text-muted uppercase tracking-wider">Default Skills</p>
                      {(selected.defaultSkills || []).length > 0 ? (
                        <div className="flex flex-wrap gap-2">{(selected.defaultSkills || []).map(s => <Badge key={s} color="success">{s}</Badge>)}</div>
                      ) : (
                        <p className="text-xs text-text-muted">No default skills configured</p>
                      )}
                    </div>
                    <div>
                      <p className="mb-2 text-xs font-medium text-text-muted uppercase tracking-wider">Tool Allowlist</p>
                      {(selected.toolAllowlist || []).length > 0 ? (
                        <div className="flex flex-wrap gap-2">{(selected.toolAllowlist || []).map(t => <Badge key={t} color="info">{t}</Badge>)}</div>
                      ) : (
                        <p className="text-xs text-text-muted">No tool restrictions (all allowed)</p>
                      )}
                    </div>
                  </div>
                  <div className="rounded-lg bg-info/5 border border-info/20 px-3 py-2 text-xs text-info flex items-center gap-2">
                    <ExternalLink size={12} />
                    Edit tool permissions and security policies in{' '}
                    <button onClick={() => { setSelected(null); navigate('/security'); }} className="underline hover:no-underline">Security Center</button>
                  </div>
                </div>
              )}

              {/* Employees tab */}
              {detailTab === 'employees' && (
                <div className="space-y-2">
                  {posEmps.length === 0 ? (
                    <div className="py-8 text-center text-text-muted">
                      <Users size={28} className="mx-auto mb-2 opacity-40" />
                      <p className="text-sm">No employees in this position yet</p>
                    </div>
                  ) : posEmps.map(e => {
                    const agent = AGENTS.find(a => a.id === e.agentId);
                    return (
                      <div key={e.id} className="flex items-center justify-between rounded-lg bg-dark-bg px-3 py-2.5">
                        <div className="flex items-center gap-3 min-w-0">
                          <span className="text-sm font-medium text-text-primary">{e.name}</span>
                          <span className="text-xs text-text-muted">{e.employeeNo}</span>
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          {agent ? (
                            <>
                              <span className="text-xs text-text-secondary truncate max-w-[120px]">{agent.name}</span>
                              <Badge color="success">Bound</Badge>
                              <button
                                onClick={() => { setSelected(null); navigate(`/agents/${agent.id}/soul`); }}
                                className="flex items-center gap-1 text-xs text-primary-light hover:underline"
                                title="Edit this employee's personal SOUL"
                              >
                                <Edit3 size={11} /> SOUL
                              </button>
                            </>
                          ) : (
                            <Badge color="warning">Unbound</Badge>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })()}
      </Modal>

      {/* ── Edit Position Modal ── */}
      <Modal open={!!editingPos} title={editingPos ? `Edit: ${editingPos.name}` : ''} onClose={() => setEditingPos(null)} footer={
        <><Button variant="ghost" onClick={() => setEditingPos(null)}>Cancel</Button>
        <Button variant="primary" disabled={updatePosition.isPending} onClick={() => {
          if (!editingPos) return;
          const dept = DEPARTMENTS.find(d => d.id === editingPos.departmentId);
          updatePosition.mutate({ id: editingPos.id, name: editingPos.name, departmentId: editingPos.departmentId,
            departmentName: dept?.name || editingPos.departmentName, defaultChannel: editingPos.defaultChannel,
            defaultSkills: editingPos.defaultSkills, toolAllowlist: editingPos.toolAllowlist, soulTemplate: editingPos.soulTemplate },
            { onSuccess: () => setEditingPos(null) });
        }}>{updatePosition.isPending ? 'Saving...' : 'Save'}</Button></>
      }>
        {editingPos && <div className="space-y-4">
          <Input label="Name" value={editingPos.name} onChange={v => setEditingPos({ ...editingPos, name: v })} />
          <Select label="Department" value={editingPos.departmentId} onChange={v => {
            const dept = DEPARTMENTS.find(d => d.id === v);
            setEditingPos({ ...editingPos, departmentId: v, departmentName: dept?.name || '' });
          }} options={DEPARTMENTS.filter(d => !d.parentId).map(d => ({ label: d.name, value: d.id }))} />
          {/* Default Channel removed — employees connect IM via Portal self-service pairing */}
        </div>}
      </Modal>

      {/* ── Delete Position Modal ── */}
      <Modal open={!!deletingPos} title="Delete Position" onClose={() => setDeletingPos(null)} footer={
        <><Button variant="ghost" onClick={() => setDeletingPos(null)}>Cancel</Button>
        <Button variant="danger" disabled={deletePosition.isPending || !!deleteError} onClick={() => {
          if (!deletingPos) return;
          setDeleteError('');
          deletePosition.mutate(deletingPos.id, {
            onSuccess: () => setDeletingPos(null),
            onError: (err: any) => setDeleteError(err?.response?.data?.message || err?.message || 'Delete failed'),
          });
        }}>{deletePosition.isPending ? 'Deleting...' : 'Delete'}</Button></>
      }>
        <div className="space-y-3">
          <p className="text-sm text-text-primary">Delete position <strong>{deletingPos?.name}</strong>?</p>
          {deleteError ? (
            <div className="flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2.5">
              <AlertTriangle size={16} className="text-danger mt-0.5 shrink-0" />
              <p className="text-sm text-danger">{deleteError}</p>
            </div>
          ) : (
            <p className="text-xs text-text-muted">All employees must be reassigned to another position first.</p>
          )}
        </div>
      </Modal>

      {/* ── Create Modal ── */}
      <Modal
        open={showCreate} onClose={() => setShowCreate(false)} title="Create Position"
        footer={<div className="flex justify-end gap-3"><Button variant="default" onClick={() => setShowCreate(false)}>Cancel</Button><Button variant="primary" onClick={() => {
          if (newName && newDept) {
            const dept = DEPARTMENTS.find(d => d.id === newDept);
            createPosition.mutate({
              name: newName,
              departmentId: newDept,
              departmentName: dept?.name || '',
              soulTemplate: newSoul,
              defaultSkills: newSkills,
              defaultKnowledge: [],
              toolAllowlist: newTools,
              defaultChannel: newChannel as any,
              memberCount: 0,
              createdAt: new Date().toISOString(),
            });
          }
          setShowCreate(false); setNewName(''); setNewDept(''); setNewSoul(''); setNewChannel('slack'); setNewTools(['web_search']); setNewSkills([]);
        }}>Create</Button></div>}
      >
        <div className="space-y-4">
          <Input label="Position Name" value={newName} onChange={setNewName} placeholder="e.g. Solutions Architect" />
          <Select label="Department" value={newDept} onChange={setNewDept} options={deptOptions} placeholder="Select department" />
          {/* Default Channel removed — employees connect IM via Portal self-service pairing */}
          <div>
            <label className="block text-xs font-medium text-text-secondary mb-1.5">Tool Permissions</label>
            <div className="flex flex-wrap gap-2">
              {['web_search', 'browser', 'file', 'file_write', 'shell', 'code_execution'].map(tool => (
                <button key={tool} onClick={() => setNewTools(prev => prev.includes(tool) ? prev.filter(t => t !== tool) : [...prev, tool])}
                  className={`rounded-lg px-3 py-1.5 text-xs font-medium border transition-colors ${newTools.includes(tool) ? 'bg-primary/10 border-primary/40 text-primary-light' : 'border-dark-border text-text-muted hover:border-text-muted'}`}>
                  {tool}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-text-muted mt-1">Tools this position's agents are allowed to use (Plan A enforcement)</p>
          </div>
          <div>
            <label className="block text-xs font-medium text-text-secondary mb-1.5">Default Skills</label>
            <div className="flex flex-wrap gap-2">
              {['web_search', 'browser', 'excel-gen', 'email-send', 'calendar-check', 'crm-query', 'google-docs', 'notion-sync', 'sap-connector'].map(skill => (
                <button key={skill} onClick={() => setNewSkills(prev => prev.includes(skill) ? prev.filter(s => s !== skill) : [...prev, skill])}
                  className={`rounded-lg px-3 py-1.5 text-xs font-medium border transition-colors ${newSkills.includes(skill) ? 'bg-success/10 border-success/40 text-success' : 'border-dark-border text-text-muted hover:border-text-muted'}`}>
                  {skill}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-text-muted mt-1">Skills auto-installed for agents in this position</p>
          </div>
          <Textarea label="SOUL Template" value={newSoul} onChange={setNewSoul} rows={6} placeholder="You are a ... specializing in ..." description="Define the default persona and behavior rules for agents in this position. This becomes the Position SOUL layer." />
        </div>
      </Modal>
    </div>
  );
}
