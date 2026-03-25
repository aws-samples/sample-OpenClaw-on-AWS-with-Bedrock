import { ReactNode } from 'react';
import clsx from 'clsx';

// === Card (M3 Surface Container with tonal elevation) ===
export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={clsx(
      'rounded-[20px] bg-dark-card p-5 border border-dark-border/50',
      'transition-all duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]',
      'hover:border-dark-border/80',
      className
    )}>
      {children}
    </div>
  );
}

// === Stat Card (M3 Filled Card with gradient accent) ===
export function StatCard({ title, value, subtitle, icon, trend, trendValue, color = 'primary' }: {
  title: string; value: string | number; subtitle?: string; icon: ReactNode;
  trend?: 'up' | 'down' | 'flat'; trendValue?: string;
  color?: 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'cyan';
}) {
  const iconBg = {
    primary: 'bg-primary/12 text-primary',
    success: 'bg-success/12 text-success',
    warning: 'bg-warning/12 text-warning',
    danger: 'bg-danger/12 text-danger',
    info: 'bg-info/12 text-info',
    cyan: 'bg-cyan/12 text-cyan',
  };
  const accentLine = {
    primary: 'from-primary/40 to-primary/0',
    success: 'from-success/40 to-success/0',
    warning: 'from-warning/40 to-warning/0',
    danger: 'from-danger/40 to-danger/0',
    info: 'from-info/40 to-info/0',
    cyan: 'from-cyan/40 to-cyan/0',
  };
  return (
    <div className="relative overflow-hidden rounded-[20px] bg-dark-card border border-dark-border/50 p-5 transition-all duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:border-dark-border/80 group">
      {/* M3 accent gradient line at top */}
      <div className={clsx('absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r', accentLine[color], 'opacity-60 group-hover:opacity-100 transition-opacity duration-300')} />
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm text-text-secondary">{title}</p>
          <p className="mt-1.5 text-2xl font-semibold tracking-tight text-text-primary">{value}</p>
          {subtitle && <p className="mt-1 text-xs text-text-muted">{subtitle}</p>}
          {trend && trendValue && (
            <div className="mt-2 flex items-center gap-1.5">
              <span className={clsx(
                'inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-xs font-medium',
                trend === 'up' ? 'bg-success/10 text-success' : trend === 'down' ? 'bg-danger/10 text-danger' : 'bg-dark-hover text-text-muted'
              )}>
                {trend === 'up' ? '↑' : trend === 'down' ? '↓' : '→'} {trendValue}
              </span>
            </div>
          )}
        </div>
        <div className={clsx('flex h-11 w-11 items-center justify-center rounded-2xl transition-transform duration-300 group-hover:scale-110', iconBg[color])}>
          {icon}
        </div>
      </div>
    </div>
  );
}

// === Badge (M3 Assist Chip style) ===
export function Badge({ children, color = 'default', dot }: {
  children: ReactNode; color?: 'default' | 'primary' | 'success' | 'warning' | 'danger' | 'info'; dot?: boolean;
}) {
  const colorMap = {
    default: 'bg-surface-container-highest/60 text-text-secondary',
    primary: 'bg-primary/10 text-primary',
    success: 'bg-success/10 text-success',
    warning: 'bg-warning/10 text-warning',
    danger: 'bg-danger/10 text-danger',
    info: 'bg-info/10 text-info',
  };
  return (
    <span className={clsx('inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors duration-200', colorMap[color])}>
      {dot && <span className={clsx('h-1.5 w-1.5 rounded-full', color === 'success' ? 'bg-success' : color === 'danger' ? 'bg-danger' : color === 'warning' ? 'bg-warning' : 'bg-text-muted')} />}
      {children}
    </span>
  );
}


// === Button (M3 Filled/Tonal/Outlined) ===
export function Button({ children, variant = 'default', size = 'md', onClick, className, disabled }: {
  children: ReactNode; variant?: 'primary' | 'default' | 'danger' | 'ghost' | 'success';
  size?: 'sm' | 'md' | 'lg'; onClick?: () => void; className?: string; disabled?: boolean;
}) {
  const variants = {
    primary: 'bg-primary/90 hover:bg-primary text-dark-bg font-medium shadow-sm shadow-primary/20',
    default: 'bg-surface-container-high hover:bg-surface-container-highest text-text-primary border border-dark-border/60',
    danger: 'bg-danger/10 hover:bg-danger/20 text-danger',
    ghost: 'hover:bg-surface-container-high text-text-secondary hover:text-text-primary',
    success: 'bg-success/10 hover:bg-success/20 text-success',
  };
  const sizes = { sm: 'px-3 py-1.5 text-xs rounded-xl', md: 'px-4 py-2 text-sm rounded-2xl', lg: 'px-5 py-2.5 text-sm rounded-2xl' };
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        'inline-flex items-center justify-center gap-2 font-medium',
        'transition-all duration-200 ease-[cubic-bezier(0.22,1,0.36,1)]',
        'active:scale-[0.97] disabled:opacity-50 disabled:pointer-events-none',
        variants[variant], sizes[size], className
      )}
    >
      {children}
    </button>
  );
}

