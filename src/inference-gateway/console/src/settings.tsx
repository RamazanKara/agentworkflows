import { useState, type ReactNode } from 'react';
import { api, date, label as titleCase, money, number, providerName, useData, workflowName, type ApiError, type Policy, type Session, type SettingField, type SettingValue, type Team, type TeamSettings } from './api';
import { ErrorMessage, Loading, PageHeader } from './ui';

// Field keys: cost_limit_usd, project_budgets.<project>, workflows.<Workflow>.<field>, model_routes.<alias>.
type Kind = 'usd' | 'tokens' | 'flag' | 'role' | 'route' | 'providers';
const workflowFields: Record<string, [string, Kind]> = {
  cost_limit_usd: ['Budget per run', 'usd'], token_limit: ['Token limit per run', 'tokens'],
  approval_required: ['Require approval', 'flag'], approval_threshold_usd: ['Approval threshold', 'usd'],
  approver_role: ['Approver role', 'role'], allowed_providers: ['Allowed providers', 'providers'],
};
const workflowOf = (field: string) => field.startsWith('workflows.') ? field.slice(10, field.lastIndexOf('.')) : '';
const kind = (field: string): Kind => field.startsWith('model_routes.') ? 'route'
  : workflowOf(field) ? workflowFields[field.slice(field.lastIndexOf('.') + 1)]?.[1] ?? 'usd' : 'usd';
// What a person reads next to the control; the accessible name adds the workflow and the unit.
const visible = (field: string) => field === 'cost_limit_usd' ? 'Team monthly budget'
  : field.startsWith('project_budgets.') ? `Project ${field.slice(16)} monthly budget`
  : field.startsWith('model_routes.') ? field.slice(13)
  : workflowFields[field.slice(field.lastIndexOf('.') + 1)]?.[0] ?? field;
const accessible = (field: string) => field.startsWith('model_routes.') ? `${field.slice(13)} model route`
  : `${workflowOf(field) ? `${workflowName(workflowOf(field))} · ` : ''}${visible(field)}${kind(field) === 'usd' ? ' (USD)' : ''}`;
const format = (field: string, value: SettingValue) => value === null || value === '' ? 'No limit'
  : Array.isArray(value) ? value.map(providerName).join(', ') || 'Team policy'
  : typeof value === 'boolean' ? value ? 'Required' : 'Not required'
  : kind(field) === 'usd' ? money(Number(value)) : kind(field) === 'tokens' ? `${number(Number(value))} tokens`
  : kind(field) === 'role' ? titleCase(String(value)) : String(value);
const budgetField = (field: string) => field === 'cost_limit_usd' || field.startsWith('project_budgets.') || /\.(cost_limit_usd|token_limit)$/.test(field);

type Groups = { team: string[]; workflows: [string, string[]][]; routes: string[] };
function group(fields: string[], budgetsOnly: boolean): Groups {
  const shown = fields.filter(field => !budgetsOnly || budgetField(field));
  const workflows = new Map<string, string[]>();
  for (const field of shown.filter(workflowOf)) workflows.set(workflowOf(field), [...workflows.get(workflowOf(field)) || [], field]);
  // Budget first, then tokens and approval, in the order of workflowFields.
  const order = Object.keys(workflowFields);
  for (const names of workflows.values()) names.sort((a, b) => order.indexOf(a.slice(a.lastIndexOf('.') + 1)) - order.indexOf(b.slice(b.lastIndexOf('.') + 1)));
  return { team: shown.filter(field => field === 'cost_limit_usd' || field.startsWith('project_budgets.')), workflows: [...workflows], routes: shown.filter(field => field.startsWith('model_routes.')) };
}

export function TeamConfiguration({ session }: { session: Session }) {
  return <><PageHeader title="Team settings" subtitle="Budgets, approval rules and model routes. Changes apply to the next request; no restart."/><SettingsPanel session={session}/></>;
}

export function SettingsPanel({ session, budgetsOnly = false, onSaved }: { session: Session; budgetsOnly?: boolean; onSaved?: () => void }) {
  return session.team.role === 'admin' ? <SettingsEditor session={session} budgetsOnly={budgetsOnly} onSaved={onSaved}/>
    : <ReadOnlySettings session={session} budgetsOnly={budgetsOnly}/>;
}

