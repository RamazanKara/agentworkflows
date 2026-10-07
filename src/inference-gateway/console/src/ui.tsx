import type { ReactNode } from 'react';
import { label } from './api';

export function Icon({ name }: { name: string }) {
  const paths: Record<string, ReactNode> = {
    brand: <><rect x="3" y="2" width="12" height="15" rx="1"/><rect x="10" y="9" width="11" height="13" rx="1"/></>,
    start: <><path d="m3 10 9-7 9 7v11H3Z"/><path d="M9 21v-8h6v8"/></>,
    runs: <><path d="M8 5h13M8 12h13M8 19h13"/><path d="M3 5h.01M3 12h.01M3 19h.01"/></>,
    approvals: <><circle cx="12" cy="12" r="9"/><path d="m7 12 3 3 7-7"/></>,
    providers: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/></>,
    costs: <path d="M5 21V11m7 10V3m7 18V7"/>,
    refresh: <><path d="M20 8a8 8 0 1 0 0 8M20 3v5h-5"/></>,
    add: <path d="M12 3v18M3 12h18"/>,
    signout: <><path d="M10 3H3v18h7M8 12h13m-5-5 5 5-5 5"/></>,
  };
  return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

export function ErrorMessage({ message, retry }: { message?: string; retry?: () => void }) {
  return message ? <div className="error" role="alert"><p>{message}</p>{retry && <button className="secondary" onClick={retry}>Try again</button>}</div> : null;
}
export function Loading() { return <p className="loading" role="status">Loading your team’s workspace…</p>; }
export function Empty({ title, children }: { title: string; children: ReactNode }) {
  return <div className="empty"><h2>{title}</h2><div>{children}</div></div>;
}
export function Badge({ value }: { value: string }) {
  return <span className={`badge ${value}`}>{label(value)}</span>;
}
export function Metrics({ items }: { items: [string, ReactNode][] }) {
  return <dl className="metrics">{items.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>;
}
export function PageHeader({ title, subtitle, children }: { title: string; subtitle: string; children?: ReactNode }) {
  return <header className="page-heading"><div><h1 tabIndex={-1}>{title}</h1><p>{subtitle}</p></div><div className="actions">{children}</div></header>;
}
export function Refresh({ onClick }: { onClick: () => void }) {
  return <button className="secondary" onClick={onClick}><Icon name="refresh"/>Refresh</button>;
}
