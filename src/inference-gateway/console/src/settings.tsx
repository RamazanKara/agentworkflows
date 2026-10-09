import { useEffect, useState, type ReactNode } from 'react';
import { AlertSettings } from './alerts';
import { api, date, label as titleCase, money, number, providerName, useData, workflowName, type ApiError, type Models, type Policy, type Session, type SettingField, type SettingValue, type Team, type TeamSettings } from './api';
import { DotList, ErrorMessage, Loading, NumberInput, PageHeader } from './ui';

// Field keys: cost_limit_usd, project_budgets.<project>, workflows.<Workflow>.<field>, model_routes.<alias>.
type Kind = 'usd' | 'tokens' | 'count' | 'minutes' | 'flag' | 'role' | 'route' | 'providers' | 'capture';
const workflowFields: Record<string, [string, Kind]> = {
  cost_limit_usd: ['Budget per run', 'usd'], token_limit: ['Token limit per run', 'tokens'],
  approval_required: ['Require approval', 'flag'], approval_threshold_usd: ['Approval threshold', 'usd'],
  approver_role: ['Approver role', 'role'], allowed_providers: ['Allowed providers', 'providers'],
  required_approvals: ['Required reviewers', 'count'], approval_timeout_seconds: ['Approval expiry (minutes)', 'minutes'],
  capture_content: ['Step content capture', 'capture'],
};
const cardRows = [['cost_limit_usd', 'token_limit'], ['approval_required', 'approval_threshold_usd', 'approver_role'], ['required_approvals', 'approval_timeout_seconds'], ['allowed_providers', 'capture_content']];
const workflowOf = (field: string) => field.startsWith('workflows.') ? field.slice(10, field.lastIndexOf('.')) : '';
const leaf = (field: string) => field.slice(field.lastIndexOf('.') + 1);
const kind = (field: string): Kind => field.startsWith('model_routes.') ? 'route'
  : field === 'capture_content' ? 'capture'
  : workflowOf(field) ? workflowFields[leaf(field)]?.[1] ?? 'usd' : 'usd';
// What a person reads next to the control; the accessible name adds the workflow and the unit.
const visible = (field: string) => field === 'cost_limit_usd' ? 'Team monthly budget'
  : field === 'soft_cost_limit_usd' ? 'Team monthly soft limit' : field === 'capture_content' ? 'Default step content capture'
  : field.startsWith('project_budgets.') ? `Project ${field.slice(16)} monthly budget`
  : field.startsWith('model_routes.') ? field.slice(13)
  : workflowFields[leaf(field)]?.[0] ?? field;
const accessible = (field: string) => field.startsWith('model_routes.') ? `Model for ${field.slice(13)}`
  : `${workflowOf(field) ? `${workflowName(workflowOf(field))} · ` : ''}${visible(field)}${kind(field) === 'usd' ? ' (USD)' : ''}`;
const format = (field: string, value: SettingValue) => value === null || value === '' ? 'No limit'
  : Array.isArray(value) ? value.map(providerName).join(', ') || 'Team policy'
  : typeof value === 'boolean' ? value ? 'Required' : 'Not required'
  : kind(field) === 'usd' ? money(Number(value)) : kind(field) === 'tokens' ? `${number(Number(value))} tokens`
  : kind(field) === 'minutes' ? `${number(Number(value) / 60)} minutes`
  : kind(field) === 'capture' && value === 'none' ? 'Off'
  : ['role', 'capture'].includes(kind(field)) ? titleCase(String(value)) : String(value);
const budgetField = (field: string) => ['cost_limit_usd', 'soft_cost_limit_usd'].includes(field) || field.startsWith('project_budgets.') || /\.(cost_limit_usd|token_limit)$/.test(field);
// An emptied input and a stored "no limit" are the same value.
const normal = (value: SettingValue) => Array.isArray(value) ? [...value].sort() : value === '' ? null : value;
const same = (a: SettingValue, b: SettingValue) => JSON.stringify(normal(a)) === JSON.stringify(normal(b));
const RouteName = ({ field }: { field: string }) => <>Model for <code className="alias">{visible(field)}</code></>;
const Custom = () => <span className="badge custom" title="Differs from team policy">Custom</span>;
const Unsaved = () => <span className="badge unsaved">Unsaved</span>;

