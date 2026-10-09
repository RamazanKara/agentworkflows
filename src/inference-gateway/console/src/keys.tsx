import { useState } from 'react';
import { api, label, useData, type AuthConfig, type Session, type Team } from './api';
import { Empty, ErrorMessage, Icon, Loading, PageHeader, Refresh } from './ui';

type Key = {
  key_id: string; name: string; role: Team['role']; project: string | null; created_at: number;
  last_used_at: number | null; expires_at: number | null; revoked_at: number | null;
};

const roles: Team['role'][] = ['viewer', 'approver', 'builder', 'admin'];
const roleHelp: Record<Team['role'], string> = {
  viewer: 'Sees runs, approvals and costs.',
  approver: 'Sees everything and approves or rejects drafts.',
  builder: 'Sees everything and starts, cancels and retries runs.',
  admin: 'Everything, including keys, providers and budgets.',
};
const lifetimes: [string, number][] = [['Never', 0], ['30 days', 30], ['90 days', 90], ['1 year', 365]];
const day = (value: number) => new Date(value * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
function ago(value: number) {
  const seconds = Date.now() / 1000 - value;
  if (seconds < 60) return 'Just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return seconds < 172800 ? 'Yesterday' : day(value);
}
const state = (key: Key) => key.revoked_at != null ? 'revoked'
  : key.expires_at != null && key.expires_at <= Date.now() / 1000 ? 'expired' : 'active';

export function Keys({ session }: { session: Session }) {
  if (session.team.role !== 'admin') return <div className="panel"><Empty title="Team admin access required"><p>Ask your team admin to manage membership and API keys.</p><a className="tap" href="#runs">View workflow runs</a></Empty></div>;
  return <KeyManagement session={session}/>;
}

function KeyManagement({ session }: { session: Session }) {
  const [revision, setRevision] = useState(0);
  const result = useData<{ keys: Key[] }>(session.csrfToken, '/v1/team/keys', revision);
  const config = useData<AuthConfig>(session.csrfToken, '/v1/auth/config');
  const [name, setName] = useState('');
  const [role, setRole] = useState<Team['role']>('viewer');
  const [project, setProject] = useState(session.team.projects.length === 1 ? session.team.projects[0] : '');
  const [lifetime, setLifetime] = useState(90);
  const [created, setCreated] = useState<{ name: string; key: string; id: string }>();
  const [editing, setEditing] = useState<Key>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const keys = [...result.data?.keys ?? []].sort((a, b) => Number(state(a) !== 'active') - Number(state(b) !== 'active') || (b.created_at ?? 0) - (a.created_at ?? 0));
  return <><PageHeader title="Members & keys" subtitle="Who can use this team, and with which role."><Refresh onClick={() => { setCreated(undefined); setRevision(value => value + 1); }}/></PageHeader>
    <p className="intro">{config.data?.oidc.enabled
      ? 'People who sign in with their company account get their team and role from it. Create keys here for anyone without company sign-in and for automation.'
      : 'Give each person and integration their own key, so you can revoke one without affecting anyone else.'}</p>
    <ErrorMessage message={error || result.error}/><p role="status" className="status">{message}</p>
    {editing && <KeyEditor key={editing.key_id} record={editing} session={session} onCancel={() => setEditing(undefined)} onSaved={name => {
      setEditing(undefined); setMessage(`${name} updated. Access changes apply immediately.`); setRevision(value => value + 1);
    }}/>}
    <section className="panel form-panel key-form" aria-labelledby="create-key">
      {created ? <div className="new-key">
        <h2 id="create-key">Key created for {created.name}</h2>
        <p>Copy it now and share it securely with its owner. You won’t be able to see it again.</p>
        <code aria-label="New API key">{created.key}</code>
        <div className="actions">
          <button onClick={async () => { try { await navigator.clipboard.writeText(created.key); setMessage('Key copied.'); } catch { setError('Copy failed. Select the key and copy it manually.'); } }}><Icon name="copy"/>Copy key</button>
          <button className="secondary" onClick={() => { setCreated(undefined); setMessage(''); }}>Done</button>
        </div>
      </div> : <><h2 id="create-key">Create a key</h2>
      <form onSubmit={async event => {
        event.preventDefault(); setBusy(true); setError(''); setMessage('');
        try {
          const expires_at = lifetime ? new Date(Date.now() + lifetime * 86400000).toISOString() : null;
          const value = await api<Key & { key: string }>(session.csrfToken, '/v1/team/keys', {
            method: 'POST', body: JSON.stringify({ name: name.trim(), role, project: project || null, expires_at }),
          });
          setCreated({ name: value.name, key: value.key, id: value.key_id }); setName(''); setRevision(value => value + 1);
        } catch (value) { setError((value as Error).message); }
        finally { setBusy(false); }
      }}>
        <div className="field"><label htmlFor="key-name">Name</label><input id="key-name" required maxLength={128} placeholder="e.g. Priya Shah or Release bot" value={name} onChange={e => setName(e.target.value)}/></div>
        <div className="field"><label htmlFor="key-role">Role</label><select id="key-role" value={role} onChange={e => setRole(e.target.value as Team['role'])} aria-describedby="key-role-help">{roles.map(value => <option key={value} value={value}>{label(value)}</option>)}</select><small id="key-role-help">{roleHelp[role]}</small></div>
        <div className="field"><label htmlFor="key-project">Project</label><select id="key-project" value={project} onChange={e => setProject(e.target.value)}><option value="">All projects</option>{session.team.projects.map(value => <option key={value}>{value}</option>)}</select></div>
        <div className="field"><label htmlFor="key-expires">Expires</label><select id="key-expires" value={lifetime} onChange={e => setLifetime(Number(e.target.value))}>{lifetimes.map(([text, days]) => <option key={days} value={days}>{text === 'Never' ? text : `In ${text}`}</option>)}</select></div>
        <div className="form-actions"><button disabled={busy}>{busy ? 'Creating…' : 'Create key'}</button></div>
      </form></>}
    </section>
    {!result.data && !result.error && <Loading/>}
    {result.data && (keys.length ? <div className="panel"><div className="table-scroll" tabIndex={0} role="region" aria-label="Team API keys"><table className="stack keys-table"><thead><tr><th>Name</th><th>Role</th><th>Project</th><th>Last used</th><th>Expires</th><th>Status</th><th><span className="visually-hidden">Action</span></th></tr></thead><tbody>{keys.map(key => {
      const current = state(key);
      return <tr key={key.key_id} className={[current === 'active' ? '' : 'inactive', key.key_id === created?.id ? 'new' : ''].join(' ').trim() || undefined}><th scope="row">{key.name}{key.key_id === session.keyId && <span className="badge you">You</span>}</th><td data-label="Role">{label(key.role)}</td><td data-label="Project">{key.project || 'All projects'}</td><td data-label="Last used">{key.last_used_at == null ? 'Never' : ago(key.last_used_at)}</td><td data-label="Expires">{key.expires_at == null ? 'Never' : day(key.expires_at)}</td><td data-label="Status"><span className={`badge key-${current}`}>{label(current)}{key.revoked_at != null && ` ${day(key.revoked_at)}`}</span></td><td className="row-action">{key.revoked_at == null && key.key_id !== session.keyId && <button className="danger" disabled={busy} aria-label={`Revoke ${key.name}`} onClick={async () => {
        if (!window.confirm(`Revoke ${key.name}? It stops working immediately.`)) return;
        setBusy(true); setError(''); setCreated(undefined); setEditing(undefined);
        try { await api(session.csrfToken, `/v1/team/keys/${encodeURIComponent(key.key_id)}`, { method: 'DELETE' }); setMessage(`${key.name} revoked.`); setRevision(value => value + 1); }
        catch (value) { setError((value as Error).message); }
        finally { setBusy(false); }
      }}>Revoke</button>}{key.revoked_at == null && key.key_id !== session.keyId && <button className="secondary" disabled={busy} aria-label={`Edit ${key.name}`} onClick={() => { setCreated(undefined); setEditing(key); setMessage(''); }}>Edit</button>}</td></tr>;
    })}</tbody></table></div></div> : <div className="panel"><Empty title="No keys yet"><p>Create the first key above. Keys from your gateway configuration keep working and are not listed here.</p></Empty></div>)}
  </>;
}

function KeyEditor({ record, session, onCancel, onSaved }: { record: Key; session: Session; onCancel: () => void; onSaved: (name: string) => void }) {
  const [name, setName] = useState(record.name);
  const [role, setRole] = useState(record.role);
  const [project, setProject] = useState(record.project || '');
  const originalExpiry = record.expires_at == null ? '' : new Date(record.expires_at * 1000).toISOString().slice(0, 16);
  const [expiry, setExpiry] = useState(originalExpiry);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return <section className="panel form-panel" aria-labelledby="edit-key"><h2 id="edit-key">Edit {record.name}</h2>
    <ErrorMessage message={error}/>
    <form onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError('');
      const changes: Record<string, unknown> = {};
      if (name.trim() !== record.name) changes.name = name.trim();
      if (role !== record.role) changes.role = role;
      if ((project || null) !== record.project) changes.project = project || null;
      if (expiry !== originalExpiry) changes.expires_at = expiry ? new Date(`${expiry}:00Z`).toISOString() : null;
      try {
        await api(session.csrfToken, `/v1/team/keys/${encodeURIComponent(record.key_id)}`, { method: 'PATCH', body: JSON.stringify(changes) });
        onSaved(name.trim());
      } catch (value) { setError((value as Error).message); }
      finally { setBusy(false); }
    }}>
      <fieldset className="plain" disabled={busy}>
        <div className="field"><label htmlFor="edit-key-name">Edit name</label><input id="edit-key-name" autoFocus required maxLength={128} value={name} onChange={event => setName(event.target.value)}/></div>
        <div className="field"><label htmlFor="edit-key-role">Edit role</label><select id="edit-key-role" value={role} onChange={event => setRole(event.target.value as Team['role'])}>{roles.map(value => <option key={value} value={value}>{label(value)}</option>)}</select></div>
        <div className="field"><label htmlFor="edit-key-project">Edit project</label><select id="edit-key-project" value={project} onChange={event => setProject(event.target.value)}><option value="">All projects</option>{session.team.projects.map(value => <option key={value}>{value}</option>)}</select></div>
        <div className="field"><label htmlFor="edit-key-expiry">Edit expiry (UTC)</label><input id="edit-key-expiry" type="datetime-local" value={expiry} onChange={event => setExpiry(event.target.value)} aria-describedby="edit-expiry-help"/><small id="edit-expiry-help">Leave blank for no expiry. A past date disables access immediately.</small></div>
        <div className="actions"><button>{busy ? 'Saving…' : 'Save key'}</button><button type="button" className="secondary" onClick={onCancel}>Cancel editing</button></div>
      </fieldset>
    </form>
  </section>;
}
