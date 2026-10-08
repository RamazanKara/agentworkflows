import { useState } from 'react';
import { api, date, providerName, useData, workflowName, type ApiError, type Policy, type Session, type SettingValue, type Team, type TeamSettings } from './api';
import { ErrorMessage, Loading, PageHeader } from './ui';

const names: Record<string, string> = {
  token_limit: 'Token limit', cost_limit_usd: 'Budget (USD)', approval_required: 'Require approval',
  approval_threshold_usd: 'Approval threshold (USD)', approver_role: 'Approver role', allowed_providers: 'Allowed providers',
};
const display = (value: SettingValue) => value === null ? 'No limit' : Array.isArray(value) ? value.map(providerName).join(', ') || 'None' : String(value);
const label = (field: string) => {
  if (field === 'cost_limit_usd') return 'Team monthly budget (USD)';
  if (field.startsWith('project_budgets.')) return `${field.slice(16)} monthly budget (USD)`;
  if (field.startsWith('model_routes.')) return `${field.slice(13)} model route`;
  const parts = field.slice(10).split('.');
  const name = parts.pop()!;
  return `${workflowName(parts.join('.'))} · ${names[name]}`;
};
const budgetField = (field: string) => field === 'cost_limit_usd' || field.startsWith('project_budgets.') || /\.(cost_limit_usd|token_limit)$/.test(field);

export function TeamConfiguration({ session }: { session: Session }) {
  return <><PageHeader title="Team settings" subtitle="Budgets, approval rules and model routes for your team."/><SettingsPanel session={session}/></>;
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
      setMessage(field ? `${label(field)} reset to policy default.` : 'Settings saved.');
      onSaved?.();
    } catch (value) {
      const failure = value as ApiError;
      setConflict(failure.status === 409);
      setError(failure.status === 409 ? 'Settings changed since you opened this page. Reload and review the latest values before saving again.' : failure.message);
      setErrors(Object.fromEntries((failure.fields || []).map(item => [item.field, item.message])));
    } finally { setBusy(false); }
  };
  return <section className="panel form-panel"><h2>{budgetsOnly ? 'Budgets' : 'Team policy overrides'}</h2>
    <p>Changes apply to the next governed request. Team and project dollar budgets reset each UTC month. Run budgets also stay within the worker’s requested limits.</p>
    {!budgetsOnly && <p>Approval rules apply at the workflow’s review gate. The threshold is the run’s spend in USD at that gate; zero means every review requires approval.</p>}
    <ErrorMessage message={error || result.error}/>
    {(conflict || result.error) && <button className="secondary" type="button" disabled={busy} onClick={reload}>Reload settings</button>}
    {!settings && !result.error && <Loading/>}
    {settings && <form onSubmit={event => { event.preventDefault(); void submit(); }}>
      <fieldset disabled={busy || conflict} style={{ border: 0, padding: 0, margin: 0 }}>
        {Object.entries(settings.fields).filter(([field]) => !budgetsOnly || budgetField(field)).map(([field, state]) => {
          const value = field in draft ? draft[field] : state.value;
          const setValue = (next: SettingValue) => setDraft(previous => ({ ...previous, [field]: next }));
          const id = `setting-${field}`;
          const choices = field.startsWith('model_routes.') ? settings.routes : field.endsWith('.approver_role') ? settings.approver_roles : undefined;
          return <div className="field" key={field}>
            {typeof value === 'boolean' ? <label className="check"><input id={id} type="checkbox" checked={value} onChange={event => setValue(event.target.checked)}/>{label(field)}</label>
              : <><label htmlFor={id}>{label(field)}</label>
                {choices ? <select id={id} value={String(value)} onChange={event => setValue(event.target.value)}>{choices.map(option => <option key={option}>{option}</option>)}</select>
                  : Array.isArray(value) ? <select id={id} multiple value={value} onChange={event => setValue(Array.from(event.target.selectedOptions, option => option.value))}>{settings.providers.map(provider => <option key={provider} value={provider}>{providerName(provider)}</option>)}</select>
                    : <input id={id} type="number" min="0" max={field.endsWith('.token_limit') ? 1000000000 : 1000000} step={field.endsWith('.token_limit') ? 1 : 'any'} placeholder="No limit" value={value ?? ''} required={field in draft} aria-invalid={Boolean(errors[field])} aria-describedby={errors[field] ? `${id}-error` : undefined} onChange={event => setValue(event.target.value === '' ? '' : Number(event.target.value))}/>}</>}
            <small>{state.source === 'override' ? 'Override' : 'Policy'} · Policy default: {display(state.policy_default)}</small>
            {state.source === 'override' && <button className="link" type="button" onClick={() => void submit(field)} aria-label={`Reset ${label(field)} to policy default`}>Reset to policy default</button>}
            {errors[field] && <p className="error" id={`${id}-error`}>{errors[field]}</p>}
          </div>;
        })}
        <button type="submit" disabled={!Object.keys(draft).length}>{busy ? 'Saving…' : 'Save settings'}</button>
      </fieldset>
      {settings.updated_at && <p className="muted">Last changed by {settings.updated_by} · {date(settings.updated_at)}</p>}
    </form>}
    <p role="status">{message}</p>
  </section>;
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
    const values = { token_limit: policy.tokenLimit, cost_limit_usd: policy.costLimitUsd, approval_required: policy.approvalRequired ?? true, approval_threshold_usd: policy.approvalThresholdUsd ?? 0, approver_role: policy.approverRole ?? 'approver', allowed_providers: policy.allowedProviders };
    for (const [field, value] of Object.entries(values)) fields[`workflows.${name}.${field}`] = value;
  }
  return <section className="panel form-panel"><h2>{budgetsOnly ? 'Budgets' : 'Team settings'}</h2><p>Read-only. Your team admin can change these settings.</p>
    <ErrorMessage message={team.error || policies.error}/>
    {!team.data && !team.error && <Loading/>}
    <dl>{Object.entries(fields).filter(([field]) => !budgetsOnly || budgetField(field)).map(([field, value]) => <div key={field}><dt>{label(field)}</dt><dd>{display(value)}</dd></div>)}</dl>
  </section>;
}