function SettingsEditor({ session, budgetsOnly, onSaved }: { session: Session; budgetsOnly: boolean; onSaved?: () => void }) {
  const [revision, setRevision] = useState(0);
  const result = useData<TeamSettings>(session.csrfToken, '/v1/team/settings', revision);
  const [saved, setSaved] = useState<TeamSettings>();
  const settings = saved || result.data;
  const [draft, setDraft] = useState<Record<string, SettingValue>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [message, setMessage] = useState('');
  const changes = Object.keys(draft).length;
  const reload = () => { setDraft({}); setSaved(undefined); setError(''); setErrors({}); setConflict(false); setMessage(''); setRevision(v => v + 1); };
  const submit = async (field?: string) => {
    if (!settings) return;
    setBusy(true); setError(''); setErrors({}); setMessage('');
    try {
      const updated = await api<TeamSettings>(session.csrfToken, '/v1/team/settings' + (field ? `/${encodeURIComponent(field)}` : ''), {
        method: field ? 'DELETE' : 'PATCH', headers: { 'If-Match': String(settings.revision) },
        ...(field ? {} : { body: JSON.stringify({ fields: draft }) }),
      });
      setSaved(updated);
      setDraft(previous => field ? Object.fromEntries(Object.entries(previous).filter(([name]) => name !== field)) : {});
      setMessage(field ? `${accessible(field)} reset to policy default.` : 'Settings saved.');
      onSaved?.();
    } catch (value) {
      const failure = value as ApiError;
      setConflict(failure.status === 409);
      setError(failure.status === 409 ? 'Settings changed since you opened this page. Reload and review the latest values before saving again.' : failure.message);
      setErrors(Object.fromEntries((failure.fields || []).map(item => [item.field, item.message])));
    } finally { setBusy(false); }
  };
  const groups = settings && group(Object.keys(settings.fields), budgetsOnly);
  // Offer the team's providers plus any a workflow already allows; local backends appear only when in use.
  const offered = (state: SettingField) => settings!.providers.filter(provider => session.team.providers.includes(provider)
    || [state.value, state.policy_default].some(value => Array.isArray(value) && value.includes(provider)));
  const field = (name: string) => <SettingControl key={name} field={name} state={settings!.fields[name]} value={name in draft ? draft[name] : settings!.fields[name].value}
    pending={name in draft} error={errors[name]} providers={offered(settings!.fields[name])}
    choices={name.startsWith('model_routes.') ? settings!.routes : name.endsWith('.approver_role') ? settings!.approver_roles : undefined}
    disabled={name.endsWith('.approval_threshold_usd') && (draft[name.replace(/threshold_usd$/, 'required')] ?? settings!.fields[name.replace(/threshold_usd$/, 'required')]?.value) === false}
    onChange={next => setDraft(previous => ({ ...previous, [name]: next }))} onReset={() => void submit(name)}/>;
  return <section className={budgetsOnly ? 'settings budgets-only' : 'settings'} aria-labelledby={budgetsOnly ? 'budgets-heading' : undefined}>
    {budgetsOnly && <h2 id="budgets-heading">Budgets</h2>}
    <ErrorMessage message={error || result.error}/>
    {(conflict || result.error) && <button className="secondary" type="button" disabled={busy} onClick={reload}>Reload settings</button>}
    {!settings && !result.error && <Loading/>}
    {settings && groups && <form onSubmit={event => { event.preventDefault(); void submit(); }}>
      <fieldset className="plain" disabled={busy || conflict}>
        {groups.team.length > 0 && <SettingsGroup nested={budgetsOnly} title={budgetsOnly ? 'Team and projects' : 'Budgets'} note="Team and project budgets reset at the start of each UTC month.">
          <div className="setting-grid">{groups.team.map(field)}</div>
        </SettingsGroup>}
        {groups.workflows.length > 0 && (budgetsOnly
          ? <SettingsGroup nested title="Run budgets" note="Each run of a workflow stays within these limits and within what its worker requests.">
            {/* On wide screens the rows read as a table: one header, one line per workflow. */}
            <div className="run-budgets"><div className="run-budgets-head" aria-hidden="true"><span>Workflow</span><span>Budget per run</span><span>Token limit per run</span></div>
              {groups.workflows.map(([workflow, fields]) => <div className="setting-row" key={workflow}><h4>{workflowName(workflow)}</h4><div className="setting-grid">{fields.map(field)}</div></div>)}</div>
          </SettingsGroup>
          : <SettingsGroup nested={false} title="Workflows" note="Approval rules apply at each workflow’s review step.">
            {groups.workflows.map(([workflow, fields]) => <WorkflowCard key={workflow} workflow={workflow} open={groups.workflows.length <= 2}
              changed={fields.some(name => name in draft || settings.fields[name].source === 'override')}
              summary={summary(workflow, name => name in draft ? draft[name] : settings.fields[name]?.value)}>
              <div className="setting-grid">{fields.map(field)}</div>
            </WorkflowCard>)}
          </SettingsGroup>)}
        {groups.routes.length > 0 && <SettingsGroup nested={false} title="Model routes" note="Choose which approved model each alias calls. New routes and prices are added by your operator.">
          <div className="setting-grid">{groups.routes.map(field)}</div>
        </SettingsGroup>}
        <div className={changes ? 'save-bar dirty' : 'save-bar'}>
          <p>{changes ? `${changes} unsaved ${changes === 1 ? 'change' : 'changes'}` : settings.updated_at ? `Last changed by ${settings.updated_by} · ${date(settings.updated_at)}` : 'Using your team policy. Nothing changed here yet.'}</p>
          {changes > 0 && <button type="button" className="secondary" onClick={() => { setDraft({}); setErrors({}); setMessage(''); }}>Discard</button>}
          <button type="submit" disabled={!changes}>{busy ? 'Saving…' : 'Save settings'}</button>
        </div>
      </fieldset>
    </form>}
    <p role="status" className="status">{message}</p>
  </section>;
}

