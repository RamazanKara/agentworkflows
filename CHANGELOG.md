# Changelog

## v0.4.0 - 2026-10-08

- Add the TypeScript workflow SDK with governed model and tool activities, human approval,
  and PR review and support triage examples that run against the local fakes.
- Make the console easier to read: friendly workflow names, distinct run IDs, one grouped
  card for notification receipts, a summary for published or rejected results, and a
  Rejected badge in the run list. Run lists now include each completed run's outcome.
- Explain provider fallback on the run page, including the conservative hold kept for a
  failed attempt that reported no usage, and note it on the Costs page.
- Show run lists, triggers, costs and providers as stacked cards on phones, with a compact
  single-row navigation.
- Return a realistic synthetic briefing from the research fake, rename the always-failing
  demo route to `demo-fallback`, and align the quickstart with the real step timeline.
- Publish releases from a tag-only workflow: multi-arch images and Helm charts signed with
  Cosign, plus the Python wheel, source archive and TypeScript SDK package with checksums.

## v0.3.0 - 2026-10-07

- Add Temporal cron schedules and replay-protected, team-signed inbound webhooks,
  configured per workflow and visible/pausable in the console and CLI.
- Receipt trigger firings and Slack, outgoing webhook and SMTP notification attempts
  for approvals, failures and run budget thresholds; link to authenticated console review.
- Include daily report and GitHub issue triage workflows plus local notification fakes
  in the Compose smoke trial. See the workflow guide for signing and retry guarantees.
- Scaffold five team workflow templates: PR review with human approval, support triage,
  weekly reports, incident summaries, and document Q&A with citations. Each has a
  credential-free Compose walkthrough with sample inputs and expected results.
- Refresh the docs landing page, template gallery, and comparison with LiteLLM plus
  Temporal to explain where AgentWorkflows fits and what teams still need to operate.

## v0.2.0 - 2026-10-07

First public release of AgentWorkflows, the agent workflow platform for teams.

- Route OpenAI, Anthropic, Azure OpenAI, AWS Bedrock, and Vertex Gemini through one
  governed gateway with server-side credentials, model policies, and ordered fallback.
- Run durable Temporal workflows with retries, human approvals, per-run budgets, and
  verifiable receipts for model and tool calls. Connect framework agents, MCP tools,
  and isolated container steps.
- Organize work into teams and projects with admin, builder, approver, and viewer roles,
  shared spend limits, usage reports, and operations dashboards.
- Use the bundled web console or CLI to start, inspect, cancel, retry, and approve runs;
  review drafts, results, step timelines, receipts, and estimated costs.
- Start with a cloud-first Compose trial that needs no cloud key or GPU. Scaffold
  research, support-triage, and code-review projects with `agentworkflows init`, following
  the Bash or native Windows PowerShell quickstart.
- Self-host with Compose or Helm/GitOps. Optional retrieval, Ollama/vLLM backends,
  hardened workspaces, and audit controls build on private-ai-platform-kit.

Production hardening and broader live-provider acceptance are the next milestone.
The upstream kit's releases and research remain in its separate repository and Git history.