type Groups = { team: string[]; capture: string[]; workflows: [string, string[]][]; routes: string[] };
function group(fields: string[], budgetsOnly: boolean): Groups {
  const shown = fields.filter(field => !budgetsOnly || budgetField(field));
  const workflows = new Map<string, string[]>();
  for (const field of shown.filter(workflowOf)) workflows.set(workflowOf(field), [...workflows.get(workflowOf(field)) || [], field]);
  // Budget first, then tokens and approval, in the order of workflowFields.
  const order = Object.keys(workflowFields);
  for (const names of workflows.values()) names.sort((a, b) => order.indexOf(leaf(a)) - order.indexOf(leaf(b)));
  return { team: shown.filter(field => ['cost_limit_usd', 'soft_cost_limit_usd'].includes(field) || field.startsWith('project_budgets.')), capture: shown.filter(field => field === 'capture_content'), workflows: [...workflows], routes: shown.filter(field => field.startsWith('model_routes.')) };
}
const rows = (fields: string[]) => cardRows.map(row => fields.filter(field => row.includes(leaf(field)))).filter(row => row.length > 0);

export function TeamConfiguration({ session }: { session: Session }) {
  return <><PageHeader title="Team settings" subtitle="Budgets, approval rules and model routes. Changes apply to the next request; no restart."/><SettingsPanel session={session}/>{session.team.role === 'admin' && <AlertSettings session={session}/>}</>;
}

export function SettingsPanel({ session, budgetsOnly = false, onSaved }: { session: Session; budgetsOnly?: boolean; onSaved?: () => void }) {
  return session.team.role === 'admin' ? <SettingsEditor session={session} budgetsOnly={budgetsOnly} onSaved={onSaved}/>
    : <ReadOnlySettings session={session} budgetsOnly={budgetsOnly}/>;
}

