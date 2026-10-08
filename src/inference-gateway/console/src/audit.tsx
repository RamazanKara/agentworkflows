import { Fragment, useEffect, useRef, useState } from 'react';
import { api, date, shortId, type Session } from './api';
import { Empty, ErrorMessage, Loading, PageHeader } from './ui';

type AuditEvent = {
  event: string; ts: number; actor?: string; principal?: { sub?: string; key_id?: string };
  project?: string; workflow_run_id?: string;
};
type Entry = { id: string; chain_id: string; sequence: number; event: AuditEvent };
type AuditPage = { enabled: boolean; message?: string; events: Entry[]; next_cursor: string | null };
type Position = { chain_id: string; sequence: number; reason: string };
type Verification = { enabled: boolean; message?: string; ok: boolean | null; checked: number; first_break: Position | null; boundaries: Position[] };
const initialFilters = { from: '', to: '', actor: '', event_type: '', project: '', run_id: '' };
const actor = (event: AuditEvent) => event.actor || event.principal?.sub || event.principal?.key_id || '—';
const snapshot = () => new URLSearchParams({ to: String(Date.now() / 1000) }).toString();

export function AuditLog({ session }: { session: Session }) {
  return session.team.role === 'admin' ? <AuditViewer session={session}/>
    : <><PageHeader title="Audit log"/><p>A team admin role is required to read the audit log.</p></>;
}

