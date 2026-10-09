import { createElement, useEffect, useRef, useState, type ReactNode } from 'react';
import { api, date, label, missingKeys, money, noProviderKeys, number, providerList, providerName, shortId, status, useData, workflowName, type InputProperty, type InputSchema, type Policy, type Run, type RunPage, type Session, type Step } from './api';
import { Badge, DotList, Empty, ErrorMessage, Icon, Loading, Metrics, NumberInput, PageHeader, Refresh } from './ui';

const canBuild = (session: Session) => ['admin', 'builder'].includes(session.team.role);
const canApprove = (session: Session) => ['admin', 'approver'].includes(session.team.role);

export function Runs({ session, initial = '' }: { session: Session; initial?: string }) {
  const query = new URLSearchParams(initial);
  const [project, setProject] = useState(query.get('project') || session.team.projects[0] || '');
  const [filter, setFilter] = useState('');
  const [workflow, setWorkflow] = useState(query.get('workflow') || '');
  const [trigger, setTrigger] = useState(query.get('trigger') || '');
  const [revision, setRevision] = useState(0);
  const [rows, setRows] = useState<Run[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(true);
  const [filters, setFilters] = useState(false);
  const current = useRef<AbortController | null>(null);
  const policies = useData<{ workflows: Record<string, Policy> }>(session.csrfToken, '/v1/workflow-policies');
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
        setRows(values => [...(reset ? [] : values), ...result.runs].filter((r, i, all) => all.findIndex(v => v.run_id === r.run_id) === i));
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
      <label>Status<select value={filter} onChange={e => setFilter(e.target.value)}><option value="">All statuses</option>{['awaiting_approval', 'running', 'completed', 'failed', 'canceled', 'timed_out', 'terminated'].map(s => <option key={s} value={s}>{label(s)}</option>)}</select></label>
      <label>Workflow<select value={workflow} onChange={e => { setWorkflow(e.target.value); setTrigger(''); }}><option value="">All workflows</option>{Object.keys(policies.data?.workflows || {}).map(w => <option key={w} value={w}>{workflowName(w)}</option>)}</select></label>
    </div>
    <ErrorMessage message={error || policies.error} retry={() => void load(cursor)}/>
    <div className="panel">
      {rows.length ? <div className="table-scroll" tabIndex={0} role="region" aria-label="Workflow runs table"><table className="stack runs-stack">
        <thead><tr>{['Workflow', 'Run', 'Status', 'Started', 'Tokens', 'Cost'].map(h => <th key={h} className={['Tokens', 'Cost'].includes(h) ? 'num' : undefined}>{h}</th>)}</tr></thead>
        <tbody>{rows.map(run => <tr key={run.run_id}><td><a href={`#run/${run.run_id}`}>{workflowName(run.workflow)}</a><small>{run.project}</small></td><td data-label="Run"><code title={run.run_id}>{shortId(run.run_id)}</code></td><td data-label="Status"><Badge value={status(run)}/></td><td data-label="Started">{date(run.created_at)}</td><td data-label="Tokens">{number(run.budget.tokens)}</td><td data-label="Cost">{money(run.budget.cost_usd)}</td></tr>)}</tbody>
      </table></div> : !busy && !error && <Empty title={filter || workflow ? 'No matching runs' : 'Your first workflow starts here'}><p>{cursor !== null ? 'Load more to continue searching older runs.' : filter || workflow ? 'No run in this project matches these filters.' : 'Runs appear here with their status, cost and receipts.'}</p>{canBuild(session) && !filter && !workflow && <a className="tap" href="#new">Run a workflow</a>}</Empty>}
      {busy && <Loading/>}
      {cursor !== null && !busy && <div className="table-footer"><button className="secondary" onClick={() => void load(cursor)}>Load more</button></div>}
    </div>
    <p className="muted">Costs are estimates from configured prices. Open a run to see each step and its receipt.</p>
  </>;
}

export function StartRun({ session, initial = '' }: { session: Session; initial?: string }) {
  const policies = useData<{ workflows: Record<string, Policy> }>(session.csrfToken, '/v1/workflow-policies');
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
    <ErrorMessage message={error || policies.error}/>
    {!policies.data ? <Loading/> : !names.length ? <Empty title="No workflows configured"><p>Ask your team admin to register a workflow and start its worker.</p>{session.team.role === 'admin' && <a href="#providers">Open provider and budget setup</a>}</Empty> :
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
    : <input {...common} type="text" minLength={property.minLength} pattern={property.pattern} defaultValue={fieldText(value)} placeholder={fieldText(example)}/>;
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

function Review({ session, run, onDone, title = 'Review the draft', eyebrow, meta, draft = run.progress?.draft }: { session: Session; run: Run; onDone: () => void; title?: string; eyebrow?: string; meta?: ReactNode; draft?: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const progress = run.progress;
  const reviewed = progress?.approved_by?.includes(session.reviewerId || '');
  const expired = progress?.expires_at ? Date.parse(progress.expires_at) <= Date.now() : false;
  const allowed = canApprove(session) && (progress?.approver_role !== 'admin' || session.team.role === 'admin');
  async function decide(approved: boolean) {
    setBusy(true); setError('');
    try {
      await api(session.csrfToken, `/v1/workflow-runs/${run.run_id}/approve`, { method: 'POST', body: JSON.stringify({ approved }) });
      setNotice(approved ? (progress?.required_approvals || 1) > 1 ? 'Approval recorded. Waiting for the required reviewers.' : 'Approved. The workflow can continue.' : 'Rejected. The publish step will not run.'); onDone();
    } catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  }
  return <section className="review panel">{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h2>{title}</h2>{meta}
    {progress?.required_approvals !== undefined && <p className="callout">{progress.approved_by?.length || 0} of {progress.required_approvals} approvals received.{progress.expires_at && <> Expires {date(Date.parse(progress.expires_at) / 1000)}.</>} Any rejection stops approval.</p>}
    {reviewed && <p>You have already approved this draft. Another reviewer must approve.</p>}
    {expired && <p>The approval deadline has passed. Refresh to see the final run status.</p>}
    <div className="draft prose"><Prose text={draft || 'This workflow did not provide a reviewable draft. Ask its builder to inspect the step before deciding.'}/></div>
    <ErrorMessage message={error}/><p role="status">{notice}</p>
    {allowed ? <div className="actions"><button disabled={busy || !!notice || reviewed || expired || !run.progress?.draft} onClick={() => void decide(true)}>Approve</button><button className="danger" disabled={busy || !!notice || reviewed || expired} onClick={() => void decide(false)}>Reject</button><span className="muted">{session.name ? `Your decision is recorded as ${session.name}.` : 'Your decision is recorded with your sign-in.'}</span></div> : <p className="callout">{progress?.approver_role === 'admin' ? 'An admin' : 'An approver or admin'} must review this draft. Use Switch account to sign in as one.</p>}
  </section>;
}

export function Approvals({ session }: { session: Session }) {
  const [revision, setRevision] = useState(0);
  const [rows, setRows] = useState<Run[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setRows([]); setError('');
    async function load() {
      try {
        // Walk every authorized project and page, including empty filtered pages.
        for (const project of session.team.projects) {
          let cursor: string | null = null;
          do {
            const query = new URLSearchParams({ project, status: 'awaiting_approval', limit: '100' });
            if (cursor) query.set('cursor', cursor);
            const page: RunPage = await api<RunPage>(session.csrfToken, `/v1/workflow-runs?${query}`, { signal: controller.signal });
            if (controller.signal.aborted) return;
            setRows(values => [...values, ...page.runs]); cursor = page.next_cursor;
          } while (cursor !== null);
        }
      } catch (error) { if (!controller.signal.aborted) setError((error as Error).message); }
      finally { if (!controller.signal.aborted) setBusy(false); }
    }
    void load(); return () => controller.abort();
  }, [session, revision]);
  return <><PageHeader title="Approvals" subtitle="Drafts waiting for a decision, across your projects."><Refresh onClick={() => setRevision(v => v + 1)}/></PageHeader>
    <ErrorMessage message={error} retry={() => setRevision(v => v + 1)}/>
    {busy && <Loading/>}
    {!busy && !error && !rows.length && <div className="panel"><Empty title="You’re all caught up"><p>No steps are waiting for review in your projects.</p><a className="tap" href="#runs">Explore workflow runs</a></Empty></div>}
    {rows.map(run => { const [heading, body] = splitDraft(run.progress?.draft); return <Review key={run.run_id} session={session} run={run} onDone={() => (run.progress?.required_approvals || 1) > 1 ? setRevision(v => v + 1) : setRows(values => values.filter(r => r.run_id !== run.run_id))}
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
  const run = result.data;
  useEffect(() => {
    if (run?.status !== 'running') return;
    const timer = window.setInterval(() => setRevision(v => v + 1), 5000);
    return () => window.clearInterval(timer);
  }, [run?.status]);
  async function act(action: 'cancel' | 'retry') {
    setBusy(true); setActionError('');
    try {
      const response = await api<{ run_id: string }>(session.csrfToken, `/v1/workflow-runs/${runId}/${action}`, { method: 'POST' });
      if (action === 'retry') location.hash = `run/${response.run_id}`;
      else { setNotice('Cancellation requested. Refresh to see the latest status.'); setConfirmCancel(false); setRevision(v => v + 1); }
    } catch (error) { setActionError((error as Error).message); }
    finally { setBusy(false); }
  }
  const running = run?.status === 'running';
  return <><a className="back tap" href="#runs">Back to workflow runs</a><PageHeader title={run ? workflowName(run.workflow) : 'Run detail'}>
      {running && canBuild(session) && !confirmCancel && <button className="danger" onClick={() => setConfirmCancel(true)}>Cancel run</button>}
    </PageHeader>
    <ErrorMessage message={result.error || actionError} retry={() => setRevision(v => v + 1)}/><p role="status" className="status">{notice || copied}</p>
    {!run && !result.error && <Loading/>}
    {run && <><div className="run-meta"><Badge value={status(run)}/><span>Project {run.project}</span><span>Started {date(run.created_at)}</span>
        {run.template && <span>Template {run.template.id} · v{run.template.version}</span>}
        {run.trigger && <a href={`#runs?${new URLSearchParams({ project: run.project, workflow: run.workflow, trigger: run.trigger.name })}`}>{run.trigger.kind === 'cron' ? 'Schedule' : 'Webhook'}: {run.trigger.name}</a>}
        <span className="run-id">Run <code title={run.run_id}>{shortId(run.run_id)}</code><button type="button" className="copy-id" aria-label="Copy run ID" onClick={async () => {
          try { await navigator.clipboard.writeText(run.run_id); setCopied('Run ID copied.'); } catch { setCopied(`Run ID: ${run.run_id}`); }
        }}><Icon name="copy"/>Copy ID</button></span></div>
      {confirmCancel && running && <div className="callout"><p>Cancel this run? Model or tool calls already sent cannot be undone.</p><div className="actions"><button className="danger" disabled={busy} onClick={() => void act('cancel')}>Confirm cancellation</button><button className="secondary" onClick={() => setConfirmCancel(false)}>Keep running</button></div></div>}
      {status(run) === 'awaiting_approval' && <Review session={session} run={run} onDone={() => setRevision(v => v + 1)}/>}
      <Metrics items={[[ 'Tokens', `${number(run.budget.tokens)} of ${number(run.budget.token_limit)}` ], ['Cost', `${money(run.budget.cost_usd)} of ${money(run.budget.cost_limit_usd)}`], ['Receipts', number(run.timeline?.length)]]}/>
      {run.progress?.message && <p className="callout">{run.progress.message}</p>}
      {running && <p className="muted">This page updates automatically while the run is active.</p>}
      {run.result !== undefined && <Result value={run.result}/>}
      <section><h2>Step timeline</h2>
        {!run.timeline?.length ? <Empty title="Waiting for the first step"><p>Refresh in a moment. If the run stays queued, check its team worker and Temporal task queue.</p></Empty> :
          <ol className="timeline">{timelineGroups(run.timeline).map((group, i) => <li key={group[0].receipt_id}>
            <span className="step-number">{String(i + 1).padStart(2, '0')}</span>{group[0].action === 'notification' ? <Notifications steps={group}/> : group[0].action === 'approval' ? <ApprovalStep step={group[0]}/> : ['cancel', 'retry', 'workflow_secret_accessed'].includes(group[0].action) ? <OperationStep step={group[0]}/> : <StepCard step={group[0]}/>}</li>)}</ol>}
        <p className="muted">Each step links to a gateway receipt. To prove nothing was changed, <a href="https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/audit-chain.md">verify an audit export</a>.</p>
      </section>
      {canBuild(session) && ['failed', 'canceled', 'terminated', 'timed_out'].includes(run.status) && <div className="run-actions">
        <p>Retry starts a new run and budget. Review side effects: tools may execute again.</p><button disabled={busy} onClick={() => void act('retry')}>Retry workflow</button>
      </div>}
    </>}
  </>;
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
  const title = step.action === 'cancel' ? 'Cancellation requested' : step.action === 'retry' ? 'Retry started' : 'Secret accessed';
  return <article className="panel step"><h3>{title}</h3><p className="muted">{date(step.timestamp)}</p>
    <p>Recorded for <strong>{who(step.receipt.principal as Record<string, unknown>)}</strong>.</p>
    {typeof step.receipt.retry_run_id === 'string' && <a href={`#run/${encodeURIComponent(step.receipt.retry_run_id)}`}>Open retry run</a>}
    {step.action === 'workflow_secret_accessed' && <p>{String(step.receipt.secret_name)} · Version {String(step.receipt.secret_version)}</p>}
    <Receipt step={step}/>
  </article>;
}

function ApprovalStep({ step }: { step: Step }) {
  const approved = step.receipt.approved === true;
  return <article className="panel step"><div className="section-heading"><h3>Approval</h3><Badge value={approved ? 'succeeded' : 'failed'} text={approved ? 'Approved' : 'Rejected'}/></div>
    <p className="muted">{date(step.timestamp)}</p>
    <p>{approved ? 'Approved' : 'Rejected'} by <strong>{who(step.receipt.principal as Record<string, unknown>)}</strong></p>
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
const notificationEvent: Record<string, string> = { awaiting_approval: 'Approval requested', failed: 'Run failed', budget_threshold: 'Budget threshold reached' };

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
  const summary = result.status === 'rejected' ? <p>Rejected by <strong>{result.reviewer || 'a reviewer'}</strong>. Nothing was published.</p>
    : result.status === 'published' ? <p>Published{result.publication?.url && <>: <a href={result.publication.url}>open the briefing</a></>}.</p> : null;
  const raw = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return <section className="panel result"><h2>Result</h2>
    {summary ? <><div className="result-summary">{summary}</div><details><summary>Raw result</summary><pre>{raw}</pre></details></>
      : typeof value === 'string' ? <div className="result-summary"><p>{value}</p></div> : <pre>{raw}</pre>}
  </section>;
}
