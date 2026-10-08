import { Fragment, useEffect, useRef, useState } from 'react';
import { api, date, label, providerName, shortId, type Session } from './api';
import { Empty, ErrorMessage, Loading, PageHeader } from './ui';

type AuditEvent = {
  event: string; ts: number; actor?: string; principal?: { sub?: string; key_id?: string; name?: string };
  action_type?: string; decision?: string; provider?: string;
  project?: string; workflow_run_id?: string;
};
type Entry = { id: string; chain_id: string; sequence: number; event: AuditEvent };
type AuditPage = { enabled: boolean; message?: string; events: Entry[]; next_cursor: string | null };
type Position = { chain_id: string; sequence: number; reason: string };
type Verification = { enabled: boolean; message?: string; ok: boolean | null; checked: number; first_break: Position | null; boundaries: Position[] };
const initialFilters = { from: '', to: '', actor: '', event_type: '', project: '', run_id: '' };
const docs = 'https://github.com/RamazanKara/agentworkflows/blob/main/docs/audit-log.md';
const eventNames: Record<string, string> = {
  inference_request: 'Model call', batch_request: 'Batch model call', agent_action: 'Agent action', workflow_operation: 'Workflow operation',
  team_key: 'API key change', team_settings_changed: 'Settings change',
};
const eventName = (type: string) => eventNames[type] || label(type);
// Plain words for the verifier's reason codes; the code itself stays visible for runbooks.
const reasons: Record<string, string> = {
  event_metadata_mismatch: 'the stored event does not match its metadata', record_hash_mismatch: 'the event no longer matches its hash',
  record_prev_hash_not_genesis: 'the first event does not start the chain', view_hash_mismatch: 'the team record no longer matches its hash',
  sequence_gap_or_reordered: 'an event is missing or out of order', broken_view_link: 'the link to the previous team event is broken',
  broken_record_link: 'the link to the previous event is broken', view_prev_hash_not_genesis: 'the first team event does not start the chain',
  malformed_event: 'an event could not be read', retained_range_start: 'older events were removed by retention',
  time_range: 'earlier events are before the selected time range',
};
const reason = (code: string) => reasons[code] || label(code).toLowerCase();
// The actor filter matches the event's actor or the key's ID; a managed key also carries its name.
const actor = (event: AuditEvent) => {
  const id = event.actor || event.principal?.sub || event.principal?.key_id;
  return { id, name: !event.actor && !event.principal?.sub ? event.principal?.name : undefined };
};
const details = (event: AuditEvent) => [event.action_type && label(event.action_type) !== eventName(event.event) && label(event.action_type),
  event.provider && providerName(event.provider), event.decision === 'denied' && 'Denied'].filter(Boolean).join(' · ');
const fields = [['from', 'From', 'datetime-local', ''], ['to', 'To', 'datetime-local', ''], ['event_type', 'Event type', 'text', ''],
  ['actor', 'Actor', 'text', 'Subject or key ID'], ['project', 'Project', 'text', 'Any project'], ['run_id', 'Run ID', 'text', 'Full run ID']] as const;
const snapshot = () => new URLSearchParams({ to: String(Date.now() / 1000) }).toString();

