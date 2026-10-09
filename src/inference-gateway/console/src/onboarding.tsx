import { useRef, useState } from 'react';
import { api, providerName, useData, type Session } from './api';
import { ErrorMessage, Loading, Refresh } from './ui';

type Setup = {
  providers: { provider: string; configured: boolean; version: number; can_save: boolean }[];
  sample: { template_id: string; version: string; workflow: string; installed: boolean; input: Record<string, unknown>; ready: boolean } | null;
  blockers: string[];
};

export function FirstRunWizard({ session }: { session: Session }) {
  const [revision, setRevision] = useState(0);
  const setup = useData<Setup>(session.csrfToken, '/v1/team/onboarding', revision);
  const [provider, setProvider] = useState('');
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const requestId = useRef(crypto.randomUUID());
  const candidates = setup.data?.providers.filter(item => item.can_save) || [];
  const selected = candidates.find(item => item.provider === provider) || candidates.find(item => !item.configured) || candidates[0];
  const sample = setup.data?.sample;
  const builder = ['admin', 'builder'].includes(session.team.role);
  return <section className="panel form-panel" aria-labelledby="first-run-title">
    <div className="section-heading"><h2 id="first-run-title">First-run wizard</h2><Refresh onClick={() => setRevision(value => value + 1)}/></div>
    <p>1. Connect a model. 2. Start the sample. 3. Review and approve its draft.</p>
    <ErrorMessage message={error || setup.error}/>
    {!setup.data && !setup.error && <Loading/>}
    {setup.data && <>
      {setup.data.blockers.length > 0 && <ul className="note-warn">{setup.data.blockers.map(message => <li key={message}>{message}</li>)}</ul>}
      {selected && <details open={setup.data.providers.some(item => !item.configured)}>
        <summary>Connect or rotate a provider key</summary>
        <form onSubmit={async event => {
          event.preventDefault(); setBusy(true); setError('');
          try {
            await api(session.csrfToken, `/v1/team/providers/${encodeURIComponent(selected.provider)}/key`, { method: 'PUT', body: JSON.stringify({ value: secret, expected_version: selected.version }) });
            setSecret(''); location.reload();
          } catch (failure) { setError((failure as Error).message); } finally { setBusy(false); }
        }}>
          <label>Provider<select aria-label="Provider" value={selected.provider} onChange={event => { setProvider(event.target.value); setSecret(''); }}>{candidates.map(item => <option key={item.provider} value={item.provider}>{providerName(item.provider)}</option>)}</select></label>
          <label>Provider API key<input type="password" autoComplete="off" required value={secret} onChange={event => setSecret(event.target.value)}/></label>
          <p className="muted">Encrypted on the server. Saving checks configuration; the first model call verifies provider access.</p>
          <button disabled={busy}>{busy ? 'Saving…' : 'Save provider key'}</button>
        </form>
      </details>}
      {setup.data.providers.some(item => !item.configured && !item.can_save) && <p className="muted">Your operator must configure the persistent encryption key or provision the provider credential. <a href="#providers">Open setup instructions</a>.</p>}
      {sample && <><p>Research a topic, draft a briefing, and pause for approval before the publish step. The run uses your team's current budgets and review policy.</p>
        {builder ? <button disabled={busy || !sample.ready} onClick={async () => {
          setBusy(true); setError('');
          try {
            if (!sample.installed && session.team.role === 'admin') await api(session.csrfToken, `/v1/workflow-templates/${sample.template_id}/install`, { method: 'POST', body: JSON.stringify({ version: sample.version }) });
            const run = await api<{ run_id: string }>(session.csrfToken, '/v1/workflow-runs', { method: 'POST', body: JSON.stringify({ workflow: sample.workflow, input: sample.input, project: session.team.projects[0], request_id: requestId.current }) });
            location.hash = `run/${run.run_id}`;
          } catch (failure) { setError((failure as Error).message); } finally { setBusy(false); }
        }}>{busy ? 'Starting…' : 'Start sample workflow'}</button> : <p>Ask a builder to start the sample. Approvers can <a href="#approvals">review approvals</a>.</p>}
        <p className="muted">The run page shows the draft, reviewer controls, and the finished timeline. <a href="#team">Review budgets and approval rules</a>.</p>
      </>}
    </>}
  </section>;
}
