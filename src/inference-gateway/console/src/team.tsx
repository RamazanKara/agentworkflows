import { useState } from 'react';
import { date, money, number, useData, workflowName, type Budget, type CostRow, type Models, type Policy, type RunPage, type Session, type Team, type Usage } from './api';
import { Empty, ErrorMessage, Loading, Metrics, PageHeader, Refresh } from './ui';

const guide = 'https://github.com/RamazanKara/agentworkflows/blob/main/docs/workflows.md';

export function GetStarted({ session }: { session: Session }) {
  const models = useData<Models>(session.token, '/v1/models');
  const runs = useData<RunPage>(session.token, `/v1/workflow-runs?${new URLSearchParams({ project: session.team.projects[0] || '', limit: '1' })}`);
  const last = runs.data?.runs[0];
  const builder = ['admin', 'builder'].includes(session.team.role);
  return <><PageHeader title="Your first governed workflow" subtitle="From a provider to a reviewed result, with evidence at every step."/>
    <p className="welcome">You’re signed in to <strong>{session.team.team_id}</strong> as <strong>{session.team.role}</strong>.</p>
    <ol className="onboarding">
      <li><span className="onboarding-number">01</span><div><h2>Use a configured provider</h2>
        <p>Cloud keys stay on the gateway. The approved models below follow your team’s policy.</p>
        <ErrorMessage message={models.error}/>
        {models.data ? models.data.data.length ? <div className="model-list">{models.data.data.map(model => <code key={model.id}>{model.id}</code>)}</div> : <p>No approved models yet. Ask your admin to connect a provider and approve a route.</p> : !models.error && <Loading/>}
        {models.data?.data.some(model => model.id === 'demo-openai') && <p className="callout">Using the Compose demo? Keep its local fake providers. No cloud key, paid call, or model download is needed.</p>}
        {session.team.role === 'admin' ? <a href="#providers">Connect a cloud provider or review budgets</a> : <p className="muted">Provider setup is managed by your team admin.</p>}
      </div></li>
      <li><span className="onboarding-number">02</span><div><h2>Run the example workflow</h2><p>Research a topic, draft a briefing, review it, and publish. The example has a budget and a human approval step built in.</p>
        {builder ? <a className="button" href="#new">Run workflow</a> : <p>Your role can inspect work. Ask a builder to start the example.</p>}
      </div></li>
      <li><span className="onboarding-number">03</span><div><h2>Open its receipts</h2><p>Open a run’s step timeline. Expand a receipt to see the provider, model, tokens, cost, and audit chain; review the draft in Approvals.</p>
        <ErrorMessage message={runs.error}/>
        {last ? <a href={`#run/${last.run_id}`}>Open latest run and receipts</a> : <a href="#runs">Explore workflow runs</a>}
      </div></li>
    </ol>
    <p className="muted">Working with your own cloud models? <a href={`${guide}#try-research--draft--approval--publish`}>Follow the team workflow guide</a>.</p>
  </>;
}

function CostTable({ title, rows }: { title: string; rows: Record<string, CostRow> }) {
  return <section><h2>{title}</h2><div className="panel">
    {Object.keys(rows).length ? <div className="table-scroll" tabIndex={0} role="region" aria-label={`${title} table`}><table className="stack"><thead><tr><th>{title === 'By provider' ? 'Provider' : 'Workflow'}</th><th>Calls</th><th>Tokens</th><th>Estimated cost</th></tr></thead>
      <tbody>{Object.entries(rows).sort((a, b) => (b[1].cost_usd || 0) - (a[1].cost_usd || 0)).map(([name, row]) => <tr key={name}><th scope="row">{title === 'By provider' ? (name === 'tool' ? 'Tools' : name) : workflowName(name)}</th><td data-label="Calls">{number(row.calls)}</td><td data-label="Tokens">{number(row.tokens)}</td><td data-label="Cost">{money(row.cost_usd)}</td></tr>)}</tbody></table></div> :
      <Empty title="No recorded spend yet"><p>Costs appear as governed calls finish. In-flight and unreported calls can retain reservations.</p></Empty>}
  </div></section>;
}

