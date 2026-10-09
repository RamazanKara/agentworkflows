# Changelog

## v0.9.0 - Unreleased

- Add per-workflow approval quorums (1–10 distinct verified identities) and expiry
  (60 seconds–7 days). Snapshot approval rules at run creation; settings changes
  affect new runs. Each identity votes once, any rejection ends the gate, and
  duplicate delivery cannot advance the quorum. Defaults remain one reviewer/seven days.
- Apply the same durable quorum and deadline behavior in Python and TypeScript
  workflow SDKs. Preserve replay of existing gates with Temporal patch markers;
  reject advanced policies on old workers. Upgrade gateway before SDK workers.
- Configure policies in Team settings and Helm, and show approval counts, deadlines,
  and already-reviewed drafts in the console. Keep partial approvals visible.
  Add the versioned worker gate contract, regression tests and a two-reviewer trial.
- Advance source, SDK and chart versions to 0.9.0. Keep published Quickstart image
  defaults on v0.5.1; verify this candidate with the documented source-build path.

## v0.8.0 - Unreleased

- Map company OIDC groups to team roles with exact, team-scoped matches. Nonempty
  mappings require one distinct mapped role; missing, malformed, unmapped or
  conflicting memberships are denied without falling back to role claims.
  Existing team/project claims still constrain access; API-key and bearer JWT
  authorization are unchanged. Empty mappings preserve existing role-claim sign-in.
- Invalidate affected OIDC sessions when access policy changes and bound group-based
  sessions to ID-token expiry. Existing sessions must sign in again when mappings
  are enabled; no tokens or group lists are retained in sessions.
- Show company sign-in policy and group roles in Members & keys, backed by the
  admin-only `GET /v1/team/sso` contract and typed Python `team_sso()` / TypeScript
  `teamSSO()` helpers. Keep identity policy operator-managed through environment
  variables or Helm values; expose only the caller's team mappings and no secrets.
- Advance source, SDK and chart versions to 0.8.0 and document source-build and SSO
  acceptance paths. Published Quickstart images remain on v0.5.1 until release.

## v0.7.0 - Unreleased

- Add retained run history for cron and webhook triggers: persist launch provenance,
  filter and cursor-page runs by workflow/trigger, and link each console trigger to
  run status, approval review and receipts. Python/TypeScript run paging and JSON
  Lines export accept the same filter. Existing cursors remain compatible; history
  starts with launches recorded by this version and follows run-record retention.
- Export current UTC month usage as CSV from Costs, the authenticated API, both SDKs
  and `agentworkflows usage --output usage.csv`. Include scope totals and provider/
  workflow breakdowns with project isolation, nanodollar precision and spreadsheet
  formula protection. Estimates include reservations; exports are not invoices.
- Advance source, SDK and chart versions to 0.7.0; document candidate build and
  verification commands while keeping published quickstart image defaults on v0.5.1.

## v0.6.0 - Unreleased

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
  retention and readiness checks. Redis remains required for live budgets, sessions,
  captured content and coordination; the bundled PostgreSQL server is for development.
- Console: show every model route name and its available key state on Get started,
  matching Team settings, with consistent phone chip heights. Keep summary and link
  items together without stray separators, remove empty workflow columns, format
  draft headings and number inputs, and use readable team and API-key names. Use teal
  tabular step numbers and scroll JSON and provider commands with a phone edge fade.
  Separate token windows from monthly spend and clarify missing-key warnings.
- Release screenshots cover 360, 393 and 1440 px with realistic setup and connected-team
  fixtures. Costs, token totals, calls, receipts and worker-key usage share one run.
- Add stable cursor paging for run history and approvals, including equal timestamps,
  expired records and empty filtered pages. Keep legacy offsets compatible and bind
  cursors to the authenticated team, project and filters.
- Add filtered run JSON Lines export with retained results, timelines and step content
  in the Python CLI and both SDKs. Document retention and partial-export limits;
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
