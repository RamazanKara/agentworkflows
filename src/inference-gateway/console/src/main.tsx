import { Component, lazy, Suspense, useEffect, useRef, useState, type ComponentType, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { api, label, reviewable, type AuthConfig, type BrowserSession, type RunPage, type Session, type Team } from './api';
import { confirmLeave, DotList, ErrorMessage, Icon, Loading, PageHeader } from './ui';
import './fonts.css';
import './style.css';

// Each page is its own chunk: signing in downloads only the shell, and a page loads when first opened.
const chunks = {
  runs: () => import('./runs'), team: () => import('./team'), insights: () => import('./insights'),
  settings: () => import('./settings'), triggers: () => import('./triggers'), keys: () => import('./keys'),
  audit: () => import('./audit'), lifecycle: () => import('./lifecycle'), invitations: () => import('./invitations'),
};
const page =<K extends string, M extends { [P in K]: ComponentType<any> }>(load: () => Promise<M>, name: K) =>
  lazy(() => load().then(module => ({ default: module[name] })));
const Runs = page(chunks.runs, 'Runs'), StartRun = page(chunks.runs, 'StartRun'), Approvals = page(chunks.runs, 'Approvals'), RunDetail = page(chunks.runs, 'RunDetail');
const GetStarted = page(chunks.team, 'GetStarted'), Costs = page(chunks.team, 'Costs'), Providers = page(chunks.team, 'Providers');
const Insights = page(chunks.insights, 'Insights'), TeamConfiguration = page(chunks.settings, 'TeamConfiguration'), Triggers = page(chunks.triggers, 'Triggers');
const Keys = page(chunks.keys, 'Keys'), AuditLog = page(chunks.audit, 'AuditLog'), AcceptInvitation = page(chunks.invitations, 'AcceptInvitation');
const Templates = page(chunks.lifecycle, 'Templates'), WorkflowSecrets = page(chunks.lifecycle, 'WorkflowSecrets'), TeamData = page(chunks.lifecycle, 'TeamData');
// Pointing at a link starts loading its page, so it is usually ready by the click.
const prefetch: Record<string, keyof typeof chunks> = {
  start: 'team', templates: 'lifecycle', runs: 'runs', approvals: 'runs', triggers: 'triggers', keys: 'keys', team: 'settings',
  providers: 'team', costs: 'team', insights: 'insights', audit: 'audit', secrets: 'lifecycle', data: 'lifecycle',
};

const navigation = [
  ['start', 'Get started'], ['templates', 'Templates'], ['runs', 'Workflow runs'], ['approvals', 'Approvals'],
  ['triggers', 'Triggers'], ['keys', 'Members & keys'], ['team', 'Team settings'], ['providers', 'Providers & budgets'], ['costs', 'Costs'], ['insights', 'Insights'],
  ['audit', 'Audit log'],
  ['secrets', 'Workflow secrets'], ['data', 'Data & privacy'],
];
const groups: [string, string[]][] = [
  ['Workflows', ['start', 'templates', 'runs', 'approvals', 'triggers']],
  ['Spend', ['providers', 'costs', 'insights']],
  ['Team', ['keys', 'team', 'audit', 'secrets', 'data']],
];
const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map(word => word[0]).join('').toUpperCase();
const runRoute = /^run\/[a-f0-9-]{36}$/i;
// "runs?project=a" → ["runs", "project=a"]; "run/<id>" and "new/<workflow>" keep their path.
const split = (route: string) => { const at = route.indexOf('?'); return at < 0 ? [route, ''] : [route.slice(0, at), route.slice(at + 1)]; };
const pageTitle = (route: string) => {
  const [path] = split(route);
  return navigation.find(([id]) => id === path)?.[1] || (path === 'new' || path.startsWith('new/') ? 'Run workflow'
    : path === 'home' ? 'Workspace' : runRoute.test(path) ? 'Run detail' : path.startsWith('invite/') ? 'Join your team' : 'Page not found');
};

// Reasons the gateway's OIDC callback can send a browser back with.
const signinErrors: Record<string, string> = {
  expired: 'That sign-in attempt expired. Start again.',
  declined: 'Sign-in was cancelled at your company account.',
  rejected: 'Your company account is not linked to a team with an allowed role. Ask your team admin to check your team, project and group membership; conflicting group roles also prevent sign-in.',
  unavailable: 'Company sign-in is unavailable right now. Try again in a moment, or use an API key.',
};
const local = ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);