// "$5.00 per run · runs from $0.50 approved by admins · OpenAI, Anthropic"
function summary(workflow: string, value: (field: string) => SettingValue) {
  const key = (name: string) => `workflows.${workflow}.${name}`;
  const threshold = Number(value(key('approval_threshold_usd')) || 0);
  const approvers = `${String(value(key('approver_role')) || 'approver')}s`;
  const approval = value(key('approval_required')) === false ? 'no approval step'
    : threshold > 0 ? `runs from ${money(threshold)} approved by ${approvers}` : `every run approved by ${approvers}`;
  return [value(key('cost_limit_usd')) != null && `${format(key('cost_limit_usd'), value(key('cost_limit_usd')))} per run`, approval,
    format(key('allowed_providers'), value(key('allowed_providers')) ?? [])].filter(Boolean).join(' · ');
}

// Groups are page sections on Team settings and subsections where Budgets is embedded in another page.
function SettingsGroup({ title, note, nested, children }: { title: string; note: string; nested: boolean; children: ReactNode }) {
  return <section className="panel settings-group">{nested ? <h3>{title}</h3> : <h2>{title}</h2>}<p className="muted">{note}</p>{children}</section>;
}

function WorkflowCard({ workflow, summary: text, changed, open, children }: { workflow: string; summary: string; changed: boolean; open: boolean; children: ReactNode }) {
  return <details className="workflow-card" open={open}><summary><span className="workflow-card-title">{workflowName(workflow)}{changed && <span className="badge override">Changed</span>}</span><small>{text}</small></summary>{children}</details>;
}

