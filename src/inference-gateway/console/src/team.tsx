import { useState } from 'react';
import { date, isDemo, missingKeys, money, noProviderKeys, number, providerList, providerName, useData, workflowName, type Budget, type CostRow, type Models, type Policy, type RunPage, type Session, type Team, type Usage } from './api';
import { Empty, ErrorMessage, Icon, Loading, Metrics, PageHeader, Refresh } from './ui';

const guide = 'https://github.com/RamazanKara/agentworkflows/blob/main/docs/workflows.md';
const routes = 'https://github.com/RamazanKara/agentworkflows/blob/main/docs/model-selection.md#cloud-routes-milestone-1';

export function GetStarted({ session }: { session: Session }) {
  const models = useData<Models>(session.csrfToken, '/v1/models');
  const runs = useData<RunPage>(session.csrfToken, `/v1/workflow-runs?${new URLSearchParams({ project: session.team.projects[0] || '', limit: '1' })}`);
  const last = runs.data?.runs[0];
  const builder = ['admin', 'builder'].includes(session.team.role);
  const blocked = noProviderKeys(session.team);
  const keyMissing = (provider: string) => session.team.provider_configuration?.[provider]?.configured === false;
  const missing = missingKeys(session.team);
  return <><PageHeader title="Your first governed workflow" subtitle="Three steps from sign-in to an approved result."/>
    {isDemo(models.data) && <DemoNote/>}
    <ol className="onboarding">
      <li><span className="onboarding-number">01</span><div><h2>{blocked ? 'Add a provider key' : 'Check your models'}</h2>
        {blocked ? <p className="note-warn">No provider key yet. {providerList(missing)} {missing.length > 1 ? 'keys are' : 'key is'} missing, so runs fail until you add one.</p>
          : <p>These are the models your team can use. Provider keys stay on your server; nobody sees them here.</p>}
        <ErrorMessage message={models.error}/>
        {models.data ? models.data.data.length ? <ul className="model-list">{models.data.data.map(model => <li key={model.id}><code>{model.id}</code>{model.owned_by && <span className="muted">{providerName(model.owned_by)}</span>}{!model.simulated && keyMissing(model.owned_by) && <span className="badge awaiting_approval">Key missing</span>}</li>)}</ul> : <p>No models yet. Ask your admin to connect a provider and approve a model.</p> : !models.error && <Loading/>}
        {session.team.role === 'admin' ? blocked ? <a className="button" href="#providers">Connect a provider</a> : <a className="tap" href="#providers">Connect a provider or review budgets</a> : <p className="muted">Your team admin manages providers.</p>}
      </div></li>
      <li><span className="onboarding-number">02</span><div><h2>Run the example workflow</h2><p>Research a topic, draft a briefing, review it, and publish. The example has a budget and a human approval step built in.</p>
        {builder ? blocked ? <><a className="button secondary" href="#new">Run workflow</a><p className="muted">Runs work once a provider key is added.</p></> : <a className="button" href="#new">Run workflow</a> : <p>Your role can inspect work. Ask a builder to start the example.</p>}
      </div></li>
      <li><span className="onboarding-number">03</span><div><h2>Approve it and check the evidence</h2><p>Approve the draft in Approvals. Then open the run to see each step’s prompt, answer, cost and receipt.</p>
        <ErrorMessage message={runs.error}/>
        {last ? <a className="tap" href={`#run/${last.run_id}`}>Open the latest run</a> : <a className="tap" href="#runs">Explore workflow runs</a>}
      </div></li>
    </ol>
    <p className="muted">Writing your own workflow? <a href={`${guide}#try-research--draft--approval--publish`}>Follow the team workflow guide</a>.</p>
  </>;
}

// Shown only when the gateway routes the Compose demo's simulated models.
function DemoNote() {
  return <p className="callout demo-note">You’re on the Compose demo. Its models are simulated, so no provider is billed; costs use demo prices.</p>;
}

function CostTable({ title, rows }: { title: string; rows: Record<string, CostRow> }) {
  return <section><h2>{title}</h2><div className="panel">
    {Object.keys(rows).length ? <div className="table-scroll" tabIndex={0} role="region" aria-label={`${title} table`}><table className="stack numeric"><thead><tr><th>{title === 'By provider' ? 'Provider' : 'Workflow'}</th><th className="num">Calls</th><th className="num">Tokens</th><th className="num">Cost</th></tr></thead>
      <tbody>{Object.entries(rows).sort((a, b) => (b[1].cost_usd || 0) - (a[1].cost_usd || 0)).map(([name, row]) => <tr key={name}><th scope="row">{title === 'By provider' ? providerName(name) : workflowName(name)}</th><td data-label="Calls">{number(row.calls)}</td><td data-label="Tokens">{number(row.tokens)}</td><td data-label="Cost">{money(row.cost_usd)}</td></tr>)}</tbody></table></div> :
      <Empty title="No recorded spend yet"><p>Costs appear as governed calls finish. In-flight and unreported calls can retain reservations.</p></Empty>}
  </div></section>;
}