function SignIn({ onSignIn, cancel, message, config, csrfToken }: {
  onSignIn: (csrfToken: string, team: Team, principal: BrowserSession['principal']) => void; cancel?: () => void; message?: string;
  config?: AuthConfig; csrfToken: string;
}) {
  const [token, setToken] = useState('');
  const [error, setError] = useState(message);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const sso = Boolean(config?.oidc.enabled);
  return <div className="signin"><div className="signin-story">
    <div className="brand"><Icon name="brand"/>AgentWorkflows</div>
    <h1>Good work.<br/>Accounted for.</h1>
    <p>Cloud AI, durable workflows, and a receipt for every model and tool call.</p>
    <ol><li>Connect your team’s providers</li><li>Run a governed workflow</li><li>Review the work and its receipts</li></ol>
  </div><main className="signin-form"><h2>{cancel ? 'Switch account' : 'Sign in to your team'}</h2>
    <p>{sso ? 'Use your company account. Your team and role come with it.' : 'Use the API key your team admin gave you. Your team and role come with it.'}</p>
    {sso && <>
      <button className="sso" onClick={() => location.assign(config!.oidc.login_url)}><Icon name="signin"/>Sign in with SSO</button>
      <p className="divider"><span>or use an API key</span></p>
    </>}
    <form onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError(undefined);
      try {
        const session = await api<BrowserSession>(csrfToken, '/v1/auth/session', { method: 'POST', body: JSON.stringify({ key: token.trim() }) });
        setToken('');
        const team = await api<Team>(session.csrf_token, '/v1/team');
        onSignIn(session.csrf_token, team, session.principal);
      }
      catch (error) { setError((error as Error).message); input.current?.focus(); }
      finally { setBusy(false); }
    }}>
      <label htmlFor="credential">API key</label>
      <input id="credential" ref={input} type="password" autoComplete="off" autoFocus={!sso && matchMedia('(pointer: fine)').matches} required value={token} onChange={e => setToken(e.target.value)} aria-describedby="credential-help" aria-invalid={error ? true : undefined}/>
      <p id="credential-help" className="muted">Exchanged for a secure session and never stored in this browser.</p>
      <ErrorMessage message={error}/>
      <div className="actions"><button className={sso ? 'secondary' : undefined} disabled={busy}>{busy ? 'Verifying…' : 'Sign in'}</button>{cancel && <button type="button" className="secondary" onClick={cancel}>Back to workspace</button>}</div>
    </form>
    {local && !sso && <details className="demo-help"><summary>Using the Compose demo?</summary>
      <p>Sign in with <code>local-development-only</code>. The demo’s built-in test models need no cloud keys and cost nothing.</p>
      <p>For role testing: <code>demo-builder</code>, <code>demo-approver</code>, or <code>demo-viewer</code>.</p>
      <button className="secondary" onClick={() => setToken('local-development-only')}>Use demo key</button>
    </details>}
  </main></div>;
}

// Opening the console without a page lands on pending approvals, then runs, and on Get started for a new team.
function Landing({ session }: { session: Session }) {
  useEffect(() => {
    const controller = new AbortController();
    // A waiting run counts only if this reviewer may decide it now (or the gateway predates escalation).
    const first = async (project: string, status?: string) => {
      const query = new URLSearchParams({ project, limit: '100', ...(status ? { status } : {}) });
      try {
        for (;;) {
          const page = await api<RunPage>(session.csrfToken, `/v1/workflow-runs?${query}`, { signal: controller.signal });
          if (status ? page.runs.some(reviewable) : page.runs.length) return true;
          if (page.next_cursor === null) return false;
          query.set('cursor', page.next_cursor);
        }
      } catch { return false; }
    };
    const any = (status?: string) => Promise.all(session.team.projects.map(project => first(project, status))).then(found => found.some(Boolean));
    const reviewer = ['admin', 'approver'].includes(session.team.role);
    // Approvals and runs share a chunk, so it downloads while the gateway is asked where to land.
    void chunks.runs().catch(() => undefined);
    (async () => {
      const page = reviewer && await any('awaiting_approval') ? 'approvals' : await any() ? 'runs' : 'start';
      if (!controller.signal.aborted) location.replace(`#${page}`);
    })();
    return () => controller.abort();
  }, [session]);
  return <Loading/>;
}