function SettingsEditor({ session, budgetsOnly, onSaved }: { session: Session; budgetsOnly: boolean; onSaved?: () => void }) {
  const [revision, setRevision] = useState(0);
  const result = useData<TeamSettings>(session.csrfToken, '/v1/team/settings', revision);
  // Route options name their provider, so "research" on a fresh install reads "research · OpenAI".
  const models = useData<Models>(session.csrfToken, '/v1/models');
  const owners = Object.fromEntries((models.data?.data || []).map(model => [model.id, providerName(model.owned_by)]));
  const [saved, setSaved] = useState<TeamSettings>();
  const settings = saved || result.data;
  // Edits and resets both wait for Save, so Discard undoes either.
  const [draft, setDraft] = useState<Record<string, SettingValue>>({});
  const [resets, setResets] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [justSaved, setJustSaved] = useState(false);
  useEffect(() => {
    if (!justSaved) return;
    const timer = window.setTimeout(() => setJustSaved(false), 4000);
    return () => window.clearTimeout(timer);
  }, [justSaved]);
  const groups = settings && group(Object.keys(settings.fields), budgetsOnly);
  const ordered = groups ? [...groups.team, ...groups.capture, ...groups.workflows.flatMap(([, names]) => names), ...groups.routes] : [];
  const pending = ordered.filter(name => name in draft || resets.includes(name));
  const changes = pending.length;
  const discard = () => { setDraft({}); setResets([]); setErrors({}); setError(''); };
  const reload = () => { discard(); setSaved(undefined); setConflict(false); setJustSaved(false); setRevision(v => v + 1); };
  const current = (name: string) => name in draft ? draft[name] : resets.includes(name) ? settings!.fields[name].policy_default : settings!.fields[name]?.value;
  const change = (name: string, next: SettingValue) => {
    setJustSaved(false);
    setResets(previous => previous.filter(item => item !== name));
    setDraft(previous => {
      const { [name]: _, ...rest } = previous;
      return same(next, settings!.fields[name].value) ? rest : { ...rest, [name]: next };
    });
  };
  const reset = (name: string) => {
    setJustSaved(false);
    setDraft(previous => { const { [name]: _, ...rest } = previous; return rest; });
    if (settings!.fields[name].source === 'override') setResets(previous => [...previous.filter(item => item !== name), name]);
  };
  const showFirstChange = () => {
    const element = document.getElementById(`setting-${pending[0]}`);
    const details = element?.closest('details');
    if (details) details.open = true;
    const target = element?.matches('input, select') ? element : element?.querySelector<HTMLElement>('input');
    target?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: 'center' });
  };
  const save = async () => {
    if (!settings || !changes) return;
    setBusy(true); setError(''); setErrors({}); setJustSaved(false);
    let latest = settings;
    let remaining = resets;
    try {
      for (const name of resets) {
        latest = await api<TeamSettings>(session.csrfToken, `/v1/team/settings/${encodeURIComponent(name)}`, { method: 'DELETE', headers: { 'If-Match': String(latest.revision) } });
        remaining = remaining.filter(item => item !== name);
      }
      if (Object.keys(draft).length) latest = await api<TeamSettings>(session.csrfToken, '/v1/team/settings', {
        method: 'PATCH', headers: { 'If-Match': String(latest.revision) }, body: JSON.stringify({ fields: draft }),
      });
      setSaved(latest); setDraft({}); setResets([]); setJustSaved(true);
      onSaved?.();
    } catch (value) {
      // Resets that went through stay saved; the rest stay pending with the edits.
      if (latest !== settings) setSaved(latest);
      setResets(remaining);
      const failure = value as ApiError;
      setConflict(failure.status === 409);
      setError(failure.status === 409 ? 'Settings changed since you opened this page. Reload and review the latest values before saving again.' : failure.message);
      setErrors(Object.fromEntries((failure.fields || []).map(item => [item.field, item.message])));
    } finally { setBusy(false); }
  };
  // Offer the team's providers plus any a workflow already allows; local backends appear only when in use.
  const offered = (state: SettingField) => settings!.providers.filter(provider => session.team.providers.includes(provider)
    || [state.value, state.policy_default].some(value => Array.isArray(value) && value.includes(provider)));
  const field = (name: string) => <SettingControl key={name} field={name} state={settings!.fields[name]} value={current(name)}
    pending={pending.includes(name)} resetting={resets.includes(name)} error={errors[name]} providers={offered(settings!.fields[name])} owners={owners}
    choices={kind(name) === 'capture' ? ['none', 'redacted', 'full'] : name.startsWith('model_routes.') ? settings!.routes : name.endsWith('.approver_role') ? settings!.approver_roles : undefined}
    disabled={name.endsWith('.approval_threshold_usd') && current(name.replace(/threshold_usd$/, 'required')) === false}
    onChange={next => change(name, next)} onReset={() => reset(name)}/>;
  const docked = changes > 0 || justSaved || Boolean(error);
  return <section className={budgetsOnly ? 'settings budgets-only' : 'settings'} aria-labelledby={budgetsOnly ? 'budgets-heading' : undefined}>
    {budgetsOnly && <h2 id="budgets-heading">Budgets</h2>}
    <ErrorMessage message={result.error}/>
    {result.error && <button className="secondary" type="button" onClick={reload}>Reload settings</button>}
    {!settings && !result.error && <Loading/>}
    {settings && groups && <form onSubmit={event => { event.preventDefault(); void save(); }}>
      <fieldset className="plain" disabled={busy || conflict}>
        {groups.team.length > 0 && <SettingsGroup nested={budgetsOnly} title={budgetsOnly ? 'Team and projects' : 'Budgets'} note="Team and project budgets reset at the start of each UTC month.">
          <div className="setting-grid">{groups.team.map(field)}</div>
          <p className="muted">The soft limit alerts your team. The monthly budget is a hard limit: calls that would exceed it return 429. Blank team limits are unlimited.</p>
        </SettingsGroup>}
        {groups.capture.length > 0 && <SettingsGroup nested={false} title="Step content" note="Off disables capture. Redacted masks configured secrets and personal data. Full stores content after request guardrails. Changes affect future steps; existing content keeps its retention deadline. Workflow overrides take precedence.">
          <div className="setting-grid">{groups.capture.map(field)}</div>
        </SettingsGroup>}
        {groups.workflows.length > 0 && (budgetsOnly
          ? <SettingsGroup nested title="Run budgets" note="Each run of a workflow stays within these limits and within what its worker requests.">
            {/* On wide screens the rows read as a table: one header, one line per workflow. */}
            <div className="run-budgets"><div className="run-budgets-head" aria-hidden="true"><span>Workflow</span><span>Budget per run</span><span>Token limit per run</span></div>
              {groups.workflows.map(([workflow, fields]) => <div className="setting-row" key={workflow}><h4>{workflowName(workflow)}</h4><div className="setting-grid">{fields.map(field)}</div></div>)}</div>
          </SettingsGroup>
          : <SettingsGroup nested={false} title="Workflows" note="Approval rules are saved when a run starts. Each reviewer counts once; any rejection stops approval. Approvals expire 1 minute to 7 days after the review step. Changes apply to new runs.">
            {groups.workflows.map(([workflow, fields]) => <WorkflowCard key={workflow} workflow={workflow} open={groups.workflows.length <= 2}
              custom={fields.some(name => settings.fields[name].source === 'override')} unsaved={fields.some(name => pending.includes(name))}
              summary={summary(workflow, current)}>
              {rows(fields).map(row => <div className="setting-grid card-row" key={row[0]}>{row.map(field)}</div>)}
            </WorkflowCard>)}
          </SettingsGroup>)}
        {groups.routes.length > 0 && <SettingsGroup nested={false} title="Model routes" note="Choose which approved model each alias calls. New routes and prices are added by your operator.">
          <div className="setting-grid">{groups.routes.map(field)}</div>
        </SettingsGroup>}
      </fieldset>
      <div className={docked ? 'save-bar docked' : 'save-bar'}>
        {error && <div className="save-error"><ErrorMessage message={error}/>{conflict && <button className="secondary" type="button" onClick={reload}>Reload settings</button>}</div>}
        <p role="status">{changes ? <button type="button" className="link" onClick={showFirstChange}>{changes} unsaved {changes === 1 ? 'change' : 'changes'}</button>
          : justSaved ? <span className="saved">Settings saved. They apply to the next request.</span>
          : settings.updated_at ? <DotList items={[`Last changed by ${settings.updated_by}`, date(settings.updated_at)]}/> : 'Using your team policy. Nothing changed here yet.'}</p>
        {changes > 0 && <button type="button" className="secondary" disabled={busy} onClick={discard}>Discard</button>}
        {(changes > 0 || !justSaved) && <button type="submit" disabled={!changes || busy || conflict}>{busy ? 'Saving…' : 'Save settings'}</button>}
      </div>
    </form>}
  </section>;
}

