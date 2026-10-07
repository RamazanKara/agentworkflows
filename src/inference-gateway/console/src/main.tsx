import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, type Session, type Team } from './api';
import { ErrorMessage, Icon } from './ui';
import { Approvals, RunDetail, Runs, StartRun } from './runs';
import { Costs, GetStarted, Providers } from './team';
import './style.css';

const navigation = [
  ['start', 'Get started'], ['runs', 'Workflow runs'], ['approvals', 'Approvals'],
  ['providers', 'Providers & budgets'], ['costs', 'Costs'],
];

function SignIn({ onSignIn, cancel, message }: {
  onSignIn: (token: string, team: Team) => void; cancel?: () => void; message?: string;
}) {
  const [token, setToken] = useState('');
  const [error, setError] = useState(message);
  const [busy, setBusy] = useState(false);
  return <div className="signin"><div className="signin-story">
    <div className="brand"><Icon name="brand"/>AgentWorkflows</div>
    <h1>Good work.<br/>Accounted for.</h1>
    <p>Cloud AI, durable workflows, and a receipt for every model and tool call.</p>
    <ol><li>Connect your team’s providers</li><li>Run a governed workflow</li><li>Review the work and its receipts</li></ol>
  </div><main className="signin-form"><h2>{cancel ? 'Add team / identity' : 'Sign in to your team'}</h2>
    <p>Use your existing team API key or signed JWT. Your role and projects come from the gateway.</p>
    <form onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError(undefined);
      try { const team = await api<Team>(token.trim(), '/v1/team'); onSignIn(token.trim(), team); setToken(''); }
      catch (error) { setError((error as Error).message); }
      finally { setBusy(false); }
    }}>
      <label htmlFor="credential">Team credential</label>
      <input id="credential" type="password" autoComplete="off" autoFocus required value={token} onChange={e => setToken(e.target.value)} aria-describedby="credential-help"/>
      <p id="credential-help" className="muted">Kept in memory only. Reloading or signing out clears credentials.</p>
      <ErrorMessage message={error}/>
      <div className="actions"><button disabled={busy}>{busy ? 'Verifying…' : 'Sign in'}</button>{cancel && <button type="button" className="secondary" onClick={cancel}>Back to workspace</button>}</div>
    </form>
    <details className="demo-help"><summary>Trying the local Compose demo?</summary>
      <p>Start <code>make compose-up</code>, then sign in with <code>local-development-only</code>. The demo uses local fake providers, with no cloud charges.</p>
      <p>For role testing: <code>demo-builder</code>, <code>demo-approver</code>, or <code>demo-viewer</code>.</p>
      <button className="secondary" onClick={() => setToken('local-development-only')}>Use demo credential</button>
    </details>
  </main></div>;
}

function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState(0);
  const [adding, setAdding] = useState(false);
  const [expired, setExpired] = useState(false);
  const [route, setRoute] = useState(location.hash.slice(1) || 'start');
  const sequence = useRef(0);
  const main = useRef<HTMLElement>(null);
  const session = sessions.find(value => value.id === selected);
  useEffect(() => {
    const change = () => setRoute(location.hash.slice(1) || 'start');
    const expire = () => { setSessions([]); setAdding(false); setExpired(true); };
    window.addEventListener('hashchange', change);
    window.addEventListener('aw:expired', expire);
    return () => { window.removeEventListener('hashchange', change); window.removeEventListener('aw:expired', expire); };
  }, []);
  useEffect(() => {
    main.current?.focus();
    const title = navigation.find(([id]) => id === route)?.[1] || (route === 'new' ? 'Run workflow' : 'Run detail');
    document.title = `${session ? title : 'Sign in'} · AgentWorkflows Console`;
  }, [route, session]);
  if (!session || adding) return <SignIn
    message={expired ? 'Your session expired. Sign in again to continue.' : undefined}
    cancel={session ? () => setAdding(false) : undefined}
    onSignIn={(token, team) => {
      const existing = sessions.find(value => value.token === token);
      const next = { token, team, id: existing?.id ?? ++sequence.current };
      setSessions(values => [...values.filter(value => value.id !== next.id), next]);
      setSelected(next.id); setAdding(false); setExpired(false);
    }}/>;
  const active = route.startsWith('run/') || route === 'new' ? 'runs' : route;
  return <div className="shell">
    <a className="skip" href="#main" onClick={e => { e.preventDefault(); main.current?.focus(); }}>Skip to content</a>
    <aside className="sidebar">
      <a href="#start" className="brand"><Icon name="brand"/><span>AgentWorkflows</span></a>
      <label htmlFor="team">Team / identity</label>
      <select id="team" value={selected} onChange={e => setSelected(Number(e.target.value))}>
        {sessions.map(value => <option key={value.id} value={value.id}>{value.team.team_id} / {value.team.role}{sessions.filter(s => s.team.team_id === value.team.team_id && s.team.role === value.team.role).length > 1 ? ` (${value.id})` : ''}</option>)}
      </select>
      <nav aria-label="Main navigation">{navigation.filter(([id]) => id !== 'providers' || session.team.role === 'admin').map(([id, text]) =>
        <a key={id} href={`#${id}`} aria-current={active === id ? 'page' : undefined}><Icon name={id}/>{text}</a>)}
      </nav>
      <div className="session-actions">
        <button onClick={() => setAdding(true)}><Icon name="add"/>Add team / identity</button>
        <button onClick={() => { setSessions([]); setExpired(false); }}><Icon name="signout"/>Sign out</button>
      </div>
    </aside>
    <main id="main" ref={main} tabIndex={-1} key={`${session.id}:${route}`}>
      {route === 'start' ? <GetStarted session={session}/> : route === 'runs' ? <Runs session={session}/> :
        route === 'new' ? <StartRun session={session}/> : route === 'approvals' ? <Approvals session={session}/> :
        route === 'providers' ? <Providers session={session}/> : route === 'costs' ? <Costs session={session}/> :
        /^run\/[a-f0-9-]{36}$/.test(route) ? <RunDetail session={session} runId={route.slice(4)}/> :
        <><h1>Page not found</h1><a href="#runs">Return to workflow runs</a></>}
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App/>);
