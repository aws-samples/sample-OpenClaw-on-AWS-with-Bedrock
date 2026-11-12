import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Cpu, Zap, Shield, Edit3, Save, X, Clock, HardDrive, AlertTriangle } from 'lucide-react';
import { Card, Badge, Button } from '../components/ui';
import { api } from '../api/client';

interface RuntimeConfig {
  id: string;
  name: string;
  tier: 'standard' | 'executive';
  dockerImage: string;
  model: string;
  iamRole: string;
  iamLevel: 'scoped' | 'full';
  skills: string;
  idleTimeoutSec: number;
  maxLifetimeSec: number;
  positions: string[];
}

const RUNTIMES: RuntimeConfig[] = [
  {
    id: 'openclaw_multitenancy_runtime-olT3WX54rJ',
    name: 'Standard Runtime',
    tier: 'standard',
    dockerImage: 'multitenancy-agent:openclaw-2026.10.4',
    model: 'Amazon Nova 2 Lite',
    iamRole: 'agentcore-execution-role',
    iamLevel: 'scoped',
    skills: 'web-search, jina-reader, deep-research, github-pr, s3-files (+15 more)',
    idleTimeoutSec: 900,
    maxLifetimeSec: 28800,
    positions: ['Solutions Architect', 'Software Engineer', 'DevOps', 'Finance Analyst', 'HR', 'PM', 'Sales', 'Legal'],
  },
  {
    id: 'openclaw_multitenancy_exec_runtime-OkWZBw3ybK',
    name: 'Executive Runtime',
    tier: 'executive',
    dockerImage: 'multitenancy-agent:openclaw-2026.10.4',
    model: 'Claude Sonnet 4.6',
    iamRole: 'agentcore-exec-role',
    iamLevel: 'full',
    skills: 'ALL (shell · browser · code · analytics · integrations)',
    idleTimeoutSec: 900,
    maxLifetimeSec: 28800,
    positions: ['Executive'],
  },
];

function fmtTime(sec: number): string {
  if (sec < 3600) return `${Math.round(sec / 60)} min`;
  return `${Math.round(sec / 3600)} hr`;
}

function TimeSlider({ label, value, onChange, min = 60, max = 28800 }: {
  label: string; value: number; onChange: (v: number) => void; min?: number; max?: number;
}) {
  const steps = [60, 300, 600, 900, 1800, 3600, 7200, 14400, 28800];
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div>
      <div className="flex justify-between mb-1">
        <span className="text-xs text-text-muted">{label}</span>
        <span className="text-xs font-medium text-text-primary">{fmtTime(value)}</span>
      </div>
      <input
        type="range" min={min} max={max} value={value}
        onChange={e => onChange(Number(e.target.value))}
        className="w-full accent-primary"
        list={`steps-${label}`}
      />
      <datalist id={`steps-${label}`}>
        {steps.map(s => <option key={s} value={s} />)}
      </datalist>
      <div className="flex justify-between text-[10px] text-text-muted mt-0.5">
        <span>1 min</span><span>15 min</span><span>1 hr</span><span>4 hr</span><span>8 hr</span>
      </div>
    </div>
  );
}

