import { useState } from 'react';
import { useData, type Session } from './api';
import { Badge, ErrorMessage, Loading, Refresh } from './ui';

type Deployment = {
  checks: { id: string; name: string; configured: boolean; action: string }[];
  verification_required: string[];
};

export function DeploymentChecklist({ session }: { session: Session }) {
  const [revision, setRevision] = useState(0);
  const result = useData<Deployment>(session.csrfToken, '/v1/team/deployment', revision);
  return <section className="panel form-panel" aria-label="Deployment checklist">
    <div className="section-heading"><h2>Deployment checklist</h2><Refresh onClick={() => setRevision(v => v + 1)}/></div>
    <p>Review these settings with your operator before opening access to your team.</p>
    <ErrorMessage message={result.error}/>
    {!result.data ? <Loading/> : <>
      <dl className="data-facts">{result.data.checks.map(check => <div key={check.id}><dt>{check.name}</dt><dd><Badge value={check.configured ? 'completed' : 'pending'} text={check.configured ? 'Configured' : 'Needs setup'}/></dd></div>)}</dl>
      {result.data.checks.filter(check => !check.configured).map(check => <p key={check.id}><strong>{check.name}:</strong> {check.action}</p>)}
      <h3>Verify in your environment</h3><ul>{result.data.verification_required.map(item => <li key={item}>{item}</li>)}</ul>
    </>}
    <a href="https://github.com/RamazanKara/agentworkflows/blob/main/docs/single-tenant.md" target="_blank" rel="noreferrer">Single-tenant deployment, backups and upgrades</a>
  </section>;
}
