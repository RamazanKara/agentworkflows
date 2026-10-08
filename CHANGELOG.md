# Changelog

## v0.5.1 - 2026-10-08

- Console: opening the console lands on Approvals when drafts wait, then Workflow runs, and on Get started for a new team.
- Console: Get started and the run form warn when a workflow uses a provider whose key is missing, even if other keys are present.
- Console: Providers & budgets gives one Helm block that stores every missing OpenAI and Anthropic key and runs a single `helm upgrade`; command lines fit a phone screen, and the key column is called "Environment variable".
- Console: approval cards use the draft's first line as the title without repeating it in the preview; revoked keys show their revoke date.
- Console: clearer empty states for runs, approvals and costs, model IDs no longer repeat the provider name, and hover colors apply only on devices that hover.

## v0.5.0 - 2026-10-08

- Make the umbrella Helm chart cloud-first: include Temporal/PostgreSQL and the worker,
  generate persistent bootstrap credentials, and ship a default team, cloud routes and
  Research example tools. Add the Kubernetes install guide; keep GitOps defaults unchanged.
- Keep the gateway console ready for provider setup when cloud keys are missing, while
  inference still fails closed.
- Capture opt-in redacted/full model and tool step input/output separately from receipts,
  with per-field size limits, Redis TTLs, and expandable console details.
- Declare workflow input schemas in both SDKs and policy, generate console forms, validate
  starts with field-level 422 errors, and include schemas in templates and `agentworkflows init`.
- Expire terminal run records after 30 days by default, backfill existing runs through an
  online state migration, and prune expired entries from run lists.
- Start the Compose quickstart without building: the stack pulls the signed release images.
- Install the SDKs from GitHub releases: `agentworkflows init` pins the release wheel, and the
  TypeScript SDK installs from the release tarball.
- Add Redis-managed team API keys with admin APIs, one-time secret display, expiry,
  last use, immediate revocation across replicas, and audit receipts. Existing flat
  hashes, key-record files, and JWT/JWKS credentials remain supported.
- Add optional company OIDC sign-in with PKCE, state and nonce validation, plus Redis
  cookie sessions for OIDC and pasted API credentials, session restore, logout, and CSRF.
- Add the console's Members & keys page and `agentworkflows keys list|create|update|revoke`.
  Keep the local Compose credential working with localhost cookie settings.
- Redesign the console for phones and first use: a compact phone menu, run forms with
  required fields first and optional ones under More options, model answers previewed on
  each step, prompts shown as a transcript, an Approval step that names the reviewer,
  readable trigger schedules, copyable webhook URLs and run IDs, approval cards that lead
  with the draft's title, and one spend summary (spent and tokens against the team limit,
  with usage bars) on Costs and Providers. Your own key is marked and cannot be revoked
  from the console; the Compose demo says when its models are simulated.
- `GET /v1/models` names each model's provider in `owned_by` and flags routes marked
  `simulated: true`; the Compose demo marks its fakes, so its console note never appears
  on a real provider route. Providers & budgets lists only the steps still needed to
  connect a missing key, including the Helm Secret for OpenAI and Anthropic.
- Record the person's display name on browser sessions and receipts: the managed key's
  name, or the `name`/`email` claim from company sign-in.

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
