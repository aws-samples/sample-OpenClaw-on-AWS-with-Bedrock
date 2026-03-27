import { useState, useEffect, useCallback } from 'react';
import { CheckCircle, Loader2, RefreshCw, Link2, ExternalLink, Clock, AlertCircle } from 'lucide-react';
import { Card, Badge, Button } from '../../components/ui';
import { api } from '../../api/client';
import { IM_ICONS } from '../../components/IMIcons';

interface Channel {
  id: string;
  label: string;
  icon: string;
  description: string;
  available: boolean;
  note?: 'coming-soon' | 'not-enterprise';
}

const CHANNELS: Channel[] = [
  { id: 'telegram', label: 'Telegram', icon: '', description: 'Scan QR or click the link to open @acme_enterprise_bot', available: true },
  { id: 'discord', label: 'Discord', icon: '', description: 'Connect to ACME Agent in your company Discord server', available: true },
  { id: 'slack', label: 'Slack', icon: '', description: 'Connect to ACME Agent in your Slack workspace', available: false, note: 'coming-soon' },
  { id: 'teams', label: 'Microsoft Teams', icon: '', description: 'Connect to ACME Agent in Microsoft Teams', available: false, note: 'coming-soon' },
  { id: 'feishu', label: 'Feishu / Lark', icon: '', description: 'Connect to the enterprise Feishu bot', available: false, note: 'coming-soon' },
  { id: 'googlechat', label: 'Google Chat', icon: '', description: 'Connect to ACME Agent in Google Chat', available: false, note: 'coming-soon' },
  { id: 'whatsapp', label: 'WhatsApp', icon: '', description: 'Personal messaging — not recommended for enterprise use', available: false, note: 'not-enterprise' },
  { id: 'wechat', label: 'WeChat', icon: '', description: 'Personal messaging — not recommended for enterprise use', available: false, note: 'not-enterprise' },
];

type StepState = 'idle' | 'loading' | 'waiting' | 'done' | 'error' | 'expired';

interface PairSession {
  token: string;
  deepLink: string | null;
  botUsername: string;
  channel: string;
  expiresAt: number;
}

function CountdownTimer({ expiresAt }: { expiresAt: number }) {
  const [remaining, setRemaining] = useState(Math.max(0, Math.floor((expiresAt - Date.now()) / 1000)));
  useEffect(() => {
    const t = setInterval(() => {
      const left = Math.max(0, Math.floor((expiresAt - Date.now()) / 1000));
      setRemaining(left);
      if (left === 0) clearInterval(t);
    }, 1000);
    return () => clearInterval(t);
  }, [expiresAt]);
  const m = Math.floor(remaining / 60), s = remaining % 60;
  return (
    <span className={`text-xs font-mono ${remaining < 60 ? 'text-danger' : 'text-text-muted'}`}>
      <Clock size={11} className="inline mr-1" />{m}:{s.toString().padStart(2, '0')}
    </span>
  );
}

function ChannelWizard({ channel, onDone, onCancel }: { channel: Channel; onDone: () => void; onCancel: () => void }) {
  const [state, setState] = useState<StepState>('idle');
  const [session, setSession] = useState<PairSession | null>(null);
  const [error, setError] = useState('');

  const startPairing = useCallback(async () => {
    setState('loading');
    setError('');
    try {
      const data = await api.post<any>('/portal/channel/pair-start', { channel: channel.id });
      setSession({ ...data, expiresAt: Date.now() + data.expiresIn * 1000 });
      setState('waiting');
    } catch (e: any) {
      setError(e?.message || 'Failed to start pairing');
      setState('error');
    }
  }, [channel.id]);

  // Poll for completion
  useEffect(() => {
    if (state !== 'waiting' || !session) return;
    const interval = setInterval(async () => {
      // Check expiry
      if (Date.now() > session.expiresAt) { setState('expired'); clearInterval(interval); return; }
      try {
        const data = await api.get<any>(`/portal/channel/pair-status?token=${session.token}`);
        if (data.status === 'completed') { setState('done'); clearInterval(interval); setTimeout(onDone, 2000); }
        if (data.status === 'expired') { setState('expired'); clearInterval(interval); }
      } catch {}
    }, 2000);
    return () => clearInterval(interval);
  }, [state, session, onDone]);

  if (state === 'idle') return (
    <div className="space-y-4">
      <div className="rounded-xl bg-surface-dim p-4 text-center">
        <div className="flex justify-center mb-2">{IM_ICONS[channel.id] ? (() => { const Icon = IM_ICONS[channel.id]; return <Icon size={48} />; })() : null}</div>
        <h3 className="text-base font-semibold text-text-primary">{channel.label}</h3>
        <p className="text-sm text-text-muted mt-1">{channel.description}</p>
      </div>
      <Button variant="primary" className="w-full" onClick={startPairing}>
        <Link2 size={16} /> Generate Connection Link
      </Button>
      <Button variant="ghost" className="w-full" onClick={onCancel}>Back</Button>
    </div>
  );

  if (state === 'loading') return (
    <div className="flex flex-col items-center py-8 gap-3">
      <Loader2 size={32} className="animate-spin text-primary" />
      <p className="text-sm text-text-muted">Generating secure link...</p>
    </div>
  );

  if (state === 'waiting' && session) return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-text-primary">Step 1 — Open {channel.label}</p>
        <CountdownTimer expiresAt={session.expiresAt} />
      </div>

      {session.deepLink ? (
        <div className="space-y-3">
          {/* QR code via URL */}
          <div className="flex justify-center rounded-xl bg-white p-4">
            <img
              src={`https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(session.deepLink)}`}
              alt="QR code"
              width={200} height={200}
              className="rounded"
            />
          </div>
          <p className="text-xs text-text-muted text-center">Scan with your phone to open {channel.label}</p>
          <div className="relative">
            <div className="absolute inset-0 flex items-center"><div className="w-full border-t border-dark-border" /></div>
            <div className="relative flex justify-center text-xs"><span className="bg-dark-card px-2 text-text-muted">or</span></div>
          </div>
          <a href={session.deepLink} target="_blank" rel="noopener noreferrer">
            <Button variant="default" className="w-full">
              <ExternalLink size={14} /> Open {channel.label} directly
            </Button>
          </a>
        </div>
      ) : (
        <div className="rounded-xl bg-dark-bg p-4 text-center">
          <p className="text-xs text-text-muted mb-2">Send this command to @{session.botUsername}</p>
          <code className="text-sm font-mono text-primary-light bg-primary/10 px-3 py-2 rounded-lg block">
            /start {session.token}
          </code>
        </div>
      )}

      <div className="rounded-lg bg-info/5 border border-info/20 px-3 py-2.5 flex items-start gap-2">
        <Loader2 size={14} className="animate-spin text-info mt-0.5 flex-shrink-0" />
        <p className="text-xs text-info">Waiting for you to connect… tap Start in {channel.label} to complete.</p>
      </div>

      <Button variant="ghost" className="w-full text-xs" onClick={onCancel}>Cancel</Button>
    </div>
  );

  if (state === 'done') return (
    <div className="flex flex-col items-center py-8 gap-3 text-center">
      <div className="flex h-16 w-16 items-center justify-center rounded-full bg-success/10">
        <CheckCircle size={36} className="text-success" />
      </div>
      <h3 className="text-base font-semibold text-text-primary">Connected!</h3>
      <p className="text-sm text-text-muted">Your {channel.label} is now linked to your Agent.</p>
    </div>
  );

  if (state === 'expired') return (
    <div className="space-y-4 text-center">
      <div className="flex flex-col items-center py-4 gap-2">
        <AlertCircle size={32} className="text-warning" />
        <p className="text-sm text-text-muted">Link expired. Please generate a new one.</p>
      </div>
      <Button variant="primary" className="w-full" onClick={startPairing}><RefreshCw size={14} /> Try Again</Button>
      <Button variant="ghost" className="w-full" onClick={onCancel}>Back</Button>
    </div>
  );

  return (
    <div className="space-y-4 text-center py-4">
      <AlertCircle size={32} className="text-danger mx-auto" />
      <p className="text-sm text-danger">{error || 'Something went wrong'}</p>
      <Button variant="ghost" className="w-full" onClick={onCancel}>Back</Button>
    </div>
  );
}

