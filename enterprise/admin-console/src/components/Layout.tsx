import { ReactNode, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  LayoutDashboard, Building2, Users, Bot, Puzzle, Activity,
  ShieldCheck, Shield, DollarSign, Gamepad2, Settings, ChevronDown, ChevronRight,
  Bell, Search, Menu, X, CheckCircle, LogOut, User, FolderOpen, BookOpen,
  Sun, Moon, MessageSquare, Plug,
} from 'lucide-react';
import { useApprovals, useAlertRules, useAgents, useEmployees } from '../hooks/useApi';
import { useAuth } from '../contexts/AuthContext';
import { useTheme } from '../contexts/ThemeContext';
import ClawForgeLogo from './ClawForgeLogo';
import AdminAssistant from './AdminAssistant';
import { ErrorBoundary } from './ui';
import clsx from 'clsx';

interface NavItem {
  label: string;
  href?: string;
  icon: ReactNode;
  children?: { label: string; href: string }[];
  badge?: number;
}

// Full admin nav
const ADMIN_NAV: NavItem[] = [
  { label: 'Dashboard', href: '/dashboard', icon: <LayoutDashboard size={20} /> },
  {
    label: 'Organization', icon: <Building2 size={20} />,
    children: [
      { label: 'Department Tree', href: '/org/departments' },
      { label: 'Positions', href: '/org/positions' },
      { label: 'Employees', href: '/org/employees' },
      { label: 'Agent Assignments', href: '/bindings' },
    ],
  },
  { label: 'Agent Factory', href: '/agents', icon: <Bot size={20} /> },
  { label: 'Workspace', href: '/workspace', icon: <FolderOpen size={20} /> },
  { label: 'Tools & Skills', href: '/skills', icon: <Puzzle size={20} /> },
  { label: 'MCP Connections', href: '/mcp', icon: <Plug size={20} /> },
  { label: 'Knowledge Base', href: '/knowledge', icon: <BookOpen size={20} /> },
  { label: 'IM Channels', href: '/channels', icon: <MessageSquare size={20} /> },
  { label: 'Security Center', href: '/security', icon: <ShieldCheck size={20} /> },
  { label: 'Monitor', href: '/monitor', icon: <Activity size={20} /> },
  { label: 'Audit Center', href: '/audit', icon: <Shield size={20} /> },
  { label: 'Approvals', href: '/approvals', icon: <CheckCircle size={20} /> },
  { label: 'Usage & Cost', href: '/usage', icon: <DollarSign size={20} /> },
  { label: 'Playground', href: '/playground', icon: <Gamepad2 size={20} /> },
  { label: 'Settings', href: '/settings', icon: <Settings size={20} /> },
  { label: 'My Portal', href: '/portal', icon: <User size={20} /> },
];

// Manager nav — own dept data only, no platform-wide settings
const MANAGER_NAV: NavItem[] = [
  { label: 'Dashboard', href: '/dashboard', icon: <LayoutDashboard size={20} /> },
  {
    label: 'My Team', icon: <Building2 size={20} />,
    children: [
      { label: 'Employees', href: '/org/employees' },
      { label: 'Agent Assignments', href: '/bindings' },
    ],
  },
  { label: 'Approvals', href: '/approvals', icon: <CheckCircle size={20} /> },
  { label: 'Monitor', href: '/monitor', icon: <Activity size={20} /> },
  { label: 'Audit Center', href: '/audit', icon: <Shield size={20} /> },
  { label: 'My Portal', href: '/portal', icon: <User size={20} /> },
];

// Choose nav based on role (set dynamically in Layout component)
const NAV = ADMIN_NAV; // default — overridden per-role in component

