import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, label, type AuthConfig, type BrowserSession, type Session, type Team } from './api';
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

// Reasons the gateway's OIDC callback can send a browser back with.
const signinErrors: Record<string, string> = {
  expired: 'That sign-in attempt expired. Start again.',
  declined: 'Sign-in was cancelled at your company account.',
  rejected: 'Your company account is not linked to a team here yet. Ask your team admin to check your team and role.',
  unavailable: 'Company sign-in is unavailable right now. Try again in a moment, or use an API key.',
};
const local = ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);

function SignIn({ onSignIn, cancel, message, config, csrfToken }: {
  onSignIn: (csrfToken: string, team: Team) => void; cancel?: () => void; message?: string;
  config?: AuthConfig; csrfToken: string;
}) {
  const [token, setToken] = useState('');
  const [error, setError] = useState(message);
  const [busy, setBusy] = useState(false);
  const sso = Boolean(config?.oidc.enabled);
  return <div className="signin"><div className="signin-story">
    <div className="brand"><Icon name="brand"/>AgentWorkflows</div>
    <h1>Good work.<br/>Accounted for.</h1>
    <p>Cloud AI, durable workflows, and a receipt for every model and tool call.</p>
    <ol><li>Connect your team’s providers</li><li>Run a governed workflow</li><li>Review the work and its receipts</li></ol>
  </div><main className="signin-form"><h2>{cancel ? 'Switch account' : 'Sign in to your team'}</h2>
    <p>{sso ? 'Use your company account. Your team and role come with it.' : 'Use the API key your team admin gave you. Your team and role come with it.'}</p>
    {sso && <>
      <button className="sso" onClick={() => location.assign(config!.oidc.login_url)}><Icon name="signin"/>Sign in with your company account</button>
      <p className="divider"><span>or use an API key</span></p>
    </>}
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
      <label htmlFor="credential">API key</label>
      <input id="credential" type="password" autoComplete="off" autoFocus={!sso} required value={token} onChange={e => setToken(e.target.value)} aria-describedby="credential-help"/>
      <p id="credential-help" className="muted">Exchanged for a secure session and never stored in this browser. Signed JWTs work too.</p>
      <ErrorMessage message={error}/>
      <div className="actions"><button className={sso ? 'secondary' : undefined} disabled={busy}>{busy ? 'Verifying…' : 'Sign in'}</button>{cancel && <button type="button" className="secondary" onClick={cancel}>Back to workspace</button>}</div>
    </form>
    {local && <details className="demo-help"><summary>Trying the local Compose demo?</summary>
      <p>Start the stack from the quickstart, then sign in with <code>local-development-only</code>. The demo uses local fake providers, with no cloud charges.</p>
      <p>For role testing: <code>demo-builder</code>, <code>demo-approver</code>, or <code>demo-viewer</code>.</p>
      <button className="secondary" onClick={() => setToken('local-development-only')}>Use demo key</button>
    </details>}
  </main></div>;
}

function App() {
  const [session, setSession] = useState<Session>();
  const [config, setConfig] = useState<AuthConfig>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [adding, setAdding] = useState(false);
  const [expired, setExpired] = useState(false);
  const [signinError] = useState(() => {
    const reason = new URLSearchParams(location.search).get('signin_error');
    if (reason) history.replaceState(null, '', location.pathname + location.hash);
    return reason ? signinErrors[reason] ?? signinErrors.unavailable : undefined;
  });
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
    message={expired ? 'Your session expired. Sign in again to continue.' : signinError}
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
      <section className="identity" aria-label="Signed in as"><span>{session.team.team_id}</span><span className="role">{label(session.team.role)}</span></section>
      <nav aria-label="Main navigation">{navigation.filter(([id]) => !['providers', 'keys'].includes(id) || session.team.role === 'admin').map(([id, text]) =>
        <a key={id} href={`#${id}`} aria-current={active === id ? 'page' : undefined}><Icon name={id}/>{text}</a>)}
      </nav>
      <div className="session-actions">
        <button onClick={() => setAdding(true)}><Icon name="add"/>Switch account</button>
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
