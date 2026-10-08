import { useState } from 'react';
import { isDemo, money, number, providerName, useData, workflowName, type Budget, type CostRow, type Models, type Policy, type RunPage, type Session, type Team, type Usage } from './api';
import { Empty, ErrorMessage, Loading, Metrics, PageHeader, Refresh } from './ui';

const guide = 'https://github.com/RamazanKara/agentworkflows/blob/main/docs/workflows.md';

export function GetStarted({ session }: { session: Session }) {
  const models = useData<Models>(session.csrfToken, '/v1/models');
  const runs = useData<RunPage>(session.csrfToken, `/v1/workflow-runs?${new URLSearchParams({ project: session.team.projects[0] || '', limit: '1' })}`);
  const last = runs.data?.runs[0];
  const builder = ['admin', 'builder'].includes(session.team.role);
  return <><PageHeader title="Your first governed workflow" subtitle="Three steps from sign-in to an approved result."/>
    <ol className="onboarding">
      <li><span className="onboarding-number">01</span><div><h2>Check your models</h2>
        <p>These are the models your team can use. Provider keys stay on your server; nobody sees them here.</p>
        <ErrorMessage message={models.error}/>
        {models.data ? models.data.data.length ? <ul className="model-list">{models.data.data.map(model => <li key={model.id}><code>{model.id}</code>{model.owned_by && <span className="muted">{providerName(model.owned_by)}</span>}</li>)}</ul> : <p>No models yet. Ask your admin to connect a provider and approve a model.</p> : !models.error && <Loading/>}
        {isDemo(models.data) && <p className="callout">You’re on the Compose demo. Its built-in test models need no cloud keys and cost nothing.</p>}
        {session.team.role === 'admin' ? <a href="#providers">Connect a provider or review budgets</a> : <p className="muted">Your team admin manages providers.</p>}
      </div></li>
      <li><span className="onboarding-number">02</span><div><h2>Run the example workflow</h2><p>Research a topic, draft a briefing, review it, and publish. The example has a budget and a human approval step built in.</p>
        {builder ? <a className="button" href="#new">Run workflow</a> : <p>Your role can inspect work. Ask a builder to start the example.</p>}
      </div></li>
      <li><span className="onboarding-number">03</span><div><h2>Approve it and check the evidence</h2><p>Approve the draft in Approvals. Then open the run to see each step’s prompt, answer, cost and receipt.</p>
        <ErrorMessage message={runs.error}/>
        {last ? <a href={`#run/${last.run_id}`}>Open the latest run</a> : <a href="#runs">Explore workflow runs</a>}
      </div></li>
    </ol>
    <p className="muted">Bringing your own models? <a href={`${guide}#try-research--draft--approval--publish`}>Follow the team workflow guide</a>.</p>
  </>;
}

function CostTable({ title, rows }: { title: string; rows: Record<string, CostRow> }) {
  return <section><h2>{title}</h2><div className="panel">
    {Object.keys(rows).length ? <div className="table-scroll" tabIndex={0} role="region" aria-label={`${title} table`}><table className="stack numeric"><thead><tr><th>{title === 'By provider' ? 'Provider' : 'Workflow'}</th><th className="num">Calls</th><th className="num">Tokens</th><th className="num">Cost</th></tr></thead>
      <tbody>{Object.entries(rows).sort((a, b) => (b[1].cost_usd || 0) - (a[1].cost_usd || 0)).map(([name, row]) => <tr key={name}><th scope="row">{title === 'By provider' ? providerName(name) : workflowName(name)}</th><td data-label="Calls">{number(row.calls)}</td><td data-label="Tokens">{number(row.tokens)}</td><td data-label="Cost">{money(row.cost_usd)}</td></tr>)}</tbody></table></div> :
      <Empty title="No recorded spend yet"><p>Costs appear as governed calls finish. In-flight and unreported calls can retain reservations.</p></Empty>}
  </div></section>;
}

// 86400 → "Last 24 hours", 604800 → "Last 7 days".
const windowName = (seconds: number) => seconds % 86400 === 0 && seconds > 86400 ? `Last ${seconds / 86400} days` : `Last ${Math.round(seconds / 3600)} hours`;
const period = (seconds: number) => seconds === 86400 ? 'per day' : seconds === 604800 ? 'per week' : `per ${windowName(seconds).replace('Last ', '')}`;

