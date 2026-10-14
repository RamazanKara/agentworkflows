import { useRef, useState } from 'react';
import { api, channelNames, duration, escalationWho, policyCache, useData, useRevision, workflowName, type ApiError, type Channel, type Escalation, type Policy, type Session } from './api';
import { DotList, ErrorMessage, Loading, NumberInput, Refresh, useUnsavedChanges } from './ui';

type Row = Escalation & { workflow: string; effective: boolean; updated_by: string | null; updated_at: number | null };
type Escalations = { revision: number; escalations: Row[]; available_channels: Channel[] };
const units: [string, number][] = [['minutes', 60], ['hours', 3600], ['days', 86400]];
const unitFor = (seconds: number) => seconds % 86400 === 0 ? 86400 : seconds % 3600 === 0 ? 3600 : 60;
const longest = 604740;
const destinations = (channels: Channel[] | null) => channels === null ? 'Team alert channels'
  : channels.length ? channels.map(channel => channelNames[channel] || channel).join(', ') : 'No notice';
// Only workflows whose reviews need an admin can widen to every approver; approvers already review the rest.
const adminReviewed = (policy?: Policy) => policy?.approverRole === 'admin';
// Reviews expire first, so the escalation never fires; say which number to change.
const notEffective = (row: Row, policy?: Policy) => !policy ? `Not in effect: ${workflowName(row.workflow)} is no longer in your team’s policy. Remove this escalation.`
  : `Not in effect: reviews of ${workflowName(row.workflow)} expire after ${duration(policy.approvalTimeoutSeconds ?? null)}, before the ${duration(row.after_seconds)} escalation. Shorten the escalation, or lengthen the workflow’s approval expiry above.`;

export function EscalationSettings({ session }: { session: Session }) {
  const [revision, setRevision] = useState(0);
  const result = useData<Escalations>(session.csrfToken, '/v1/team/approval-escalations', revision);
  const policies = useData<{ workflows: Record<string, Policy> }>(session.csrfToken, '/v1/workflow-policies', revision, policyCache);
  const at = useRevision(result.data?.revision);
  const [editing, setEditing] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [message, setMessage] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const data = result.data;
  const rows = new Map((data?.escalations || []).map(row => [row.workflow, row]));
  const known = policies.data?.workflows || {};
  // Workflows with a review step, plus any escalation whose workflow left the policy, so it can be removed.
  const names = [...Object.keys(known).filter(name => known[name].approvalRequired !== false || rows.has(name)), ...[...rows.keys()].filter(name => !known[name])];
  const reload = () => { setRevision(value => value + 1); setConflict(false); setError(''); };
  const failed = (failure: ApiError) => { setConflict(failure.status === 409); setError(failure.message); };
  const finish = (text: string) => { setMessage(text); setEditing(undefined); setRevision(value => value + 1); heading.current?.focus(); };
  async function remove(name: string) {
    if (!window.confirm(`Stop escalating ${workflowName(name)} reviews? Runs already started keep their escalation.`)) return;
    setBusy(true); setError(''); setMessage('');
    try {
      await api(session.csrfToken, `/v1/team/workflows/${encodeURIComponent(name)}/approval-escalation`, { method: 'DELETE', headers: { 'If-Match': String(at ?? data!.revision) } });
      finish(`Escalation for ${workflowName(name)} removed. Runs already started keep theirs.`);
    } catch (failure) { failed(failure as ApiError); } finally { setBusy(false); }
  }
  return <section className="panel form-panel escalations" aria-labelledby="escalation-title">
    <div className="section-heading"><h2 id="escalation-title" ref={heading} tabIndex={-1}>Approval escalation</h2><Refresh busy={result.loading && Boolean(data)} onClick={reload}/></div>
    <p>When a review waits too long, let more approvers decide and send a notice. Changes apply to runs started afterwards; quorum and expiry stay the same.</p>
    <ErrorMessage message={result.error || policies.error} retry={reload}/>
    {!data && !result.error && <Loading text="Loading approval escalation…"/>}
    {data && <>
      <p className="muted">{data.available_channels.length ? `Notices can go to ${data.available_channels.map(channel => channelNames[channel] || channel).join(', ')}.`
        : 'No notification destination is configured, so escalation widens who may review without a notice. Ask your operator to add Slack, email or a webhook.'}</p>
      {names.length ? <ul className="escalation-list">{names.map(name => {
        const row = rows.get(name);
        const policy = known[name];
        return <li key={name}><div>
          <strong>{workflowName(name)}</strong>
          <small>{row ? <DotList items={[`After ${duration(row.after_seconds)}`, escalationWho(row) || 'Notice only', destinations(row.channels)]}/> : 'Off'}</small>
          {row && !row.effective && <p className="note-warn">{notEffective(row, policy)}</p>}
        </div><div className="actions">
          {policy && <button className="secondary" disabled={busy} aria-label={`${row ? 'Edit' : 'Set up'} escalation for ${workflowName(name)}`} onClick={() => { setEditing(name); setMessage(''); setError(''); }}>{row ? 'Edit' : 'Set up'}</button>}
          {row && <button className="danger" disabled={busy} aria-label={`Remove escalation for ${workflowName(name)}`} onClick={() => void remove(name)}>Remove</button>}
        </div></li>;
      })}</ul> : <p>No workflow has a review step yet.</p>}
      {editing && known[editing] && <EscalationForm key={editing} session={session} name={editing} policy={known[editing]} row={rows.get(editing)}
        available={data.available_channels} revision={at ?? data.revision} onCancel={() => setEditing(undefined)} onFailed={failed}
        onSaved={() => finish(`Escalation for ${workflowName(editing)} saved. It applies to runs started from now on.`)}/>}
    </>}
    <ErrorMessage message={error}/>{conflict && <button className="secondary" type="button" onClick={reload}>Reload escalations</button>}
    {message && <p role="status">{message}</p>}
  </section>;
}