export default function BindIM() {
  const [selected, setSelected] = useState<Channel | null>(null);
  const [connected, setConnected] = useState<string[]>([]);

  // Check existing connections
  useEffect(() => {
    api.get<any>('/portal/channels').then(d => {
      if (d?.connected) setConnected(d.connected);
    }).catch(() => {});
  }, []);

  const handleDone = useCallback((channelId: string) => {
    setConnected(prev => [...prev.filter(c => c !== channelId), channelId]);
    setSelected(null);
  }, []);

  if (selected) return (
    <div className="max-w-sm mx-auto p-6">
      <ChannelWizard
        channel={selected}
        onDone={() => handleDone(selected.id)}
        onCancel={() => setSelected(null)}
      />
    </div>
  );

  return (
    <div className="max-w-2xl mx-auto p-6 space-y-6">
      <div>
        <h1 className="text-xl font-bold text-text-primary">Connect IM Channels</h1>
        <p className="text-sm text-text-muted mt-1">
          Link your messaging apps so your AI Agent can respond directly in your favorite chat.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {CHANNELS.map(ch => {
          const isConnected = connected.includes(ch.id);
          const isNotEnterprise = ch.note === 'not-enterprise';
          const isComingSoon = ch.note === 'coming-soon';
          return (
            <Card key={ch.id} className={`transition-all ${
              !ch.available
                ? isNotEnterprise
                  ? 'opacity-40 cursor-not-allowed grayscale'
                  : 'opacity-50 cursor-not-allowed'
                : 'cursor-pointer hover:border-primary/40'
            }`}>
              <div className="flex items-start gap-3">
                <div className="flex-shrink-0 mt-0.5">{(() => { const Icon = IM_ICONS[ch.id]; return Icon ? <Icon size={28} /> : null; })()}</div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                    <h3 className="text-sm font-semibold text-text-primary">{ch.label}</h3>
                    {isConnected && <Badge color="success" dot>Connected</Badge>}
                    {isComingSoon && <Badge color="info">Coming soon</Badge>}
                    {isNotEnterprise && <Badge color="default">Not for enterprise</Badge>}
                  </div>
                  <p className="text-xs text-text-muted">{ch.description}</p>
                </div>
              </div>
              {ch.available && (
                <div className="mt-3">
                  {isConnected ? (
                    <Button variant="ghost" size="sm" className="w-full text-xs" onClick={() => setSelected(ch)}>
                      Reconnect
                    </Button>
                  ) : (
                    <Button variant="primary" size="sm" className="w-full" onClick={() => setSelected(ch)}>
                      <Link2 size={13} /> Connect
                    </Button>
                  )}
                </div>
              )}
              {isComingSoon && (
                <div className="mt-3">
                  <p className="text-[10px] text-text-muted text-center">Enterprise support coming soon</p>
                </div>
              )}
            </Card>
          );
        })}
      </div>

      <div className="rounded-lg bg-dark-bg border border-dark-border/40 px-4 py-3 text-xs text-text-muted">
        All connections are managed by your IT Admin and can be revoked at any time.
        Your messages are routed to your personal AI Agent only.
      </div>
    </div>
  );
}