function SettingControl({ field, state, value, pending, error, providers, choices, disabled, onChange, onReset }: {
  field: string; state: SettingField; value: SettingValue; pending: boolean; error?: string; providers: string[]; choices?: string[]; disabled: boolean;
  onChange: (value: SettingValue) => void; onReset: () => void;
}) {
  const id = `setting-${field}`;
  const type = kind(field);
  const prefix = workflowOf(field) ? `${workflowName(workflowOf(field))} · ` : '';
  const name = <>{prefix && <span className="visually-hidden">{prefix}</span>}{type === 'route' ? <code>{visible(field)}</code> : visible(field)}{type === 'usd' && <span className="visually-hidden"> (USD)</span>}{type === 'route' && <span className="visually-hidden"> model route</span>}</>;
  // Values from the policy need no note; changed values show the policy default and a way back to it.
  const meta = state.source === 'override'
    ? <p className="setting-meta"><span className="badge override">Changed</span><span>Policy default {format(field, state.policy_default)}</span><button className="link" type="button" onClick={onReset} aria-label={`Reset ${accessible(field)} to policy default`}>Reset</button></p>
    : pending ? <p className="setting-meta"><span>Policy default {format(field, state.policy_default)}</span></p> : null;
  const described = [error && `${id}-error`, field.endsWith('.approval_threshold_usd') && `${id}-help`].filter(Boolean).join(' ') || undefined;
  let control: ReactNode;
  if (type === 'flag') control = <label className="check"><input id={id} type="checkbox" checked={value === true} onChange={event => onChange(event.target.checked)}/>{name}</label>;
  else if (type === 'providers') control = <fieldset className="choices"><legend>{name}</legend>{providers.map(provider => <label className="check" key={provider}>
    <input type="checkbox" checked={Array.isArray(value) && value.includes(provider)} onChange={event => onChange(event.target.checked
      ? [...(Array.isArray(value) ? value : []), provider] : (Array.isArray(value) ? value : []).filter(item => item !== provider))}/>{providerName(provider)}</label>)}</fieldset>;
  else if (choices) control = <><label htmlFor={id}>{name}</label><select id={id} value={String(value)} onChange={event => onChange(event.target.value)}>
    {choices.map(option => <option key={option} value={option}>{type === 'role' ? titleCase(option) : option}</option>)}</select></>;
  else {
    const input = <input id={id} type="number" inputMode="decimal" min="0" max={type === 'tokens' ? 1000000000 : 1000000} step={type === 'tokens' ? 1 : 'any'} placeholder="No limit"
      value={value === null ? '' : String(value)} required={pending} disabled={disabled} aria-invalid={Boolean(error)} aria-describedby={described}
      onChange={event => onChange(event.target.value === '' ? '' : Number(event.target.value))}/>;
    control = <><label htmlFor={id}>{name}</label>{type === 'usd' ? <div className="prefixed"><span aria-hidden="true">$</span>{input}</div> : input}</>;
  }
  return <div className={['setting', pending && 'pending', type === 'flag' && 'flag'].filter(Boolean).join(' ')}>
    {control}
    {field.endsWith('.approval_threshold_usd') && <small id={`${id}-help`}>{disabled ? 'Approval is off for this workflow.' : 'Runs that spent less at the review step skip approval. $0 means every run.'}</small>}
    {meta}
    {error && <p className="error" id={`${id}-error`}>{error}</p>}
  </div>;
}

function ReadOnlySettings({ session, budgetsOnly }: { session: Session; budgetsOnly: boolean }) {
  const team = useData<Team>(session.csrfToken, '/v1/team');
  const policies = useData<{ workflows: Record<string, Policy> }>(session.csrfToken, '/v1/workflow-policies');
  const fields: Record<string, SettingValue> = {};
  if (team.data) {
    fields.cost_limit_usd = team.data.cost_limit_usd;
    for (const project of team.data.projects) fields[`project_budgets.${project}`] = team.data.project_budgets?.[project] ?? null;
    for (const [alias, model] of Object.entries(team.data.model_routes || {})) fields[`model_routes.${alias}`] = model;
  }
  for (const [name, policy] of Object.entries(policies.data?.workflows || {})) {
    const values = { cost_limit_usd: policy.costLimitUsd, token_limit: policy.tokenLimit, approval_required: policy.approvalRequired ?? true, approval_threshold_usd: policy.approvalThresholdUsd ?? 0, approver_role: policy.approverRole ?? 'approver', allowed_providers: policy.allowedProviders };
    for (const [field, value] of Object.entries(values)) fields[`workflows.${name}.${field}`] = value;
  }
  const groups = group(Object.keys(fields), budgetsOnly);
  const list = (names: string[]) => <dl className="setting-list">{names.map(field => <div key={field}><dt>{field.startsWith('model_routes.') ? <code>{visible(field)}</code> : visible(field)}</dt><dd>{format(field, fields[field])}</dd></div>)}</dl>;
  return <section className={budgetsOnly ? 'settings budgets-only' : 'settings'}>
    {budgetsOnly && <h2>Budgets</h2>}
    <p className="callout">Read-only. Your team admin can change these settings.</p>
    <ErrorMessage message={team.error || policies.error}/>
    {!team.data && !team.error && <Loading/>}
    {groups.team.length > 0 && <SettingsGroup nested={budgetsOnly} title={budgetsOnly ? 'Team and projects' : 'Budgets'} note="Team and project budgets reset at the start of each UTC month.">{list(groups.team)}</SettingsGroup>}
    {groups.workflows.length > 0 && <SettingsGroup nested={budgetsOnly} title={budgetsOnly ? 'Run budgets' : 'Workflows'} note={budgetsOnly ? 'Each run of a workflow stays within these limits.' : 'Approval rules apply at each workflow’s review step.'}>
      {groups.workflows.map(([workflow, names]) => <div className="setting-row" key={workflow}><h4>{workflowName(workflow)}</h4>{list(names)}</div>)}
    </SettingsGroup>}
    {groups.routes.length > 0 && <SettingsGroup nested={budgetsOnly} title="Model routes" note="Which approved model each alias calls.">{list(groups.routes)}</SettingsGroup>}
  </section>;
}
