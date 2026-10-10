import { useState } from 'react';
import { duration, label, milliseconds, money, number, useData, workflowName, type RunSummary, type Session, type WorkflowInsight, type WorkflowInsights } from './api';
import { Empty, ErrorMessage, Loading, Metrics, PageHeader, Refresh } from './ui';

const outcomes: [keyof WorkflowInsight['outcomes'], string][] = [
  ['completed', 'completed'], ['rejected', 'rejected'], ['failed', 'failed'], ['canceled', 'canceled'],
  ['awaiting_approval', 'awaiting review'], ['running', 'running'],
];
const windows: [number, string][] = [[1, 'Last 24 hours'], [7, 'Last 7 days'], [30, 'Last 30 days']];

type StepRef = { step_id: string; action?: string; name?: string };
const isTool = (step: StepRef) => ['tool_exec', 'tool_call'].includes(step.action || '');
// SDK step IDs are counters ("3"); people know a step by the tool it ran or the model it called.
export function stepLabel(step: StepRef) {
  if (step.step_id && !/^\d+$/.test(step.step_id)) return label(step.step_id);
  if (isTool(step)) return step.name ? label(step.name) : 'Tool call';
  if (step.action === 'model_call' || step.name) return step.name ? `Model call (${step.name})` : 'Model call';
  return step.step_id ? `Step ${step.step_id}` : label(step.action || 'step');
}

function OutcomeBar({ row }: { row: WorkflowInsight }) {
  const summary = outcomes.filter(([key]) => row.outcomes[key] > 0).map(([key, text]) => `${number(row.outcomes[key])} ${text}`).join(', ');
  return <><span className="outcome-bar" role="img" aria-label={summary}>{outcomes.map(([key]) => row.outcomes[key] > 0 &&
    <span key={key} className={`o-${key}`} style={{ width: `${(row.outcomes[key] / row.runs) * 100}%` }}/>)}</span>
    <small>{summary}</small></>;
}

