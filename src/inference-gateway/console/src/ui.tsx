import { useEffect, useState, type InputHTMLAttributes, type ReactNode } from 'react';
import { label } from './api';

// Built once: the sidebar renders a dozen icons on every navigation.
const paths: Record<string, ReactNode> = {
  brand: <><rect x="3" y="2" width="12" height="15" rx="1"/><rect x="10" y="9" width="11" height="13" rx="1"/></>,
  start: <><path d="m3 10 9-7 9 7v11H3Z"/><path d="M9 21v-8h6v8"/></>,
  templates: <><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></>,
  secrets: <><rect x="4" y="10" width="16" height="12" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4M12 15v3"/></>,
  data: <><path d="M12 2 3 6v6c0 5 9 10 9 10s9-5 9-10V6Z"/><path d="m8 12 3 3 5-6"/></>,
  runs: <><path d="M8 5h13M8 12h13M8 19h13"/><path d="M3 5h.01M3 12h.01M3 19h.01"/></>,
  approvals: <><circle cx="12" cy="12" r="9"/><path d="m7 12 3 3 7-7"/></>,
  providers: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/></>,
  costs: <path d="M5 21V11m7 10V3m7 18V7"/>,
  insights: <><path d="M3 3v18h18"/><path d="m7 15 4-5 3 3 5-7"/></>,
  triggers: <><circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/></>,
  keys: <><circle cx="8" cy="8" r="5"/><path d="m12 12 9 9m-4-4 3-3m-6 0 3-3"/></>,
  audit: <><path d="M5 3h14v18H5Z M8 7h8M8 12h8M8 17h5"/></>,
  team: <><circle cx="9" cy="7" r="3"/><path d="M3 21v-4a6 6 0 0 1 12 0v4M16 4a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5v2"/></>,
  refresh: <><path d="M20 8a8 8 0 1 0 0 8M20 3v5h-5"/></>,
  add: <path d="M12 3v18M3 12h18"/>,
  signout: <><path d="M10 3H3v18h7M8 12h13m-5-5 5 5-5 5"/></>,
  signin: <><path d="M14 3h7v18h-7M3 12h13m-5-5 5 5-5 5"/></>,
  swap: <><path d="M4 8h15m-4-4 4 4-4 4M20 16H5m4-4-4 4 4 4"/></>,
  menu: <path d="M4 6h16M4 12h16M4 18h16"/>,
  close: <path d="M6 6l12 12M18 6 6 18"/>,
  copy: <><rect x="9" y="9" width="11" height="11" rx="1"/><path d="M5 15H4V4h11v1"/></>,
};