function EscalationForm({ session, name, policy, row, available, revision, onSaved, onCancel, onFailed }: {
  session: Session; name: string; policy: Policy; row?: Row; available: Channel[]; revision: number;
  onSaved: () => void; onCancel: () => void; onFailed: (failure: ApiError) => void;
}) {
  const timeout = policy.approvalTimeoutSeconds ?? 604800;
  // A new escalation starts at four hours, or halfway to the review's expiry when that comes sooner.
  const start = row?.after_seconds ?? Math.max(60, Math.min(14400, Math.floor(timeout / 120) * 60));
  const [amount, setAmount] = useState<number | ''>(start / unitFor(start));
  const [unit, setUnit] = useState(unitFor(start));
  const [everyApprover, setEveryApprover] = useState(row ? row.approver_role === 'approver' : adminReviewed(policy));
  const [reviewers, setReviewers] = useState((row?.reviewers || []).join('\n'));
  const [mode, setMode] = useState<'team' | 'choose' | 'none'>(row?.channels == null ? 'team' : row.channels.length ? 'choose' : 'none');
  const [chosen, setChosen] = useState<Channel[]>(row?.channels?.length ? row.channels : available.slice(0, 1));
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  useUnsavedChanges(touched, `Discard your unsaved escalation for ${workflowName(name)}?`);
  const edit = <T,>(set: (value: T) => void) => (value: T) => { set(value); setTouched(true); };
  const admin = adminReviewed(policy);
  const id = (field: string) => `escalation-${field}`;
  return <form className="escalation-form" aria-labelledby={id('title')} onSubmit={async event => {
    event.preventDefault();
    const seconds = Math.round(Number(amount) * unit);
    const problems: Record<string, string> = {};
    if (!(seconds >= 60 && seconds <= longest)) problems.after_seconds = 'Choose at least 1 minute and less than 7 days.';
    else if (seconds >= timeout) problems.after_seconds = `Escalate before reviews of this workflow expire, after ${duration(timeout)}.`;
    if (mode === 'choose' && !chosen.length) problems.channels = 'Choose at least one destination, or pick another notice option.';
    setErrors(problems);
    if (Object.keys(problems).length) return;
    setBusy(true);
    const body = {
      after_seconds: seconds, approver_role: admin && everyApprover ? 'approver' : null,
      reviewers: admin ? reviewers.split(/\r?\n/).map(line => line.trim()).filter(Boolean) : [],
      channels: mode === 'team' ? null : mode === 'none' ? [] : chosen,
    };
    try {
      await api(session.csrfToken, `/v1/team/workflows/${encodeURIComponent(name)}/approval-escalation`, { method: 'PUT', headers: { 'If-Match': String(revision) }, body: JSON.stringify(body) });
      setTouched(false); onSaved();
    } catch (value) {
      const failure = value as ApiError;
      const fields = Object.fromEntries((failure.fields || []).map(item => [item.field, item.message]));
      if (Object.keys(fields).length) setErrors(fields); else onFailed(failure);
    } finally { setBusy(false); }
  }}>
    <h3 id={id('title')}>{row ? 'Edit' : 'Set up'} escalation for {workflowName(name)}</h3>
    <fieldset className="plain" disabled={busy}>
      <div className="field"><label htmlFor={id('after')}>Escalate a review still waiting after</label>
        <div className="duration-input"><NumberInput id={id('after')} required min={1} step={1} value={amount} onChange={edit(setAmount)} aria-invalid={Boolean(errors.after_seconds)}
          aria-describedby={[id('after-help'), errors.after_seconds && id('after-error')].filter(Boolean).join(' ')}/>
          <select aria-label="Unit" value={unit} onChange={event => edit(setUnit)(Number(event.target.value))}>{units.map(([text, seconds]) => <option key={seconds} value={seconds}>{text}</option>)}</select></div>
        <small id={id('after-help')}>Reviews of this workflow expire after {duration(timeout)}; escalate before that.</small>
        {errors.after_seconds && <p className="error" id={id('after-error')}>{errors.after_seconds}</p>}</div>
      {admin ? <>
        <label className="check"><input type="checkbox" checked={everyApprover} onChange={event => edit(setEveryApprover)(event.target.checked)}/>Let every approver review once escalated</label>
        <div className="field"><label htmlFor={id('reviewers')}>Named reviewers (optional)</label>
          <textarea id={id('reviewers')} rows={3} spellCheck={false} value={reviewers} onChange={event => edit(setReviewers)(event.target.value)} aria-describedby={id('reviewers-help')}/>
          <small id={id('reviewers-help')}>One per line: a company sign-in subject or managed key ID that holds the approver role. Admins can always review.</small>
          {errors.reviewers && <p className="error">{errors.reviewers}</p>}</div>
      </> : <p className="muted">Approvers can already review this workflow, so escalating it sends the notice without changing who may review.</p>}
      <fieldset className="choices" aria-describedby={errors.channels ? id('channels-error') : undefined}><legend>Notice</legend>
        <label className="check"><input type="radio" name={id('mode')} checked={mode === 'team'} onChange={() => edit(setMode)('team')}/>The team’s alert channels</label>
        <label className="check"><input type="radio" name={id('mode')} checked={mode === 'choose'} disabled={!available.length} onChange={() => edit(setMode)('choose')}/>Choose destinations</label>
        {mode === 'choose' && <div className="nested-choices">{available.map(channel => <label className="check" key={channel}><input type="checkbox" checked={chosen.includes(channel)}
          onChange={event => edit(setChosen)(event.target.checked ? [...chosen, channel] : chosen.filter(item => item !== channel))}/>{channelNames[channel] || channel}</label>)}</div>}
        <label className="check"><input type="radio" name={id('mode')} checked={mode === 'none'} onChange={() => edit(setMode)('none')}/>No notice</label>
      </fieldset>
      {errors.channels && <p className="error" id={id('channels-error')}>{errors.channels}</p>}
      <div className="actions"><button>{busy ? 'Saving…' : 'Save escalation'}</button><button type="button" className="secondary" onClick={onCancel}>Cancel</button></div>
    </fieldset>
  </form>;
}
