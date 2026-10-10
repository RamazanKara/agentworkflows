# Changelog

## v0.9.0 - 2026-10-10

The first release since v0.5.1. It ships everything built since then: a team can now go
from install to its first approved run in the console, bring its own workflows without a
gateway redeploy, see where each run's time and money went, and prove an install with one
command. Images, charts and SDKs are all version 0.9.0.

### Fixed in this release

- Run forms in the console accept topics, tickets and diffs of any length. Workflow input
  schemas mark text as "not blank" with a JSON Schema pattern, and the console now handles
  that pattern correctly in the browser, which matches a pattern against the whole value.
- Get started is one three-step list with progress marks instead of a wizard repeating
  the same steps. Run IDs show their distinct last characters, run summaries and Insights
  name steps by tool or model, retention and alert thresholds use days and seconds, Members
  & keys lists who has access, audit rows name tool calls, notifications and trigger
  resumes plainly, and the navigation fits on desktop and phones.
- The RAG service's tracing module matches the gateway's again (route-named spans, error
  status and OTLP signal paths).

### Bring your own workflow, run insights and install check

- Let a team admin register their own workflow types from the console, CLI, API and both
  SDKs without a gateway redeploy. Registrations are revision-checked and audited, can only
  use models, tools and limits already approved for the team, derive network egress, and are
  read through the same effective policy as YAML workflows. Operators can opt out per team
  with `selfServiceWorkflows: false`.
- Add run insights: a per-run summary of elapsed, working and review time with cost by step,
  and a per-workflow Insights page, API, CLI command and SDK methods for outcomes, run time,
  review wait, cost per run and the slowest and costliest steps over 1 to 30 days.
- Add `agentworkflows check` (and `first_approved_run` / `firstApprovedRun`): a post-install
  acceptance check that runs the console wizard's path against any gateway, times each stage,
  refuses to spend without `--allow-paid`, and verifies the approval receipt and audit chain.
  The deployment checklist and single-tenant guide now point to it.
- The offline audit verifier accepts every chain-linked record, including chained key,
  settings, template, secret and provider-key events, so its gap reports are accurate.

### First-run wizard, nine templates, invitations, alert rules and deployment readiness

- Rewrite the product README and docs landing page around team outcomes, a console
  walkthrough, a build-it-yourself comparison, and architecture and privacy guidance.
- Add a first-run console wizard and readiness API, encrypted team provider-key
  provisioning with version checks, and a sample workflow through approval and receipts.
- Expand the versioned gallery to nine workflows with release notes, meeting actions,
  and evidence-backed security questionnaire drafts in Python and TypeScript.
- Add single-use, 24-hour invitations with scoped roles/projects, revocation, audit,
  and console acceptance; refresh member access and expose company sign-in testing.
- Add revision-checked Slack/email/webhook alert rules for approvals, failures, budget
  thresholds and slow steps, plus safe failure details in the existing run timeline.
- Add deployment configuration checks, a hardened single-tenant Helm reference, and
  coordinated backup/restore and upgrade guidance. Reuse Compose and kind trial scripts.
- Extend API contracts and both SDKs with tests, and refresh console captures at 360,
  393 and 1440 pixels. Container acceptance and native checks have their own records.

### Reliability: failure tests, load baseline and backup verification

- Add dependency outage, slow-response and partial-write tests for gateway and worker;
  exercise lost Temporal replies, stable retry identities, SQL rollback and Redis migrations.
  Extend the isolated restore drill with Redis/Postgres/Temporal stop and pause faults.
- Bound worker connection startup and runtime retry delays; preserve circuit failures
  across truncated streams. Disable automatic Redis accounting-write retries after lost replies.
- Check historical 0.2.0 and rc.2 config/Helm values; correct the future-schema rejection drill.
- Extend the native Python load harness with recorded baseline evidence and a regression
  checker enforcing zero errors, p95 within +25% and throughput within -20%.
- Add offline backup verification, reject empty backup files and correct the recovery
  runbook's durable Redis, gateway PostgreSQL, Temporal and encryption-key backup scope.
- Bound HTTP method labels, forward the gateway trace span to local/cloud runtimes, and
  redact storage-driver and worker response payloads from operational failure logs.
