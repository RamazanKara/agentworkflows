import { useState } from 'react';
import { api, money, number, providerName, useData, type Session } from './api';
import { Empty, ErrorMessage, Loading, NumberInput, Refresh } from './ui';

type Registered = {
  name: string; allowed_models: string[]; allowed_providers: string[]; allowed_tools: string[]; allowed_egress: string[];
  token_limit: number; cost_limit_usd: number; approval_required: boolean; approver_role: 'admin' | 'approver';
  required_approvals: number; approval_timeout_seconds: number; input_schema: Record<string, unknown> | null;
};
type Registry = {
  revision: number; enabled: boolean; workflows: Registered[]; reserved: string[];
  options: { providers: string[]; models: { id: string; provider: string; simulated: boolean }[]; tools: string[] };
  limits: { token_limit: number; cost_limit_usd: number };
};
type Draft = {
  name: string; models: string[]; tools: string[]; tokens: number | ''; cost: number | ''; approval: boolean;
  reviewers: number | ''; deadline: number; schema: string;
};

const deadlines: [string, number][] = [['1 hour', 3600], ['4 hours', 14400], ['1 day', 86400], ['3 days', 259200], ['7 days', 604800]];
const lifetime = (seconds: number) => deadlines.find(([, value]) => value === seconds)?.[0]
  ?? (seconds % 3600 === 0 ? `${seconds / 3600} hours` : `${Math.round(seconds / 60)} minutes`);
const blank = (registry: Registry): Draft => ({
  name: '', models: registry.options.models.slice(0, 1).map(model => model.id), tools: [], tokens: Math.min(10000, registry.limits.token_limit),
  cost: Math.min(5, registry.limits.cost_limit_usd), approval: true, reviewers: 1, deadline: 604800, schema: '',
});
const fromRegistered = (workflow: Registered): Draft => ({
  name: workflow.name, models: workflow.allowed_models, tools: workflow.allowed_tools, tokens: workflow.token_limit, cost: workflow.cost_limit_usd,
  approval: workflow.approval_required, reviewers: workflow.required_approvals, deadline: workflow.approval_timeout_seconds,
  schema: workflow.input_schema ? JSON.stringify(workflow.input_schema, null, 2) : '',
});

