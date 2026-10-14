import { useEffect, useRef, useState } from 'react';
import { DeploymentChecklist } from './deployment';
import { api, date, policyCache, useData, useRevision, workflowName, type Policy, type Session } from './api';
import { AdminOnly, Badge, Empty, ErrorMessage, Loading, NumberInput, PageHeader, Refresh } from './ui';
import { WorkflowRegistry } from './workflows';

type Template = { id: string; version: string; workflow: string; name: string; description: string; installable: boolean; installed_version: string | null };
type Secret = { name: string; version: number; updated_at: number };
type Retention = { revision: number; run_seconds: number; content_seconds: number; audit_seconds: number };
type DataStatus = { team_id: string; status: string; external_follow_up: string[] };
type Telemetry = { traces_enabled: boolean; metrics_enabled: boolean; service_name: string; protocol: string };

export function Templates({ session }: { session: Session }) {
  const [revision, setRevision] = useState(0);
  const result = useData<Template[]>(session.csrfToken, '/v1/workflow-templates', revision);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  // Installing a template changes the team's configuration, which the workflow registry below also saves to.
  const [installed, setInstalled] = useState(0);
  return <><PageHeader title="Workflow templates" subtitle="Install a reviewed version, then start a run with your team’s policy."><Refresh busy={result.loading && Boolean(result.data)} onClick={() => setRevision(v => v + 1)}/></PageHeader>
    <ErrorMessage message={error || result.error} retry={result.error && !error ? () => setRevision(v => v + 1) : undefined}/><p className="status" role="status">{notice}</p>
    {!result.data ? !result.error && <Loading text="Loading templates…"/> : !result.data.length ? <div className="panel"><Empty title="No templates available"><p>Your gateway has no workflow templates. Register your own workflow below, or ask your operator to add templates.</p></Empty></div> : <div className="template-gallery">{result.data.map(template => <article className="panel template-card" key={`${template.id}:${template.version}`}>
      <div className="section-heading"><h2>{template.name}</h2><Badge value={template.installed_version ? 'completed' : 'recorded'} text={template.installed_version ? 'Installed' : `v${template.version}`}/></div>
      <p>{template.description}</p><p className="muted">Version {template.version} · <a href={`https://github.com/RamazanKara/agentworkflows/blob/main/docs/templates.md#${template.id}`} target="_blank" rel="noreferrer">Workflow guide</a></p>
      {!template.installable ? <p className="muted">Your operator needs to register this workflow and approve its policy.</p> : template.installed_version ? <a className="button" href={`#new/${encodeURIComponent(template.workflow)}`}>Run {template.name.toLowerCase()}</a> : session.team.role !== 'admin' ? <p className="muted">A team admin can install this template.</p> : <button className="secondary" disabled={Boolean(busy)} onClick={async () => {
        setBusy(template.id); setError(''); setNotice('');
        try {
          await api(session.csrfToken, `/v1/workflow-templates/${encodeURIComponent(template.id)}/install`, { method: 'POST', body: JSON.stringify({ version: template.version }) });
          setNotice(`${template.name} ${template.version} installed.`); setRevision(v => v + 1); setInstalled(v => v + 1);
        } catch (value) { setError((value as Error).message); } finally { setBusy(''); }
      }}>{busy === template.id ? 'Installing…' : 'Install template'}</button>}
    </article>)}</div>}
    {session.team.role === 'admin' && <WorkflowRegistry session={session} changed={installed}/>}
  </>;
}

export function WorkflowSecrets({ session }: { session: Session }) {
  if (session.team.role !== 'admin') return <AdminOnly title="Workflow secrets"><p>Ask your team admin to manage workflow secrets.</p></AdminOnly>;
  return <SecretManagement session={session}/>;
}

function SecretManagement({ session }: { session: Session }) {
  const policies = useData<{ workflows: Record<string, Policy> }>(session.csrfToken, '/v1/workflow-policies', 0, policyCache);
  const [workflow, setWorkflow] = useState('');
  const names = Object.keys(policies.data?.workflows || {});
  const selected = names.includes(workflow) ? workflow : names[0];
  return <><PageHeader title="Workflow secrets" subtitle="Store encrypted credentials and rotate them without restarting workers."/>
    <ErrorMessage message={policies.error}/>
    {!policies.data ? !policies.error && <Loading text="Loading workflows…"/> : !selected ? <Empty title="No workflows configured"><p>Register a workflow before adding its secrets.</p></Empty> : <>
      <div className="panel form-panel"><label>Workflow<select value={selected} onChange={event => setWorkflow(event.target.value)}>{names.map(name => <option key={name} value={name}>{workflowName(name)}</option>)}</select></label>
      <p className="muted">Only an authorized worker activity in a running {workflowName(selected).toLowerCase()} workflow can read these values. Listings and audit receipts show metadata only.</p></div>
      <SecretEditor key={selected} session={session} workflow={selected}/>
    </>}
  </>;
}