- Fix runbook link mapping for the docs site; record native checks and WSL drills.

### Template gallery, workflow secrets, OTLP metrics and data retention

- Add a versioned console gallery for the six bundled templates, idempotent installation
  into approved team policies, and template version provenance on new runs.
- Complete cancel/retry audit receipts and show these operations in the run timeline.
- Add encrypted per-workflow secrets, activity-scoped resolution, version-checked rotation,
  metadata-only audit, console administration and Helm Secret references.
- Export bounded HTTP metrics and workflow/spend gauges over OTLP alongside traces;
  provide an OTLP Grafana dashboard and console configuration status.
- Add team retention settings, retained-data/Temporal export and resumable erasure with
  cross-team isolation and a tombstone preventing new work. Document external data owners.
- Extend the OpenAPI contract and both SDKs; add scripted Compose/kind first-approved-run
  checks with a five-minute runtime budget after image preparation, regression coverage,
  and console captures at 360, 393 and 1440 pixels.

### Documentation and console polish

- Reconcile candidate quickstarts, installation, API/SDK and feature documentation;
  add Why AgentWorkflows and a code-aligned architecture overview.
- Refresh console evidence at 360, 393 and 1440 pixels with consistent seeded identity,
  receipts and totals, including approval quorums, SSO, spend, triggers and CSV export.
  Fix spend-alert spacing and adjacent key/trigger action controls.
- Keep template hints intact on phones, simplify approval and sign-in copy, and
  edit budgets only in Team settings. Use one six-workflow policy and an internal
  gateway host across console captures, with distinct SSO and spend-limit views.
- Throttle browser authentication with the configured limiter and remove OIDC query
  parameters from Uvicorn access logs. Bound webhook bodies before authentication;
  add regression tests and security review notes.
- Add a native local gateway HTTP load sanity script and record measured results.
  Document the WSL, container and cluster checks.

### Shared approval decisions

- Add per-workflow approval quorums (1–10 distinct verified identities) and expiry
  (60 seconds–7 days). Snapshot approval rules at run creation; settings changes
  affect new runs. Each identity votes once, any rejection ends the gate, and
  duplicate delivery counts once toward the quorum. Defaults remain one reviewer/seven days.
- Apply the same durable quorum and deadline behavior in Python and TypeScript
  workflow SDKs. Preserve replay of existing gates with Temporal patch markers;
  reject advanced policies on old workers. Upgrade gateway before SDK workers.
- Configure policies in Team settings and Helm, and show approval counts, deadlines,
  and already-reviewed drafts in the console. Keep partial approvals visible.
  Add the versioned worker gate contract, regression tests and a two-reviewer trial.

### Company group sign-in

- Map company OIDC groups to team roles with exact, team-scoped matches. Nonempty
  mappings require one distinct mapped role; missing, malformed, unmapped or
  conflicting memberships are denied without falling back to role claims.
  Existing team/project claims still constrain access; API-key and bearer JWT
  authorization are unchanged. Empty mappings preserve existing role-claim sign-in.
- Invalidate affected OIDC sessions when access policy changes and bound group-based
  sessions to ID-token expiry. Existing sessions sign in again when mappings are
  enabled; tokens and group lists stay out of sessions.
- Show company sign-in policy and group roles in Members & keys, backed by the
  admin-only `GET /v1/team/sso` contract and typed Python `team_sso()` / TypeScript
  `teamSSO()` helpers. Keep identity policy operator-managed through environment
  variables or Helm values; expose only the caller's team mappings.

### Trigger run history and usage CSV export

- Add retained run history for cron and webhook triggers: persist launch provenance,
  filter and cursor-page runs by workflow/trigger, and link each console trigger to
  run status, approval review and receipts. Python/TypeScript run paging and JSON
  Lines export accept the same filter. Existing cursors remain compatible; history
  starts with launches recorded by this version and follows run-record retention.
- Export current UTC month usage as CSV from Costs, the authenticated API, both SDKs
  and `agentworkflows usage --output usage.csv`. Include scope totals and provider/
  workflow breakdowns with project isolation, nanodollar precision and spreadsheet
  formula protection. Estimates include reservations.

### Team settings, audit log, PostgreSQL storage and spend limits