// === Page Header (M3 Top App Bar style) ===
export function PageHeader({ title, description, actions }: {
  title: string; description?: string; actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-text-primary">{title}</h1>
        {description && <p className="mt-1 text-sm text-text-secondary">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-3">{actions}</div>}
    </div>
  );
}

// === Table (M3 Data Table with rounded rows) ===
export function Table<T>({ columns, data, onRowClick, emptyText = 'No data' }: {
  columns: { key: string; label: string; render: (item: T) => ReactNode; width?: string }[];
  data: T[]; onRowClick?: (item: T) => void; emptyText?: string;
}) {
  return (
    <div className="overflow-x-auto rounded-2xl border border-dark-border/50">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-dark-border/50 bg-surface-container/50">
            {columns.map(col => (
              <th key={col.key} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-muted" style={col.width ? { width: col.width } : undefined}>
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-dark-border/30">
          {data.length === 0 ? (
            <tr><td colSpan={columns.length} className="px-4 py-12 text-center text-text-muted">{emptyText}</td></tr>
          ) : (
            data.map((item, i) => (
              <tr
                key={i}
                onClick={() => onRowClick?.(item)}
                className={clsx(
                  'bg-dark-card transition-all duration-200 ease-[cubic-bezier(0.22,1,0.36,1)]',
                  'hover:bg-surface-container-high',
                  onRowClick && 'cursor-pointer'
                )}
              >
                {columns.map(col => (
                  <td key={col.key} className="px-4 py-3 text-text-primary">{col.render(item)}</td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

// === Modal (M3 Dialog with spring animation) ===
export function Modal({ open, onClose, title, children, size = 'md', footer }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode; size?: 'sm' | 'md' | 'lg' | 'xl'; footer?: ReactNode;
}) {
  if (!open) return null;
  const sizeMap = { sm: 'max-w-md', md: 'max-w-lg', lg: 'max-w-2xl', xl: 'max-w-4xl' };
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60 animate-fade-enter" onClick={onClose} />
      <div className={clsx(
        'relative w-full rounded-[28px] border border-dark-border/50 bg-dark-card shadow-2xl animate-card-enter',
        sizeMap[size]
      )}>
        {title && (
          <div className="flex items-center justify-between px-6 py-5">
            <h3 className="text-lg font-semibold text-text-primary">{title}</h3>
            <button onClick={onClose} className="rounded-full p-2 text-text-muted hover:bg-surface-container-high hover:text-text-primary transition-colors duration-200">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
            </button>
          </div>
        )}
        <div className="max-h-[70vh] overflow-y-auto px-6 pb-4">{children}</div>
        {footer && <div className="border-t border-dark-border/30 px-6 py-4">{footer}</div>}
      </div>
    </div>
  );
}

// === Tabs (M3 Tab Bar with animated indicator) ===
export function Tabs({ tabs, activeTab, onChange }: {
  tabs: { id: string; label: string; count?: number }[]; activeTab: string; onChange: (id: string) => void;
}) {
  return (
    <div className="flex gap-1 border-b border-dark-border/40">
      {tabs.map(tab => (
        <button
          key={tab.id}
          onClick={() => onChange(tab.id)}
          className={clsx(
            'relative px-4 py-2.5 text-sm font-medium transition-colors duration-200 -mb-px',
            activeTab === tab.id
              ? 'text-primary'
              : 'text-text-secondary hover:text-text-primary'
          )}
        >
          {tab.label}
          {tab.count !== undefined && (
            <span className={clsx('ml-2 rounded-full px-2 py-0.5 text-xs', activeTab === tab.id ? 'bg-primary/15 text-primary' : 'bg-surface-container-highest/60 text-text-muted')}>
              {tab.count}
            </span>
          )}
          {/* M3 active indicator */}
          {activeTab === tab.id && (
            <span className="absolute bottom-0 left-2 right-2 h-[3px] rounded-full bg-primary animate-scale-enter" />
          )}
        </button>
      ))}
    </div>
  );
}

// === Input (M3 Outlined Text Field) ===
export function Input({ label, value, onChange, placeholder, type = 'text', description }: {
  label?: string; value: string; onChange: (v: string) => void; placeholder?: string; type?: string; description?: string;
}) {
  return (
    <div>
      {label && <label className="mb-1.5 block text-sm font-medium text-text-primary">{label}</label>}
      {description && <p className="mb-1.5 text-xs text-text-muted">{description}</p>}
      <input
        type={type}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-2xl border border-dark-border/60 bg-surface-dim px-4 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-primary/60 focus:outline-none focus:ring-2 focus:ring-primary/10 transition-all duration-200"
      />
    </div>
  );
}

// === Textarea (M3 Outlined) ===
export function Textarea({ label, value, onChange, placeholder, rows = 4, description }: {
  label?: string; value: string; onChange: (v: string) => void; placeholder?: string; rows?: number; description?: string;
}) {
  return (
    <div>
      {label && <label className="mb-1.5 block text-sm font-medium text-text-primary">{label}</label>}
      {description && <p className="mb-1.5 text-xs text-text-muted">{description}</p>}
      <textarea
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        rows={rows}
        className="w-full rounded-2xl border border-dark-border/60 bg-surface-dim px-4 py-3 text-sm text-text-primary placeholder:text-text-muted focus:border-primary/60 focus:outline-none focus:ring-2 focus:ring-primary/10 transition-all duration-200 resize-none"
      />
    </div>
  );
}

// === Select (M3 Outlined) ===
export function Select({ label, value, onChange, options, placeholder, description }: {
  label?: string; value: string; onChange: (v: string) => void;
  options: { label: string; value: string; description?: string }[];
  placeholder?: string; description?: string;
}) {
  return (
    <div>
      {label && <label className="mb-1.5 block text-sm font-medium text-text-primary">{label}</label>}
      {description && <p className="mb-1.5 text-xs text-text-muted">{description}</p>}
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className="w-full rounded-2xl border border-dark-border/60 bg-surface-dim px-4 py-2.5 text-sm text-text-primary focus:border-primary/60 focus:outline-none focus:ring-2 focus:ring-primary/10 transition-all duration-200 appearance-none"
      >
        {placeholder && <option value="">{placeholder}</option>}
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}

// === Toggle (M3 Switch) ===
export function Toggle({ label, checked, onChange, description }: {
  label: string; checked: boolean; onChange: (v: boolean) => void; description?: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <button
        onClick={() => onChange(!checked)}
        className={clsx(
          'relative mt-0.5 h-7 w-12 shrink-0 rounded-full transition-all duration-300 ease-[cubic-bezier(0.34,1.56,0.64,1)]',
          checked ? 'bg-primary' : 'bg-surface-container-highest'
        )}
      >
        <span className={clsx(
          'absolute top-1 h-5 w-5 rounded-full bg-white shadow-sm transition-all duration-300 ease-[cubic-bezier(0.34,1.56,0.64,1)]',
          checked ? 'translate-x-5.5 scale-100' : 'translate-x-1 scale-90'
        )} />
      </button>
      <div>
        <p className="text-sm font-medium text-text-primary">{label}</p>
        {description && <p className="text-xs text-text-muted">{description}</p>}
      </div>
    </div>
  );
}

// === Status Dot (M3 with pulse animation for active) ===
export function StatusDot({ status }: { status: 'active' | 'idle' | 'error' | 'archived' | 'inactive' | 'pending' | 'disconnected' | 'expired' | 'bound' | 'revoked' | string }) {
  const colorMap: Record<string, string> = {
    active: 'bg-success', idle: 'bg-text-muted', error: 'bg-danger', archived: 'bg-text-muted',
    inactive: 'bg-text-muted', pending: 'bg-info', disconnected: 'bg-warning', expired: 'bg-danger',
    bound: 'bg-success', revoked: 'bg-danger', completed: 'bg-text-muted',
  };
  const labelMap: Record<string, string> = {
    active: 'Active', idle: 'Idle', bound: 'Bound', pending: 'Pending', expired: 'Expired',
    disconnected: 'Disconnected', error: 'Error', revoked: 'Revoked', completed: 'Completed',
  };
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="relative flex h-2 w-2">
        {status === 'active' && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-40" />}
        {status === 'pending' && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-info opacity-40" />}
        <span className={clsx('relative inline-flex h-2 w-2 rounded-full', colorMap[status] || 'bg-text-muted')} />
      </span>
      <span className="text-sm">{labelMap[status] || status}</span>
    </span>
  );
}