export function Insights({ session }: { session: Session }) {
  const [days, setDays] = useState(7);
  const [project, setProject] = useState('');
  const [revision, setRevision] = useState(0);
  const query = new URLSearchParams({ days: String(days), ...(project ? { project } : {}) });
  const result = useData<WorkflowInsights>(session.csrfToken, `/v1/workflow-insights?${query}`, revision);
  const data = result.data;
  const runs = data?.workflows.reduce((sum, row) => sum + row.runs, 0) ?? 0;
  const finished = data?.workflows.reduce((sum, row) => sum + row.outcomes.completed + row.outcomes.rejected + row.outcomes.failed + row.outcomes.canceled, 0) ?? 0;
  const completed = data?.workflows.reduce((sum, row) => sum + row.outcomes.completed, 0) ?? 0;
  const spend = data?.workflows.reduce((sum, row) => sum + row.total_cost_usd, 0) ?? 0;
  const projects = session.team.projects;
  return <><PageHeader title="Insights" subtitle="How each workflow is doing across recent runs.">
      <Refresh onClick={() => setRevision(value => value + 1)}/></PageHeader>
    <div className="filters panel insight-filters" role="group" aria-label="Insight filters">
      <label>Period<select aria-label="Period" value={days} onChange={event => setDays(Number(event.target.value))}>{windows.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>
      {projects.length > 1 && <label>Project<select aria-label="Project" value={project} onChange={event => setProject(event.target.value)}>
        <option value="">All projects</option>{projects.map(name => <option key={name} value={name}>{name}</option>)}</select></label>}
    </div>
    <ErrorMessage message={result.error} retry={() => setRevision(value => value + 1)}/>
    {!data && !result.error && <Loading/>}
    {data && <>
      <Metrics items={[
        ['Runs', number(runs)],
        ['Completed', finished ? <>{Math.round((completed / finished) * 100)}%<small>of {number(finished)} finished</small></> : '—'],
        ['Spend', money(spend)],
      ]}/>
      {data.truncated && <p className="note-warn">This period has more runs than the {number(data.scanned)} most recent ones summarized here. Choose a shorter period for the full picture.</p>}
      {data.workflows.length ? <div className="panel"><div className="table-scroll" tabIndex={0} role="region" aria-label="Workflow insights table"><table className="stack insights-table">
        <thead><tr><th>Workflow</th><th>Outcomes</th><th>Typical run</th><th>Review wait</th><th>Cost per run</th><th>Slowest step</th></tr></thead>
        <tbody>{data.workflows.map(row => <tr key={row.workflow}>
          <th scope="row">{workflowName(row.workflow)}<small>{number(row.runs)} {row.runs === 1 ? 'run' : 'runs'}</small></th>
          <td data-label="Outcomes"><OutcomeBar row={row}/></td>
          <td data-label="Typical run">{duration(row.median_seconds)}<small>{row.p95_seconds == null ? 'No finished runs' : `95% finish within ${duration(row.p95_seconds)}`}</small></td>
          <td data-label="Review wait">{duration(row.median_review_seconds)}<small>{row.median_review_seconds == null ? 'No decisions yet' : 'median to a decision'}</small></td>
          <td data-label="Cost per run">{money(row.average_cost_usd)}<small>{money(row.total_cost_usd)} in total{row.costliest_step ? `, most on ${stepLabel(row.costliest_step)}` : ''}</small></td>
          <td data-label="Slowest step">{row.slowest_step ? stepLabel(row.slowest_step) : '—'}{row.slowest_step && <small>median {milliseconds(row.slowest_step.median_ms)}</small>}</td>
        </tr>)}</tbody></table></div></div>
        : <div className="panel"><Empty title="No runs in this period"><p>Start a workflow, or choose a longer period. Insights use the runs and receipts your team still retains.</p><a className="tap" href="#new">Run a workflow</a></Empty></div>}
      <p className="muted">Durations come from gateway receipts. Review wait is the time from the last step before a decision to that decision. Costs are estimates from configured prices, not an invoice.</p>
    </>}
  </>;
}

export function RunSummaryPanel({ summary }: { summary: RunSummary }) {
  if (!summary.by_step.length) return null;
  const costs = summary.by_step.reduce((sum, step) => sum + step.cost_usd, 0);
  const time = summary.by_step.reduce((sum, step) => sum + step.duration_ms, 0);
  const calls = (count: number, noun: string) => `${number(count)} ${noun}${count === 1 ? '' : 's'}`;
  return <section className="panel run-summary" aria-labelledby="run-summary-title">
    <div className="section-heading"><h2 id="run-summary-title">Where the time and money went</h2></div>
    <Metrics items={[
      ['Elapsed', duration(summary.elapsed_seconds)],
      ['Working', <>{duration((summary.model_ms + summary.tool_ms) / 1000)}<small>{calls(summary.model_calls, 'model call')}, {calls(summary.tool_calls, 'tool call')}</small></>],
      [summary.review_open ? 'Waiting for review' : 'Review wait', summary.review_seconds == null ? '—' : <>{duration(summary.review_seconds)}{summary.review_open && <small>so far</small>}</>],
    ]}/>
    <div className="table-scroll" tabIndex={0} role="region" aria-label="Time and cost by step"><table className="stack numeric"><thead><tr>
      <th>Step</th><th className="num">Calls</th><th className="num">Time</th><th className="num">Tokens</th><th className="num">Cost</th></tr></thead>
      <tbody>{summary.by_step.map(step => <tr key={`${step.step_id}:${step.action}`}>
        <th scope="row">{/^\d+$/.test(step.step_id) && step.name ? isTool(step) ? label(step.name) : 'Model call' : label(step.step_id || step.action)}<small>{isTool(step) ? 'Tool call' : step.name && /^\d+$/.test(step.step_id) ? step.name : 'Model call'}</small></th>
        <td data-label="Calls">{number(step.calls)}</td>
        <td data-label="Time">{milliseconds(step.duration_ms)}{time > 0 && <Share value={step.duration_ms / time} what="of working time"/>}</td>
        <td data-label="Tokens">{number(step.tokens)}</td>
        <td data-label="Cost">{money(step.cost_usd)}{costs > 0 && <Share value={step.cost_usd / costs} what="of cost"/>}</td>
      </tr>)}</tbody></table></div>
  </section>;
}

function Share({ value, what }: { value: number; what: string }) {
  const percent = Math.round(value * 100);
  return <span className="usage-bar share" role="img" aria-label={`${percent}% ${what}`}><span style={{ width: `${percent}%`, minWidth: value > 0 ? 4 : 0 }}/></span>;
}
