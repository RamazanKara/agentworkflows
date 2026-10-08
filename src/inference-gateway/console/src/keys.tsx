import { useState } from 'react';
import { api, date, useData, type Session, type Team } from './api';
import { Empty, ErrorMessage, Loading, PageHeader, Refresh } from './ui';

type Key = {
  key_id: string; name: string; role: Team['role']; project: string | null;
  last_used_at: number | null; expires_at: number | null; revoked_at: number | null;
};

export function Keys({ session }: { session: Session }) {
  if (session.team.role !== 'admin') return <Empty title="Team admin access required"><p>Ask your team admin to manage membership and API keys.</p></Empty>;
  return <KeyManagement session={session}/>;
}

function KeyManagement({ session }: { session: Session }) {
  const [revision, setRevision] = useState(0);
  const result = useData<{ keys: Key[] }>(session.csrfToken, '/v1/team/keys', revision);
  const [name, setName] = useState('');
  const [role, setRole] = useState<Team['role']>('viewer');
  const [project, setProject] = useState(session.team.projects.length === 1 ? session.team.projects[0] : '');
  const [expires, setExpires] = useState('');
  const [created, setCreated] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  return <><PageHeader title="Members & keys" subtitle="Give teammates and integrations access to this team."><Refresh onClick={() => { setCreated(''); setRevision(value => value + 1); }}/></PageHeader>
    <ErrorMessage message={error || result.error}/><p role="status">{message}</p>
    <section className="panel setup"><h2>Create API key</h2>
      <form onSubmit={async event => {
        event.preventDefault(); setBusy(true); setError(''); setMessage(''); setCreated('');
        try {
          const value = await api<Key & { key: string }>(session.csrfToken, '/v1/team/keys', {
            method: 'POST', body: JSON.stringify({ name, role, project: project || null, expires_at: expires ? new Date(expires).toISOString() : null }),
          });
          setCreated(value.key); setName(''); setRevision(value => value + 1);
        } catch (value) { setError((value as Error).message); }
        finally { setBusy(false); }
      }}>
        <label htmlFor="key-name">Name</label><input id="key-name" required maxLength={128} value={name} onChange={e => setName(e.target.value)}/>
        <label htmlFor="key-role">Role</label><select id="key-role" value={role} onChange={e => setRole(e.target.value as Team['role'])}>{['viewer', 'approver', 'builder', 'admin'].map(value => <option key={value}>{value}</option>)}</select>
        <label htmlFor="key-project">Project</label><select id="key-project" value={project} onChange={e => setProject(e.target.value)}><option value="">All permitted projects</option>{session.team.projects.map(value => <option key={value}>{value}</option>)}</select>
        <label htmlFor="key-expires">Expires (optional)</label><input id="key-expires" type="datetime-local" value={expires} onChange={e => setExpires(e.target.value)}/>
        <button disabled={busy}>{busy ? 'Saving…' : 'Create key'}</button>
      </form>
      {created && <div className="callout"><h3>Save this key now</h3><p>You can only see it once. Share it securely with its owner.</p><code aria-label="New API key">{created}</code><div className="actions">
        <button onClick={async () => { try { await navigator.clipboard.writeText(created); setMessage('Key copied.'); } catch { setError('Copy failed. Select the key and copy it manually.'); } }}>Copy key</button>
        <button className="secondary" onClick={() => setCreated('')}>Done</button>
      </div></div>}
    </section>
    {!result.data && !result.error && <Loading/>}
    {result.data && (result.data.keys.length ? <div className="panel"><div className="table-scroll" tabIndex={0} role="region" aria-label="Team API keys"><table className="stack"><thead><tr><th>Name</th><th>Role</th><th>Project</th><th>Last used</th><th>Expires</th><th>Status</th><th>Action</th></tr></thead><tbody>{result.data.keys.map(key => {
      const state = key.revoked_at != null ? 'Revoked' : key.expires_at != null && key.expires_at <= Date.now() / 1000 ? 'Expired' : 'Active';
      return <tr key={key.key_id}><th scope="row">{key.name}</th><td data-label="Role">{key.role}</td><td data-label="Project">{key.project || 'All'}</td><td data-label="Last used">{key.last_used_at == null ? 'Never' : date(key.last_used_at)}</td><td data-label="Expires">{key.expires_at == null ? 'Never' : date(key.expires_at)}</td><td data-label="Status">{state}</td><td>{key.revoked_at == null && <button className="secondary" disabled={busy} aria-label={`Revoke ${key.name}`} onClick={async () => {
        if (!window.confirm(`Revoke ${key.name}? It will stop working immediately.`)) return;
        setBusy(true); setError(''); setCreated('');
        try { await api(session.csrfToken, `/v1/team/keys/${encodeURIComponent(key.key_id)}`, { method: 'DELETE' }); setMessage(`${key.name} revoked.`); setRevision(value => value + 1); }
        catch (value) { setError((value as Error).message); }
        finally { setBusy(false); }
      }}>Revoke</button>}</td></tr>;
    })}</tbody></table></div></div> : <Empty title="No managed keys yet"><p>Create a key above. Bootstrap keys remain in your gateway configuration.</p></Empty>)}
  </>;
}