export function AuditLog({ session }: { session: Session }) {
  return session.team.role === 'admin' ? <AuditViewer session={session}/>
    : <><PageHeader title="Audit log"/><div className="panel"><Empty title="Team admin access required"><p>A team admin role is required to read the audit log.</p><a className="tap" href="#runs">View workflow runs</a></Empty></div></>;
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
  const [showFilters, setShowFilters] = useState(false);
  const [applied, setApplied] = useState(0);
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
    setApplied(Object.values(filters).filter(Boolean).length);
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
  const clear = () => { setFilters(initialFilters); setApplied(0); setQuery(snapshot()); setCursor(''); setVerification(undefined); };
  return <><PageHeader title="Audit log" subtitle="Who did what in your team, with a tamper check you can run any time.">
    <button className="secondary" disabled={!page?.enabled || Boolean(busy)} onClick={() => void verify()}>{busy === 'verify' ? 'Verifying…' : 'Verify chain'}</button>
    <button className="secondary" disabled={!page?.enabled || Boolean(busy)} onClick={() => void download()}>{busy === 'export' ? 'Exporting…' : 'Export JSON Lines'}</button>
  </PageHeader>
    <button className="secondary filter-toggle" aria-expanded={showFilters} aria-controls="audit-filters" onClick={() => setShowFilters(value => !value)}>
      {showFilters ? 'Hide filters' : applied ? `Filters · ${applied} applied` : 'Filters'}</button>
    <form id="audit-filters" className={showFilters ? 'panel filters audit-filters' : 'panel filters audit-filters collapsed'} onSubmit={event => { event.preventDefault(); apply(); }}>
      {fields.map(([name, text, type, hint]) => <div className="field" key={name}><label htmlFor={`audit-${name}`}>{text}</label>
        {name === 'event_type' ? <select id="audit-event_type" value={filters.event_type} disabled={Boolean(busy)} onChange={event => setFilters(previous => ({ ...previous, event_type: event.target.value }))}>
          <option value="">All events</option>{Object.entries(eventNames).map(([value, name]) => <option key={value} value={value}>{name}</option>)}</select>
          : <input id={`audit-${name}`} type={type} placeholder={hint} value={filters[name]} disabled={Boolean(busy)} onChange={event => setFilters(previous => ({ ...previous, [name]: event.target.value }))}/>}</div>)}
      <div className="filter-actions">{applied > 0 && <button type="button" className="secondary" disabled={Boolean(busy)} onClick={clear}>Clear filters</button>}<button type="submit" disabled={Boolean(busy)}>Apply filters</button></div>
    </form>
    <ErrorMessage message={error}/>
    {!page && !error && <Loading/>}
    {page && !page.enabled && <section className="panel"><h2>Audit view is off</h2><p>{page.message}</p><a className="tap" href={docs}>How to turn on the audit view</a></section>}
    {page?.enabled && <>
      {verification && <section className={['panel', 'verify-result', verification.ok ? 'ok' : verification.enabled && 'broken'].filter(Boolean).join(' ')} role="status">
        {!verification.enabled ? <p>{verification.message}</p> : <>
          <h2>{verification.ok ? 'Chain verified' : 'Chain break found'}</h2>
          <p>{verification.checked} {verification.checked === 1 ? 'event' : 'events'} checked.{verification.ok && ' None were changed, removed or reordered.'}</p>
          {verification.first_break && <p>First break at sequence {verification.first_break.sequence} in {verification.first_break.chain_id}: {reason(verification.first_break.reason)} ({verification.first_break.reason}).</p>}
          {verification.boundaries.map(boundary => <p className="muted" key={`${boundary.chain_id}:${boundary.sequence}`}>Range boundary at sequence {boundary.sequence} in {boundary.chain_id}: {reason(boundary.reason)}. Earlier events are outside this check.</p>)}
        </>}
      </section>}
      {page.events.length ? <section className="panel"><div className="table-scroll" tabIndex={0} role="region" aria-label="Audit events table"><table className="stack audit-table">
        <thead><tr><th>Event</th><th>Time</th><th>Actor</th><th>Project</th><th>Run</th><th><span className="visually-hidden">Details</span></th></tr></thead>
        <tbody>{page.events.map(entry => { const who = actor(entry.event); const detail = details(entry.event); return <Fragment key={entry.id}>
          <tr className={expanded === entry.id ? 'open' : undefined}><td><strong>{eventName(entry.event.event)}</strong>{detail && <small>{detail}</small>}</td>
            <td data-label="Time">{date(entry.event.ts)}</td>
            <td data-label="Actor">{who.name || who.id || '—'}{who.name && who.id && <small title={who.id}>Key {shortId(who.id)}</small>}</td>
            <td data-label="Project">{entry.event.project || '—'}</td>
            <td data-label="Run">{entry.event.workflow_run_id ? <a href={`#run/${encodeURIComponent(entry.event.workflow_run_id)}`}><code>{shortId(entry.event.workflow_run_id)}</code></a> : '—'}</td>
            <td className="row-action"><button className="link" aria-expanded={expanded === entry.id} aria-controls={`event-${entry.id}`} onClick={() => setExpanded(expanded === entry.id ? undefined : entry.id)}>{expanded === entry.id ? 'Hide JSON' : 'Show JSON'}</button></td></tr>
          {expanded === entry.id && <tr className="json-row"><td colSpan={6}><div className="code-scroll"><pre className="json" id={`event-${entry.id}`}>{JSON.stringify(entry.event, null, 2)}</pre></div></td></tr>}
        </Fragment>; })}</tbody>
      </table></div>{(cursor || page.next_cursor) && <div className="table-footer">
        {cursor && <button className="secondary" disabled={Boolean(busy)} onClick={() => setCursor('')}>Newest events</button>}
        {page.next_cursor && <button className="secondary" disabled={Boolean(busy)} onClick={() => setCursor(page.next_cursor!)}>Older events</button>}
      </div>}</section>
        : <div className="panel"><Empty title={applied ? 'No matching events' : 'No audit events yet'}><p>{applied ? 'No retained events match these filters.' : 'Model calls, approvals, key and settings changes appear here as your team works.'}</p>{applied > 0 && <button className="link" onClick={clear}>Clear filters</button>}</Empty></div>}
      <p className="muted">Times use your local time zone. Verify chain checks every team event in the time range, including events the other filters hide.</p>
    </>}
  </>;
}
