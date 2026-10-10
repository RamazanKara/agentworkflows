import { useRef, useState } from 'react';
import { api, providerName, useData, type Session } from './api';
import { ErrorMessage } from './ui';

export type Setup = {
  providers: { provider: string; configured: boolean; version: number; can_save: boolean }[];
  sample: { template_id: string; version: string; workflow: string; installed: boolean; input: Record<string, unknown>; ready: boolean } | null;
  blockers: string[];
};

export function useSetup(session: Session) {
  return useData<Setup>(session.csrfToken, '/v1/team/onboarding');
}

// Lets an admin save a missing provider key from Get started; keys are encrypted on the server.
export function ProviderKeyForm({ session, setup }: { session: Session; setup: Setup }) {
  const [provider, setProvider] = useState('');
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const candidates = setup.providers.filter(item => item.can_save);
  const selected = candidates.find(item => item.provider === provider) || candidates.find(item => !item.configured) || candidates[0];
  if (session.team.role !== 'admin') return null;
  if (!selected) return setup.providers.some(item => !item.configured) ? <p className="muted">Your operator must configure the persistent encryption key or provision the provider credential. <a href="#providers">Open setup instructions</a>.</p> : null;
  return <details className="key-form" open={setup.providers.some(item => !item.configured)}>
    <summary>{setup.providers.some(item => !item.configured) ? 'Add a provider key' : 'Replace a provider key'}</summary>
    <form onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError('');
      try {
        await api(session.csrfToken, `/v1/team/providers/${encodeURIComponent(selected.provider)}/key`, { method: 'PUT', body: JSON.stringify({ value: secret, expected_version: selected.version }) });
        setSecret(''); location.reload();
      } catch (failure) { setError((failure as Error).message); } finally { setBusy(false); }
    }}>
      <ErrorMessage message={error}/>
      <label>Provider<select aria-label="Provider" value={selected.provider} onChange={event => { setProvider(event.target.value); setSecret(''); }}>{candidates.map(item => <option key={item.provider} value={item.provider}>{providerName(item.provider)}{item.configured ? ' (saved)' : ''}</option>)}</select></label>
      <label>Provider API key<input type="password" autoComplete="off" required value={secret} onChange={event => setSecret(event.target.value)}/></label>
      <p className="muted">Encrypted on the server. The first model call checks that the key works.</p>
      <button disabled={busy}>{busy ? 'Saving…' : 'Save provider key'}</button>
    </form>
  </details>;
}

// Installs the sample template when needed and starts it; a retry reuses the same request ID.
export function SampleButton({ session, setup }: { session: Session; setup: Setup }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const requestId = useRef(crypto.randomUUID());
  const sample = setup.sample;
  if (!sample) return null;
  return <>
    <ErrorMessage message={error}/>
    <button disabled={busy || !sample.ready} onClick={async () => {
      setBusy(true); setError('');
      try {
        if (!sample.installed && session.team.role === 'admin') await api(session.csrfToken, `/v1/workflow-templates/${sample.template_id}/install`, { method: 'POST', body: JSON.stringify({ version: sample.version }) });
        const run = await api<{ run_id: string }>(session.csrfToken, '/v1/workflow-runs', { method: 'POST', body: JSON.stringify({ workflow: sample.workflow, input: sample.input, project: session.team.projects[0], request_id: requestId.current }) });
        location.hash = `run/${run.run_id}`;
      } catch (failure) { setError((failure as Error).message); } finally { setBusy(false); }
    }}>{busy ? 'Starting…' : 'Start sample workflow'}</button>
  </>;
}