// ["$5.00 per run", "Approval from $0.50 by admins", "OpenAI, Anthropic"]
function summary(workflow: string, value: (field: string) => SettingValue) {
  const key = (name: string) => `workflows.${workflow}.${name}`;
  const budget = value(key('cost_limit_usd'));
  const threshold = Number(value(key('approval_threshold_usd')) || 0);
  const admins = value(key('approver_role')) === 'admin' ? ' by admins' : '';
  const providers = value(key('allowed_providers'));
  return [budget === null || budget === '' || budget === undefined ? 'No run budget' : `${money(Number(budget))} per run`,
    value(key('approval_required')) === false ? 'No approval' : threshold > 0 ? `Approval from ${money(threshold)}${admins}` : `Approval on every run${admins}`,
    Array.isArray(providers) ? providers.map(providerName).join(', ') : ''].filter(Boolean);
}

// Groups are page sections on Team settings and subsections where Budgets is embedded in another page.
function SettingsGroup({ title, note, nested, children }: { title: string; note: string; nested: boolean; children: ReactNode }) {
  return <section className="panel settings-group">{nested ? <h3>{title}</h3> : <h2>{title}</h2>}<p className="muted">{note}</p>{children}</section>;
}

function WorkflowCard({ workflow, summary: parts, custom, unsaved, open, children }: { workflow: string; summary: string[]; custom: boolean; unsaved: boolean; open: boolean; children: ReactNode }) {
  return <details className="workflow-card" open={open}><summary>
    <span className="workflow-card-title">{workflowName(workflow)}{unsaved ? <Unsaved/> : custom && <Custom/>}</span>
    <small><DotList items={parts}/></small>
  </summary>{children}</details>;
}