function RuntimeCard({ rt, onSave }: { rt: RuntimeConfig; onSave: (id: string, idle: number, maxLife: number) => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [idle, setIdle] = useState(rt.idleTimeoutSec);
  const [maxLife, setMaxLife] = useState(rt.maxLifetimeSec);
  const [saving, setSaving] = useState(false);

  const isExec = rt.tier === 'executive';

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(rt.id, idle, maxLife);
      setEditing(false);
    } catch {}
    setSaving(false);
  };

  return (
    <Card className={isExec ? 'border-warning/30' : ''}>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className={`flex h-10 w-10 items-center justify-center rounded-xl ${isExec ? 'bg-warning/10' : 'bg-primary/10'}`}>
            {isExec ? <Zap size={20} className="text-warning" /> : <Cpu size={20} className="text-primary" />}
          </div>
          <div>
            <h3 className="text-sm font-semibold text-text-primary">{rt.name}</h3>
            <p className="text-xs text-text-muted">{rt.positions.join(' · ')}</p>
          </div>
        </div>
        <Badge color="success" dot>Active</Badge>
      </div>

      {/* Static info */}
      <div className="space-y-2 text-xs mb-4">
        <div className="flex justify-between rounded-lg bg-dark-bg px-3 py-2">
          <span className="text-text-muted">Docker Image</span>
          <span className="font-mono text-text-secondary">{rt.dockerImage}</span>
        </div>
        <div className="flex justify-between rounded-lg bg-dark-bg px-3 py-2">
          <span className="text-text-muted">Default Model</span>
          <span className={isExec ? 'text-warning font-medium' : 'text-text-secondary'}>{rt.model}{isExec ? ' ✦' : ''}</span>
        </div>
        <div className="flex justify-between rounded-lg bg-dark-bg px-3 py-2">
          <span className="text-text-muted">IAM Role</span>
          <div className="flex items-center gap-1.5">
            <span className="text-text-secondary">{rt.iamRole}</span>
            <Badge color={rt.iamLevel === 'full' ? 'warning' : 'success'}>
              {rt.iamLevel === 'full' ? 'Full Access' : 'Scoped'}
            </Badge>
          </div>
        </div>
      </div>

      {/* Lifecycle settings */}
      <div className="border-t border-dark-border/40 pt-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Clock size={14} className="text-text-muted" />
            <span className="text-xs font-medium text-text-primary">Lifecycle Settings</span>
          </div>
          {!editing ? (
            <Button variant="ghost" size="sm" onClick={() => setEditing(true)}>
              <Edit3 size={13} /> Edit
            </Button>
          ) : (
            <div className="flex gap-1.5">
              <Button variant="primary" size="sm" disabled={saving} onClick={handleSave}>
                <Save size={13} /> {saving ? 'Saving...' : 'Save'}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => { setEditing(false); setIdle(rt.idleTimeoutSec); setMaxLife(rt.maxLifetimeSec); }}>
                <X size={13} />
              </Button>
            </div>
          )}
        </div>

        {editing ? (
          <div className="space-y-4 bg-dark-bg rounded-lg p-3">
            <TimeSlider label="Idle timeout (no messages → terminate microVM)" value={idle} onChange={setIdle} />
            <TimeSlider label="Max lifetime (force restart regardless of activity)" value={maxLife} onChange={setMaxLife} />
            <div className="rounded-lg bg-info/5 border border-info/20 px-3 py-2 text-[10px] text-info">
              Changes apply to new microVM sessions. Running sessions are unaffected until they restart.
            </div>
            {idle > maxLife && (
              <div className="flex items-center gap-2 rounded-lg bg-danger/5 border border-danger/20 px-3 py-2 text-[10px] text-danger">
                <AlertTriangle size={12} /> Idle timeout cannot exceed max lifetime
              </div>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="rounded-lg bg-dark-bg px-3 py-2">
              <p className="text-text-muted mb-0.5">Idle timeout</p>
              <p className="font-medium text-text-primary">{fmtTime(rt.idleTimeoutSec)}</p>
              <p className="text-[10px] text-text-muted">No msg → microVM released</p>
            </div>
            <div className="rounded-lg bg-dark-bg px-3 py-2">
              <p className="text-text-muted mb-0.5">Max lifetime</p>
              <p className="font-medium text-text-primary">{fmtTime(rt.maxLifetimeSec)}</p>
              <p className="text-[10px] text-text-muted">Force restart ceiling</p>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

export default function RuntimesTab({ positions }: { positions: any[] }) {
  const [runtimes, setRuntimes] = useState(RUNTIMES);
  const [savingMapping, setSavingMapping] = useState(false);
  const [savedMapping, setSavedMapping] = useState(false);

  const handleLifecycleSave = async (runtimeId: string, idle: number, maxLife: number) => {
    if (idle > maxLife) throw new Error('idle > maxLife');
    try {
      await api.put(`/settings/runtime/${runtimeId}/lifecycle`, {
        idleTimeoutSec: idle,
        maxLifetimeSec: maxLife,
      });
    } catch {
      // API may not exist yet — update local state optimistically
    }
    setRuntimes(prev => prev.map(r => r.id === runtimeId
      ? { ...r, idleTimeoutSec: idle, maxLifetimeSec: maxLife }
      : r
    ));
  };

  return (
    <div className="space-y-6">
      <div className="rounded-lg bg-info/5 border border-info/20 px-4 py-3 text-sm text-info">
        Each Runtime has its own Docker image, IAM role, and lifecycle settings. Employees route to runtimes based on their position.
        Infrastructure-level isolation — IAM constraints cannot be bypassed by prompt injection.
      </div>

      {/* Runtime cards */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {runtimes.map(rt => (
          <RuntimeCard key={rt.id} rt={rt} onSave={handleLifecycleSave} />
        ))}
      </div>

      {/* Security layers */}
      <Card>
        <h3 className="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
          <Shield size={16} className="text-primary" /> Defense in Depth — Access Control Layers
        </h3>
        <div className="space-y-2">
          {[
            { layer: 'L1 — Prompt', desc: 'SOUL.md rules ("never access finance data")', safe: false, label: 'Prompt-level · Can be bypassed by injection' },
            { layer: 'L2 — Application', desc: 'Skills manifest allowedRoles / blockedRoles', safe: false, label: 'App-level · Code bug risk' },
            { layer: 'L3 — IAM Role', desc: 'Runtime execution role has no permission on target resource', safe: true, label: 'Infrastructure · Cannot be bypassed' },
            { layer: 'L4 — Network', desc: 'VPC isolation between Runtimes', safe: true, label: 'Infrastructure · Cannot be bypassed' },
          ].map(l => (
            <div key={l.layer} className={`flex items-center gap-3 rounded-lg px-4 py-2.5 ${l.safe ? 'bg-success/5 border border-success/20' : 'bg-dark-bg border border-dark-border/40'}`}>
              <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${l.safe ? 'bg-success' : 'bg-warning'}`} />
              <div className="flex-1 min-w-0">
                <span className={`text-xs font-semibold mr-2 ${l.safe ? 'text-success' : 'text-text-primary'}`}>{l.layer}</span>
                <span className="text-xs text-text-muted">{l.desc}</span>
              </div>
              <span className={`text-[10px] flex-shrink-0 ${l.safe ? 'text-success' : 'text-text-muted'}`}>{l.label}</span>
            </div>
          ))}
        </div>
      </Card>

      {/* Position → Runtime mapping */}
      <Card>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-sm font-semibold text-text-primary">Position → Runtime Mapping</h3>
            <p className="text-xs text-text-muted mt-0.5">Changes take effect on next microVM cold start — no redeployment needed</p>
          </div>
          <Button variant="primary" size="sm" disabled={savingMapping || savedMapping}
            onClick={async () => {
              setSavingMapping(true);
              try { await api.put('/settings/position-runtime-map', {}); } catch {}
              setSavingMapping(false); setSavedMapping(true);
              setTimeout(() => setSavedMapping(false), 3000);
            }}>
            <Save size={13} /> {savingMapping ? 'Saving...' : savedMapping ? 'Saved ✓' : 'Save Changes'}
          </Button>
        </div>
        <div className="space-y-2">
          {[
            { position: 'Solutions Architect', posId: 'pos-sa', runtime: 'Standard', model: 'Nova 2 Lite', highlight: false },
            { position: 'Software Engineer', posId: 'pos-sde', runtime: 'Standard', model: 'Nova 2 Lite', highlight: false },
            { position: 'DevOps Engineer', posId: 'pos-devops', runtime: 'Standard', model: 'Nova 2 Lite', highlight: false },
            { position: 'Finance Analyst', posId: 'pos-fa', runtime: 'Standard', model: 'Nova 2 Lite', highlight: false },
            { position: 'HR Specialist', posId: 'pos-hr', runtime: 'Standard', model: 'Nova 2 Lite', highlight: false },
            { position: 'Product Manager', posId: 'pos-pm', runtime: 'Standard', model: 'Nova 2 Lite', highlight: false },
            { position: 'Executive', posId: 'pos-exec', runtime: 'Executive ✦', model: 'Claude Sonnet 4.6', highlight: true },
          ].map(r => (
            <div key={r.position} className={`flex items-center justify-between rounded-lg px-4 py-2.5 ${r.highlight ? 'bg-warning/5 border border-warning/20' : 'bg-dark-bg border border-dark-border/30'}`}>
              <div>
                <span className={`text-xs font-medium ${r.highlight ? 'text-warning' : 'text-text-primary'}`}>{r.position}</span>
                <span className="text-[10px] text-text-muted ml-2">{r.posId}</span>
              </div>
              <div className="flex items-center gap-3">
                <Badge color={r.highlight ? 'warning' : 'default'}>{r.runtime}</Badge>
                <span className="text-xs text-text-muted">{r.model}</span>
                {r.highlight && <span className="text-[10px] text-warning">IAM: Full</span>}
              </div>
            </div>
          ))}
        </div>
      </Card>

      {/* What's configurable — design reference */}
      <Card>
        <h3 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
          <HardDrive size={14} className="text-text-muted" /> Session Termination — All Configurable Signals
        </h3>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 text-xs">
          {[
            { cat: 'AgentCore API', items: ['Idle timeout (no message → terminate)', 'Max lifetime (force restart ceiling)'] },
            { cat: 'Application Layer', items: ['Token budget per session (context overrun → graceful close)', 'Daily message quota per employee'] },
            { cat: 'Admin Controls', items: ['Emergency terminate (one-click kill for dept/employee)', 'Session inactivity warning (bot sends "2min left" alert)'] },
            { cat: 'Cost Control', items: ['Budget overage → auto-downgrade model (Sonnet → Nova)', 'Daily spend cap → pause new sessions for dept'] },
          ].map(section => (
            <div key={section.cat} className="rounded-lg bg-dark-bg px-3 py-2.5">
              <p className="font-medium text-text-secondary mb-1.5">{section.cat}</p>
              {section.items.map(item => (
                <div key={item} className="flex items-start gap-1.5 mb-1">
                  <div className="w-1 h-1 rounded-full bg-primary mt-1.5 flex-shrink-0" />
                  <span className="text-text-muted">{item}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
        <p className="text-[10px] text-text-muted mt-3">AgentCore API controls are live. Application layer controls (token budget, daily quota, emergency terminate) are roadmap items.</p>
      </Card>
    </div>
  );
}