function AuditViewer({ session }: { session: Session }) {
  const [filters, setFilters] = useState(initialFilters);
  const [query, setQuery] = useState(snapshot);
  const [cursor, setCursor] = useState('');
  const [page, setPage] = useState<AuditPage>();
  const [expanded, setExpanded] = useState<string>();
  const [verification, setVerification] = useState<Verification>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const action = useRef<AbortController | null>(null);
  useEffect(() => () => action.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    setPage(undefined); setError(''); setExpanded(undefined);
    api<AuditPage>(session.csrfToken, `/v1/team/audit?${query}&limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`, { signal: controller.signal }).then(
      data => { if (!controller.signal.aborted) setPage(data); },
      failure => { if (!controller.signal.aborted) setError(failure.message); },
    );
    return () => controller.abort();
  }, [session.csrfToken, query, cursor]);
  const apply = () => {
    const params = new URLSearchParams();
    for (const [name, value] of Object.entries(filters)) {
      if (value) params.set(name, name === 'from' || name === 'to' ? String(new Date(value).getTime() / 1000) : value);
    }
    if (!params.has('to')) params.set('to', String(Date.now() / 1000));
    setQuery(params.toString()); setCursor(''); setVerification(undefined);
  };
  const verify = async () => {
    setBusy('verify'); setError(''); setVerification(undefined);
    const controller = new AbortController();
    action.current = controller;
    const params = new URLSearchParams(query);
    for (const name of ['actor', 'event_type', 'project', 'run_id']) params.delete(name);
    try {
      const result = await api<Verification>(session.csrfToken, `/v1/team/audit/verify?${params}`, { signal: controller.signal });
      if (!controller.signal.aborted) setVerification(result);
    } catch (value) { if (!controller.signal.aborted) setError((value as Error).message); }
    finally { setBusy(''); }
  };
  const download = async () => {
    setBusy('export'); setError('');
    const controller = new AbortController();
    action.current = controller;
    try {
      const lines: string[] = [];
      let next: string | null = null;
      do {
        const result: AuditPage = await api<AuditPage>(session.csrfToken, `/v1/team/audit?${query}&limit=200${next ? `&cursor=${encodeURIComponent(next)}` : ''}`, { signal: controller.signal });
        if (!result.enabled) throw new Error(result.message);
        lines.push(...result.events.map(entry => JSON.stringify(entry.event) + '\n'));
        next = result.next_cursor;
      } while (next);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(new Blob(lines, { type: 'application/x-ndjson' }));
      const link = document.createElement('a');
      link.href = url; link.download = 'audit-log.jsonl'; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (value) { if (!controller.signal.aborted) setError((value as Error).message); }
    finally { setBusy(''); }
  };
  return <><PageHeader title="Audit log" subtitle="Read your team’s receipts and check the retained audit trail.">
    <button className="secondary" disabled={!page?.enabled || Boolean(busy)} onClick={() => void verify()}>{busy === 'verify' ? 'Verifying…' : 'Verify chain'}</button>
    <button className="secondary" disabled={!page?.enabled || Boolean(busy)} onClick={() => void download()}>{busy === 'export' ? 'Exporting…' : 'Export JSON Lines'}</button>
  </PageHeader>
    <form className="panel filters" onSubmit={event => { event.preventDefault(); apply(); }}>
      {([['from', 'From', 'datetime-local'], ['to', 'To', 'datetime-local'], ['actor', 'Actor', 'text'], ['event_type', 'Event type', 'text'], ['project', 'Project', 'text'], ['run_id', 'Run ID', 'text']] as const).map(([name, label, type]) =>
        <div className="field" key={name}><label htmlFor={`audit-${name}`}>{label}</label><input id={`audit-${name}`} type={type} value={filters[name]} disabled={Boolean(busy)} onChange={event => setFilters(previous => ({ ...previous, [name]: event.target.value }))}/></div>)}
      <button type="submit" disabled={Boolean(busy)}>Apply filters</button>
    </form>
    <ErrorMessage message={error}/>
    {!page && !error && <Loading/>}
    {page && !page.enabled && <section className="panel"><h2>Audit view is off</h2><p>{page.message}</p></section>}
    {page?.enabled && <>
      <p>Verification checks all team events in the selected time range, including events hidden by the other filters. Times use your local timezone.</p>
      {verification && <section className="panel" role="status">
        {!verification.enabled ? <p>{verification.message}</p> : <>
          <h2>{verification.ok ? 'Chain verified' : 'Chain break found'}</h2><p>{verification.checked} events checked.</p>
          {verification.first_break && <p>First break at sequence {verification.first_break.sequence} in {verification.first_break.chain_id}: {verification.first_break.reason}.</p>}
          {verification.boundaries.map(boundary => <p key={`${boundary.chain_id}:${boundary.sequence}`}>Range boundary at sequence {boundary.sequence} in {boundary.chain_id}: {boundary.reason}. Earlier events are outside this check.</p>)}
        </>}
      </section>}
      {page.events.length ? <section className="panel"><div className="table-scroll"><table>
        <thead><tr><th>Time</th><th>Actor</th><th>Event</th><th>Project</th><th>Run</th><th>Details</th></tr></thead>
        <tbody>{page.events.map(entry => <Fragment key={entry.id}>
          <tr><td>{date(entry.event.ts)}</td><td>{actor(entry.event)}</td><td>{entry.event.event}</td><td>{entry.event.project || '—'}</td>
            <td>{entry.event.workflow_run_id ? <a href={`#run/${encodeURIComponent(entry.event.workflow_run_id)}`}>{shortId(entry.event.workflow_run_id)}</a> : '—'}</td>
            <td><button className="secondary" aria-expanded={expanded === entry.id} aria-controls={`event-${entry.id}`} onClick={() => setExpanded(expanded === entry.id ? undefined : entry.id)}>{expanded === entry.id ? 'Hide JSON' : 'Show JSON'}</button></td></tr>
          {expanded === entry.id && <tr><td colSpan={6}><pre id={`event-${entry.id}`}>{JSON.stringify(entry.event, null, 2)}</pre></td></tr>}
        </Fragment>)}</tbody>
      </table></div><div className="table-footer">
        {cursor && <button className="secondary" disabled={Boolean(busy)} onClick={() => setCursor('')}>Newest events</button>}
        {page.next_cursor && <button className="secondary" disabled={Boolean(busy)} onClick={() => setCursor(page.next_cursor!)}>Older events</button>}
      </div></section> : <Empty title="No audit events">No retained events match these filters.</Empty>}
    </>}
  </>;
}
