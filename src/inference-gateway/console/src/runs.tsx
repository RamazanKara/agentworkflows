import { createElement, useEffect, useRef, useState, type ReactNode } from 'react';
import { api, date, duration, label, missingKeys, money, noProviderKeys, number, escalationReach, policyCache, providerList, providerName, reviewable, shortId, status, useData, usePolling, webUrl, workflowName, type InputProperty, type InputSchema, type Policy, type Run, type RunPage, type Session, type Step } from './api';
import { RunSummaryPanel } from './insights';
import { Badge, DotList, Empty, ErrorMessage, Icon, Loading, Metrics, NumberInput, PageHeader, Refresh } from './ui';

const canBuild = (session: Session) => ['admin', 'builder'].includes(session.team.role);
const canApprove = (session: Session) => ['admin', 'approver'].includes(session.team.role);
const statuses = ['awaiting_approval', 'running', 'completed', 'failed', 'canceled', 'timed_out', 'terminated'];

export function Runs({ session, initial = '' }: { session: Session; initial?: string }) {
  const query = new URLSearchParams(initial);
  const home = session.team.projects[0] || '';
  const [project, setProject] = useState(session.team.projects.includes(query.get('project') || '') ? query.get('project')! : home);
  const [filter, setFilter] = useState(statuses.includes(query.get('status') || '') ? query.get('status')! : '');
  const [workflow, setWorkflow] = useState(query.get('workflow') || '');
  const [trigger, setTrigger] = useState(query.get('trigger') || '');
  const [revision, setRevision] = useState(0);
  const [rows, setRows] = useState<Run[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(true);
  const [filters, setFilters] = useState(false);
  const current = useRef<AbortController | null>(null);
  const policies = useData<{ workflows: Record<string, Policy> }>(session.csrfToken, '/v1/workflow-policies', 0, policyCache);
  // The address keeps the filters, so reloading, sharing the link or coming back from a run shows the same list.
  useEffect(() => {
    const params = new URLSearchParams();
    if (project !== home) params.set('project', project);
    if (filter) params.set('status', filter);
    if (workflow) params.set('workflow', workflow);
    if (trigger) params.set('trigger', trigger);
    const text = params.toString();
    const hash = `#runs${text ? `?${text}` : ''}`;
    if (location.hash !== hash) history.replaceState(history.state, '', hash);
  }, [home, project, filter, workflow, trigger]);
  async function load(after: string | null, reset = false) {
    current.current?.abort();
    const controller = new AbortController(); current.current = controller;
    setBusy(true); setError('');
    if (reset) setRows([]);
    try {
      const query = new URLSearchParams({ project, limit: '20' });
      if (after) query.set('cursor', after);
      if (filter) query.set('status', filter);
      if (workflow) query.set('workflow', workflow);
      if (trigger) query.set('trigger', trigger);
      const result = await api<RunPage>(session.csrfToken, `/v1/workflow-runs?${query}`, { signal: controller.signal });
      if (!controller.signal.aborted) {
        // Pages can overlap when new runs start between requests; each run is listed once.
        setRows(values => {
          const seen = new Set<string>();
          return [...(reset ? [] : values), ...result.runs].filter(r => !seen.has(r.run_id) && Boolean(seen.add(r.run_id)));
        });
        setCursor(result.next_cursor);
      }
    } catch (error) { if (!controller.signal.aborted) setError((error as Error).message); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  useEffect(() => { void load(null, true); return () => current.current?.abort(); }, [project, filter, workflow, trigger, revision]);
  return <>
    <PageHeader title="Workflow runs" subtitle="Runs in your projects, newest first."><Refresh onClick={() => setRevision(v => v + 1)}/>{canBuild(session) && <a className="button" href="#new">Run workflow</a>}</PageHeader>
    {trigger && <p className="callout">Started by trigger <strong>{trigger}</strong>. History includes retained launches. <a href="#runs">All workflow runs</a> · <a href="#triggers">Back to triggers</a></p>}
    <Metrics compact items={[
      ['Runs shown', number(rows.length)], ['Awaiting approval', number(rows.filter(r => status(r) === 'awaiting_approval').length)],
      ['Cost of these runs', money(rows.reduce((sum, run) => sum + run.budget.cost_usd, 0))],
    ]}/>
    <button className="secondary filter-toggle" aria-expanded={filters} aria-controls="run-filters" onClick={() => setFilters(value => !value)}>
      {filters ? 'Hide filters' : <DotList items={['Filters', project, filter && label(filter), workflow && workflowName(workflow)]}/>}</button>
    <div id="run-filters" className={filters ? 'filters panel' : 'filters panel collapsed'}>
      <label>Project<select value={project} onChange={e => { setProject(e.target.value); setTrigger(''); }}>{session.team.projects.map(p => <option key={p}>{p}</option>)}</select></label>
      <label>Status<select value={filter} onChange={e => setFilter(e.target.value)}><option value="">All statuses</option>{statuses.map(s => <option key={s} value={s}>{label(s)}</option>)}</select></label>
      <label>Workflow<select value={workflow} onChange={e => { setWorkflow(e.target.value); setTrigger(''); }}><option value="">All workflows</option>{Object.keys(policies.data?.workflows || {}).map(w => <option key={w} value={w}>{workflowName(w)}</option>)}</select></label>
    </div>
    <ErrorMessage message={error || policies.error} retry={() => void load(cursor)}/>
    <div className="panel">
      {rows.length ? <div className="table-scroll" tabIndex={0} role="region" aria-label="Workflow runs table"><table className="stack runs-stack">
        <thead><tr>{['Workflow', 'Run', 'Status', 'Started', 'Tokens', 'Cost'].map(h => <th key={h} className={['Tokens', 'Cost'].includes(h) ? 'num' : undefined}>{h}</th>)}</tr></thead>
        <tbody>{rows.map(run => <tr key={run.run_id}><td><a href={`#run/${run.run_id}`}>{workflowName(run.workflow)}</a><small>{run.project}</small></td><td data-label="Run"><code title={run.run_id}>{shortId(run.run_id)}</code></td><td data-label="Status"><Badge value={status(run)}/><EscalationMark run={run}/></td><td data-label="Started">{date(run.created_at)}</td><td data-label="Tokens">{number(run.budget.tokens)}</td><td data-label="Cost">{money(run.budget.cost_usd)}</td></tr>)}</tbody>
      </table></div> : !busy && !error && <Empty title={filter || workflow ? 'No matching runs' : 'Your first workflow starts here'}><p>{cursor !== null ? 'Load more to continue searching older runs.' : filter || workflow ? 'No run in this project matches these filters.' : 'Runs appear here with their status, cost and receipts.'}</p>{canBuild(session) && !filter && !workflow && <a className="tap" href="#new">Run a workflow</a>}</Empty>}
      {busy && <Loading text={rows.length ? 'Loading older runs…' : 'Loading workflow runs…'}/>}
      {cursor !== null && !busy && <div className="table-footer"><button className="secondary" onClick={() => void load(cursor)}>Load more</button></div>}
    </div>
    <p className="muted">Costs are estimates from configured prices. Open a run to see each step and its receipt.</p>
  </>;
}

export function StartRun({ session, initial = '' }: { session: Session; initial?: string }) {
  const policies = useData<{ workflows: Record<string, Policy> }>(session.csrfToken, '/v1/workflow-policies', 0, policyCache);
  const [workflow, setWorkflow] = useState(initial);
  const [project, setProject] = useState(session.team.projects[0] || '');
  const [input, setInput] = useState('{}');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState(false);
  const request = useRef<{ fingerprint: string; id: string } | null>(null);
  const names = Object.keys(policies.data?.workflows || {});
  const selected = names.includes(workflow) ? workflow : names[0];
  const policy = policies.data?.workflows[selected];
  // Admins see key status; warn when a provider this workflow uses has no key.
  const keyless = noProviderKeys(session.team) ? [] : (policy?.allowedProviders || []).filter(provider => missingKeys(session.team).includes(provider));
  if (!canBuild(session)) return <Empty title="A builder or admin can start workflows"><p>Your {session.team.role} role can inspect runs and costs.</p><a href="#runs">View workflow runs</a></Empty>;
  return <><PageHeader title="Run workflow" subtitle="Start with an approved workflow. Every call stays within your team’s policy."/>
    {noProviderKeys(session.team) && <p className="note-warn form-width">No provider key yet, so model calls in this run will fail. <a href="#providers">Add a provider key</a> first.</p>}
    {keyless.length > 0 && <p className="note-warn form-width">{providerList(keyless)} {keyless.length > 1 ? 'have' : 'has'} no key yet, so this workflow’s calls there fail. <a className="nowrap" href="#providers">Add the key</a> first.</p>}
    {workflow && names.length > 0 && !names.includes(workflow) && <p className="note-warn form-width">{workflowName(workflow)} is not available to your team, so {workflowName(selected)} is selected instead. Choose the workflow to run below.</p>}
    <ErrorMessage message={error || policies.error}/>
    {!policies.data ? !policies.error && <Loading text="Loading workflows…"/> : !names.length ? <Empty title="No workflows configured"><p>Ask your team admin to register a workflow and start its worker.</p>{session.team.role === 'admin' && <a href="#providers">Open provider and budget setup</a>}</Empty> :
      <form className="panel form-panel" onSubmit={async event => {
        event.preventDefault(); setError(''); setBusy(true);
        try {
          let value: unknown;
          if (policy?.inputSchema) value = schemaInput(policy.inputSchema, new FormData(event.currentTarget));
          else { try { value = JSON.parse(input); } catch { throw new Error('Workflow input must be valid JSON.'); } }
          const body = { workflow: selected, project, input: value };
          const fingerprint = JSON.stringify(body);
          if (request.current?.fingerprint !== fingerprint) request.current = { fingerprint, id: crypto.randomUUID() };
          const result = await api<{ run_id: string }>(session.csrfToken, '/v1/workflow-runs', { method: 'POST', body: JSON.stringify({ ...body, request_id: request.current.id }) });
          location.hash = `run/${result.run_id}`;
        } catch (error) { setError((error as Error).message); }
        finally { setBusy(false); }
      }}>
        <div className="field-row">
          <div className="field"><label htmlFor="run-workflow">Workflow</label><select id="run-workflow" value={selected} aria-describedby={policy?.inputSchema?.description ? 'run-workflow-help' : undefined} onChange={e => { setWorkflow(e.target.value); setInput('{}'); setMore(false); }}>{names.map(n => <option key={n} value={n}>{workflowName(n)}</option>)}</select>{policy?.inputSchema?.description && <small id="run-workflow-help">{policy.inputSchema.description}</small>}</div>
          <div className="field"><label htmlFor="run-project">Project</label><select id="run-project" value={project} onChange={e => setProject(e.target.value)}>{session.team.projects.map(p => <option key={p}>{p}</option>)}</select></div>
        </div>
        {policy?.inputSchema ? <SchemaFields key={selected} schema={policy.inputSchema} models={policy.allowedModels} onMore={setMore}/> : <div className="field"><label htmlFor="run-input">Workflow input (JSON)</label><textarea id="run-input" required spellCheck={false} aria-describedby="run-input-help" value={input} onChange={e => setInput(e.target.value)} rows={6}/><small id="run-input-help">This workflow has no input form yet. Enter the JSON input its builder documents.</small></div>}
        {policy && !more && <p className="muted ceiling">Each run can use up to <span className="nowrap">{number(policy.tokenLimit)} tokens</span> and <span className="nowrap">{money(policy.costLimitUsd)}.</span></p>}
        <div className="actions"><button disabled={busy}>{busy ? 'Starting…' : 'Start run'}</button><a className="tap" href="#runs">Back to workflow runs</a></div>
        {error && request.current && <p className="muted">Retry here with unchanged input to reuse request ID <code>{request.current.id}</code>.</p>}
      </form>}
  </>;
}

function schemaInput(schema: InputSchema, form: FormData): Record<string, unknown> {
  const input: Record<string, unknown> = {};
  for (const [name, property] of Object.entries(schema.properties)) {
    const raw = form.get(`input.${name}`);
    if (property.enum) {
      if (raw !== null && raw !== '') input[name] = property.enum[Number(raw)];
    } else if (property.type === 'boolean') input[name] = raw === 'on';
    else if (raw !== '' || schema.required?.includes(name)) {
      const text = String(raw ?? '');
      input[name] = property.type === 'array' ? text.split(/\r?\n/).filter(line => line.length > 0)
        : property.type === 'number' || property.type === 'integer' ? Number(text.replaceAll(',', '')) : text;
    }
  }
  return input;
}

// Field names become labels: cost_limit_usd → "Cost limit" (with a $ prefix), incident_id → "Incident ID".
const fieldLabel = (name: string, property: InputProperty) => property.title || name.replace(/_usd$/, '').split('_')
  .map((word, i) => /^(id|usd|url|api|qa|pr)$/i.test(word) ? word.toUpperCase() : i ? word : word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
const fieldText = (value: unknown) => Array.isArray(value) ? value.join('\n') : value === undefined ? '' : String(value);

function SchemaFields({ schema, models, onMore }: { schema: InputSchema; models: string[]; onMore: (open: boolean) => void }) {
  const entries = Object.entries(schema.properties);
  const primary = entries.filter(([name]) => schema.required?.includes(name));
  const more = primary.length ? entries.filter(([name]) => !schema.required?.includes(name)) : [];
  const field = ([name, property]: [string, InputProperty]) => <SchemaField key={name} name={name} property={property} required={Boolean(schema.required?.includes(name))} models={models}/>;
  return <>{(primary.length ? primary : entries).map(field)}
    {more.length > 0 && <details className="more-options" onToggle={e => onMore(e.currentTarget.open)}><summary>More options</summary><div>{more.map(field)}</div></details>}
  </>;
}

// JSON Schema patterns match anywhere in the value; HTML patterns must match all of it.
// Without this, a "\\S" (not blank) pattern rejected every answer longer than one character.
function htmlPattern(pattern?: string) {
  return pattern ? `[\\s\\S]*(?:${pattern})[\\s\\S]*` : undefined;
}

function SchemaField({ name, property, required, models }: { name: string; property: InputProperty; required: boolean; models: string[] }) {
  const id = `field-${name}`;
  const value = property.default;
  const [numeric, setNumeric] = useState<number | ''>(typeof value === 'number' ? value : '');
  const example = property.examples?.[0];
  const help = [property.description && `${id}-help`, property.type === 'array' && `${id}-lines`].filter(Boolean).join(' ') || undefined;
  const common = { id, name: `input.${name}`, required, 'aria-describedby': help };
  const multiline = [value, ...(property.examples || [])].some(v => typeof v === 'string' && (v.length > 80 || v.includes('\n')));
  const modelChoice = name === 'model' && property.type === 'string' && !property.enum && models.length > 0;
  const control = property.type === 'boolean'
    ? <label className="check"><input name={common.name} aria-describedby={help} type="checkbox" defaultChecked={value === true}/>{fieldLabel(name, property)}</label>
    : property.enum ? <select {...common} defaultValue={value === undefined ? '' : String(property.enum.findIndex(v => JSON.stringify(v) === JSON.stringify(value)))}>
      <option value="">Choose…</option>{property.enum.map((v, i) => <option key={i} value={i}>{fieldText(v)}</option>)}
    </select>
    : modelChoice ? <select {...common} defaultValue={models.includes(String(value)) ? String(value) : models[0]}>{models.map(model => <option key={model}>{model}</option>)}</select>
    : property.type === 'array' || multiline ? <textarea {...common} minLength={property.minLength} defaultValue={fieldText(value)} placeholder={fieldText(example)} rows={multiline ? 6 : 4} spellCheck={!multiline}/>
    : ['number', 'integer'].includes(property.type) ? <NumberInput {...common} step={property.type === 'integer' ? 1 : 'any'} min={property.minimum} max={property.maximum} value={numeric} onChange={setNumeric} placeholder={fieldText(example)}/>
    : <input {...common} type="text" minLength={property.minLength} pattern={htmlPattern(property.pattern)} defaultValue={fieldText(value)} placeholder={fieldText(example)}/>;
  const usd = /_usd$/.test(name) && property.type === 'number';
  return <div className="field">
    {property.type !== 'boolean' && <label htmlFor={id}>{fieldLabel(name, property)}{usd && <span className="visually-hidden"> in US dollars</span>}</label>}
    {usd ? <div className="prefixed"><span aria-hidden="true">$</span>{control}</div> : control}
    {property.description && <small id={`${id}-help`}>{property.description.split(/(\S+)/).map((part, index) => /\S/.test(part) ? <span className="nowrap" key={index}>{part}</span> : part)}</small>}
    {property.type === 'array' && <small id={`${id}-lines`}>One item per line.</small>}
    {example !== undefined && value === undefined && property.type !== 'boolean' && !property.enum && <button type="button" className="link" onClick={() => {
      const element = document.getElementById(id) as HTMLInputElement | HTMLTextAreaElement | null;
      if (['number', 'integer'].includes(property.type)) setNumeric(Number(example));
      else if (element) { element.value = fieldText(example); element.focus(); }
    }}>Use the example</button>}
  </div>;
}

function Prose({ text }: { text: string }) {
  return <>{text.split(/(^#{1,6}[ \t]+.*$)/m).filter(part => part.trim()).map((part, index) => {
    const heading = /^(#{1,6})[ \t]+(.+)$/.exec(part);
    return heading ? createElement(`h${Math.min(heading[1].length + 2, 6)}`, { key: index }, heading[2]) : <p key={index}>{part.trim()}</p>;
  })}</>;
}

function Review({ session, run, onDone, title = 'Review the draft', eyebrow, meta, draft = run.progress?.draft }: {
  session: Session; run: Run; onDone: (final: boolean, message: string) => void; title?: string; eyebrow?: string; meta?: ReactNode; draft?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [confirmReject, setConfirmReject] = useState(false);
  const rejectButton = useRef<HTMLButtonElement>(null);
  const refocus = useRef(false);
  useEffect(() => { if (!confirmReject && refocus.current) { refocus.current = false; rejectButton.current?.focus(); } }, [confirmReject]);
  const progress = run.progress;
  const reviewed = progress?.approved_by?.includes(session.reviewerId || '');
  const expired = progress?.expires_at ? Date.parse(progress.expires_at) <= Date.now() : false;
  // The gateway says whether this reviewer may decide now; an older gateway leaves it to the role.
  const access = run.approval_access;
  const allowed = access ? access.eligible : canApprove(session) && (progress?.approver_role !== 'admin' || session.team.role === 'admin');
  const escalation = run.approval_escalation;
  // Approvals still needed after this one; the last required approval lets the workflow continue.
  const remaining = (progress?.required_approvals || 1) - (progress?.approved_by?.length || 0) - 1;
  async function decide(approved: boolean) {
    setBusy(true); setError('');
    try {
      await api(session.csrfToken, `/v1/workflow-runs/${run.run_id}/approve`, { method: 'POST', body: JSON.stringify({ approved }) });
      const final = !approved || remaining <= 0;
      const message = !approved ? 'Rejected. The publish step will not run.' : final ? 'Approved. The workflow can continue.'
        : `Approval recorded. Waiting for ${remaining} more ${remaining === 1 ? 'reviewer' : 'reviewers'}.`;
      setNotice(message); setConfirmReject(false); onDone(final, message);
    } catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  }
  const decided = busy || !!notice || reviewed || expired;
  return <section className="review panel" aria-labelledby={`review-${run.run_id}`}>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h2 id={`review-${run.run_id}`} tabIndex={-1}>{title}</h2>{meta}
    {progress?.required_approvals !== undefined && <p className="callout">{progress.approved_by?.length || 0} of {progress.required_approvals} approvals received.{progress.expires_at && <> Expires {date(Date.parse(progress.expires_at) / 1000)}.</>} Any rejection stops approval.</p>}
    {escalation && <p className="escalation-note">{escalation.escalated
      ? <>Escalated{escalation.recorded_at ? ` ${date(escalation.recorded_at)}` : ''}: {escalationReach(escalation, true)}</>
      : escalation.escalates_at ? <>Escalates {date(escalation.escalates_at)} if nobody decides first: {escalationReach(escalation, false)}</>
      : <>Escalates {duration(escalation.after_seconds)} after reaching review: {escalationReach(escalation, false)}</>}</p>}
    {allowed && access?.via === 'escalation' && <p className="callout">You can review this draft because its review escalated.</p>}
    {reviewed && <p>You have already approved this draft. Another reviewer must approve.</p>}
    {expired && <p>The approval deadline has passed. Refresh to see the final run status.</p>}
    <div className="draft prose"><Prose text={draft || 'This workflow did not provide a reviewable draft. Ask its builder to inspect the step before deciding.'}/></div>
    <ErrorMessage message={error}/><p role="status">{notice}</p>
    {!allowed ? access?.eligible_at ? <p className="callout">This review opens to you {date(access.eligible_at)} if it is still waiting then. Until then {progress?.approver_role === 'admin' ? 'an admin' : 'an approver or admin'} decides.</p>
      : <p className="callout">{progress?.approver_role === 'admin' ? 'An admin' : 'An approver or admin'} must review this draft. Use Switch account to sign in as one.</p>
      : confirmReject && !decided ? <div className="confirm callout"><p>Reject this draft? The run ends and nothing is published. This cannot be undone.</p>
        <div className="actions"><button className="danger" disabled={busy} onClick={() => void decide(false)}>Confirm rejection</button>
          <button className="secondary" autoFocus onClick={() => { refocus.current = true; setConfirmReject(false); }}>Keep reviewing</button></div></div>
      : <div className="actions"><button disabled={decided || !run.progress?.draft} onClick={() => void decide(true)}>Approve</button><button ref={rejectButton} className="danger" disabled={decided} onClick={() => setConfirmReject(true)}>Reject</button><span className="muted">{session.name ? `Your decision is recorded as ${session.name}.` : 'Your decision is recorded with your sign-in.'}</span></div>}
  </section>;
}

export function Approvals({ session, initial = '' }: { session: Session; initial?: string }) {
  const reviewer = canApprove(session);
  // Reviewers start with the drafts they may decide now; everyone can see every waiting draft.
  const [view, setView] = useState<'mine' | 'all'>(() => reviewer && new URLSearchParams(initial).get('view') !== 'all' ? 'mine' : 'all');
  useEffect(() => {
    const hash = view === 'all' && reviewer ? '#approvals?view=all' : '#approvals';
    if (location.hash !== hash) history.replaceState(history.state, '', hash);
  }, [view, reviewer]);
  const [revision, setRevision] = useState(0);
  const [rows, setRows] = useState<Run[]>();
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  // After a decision removes a card, focus moves to the card that took its place.
  const focusAt = useRef<number | null>(null);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setError('');
    // Walk every authorized project's pages, including empty filtered pages; projects load side by side.
    const project = async (name: string) => {
      const found: Run[] = [];
      let cursor: string | null = null;
      do {
        const query = new URLSearchParams({ project: name, status: 'awaiting_approval', limit: '100' });
        if (cursor) query.set('cursor', cursor);
        const page: RunPage = await api<RunPage>(session.csrfToken, `/v1/workflow-runs?${query}`, { signal: controller.signal });
        found.push(...page.runs); cursor = page.next_cursor;
      } while (cursor !== null);
      return found;
    };
    // A refresh keeps the cards on screen, so a reviewer's place and any decision notes survive it.
    Promise.all(session.team.projects.map(project)).then(
      pages => {
        if (controller.signal.aborted) return;
        const seen = new Set<string>();
        setRows(pages.flat().filter(run => !seen.has(run.run_id) && Boolean(seen.add(run.run_id)))); setBusy(false);
      },
      failure => { if (!controller.signal.aborted) { setError((failure as Error).message); setBusy(false); } },
    );
    return () => controller.abort();
  }, [session, revision]);
  useEffect(() => {
    if (focusAt.current === null) return;
    const headings = document.querySelectorAll<HTMLElement>('main .review h2');
    (headings[Math.min(focusAt.current, headings.length - 1)] ?? document.querySelector<HTMLElement>('main h1'))?.focus();
    focusAt.current = null;
  }, [rows]);
  const decided = (run: Run, final: boolean, message: string) => {
    if (!final) { setRevision(v => v + 1); return; }
    focusAt.current = shown?.findIndex(r => r.run_id === run.run_id) ?? null;
    setRows(values => values?.filter(r => r.run_id !== run.run_id));
    setNotice(`${workflowName(run.workflow)}: ${message}`);
  };
  const ready = rows?.filter(reviewable);
  const shown = view === 'mine' ? ready : rows;
  return <><PageHeader title="Approvals" subtitle={view === 'mine' ? 'Drafts you can decide now, across your projects.' : 'Drafts waiting for a decision, across your projects.'}><Refresh busy={busy && Boolean(rows)} onClick={() => setRevision(v => v + 1)}/></PageHeader>
    {reviewer && rows && <div className="segmented" role="group" aria-label="Which drafts to show">
      <button type="button" aria-pressed={view === 'mine'} onClick={() => setView('mine')}>Ready for you<span className="count">{ready?.length}</span></button>
      <button type="button" aria-pressed={view === 'all'} onClick={() => setView('all')}>All waiting<span className="count">{rows.length}</span></button>
    </div>}
    <ErrorMessage message={error} retry={() => setRevision(v => v + 1)}/>
    <p role="status" className="status">{notice}</p>
    {busy && !rows && <Loading text="Loading drafts waiting for review…"/>}
    {rows && !rows.length && !error && <div className="panel"><Empty title="You’re all caught up"><p>No steps are waiting for review in your projects.</p><a className="tap" href="#runs">Explore workflow runs</a></Empty></div>}
    {rows && rows.length > 0 && shown && !shown.length && <div className="panel"><Empty title="Nothing is ready for you"><p>{rows.length === 1 ? 'One draft waits' : `${rows.length} drafts wait`} for other reviewers. Escalated reviews appear here once they open to you.</p><button className="link" onClick={() => setView('all')}>Show all waiting drafts</button></Empty></div>}
    {shown?.map(run => { const [heading, body] = splitDraft(run.progress?.draft); return <Review key={run.run_id} session={session} run={run} onDone={(final, message) => decided(run, final, message)}
      eyebrow={workflowName(run.workflow)} title={heading || 'Review the draft'} draft={body}
      meta={<p className="muted review-meta"><DotList items={[`Project ${run.project}`, `Started ${date(run.created_at)}`, `${money(run.budget.cost_usd)} so far`, <a className="tap" href={`#run/${run.run_id}`}>Open run</a>]}/></p>}/>; })}
  </>;
}

export function RunDetail({ session, runId }: { session: Session; runId: string }) {
  const [revision, setRevision] = useState(0);
  const result = useData<Run>(session.csrfToken, `/v1/workflow-runs/${runId}`, revision);
  const [actionError, setActionError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [copied, setCopied] = useState('');
  const cancelButton = useRef<HTMLButtonElement>(null);
  const refocus = useRef(false);
  useEffect(() => { if (!confirmCancel && refocus.current) { refocus.current = false; cancelButton.current?.focus(); } }, [confirmCancel]);
  const run = result.data;
  // Several runs open in tabs stay distinguishable.
  useEffect(() => { if (run) document.title = `${workflowName(run.workflow)} run ${shortId(run.run_id)} · AgentWorkflows Console`; }, [run?.workflow, run?.run_id]);
  // While the run is active the page follows it; a failed refresh keeps the last answer and tries again.
  usePolling(run?.status === 'running', () => setRevision(v => v + 1), result.settled);
  async function act(action: 'cancel' | 'retry') {
    setBusy(true); setActionError('');
    try {
      const response = await api<{ run_id: string }>(session.csrfToken, `/v1/workflow-runs/${runId}/${action}`, { method: 'POST' });
      if (action === 'retry') location.hash = `run/${response.run_id}`;
      else { setNotice('Cancellation requested. This page updates when the run stops.'); setConfirmCancel(false); setRevision(v => v + 1); }
    } catch (error) { setActionError((error as Error).message); }
    finally { setBusy(false); }
  }
  const running = run?.status === 'running';
  return <><a className="back tap" href="#runs">Back to workflow runs</a><PageHeader title={run ? workflowName(run.workflow) : 'Run detail'}>
      {running && canBuild(session) && !confirmCancel && <button ref={cancelButton} className="danger" onClick={() => setConfirmCancel(true)}>Cancel run</button>}
    </PageHeader>
    <ErrorMessage message={result.error || actionError} retry={() => setRevision(v => v + 1)}/><p role="status" className="status">{notice || copied}</p>
    {!run && !result.error && <Loading text="Loading run…"/>}
    {run && <><div className="run-meta"><Badge value={status(run)}/><EscalationMark run={run}/><span>Project {run.project}</span><span>Started {date(run.created_at)}</span>
        {run.template && <span>Template {run.template.id} · v{run.template.version}</span>}
        {run.trigger && <a href={`#runs?${new URLSearchParams({ project: run.project, workflow: run.workflow, trigger: run.trigger.name })}`}>{run.trigger.kind === 'cron' ? 'Schedule' : 'Webhook'}: {run.trigger.name}</a>}
        <span className="run-id">Run <code title={run.run_id}>{shortId(run.run_id)}</code><button type="button" className="copy-id" aria-label="Copy run ID" onClick={async () => {
          try { await navigator.clipboard.writeText(run.run_id); setCopied('Run ID copied.'); } catch { setCopied(`Run ID: ${run.run_id}`); }
        }}><Icon name="copy"/>Copy ID</button></span></div>
      {confirmCancel && running && <div className="callout confirm"><p>Cancel this run? Model or tool calls already sent cannot be undone.</p><div className="actions"><button className="danger" disabled={busy} onClick={() => void act('cancel')}>Confirm cancellation</button><button className="secondary" autoFocus onClick={() => { refocus.current = true; setConfirmCancel(false); }}>Keep running</button></div></div>}
      {status(run) === 'awaiting_approval' && <Review session={session} run={run} onDone={() => setRevision(v => v + 1)}/>}
      <Metrics items={[[ 'Tokens', `${number(run.budget.tokens)} of ${number(run.budget.token_limit)}` ], ['Cost', `${money(run.budget.cost_usd)} of ${money(run.budget.cost_limit_usd)}`], ['Receipts', number(run.timeline?.length)]]}/>
      {run.summary && <RunSummaryPanel summary={run.summary}/>}
      {run.error && <section className="callout" role="alert"><h2>Run needs attention</h2><p>{run.error.message}</p><code>{run.error.code}</code></section>}
      {run.progress?.message && <p className="callout">{run.progress.message}</p>}
      {running && <p className="muted">This page updates automatically while the run is active.</p>}
      {run.result !== undefined && <Result value={run.result}/>}
      <section><h2>Step timeline</h2>
        {!run.timeline?.length ? <Empty title="Waiting for the first step"><p>Refresh in a moment. If the run stays queued, check its team worker and Temporal task queue.</p></Empty> :
          <ol className="timeline">{timelineGroups(run.timeline).map((group, i) => <li key={group[0].receipt_id}>
            <span className="step-number">{String(i + 1).padStart(2, '0')}</span>{group[0].action === 'notification' ? <Notifications steps={group}/> : group[0].action === 'approval' ? <ApprovalStep step={group[0]}/> : ['cancel', 'retry', 'workflow_secret_accessed', 'approval_escalated'].includes(group[0].action) ? <OperationStep step={group[0]}/> : <StepCard step={group[0]}/>}</li>)}</ol>}
        <p className="muted">Each step links to a gateway receipt. To prove nothing was changed, <a href="https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/audit-chain.md">verify an audit export</a>.</p>
      </section>
      {canBuild(session) && ['failed', 'canceled', 'terminated', 'timed_out'].includes(run.status) && <div className="run-actions">
        <p>Retry starts a new run and budget. Review side effects: tools may execute again.</p><button disabled={busy} onClick={() => void act('retry')}>Retry workflow</button>
      </div>}
    </>}
  </>;
}

// Waiting runs show when their review escalates, and an Escalated mark once it has.
function EscalationMark({ run }: { run: Run }) {
  const escalation = run.approval_escalation;
  if (!escalation || status(run) !== 'awaiting_approval') return null;
  if (escalation.escalated) return <Badge value="escalated" text="Escalated"/>;
  return escalation.escalates_at ? <small className="escalates">Escalates {date(escalation.escalates_at)}</small> : null;
}

// A draft's first line ("# Briefing: …") becomes the card title and leaves the preview; a long one stays in it.
function splitDraft(text?: string): [string, string | undefined] {
  const lines = text?.split('\n') || [];
  const index = lines.findIndex(line => line.trim());
  if (index < 0) return ['', text];
  const heading = lines[index].trim().replace(/^#+\s*/, '').replace(/^\*\*(.+)\*\*$/, '$1').trim();
  const rest = lines.slice(index + 1).join('\n').replace(/^\s*\n/, '');
  return heading.length <= 80 && rest.trim() ? [heading, rest] : [heading.length <= 80 ? heading : `${heading.slice(0, 79)}…`, text];
}
const who = (principal: Record<string, unknown> | undefined) => String(principal?.name || principal?.sub || principal?.key_id || 'a reviewer');

function OperationStep({ step }: { step: Step }) {
  const title = step.action === 'cancel' ? 'Cancellation requested' : step.action === 'retry' ? 'Retry started'
    : step.action === 'approval_escalated' ? 'Review escalated' : 'Secret accessed';
  const receipt = step.receipt as Record<string, unknown>;
  return <article className="panel step"><h3>{title}</h3><p className="muted">{date(step.timestamp)}</p>
    {step.action === 'approval_escalated'
      ? <p>No decision after {duration(Number(receipt.after_seconds))}, so {escalationReach({ approver_role: receipt.approver_role === 'approver' ? 'approver' : null, reviewers: Array.isArray(receipt.reviewers) ? receipt.reviewers.map(String) : [] }, true)} Recorded by the team’s escalation policy.</p>
      : <p>Recorded for <strong>{who(step.receipt.principal as Record<string, unknown>)}</strong>.</p>}
    {typeof step.receipt.retry_run_id === 'string' && <a href={`#run/${encodeURIComponent(step.receipt.retry_run_id)}`}>Open retry run</a>}
    {step.action === 'workflow_secret_accessed' && <p>{String(step.receipt.secret_name)} · Version {String(step.receipt.secret_version)}</p>}
    <Receipt step={step}/>
  </article>;
}

function ApprovalStep({ step }: { step: Step }) {
  const approved = step.receipt.approved === true;
  return <article className="panel step"><div className="section-heading"><h3>Approval</h3><Badge value={approved ? 'succeeded' : 'failed'} text={approved ? 'Approved' : 'Rejected'}/></div>
    <p className="muted">{date(step.timestamp)}</p>
    <p>{approved ? 'Approved' : 'Rejected'} by <strong>{who(step.receipt.principal as Record<string, unknown>)}</strong>{step.receipt.eligibility === 'escalation' && ', who could review because the review escalated'}</p>
    <Receipt step={step}/>
  </article>;
}

const stepTitle = (step: Step) => step.tool ? label(step.tool) : label(step.action || 'operation');

// Consecutive notification receipts (one per channel and delivery attempt) read as one step.
function timelineGroups(steps: Step[]) {
  const groups: Step[][] = [];
  for (const step of steps) {
    const last = groups[groups.length - 1];
    if (step.action === 'notification' && last?.[0].action === 'notification') last.push(step);
    else groups.push([step]);
  }
  return groups;
}

function Receipt({ step }: { step: Step }) {
  return <details><summary>Receipt</summary><p className="muted"><DotList items={[<>Receipt <code>{step.receipt_id.slice(0, 12)}</code></>, <>Chain <code>{step.chain_id}</code></>]}/></p><pre className="json">{JSON.stringify(step.receipt, null, 2)}</pre></details>;
}

function StepCard({ step }: { step: Step }) {
  const attempts = (step.attempts || []) as { provider?: string; status?: string; status_code?: number; reserved?: { tokens: number; cost_usd: number }; charged?: unknown }[];
  const failed = attempts.filter(a => a.status === 'failed');
  const held = failed.reduce((sum, a) => sum + (a.charged ? 0 : a.reserved?.cost_usd || 0), 0);
  const heldTokens = failed.reduce((sum, a) => sum + (a.charged ? 0 : a.reserved?.tokens || 0), 0);
  const served = (attempts.find(a => a.status === 'served')?.charged || null) as { tokens: number; cost_usd: number } | null;
  const tokens = served && failed.length ? served.tokens : step.tokens;
  const cost = served && failed.length ? served.cost_usd : step.cost_usd;
  const tool = Boolean(step.tool) || step.action === 'tool_call';
  const [open, setOpen] = useState(false);
  const answer = step.content?.output ? preview(step.content.output) : '';
  return <article className="panel step"><div className="section-heading"><h3>{stepTitle(step)}</h3>{step.status_code && step.status_code >= 400 ? <Badge value="failed"/> : <Badge value="succeeded"/>}</div>
    <p className="muted"><DotList items={[date(step.timestamp), `${number(step.duration_ms)} ms`]}/></p>
    {tool ? <dl className="step-facts"><div><dt>Tool</dt><dd>{step.tool || '—'}</dd></div><div><dt>Cost</dt><dd>{money(cost)}</dd></div></dl>
      : (step.provider || step.tokens > 0 || step.cost_usd > 0) && <dl className="step-facts"><div><dt>Provider</dt><dd>{step.provider ? providerName(step.provider) : '—'}</dd></div><div><dt>Model</dt><dd>{step.model || '—'}</dd></div><div><dt>Tokens</dt><dd>{number(tokens)}</dd></div><div><dt>Cost</dt><dd>{money(cost)}</dd></div></dl>}
    {failed.length > 0 && <p className="note-warn">{failed.map(a => `${providerName(a.provider || '')}${a.status_code ? ` returned ${a.status_code}` : ' failed'}`).join(', ')}, so the gateway sent this step to {providerName(step.provider)}.{held > 0 && ` ${money(held)} and ${number(heldTokens)} tokens stay held for the failed ${providerName(failed[0].provider || '')} attempt because it reported no usage. The run totals and Costs include them.`}</p>}
    {answer && !open && <blockquote className="step-preview prose"><Prose text={answer}/></blockquote>}
    <details onToggle={e => setOpen(e.currentTarget.open)}><summary>{tool ? 'Arguments and result' : 'Prompt and response'}</summary>
      {step.content ? <>
        <p className="muted">{step.content.redaction === 'redacted' ? 'Saved with sensitive values masked.' : 'Saved in full after the gateway’s checks.'}</p>
        <h4>{tool ? 'Arguments' : 'Prompt'}</h4><Content text={step.content.input} empty="No input recorded."/>
        {step.content.truncated.input && <p className="muted">Input truncated at the capture size limit.</p>}
        <h4>{tool ? 'Result' : 'Response'}</h4><Content text={step.content.output} empty="No output recorded."/>
        {step.content.truncated.output && <p className="muted">Output truncated at the capture size limit.</p>}
      </> : <p className="muted">{step.content_reason === 'expired' ? 'Captured content expired.' : 'Content capture is off for this step.'}</p>}
    </details>
    <Receipt step={step}/>
    <details><summary>Step logs</summary><p>Gateway event and routing attempts. Worker output stays in the operator’s logs.</p><pre className="json">{JSON.stringify({ timestamp: step.timestamp, action: step.action, status_code: step.status_code, duration_ms: step.duration_ms, attempts: step.attempts, reason: step.receipt?.reason }, null, 2)}</pre></details>
  </article>;
}

const parsed = (text: string): unknown => { try { return JSON.parse(text); } catch { return undefined; } };
const messageText = (content: unknown) => content == null ? '' : typeof content === 'string' ? content
  : Array.isArray(content) ? content.map(part => (part as { text?: string }).text ?? JSON.stringify(part)).join('\n') : JSON.stringify(content, null, 2);
// Previews show prose answers; structured tool results stay in the expandable section.
const preview = (text: string) => { const value = parsed(text); const plain = typeof value === 'string' ? value : value === undefined ? text : ''; return plain.length > 280 ? plain.slice(0, 280).trimEnd() + '…' : plain; };

// Model prompts arrive as message arrays; show them as a short transcript instead of raw JSON.
function Content({ text, empty }: { text: string | null; empty: string }) {
  if (text == null) return <pre className="step-content prose">{empty}</pre>;
  const value = parsed(text);
  if (Array.isArray(value) && value.length && value.every(m => m && typeof m === 'object' && 'role' in m)) {
    return <ol className="transcript">{(value as { role: string; content: unknown }[]).map((message, i) => <li key={i}>
      <span className={`speaker ${message.role}`}>{label(String(message.role))}</span><div className="step-content prose"><Prose text={messageText(message.content)}/></div>
    </li>)}</ol>;
  }
  return value !== null && typeof value === 'object' ? <div className="code-scroll"><pre className="step-content json">{JSON.stringify(value, null, 2)}</pre></div>
    : <div className="step-content prose"><Prose text={typeof value === 'string' ? value : text}/></div>;
}

const channelName: Record<string, string> = { slack: 'Slack', webhook: 'Webhook', email: 'Email' };
const notificationEvent: Record<string, string> = { awaiting_approval: 'Approval requested', failed: 'Run failed', budget_threshold: 'Budget threshold reached', approval_escalated: 'Review escalated' };

function Notifications({ steps }: { steps: Step[] }) {
  // Each delivery writes an "attempted" receipt, then its outcome; show the latest per channel and event.
  const latest = new Map<string, Step>();
  for (const step of steps) latest.set(`${step.receipt.channel}/${step.receipt.notification_event}`, step);
  const undelivered = [...latest.values()].some(step => step.receipt.outcome === 'failed');
  return <article className="panel step"><div className="section-heading"><h3>Notifications</h3>{undelivered ? <Badge value="failed" text="Not delivered"/> : <Badge value="succeeded" text="Sent"/>}</div>
    <p className="muted">{date(steps[0].timestamp)}</p>
    <ul className="notifications">{[...latest.values()].map(step => <li key={step.receipt_id}>
      <strong>{channelName[String(step.receipt.channel)] || String(step.receipt.channel || 'Channel')}</strong>
      <span>{notificationEvent[String(step.receipt.notification_event)] || label(String(step.receipt.notification_event || 'event'))}</span>
      <span className="muted">{label(String(step.receipt.outcome || 'recorded'))}</span></li>)}</ul>
    <details><summary>{steps.length === 1 ? '1 receipt' : `${steps.length} receipts`}</summary>{steps.map(step => <Receipt key={step.receipt_id} step={step}/>)}</details>
  </article>;
}

function Result({ value }: { value: unknown }) {
  const result = (value && typeof value === 'object' ? value : {}) as { status?: string; reviewer?: string; publication?: { url?: string } };
  // The URL comes from workflow output, so only a web address becomes a link.
  const link = webUrl(result.publication?.url);
  const summary = result.status === 'rejected' ? <p>Rejected by <strong>{result.reviewer || 'a reviewer'}</strong>. Nothing was published.</p>
    : result.status === 'published' ? <p>Published{link ? <>: <a href={link} target="_blank" rel="noopener noreferrer">open the briefing</a></> : result.publication?.url && <> at <code>{String(result.publication.url)}</code></>}.</p> : null;
  const raw = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return <section className="panel result"><h2>Result</h2>
    {summary ? <><div className="result-summary">{summary}</div><details><summary>Raw result</summary><pre>{raw}</pre></details></>
      : typeof value === 'string' ? <div className="result-summary"><p>{value}</p></div> : <pre>{raw}</pre>}
  </section>;
}
