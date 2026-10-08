import { Fragment, useEffect, useRef, useState, type ReactNode } from 'react';
import { api, date, label, money, providerName, shortId, workflowName, type Session } from './api';
import { Badge, Empty, ErrorMessage, Icon, Loading, PageHeader } from './ui';

type SettingSnapshot = { value?: unknown } | unknown;
type AuditEvent = {
  event: string; ts: number; actor?: string; principal?: { sub?: string; key_id?: string; name?: string };
  action_type?: string; decision?: string; provider?: string; project?: string; workflow_run_id?: string;
  before?: Record<string, SettingSnapshot>; after?: Record<string, SettingSnapshot>; key?: { name?: string };
};
type Entry = { id: string; chain_id: string; sequence: number; event: AuditEvent };
type AuditPage = { enabled: boolean; message?: string; events: Entry[]; next_cursor: string | null };
type Position = { chain_id: string; sequence: number; reason: string };
type Verification = { enabled: boolean; message?: string; ok: boolean | null; checked: number; first_break: Position | null; boundaries: Position[] };
const initialFilters = { from: '', to: '', actor: '', event_type: '', project: '', run_id: '' };
const docs = 'https://github.com/RamazanKara/agentworkflows/blob/main/docs/audit-log.md';
const enableSettings = 'SANDBOX_BUDGET_BACKEND=redis\nSANDBOX_BUDGET_REDIS_URL=redis://<host>:6379/0\nAUDIT_LOG_ENABLED=true';
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
  malformed_event: 'an event could not be read',
};
const reason = (code: string) => reasons[code] || label(code).toLowerCase();
// The actor filter matches the event's actor or the principal's subject or key ID; a managed key also carries its name.
const actor = (event: AuditEvent) => {
  const id = event.actor || event.principal?.sub || event.principal?.key_id;
  const name = event.principal?.name;
  return { id, name: name && name !== id ? name : undefined };
};
// "Research · approval threshold: $0.00 → $0.50"
const settingName = (field: string) => {
  const [scope, middle, leaf] = field.split('.');
  return scope === 'cost_limit_usd' ? 'Team monthly budget' : scope === 'project_budgets' ? `Project ${middle} budget`
    : scope === 'model_routes' ? `Model for ${middle}` : scope === 'workflows' ? `${workflowName(middle)} · ${label(leaf.replace(/_usd$/, '')).toLowerCase()}` : field;
};
const settingValue = (field: string, snapshot: SettingSnapshot) => {
  const value = snapshot && typeof snapshot === 'object' && 'value' in snapshot ? snapshot.value : snapshot;
  return value === null || value === undefined ? 'no limit' : typeof value === 'boolean' ? value ? 'on' : 'off'
    : Array.isArray(value) ? value.map(item => providerName(String(item))).join(', ')
    : typeof value === 'number' && /(_usd|^project_budgets\.)/.test(field) ? money(value) : String(value);
};
const details = (event: AuditEvent): ReactNode => {
  if (event.event === 'team_settings_changed') {
    const fields = Object.keys(event.after || {});
    return fields.length === 1 ? <>{settingName(fields[0])}: <span className="nowrap">{settingValue(fields[0], event.before?.[fields[0]])} → {settingValue(fields[0], event.after?.[fields[0]])}</span></>
      : `${fields.length} settings changed`;
  }
  if (event.event === 'team_key') return [event.action_type && label(event.action_type), event.key?.name].filter(Boolean).join(' · ');
  return [event.action_type && label(event.action_type) !== eventName(event.event) && label(event.action_type), event.provider && providerName(event.provider)].filter(Boolean).join(' · ');
};
const fields = [['from', 'From', 'datetime-local', ''], ['to', 'To', 'datetime-local', ''], ['event_type', 'Event type', 'text', ''],
  ['actor', 'Actor', 'text', 'Key ID, worker or user ID'], ['project', 'Project', 'text', 'Any project'], ['run_id', 'Run ID', 'text', 'Full run ID']] as const;
function Json({ id, value }: { id: string; value: unknown }) {
  const lines = JSON.stringify(value, null, 2).split('\n');
  return <pre className="json event-json" id={id}>{lines.map((line, index) => {
    const indent = line.length - line.trimStart().length + 2;
    return <span key={index} style={{ paddingLeft: `${indent}ch`, textIndent: `-${indent}ch` }}>{line}{index < lines.length - 1 && '\n'}</span>;
  })}</pre>;
}
const snapshot = () => new URLSearchParams({ to: String(Date.now() / 1000) }).toString();
const copyText = async (text: string, done: string, report: (message: string) => void) => {
  try { await navigator.clipboard.writeText(text); report(done); } catch { report('Copy failed. Select the text and copy it manually.'); }
};

