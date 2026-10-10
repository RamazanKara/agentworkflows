import { useState } from 'react';
import { api, date, label, useData, type Session, type Team } from './api';
import { ErrorMessage, Loading } from './ui';

type Invitation = { invitation_id: string; name: string; role: Team['role']; project: string | null; expires_at: number; accepted_at: number | null; revoked_at: number | null };

export function Invitations({ session, onChanged }: { session: Session; onChanged: () => void }) {
  const [revision, setRevision] = useState(0);
  const result = useData<Invitation[]>(session.csrfToken, '/v1/team/invitations', revision);
  const [name, setName] = useState('');
  const [role, setRole] = useState<Team['role']>('viewer');
  const [project, setProject] = useState(session.team.projects[0] || '');
  const [link, setLink] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  return <section className="panel form-panel" aria-labelledby="invitations-title">
    <h2 id="invitations-title">Invite a teammate</h2>
    <p>Share a single-use link through a trusted channel. It expires in 24 hours. Company SSO access still comes from your identity provider.</p>
    <ErrorMessage message={error || result.error}/>{message && <p role="status">{message}</p>}
    {link ? <><label>Invitation link<input readOnly value={link}/></label><div className="actions">
      <button onClick={async () => { try { await navigator.clipboard.writeText(link); setMessage('Invitation link copied.'); } catch { setError('Select the invitation link and copy it manually.'); } }}>Copy invitation link</button>
      <button className="secondary" onClick={() => { setLink(''); setMessage(''); }}>Done with invitation</button>
    </div></> : <form onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError('');
      try {
        const invite = await api<Invitation & { token: string }>(session.csrfToken, '/v1/team/invitations', { method: 'POST', body: JSON.stringify({ name: name.trim(), role, project: project || null, expires_at: new Date(Date.now() + 90 * 86400000).toISOString() }) });
        setLink(`${location.origin}${location.pathname}#invite/${encodeURIComponent(invite.token)}`); setName(''); setRevision(value => value + 1); onChanged();
      } catch (failure) { setError((failure as Error).message); } finally { setBusy(false); }
    }}>
      <label>Teammate name<input required maxLength={128} value={name} onChange={event => setName(event.target.value)}/></label>
      <label>Invitation role<select aria-label="Invitation role" value={role} onChange={event => setRole(event.target.value as Team['role'])}>{['viewer', 'approver', 'builder', 'admin'].map(value => <option key={value} value={value}>{label(value)}</option>)}</select></label>
      <label>Invitation project<select aria-label="Invitation project" value={project} onChange={event => setProject(event.target.value)}><option value="">All projects</option>{session.team.projects.map(value => <option key={value}>{value}</option>)}</select></label>
      <p className="muted">Access lasts 90 days. You can edit or revoke it on this page.</p>
      <button disabled={busy}>{busy ? 'Creating…' : 'Create invitation'}</button>
    </form>}
    {!result.data && !result.error && <Loading/>}
    {result.data?.map(invite => <div className="secret-row" key={invite.invitation_id}>
      <div><strong>{invite.name}</strong><p>{label(invite.role)} · {invite.project || 'All projects'} · {invite.revoked_at ? 'Revoked' : invite.accepted_at ? 'Accepted' : invite.expires_at <= Date.now() / 1000 ? 'Expired' : `Pending until ${date(invite.expires_at)}`}</p></div>
      {!invite.revoked_at && <button className="danger" disabled={busy} onClick={async () => {
        if (!window.confirm(`Revoke access for ${invite.name}?`)) return;
        setBusy(true); setError('');
        try { await api(session.csrfToken, `/v1/team/invitations/${invite.invitation_id}`, { method: 'DELETE' }); setRevision(value => value + 1); onChanged(); }
        catch (failure) { setError((failure as Error).message); } finally { setBusy(false); }
      }}>Revoke invitation</button>}
    </div>)}
  </section>;
}

export function AcceptInvitation({ token }: { token: string }) {
  const [key, setKey] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  return <main className="signin-form"><h1>Join your team</h1><p>This link grants the role and project your admin selected. Accept only an invitation you expected.</p>
    <ErrorMessage message={error}/>
    <button disabled={busy} onClick={async () => {
      setBusy(true); setError('');
      try {
        let credential = key;
        if (!credential) {
          const accepted = await api<{ key: string }>('', '/v1/auth/invitations/accept', { method: 'POST', body: JSON.stringify({ token }) });
          credential = accepted.key; setKey(credential);
          history.replaceState(null, '', location.pathname + '#start');
        }
        await api('', '/v1/auth/session', { method: 'POST', body: JSON.stringify({ key: credential }) });
        location.replace(location.pathname + '#start'); location.reload();
      } catch (failure) { setError((failure as Error).message); } finally { setBusy(false); }
    }}>{busy ? 'Joining…' : key ? 'Retry sign-in' : 'Accept invitation'}</button>
    <p className="muted">The link works once. If it expired or was already used, ask your admin for a new invitation.</p>
  </main>;
}