export function Icon({ name }: { name: string }) {
  return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

export function ErrorMessage({ message, retry }: { message?: string; retry?: () => void }) {
  return message ? <div className="error" role="alert"><p>{message}</p>{retry && <button className="secondary" onClick={retry}>Try again</button>}</div> : null;
}
export function Loading({ text = 'Loading your team’s workspace…' }: { text?: string }) { return <p className="loading" role="status">{text}</p>; }
export function Empty({ title, children }: { title: string; children: ReactNode }) {
  return <div className="empty"><h2>{title}</h2><div>{children}</div></div>;
}
// Pages a role cannot open still name themselves, so the title and heading match the navigation.
export function AdminOnly({ title, children }: { title: string; children: ReactNode }) {
  return <><PageHeader title={title}/><div className="panel"><Empty title="Team admin access required">{children}<a className="tap" href="#runs">View workflow runs</a></Empty></div></>;
}
export function Badge({ value, text }: { value: string; text?: string }) {
  return <span className={`badge ${value}`}>{text || label(value)}</span>;
}
export function Metrics({ items, compact }: { items: [string, ReactNode][]; compact?: boolean }) {
  return <dl className={compact ? 'metrics compact' : 'metrics'}>{items.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>;
}
export function DotList({ items }: { items: ReactNode[] }) {
  return <span className="dot-list">{items.filter(Boolean).map((item, index) => <span key={index}>{item}</span>)}</span>;
}
export function NumberInput({ value, onChange, min, max, step, ...props }: Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'> & {
  value: number | ''; onChange: (value: number | '') => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const raw = editing ?? String(value);
  const numeric = Number(raw);
  const valid = raw === '' || (Number.isFinite(numeric) && (min === undefined || numeric >= Number(min))
    && (max === undefined || numeric <= Number(max)) && (step !== 1 || Number.isInteger(numeric)));
  return <input {...props} className="number-input" type="text" inputMode={step === 1 ? 'numeric' : 'decimal'}
    value={editing ?? (value === '' ? '' : value.toLocaleString('en-US', { maximumFractionDigits: 20 }))}
    ref={input => { input?.setCustomValidity(valid ? '' : `Enter ${step === 1 ? 'a whole number' : 'a number'}${min !== undefined ? ` of at least ${min}` : ''}${max !== undefined ? ` and at most ${max}` : ''}.`); }}
    onBlur={() => { if (valid) setEditing(null); }}
    onChange={event => {
      const text = event.target.value.replaceAll(',', '');
      setEditing(text);
      if (text === '' || Number.isFinite(Number(text))) onChange(text === '' ? '' : Number(text));
    }}/>;
}
export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: string; children?: ReactNode }) {
  return <header className="page-heading"><div><h1 tabIndex={-1}>{title}</h1>{subtitle && <p>{subtitle}</p>}</div><div className="actions">{children}</div></header>;
}
// On phones the label hides and the button becomes a 44px icon on the title row.
// While a refresh is in flight the icon turns; the button stays focusable so keyboard focus is kept.
export function Refresh({ onClick, busy = false }: { onClick: () => void; busy?: boolean }) {
  return <button className="secondary refresh" onClick={onClick} aria-label="Refresh" aria-busy={busy || undefined}><Icon name="refresh"/><span>Refresh</span></button>;
}
// A single-line value (webhook URL, new secret) that scrolls instead of wrapping, with a copy button.
// URLs wrap only after a slash, so a path never breaks mid-word.
export function CopyField({ value, label: name, onCopied }: { value: string; label: string; onCopied?: (ok: boolean) => void }) {
  const parts = value.split(/(?<=\/)/);
  return <div className="copy-field"><code aria-label={name}>{parts.map((part, i) => <span key={i}>{part}{i < parts.length - 1 && <wbr/>}</span>)}</code><button type="button" className="secondary" onClick={async () => {
    try { await navigator.clipboard.writeText(value); onCopied?.(true); } catch { onCopied?.(false); }
  }}><Icon name="copy"/>Copy</button></div>;
}

// Unsaved edits survive a stray click: leaving the page, reloading, or switching account asks first.
let unsaved: string | null = null;
export function useUnsavedChanges(dirty: boolean, message = 'Discard your unsaved changes?') {
  useEffect(() => {
    if (!dirty) return;
    unsaved = message;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener('beforeunload', warn);
    return () => { if (unsaved === message) unsaved = null; window.removeEventListener('beforeunload', warn); };
  }, [dirty, message]);
}
// True when nothing is unsaved or the person agrees to discard it.
export function confirmLeave() {
  if (!unsaved || window.confirm(unsaved)) { unsaved = null; return true; }
  return false;
}

export type SortState = { column: string; direction: 'ascending' | 'descending' };
// Sorts rows by a column's value; text compares in the reader's language, missing values sort last.
export function sortRows<T>(rows: T[], sort: SortState, value: (row: T, column: string) => string | number | null | undefined) {
  const sign = sort.direction === 'ascending' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = value(a, sort.column), y = value(b, sort.column);
    if (x == null || y == null) return x == null && y == null ? 0 : x == null ? 1 : -1;
    return sign * (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true }));
  });
}
// A column header that sorts its table; a second press reverses the order.
export function SortHeader({ column, text, sort, onSort, numeric = false, first = 'descending' }: {
  column: string; text: string; sort: SortState; onSort: (sort: SortState) => void; numeric?: boolean; first?: SortState['direction'];
}) {
  const active = sort.column === column;
  const next = active ? sort.direction === 'ascending' ? 'descending' : 'ascending' : first;
  // Phones stack the rows and hide the header row; the plain copy of the name keeps the column named there.
  return <th className={numeric ? 'num sortable' : 'sortable'} aria-sort={active ? sort.direction : undefined}>
    <button type="button" className="sort" onClick={() => onSort({ column, direction: next })}>{text}<span className="sort-mark" aria-hidden="true">{active ? sort.direction === 'ascending' ? '▲' : '▼' : ''}</span></button>
    <span className="sort-text">{text}</span>
  </th>;
}