function SecretEditor({ session, workflow }: { session: Session; workflow: string }) {
  const [revision, setRevision] = useState(0);
  const path = `/v1/workflows/${encodeURIComponent(workflow)}/secrets`;
  const result = useData<Secret[]>(session.csrfToken, path, revision);
  const [editing, setEditing] = useState<Secret>();
  const [name, setName] = useState('');
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  // Rotating starts in the value field, wherever the list left the reader.
  const secretValue = useRef<HTMLInputElement>(null);
  useEffect(() => { if (editing) secretValue.current?.focus(); }, [editing]);
  return <><ErrorMessage message={error || result.error} retry={result.error && !error ? () => setRevision(v => v + 1) : undefined}/><p role="status" className="status">{notice}</p>
    {!result.data ? !result.error && <Loading text="Loading secrets…"/> : <div className="secret-list">{result.data.length === 0 ? <p className="muted">No secrets saved for this workflow.</p> : result.data.map(secret => <article className="panel secret-row" key={secret.name}>
      <div><h2 className="secret-name">{secret.name}</h2><p className="muted">Version {secret.version} · Updated {date(secret.updated_at)}</p></div>
      <button className="secondary" onClick={() => { setEditing(secret); setName(secret.name); setValue(''); setError(''); setNotice(''); }}>Rotate secret</button>
    </article>)}</div>}
    <form className="panel form-panel" onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError(''); setNotice('');
      try {
        const saved = await api<Secret>(session.csrfToken, `${path}/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify({ value, expected_version: editing?.version ?? 0 }) });
        setValue(''); setName(''); setEditing(undefined); setRevision(v => v + 1); setNotice(`${saved.name} saved as version ${saved.version}.`);
      } catch (failure) { setError((failure as Error).message); setValue(''); } finally { setBusy(false); }
    }}><h2>{editing ? 'Rotate secret' : 'Add a secret'}</h2>
      <label>Secret name<input required pattern="[A-Za-z][A-Za-z0-9_\-]{0,63}" autoComplete="off" readOnly={Boolean(editing)} value={name} onChange={event => setName(event.target.value)}/></label>
      <label>{editing ? 'New secret value' : 'Secret value'}<input ref={secretValue} required type="password" autoComplete="new-password" value={value} onChange={event => setValue(event.target.value)}/></label>
      <p className="muted">A rotation replaces the old value immediately. Calls that already read it may still be using it.</p>
      <div className="actions"><button disabled={busy || !result.data}>{busy ? 'Saving…' : editing ? 'Save rotation' : 'Save secret'}</button>{editing && <button type="button" className="secondary" onClick={() => { setEditing(undefined); setName(''); setValue(''); }}>Cancel</button>}</div>
    </form>
  </>;
}

export function TeamData({ session }: { session: Session }) {
  if (session.team.role !== 'admin') return <AdminOnly title="Data & privacy"><p>Ask your team admin to manage retention and team data.</p></AdminOnly>;
  return <DataManagement session={session}/>;
}

function DataManagement({ session }: { session: Session }) {
  const [revision, setRevision] = useState(0);
  const retention = useData<Retention>(session.csrfToken, '/v1/team/retention', revision);
  const state = useData<DataStatus>(session.csrfToken, '/v1/team/data', revision);
  const telemetry = useData<Telemetry>(session.csrfToken, '/v1/team/telemetry', revision);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [busy, setBusy] = useState(false);
  const [erased, setErased] = useState(false);
  // Leaving the page drops an export in progress instead of downloading it later.
  const exporting = useRef<AbortController | null>(null);
  useEffect(() => () => exporting.current?.abort(), []);
  return <><PageHeader title="Data & privacy" subtitle="Control retention, export team data, and inspect telemetry."/>
    <ErrorMessage message={error || retention.error || state.error || telemetry.error}/><p role="status" className="status">{notice}</p>
    {erased ? <div className="callout"><h2>Team data erased</h2><p>New work is blocked. Complete the external follow-up below and remove the team’s static access configuration.</p></div> : <>
      {retention.data ? <RetentionForm key={retention.data.revision} session={session} retention={retention.data} onSaved={() => { setRevision(v => v + 1); setNotice('Retention saved.'); }}/> : !retention.error && <Loading text="Loading retention…"/>}
      <section className="panel form-panel"><h2>Export team data</h2><p>Download retained runs, step content, audit entries, key metadata, team settings, and Temporal histories. Secret values and authentication tokens are excluded.</p>
        <button disabled={busy || state.data?.status !== 'active'} onClick={async () => {
          setBusy(true); setError(''); setNotice('');
          const controller = new AbortController(); exporting.current = controller;
          try {
            const data = await api(session.csrfToken, '/v1/team/data/export', { signal: controller.signal });
            if (controller.signal.aborted) return;
            const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
            const link = document.createElement('a'); link.href = url; link.download = 'team-data.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
            setNotice('Team data exported. Store the download securely.');
          } catch (failure) { if (!controller.signal.aborted) setError((failure as Error).message); } finally { setBusy(false); }
        }}>Export JSON</button>
      </section>
      <section className="panel form-panel"><h2>Erase team data</h2><p>Stop clients, cancel active runs and batches, and pause triggers first. Erasure removes team records and Temporal histories, revokes managed keys, and blocks new work. This cannot be undone.</p>
        {state.data && state.data.status !== 'active' && <p className="note-warn">Erasure state: {state.data.status}. Use your bootstrap admin credential to resume if needed.</p>}
        <form onSubmit={async event => {
          event.preventDefault(); setBusy(true); setError('');
          try { await api(session.csrfToken, '/v1/team/data', { method: 'DELETE', body: JSON.stringify({ confirm_team: confirmation }) }); setErased(true); setNotice('Erasure complete for gateway and Temporal data.'); }
          catch (failure) { setError((failure as Error).message); } finally { setBusy(false); }
        }}><label>Type {session.team.team_id} to confirm<input autoComplete="off" required value={confirmation} onChange={event => setConfirmation(event.target.value)}/></label>
          <button className="danger" disabled={busy || confirmation !== session.team.team_id}>Erase team data</button></form>
      </section>
    </>}
    <DeploymentChecklist session={session}/>
    {state.data && <section className="panel form-panel"><h2>External follow-up</h2><p>These records have separate owners and retention controls:</p><ul>{state.data.external_follow_up.map(item => <li key={item}>{item}</li>)}</ul></section>}
    {telemetry.data && <section className="panel form-panel"><h2>OpenTelemetry export</h2><dl className="data-facts"><div><dt>Traces</dt><dd>{telemetry.data.traces_enabled ? 'Enabled' : 'Disabled'}</dd></div><div><dt>Metrics</dt><dd>{telemetry.data.metrics_enabled ? 'Enabled' : 'Disabled'}</dd></div><div><dt>Protocol</dt><dd>{telemetry.data.protocol}</dd></div></dl>
      <p className="muted">Configuration is shown here. Check your collector and Grafana for delivery and current data.</p>
    </section>}
  </>;
}

function RetentionForm({ session, retention, onSaved }: { session: Session; retention: Retention; onSaved: () => void }) {
  const fields = [['run_seconds', 'Run history'], ['content_seconds', 'Step content'], ['audit_seconds', 'Audit view']] as const;
  type Field = typeof fields[number][0];
  // Days are edited as typed, so clearing a field to enter a new number does not snap it to a minimum.
  const [value, setValue] = useState(() => Object.fromEntries(fields.map(([field]) => [field, Number((retention[field] / 86400).toFixed(3))])) as Record<Field, number | ''>);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const at = useRevision(retention.revision);
  return <form className="panel form-panel" onSubmit={async event => {
    event.preventDefault(); setBusy(true); setError('');
    const body = Object.fromEntries(fields.map(([field]) => [field, Math.max(60, Math.round(Number(value[field]) * 86400))]));
    try { await api(session.csrfToken, '/v1/team/retention', { method: 'PUT', headers: { 'If-Match': String(at ?? retention.revision) }, body: JSON.stringify(body) }); onSaved(); }
    catch (failure) { setError((failure as Error).message); } finally { setBusy(false); }
  }}><h2>Retention</h2><p>Run retention starts when a run closes. Content changes apply to new captures. Audit view changes apply when records are read or pruned. Temporal and backups use separate retention.</p>
    {fields.map(([field, label]) => <label key={field}>{label} (days)<NumberInput required min={0.001} max={365} step="any" value={value[field]} onChange={next => setValue(previous => ({ ...previous, [field]: next }))}/></label>)}
    <ErrorMessage message={error}/><button disabled={busy}>{busy ? 'Saving…' : 'Save retention'}</button>
  </form>;
}
