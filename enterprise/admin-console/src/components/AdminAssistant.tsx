/**
 * IT Admin Assistant — Floating chat bubble in Admin Console.
 *
 * PATH B: Runs directly on EC2's OpenClaw CLI (NOT via AgentCore microVM).
 *
 * Architecture:
 *   Chat bubble → POST /playground/send (tenant_id=port__admin)
 *   → FastAPI _admin_assistant_direct() → subprocess: openclaw CLI on EC2
 *   → OpenClaw calls Bedrock directly (bypasses H2 Proxy)
 *
 * Completely independent from PATH A (employee agents via AgentCore).
 * Has real access to EC2 filesystem, services, and logs (read-only).
 */
import { useState, useRef, useEffect, useCallback } from 'react';
import { Terminal, X, Send, Bot, User, Minimize2, Trash2, Sparkles } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { useAuth } from '../contexts/AuthContext';
import { api } from '../api/client';
import ClawForgeLogo from './ClawForgeLogo';
import clsx from 'clsx';

interface Message {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
}

const STORAGE_KEY = 'openclaw_admin_assistant';

function loadMessages(): Message[] {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'); }
  catch { return []; }
}

function saveMessages(msgs: Message[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(msgs));
}

export default function AdminAssistant() {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>(() => {
    const saved = loadMessages();
    if (saved.length > 0) return saved;
    return [{
      id: 0, role: 'assistant',
      content: `Hi! I'm the **IT Admin Assistant** for OpenClaw Enterprise.\n\nI run on this EC2 instance with full system access. I can:\n- 🔍 Check service status, logs, configurations\n- 📊 Query DynamoDB, S3, SSM data\n- 🛠 Run shell commands on the Gateway EC2\n- 📋 Explain the architecture and help troubleshoot\n\nWhat do you need?`,
      timestamp: new Date().toISOString(),
    }];
  });
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { saveMessages(messages); }, [messages]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);
  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 300); }, [open]);

  const clearChat = useCallback(async () => {
    try { await api.del('/admin-ai/chat'); } catch {}
    setMessages([{
      id: Date.now(), role: 'assistant',
      content: 'Chat cleared. How can I help?',
      timestamp: new Date().toISOString(),
    }]);
  }, []);

  const sendMessage = async () => {
    if (!input.trim() || sending) return;
    const userMsg: Message = { id: Date.now(), role: 'user', content: input.trim(), timestamp: new Date().toISOString() };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setSending(true);

    try {
      const resp = await api.post<{ response: string }>('/admin-ai/chat', {
        message: userMsg.content,
      });
      setMessages(prev => [...prev, {
        id: Date.now() + 1, role: 'assistant',
        content: resp.response,
        timestamp: new Date().toISOString(),
      }]);
    } catch (err: any) {
      setMessages(prev => [...prev, {
        id: Date.now() + 1, role: 'assistant',
        content: `⚠️ ${err?.message || 'Connection failed. Please try again.'}`,
        timestamp: new Date().toISOString(),
      }]);
    } finally {
      setSending(false);
    }
  };

  return (
    <>
      {/* Floating bubble */}
      <button
        onClick={() => setOpen(!open)}
        className={clsx(
          'fixed bottom-6 right-6 z-[90] flex items-center justify-center rounded-full shadow-lg',
          'transition-all duration-500 ease-[cubic-bezier(0.34,1.56,0.64,1)]',
          open
            ? 'h-12 w-12 bg-surface-container-highest text-text-muted hover:bg-dark-hover'
            : 'h-14 w-14 bg-gradient-to-br from-primary to-indigo-600 text-white shadow-lg shadow-primary/30 hover:scale-110'
        )}
      >
        {open
          ? <X size={20} />
          : <div className="relative flex items-center justify-center">
              <Terminal size={22} />
              <Sparkles size={10} className="absolute -top-1 -right-1 text-yellow-300" />
            </div>
        }
        {!open && messages.length > 1 && (
          <span className="absolute -top-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-danger text-[10px] text-white font-medium animate-scale-enter">
            {messages.filter(m => m.role === 'assistant').length}
          </span>
        )}
      </button>

      {/* Chat panel */}
      <div className={clsx(
        'fixed bottom-24 right-6 z-[90] w-[520px] max-h-[700px] flex flex-col',
        'rounded-[24px] border border-dark-border/50 bg-dark-card shadow-2xl',
        'transition-all duration-500 ease-[cubic-bezier(0.34,1.56,0.64,1)] origin-bottom-right',
        open
          ? 'opacity-100 scale-100 translate-y-0 pointer-events-auto'
          : 'opacity-0 scale-90 translate-y-4 pointer-events-none'
      )}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-dark-border/30">
          <div className="flex items-center gap-3">
            <div className="relative">
              <ClawForgeLogo size={32} animate={sending ? 'working' : 'idle'} />
              <span className="absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full bg-success border-2 border-dark-card" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-text-primary">IT Admin Assistant</h3>
              <p className="text-[10px] text-text-muted">查员工权限 · 改 SOUL 配置 · 查用量审计</p>
            </div>
          </div>
          <div className="flex items-center gap-1">
            <button onClick={clearChat} className="rounded-full p-1.5 text-text-muted hover:bg-dark-hover hover:text-text-primary transition-colors" title="Clear chat">
              <Trash2 size={14} />
            </button>
            <button onClick={() => setOpen(false)} className="rounded-full p-1.5 text-text-muted hover:bg-dark-hover hover:text-text-primary transition-colors">
              <Minimize2 size={14} />
            </button>
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3 min-h-[400px] max-h-[520px]">
          {messages.map(msg => (
            <div key={msg.id} className={clsx('flex gap-2.5', msg.role === 'user' ? 'justify-end' : '')}>
              {msg.role === 'assistant' && (
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary mt-0.5">
                  <Bot size={14} />
                </div>
              )}
              <div className={clsx(
                'max-w-[85%] rounded-2xl px-3.5 py-2.5',
                msg.role === 'user'
                  ? 'bg-primary text-white rounded-br-lg'
                  : 'bg-surface-container-high text-text-primary rounded-bl-lg'
              )}>
                {msg.role === 'assistant' ? (
                  <div className="text-[13px] leading-relaxed prose prose-invert prose-sm max-w-none [&_p]:my-1 [&_h1]:text-sm [&_h1]:font-bold [&_h2]:text-xs [&_h2]:font-semibold [&_ul]:my-1 [&_li]:my-0.5 [&_code]:bg-dark-bg [&_code]:px-1 [&_code]:rounded [&_pre]:bg-dark-bg [&_pre]:p-2 [&_pre]:rounded-xl [&_pre]:my-1.5 [&_pre]:text-xs [&_strong]:text-text-primary [&_table]:text-xs [&_th]:px-2 [&_td]:px-2">
                    <ReactMarkdown>{msg.content}</ReactMarkdown>
                  </div>
                ) : (
                  <p className="text-[13px] leading-relaxed whitespace-pre-wrap">{msg.content}</p>
                )}
                <p className={clsx('text-[9px] mt-1', msg.role === 'user' ? 'text-white/50' : 'text-text-muted')}>
                  {new Date(msg.timestamp).toLocaleTimeString()}
                </p>
              </div>
              {msg.role === 'user' && (
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-xl bg-info/10 text-info mt-0.5">
                  <User size={14} />
                </div>
              )}
            </div>
          ))}
          {sending && (
            <div className="flex gap-2.5">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-xl bg-primary/10 mt-0.5">
                <ClawForgeLogo size={18} animate="working" />
              </div>
              <div className="rounded-2xl rounded-bl-lg bg-surface-container-high px-3.5 py-2.5">
                <div className="flex items-center gap-1.5">
                  <span className="h-1.5 w-1.5 rounded-full bg-primary animate-bounce" style={{ animationDelay: '0ms' }} />
                  <span className="h-1.5 w-1.5 rounded-full bg-primary animate-bounce" style={{ animationDelay: '150ms' }} />
                  <span className="h-1.5 w-1.5 rounded-full bg-primary animate-bounce" style={{ animationDelay: '300ms' }} />
                </div>
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="border-t border-dark-border/30 px-4 py-3">
          <div className="flex gap-2">
            <input
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && !e.shiftKey && sendMessage()}
              placeholder="查员工权限、改 SOUL、查用量..."
              disabled={sending}
              className="flex-1 rounded-2xl border border-dark-border/40 bg-surface-dim px-3.5 py-2 text-[13px] text-text-primary placeholder:text-text-muted focus:border-primary/50 focus:outline-none disabled:opacity-50"
            />
            <button
              onClick={sendMessage}
              disabled={!input.trim() || sending}
              className="flex h-9 w-9 items-center justify-center rounded-2xl bg-primary text-white hover:bg-primary/90 disabled:opacity-40 transition-all duration-200 active:scale-95"
            >
              <Send size={15} />
            </button>
          </div>
          <p className="text-[9px] text-text-muted mt-1.5 text-center">
            Powered by Claude on Amazon Bedrock · Enter 发送
          </p>
        </div>
      </div>
    </>
  );
}