export function Costs({ session }: { session: Session }) {
  const [revision, setRevision] = useState(0);
  const result = useData<Usage>(session.token, '/v1/usage', revision);
  const spend = result.data?.spend;
  return <><PageHeader title="Costs" subtitle={`Understand where ${session.team.team_id} spends, across providers and workflows.`}><Refresh onClick={() => setRevision(v => v + 1)}/></PageHeader>
    <ErrorMessage message={result.error} retry={() => setRevision(v => v + 1)}/>
    {!result.data && !result.error && <Loading/>}
    {spend && <>
      <p className="scope">{spend.project ? `Project: ${spend.project}` : `Team: ${session.team.team_id}`} · {spend.window_seconds ? `${date(spend.window_start)} – ${date(spend.window_start + spend.window_seconds)}` : 'Lifetime accounting'}</p>
      <Metrics items={[[ 'Recorded call costs', money(result.data?.estimated_cost) ], ['Reserved + spent (team)', money(spend.reserved_and_spent_usd)], ['Team spend limit', spend.cost_limit_usd ? money(spend.cost_limit_usd) : 'No USD limit']]}/>
      {spend.project && <p className="callout">This credential is project-scoped. Team-wide spend is hidden; provider and workflow rows cover your project only.</p>}
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
  const team = useData<Team>(session.token, '/v1/team', revision);
  const budget = useData<Budget>(session.token, '/v1/sandbox/budget', revision);
  const usage = useData<Usage>(session.token, '/v1/usage', revision);
  const policies = useData<{ workflows: Record<string, Policy> }>(session.token, '/v1/workflow-policies', revision);
  const settings = team.data;
  const snippet = `# Merge into this team's existing SandboxPolicySet entry.\nsandboxId: ${JSON.stringify(session.team.team_id)}\nproviderCredentials:\n  openai: TEAM_OPENAI_KEY\nbudgets:\n  estimatedTokenLimit: ${budget.data?.limits.estimated_tokens || 200000}\n  costLimitUsd: ${settings?.cost_limit_usd || 25}`;
  return <><PageHeader title="Providers & budgets" subtitle="One governed gateway. Your team’s keys, models, and spending limits."><Refresh onClick={() => setRevision(v => v + 1)}/></PageHeader>
    <ErrorMessage message={team.error || budget.error || usage.error || policies.error} retry={() => setRevision(v => v + 1)}/>
    {!settings && !team.error && <Loading/>}
    <Metrics items={[[ 'Team spend limit', settings ? settings.cost_limit_usd ? money(settings.cost_limit_usd) : 'No USD limit' : '—' ], ['Reserved + spent', money(usage.data?.spend.reserved_and_spent_usd)], ['Token budget used', budget.data ? `${number(budget.data.usage.estimated_tokens)} / ${budget.data.limits.estimated_tokens ? number(budget.data.limits.estimated_tokens) : 'No limit'}` : '—']]}/>
    <h2>Provider connections</h2><div className="panel">
      {settings?.providers.length ? <div className="table-scroll" tabIndex={0} role="region" aria-label="Provider connections table"><table className="stack"><thead><tr><th>Provider</th><th>Server environment reference</th><th>Configuration</th></tr></thead><tbody>
        {Object.entries(settings.provider_configuration || {}).map(([provider, configuration]) => <tr key={provider}><th scope="row">{provider}</th><td data-label="Key"><code>{configuration.environment_variable}</code></td><td><span className={`badge ${configuration.configured ? 'recorded' : 'awaiting_approval'}`}>{configuration.configured ? 'Key present' : 'Key missing'}</span></td></tr>)}
      </tbody></table></div> : <Empty title="Connect your first provider"><p>Choose a cloud provider and follow the setup below. For a local evaluation, Compose comes with fake providers already connected.</p></Empty>}
    </div><p className="muted">Key presence is a configuration check, not a live provider test. Secret values never appear here.</p>
    <section className="setup panel"><h2>Connect a key and set a budget</h2>
      <ol><li>Store your provider key in a gateway environment variable or Kubernetes Secret. Use <code>TEAM_OPENAI_KEY</code> for this example.</li>
        <li>Merge the fragment below into your existing team policy. Preserve existing providers, projects, tools, and workflows.</li>
        <li>Approve the provider’s model route, prices, and egress in your gateway configuration. Recreate the gateway, then refresh this page.</li></ol>
      <pre>{snippet}</pre><div className="actions"><button className="secondary" onClick={async () => {
        try { await navigator.clipboard.writeText(snippet); setCopied('Configuration copied.'); }
        catch { setCopied('Clipboard unavailable. Select and copy the configuration above.'); }
      }}>Copy policy fragment</button><span role="status">{copied}</span></div>
      <p><a href={`${guide}#teams-projects-and-roles`}>Team setup and Secret instructions</a> · <a href="https://github.com/RamazanKara/agentworkflows/blob/main/docs/model-selection.md#cloud-routes-milestone-1">Cloud routes and prices</a></p>
      <p className="callout">The Compose demo already connects local fakes for OpenAI, Anthropic, Azure OpenAI, Bedrock, and Vertex. Keep those keys for the trial, then <a href="#new">run the example</a>.</p>
    </section>
    <section><h2>Workflow budget ceilings</h2><div className="panel"><div className="table-scroll" tabIndex={0} role="region" aria-label="Workflow budget ceilings table"><table className="stack"><thead><tr><th>Workflow</th><th>Providers</th><th>Tokens</th><th>USD limit</th></tr></thead><tbody>{Object.entries(policies.data?.workflows || {}).map(([name, policy]) => <tr key={name}><th scope="row">{workflowName(name)}</th><td data-label="Providers">{policy.allowedProviders.join(', ') || 'Team policy'}</td><td data-label="Tokens">{number(policy.tokenLimit)}</td><td data-label="Limit">{money(policy.costLimitUsd)}</td></tr>)}</tbody></table></div></div><p className="muted">Changes use the existing reviewed policy and Secret deployment. Running workflows keep their immutable run budgets.</p></section>
  </>;
}