function Usage({ value, limit }: { value: number; limit: number }) {
  return <span className="usage-bar" role="img" aria-label={`${Math.round(Math.min(value / limit, 1) * 100)}% used`}><span style={{ width: `${Math.min(value / limit, 1) * 100}%` }}/></span>;
}

export function Costs({ session }: { session: Session }) {
  const [revision, setRevision] = useState(0);
  const result = useData<Usage>(session.csrfToken, '/v1/usage', revision);
  const budget = useData<Budget>(session.csrfToken, '/v1/sandbox/budget', revision);
  const spend = result.data?.spend;
  const spent = spend?.reserved_and_spent_usd ?? result.data?.estimated_cost ?? 0;
  const held = spend?.reserved_and_spent_usd != null ? spend.reserved_and_spent_usd - (result.data?.estimated_cost || 0) : 0;
  const tokens = budget.data;
  return <><PageHeader title="Costs" subtitle="Where your team’s spend goes, by provider and workflow."><Refresh onClick={() => setRevision(v => v + 1)}/></PageHeader>
    <ErrorMessage message={result.error} retry={() => setRevision(v => v + 1)}/>
    {!result.data && !result.error && <Loading/>}
    {spend && <>
      <p className="scope">{spend.window_seconds ? windowName(spend.window_seconds) : 'Since the gateway started'}{spend.project && ` · Project ${spend.project}`}</p>
      <Metrics items={[
        ['Spent', <>{money(spent)}{held >= 0.005 && <small>incl. {money(held)} held for running work</small>}</>],
        ['Team limit', spend.cost_limit_usd ? <>{money(spend.cost_limit_usd)} <small className="inline">{spend.window_seconds ? period(spend.window_seconds) : ''}</small><Usage value={spent} limit={spend.cost_limit_usd}/></> : 'No limit'],
        ['Tokens', tokens ? tokens.limits.estimated_tokens ? <>{number(tokens.usage.estimated_tokens)} <small className="inline">of {number(tokens.limits.estimated_tokens)}</small><Usage value={tokens.usage.estimated_tokens} limit={tokens.limits.estimated_tokens}/></> : number(tokens.usage.estimated_tokens) : '—'],
      ]}/>
      {spend.project && <p className="callout">This sign-in is limited to one project. Team-wide spend is hidden; the tables cover your project only.</p>}
      <CostTable title="By provider" rows={spend.providers}/>
      <CostTable title="By workflow" rows={spend.workflows || {}}/>
      <p className="muted">Estimates from configured prices, not a provider invoice. A provider’s row includes amounts held for failed attempts that reported no usage. Calls made outside a workflow appear only under By provider.</p>
    </>}
  </>;
}

export function Providers({ session }: { session: Session }) {
  if (session.team.role !== 'admin') return <Empty title="Team admin access required"><p>Sign in with your team admin credential to inspect provider setup and shared budgets.</p><a href="#runs">View workflow runs</a></Empty>;
  return <ProviderSettings session={session}/>;
}