export function AuditLog({ session }: { session: Session }) {
  return session.team.role === 'admin' ? <AuditViewer session={session}/>
    : <><PageHeader title="Audit log"/><div className="panel"><Empty title="Team admin access required"><p>Ask a team admin for the Admin role, or for an export of the events you need.</p><a className="tap" href="#runs">View workflow runs</a></Empty></div></>;
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
  const [copied, setCopied] = useState('');
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
      if (controller.signal.aborted) return;
      setVerification(result);
      // Open the broken event when it is on this page, so the banner and the row tell one story.
      const broken = result.first_break && page?.events.find(entry => entry.chain_id === result.first_break!.chain_id && entry.sequence === result.first_break!.sequence);
      if (broken) setExpanded(broken.id);
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
  const off = page && !page.enabled;
  const nothing = page?.enabled && !page.events.length && !applied && !cursor;
  const broken = verification?.first_break;
  const isBroken = (entry: Entry) => Boolean(broken && entry.chain_id === broken.chain_id && entry.sequence === broken.sequence);
  const range = new URLSearchParams(query);
  const checkedAt = (at: number) => {
    const from = Number(range.get('from')) || 0;
    const to = Number(range.get('to'));
    return from ? `Covers ${date(from)} to ${date(to)} · checked ${date(at)}` : at - to > 60 ? `Covers events up to ${date(to)} · checked ${date(at)}` : `Checked ${date(at)}`;
  };
  const brokenIndex = page?.events.findIndex(isBroken) ?? -1;
  const showBroken = () => {
    const row = page?.events.find(isBroken);
    if (row) { setExpanded(row.id); document.getElementById(`row-${row.id}`)?.scrollIntoView({ block: 'center' }); }
  };
  return <><PageHeader title="Audit log" subtitle={off ? 'Who did what in your team, once audit storage is on.' : 'Who did what in your team, newest first, with a tamper check you can run any time.'}>
    {!off && <>
      <button className="secondary" disabled={!page || nothing || Boolean(busy)} onClick={() => void verify()}>{busy === 'verify' ? 'Verifying…' : 'Verify chain'}</button>
      <button className="secondary" disabled={!page || nothing || Boolean(busy)} onClick={() => void download()}>{busy === 'export' ? 'Exporting…' : 'Export JSON Lines'}</button>
    </>}
  </PageHeader>
    {!off && !nothing && <>
      <button className="secondary filter-toggle" aria-expanded={showFilters} aria-controls="audit-filters" onClick={() => setShowFilters(value => !value)}>
        {showFilters ? 'Hide filters' : applied ? `Filters · ${applied} applied` : 'Filters'}</button>
      <form id="audit-filters" className={showFilters ? 'panel filters audit-filters' : 'panel filters audit-filters collapsed'} onSubmit={event => { event.preventDefault(); apply(); }}>
        {fields.map(([name, text, type, hint]) => <div className="field" key={name}><label htmlFor={`audit-${name}`}>{text}</label>
          {name === 'event_type' ? <select id="audit-event_type" value={filters.event_type} disabled={Boolean(busy)} onChange={event => setFilters(previous => ({ ...previous, event_type: event.target.value }))}>
            <option value="">All events</option>{Object.entries(eventNames).map(([value, name]) => <option key={value} value={value}>{name}</option>)}</select>
            : <input id={`audit-${name}`} type={type} placeholder={hint} value={filters[name]} disabled={Boolean(busy)} onChange={event => setFilters(previous => ({ ...previous, [name]: event.target.value }))}/>}</div>)}
        <div className="filter-actions">{applied > 0 && <button type="button" className="secondary" disabled={Boolean(busy)} onClick={clear}>Clear filters</button>}<button type="submit" disabled={Boolean(busy)}>Apply filters</button></div>
      </form>
    </>}
    <ErrorMessage message={error}/>
    {!page && !error && <Loading/>}
    {off && <section className="setup panel"><h2>Audit log is turned off</h2>
      <p className="muted">Ask your gateway operator to add these settings and turn on Redis persistence, then refresh this page.</p>
      <pre className="json wrap">{enableSettings}</pre>
      <div className="actions"><button className="secondary" onClick={() => void copyText(enableSettings, 'Settings copied.', setCopied)}><Icon name="copy"/>Copy settings</button><span role="status">{copied}</span></div>
      <p className="setup-links"><a href={docs}>How to turn on the audit log</a></p>
    </section>}
    {page?.enabled && <>
      {verification && <section className={['panel', 'verify-result', verification.ok ? 'ok' : verification.enabled && 'broken'].filter(Boolean).join(' ')} role="status">
        {!verification.enabled ? <p>{verification.message}</p> : <>
          <h2>{verification.ok ? 'Chain verified' : 'Chain break found'}</h2>
          {broken ? <>
            <p>{verification.checked - 1} {verification.checked === 2 ? 'event' : 'events'} passed before the break.</p>
            <p>First break at sequence {broken.sequence} in {broken.chain_id}: {reason(broken.reason)} ({broken.reason}).
              {brokenIndex > 0 ? ` This event and the ${brokenIndex} newer ${brokenIndex === 1 ? 'event' : 'events'} above it can’t be trusted.` : brokenIndex === 0 ? ' This event can’t be trusted.' : ' This event and every newer one can’t be trusted.'}</p>
            <p>Export this range now and tell your gateway operator.</p>
            {page.events.some(isBroken) && <button className="link" onClick={showBroken}>Show the event</button>}
          </> : <p>{verification.checked} {verification.checked === 1 ? 'event' : 'events'} checked. None were changed, removed or reordered.</p>}
          {verification.boundaries.map(boundary => <p className="muted" key={`${boundary.chain_id}:${boundary.sequence}`}>This check starts at event {boundary.sequence} in chain {boundary.chain_id}. {boundary.reason === 'time_range' ? 'Earlier events are outside the selected time range.' : 'Older events were removed by retention, so they aren’t included.'}</p>)}
          <p className="muted">{checkedAt(Date.now() / 1000)}</p>
        </>}
      </section>}
      {page.events.length ? <section className="panel"><div className="table-scroll" tabIndex={0} role="region" aria-label="Audit events table"><table className="stack audit-table">
        <thead><tr><th>Event</th><th>Time</th><th>Actor</th><th>Project</th><th>Run</th><th><span className="visually-hidden">Details</span></th></tr></thead>
        <tbody>{page.events.map(entry => { const who = actor(entry.event); const detail = details(entry.event); const open = expanded === entry.id; return <Fragment key={entry.id}>
          <tr id={`row-${entry.id}`} className={[open && 'open', isBroken(entry) && 'broken'].filter(Boolean).join(' ') || undefined}>
            <td><span className="event-name"><strong>{eventName(entry.event.event)}</strong>{entry.event.decision === 'denied' && <Badge value="failed" text="Denied"/>}{isBroken(entry) && <Badge value="failed" text="Chain break"/>}{brokenIndex > 0 && page.events.indexOf(entry) < brokenIndex && <Badge value="unverified" text="Not verified"/>}</span>{detail && <small>{detail}</small>}</td>
            <td data-label="Time">{date(entry.event.ts)}</td>
            <td data-label="Actor">{who.name || who.id || '—'}{who.name && who.id && <small title={who.id}>Key {shortId(who.id)}</small>}</td>
            <td data-label="Project" className={entry.event.project ? undefined : 'empty-cell'}>{entry.event.project || '—'}</td>
            <td data-label="Run" className={entry.event.workflow_run_id ? undefined : 'empty-cell'}>{entry.event.workflow_run_id ? <a className="run-link" href={`#run/${encodeURIComponent(entry.event.workflow_run_id)}`}><code>{shortId(entry.event.workflow_run_id)}</code></a> : '—'}</td>
            <td className="row-action"><button className="link" aria-expanded={open} aria-controls={`event-${entry.id}`} onClick={() => setExpanded(open ? undefined : entry.id)}>{open ? 'Hide JSON' : 'Show JSON'}</button></td></tr>
          {open && <tr className="json-row"><td colSpan={6}>
            <Json id={`event-${entry.id}`} value={entry.event}/>
            <div className="actions"><button className="secondary" onClick={() => void copyText(JSON.stringify(entry.event, null, 2), 'Event copied.', setCopied)}><Icon name="copy"/>Copy JSON</button><span role="status">{copied}</span></div>
          </td></tr>}
        </Fragment>; })}</tbody>
      </table></div><div className="table-footer">
        <span className="muted">Showing {page.events.length} {page.events.length === 1 ? 'event' : 'events'}{cursor ? ' from an older page' : ''}</span>
        {cursor && <button className="secondary" disabled={Boolean(busy)} onClick={() => setCursor('')}>Newest events</button>}
        {page.next_cursor && <button className="secondary" disabled={Boolean(busy)} onClick={() => setCursor(page.next_cursor!)}>Older events</button>}
      </div></section>
        : <div className="panel"><Empty title={nothing ? 'No audit events yet' : 'No matching events'}><p>{nothing ? 'Model calls, approvals, and key and settings changes appear here as your team works.' : 'No retained events match these filters.'}</p>
          {nothing ? <a className="tap" href="#new">Run a workflow</a> : applied > 0 && <button className="link" onClick={clear}>Clear filters</button>}</Empty></div>}
      {!nothing && <p className="muted">Times use your local time zone. Verify chain checks every team event in the time range, including events the other filters hide.</p>}
    </>}
  </>;
}
