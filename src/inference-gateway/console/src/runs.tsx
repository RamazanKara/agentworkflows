import { useEffect, useRef, useState } from 'react';
import { api, date, label, money, number, status, useData, type Policy, type Run, type RunPage, type Session } from './api';
import { Badge, Empty, ErrorMessage, Loading, Metrics, PageHeader, Refresh } from './ui';

const canBuild = (session: Session) => ['admin', 'builder'].includes(session.team.role);
const canApprove = (session: Session) => ['admin', 'approver'].includes(session.team.role);

export function Runs({ session }: { session: Session }) {
  const [project, setProject] = useState(session.team.projects[0] || '');
  const [filter, setFilter] = useState('');
  const [workflow, setWorkflow] = useState('');
  const [revision, setRevision] = useState(0);
  const [rows, setRows] = useState<Run[]>([]);
  const [offset, setOffset] = useState<number | null>(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(true);
  const current = useRef<AbortController | null>(null);
  const policies = useData<{ workflows: Record<string, Policy> }>(session.token, '/v1/workflow-policies');
  async function load(cursor: number, reset = false) {
    current.current?.abort();
    const controller = new AbortController(); current.current = controller;
    setBusy(true); setError('');
    if (reset) setRows([]);
    try {
      const query = new URLSearchParams({ project, offset: String(cursor), limit: '20' });
      if (filter) query.set('status', filter);
      if (workflow) query.set('workflow', workflow);
      const result = await api<RunPage>(session.token, `/v1/workflow-runs?${query}`, { signal: controller.signal });
      if (!controller.signal.aborted) {
        setRows(values => [...(reset ? [] : values), ...result.runs].filter((r, i, all) => all.findIndex(v => v.run_id === r.run_id) === i));
        setOffset(result.next_offset);
      }
    } catch (error) { if (!controller.signal.aborted) setError((error as Error).message); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  useEffect(() => { void load(0, true); return () => current.current?.abort(); }, [project, filter, workflow, revision]);
  return <>
    <PageHeader title="Workflow runs" subtitle="Every step accounted for."><Refresh onClick={() => setRevision(v => v + 1)}/>{canBuild(session) && <a className="button" href="#new">Run workflow</a>}</PageHeader>
    <Metrics items={[
      ['In this page', number(rows.length)], ['Waiting for review', number(rows.filter(r => status(r) === 'awaiting_approval').length)],
      ['Estimated spend', money(rows.reduce((sum, run) => sum + run.budget.cost_usd, 0))],
    ]}/>
    <div className="filters panel">
      <label>Project<select value={project} onChange={e => setProject(e.target.value)}>{session.team.projects.map(p => <option key={p}>{p}</option>)}</select></label>
      <label>Status<select value={filter} onChange={e => setFilter(e.target.value)}><option value="">All statuses</option>{['awaiting_approval', 'running', 'completed', 'failed', 'canceled', 'timed_out', 'terminated'].map(s => <option key={s} value={s}>{label(s)}</option>)}</select></label>
      <label>Workflow<select value={workflow} onChange={e => setWorkflow(e.target.value)}><option value="">All workflows</option>{Object.keys(policies.data?.workflows || {}).map(w => <option key={w}>{w}</option>)}</select></label>
    </div>
    <ErrorMessage message={error || policies.error} retry={() => void load(offset ?? 0)}/>
    <div className="panel">
      {rows.length ? <div className="table-scroll" tabIndex={0} role="region" aria-label="Workflow runs table"><table>
        <thead><tr>{['Workflow', 'Run', 'Status', 'Started', 'Tokens', 'Cost'].map(h => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>{rows.map(run => <tr key={run.run_id}><td><a href={`#run/${run.run_id}`}>{run.workflow}</a><small>{run.project}</small></td><td><code>{run.run_id.slice(0, 8)}</code></td><td><Badge value={status(run)}/></td><td>{date(run.created_at)}</td><td>{number(run.budget.tokens)}</td><td>{money(run.budget.cost_usd)}</td></tr>)}</tbody>
      </table></div> : !busy && !error && <Empty title={filter || workflow ? 'No matching runs on this page' : 'Your first workflow starts here'}><p>{offset === null ? 'Start a workflow or choose different filters.' : 'Load more to continue searching older runs.'}</p>{canBuild(session) && <a href="#new">Run a workflow</a>}</Empty>}
      {busy && <Loading/>}
      {offset !== null && !busy && <div className="table-footer"><button className="secondary" onClick={() => void load(offset)}>Load more</button></div>}
    </div>
    <p className="muted">Configured-price estimates. Open a run to inspect its receipts.</p>
  </>;
}

export function StartRun({ session }: { session: Session }) {
  const policies = useData<{ workflows: Record<string, Policy> }>(session.token, '/v1/workflow-policies');
  const [workflow, setWorkflow] = useState('');
  const [project, setProject] = useState(session.team.projects[0] || '');
  const [topic, setTopic] = useState('How should our team evaluate AI agents?');
  const [model, setModel] = useState('');
  const [input, setInput] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const request = useRef<{ fingerprint: string; id: string } | null>(null);
  const names = Object.keys(policies.data?.workflows || {});
  const selected = workflow || (names.includes('ResearchWorkflow') ? 'ResearchWorkflow' : names[0]);
  const policy = policies.data?.workflows[selected];
  const chosenModel = model || policy?.allowedModels[0] || '';
  const suggestedInput = selected === 'SupportTriageWorkflow'
    ? '{"ticket":"I cannot sign in after resetting my password."}'
    : selected === 'CodeReviewWorkflow' ? JSON.stringify({ diff: '- return user.is_admin\n+ return True' })
    : selected === 'WeeklyReportWorkflow' ? '{"period":"2026-09-28/2026-10-04"}'
    : selected === 'IncidentSummaryWorkflow' ? '{"incident_id":"INC-1042"}'
    : selected === 'DocumentQAWorkflow' ? '{"question":"Who can approve a workflow, and when does approval expire?"}' : '{}';
  if (!canBuild(session)) return <Empty title="A builder or admin can start workflows"><p>Your {session.team.role} role can inspect runs and costs.</p><a href="#runs">View workflow runs</a></Empty>;
  return <><PageHeader title="Run workflow" subtitle="Start with an approved workflow. Every call stays within your team’s policy."/>
    <ErrorMessage message={error || policies.error}/>
    {!policies.data ? <Loading/> : !names.length ? <Empty title="No workflows configured"><p>Ask your team admin to register a workflow and start its worker.</p>{session.team.role === 'admin' && <a href="#providers">Open provider and budget setup</a>}</Empty> :
      <form className="panel form-panel" onSubmit={async event => {
        event.preventDefault(); setError(''); setBusy(true);
        try {
          let value: unknown;
          if (selected === 'ResearchWorkflow') value = { topic, ...(chosenModel ? { model: chosenModel } : {}) };
          else { try { value = JSON.parse(input ?? suggestedInput); } catch { throw new Error(`Workflow input must be valid JSON. For example: ${suggestedInput}.`); } }
          const body = { workflow: selected, project, input: value };
          const fingerprint = JSON.stringify(body);
          if (request.current?.fingerprint !== fingerprint) request.current = { fingerprint, id: crypto.randomUUID() };
          const result = await api<{ run_id: string }>(session.token, '/v1/workflow-runs', { method: 'POST', body: JSON.stringify({ ...body, request_id: request.current.id }) });
          location.hash = `run/${result.run_id}`;
        } catch (error) { setError((error as Error).message); }
        finally { setBusy(false); }
      }}>
        <label>Workflow<select value={selected} onChange={e => { setWorkflow(e.target.value); setModel(''); setInput(null); }}>{names.map(n => <option key={n}>{n}</option>)}</select></label>
        <label>Project<select value={project} onChange={e => setProject(e.target.value)}>{session.team.projects.map(p => <option key={p}>{p}</option>)}</select></label>
        {selected === 'ResearchWorkflow' ? <>
          <label>Research topic<textarea required value={topic} onChange={e => setTopic(e.target.value)} rows={3}/></label>
          {policy?.allowedModels.length ? <label>Model<select value={chosenModel} onChange={e => setModel(e.target.value)}>{policy.allowedModels.map(m => <option key={m}>{m}</option>)}</select></label> : <p>The example uses its worker’s default model.</p>}
          <p className="callout">Research → draft → approval → publish. An approver or admin reviews the draft before the publish tool runs.</p>
        </> : <label>Workflow input (JSON)<textarea required spellCheck={false} value={input ?? suggestedInput} onChange={e => setInput(e.target.value)} rows={6}/></label>}
        {policy && <p className="muted">Policy ceiling: {number(policy.tokenLimit)} tokens · {money(policy.costLimitUsd)} per run. The workflow can use a lower limit.</p>}
        <div className="actions"><button disabled={busy}>{busy ? 'Starting…' : 'Start run'}</button><a href="#runs">Back to runs</a></div>
        {error && request.current && <p className="muted">Retry here with unchanged input to reuse request ID <code>{request.current.id}</code>.</p>}
      </form>}
  </>;
}

function Review({ session, run, onDone }: { session: Session; run: Run; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  async function decide(approved: boolean) {
    setBusy(true); setError('');
    try {
      await api(session.token, `/v1/workflow-runs/${run.run_id}/approve`, { method: 'POST', body: JSON.stringify({ approved }) });
      setNotice(approved ? 'Approved. The workflow can continue.' : 'Rejected. The publish step will not run.'); onDone();
    } catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  }
  return <section className="review panel"><div className="section-heading"><h2>Review the waiting draft</h2><Badge value="awaiting_approval"/></div>
    <p>Your decision applies to run <code>{run.run_id}</code>.</p>
    <pre className="draft">{run.progress?.draft || 'This workflow did not provide a reviewable draft. Ask its builder to inspect the step before deciding.'}</pre>
    <ErrorMessage message={error}/><p role="status">{notice}</p>
    {canApprove(session) ? <div className="actions"><button disabled={busy || !!notice || !run.progress?.draft} onClick={() => void decide(true)}>Approve</button><button className="danger" disabled={busy || !!notice} onClick={() => void decide(false)}>Reject</button><span className="muted">Recorded as your verified identity.</span></div> : <p className="callout">An approver or admin must review this draft. Use “Add team / identity” to sign in with that credential.</p>}
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
          let offset: number | null = 0;
          do {
            const query = new URLSearchParams({ project, status: 'awaiting_approval', offset: String(offset), limit: '100' });
            const page: RunPage = await api<RunPage>(session.token, `/v1/workflow-runs?${query}`, { signal: controller.signal });
            if (controller.signal.aborted) return;
            setRows(values => [...values, ...page.runs]); offset = page.next_offset;
          } while (offset !== null);
        }
      } catch (error) { if (!controller.signal.aborted) setError((error as Error).message); }
      finally { if (!controller.signal.aborted) setBusy(false); }
    }
    void load(); return () => controller.abort();
  }, [session, revision]);
  return <><PageHeader title="Approvals" subtitle="Review waiting work across all your available projects."><Refresh onClick={() => setRevision(v => v + 1)}/></PageHeader>
    <ErrorMessage message={error} retry={() => setRevision(v => v + 1)}/>
    {busy && <Loading/>}
    {!busy && !error && !rows.length && <Empty title="You’re all caught up"><p>No steps are waiting for review in your projects.</p><a href="#runs">Explore workflow runs</a></Empty>}
    {rows.map(run => <article className="approval-item" key={run.run_id}><h2><a href={`#run/${run.run_id}`}>{run.workflow}</a></h2><p className="muted">{run.project} · Started {date(run.created_at)}</p><Review session={session} run={run} onDone={() => setRows(values => values.filter(r => r.run_id !== run.run_id))}/></article>)}
  </>;
}

export function RunDetail({ session, runId }: { session: Session; runId: string }) {
  const [revision, setRevision] = useState(0);
  const result = useData<Run>(session.token, `/v1/workflow-runs/${runId}`, revision);
  const [actionError, setActionError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const run = result.data;
  useEffect(() => {
    if (run?.status !== 'running') return;
    const timer = window.setInterval(() => setRevision(v => v + 1), 5000);
    return () => window.clearInterval(timer);
  }, [run?.status]);
  async function act(action: 'cancel' | 'retry') {
    setBusy(true); setActionError('');
    try {
      const response = await api<{ run_id: string }>(session.token, `/v1/workflow-runs/${runId}/${action}`, { method: 'POST' });
      if (action === 'retry') location.hash = `run/${response.run_id}`;
      else { setNotice('Cancellation requested. Refresh to see the latest status.'); setConfirmCancel(false); setRevision(v => v + 1); }
    } catch (error) { setActionError((error as Error).message); }
    finally { setBusy(false); }
  }
  return <><a className="back" href="#runs">Back to workflow runs</a><PageHeader title={run?.workflow || 'Run detail'} subtitle="Follow the work, inspect the evidence."><Refresh onClick={() => setRevision(v => v + 1)}/></PageHeader>
    <ErrorMessage message={result.error || actionError} retry={() => setRevision(v => v + 1)}/><p role="status">{notice}</p>
    {!run && !result.error && <Loading/>}
    {run && <><div className="run-meta"><Badge value={status(run)}/><span>{run.project}</span><span>{date(run.created_at)}</span><code>{run.run_id}</code></div>
      <Metrics items={[[ 'Tokens / limit', `${number(run.budget.tokens)} / ${number(run.budget.token_limit)}` ], ['Cost / limit', `${money(run.budget.cost_usd)} / ${money(run.budget.cost_limit_usd)}`], ['Receipted steps', number(run.timeline?.length)]]}/>
      {run.progress?.message && <p className="callout">{run.progress.message}</p>}
      {run.status === 'running' && <p className="muted">Updates every 5 seconds while this run is active.</p>}
      {status(run) === 'awaiting_approval' && <Review session={session} run={run} onDone={() => setRevision(v => v + 1)}/>}
      {run.result !== undefined && <section className="panel"><h2>Result</h2><pre>{typeof run.result === 'string' ? run.result : JSON.stringify(run.result, null, 2)}</pre></section>}
      <section><div className="section-heading"><h2>Step timeline</h2><span className="muted">Provider, usage, and evidence</span></div>
        {!run.timeline?.length ? <Empty title="Waiting for the first step"><p>Refresh in a moment. If the run stays queued, check its team worker and Temporal task queue.</p></Empty> :
          <ol className="timeline">{run.timeline.map((step, i) => <li key={step.receipt_id}>
            <span className="step-number">{i + 1}</span><article className="panel step"><div className="section-heading"><h3>{step.tool || label(step.action || 'operation')}</h3><Badge value={step.status_code && step.status_code >= 400 ? 'failed' : 'recorded'}/></div>
              <p className="muted">Step <code>{step.step_id}</code> · {date(step.timestamp)} · {number(step.duration_ms)} ms</p>
              <dl className="step-facts"><div><dt>Provider</dt><dd>{step.provider || '—'}</dd></div><div><dt>Model / tool</dt><dd>{step.tool || step.model || '—'}</dd></div><div><dt>Tokens</dt><dd>{number(step.tokens)}</dd></div><div><dt>Cost</dt><dd>{money(step.cost_usd)}</dd></div></dl>
              <details><summary>Receipt · {step.receipt_id.slice(0, 12)}</summary><p>Chain <code>{step.chain_id}</code> · Compare this record with the retained audit export.</p><pre>{JSON.stringify(step.receipt, null, 2)}</pre></details>
              <details><summary>Step logs</summary><p>Gateway event and routing attempts. Worker stdout remains in the operator’s logs.</p><pre>{JSON.stringify({ timestamp: step.timestamp, action: step.action, status_code: step.status_code, duration_ms: step.duration_ms, attempts: step.attempts, reason: step.receipt?.reason }, null, 2)}</pre></details>
            </article></li>)}</ol>}
        <p className="muted">This timeline indexes retained gateway receipts. A hash alone does not verify a chain; verify the audit export and retained head anchors.</p>
      </section>
      {canBuild(session) && <div className="run-actions">
        {run.status === 'running' && (confirmCancel ? <div className="callout"><p>Cancel this run? Already-sent model or tool actions cannot be undone.</p><div className="actions"><button className="danger" disabled={busy} onClick={() => void act('cancel')}>Confirm cancellation</button><button className="secondary" onClick={() => setConfirmCancel(false)}>Keep running</button></div></div> : <button className="secondary" onClick={() => setConfirmCancel(true)}>Cancel run</button>)}
        {['failed', 'canceled', 'terminated', 'timed_out'].includes(run.status) && <><p>Retry starts a new run and budget. Review side effects: tools may execute again.</p><button disabled={busy} onClick={() => void act('retry')}>Retry workflow</button></>}
      </div>}
    </>}
  </>;
}