export function WorkflowRegistry({ session }: { session: Session }) {
  const [revision, setRevision] = useState(0);
  const result = useData<Registry>(session.csrfToken, '/v1/team/workflows', revision);
  const [draft, setDraft] = useState<Draft>();
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const registry = result.data;
  const toggle = (field: 'models' | 'tools', value: string) => setDraft(current => current && ({
    ...current, [field]: current[field].includes(value) ? current[field].filter(item => item !== value) : [...current[field], value],
  }));
  const form = draft && registry ? draft : undefined;
  return <section className="registry" aria-labelledby="registry-title">
    <div className="section-heading"><h2 id="registry-title">Your workflows</h2><Refresh onClick={() => setRevision(value => value + 1)}/></div>
    <p>Register a workflow your team wrote. It can use only the models and tools already approved for your team, and its egress follows from them. Run it from <a href="#new">Run workflow</a>.</p>
    <ErrorMessage message={error || result.error} retry={() => setRevision(value => value + 1)}/><p className="status" role="status">{notice}</p>
    {!registry && !result.error && <Loading/>}
    {registry && !registry.enabled && <p className="callout">Your operator keeps workflow types in reviewed policy. Ask them to add a workflow to your team’s policy.</p>}
    {registry && registry.enabled && <>
      {registry.workflows.length ? <div className="panel"><div className="table-scroll" tabIndex={0} role="region" aria-label="Registered workflows table"><table className="stack registry-table"><thead><tr>
        <th>Workflow</th><th>Models</th><th>Tools</th><th>Limits per run</th><th>Review</th><th><span className="visually-hidden">Actions</span></th></tr></thead><tbody>
        {registry.workflows.map(workflow => <tr key={workflow.name}>
          <th scope="row"><code>{workflow.name}</code></th>
          <td data-label="Models">{workflow.allowed_models.join(', ')}<small>{workflow.allowed_providers.map(providerName).join(', ')}</small></td>
          <td data-label="Tools">{workflow.allowed_tools.length ? workflow.allowed_tools.join(', ') : 'None'}</td>
          <td data-label="Limits">{number(workflow.token_limit)} tokens<small>{money(workflow.cost_limit_usd)}</small></td>
          <td data-label="Review">{workflow.approval_required ? `${workflow.required_approvals} ${workflow.required_approvals === 1 ? 'reviewer' : 'reviewers'}` : 'No approval'}{workflow.approval_required && <small>Expires after {lifetime(workflow.approval_timeout_seconds)}</small>}</td>
          <td className="row-action"><a className="button secondary" href={`#new/${encodeURIComponent(workflow.name)}`} aria-label={`Run ${workflow.name}`}>Run</a>
            <button className="secondary" disabled={Boolean(busy)} aria-label={`Edit ${workflow.name}`} onClick={() => { setDraft(fromRegistered(workflow)); setEditing(true); setError(''); setNotice(''); }}>Edit</button>
            <button className="danger" disabled={Boolean(busy)} aria-label={`Remove ${workflow.name}`} onClick={async () => {
              if (!window.confirm(`Remove ${workflow.name}? New calls from its runs are denied immediately.`)) return;
              setBusy(workflow.name); setError(''); setNotice('');
              try {
                await api(session.csrfToken, `/v1/team/workflows/${encodeURIComponent(workflow.name)}`, { method: 'DELETE', headers: { 'If-Match': String(registry.revision) } });
                setNotice(`${workflow.name} removed.`); setDraft(undefined); setRevision(value => value + 1);
              } catch (failure) { setError((failure as Error).message); } finally { setBusy(''); }
            }}>Remove</button></td>
        </tr>)}</tbody></table></div></div>
        : !form && <div className="panel"><Empty title="No workflows registered"><p>Your team uses the templates above. Register your own to run code you have written.</p></Empty></div>}
      {!form && <button onClick={() => { setDraft(blank(registry)); setEditing(false); setError(''); setNotice(''); }}>Register a workflow</button>}
      {form && <form className="panel form-panel" aria-labelledby="registry-form-title" onSubmit={async event => {
        event.preventDefault(); setError(''); setNotice('');
        let input_schema: unknown = null;
        if (form.schema.trim()) {
          try { input_schema = JSON.parse(form.schema); } catch { setError('The input schema must be valid JSON, or leave it empty for a free-form JSON input.'); return; }
        }
        if (!form.models.length) { setError('Choose at least one model.'); return; }
        setBusy(form.name);
        try {
          const body = {
            allowed_models: form.models, allowed_tools: form.tools, token_limit: form.tokens, cost_limit_usd: form.cost, approval_required: form.approval,
            required_approvals: form.approval ? form.reviewers : 1, approval_timeout_seconds: form.deadline, input_schema,
          };
          const saved = await api<{ workflow: Registered }>(session.csrfToken, `/v1/team/workflows/${encodeURIComponent(form.name)}`, {
            method: 'PUT', headers: { 'If-Match': String(registry.revision) }, body: JSON.stringify(body),
          });
          setNotice(`${saved.workflow.name} ${editing ? 'updated' : 'registered'}. Start your worker, then run it.`); setDraft(undefined); setRevision(value => value + 1);
        } catch (failure) { setError((failure as Error).message); } finally { setBusy(''); }
      }}>
        <h2 id="registry-form-title">{editing ? `Edit ${form.name}` : 'Register a workflow'}</h2>
        <div className="field"><label htmlFor="workflow-name">Workflow name</label>
          <input id="workflow-name" required readOnly={editing} maxLength={64} pattern="[A-Za-z][A-Za-z0-9_]*" autoComplete="off" spellCheck={false} placeholder="e.g. TicketTriageWorkflow"
            aria-describedby="workflow-name-help" value={form.name} onChange={event => setDraft({ ...form, name: event.target.value })}/>
          <small id="workflow-name-help" className="muted">The Temporal workflow type your worker registers. Names from your operator’s policy or the built-in templates are reserved.</small></div>
        <fieldset className="choices"><legend>Models</legend>{registry.options.models.map(model => <label className="check" key={model.id}>
          <input type="checkbox" checked={form.models.includes(model.id)} onChange={() => toggle('models', model.id)}/><span><code>{model.id}</code> <span className="muted">{providerName(model.provider)}{model.simulated && ', simulated'}</span></span></label>)}</fieldset>
        {registry.options.tools.length > 0 && <fieldset className="choices"><legend>Tools</legend>{registry.options.tools.map(tool => <label className="check" key={tool}>
          <input type="checkbox" checked={form.tools.includes(tool)} onChange={() => toggle('tools', tool)}/><code>{tool}</code></label>)}</fieldset>}
        <div className="field"><label htmlFor="workflow-tokens">Token limit per run</label>
          <NumberInput id="workflow-tokens" required min={0} max={registry.limits.token_limit} step={1} value={form.tokens} onChange={value => setDraft({ ...form, tokens: value })}/>
          <small className="muted">Up to {number(registry.limits.token_limit)}, your team’s ceiling.</small></div>
        <div className="field"><label htmlFor="workflow-cost">Cost limit per run (USD)</label>
          <NumberInput id="workflow-cost" required min={0} max={registry.limits.cost_limit_usd} value={form.cost} onChange={value => setDraft({ ...form, cost: value })}/>
          <small className="muted">Up to {money(registry.limits.cost_limit_usd)}, your team’s ceiling.</small></div>
        <label className="check"><input type="checkbox" checked={form.approval} onChange={event => setDraft({ ...form, approval: event.target.checked })}/>Require human approval before publishing</label>
        {form.approval && <><div className="field"><label htmlFor="workflow-reviewers">Reviewers required</label>
          <NumberInput id="workflow-reviewers" required min={1} max={10} step={1} value={form.reviewers} onChange={value => setDraft({ ...form, reviewers: value })}/></div>
          <div className="field"><label htmlFor="workflow-deadline">Review deadline</label>
            <select id="workflow-deadline" value={form.deadline} onChange={event => setDraft({ ...form, deadline: Number(event.target.value) })}>
              {[...(deadlines.some(([, value]) => value === form.deadline) ? [] : [[lifetime(form.deadline), form.deadline] as [string, number]]), ...deadlines].map(([text, value]) => <option key={value} value={value}>{text}</option>)}
            </select></div></>}
        <div className="field"><label htmlFor="workflow-schema">Input schema (optional)</label>
          <textarea id="workflow-schema" rows={6} spellCheck={false} placeholder={'{"type":"object","properties":{"ticket":{"type":"string"}},"required":["ticket"]}'}
            aria-describedby="workflow-schema-help" value={form.schema} onChange={event => setDraft({ ...form, schema: event.target.value })}/>
          <small id="workflow-schema-help" className="muted">A flat JSON Schema turns the run page into a form. Leave empty for a JSON box.</small></div>
        <div className="form-actions"><button disabled={Boolean(busy)}>{busy ? 'Saving…' : editing ? 'Save changes' : 'Register workflow'}</button>
          <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => { setDraft(undefined); setError(''); }}>Cancel</button></div>
      </form>}
    </>}
  </section>;
}