function SettingControl({ field, state, value, pending, resetting, error, providers, owners, choices, disabled, onChange, onReset }: {
  field: string; state: SettingField; value: SettingValue; pending: boolean; resetting: boolean; error?: string; providers: string[]; owners: Record<string, string>; choices?: string[]; disabled: boolean;
  onChange: (value: SettingValue) => void; onReset: () => void;
}) {
  const id = `setting-${field}`;
  const type = kind(field);
  const prefix = workflowOf(field) ? `${workflowName(workflowOf(field))} · ` : '';
  const name = <>{prefix && <span className="visually-hidden">{prefix}</span>}{type === 'route' ? <RouteName field={field}/> : visible(field)}{type === 'usd' && <span className="visually-hidden"> (USD)</span>}</>;
  // Policy values need no note; custom or unsaved values show the policy value and a way back to it.
  const meta = pending || state.source === 'override' ? <p className="setting-meta">
    {pending ? <Unsaved/> : <Custom/>}
    <span>{resetting ? 'Back to team policy' : `Team policy: ${format(field, state.policy_default)}`}</span>
    {!resetting && <button className="link" type="button" onClick={onReset} aria-label={`Reset ${accessible(field)} to policy default`}>Reset</button>}
  </p> : null;
  const described = [error && `${id}-error`, field.endsWith('.approval_threshold_usd') && `${id}-help`].filter(Boolean).join(' ') || undefined;
  let control: ReactNode;
  if (type === 'flag') control = <label className="check"><input id={id} type="checkbox" checked={value === true} onChange={event => onChange(event.target.checked)}/>{name}</label>;
  else if (type === 'providers') control = <fieldset className="choices" id={id}><legend>{name}</legend>{providers.map(provider => <label className="check" key={provider}>
    <input type="checkbox" checked={Array.isArray(value) && value.includes(provider)} onChange={event => onChange(event.target.checked
      ? [...(Array.isArray(value) ? value : []), provider] : (Array.isArray(value) ? value : []).filter(item => item !== provider))}/>{providerName(provider)}</label>)}</fieldset>;
  else if (choices) control = <><label htmlFor={id}>{name}</label><select id={id} value={String(value)} onChange={event => onChange(event.target.value)}>
    {choices.map(option => <option key={option} value={option}>{['role', 'capture'].includes(type) ? format(field, option) : owners[option] ? `${option} · ${owners[option]}` : option}</option>)}</select></>;
  else {
    const input = <NumberInput id={id} min={['count', 'minutes'].includes(type) ? 1 : 0} max={type === 'count' ? 10 : type === 'minutes' ? 10080 : type === 'tokens' ? 1000000000 : 1000000} step={['usd', 'minutes'].includes(type) ? 'any' : 1} placeholder="No limit"
      value={value === null || value === '' ? '' : Number(value) / (type === 'minutes' ? 60 : 1)} required={pending && !resetting && !['cost_limit_usd', 'soft_cost_limit_usd'].includes(field)} disabled={disabled} aria-invalid={Boolean(error)} aria-describedby={described}
      onChange={next => onChange(type === 'minutes' && next !== '' ? Math.round(next * 60) : next)}/>;
    control = <><label htmlFor={id}>{name}</label>{type === 'usd' ? <div className="prefixed"><span aria-hidden="true">$</span>{input}</div> : input}</>;
  }
  return <div className={['setting', pending && 'pending', type === 'flag' && 'flag'].filter(Boolean).join(' ')}>
    {control}
    {field.endsWith('.approval_threshold_usd') && <small id={`${id}-help`}>{disabled ? 'Approval is off for this workflow.' : 'Runs that have spent less than this by the review step skip approval. $0 means every run needs approval.'}</small>}
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
    fields.soft_cost_limit_usd = team.data.soft_cost_limit_usd ?? null;
    fields.capture_content = team.data.capture_content ?? 'none';
    for (const project of team.data.projects) fields[`project_budgets.${project}`] = team.data.project_budgets?.[project] ?? null;
    for (const [alias, model] of Object.entries(team.data.model_routes || {})) fields[`model_routes.${alias}`] = model;
  }
  for (const [name, policy] of Object.entries(policies.data?.workflows || {})) {
    const values = { cost_limit_usd: policy.costLimitUsd, token_limit: policy.tokenLimit, approval_required: policy.approvalRequired ?? true, approval_threshold_usd: policy.approvalThresholdUsd ?? 0, approver_role: policy.approverRole ?? 'approver', required_approvals: policy.requiredApprovals ?? 1, approval_timeout_seconds: policy.approvalTimeoutSeconds ?? 604800, allowed_providers: policy.allowedProviders, capture_content: policy.captureContent ?? team.data?.capture_content ?? 'none' };
    for (const [field, value] of Object.entries(values)) fields[`workflows.${name}.${field}`] = value;
  }
  const groups = group(Object.keys(fields), budgetsOnly);
  const term = (field: string) => field.startsWith('model_routes.') ? <RouteName field={field}/> : field.endsWith('.approval_required') ? 'Approval' : visible(field);
  const list = (names: string[], className = 'setting-list') => <dl className={className}>{names.map(field => <div key={field}><dt>{term(field)}</dt><dd>{format(field, fields[field])}</dd></div>)}</dl>;
  // With approval off, the threshold and approver say nothing.
  const shown = (workflow: string, names: string[]) => fields[`workflows.${workflow}.approval_required`] === false ? names.filter(name => !/\.(approval_threshold_usd|approver_role)$/.test(name)) : names;
  return <section className={budgetsOnly ? 'settings budgets-only' : 'settings'}>
    {budgetsOnly && <h2>Budgets</h2>}
    <p className="callout">Read-only. Your team admin can change these settings.</p>
    <ErrorMessage message={team.error || policies.error}/>
    {!team.data && !team.error && <Loading/>}
    {groups.team.length > 0 && <SettingsGroup nested={budgetsOnly} title={budgetsOnly ? 'Team and projects' : 'Budgets'} note="Team and project budgets reset at the start of each UTC month.">{list(groups.team)}</SettingsGroup>}
    {groups.capture.length > 0 && <SettingsGroup nested={false} title="Step content" note="Capture applies to future steps; workflow overrides take precedence.">{list(groups.capture)}</SettingsGroup>}
    {groups.workflows.length > 0 && (budgetsOnly
      ? <SettingsGroup nested title="Run budgets" note="Each run of a workflow stays within these limits.">
        <table className="stack numeric"><thead><tr><th>Workflow</th><th className="num">Budget per run</th><th className="num">Token limit per run</th></tr></thead>
          <tbody>{groups.workflows.map(([workflow]) => <tr key={workflow}><th scope="row">{workflowName(workflow)}</th>
            <td className="num" data-label="Budget per run">{format(`workflows.${workflow}.cost_limit_usd`, fields[`workflows.${workflow}.cost_limit_usd`] ?? null)}</td>
            <td className="num" data-label="Token limit per run">{format(`workflows.${workflow}.token_limit`, fields[`workflows.${workflow}.token_limit`] ?? null)}</td></tr>)}</tbody></table>
      </SettingsGroup>
      : <SettingsGroup nested={false} title="Workflows" note="Approval rules apply at each workflow’s review step.">
        {groups.workflows.map(([workflow, names]) => <WorkflowCard key={workflow} workflow={workflow} open={false} custom={false} unsaved={false}
          summary={summary(workflow, name => fields[name])}>{list(shown(workflow, names), 'setting-list card-list')}</WorkflowCard>)}
      </SettingsGroup>)}
    {groups.routes.length > 0 && <SettingsGroup nested={budgetsOnly} title="Model routes" note="Which approved model each alias calls.">{list(groups.routes)}</SettingsGroup>}
  </section>;
}
