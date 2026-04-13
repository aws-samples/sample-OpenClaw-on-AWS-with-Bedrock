import { useState, useCallback } from 'react';
import { Link2, Users, Zap, Smartphone, Trash2, RefreshCw } from 'lucide-react';
import { IM_ICONS } from '../components/IMIcons';
import { Card, StatCard, Badge, Button, PageHeader, Table, Modal, Select, Tabs, StatusDot } from '../components/ui';
import { useBindings, useEmployees, useAgents, usePositions, useBulkProvision, useRoutingRules, useUserMappings, useCreateUserMapping, useDeleteUserMapping, useApprovePairing } from '../hooks/useApi';
import { CHANNEL_LABELS } from '../types';
import type { ChannelType } from '../types';

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
                  <Button variant="ghost" size="sm" onClick={() => setConfirming(null)}>No</Button>
                </>
              ) : (
                <Button variant="ghost" size="sm" onClick={() => setConfirming(rKey)}>
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
  const { data: EMPLOYEES = [] } = useEmployees();
  const { data: AGENTS = [] } = useAgents();
  const { data: POSITIONS = [] } = usePositions();
  const { data: routingRules = [] } = useRoutingRules();
  const { data: userMappings = [] } = useUserMappings();
  const bulkProvision = useBulkProvision();
  const createUserMapping = useCreateUserMapping();
  const deleteUserMapping = useDeleteUserMapping();
  const approvePairing = useApprovePairing();

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
  const [activeTab, setActiveTab] = useState('employees');

  const channelOptions = Object.entries(CHANNEL_LABELS).map(([v, l]) => ({ label: l, value: v }));

  // Classify employees by deployment mode
  const alwaysOnAgentIds = new Set(AGENTS.filter(a => a.deployMode === 'always-on-ecs').map(a => a.id));
  const serverlessCount = EMPLOYEES.filter(e => e.agentId && !alwaysOnAgentIds.has(e.agentId)).length;
  const alwaysOnCount = EMPLOYEES.filter(e => e.agentId && alwaysOnAgentIds.has(e.agentId)).length;
  const unboundCount = EMPLOYEES.filter(e => !e.agentId).length;
  const imConnectedCount = userMappings.length;

  const employeeColumns = [
    { key: 'name', label: 'Employee', render: (e: any) => <span className="font-medium">{e.name}</span> },
    { key: 'position', label: 'Position', render: (e: any) => <span className="text-text-secondary">{e.positionName}</span> },
    { key: 'dept', label: 'Department', render: (e: any) => <span className="text-text-muted text-xs">{e.departmentName}</span> },
    { key: 'agent', label: 'Agent', render: (e: any) => {
      if (!e.agentId) return <Badge color="default">No agent</Badge>;
      const agent = AGENTS.find((a: any) => a.id === e.agentId);
      return <span className="text-sm">{agent?.name || e.agentId}</span>;
    }},
    { key: 'mode', label: 'Mode', render: (e: any) => {
      if (!e.agentId) return <Badge color="default">—</Badge>;
      const agent = AGENTS.find((a: any) => a.id === e.agentId);
      if (agent?.deployMode === 'always-on-ecs') {
        return <Badge color="info"><Zap size={10} className="mr-1 inline" />Always-on</Badge>;
      }
      return <Badge color="success">Serverless</Badge>;
    }},
    { key: 'im', label: 'IM Connected', render: (e: any) => {
      const channels = userMappings.filter((m: any) => m.employeeId === e.id);
      if (channels.length === 0) return <span className="text-text-muted text-xs">—</span>;
      return (
        <div className="flex gap-1">
          {channels.map((m: any) => <Badge key={m.channel + m.channelUserId} color="info">{m.channel}</Badge>)}
        </div>
      );
    }},
  ];

  return (
    <div>
      <PageHeader
        title="Agent Assignments"
        description="Every employee automatically gets a Serverless agent. Admin can upgrade to Always-on (Fargate) for scheduled tasks and instant response."
        actions={
          <div className="flex gap-3">
            <Button variant="default" onClick={() => setShowBulk(true)}>Bulk Provision</Button>
          </div>
        }
      />

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 mb-6">
        <StatCard title="Total Employees" value={EMPLOYEES.length} icon={<Users size={22} />} color="primary" />
        <StatCard title="Serverless" value={serverlessCount} icon={<RefreshCw size={22} />} color="success" />
        <StatCard title="Always-on" value={alwaysOnCount} icon={<Zap size={22} />} color="cyan" />
        <StatCard title="IM Connected" value={imConnectedCount} icon={<Smartphone size={22} />} color="info" />
      </div>

      <Card>
        <Tabs
          tabs={[
            { id: 'employees', label: 'All Employees', count: EMPLOYEES.length },
            { id: 'routing', label: 'Routing Rules', count: routingRules.length },
            { id: 'mappings', label: 'IM User Mappings', count: userMappings.length },
          ]}
          activeTab={activeTab}
          onChange={setActiveTab}
        />
        <div className="mt-4">
          {activeTab === 'routing' ? (
            <div>
              <p className="text-sm text-text-secondary mb-4">Position → Runtime routing rules. The Tenant Router uses these to determine which AgentCore Runtime handles each employee's messages.</p>
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
                    <Badge color="primary">→ {r.agentId || r.action}</Badge>
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
                  <p className="text-xs mt-1">Employees self-pair via Portal → Connect IM, or add manually here.</p>
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
            <div>
              <div className="rounded-xl bg-surface-secondary border border-dark-border/30 px-4 py-3 mb-4">
                <p className="text-xs text-text-muted">
                  Every employee with a position automatically gets a <strong>Serverless</strong> agent (AgentCore microVM, scales to zero).
                  Admin can upgrade any employee to <strong>Always-on</strong> (ECS Fargate) from Agent Factory for scheduled tasks, direct IM bots, and instant response.
                  When upgraded, the Serverless agent is replaced — employee always sees one agent.
                </p>
              </div>
              <Table columns={employeeColumns} data={EMPLOYEES} />
            </div>
          )}
        </div>
      </Card>

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
              Auto-create a Serverless agent for every employee in the selected position who doesn't already have one.
              The agent inherits the position's SOUL template automatically.
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
              <p className="text-xs text-text-muted mt-1">For Discord, use the username from pairing metadata. For Slack, use the DM display name if the runtime surfaces messages as <code>dm_name</code>. This creates compatibility mappings for reliable routing.</p>
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