// Budgets reset on fixed UTC windows; a one-day window reads as "today".
const scope = (seconds?: number) => seconds === 86400 ? 'today' : seconds === 3600 ? 'this hour' : 'this window';

function Usage({ value, limit }: { value: number; limit: number }) {
  return <span className="usage-bar" role="img" aria-label={`${Math.round(Math.min(value / limit, 1) * 100)}% used`}><span style={{ width: `${Math.min(value / limit, 1) * 100}%` }}/></span>;
}

// Spend and tokens against their limits, shared by Costs and Providers & budgets.
function SpendTiles({ usage, budget }: { usage?: Usage; budget?: Budget }) {
  const spend = usage?.spend;
  const spent = spend?.reserved_and_spent_usd ?? usage?.estimated_cost;
  const held = spend?.reserved_and_spent_usd != null ? spend.reserved_and_spent_usd - (usage?.estimated_cost || 0) : 0;
  const period = scope(spend?.window_seconds);
  return <Metrics items={[
    [`Spent ${period}`, spent == null ? '—' : <>{money(spent)}{spend?.cost_limit_usd ? <> <small className="inline">of {money(spend.cost_limit_usd)}</small><Usage value={spent} limit={spend.cost_limit_usd}/></> : <small>No team limit</small>}{held >= 0.005 && <small>Includes {money(held)} held for running work</small>}</>],
    [`Tokens ${period}`, budget ? <>{number(budget.usage.estimated_tokens)}{budget.limits.estimated_tokens ? <> <small className="inline">of {number(budget.limits.estimated_tokens)}</small><Usage value={budget.usage.estimated_tokens} limit={budget.limits.estimated_tokens}/></> : <small>No token limit</small>}</> : '—'],
  ]}/>;
}

