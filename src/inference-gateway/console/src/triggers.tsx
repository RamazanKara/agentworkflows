import { useState } from 'react';
import { api, useData, workflowName, type Session } from './api';
import { CopyField, Empty, ErrorMessage, Loading, PageHeader, Refresh } from './ui';

type Trigger = {
  workflow: string; name: string; kind: 'cron' | 'webhook'; project: string; cron: string;
  paused: boolean; configuration_paused: boolean; url?: string; next_fire_at?: string[];
  secret_configured?: boolean; error?: string;
};

const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const pad = (value: string) => value.padStart(2, '0');
// Common cron shapes read as sentences; anything else stays as the cron expression.
function schedule(cron: string) {
  const [minute, hour, dom, month, dow] = cron.trim().split(/\s+/);
  const every = /^\*\/(\d+)$/;
  if (dom === '*' && month === '*' && dow === '*' && hour === '*' && every.test(minute)) return `Every ${minute.match(every)![1]} minutes`;
  if (dom === '*' && month === '*' && dow === '*' && minute === '0' && hour === '*') return 'Every hour';
  if (!/^\d+$/.test(minute ?? '') || !/^\d+$/.test(hour ?? '') || dom !== '*' || month !== '*') return `${cron} (UTC)`;
  const time = `${pad(hour)}:${pad(minute)} UTC`;
  if (dow === '*') return `Every day at ${time}`;
  if (dow === '1-5') return `Weekdays at ${time}`;
  if (/^[0-7]$/.test(dow)) return `Every ${days[Number(dow) % 7]} at ${time}`;
  return `${cron} (UTC)`;
}
const utc = (value: string) => new Date(value).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'UTC' }) + ' UTC';

export function Triggers({ session }: { session: Session }) {
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const result = useData<{ triggers: Trigger[] }>(session.csrfToken, '/v1/workflow-triggers', revision);
  const canEdit = ['admin', 'builder'].includes(session.team.role);
  async function toggle(trigger: Trigger) {
    setBusy(`${trigger.workflow}/${trigger.name}`); setError(''); setMessage('');
    try {
      await api(session.csrfToken, `/v1/workflow-triggers/${encodeURIComponent(trigger.workflow)}/${encodeURIComponent(trigger.name)}`, {
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
      <thead><tr><th>Workflow</th><th>Project</th><th>Starts</th><th>State</th><th><span className="visually-hidden">Action</span></th></tr></thead>
      <tbody>{result.data.triggers.map(trigger => <tr key={`${trigger.workflow}/${trigger.name}`}>
        <th scope="row">{workflowName(trigger.workflow)}{result.data!.triggers.filter(t => t.workflow === trigger.workflow).length > 1 && <div className="muted">{trigger.name}</div>}</th><td data-label="Project">{trigger.project}</td>
        <td data-label="Starts">{trigger.kind === 'cron' ? <><span title={`cron: ${trigger.cron} (UTC)`}>{schedule(trigger.cron)}</span>{!trigger.paused && trigger.next_fire_at?.[0] && <div className="muted">Next: {utc(trigger.next_fire_at[0])}</div>}</> : <><span>On a signed webhook</span><CopyField value={new URL(trigger.url || '', location.origin).href} label={`Webhook URL for ${workflowName(trigger.workflow)}`} onCopied={ok => setMessage(ok ? 'Webhook URL copied.' : 'Copy failed. Select the URL and copy it manually.')}/>{!trigger.secret_configured && <p className="error">Team webhook secret missing. Ask your admin to configure it.</p>}</>}{trigger.error && <p className="error">{trigger.error}</p>}</td>
        <td data-label="State"><span className={`badge ${trigger.paused ? 'awaiting_approval' : 'recorded'}`}>{trigger.paused ? 'Paused' : 'Active'}</span>{trigger.configuration_paused && <p className="muted">Paused in team configuration</p>}</td>
        <td className="row-action">{canEdit ? <button className="secondary" disabled={!!busy || trigger.configuration_paused} aria-label={`${trigger.paused ? 'Resume' : 'Pause'} ${trigger.name}`} onClick={() => void toggle(trigger)}>{busy === `${trigger.workflow}/${trigger.name}` ? 'Saving…' : trigger.paused ? 'Resume' : 'Pause'}</button> : <span className="muted">Builder access required</span>}</td>
      </tr>)}</tbody></table></div></div> : <Empty title="No triggers configured"><p>Add a cron or webhook trigger to a workflow in your team configuration, then restart the gateway.</p></Empty>)}
    <p className="muted">Triggers are defined in your team policy. Schedules use UTC and skip overlapping runs; pausing stops future launches while existing runs continue.{session.team.role !== 'admin' && ' Your team admin manages the policy.'}</p>
    <section className="panel setup"><h2>Notifications</h2>
      {session.team.notifications?.channels.length ? <p>Connected channels: {session.team.notifications.channels.map(channel => ({ slack: 'Slack', webhook: 'Webhook', email: 'Email' } as Record<string, string>)[channel] || channel).join(', ')}. Alerts cover waiting approvals, failed runs and {Math.round(session.team.notifications.budget_threshold * 100)}% of a run’s token or cost budget.</p> : <p>No notification channels configured. Ask your admin to connect Slack, an outgoing webhook or SMTP in the team configuration.</p>}
      <p>Each delivery attempt is recorded in the run’s receipts. Approval alerts link to the run in this console, where reviewers approve or reject the work.</p>
      <a className="tap" href="https://github.com/RamazanKara/agentworkflows/blob/main/docs/workflows.md#triggers-and-notifications">Trigger signing and notification setup</a>
    </section>
  </>;
}