// A page chunk fails to load when the console was upgraded since this tab opened; a reload fetches the new one.
// Any other error stays inside the page, so navigation and sign-out keep working.
class PageBoundary extends Component<{ children: ReactNode }, { error?: Error }> {
  state: { error?: Error } = {};
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const stale = /dynamically imported module|Importing a module script failed/i.test(error.message);
    return <div className="panel"><div className="empty"><h2>This page could not load</h2>
      <p>{stale ? 'The console was updated or the connection dropped. Reload to get the latest version.' : 'Something went wrong while showing this page. Reload to try again; if it keeps happening, tell your operator.'}</p>
      <button onClick={() => location.reload()}>Reload the console</button></div></div>;
  }
}

function Page({ route, session }: { route: string; session: Session }) {
  const [path, query] = split(route);
  return path === 'home' ? <Landing session={session}/> : path === 'start' ? <GetStarted session={session}/> : path === 'runs' ? <Runs session={session} initial={query}/> :
    path === 'new' || path.startsWith('new/') ? <StartRun session={session} initial={decodeURIComponent(path.slice(4))}/> : path === 'approvals' ? <Approvals session={session} initial={query}/> :
    path === 'keys' ? <Keys session={session}/> :
    path === 'audit' ? <AuditLog session={session}/> :
    path === 'templates' ? <Templates session={session}/> :
    path === 'secrets' ? <WorkflowSecrets session={session}/> :
    path === 'data' ? <TeamData session={session}/> :
    path === 'team' ? <TeamConfiguration session={session}/> :
    path === 'triggers' ? <Triggers session={session}/> : path === 'providers' ? <Providers session={session}/> : path === 'costs' ? <Costs session={session}/> : path === 'insights' ? <Insights session={session} initial={query}/> :
    runRoute.test(path) ? <RunDetail session={session} runId={path.slice(4).toLowerCase()}/> :
    <><PageHeader title="Page not found" subtitle="This link does not match a console page."/><a className="tap" href="#runs">Return to workflow runs</a></>;
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
  const [route, setRoute] = useState(location.hash.slice(1) || 'home');
  // Counts navigations, so following a link to the page already open (with different filters) starts it fresh.
  const [visit, setVisit] = useState(0);
  const [menu, setMenu] = useState(false);
  const sequence = useRef(0);
  const main = useRef<HTMLElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    const options = { signal: controller.signal };
    const configuration = api<AuthConfig>('', '/v1/auth/config', options).then(setConfig);
    const restore = api<BrowserSession>('', '/v1/auth/session', options).then(async value => {
      const team = await api<Team>(value.csrf_token, '/v1/team', options);
      if (!controller.signal.aborted) setSession({ csrfToken: value.csrf_token, team, id: ++sequence.current, name: value.principal.name, keyId: value.principal.key_id, reviewerId: value.principal.sub || value.principal.key_id });
    });
    Promise.allSettled([configuration, restore]).then(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const change = (event: HashChangeEvent) => {
      // Unsaved edits stay when the person cancels; the address goes back to the page they are on.
      if (!confirmLeave()) { history.replaceState(null, '', event.oldURL); return; }
      setRoute(location.hash.slice(1) || 'home'); setVisit(value => value + 1); setMenu(false);
    };
    const expire = () => { setSession(undefined); setAdding(false); setExpired(true); };
    window.addEventListener('hashchange', change);
    window.addEventListener('aw:expired', expire);
    return () => { window.removeEventListener('hashchange', change); window.removeEventListener('aw:expired', expire); };
  }, []);
  useEffect(() => {
    main.current?.focus();
    document.title = `${route.startsWith('invite/') ? 'Join your team' : session ? pageTitle(route) : 'Sign in'} · AgentWorkflows Console`;
  }, [route, session, visit]);
  if (loading) return <Loading/>;
  if (route.startsWith('invite/')) return <Suspense fallback={<Loading/>}><PageBoundary><AcceptInvitation token={decodeURIComponent(route.slice(7))}/></PageBoundary></Suspense>;
  if (!session || adding) return <SignIn config={config} csrfToken={session?.csrfToken || ''}
    message={expired ? 'Your session expired. Sign in again to continue.' : signinError}
    cancel={session ? () => setAdding(false) : undefined}
    onSignIn={(csrfToken, team, principal) => {
      setSession({ csrfToken, team, id: ++sequence.current, name: principal.name, keyId: principal.key_id, reviewerId: principal.sub || principal.key_id });
      setAdding(false); setExpired(false); setError('');
    }}/>;
  const [path] = split(route);
  const active = runRoute.test(path) || path === 'new' || path.startsWith('new/') ? 'runs' : path;
  const teamName = label(session.team.team_id.replaceAll('-', ' '));
  const closeMenu = () => { setMenu(false); menuButton.current?.focus(); };
  // Phones get a compact top bar; the menu button opens the same navigation as the desktop sidebar.
  return <div className={menu ? 'shell menu-open' : 'shell'} onKeyDown={e => { if (e.key === 'Escape' && menu) closeMenu(); }}>
    <a className="skip" href="#main" onClick={e => { e.preventDefault(); main.current?.focus(); }}>Skip to content</a>
    <aside className="sidebar">
      <a href="#start" className="brand"><Icon name="brand"/><span>AgentWorkflows</span></a>
      <section className="identity" aria-label="Signed in as"><span className="avatar" aria-hidden="true">{initials(session.name || teamName)}</span>{session.name && <span className="who">{session.name}</span>}<span className="team">{teamName}</span><span className="role">{label(session.team.role)}</span></section>
      <button ref={menuButton} className="menu-button" aria-expanded={menu} aria-controls="app-menu" aria-label={menu ? 'Close menu' : 'Open menu'} onClick={() => setMenu(value => !value)}><Icon name={menu ? 'close' : 'menu'}/></button>
      <div id="app-menu" className="app-menu">
        <p className="menu-who">{session.name && <strong>{session.name}</strong>}<DotList items={[teamName, label(session.team.role)]}/></p>
        <nav aria-label="Main navigation">{groups.map(([group, ids]) => {
          const links = navigation.filter(([id]) => ids.includes(id) && (!['keys', 'audit', 'secrets', 'data'].includes(id) || session.team.role === 'admin'));
          return links.length > 0 && <div className="nav-group" key={group}><h2>{group}</h2>{links.map(([id, text]) => {
            const load = () => { void chunks[prefetch[id]]?.().catch(() => undefined); };
            return <a key={id} href={`#${id}`} aria-current={active === id ? 'page' : undefined} onClick={() => setMenu(false)} onPointerEnter={load} onFocus={load}><Icon name={id}/>{text}</a>;
          })}</div>;
        })}
        </nav>
        <div className="session-actions">
          <button onClick={() => { if (!confirmLeave()) return; setMenu(false); setAdding(true); }}><Icon name="swap"/>Switch account</button>
          <button onClick={async () => {
            if (!confirmLeave()) return;
            try { await api(session.csrfToken, '/v1/auth/logout', { method: 'POST' }); setSession(undefined); setExpired(false); setMenu(false); }
            catch (value) { setError((value as Error).message); }
          }}><Icon name="signout"/>Sign out</button>
          <ErrorMessage message={error}/>
        </div>
      </div>
    </aside>
    <main id="main" ref={main} tabIndex={-1} key={`${session.id}:${route}:${visit}`}>
      <PageBoundary><Suspense fallback={<Loading/>}><Page route={route} session={session}/></Suspense></PageBoundary>
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App/>);