- Add an offline Redis-to-PostgreSQL import command with read-only dry-run,
  progress, conflict refusal, atomic run/timeline copies and resumable idempotent
  inserts. Preserve retention deadlines, settings revisions, key revocations and
  original audit hashes; verify source and imported chains before cutover.
- Add monthly team soft spend limits and durable in-console/webhook alerts using
  existing Redis accounting. Hard team/project denials return 429 and Retry-After
  until the next UTC month. Add a typed spend API and Python/TypeScript SDK methods.
- Complete key access editing in Members & keys, and expose team/workflow content
  capture controls through revision-checked settings, the console and both SDKs.
- Fix PostgreSQL upgrade links when runbooks are mirrored into the strict docs site.

- Add an opt-in PostgreSQL gateway store for run metadata, receipts and terminal
  snapshots, audit events, team settings and managed-key metadata, with migrations,
  retention and readiness checks. Redis holds live budgets, sessions, captured content
  and coordination; the bundled PostgreSQL server is for development.
- Console: show every model route name and its available key state on Get started,
  matching Team settings, with consistent phone chip heights. Keep summary and link
  items together without stray separators, remove empty workflow columns, format
  draft headings and number inputs, and use readable team and API-key names. Use teal
  tabular step numbers and scroll JSON and provider commands with a phone edge fade.
  Separate token windows from monthly spend and clarify key setup prompts.
- Release screenshots cover 360, 393 and 1440 px with realistic setup and connected-team
  fixtures. Costs, token totals, calls, receipts and worker-key usage share one run.
- Add stable cursor paging for run history and approvals, including equal timestamps,
  expired records and empty filtered pages. Keep legacy offsets compatible and bind
  cursors to the authenticated team, project and filters.
- Add filtered run JSON Lines export with retained results, timelines and step content
  in the Python CLI and both SDKs. Document retention and export behavior;
  synchronize the OpenAPI response and SDK paging types.
- Console: balance the Providers Helm comment, scroll step JSON sideways on phones
  with a right-edge fade, keep “Add the key” together, show at least 4 pixels for
  nonzero spend, and style provider labels consistently in fresh-install model chips.

- Add typed managed-key, revision-checked settings and audit methods to the Python
  and TypeScript SDKs. Add `settings show|set|reset` and `audit list|verify|export`
  to the Python CLI, including filtered cursor paging, JSON Lines export and
  actionable settings conflicts and validation errors; preserve the keys commands.
- Add opt-in umbrella-chart TLS ingress with Secure cookies, OIDC callback derivation,
  gateway HA controls, single-namespace NetworkPolicies and Secret-backed external
  Redis/PostgreSQL configuration. Document production operations and kind verification;
  keep the default localhost demo install and bundled datastores.
- Add Redis-backed team settings with YAML defaults, revision-checked admin APIs,
  field resets and chained change receipts. Apply monthly team/project budgets,
  workflow limits and approval rules, provider allowances and existing model alias
  selections without restarting the gateway. Add console editors and read-only
  views; keep provider keys in environment variables or Kubernetes Secrets.
- Console: Team settings groups budgets, workflow approval rules and model routes into panels with one card per workflow. Values that differ from team policy are marked Custom with a Reset; edits and resets wait for Save, Discard undoes both, and the save bar stays docked until the save is confirmed. Costs and Providers & budgets show the same budgets as a compact table.
- Console: Costs labels monthly spend "This month (UTC)" and the token tile names its budget window.
- Console tests pin the browser locale and time zone and format expected dates in the browser, so date assertions pass on every machine and browser.
- Add a retained Redis audit view for team admins, with filtered cursor APIs,
  receipt and team-chain verification, a console audit page and JSON Lines export.
  Preserve the existing audit log format and operator verifier.
- Console: the audit log names events in plain words (Model call, Settings change), shows key names for actors, reads as compact cards on phones and explains verification results and range boundaries.
- Console: Team settings route options name their provider, and the Costs footnote only shows with recorded spend.

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
  with usage bars) on Costs and Providers. Your own key is marked and protected from
  revocation in the console; the Compose demo says when its models are simulated.
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
- Refresh the docs landing page, template gallery, and build-it-yourself comparison to
  explain where AgentWorkflows fits and what each team operates.

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

The upstream kit's releases and research live in its separate repository and Git history.
