import { useState, useCallback } from 'react';
import { Link2, Plus, Users, User, GitBranch, Smartphone, Trash2 } from 'lucide-react';
import { IM_ICONS } from '../components/IMIcons';
import { Card, StatCard, Badge, Button, PageHeader, Table, Modal, Select, Tabs, StatusDot } from '../components/ui';
import { useBindings, useEmployees, useAgents, usePositions, useCreateBinding, useBulkProvision, useRoutingRules, useUserMappings, useCreateUserMapping, useDeleteUserMapping, useApprovePairing } from '../hooks/useApi';
import { CHANNEL_LABELS } from '../types';
import type { Binding, ChannelType } from '../types';

// Inline component for IM mapping table with per-row confirm-to-revoke
function RevokeTable({ mappings, employees, onRevoke, isPending }: {
  mappings: any[];
  employees: any[];
  onRevoke: (r: any) => void;
  isPending: boolean;
}) {
  const [confirming, setConfirming] = useState<string | null>(null);

  const key = useCallback((r: any) => `${r.channel}__${r.channelUserId}`, []);

  const ChannelIcon = ({ channel }: { channel: string }) => {
    const Icon = IM_ICONS[channel];
    return Icon ? <Icon size={22} /> : <Smartphone size={22} className="text-text-muted" />;
  };

  return (
    <div className="space-y-2">
      {mappings.map(r => {
        const emp = employees.find((e: any) => e.id === r.employeeId);
        const rKey = key(r);
        const isConfirming = confirming === rKey;
        return (
          <div key={rKey} className="flex items-center gap-3 rounded-lg bg-dark-bg border border-dark-border/40 px-4 py-2.5">
            <ChannelIcon channel={r.channel} />
            <Badge color="info" >{r.channel}</Badge>
            <code className="text-xs text-text-secondary bg-dark-hover px-2 py-0.5 rounded flex-shrink-0">
              {r.channelUserId}
            </code>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-text-primary truncate">
                {emp?.name || r.employeeId}
              </p>
              {emp && <p className="text-xs text-text-muted truncate">{emp.positionName}</p>}
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              {isConfirming ? (
                <>
                  <span className="text-xs text-danger">Revoke access?</span>
                  <Button variant="danger" size="sm" disabled={isPending}
                    onClick={() => { onRevoke(r); setConfirming(null); }}>
                    Confirm
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setConfirming(null)}>
                    Cancel
                  </Button>
                </>
              ) : (
                <Button variant="ghost" size="sm"
                  className="text-text-muted hover:text-danger hover:border-danger/30"
                  onClick={() => setConfirming(rKey)}>
                  <Trash2 size={13} /> Revoke
                </Button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function Bindings() {
  const { data: BINDINGS = [] } = useBindings();
  const { data: EMPLOYEES = [] } = useEmployees();
  const { data: AGENTS = [] } = useAgents();
  const { data: POSITIONS = [] } = usePositions();
  const createBinding = useCreateBinding();
  const bulkProvision = useBulkProvision();
  const { data: routingRules = [] } = useRoutingRules();
  const { data: userMappings = [] } = useUserMappings();
  const createUserMapping = useCreateUserMapping();
  const deleteUserMapping = useDeleteUserMapping();
  const approvePairing = useApprovePairing();
  const [showCreate, setShowCreate] = useState(false);
  const [showBulk, setShowBulk] = useState(false);
  const [showMapping, setShowMapping] = useState(false);
  const [showPairing, setShowPairing] = useState(false);
  const [mapChannel, setMapChannel] = useState('discord');
  const [mapUserId, setMapUserId] = useState('');
  const [mapEmpId, setMapEmpId] = useState('');
  const [pairChannel, setPairChannel] = useState('discord');
  const [pairCode, setPairCode] = useState('');
  const [pairUserId, setPairUserId] = useState('');
  const [pairUsername, setPairUsername] = useState('');
  const [pairEmpId, setPairEmpId] = useState('');
  const [pairResult, setPairResult] = useState<string | null>(null);
  const [bulkPos, setBulkPos] = useState('');
  const [bulkChannel, setBulkChannel] = useState('slack');
  const [bulkResult, setBulkResult] = useState<any>(null);
  const [activeTab, setActiveTab] = useState('all');
  const [selEmp, setSelEmp] = useState('');
  const [selAgent, setSelAgent] = useState('');
  const [selChannel, setSelChannel] = useState('');
  const [selMode, setSelMode] = useState('1:1');

  const empOptions = EMPLOYEES.map(e => ({ label: `${e.name} (${e.positionName})`, value: e.id }));
  const agentOptions = AGENTS.map(a => ({ label: a.name, value: a.id }));
  const channelOptions = Object.entries(CHANNEL_LABELS).map(([v, l]) => ({ label: l, value: v }));

  const oneToOne = BINDINGS.filter(b => b.mode === '1:1');
  const shared = BINDINGS.filter(b => b.mode === 'N:1');
  const multi = BINDINGS.filter(b => b.mode === '1:N');

  const tabData: Record<string, Binding[]> = {
    all: BINDINGS, private: oneToOne, shared, multi,
  };

  const columns = [
    { key: 'employee', label: 'Employee', render: (b: Binding) => <span className="font-medium">{b.employeeName}</span> },
    { key: 'arrow', label: '', render: () => <span className="text-text-muted">↔</span>, width: '40px' },
    { key: 'agent', label: 'Agent', render: (b: Binding) => <span className="font-medium">{b.agentName}</span> },
    { key: 'mode', label: 'Mode', render: (b: Binding) => (
      <Badge color={b.mode === '1:1' ? 'success' : b.mode === 'N:1' ? 'info' : 'default'}>{b.mode}</Badge>
    )},
    { key: 'channel', label: 'Channel', render: (b: Binding) => <Badge color="info">{CHANNEL_LABELS[b.channel as ChannelType]}</Badge> },
    { key: 'status', label: 'Status', render: (b: Binding) => <StatusDot status={b.status} /> },
    { key: 'source', label: 'Source', render: (b: Binding) => {
      const src = (b as any).source;
      return src?.startsWith('auto') ? <Badge color="info">Auto</Badge> : <Badge>Manual</Badge>;
    }},
    { key: 'created', label: 'Created', render: (b: Binding) => <span className="text-text-muted text-xs">{new Date(b.createdAt).toLocaleDateString()}</span> },
  ];

  return (
    <div>
      <PageHeader
        title="Bindings & Routing"
        description="Manage employee-agent bindings and message routing rules"
        actions={
          <div className="flex gap-3">
            <Button variant="default" onClick={() => setShowBulk(true)}>Bulk Assign by Position</Button>
            <Button variant="primary" onClick={() => setShowCreate(true)}><Plus size={16} /> Create Binding</Button>
          </div>
        }
      />

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 mb-6">
        <StatCard title="Total Bindings" value={BINDINGS.length} icon={<Link2 size={22} />} color="primary" />
        <StatCard title="1:1 Private" value={oneToOne.length} icon={<User size={22} />} color="success" />
        <StatCard title="N:1 Shared" value={shared.length} icon={<Users size={22} />} color="info" />
        <StatCard title="Bound" value={BINDINGS.filter(b => b.status === 'bound' || b.status === 'active').length} icon={<Link2 size={22} />} color="cyan" />
      </div>

      <Card>
        <Tabs
          tabs={[
            { id: 'all', label: 'All', count: BINDINGS.length },
            { id: 'private', label: '1:1 Private', count: oneToOne.length },
            { id: 'shared', label: 'N:1 Shared', count: shared.length },
            { id: 'multi', label: '1:N Multi-Agent', count: multi.length },
            { id: 'routing', label: 'Routing Rules', count: routingRules.length },
            { id: 'mappings', label: 'IM User Mappings', count: userMappings.length },
          ]}
          activeTab={activeTab}
          onChange={setActiveTab}
        />
        <div className="mt-4">
          {activeTab === 'routing' ? (
            <div>
              <p className="text-sm text-text-secondary mb-4">Rules are evaluated in priority order. First match wins. The Tenant Router uses these rules to determine which agent handles each incoming message.</p>
              <Table
                columns={[
                  { key: 'priority', label: '#', render: (r: typeof routingRules[0]) => <span className="font-mono text-sm">{r.priority}</span> },
                  { key: 'name', label: 'Rule', render: (r: typeof routingRules[0]) => <span className="font-medium">{r.name}</span> },
                  { key: 'condition', label: 'Condition', render: (r: typeof routingRules[0]) => (
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(r.condition).map(([k, v]) => <Badge key={k} color="info">{k}={v}</Badge>)}
                      {Object.keys(r.condition).length === 0 && <Badge>any</Badge>}
                    </div>
                  )},
                  { key: 'action', label: 'Action', render: (r: typeof routingRules[0]) => (
                    <Badge color={r.action === 'route_to_shared_agent' ? 'primary' : 'success'}>
                      {r.action === 'route_to_shared_agent' ? `→ ${r.agentId || 'shared'}` : '→ personal agent'}
                    </Badge>
                  )},
                  { key: 'desc', label: 'Description', render: (r: typeof routingRules[0]) => <span className="text-xs text-text-muted">{r.description}</span> },
                ]}
                data={routingRules}
              />
            </div>
          ) : activeTab === 'mappings' ? (
            <div>
              <div className="flex items-center justify-between mb-4">
                <p className="text-sm text-text-secondary">Map IM platform user IDs to employee IDs. This tells the system which employee is behind each Discord/Telegram/Slack/WhatsApp account.</p>
                <div className="flex gap-2">
                  <Button variant="primary" onClick={() => setShowPairing(true)}>🔑 Approve Pairing</Button>
                  <Button variant="default" onClick={() => setShowMapping(true)}><Smartphone size={14} className="mr-1" /> Manual Mapping</Button>
                </div>
              </div>
              {userMappings.length === 0 ? (
                <div className="text-center py-8 text-text-muted">
                  <Smartphone size={32} className="mx-auto mb-2 opacity-50" />
                  <p className="text-sm">No IM user mappings configured yet.</p>
                  <p className="text-xs mt-1">Add mappings so the system knows which employee is behind each IM account.</p>
                </div>
              ) : (
                <RevokeTable
                  mappings={userMappings}
                  employees={EMPLOYEES}
                  onRevoke={(r) => deleteUserMapping.mutate({ channel: r.channel, channelUserId: r.channelUserId })}
                  isPending={deleteUserMapping.isPending}
                />
              )}
            </div>
          ) : (
            <Table columns={columns} data={tabData[activeTab] || []} />
          )}
        </div>
      </Card>

      <Modal
        open={showCreate} onClose={() => setShowCreate(false)} title="Create Binding"
        footer={<div className="flex justify-end gap-3"><Button variant="default" onClick={() => setShowCreate(false)}>Cancel</Button><Button variant="primary" onClick={() => {
          if (selEmp && selAgent && selChannel) {
            const emp = EMPLOYEES.find(e => e.id === selEmp);
            const agent = AGENTS.find(a => a.id === selAgent);
            createBinding.mutate({
              employeeId: selEmp, employeeName: emp?.name || '',
              agentId: selAgent, agentName: agent?.name || '',
              mode: selMode as '1:1' | 'N:1' | '1:N', channel: selChannel as any,
              status: 'bound', createdAt: new Date().toISOString(),
            });
          }
          setShowCreate(false); setSelEmp(''); setSelAgent(''); setSelChannel('');
        }}>Create</Button></div>}
      >
        <div className="space-y-4">
          <Select label="Employee" value={selEmp} onChange={setSelEmp} options={empOptions} placeholder="Select employee" />
          <Select label="Agent" value={selAgent} onChange={setSelAgent} options={agentOptions} placeholder="Select agent" />
          <Select label="Channel" value={selChannel} onChange={setSelChannel} options={channelOptions} placeholder="Select messaging channel" />
          <Select label="Binding Mode" value={selMode} onChange={setSelMode} options={[
            { label: '1:1 Private — One employee, one dedicated agent', value: '1:1' },
            { label: 'N:1 Shared — Multiple employees share one agent', value: 'N:1' },
            { label: '1:N Multi-Agent — One employee uses multiple agents', value: '1:N' },
          ]} />
        </div>
      </Modal>

      {/* Bulk Provision by Position Modal */}
      <Modal
        open={showBulk}
        onClose={() => { setShowBulk(false); setBulkResult(null); setBulkPos(''); }}
        title="Bulk Provision by Position"
        footer={
          <div className="flex justify-end gap-3">
            <Button variant="default" onClick={() => { setShowBulk(false); setBulkResult(null); setBulkPos(''); }}>
              {bulkResult ? 'Close' : 'Cancel'}
            </Button>
            {!bulkResult && (
              <Button
                variant="primary"
                disabled={!bulkPos || bulkProvision.isPending}
                onClick={() => {
                  if (bulkPos) {
                    bulkProvision.mutate({ positionId: bulkPos, defaultChannel: bulkChannel }, {
                      onSuccess: (data) => setBulkResult(data),
                    });
                  }
                }}
              >
                {bulkProvision.isPending ? 'Provisioning...' : 'Provision All'}
              </Button>
            )}
          </div>
        }
      >
        {bulkResult ? (
          <div className="space-y-4">
            <div className="rounded-lg bg-green-500/10 border border-green-500/20 p-4">
              <p className="text-sm font-medium text-green-400">
                ✓ Provisioned {bulkResult.provisioned} agent{bulkResult.provisioned !== 1 ? 's' : ''} for {bulkResult.position}
              </p>
              {bulkResult.alreadyBound > 0 && (
                <p className="text-xs text-text-muted mt-1">{bulkResult.alreadyBound} employee(s) already had agents — skipped.</p>
              )}
            </div>
            {bulkResult.details?.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs text-text-muted font-medium">Newly provisioned:</p>
                {bulkResult.details.map((d: { employee: string; agent: string; channel: string }, i: number) => (
                  <div key={i} className="flex items-center justify-between text-sm bg-surface-secondary rounded px-3 py-2">
                    <span>{d.employee}</span>
                    <span className="text-text-muted">→</span>
                    <span className="text-text-secondary">{d.agent}</span>
                    <Badge color="info">{CHANNEL_LABELS[d.channel as ChannelType]}</Badge>
                  </div>
                ))}
              </div>
            )}
            {bulkResult.provisioned === 0 && (
              <p className="text-sm text-text-muted">All employees in this position already have agents assigned.</p>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-sm text-text-secondary">
              Auto-create a 1:1 agent and binding for every employee in the selected position who doesn't already have one.
            </p>
            <Select
              label="Position"
              value={bulkPos}
              onChange={setBulkPos}
              options={POSITIONS.map(p => ({ label: `${p.name} (${p.departmentName})`, value: p.id }))}
              placeholder="Select position"
            />
            <Select
              label="Default Channel"
              value={bulkChannel}
              onChange={setBulkChannel}
              options={channelOptions}
            />
            {bulkPos && (
              <div className="rounded-lg bg-surface-secondary p-3 text-sm">
                <p className="text-text-muted mb-2">Preview:</p>
                {(() => {
                  const posEmps = EMPLOYEES.filter(e => e.positionId === bulkPos);
                  const unbound = posEmps.filter(e => !e.agentId);
                  const bound = posEmps.filter(e => e.agentId);
                  return (
                    <>
                      <p className="text-text-primary">{posEmps.length} employee(s) in this position</p>
                      <p className="text-green-400">{unbound.length} will be provisioned</p>
                      {bound.length > 0 && <p className="text-text-muted">{bound.length} already have agents (skipped)</p>}
                      {unbound.length > 0 && (
                        <div className="mt-2 space-y-1">
                          {unbound.map(e => (
                            <div key={e.id} className="text-xs text-text-secondary">• {e.name}</div>
                          ))}
                        </div>
                      )}
                    </>
                  );
                })()}
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* IM User Mapping Modal */}
      <Modal
        open={showMapping} onClose={() => { setShowMapping(false); setMapChannel('discord'); setMapUserId(''); setMapEmpId(''); }}
        title="Add IM User Mapping"
        footer={<div className="flex justify-end gap-3">
          <Button variant="default" onClick={() => setShowMapping(false)}>Cancel</Button>
          <Button variant="primary" disabled={!mapUserId || !mapEmpId || createUserMapping.isPending} onClick={() => {
            createUserMapping.mutate({ channel: mapChannel, channelUserId: mapUserId, employeeId: mapEmpId }, {
              onSuccess: () => { setShowMapping(false); setMapChannel('discord'); setMapUserId(''); setMapEmpId(''); },
            });
          }}>{createUserMapping.isPending ? 'Saving...' : 'Save Mapping'}</Button>
        </div>}
      >
        <div className="space-y-4">
          <p className="text-sm text-text-secondary">Map an IM platform user ID to an employee. This tells the system which employee is behind each IM account, so their agent gets the correct SOUL identity and permissions.</p>
          <Select label="IM Channel" value={mapChannel} onChange={setMapChannel} options={[
            { label: 'Discord', value: 'discord' },
            { label: 'Telegram', value: 'telegram' },
            { label: 'Slack', value: 'slack' },
            { label: 'WhatsApp', value: 'whatsapp' },
            { label: 'Feishu', value: 'feishu' },
          ]} />
          <div>
            <label className="block text-xs font-medium text-text-secondary mb-1">Platform User ID</label>
            <input value={mapUserId} onChange={e => setMapUserId(e.target.value)}
              placeholder="e.g. 1460888812426363004 (Discord) or ou_62be5691... (Feishu)"
              className="w-full rounded-lg border border-dark-border bg-dark-bg px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none" />
            <p className="text-xs text-text-muted mt-1">Find this in the pairing message the employee received from the Bot.</p>
          </div>
          <Select label="Employee" value={mapEmpId} onChange={setMapEmpId}
            options={EMPLOYEES.map(e => ({ label: `${e.name} (${e.positionName})`, value: e.id }))}
            placeholder="Select employee" />
        </div>
      </Modal>

      {/* Pairing Approve Modal */}
      <Modal
        open={showPairing} onClose={() => { setShowPairing(false); setPairCode(''); setPairUserId(''); setPairUsername(''); setPairEmpId(''); setPairResult(null); }}
        title="Approve IM Pairing"
        footer={<div className="flex justify-end gap-3">
          <Button variant="default" onClick={() => { setShowPairing(false); setPairResult(null); }}>
            {pairResult ? 'Close' : 'Cancel'}
          </Button>
          {!pairResult && (
            <Button variant="primary" disabled={!pairCode || !pairEmpId || approvePairing.isPending} onClick={() => {
              approvePairing.mutate({ channel: pairChannel, pairingCode: pairCode, employeeId: pairEmpId, channelUserId: pairUserId, pairingUserId: pairUsername }, {
                onSuccess: (data) => {
                  if (data.approved) {
                    setPairResult(`✅ Approved! ${data.output || ''} ${data.mappingWritten ? '+ SSM mapping written' : ''}`);
                  } else {
                    setPairResult(`❌ Failed: ${data.error || 'Unknown error'}`);
                  }
                },
                onError: (e: any) => setPairResult(`❌ Error: ${e.message || e}`),
              });
            }}>{approvePairing.isPending ? 'Approving...' : 'Approve & Bind'}</Button>
          )}
        </div>}
      >
        {pairResult ? (
          <div className={`rounded-lg p-4 text-sm ${pairResult.startsWith('✅') ? 'bg-green-500/10 border border-green-500/20 text-green-400' : 'bg-red-500/10 border border-red-500/20 text-red-400'}`}>
            {pairResult}
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-sm text-text-secondary">When an employee DMs the Bot for the first time, they receive a pairing code. Enter it here to approve access and bind their IM account to their employee profile.</p>
            <Select label="IM Channel" value={pairChannel} onChange={setPairChannel} options={[
              { label: 'Discord', value: 'discord' },
              { label: 'Telegram', value: 'telegram' },
              { label: 'Slack', value: 'slack' },
              { label: 'WhatsApp', value: 'whatsapp' },
              { label: 'Feishu', value: 'feishu' },
            ]} />
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1">Pairing Code</label>
              <input value={pairCode} onChange={e => setPairCode(e.target.value.toUpperCase())}
                placeholder="e.g. KFDAF3GN"
                className="w-full rounded-lg border border-dark-border bg-dark-bg px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none font-mono tracking-wider" />
            </div>
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1">Platform User ID (numeric, from pairing message)</label>
              <input value={pairUserId} onChange={e => setPairUserId(e.target.value)}
                placeholder="e.g. 1460888812426363004"
                className="w-full rounded-lg border border-dark-border bg-dark-bg px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none font-mono" />
              <p className="text-xs text-text-muted mt-1">The numeric "Your user id" from the pairing message.</p>
            </div>
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1">Username / Handle (optional)</label>
              <input value={pairUsername} onChange={e => setPairUsername(e.target.value)}
                placeholder="e.g. wujiade4444"
                className="w-full rounded-lg border border-dark-border bg-dark-bg px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none font-mono" />
              <p className="text-xs text-text-muted mt-1">Discord username shown in pairing message meta. Creates additional SSM mappings for reliable routing.</p>
            </div>
            <Select label="Employee" value={pairEmpId} onChange={setPairEmpId}
              options={EMPLOYEES.map(e => ({ label: `${e.name} (${e.positionName})`, value: e.id }))}
              placeholder="Select employee" />
          </div>
        )}
      </Modal>
    </div>
  );
}