function SidebarItem({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  const isActive = item.href
    ? location.pathname === item.href || location.pathname.startsWith(item.href + '/')
    : item.children?.some(c => location.pathname === c.href || location.pathname.startsWith(c.href + '/'));

  if (item.children) {
    const childActive = item.children.some(c => location.pathname === c.href || location.pathname.startsWith(c.href + '/'));
    return (
      <div>
        <button
          onClick={() => setOpen(!open)}
          className={clsx(
            'flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors',
            childActive ? 'bg-primary/10 text-primary-light' : 'text-text-secondary hover:bg-dark-hover hover:text-text-primary'
          )}
        >
          {item.icon}
          {!collapsed && (
            <>
              <span className="flex-1 text-left">{item.label}</span>
              {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            </>
          )}
        </button>
        {!collapsed && open && (
          <div className="ml-8 mt-1 space-y-0.5">
            {item.children.map(child => (
              <button
                key={child.href}
                onClick={() => navigate(child.href)}
                className={clsx(
                  'block w-full rounded-md px-3 py-2 text-left text-sm transition-colors',
                  location.pathname === child.href
                    ? 'bg-primary/10 text-primary-light font-medium'
                    : 'text-text-secondary hover:text-text-primary hover:bg-dark-hover'
                )}
              >
                {child.label}
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <button
      onClick={() => item.href && navigate(item.href)}
      className={clsx(
        'flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors',
        isActive ? 'bg-primary/10 text-primary-light font-medium' : 'text-text-secondary hover:bg-dark-hover hover:text-text-primary'
      )}
    >
      {item.icon}
      {!collapsed && <span className="flex-1 text-left">{item.label}</span>}
      {!collapsed && item.badge && (
        <span className="rounded-full bg-danger px-2 py-0.5 text-xs text-white">{item.badge}</span>
      )}
    </button>
  );
}

export default function Layout({ children }: { children: ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchFocused, setSearchFocused] = useState(false);
  const navigate = useNavigate();
  const { data: approvalsData } = useApprovals();
  const { data: alertRules = [] } = useAlertRules();
  const { data: agents = [] } = useAgents();
  const { data: employees = [] } = useEmployees();
  const { user, logout } = useAuth();
  const { theme, toggle: toggleTheme } = useTheme();
  const pendingApprovals = approvalsData?.pending?.length || 0;
  const activeAlerts = alertRules.filter(a => a.status === 'warning').length;
  // Role-based nav
  const NAV_ITEMS = user?.role === 'manager' ? MANAGER_NAV : ADMIN_NAV;
  const notifCount = pendingApprovals + activeAlerts;

  // Quick search results — searches pages, agents, employees
  const searchResults = searchQuery.length >= 2 ? (() => {
    const seen = new Set<string>();
    const results: { label: string; href: string; type: string }[] = [];
    const q = searchQuery.toLowerCase();
    const add = (label: string, href: string, type: string) => {
      const key = `${label}:${href}`;
      if (!seen.has(key)) { seen.add(key); results.push({ label, href, type }); }
    };
    // Pages
    NAV.forEach(item => {
      if (item.href && item.label.toLowerCase().includes(q))
        add(item.label, item.href, 'page');
      item.children?.forEach(c => {
        if (c.label.toLowerCase().includes(q))
          add(c.label, c.href, 'page');
      });
    });
    // Agents
    agents.forEach(a => {
      if (a.name.toLowerCase().includes(q) || a.employeeName.toLowerCase().includes(q) || a.positionName.toLowerCase().includes(q))
        add(`${a.name} (${a.positionName})`, `/agents/${a.id}`, 'agent');
    });
    // Employees
    employees.forEach(e => {
      if (e.name.toLowerCase().includes(q) || e.positionName.toLowerCase().includes(q) || e.departmentName.toLowerCase().includes(q))
        add(`${e.name} — ${e.positionName}`, '/org/employees', 'employee');
    });
    // Feature shortcuts
    if (q.includes('soul')) add('SOUL Editor', '/agents', 'feature');
    if (q.includes('alert')) add('Monitor Alerts', '/monitor', 'feature');
    if (q.includes('budget')) add('Budget Management', '/usage', 'feature');
    if (q.includes('provision')) add('Bulk Provision', '/org/positions', 'feature');
    return results.slice(0, 8);
  })() : [];

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Mobile overlay */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 bg-black/50 lg:hidden" onClick={() => setMobileOpen(false)} />
      )}

      {/* Sidebar */}
      <aside
        className={clsx(
          'fixed left-0 top-0 z-50 flex h-full flex-col border-r border-dark-border bg-dark-sidebar transition-all duration-300 lg:static',
          sidebarOpen ? 'w-64' : 'w-20',
          mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'
        )}
      >
        {/* Logo */}
        <div className="flex h-16 items-center gap-3 border-b border-dark-border px-4">
          <ClawForgeLogo size={32} animate="idle" />
          {sidebarOpen && (
            <div className="overflow-hidden">
              <div className="text-sm font-semibold text-text-primary truncate">OpenClaw Enterprise</div>
              <div className="text-[10px] text-text-muted truncate">on AgentCore · aws-samples</div>
            </div>
          )}
        </div>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-1">
          {NAV_ITEMS.map(item => (
            <SidebarItem key={item.label} item={item} collapsed={!sidebarOpen} />
          ))}
        </nav>

        {/* Theme toggle + Collapse */}
        <div className="hidden border-t border-dark-border p-3 lg:flex flex-col gap-2">
          <button
            onClick={toggleTheme}
            className="flex w-full items-center justify-center gap-2 rounded-2xl py-2.5 text-text-muted hover:bg-dark-hover hover:text-text-primary transition-all duration-300 ease-[cubic-bezier(0.34,1.56,0.64,1)] group"
            title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            <span className="relative w-5 h-5">
              <Sun size={18} className={clsx('absolute inset-0 transition-all duration-500', theme === 'light' ? 'opacity-100 rotate-0 scale-100' : 'opacity-0 rotate-90 scale-50')} />
              <Moon size={18} className={clsx('absolute inset-0 transition-all duration-500', theme === 'dark' ? 'opacity-100 rotate-0 scale-100' : 'opacity-0 -rotate-90 scale-50')} />
            </span>
            {sidebarOpen && <span className="text-sm">{theme === 'dark' ? 'Light mode' : 'Dark mode'}</span>}
          </button>
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="flex w-full items-center justify-center rounded-2xl py-2 text-text-muted hover:bg-dark-hover hover:text-text-primary transition-colors"
          >
            {sidebarOpen ? <ChevronRight size={18} /> : <Menu size={18} />}
          </button>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex h-16 items-center justify-between border-b border-dark-border bg-dark-card px-4 lg:px-6">
          <div className="flex items-center gap-4">
            <button onClick={() => setMobileOpen(true)} className="text-text-secondary hover:text-text-primary lg:hidden">
              <Menu size={24} />
            </button>
            <div className="relative hidden md:block">
              <Search size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
              <input
                type="text"
                placeholder="Search agents, employees, skills..."
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                onFocus={() => setSearchFocused(true)}
                onBlur={() => setTimeout(() => setSearchFocused(false), 200)}
                className="w-80 rounded-lg border border-dark-border bg-dark-bg py-2 pl-10 pr-4 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none"
              />
              {searchFocused && searchResults.length > 0 && (
                <div className="absolute top-full left-0 right-0 mt-1 rounded-2xl border border-dark-border/50 bg-dark-card shadow-xl z-50 overflow-hidden">
                  {searchResults.map((r, i) => (
                    <button key={i} onClick={() => { navigate(r.href); setSearchQuery(''); setSearchFocused(false); }}
                      className="flex items-center gap-3 w-full px-4 py-2.5 text-left hover:bg-dark-hover transition-colors">
                      {r.type === 'agent' ? <Bot size={14} className="text-primary shrink-0" /> :
                       r.type === 'employee' ? <Users size={14} className="text-cyan shrink-0" /> :
                       <Search size={14} className="text-text-muted shrink-0" />}
                      <span className="text-sm text-text-primary truncate">{r.label}</span>
                      <span className="text-[10px] text-text-muted ml-auto shrink-0 rounded-full bg-surface-container-highest/60 px-2 py-0.5">{r.type}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div onClick={() => navigate('/monitor')} className="flex items-center gap-1.5 rounded-full px-3 py-1 cursor-pointer" style={{ backgroundColor: activeAlerts > 0 ? 'rgba(239,68,68,0.1)' : 'rgba(34,197,94,0.1)' }}>
              <CheckCircle size={14} className={activeAlerts > 0 ? 'text-danger' : 'text-success'} />
              <span className={`text-xs font-medium ${activeAlerts > 0 ? 'text-danger' : 'text-success'}`}>
                {activeAlerts > 0 ? `${activeAlerts} Alert${activeAlerts > 1 ? 's' : ''}` : 'All Systems OK'}
              </span>
            </div>
            <button onClick={() => navigate('/approvals')} className="relative rounded-lg p-2 text-text-secondary hover:bg-dark-hover hover:text-text-primary transition-colors">
              <Bell size={20} />
              {notifCount > 0 && <span className="absolute right-1 top-1 flex h-4 w-4 items-center justify-center rounded-full bg-danger text-[10px] text-white font-medium">{notifCount}</span>}
            </button>
            <div className="h-6 w-px bg-dark-border" />
            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/20 text-primary text-sm font-medium">
                {user?.name?.[0] || 'A'}
              </div>
              <div className="hidden md:block">
                <div className="text-sm font-medium text-text-primary">{user?.name || 'Admin'}</div>
                <div className="text-xs text-text-muted">{user?.role === 'admin' ? 'Admin' : 'Manager'} · {user?.departmentName || ''}</div>
              </div>
              <button onClick={() => { logout(); navigate('/login'); }} className="ml-1 text-text-muted hover:text-text-primary" title="Sign out">
                <LogOut size={16} />
              </button>
            </div>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto p-4 lg:p-6">
          <ErrorBoundary>{children}</ErrorBoundary>
        </main>
      </div>

      {/* Floating Admin Assistant — admin only */}
      {user?.role === 'admin' && <AdminAssistant />}
    </div>
  );
}