function ProviderSettings({ session }: { session: Session }) {
  const [revision, setRevision] = useState(0);
  const [copied, setCopied] = useState('');
  const team = useData<Team>(session.csrfToken, '/v1/team', revision);
  const budget = useData<Budget>(session.csrfToken, '/v1/sandbox/budget', revision);
  const usage = useData<Usage>(session.csrfToken, '/v1/usage', revision);
  const policies = useData<{ workflows: Record<string, Policy> }>(session.csrfToken, '/v1/workflow-policies', revision);
  const models = useData<Models>(session.csrfToken, '/v1/models');
  const settings = team.data;
  const providers = Object.entries(settings?.provider_configuration || {});
  const workflows = Object.entries(policies.data?.workflows || {});
  const usedBy = (provider: string) => workflows.filter(([, policy]) => policy.allowedProviders.includes(provider)).map(([name]) => workflowName(name));
  // Point the setup steps at the first provider still missing its key; otherwise show how to add one.
  const missing = providers.find(([, configuration]) => !configuration.configured);
  const [target, variable] = missing ? [missing[0], missing[1].environment_variable] : ['openai', 'OPENAI_API_KEY'];
  const snippet = `# Merge into this team's existing SandboxPolicySet entry.\nsandboxId: ${JSON.stringify(session.team.team_id)}\nproviderCredentials:\n${[...providers.map(([name, c]) => [name, c.environment_variable]), ...(missing || providers.some(([name]) => name === target) ? [] : [[target, variable]])].map(([name, env]) => `  ${name}: ${env}`).join('\n')}\nbudgets:\n  estimatedTokenLimit: ${budget.data?.limits.estimated_tokens || 200000}\n  costLimitUsd: ${settings?.cost_limit_usd || 25}`;
  const spent = usage.data?.spend.reserved_and_spent_usd ?? usage.data?.estimated_cost;
  return <><PageHeader title="Providers & budgets" subtitle="Your team’s provider keys, models and spending limits."><Refresh onClick={() => setRevision(v => v + 1)}/></PageHeader>
    <ErrorMessage message={team.error || budget.error || usage.error || policies.error} retry={() => setRevision(v => v + 1)}/>
    {!settings && !team.error && <Loading/>}
    <Metrics items={[
      ['Team limit', settings ? settings.cost_limit_usd ? money(settings.cost_limit_usd) : 'No limit' : '—'],
      ['Spent', money(spent)],
      ['Tokens', budget.data ? <>{number(budget.data.usage.estimated_tokens)} <small className="inline">of {budget.data.limits.estimated_tokens ? number(budget.data.limits.estimated_tokens) : 'no limit'}</small></> : '—'],
    ]}/>
    <p className="muted">Your team ID is <code>{session.team.team_id}</code> (the <code>sandboxId</code> in your team policy).</p>
    <h2>Provider connections</h2><div className="panel">
      {providers.length ? <div className="table-scroll" tabIndex={0} role="region" aria-label="Provider connections table"><table className="stack"><thead><tr><th>Provider</th><th>Secret name</th><th>Status</th></tr></thead><tbody>
        {providers.map(([provider, configuration]) => <tr key={provider}><th scope="row">{providerName(provider)}</th><td data-label="Secret name"><code>{configuration.environment_variable}</code></td><td data-label="Status">{configuration.configured
          ? <span className="badge recorded">Key present</span>
          : <><span className="badge awaiting_approval">Key missing</span>{usedBy(provider).length > 0 && <small>Needed by {usedBy(provider).join(', ')}</small>}</>}</td></tr>)}
      </tbody></table></div> : <Empty title="Connect your first provider"><p>Choose a cloud provider and follow the steps below.</p></Empty>}
    </div><p className="muted">Key presence is a configuration check, not a live provider test. Secret values never appear here.</p>
    <section className="setup panel"><h2>{missing ? `Connect ${providerName(target)}` : 'Add a provider or change a budget'}</h2>
      <ol><li>Store the {providerName(target)} key in the gateway’s <code>{variable}</code> environment variable or Kubernetes Secret.</li>
        <li>Merge the fragment below into your team policy. Keep your existing providers, projects, tools and workflows.</li>
        <li>Approve the provider’s models, prices and network access in your gateway configuration. Restart the gateway, then refresh this page.</li></ol>
      <pre>{snippet}</pre><div className="actions"><button className="secondary" onClick={async () => {
        try { await navigator.clipboard.writeText(snippet); setCopied('Configuration copied.'); }
        catch { setCopied('Clipboard unavailable. Select and copy the configuration above.'); }
      }}>Copy policy fragment</button><span role="status">{copied}</span></div>
      <p className="setup-links"><a href={`${guide}#teams-projects-and-roles`}>Team setup and Secret instructions</a> · <a href="https://github.com/RamazanKara/agentworkflows/blob/main/docs/model-selection.md#cloud-routes-milestone-1">Cloud routes and prices</a></p>
      {isDemo(models.data) && <p className="callout">You’re on the Compose demo. Its built-in test models need no cloud keys and cost nothing, so you can <a href="#new">run the example</a> right away.</p>}
    </section>
    <section><h2>Workflow budgets</h2><div className="panel"><div className="table-scroll" tabIndex={0} role="region" aria-label="Workflow budgets table"><table className="stack numeric"><thead><tr><th>Workflow</th><th>Providers</th><th className="num">Token limit</th><th className="num">Cost limit</th></tr></thead><tbody>{workflows.map(([name, policy]) => <tr key={name}><th scope="row">{workflowName(name)}</th><td data-label="Providers">{policy.allowedProviders.map(providerName).join(', ') || 'Team policy'}</td><td data-label="Token limit">{number(policy.tokenLimit)}</td><td data-label="Cost limit">{money(policy.costLimitUsd)}</td></tr>)}</tbody></table></div></div><p className="muted">Budget changes apply to new runs. Runs in progress keep their budget.</p></section>
  </>;
}