export function Costs({ session }: { session: Session }) {
  const [revision, setRevision] = useState(0);
  const result = useData<Usage>(session.csrfToken, '/v1/usage', revision);
  const budget = useData<Budget>(session.csrfToken, '/v1/sandbox/budget', revision);
  const spend = result.data?.spend;
  return <><PageHeader title="Costs" subtitle="Where your team’s spend goes, by provider and workflow."><Refresh onClick={() => setRevision(v => v + 1)}/></PageHeader>
    <ErrorMessage message={result.error} retry={() => setRevision(v => v + 1)}/>
    {!result.data && !result.error && <Loading/>}
    {spend && <>
      <p className="scope">{spend.window_seconds === 86400 ? 'Today (UTC)' : spend.window_seconds ? `Since ${date(spend.window_start)}` : 'Since the gateway started'}{spend.project && ` · Project ${spend.project}`}</p>
      <SpendTiles usage={result.data} budget={budget.data}/>
      {spend.project && <p className="callout">This sign-in is limited to one project. Team-wide spend is hidden; the tables cover your project only.</p>}
      <CostTable title="By provider" rows={spend.providers}/>
      <CostTable title="By workflow" rows={spend.workflows || {}}/>
      <p className="muted">Estimates from configured prices, not a provider invoice. A provider’s row includes budget reserved for failed calls the provider didn’t report. Calls made outside a workflow appear only under By provider.</p>
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
  const needed = (provider: string) => usedBy(provider).length > 0;
  const missing = providers.find(([provider, configuration]) => !configuration.configured && needed(provider));
  const [target, variable] = missing ? [missing[0], missing[1].environment_variable] : ['openai', 'OPENAI_API_KEY'];
  const others = providers.filter(([name, configuration]) => !configuration.configured && needed(name) && name !== target).map(([name]) => name);
  // The Helm chart wires OpenAI and Anthropic keys from a Secret; a listed model means the route exists.
  const helmSecret = ['openai', 'anthropic'].includes(target) && variable === `${target.toUpperCase()}_API_KEY` ? `${target}-api-key` : '';
  const routed = Boolean(models.data?.data.some(model => model.owned_by === target));
  // Uses the install guide's release and namespace names; the comment says to change them.
  const helmCommands = `# Release "aw" in namespace "aw", as in the install guide; change both if yours differ.
read -rs -p '${providerName(target)} API key: ' PROVIDER_KEY; echo
printf '%s' "$PROVIDER_KEY" | kubectl create secret generic ${helmSecret} -n aw --from-file=api-key=/dev/stdin
unset PROVIDER_KEY
helm upgrade aw deploy/charts/agentworkflows -n aw --reuse-values \\
  --set providers.${target}.existingSecret=${helmSecret} --wait`;
  const copy = async (text: string, done: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(done); }
    catch { setCopied('Clipboard unavailable. Select the text above and copy it.'); }
  };
  const snippet = `# Merge into your team's policy entry.\nsandboxId: ${JSON.stringify(session.team.team_id)}\nproviderCredentials:\n${[...providers.map(([name, c]) => [name, c.environment_variable]), ...(missing || providers.some(([name]) => name === target) ? [] : [[target, variable]])].map(([name, env]) => `  ${name}: ${env}`).join('\n')}\nbudgets:\n  estimatedTokenLimit: ${budget.data?.limits.estimated_tokens || 200000}\n  costLimitUsd: ${settings?.cost_limit_usd || 25}`;
  return <><PageHeader title="Providers & budgets" subtitle="Your team’s provider keys and spending limits."><Refresh onClick={() => setRevision(v => v + 1)}/></PageHeader>
    <ErrorMessage message={team.error || budget.error || usage.error || policies.error} retry={() => setRevision(v => v + 1)}/>
    {!settings && !team.error && <Loading/>}
    {isDemo(models.data) && <DemoNote/>}
    <SpendTiles usage={usage.data} budget={budget.data}/>
    <h2>Provider connections</h2><div className="panel">
      {providers.length ? <div className="table-scroll" tabIndex={0} role="region" aria-label="Provider connections table"><table className="stack"><thead><tr><th>Provider</th><th>Secret name</th><th>Status</th></tr></thead><tbody>
        {providers.map(([provider, configuration]) => <tr key={provider}><th scope="row">{providerName(provider)}</th><td data-label="Secret name"><code>{configuration.environment_variable}</code></td><td data-label="Status">{configuration.configured
          ? <span className="badge recorded">Key present</span>
          : needed(provider) ? <><span className="badge awaiting_approval">Key missing</span><small>Needed by {usedBy(provider).join(', ')}</small></>
          : <><span className="badge">Not connected</span><small>No workflow uses it yet</small></>}</td></tr>)}
      </tbody></table></div> : <Empty title="Connect your first provider"><p>Choose a cloud provider and follow the steps below.</p></Empty>}
    </div><p className="muted">Key presence is a configuration check, not a live provider test. Secret values never appear here.</p>
    {missing ? <section className="setup panel"><h2>Connect {providerName(target)}</h2>
      {helmSecret ? <>
        <p>Installed with Helm? Store the key in a Secret and upgrade the release; the upgrade restarts the gateway. Then refresh this page.</p>
        <pre className="json">{helmCommands}</pre>
        <div className="actions"><button className="secondary" onClick={() => void copy(helmCommands, 'Commands copied.')}><Icon name="copy"/>Copy commands</button><span role="status">{copied}</span></div>
        <p className="muted">Not using Helm? Set <code>{variable}</code> in the gateway’s environment, restart the gateway, then refresh this page.</p>
      </> : <ol><li>Add the {providerName(target)} API key to the gateway as <code>{variable}</code>.</li>
        {!routed && <li>Add a reviewed {providerName(target)} model route with prices to your gateway configuration.</li>}
        <li>Restart the gateway, then refresh this page.</li></ol>}
      {others.length > 0 && <p className="muted">Then repeat for {providerList(others)}.</p>}
      <p className="setup-links">{helmSecret && <><a href="https://github.com/RamazanKara/agentworkflows/blob/main/docs/install-kubernetes.md#add-a-provider-key">Kubernetes install guide</a> · </>}<a href={`${guide}#teams-projects-and-roles`}>Secret instructions</a>{!routed && <> · <a href={routes}>Cloud routes and prices</a></>}</p>
    </section> : <section className="setup panel"><h2>Add a provider or change a budget</h2>
      <ol><li>Store the provider key in the gateway’s environment or a Kubernetes Secret.</li>
        <li>Merge the fragment below into your team’s entry in the policy (team ID <code>{session.team.team_id}</code>). Keep your existing providers, projects, tools and workflows.</li>
        <li>Approve the provider’s models, prices and network access in your gateway configuration. Restart the gateway, then refresh this page.</li></ol>
      <pre className="json">{snippet}</pre><div className="actions"><button className="secondary" onClick={() => void copy(snippet, 'Configuration copied.')}><Icon name="copy"/>Copy policy fragment</button><span role="status">{copied}</span></div>
      <p className="setup-links"><a href={`${guide}#teams-projects-and-roles`}>Team setup and Secret instructions</a> · <a href={routes}>Cloud routes and prices</a></p>
    </section>}
    <section><h2>Workflow budgets</h2><div className="panel"><div className="table-scroll" tabIndex={0} role="region" aria-label="Workflow budgets table"><table className="stack numeric"><thead><tr><th>Workflow</th><th>Providers</th><th className="num">Token limit</th><th className="num">Cost limit</th></tr></thead><tbody>{workflows.map(([name, policy]) => <tr key={name}><th scope="row">{workflowName(name)}</th><td data-label="Providers">{policy.allowedProviders.map(providerName).join(', ') || 'Team policy'}</td><td data-label="Token limit">{number(policy.tokenLimit)}</td><td data-label="Cost limit">{money(policy.costLimitUsd)}</td></tr>)}</tbody></table></div></div><p className="muted">Budget changes apply to new runs. Runs in progress keep their budget.</p></section>
  </>;
}
