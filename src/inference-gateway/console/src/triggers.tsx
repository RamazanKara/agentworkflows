import { useState } from 'react';
import { api, useData, workflowName, type Session } from './api';
import { Empty, ErrorMessage, Loading, PageHeader, Refresh } from './ui';

type Trigger = {
  workflow: string; name: string; kind: 'cron' | 'webhook'; project: string; cron: string;
  paused: boolean; configuration_paused: boolean; url?: string; next_fire_at?: string[];
  secret_configured?: boolean; error?: string;
};

export function Triggers({ session }: { session: Session }) {
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const result = useData<{ triggers: Trigger[] }>(session.token, '/v1/workflow-triggers', revision);
  const canEdit = ['admin', 'builder'].includes(session.team.role);
  async function toggle(trigger: Trigger) {
    setBusy(`${trigger.workflow}/${trigger.name}`); setError(''); setMessage('');
    try {
      await api(session.token, `/v1/workflow-triggers/${encodeURIComponent(trigger.workflow)}/${encodeURIComponent(trigger.name)}`, {
        method: 'PATCH', body: JSON.stringify({ paused: !trigger.paused }),
      });
      setMessage(`${trigger.name} ${trigger.paused ? 'resumed' : 'paused'}. Existing runs continue.`);
      setRevision(value => value + 1);
    } catch (value) { setError((value as Error).message); }
    finally { setBusy(''); }
  }
  return <><PageHeader title="Triggers" subtitle="Start workflows on a schedule or from a signed webhook."><Refresh onClick={() => setRevision(value => value + 1)}/></PageHeader>
    <ErrorMessage message={error || result.error} retry={() => setRevision(value => value + 1)}/>
    <p role="status">{message}</p>
    {!result.data && !result.error && <Loading/>}
    {result.data && (result.data.triggers.length ? <div className="panel"><div className="table-scroll" tabIndex={0} role="region" aria-label="Workflow triggers"><table className="stack">
      <thead><tr><th>Workflow / trigger</th><th>Project</th><th>Starts when</th><th>State</th><th>Action</th></tr></thead>
      <tbody>{result.data.triggers.map(trigger => <tr key={`${trigger.workflow}/${trigger.name}`}>
        <th scope="row">{workflowName(trigger.workflow)}<div className="muted">{trigger.name}</div></th><td data-label="Project">{trigger.project}</td>
        <td data-label="Starts">{trigger.kind === 'cron' ? <><code>{trigger.cron}</code> UTC{!trigger.paused && trigger.next_fire_at?.[0] && <div className="muted">Next: {new Date(trigger.next_fire_at[0]).toLocaleString()}</div>}</> : <><span>Signed webhook</span><div><code>{trigger.url}</code></div>{!trigger.secret_configured && <p className="error">Team webhook secret missing. Ask your admin to configure it.</p>}</>}{trigger.error && <p className="error">{trigger.error}</p>}</td>
        <td data-label="State"><span className={`badge ${trigger.paused ? 'awaiting_approval' : 'recorded'}`}>{trigger.paused ? 'Paused' : 'Active'}</span>{trigger.configuration_paused && <p className="muted">Paused in team configuration</p>}</td>
        <td>{canEdit ? <button className="secondary" disabled={!!busy || trigger.configuration_paused} aria-label={`${trigger.paused ? 'Resume' : 'Pause'} ${trigger.name}`} onClick={() => void toggle(trigger)}>{busy === `${trigger.workflow}/${trigger.name}` ? 'Saving…' : trigger.paused ? 'Resume' : 'Pause'}</button> : <span className="muted">Builder access required</span>}</td>
      </tr>)}</tbody></table></div></div> : <Empty title="No triggers configured"><p>Add a cron or webhook trigger to a workflow in your team configuration, then restart the gateway.</p></Empty>)}
    <p className="muted">Schedules use UTC and skip overlapping runs. Pausing stops future launches; existing runs continue.{session.team.role !== 'admin' && ' Configuration changes are managed by your team admin.'}</p>
    <section className="panel setup"><h2>Notifications</h2>
      {session.team.notifications?.channels.length ? <p>Connected channels: {session.team.notifications.channels.map(channel => ({ slack: 'Slack', webhook: 'Webhook', email: 'Email' } as Record<string, string>)[channel] || channel).join(', ')}. Alerts cover waiting approvals, failed runs and {Math.round(session.team.notifications.budget_threshold * 100)}% of a run’s token or cost budget.</p> : <p>No notification channels configured. Ask your admin to connect Slack, an outgoing webhook or SMTP in the team configuration.</p>}
      <p>Delivery attempts and failures appear in each run’s receipts. Open the console to approve or reject work.</p>
      <a href="https://github.com/RamazanKara/agentworkflows/blob/main/docs/workflows.md#triggers-and-notifications">Trigger signing and notification setup</a>
    </section>
  </>;
}
