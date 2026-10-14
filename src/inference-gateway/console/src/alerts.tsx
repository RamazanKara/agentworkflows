import { useState } from 'react';
import { api, useData, type Session } from './api';
import { ErrorMessage, Loading, NumberInput, Refresh } from './ui';

type Rules = { revision: number; events: string[]; channels: string[]; available_channels: string[]; budget_threshold: number; slow_step_ms: number };
const events = [['awaiting_approval', 'Waiting for approval'], ['failed', 'Run failed'], ['budget_threshold', 'Run budget threshold'], ['slow_step', 'Slow step']];
const labels: Record<string, string> = { slack: 'Slack', email: 'Email', webhook: 'Webhook' };

export function AlertSettings({ session }: { session: Session }) {
  const [revision, setRevision] = useState(0);
  const result = useData<Rules>(session.csrfToken, '/v1/team/alert-rules', revision);
  return <section className="panel form-panel" aria-labelledby="alert-rules-title">
    <div className="section-heading"><h2 id="alert-rules-title">Alert rules</h2><Refresh busy={result.loading && Boolean(result.data)} onClick={() => setRevision(value => value + 1)}/></div>
    <ErrorMessage message={result.error} retry={() => setRevision(value => value + 1)}/>
    {result.data ? <AlertForm key={result.data.revision} session={session} rules={result.data}/> : !result.error && <Loading text="Loading alert rules…"/>}
  </section>;
}

function AlertForm({ session, rules }: { session: Session; rules: Rules }) {
  const [value, setValue] = useState(rules);
  // Percent and seconds are edited as typed; clearing a field no longer snaps it to its minimum.
  const [percent, setPercent] = useState<number | ''>(Math.round(rules.budget_threshold * 100));
  const [seconds, setSeconds] = useState<number | ''>(rules.slow_step_ms / 1000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const toggle = (field: 'channels' | 'events', key: string) => setValue({ ...value, [field]: value[field].includes(key) ? value[field].filter(item => item !== key) : [...value[field], key] });
  return <form onSubmit={async event => {
    event.preventDefault(); setBusy(true); setError(''); setMessage('');
    const { revision, available_channels: _available, ...rest } = value;
    const body = { ...rest, budget_threshold: Number(percent) / 100, slow_step_ms: Math.max(1, Math.round(Number(seconds) * 1000)) };
    try {
      const saved = await api<Rules>(session.csrfToken, '/v1/team/alert-rules', { method: 'PUT', headers: { 'If-Match': String(revision) }, body: JSON.stringify(body) });
      setValue(saved); setPercent(Math.round(saved.budget_threshold * 100)); setSeconds(saved.slow_step_ms / 1000); setMessage('Alert rules saved.');
    } catch (failure) { setError((failure as Error).message); } finally { setBusy(false); }
  }}>
    <p>Get notified when a run needs attention. The gateway checks retained runs every 30 seconds. Delivery attempts appear in the run timeline.</p>
    <fieldset className="plain"><legend>Events</legend>{events.map(([event, title]) => <label className="check" key={event}><input type="checkbox" checked={value.events.includes(event)} onChange={() => toggle('events', event)}/>{title}</label>)}</fieldset>
    <label>Budget used (%)<NumberInput min={1} max={100} step={1} required value={percent} onChange={setPercent}/></label>
    <label>Slow step threshold (seconds)<NumberInput min={0.001} max={86400} step="any" required value={seconds} onChange={setSeconds}/></label>
    <fieldset className="plain"><legend>Destinations</legend>{rules.available_channels.map(channel => <label className="check" key={channel}><input type="checkbox" checked={value.channels.includes(channel)} onChange={() => toggle('channels', channel)}/>{labels[channel]}</label>)}</fieldset>
    {!rules.available_channels.length && <p className="note-warn">Your operator must configure a Slack webhook, email, or webhook destination before alerts can be delivered.</p>}
    <p className="muted">Clearing events or destinations pauses delivery. Each alert is sent once per run and destination. Destination secrets stay on the server.</p>
    <ErrorMessage message={error}/>{message && <p role="status">{message}</p>}<button disabled={busy}>{busy ? 'Saving…' : 'Save alert rules'}</button>
  </form>;
}
