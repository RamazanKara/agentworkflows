import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, type AuthConfig, type BrowserSession, type Session, type Team } from './api';
import { ErrorMessage, Icon, Loading } from './ui';
import { Approvals, RunDetail, Runs, StartRun } from './runs';
import { Costs, GetStarted, Providers } from './team';
import { Triggers } from './triggers';
import { Keys } from './keys';
import './style.css';

const navigation = [
  ['start', 'Get started'], ['runs', 'Workflow runs'], ['approvals', 'Approvals'],
  ['triggers', 'Triggers'], ['keys', 'Members & keys'], ['providers', 'Providers & budgets'], ['costs', 'Costs'],
];

function SignIn({ onSignIn, cancel, message, config, csrfToken }: {
  onSignIn: (csrfToken: string, team: Team) => void; cancel?: () => void; message?: string;
  config?: AuthConfig; csrfToken: string;
}) {
  const [token, setToken] = useState('');
  const [error, setError] = useState(message);
  const [busy, setBusy] = useState(false);
  return <div className="signin"><div className="signin-story">
    <div className="brand"><Icon name="brand"/>AgentWorkflows</div>
    <h1>Good work.<br/>Accounted for.</h1>
    <p>Cloud AI, durable workflows, and a receipt for every model and tool call.</p>
    <ol><li>Connect your team’s providers</li><li>Run a governed workflow</li><li>Review the work and its receipts</li></ol>
  </div><main className="signin-form"><h2>{cancel ? 'Switch identity' : 'Sign in to your team'}</h2>
    {config?.oidc.enabled && <p><button onClick={() => location.assign(config.oidc.login_url)}>Sign in with {config.oidc.provider_name}</button></p>}
    <p>Use your existing team API key or signed JWT. Your role and projects come from the gateway.</p>
    <form onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError(undefined);
      try {
        const session = await api<BrowserSession>(csrfToken, '/v1/auth/session', { method: 'POST', body: JSON.stringify({ key: token.trim() }) });
        setToken('');
        const team = await api<Team>(session.csrf_token, '/v1/team');
        onSignIn(session.csrf_token, team);
      }
      catch (error) { setError((error as Error).message); }
      finally { setBusy(false); }
    }}>
      <label htmlFor="credential">Team credential</label>
      <input id="credential" type="password" autoComplete="off" autoFocus required value={token} onChange={e => setToken(e.target.value)} aria-describedby="credential-help"/>
      <p id="credential-help" className="muted">Your key is exchanged for a secure session. It is never saved in this browser.</p>
      <ErrorMessage message={error}/>
      <div className="actions"><button disabled={busy}>{busy ? 'Verifying…' : 'Sign in'}</button>{cancel && <button type="button" className="secondary" onClick={cancel}>Back to workspace</button>}</div>
    </form>
    <details className="demo-help"><summary>Trying the local Compose demo?</summary>
      <p>Start the stack from the quickstart, then sign in with <code>local-development-only</code>. The demo uses local fake providers, with no cloud charges.</p>
      <p>For role testing: <code>demo-builder</code>, <code>demo-approver</code>, or <code>demo-viewer</code>.</p>
      <button className="secondary" onClick={() => setToken('local-development-only')}>Use demo credential</button>
    </details>
  </main></div>;
}

function App() {
  const [session, setSession] = useState<Session>();
  const [config, setConfig] = useState<AuthConfig>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [adding, setAdding] = useState(false);
  const [expired, setExpired] = useState(false);
  const [route, setRoute] = useState(location.hash.slice(1) || 'start');
  const sequence = useRef(0);
  const main = useRef<HTMLElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    const options = { signal: controller.signal };
    const configuration = api<AuthConfig>('', '/v1/auth/config', options).then(setConfig);
    const restore = api<BrowserSession>('', '/v1/auth/session', options).then(async value => {
      const team = await api<Team>(value.csrf_token, '/v1/team', options);
      if (!controller.signal.aborted) setSession({ csrfToken: value.csrf_token, team, id: ++sequence.current });
    });
    Promise.allSettled([configuration, restore]).then(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const change = () => setRoute(location.hash.slice(1) || 'start');
    const expire = () => { setSession(undefined); setAdding(false); setExpired(true); };
    window.addEventListener('hashchange', change);
    window.addEventListener('aw:expired', expire);
    return () => { window.removeEventListener('hashchange', change); window.removeEventListener('aw:expired', expire); };
  }, []);
  useEffect(() => {
    main.current?.focus();
    const title = navigation.find(([id]) => id === route)?.[1] || (route === 'new' ? 'Run workflow' : 'Run detail');
    document.title = `${session ? title : 'Sign in'} · AgentWorkflows Console`;
  }, [route, session]);
  if (loading) return <Loading/>;
  if (!session || adding) return <SignIn config={config} csrfToken={session?.csrfToken || ''}
    message={expired ? 'Your session expired. Sign in again to continue.' : undefined}
    cancel={session ? () => setAdding(false) : undefined}
    onSignIn={(csrfToken, team) => {
      setSession({ csrfToken, team, id: ++sequence.current });
      setAdding(false); setExpired(false); setError('');
    }}/>;
  const active = route.startsWith('run/') || route === 'new' ? 'runs' : route;
  return <div className="shell">
    <a className="skip" href="#main" onClick={e => { e.preventDefault(); main.current?.focus(); }}>Skip to content</a>
    <aside className="sidebar">
      <a href="#start" className="brand"><Icon name="brand"/><span>AgentWorkflows</span></a>
      <p aria-label="Team / identity">{session.team.team_id} / {session.team.role}</p>
      <nav aria-label="Main navigation">{navigation.filter(([id]) => !['providers', 'keys'].includes(id) || session.team.role === 'admin').map(([id, text]) =>
        <a key={id} href={`#${id}`} aria-current={active === id ? 'page' : undefined}><Icon name={id}/>{text}</a>)}
      </nav>
      <div className="session-actions">
        <button onClick={() => setAdding(true)}><Icon name="add"/>Switch identity</button>
        <button onClick={async () => {
          try { await api(session.csrfToken, '/v1/auth/logout', { method: 'POST' }); setSession(undefined); setExpired(false); }
          catch (value) { setError((value as Error).message); }
        }}><Icon name="signout"/>Sign out</button>
        <ErrorMessage message={error}/>
      </div>
    </aside>
    <main id="main" ref={main} tabIndex={-1} key={`${session.id}:${route}`}>
      {route === 'start' ? <GetStarted session={session}/> : route === 'runs' ? <Runs session={session}/> :
        route === 'new' ? <StartRun session={session}/> : route === 'approvals' ? <Approvals session={session}/> :
        route === 'keys' ? <Keys session={session}/> :
        route === 'triggers' ? <Triggers session={session}/> : route === 'providers' ? <Providers session={session}/> : route === 'costs' ? <Costs session={session}/> :
        /^run\/[a-f0-9-]{36}$/.test(route) ? <RunDetail session={session} runId={route.slice(4)}/> :
        <><h1>Page not found</h1><a href="#runs">Return to workflow runs</a></>}
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App/>);
