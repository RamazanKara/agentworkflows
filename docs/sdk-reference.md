# CLI and Python SDK reference

Install from this checkout with `python -m pip install ./sdk/python` in a virtual
environment. `agentworkflows --help` and each subcommand's `--help` work without a key.
The equivalent module entry point is `python -m agentworkflows.cli`.

## CLI

| Command | Purpose |
| --- | --- |
| `init [DIRECTORY] --template research` | Scaffold a new or empty directory; defaults to current directory and research |
| `init DIRECTORY --template support-triage` | Scaffold ticket classification and a suggested reply |
| `init DIRECTORY --template code-review` | Scaffold diff review with human approval |
| `models` | Discover model IDs available to your credential |
| `chat "PROMPT" --model MODEL` | Governed model call; omit model for the gateway default |
| `team` | Your team, role, projects, and configured providers |
| `usage` | Usage, estimated spend, provider and workflow breakdowns |
| `runs start [WORKFLOW] --input '@input.json'` | Start a workflow (default ResearchWorkflow); input can also be inline JSON |
| `runs list --project PROJECT --offset OFFSET` | List runs; both options are optional; use returned next_offset for pagination |
| `runs inspect RUN_ID` | Status, draft, completed result, budget, and receipt timeline |
| `runs approve RUN_ID` | Approve as your authenticated identity; `--reject` rejects instead |
| `runs cancel RUN_ID` | Request cancellation; cannot undo already-sent tool actions |
| `runs retry RUN_ID` | Start a new execution after failure/cancellation; steps may run again |

`runs start` also accepts `--project` and `--request-id UUID`. If a start response is lost,
reuse the request ID printed on stderr with **identical input** to avoid duplicate runs.
Exit codes: **0** success, **1** gateway/transport failure, **2** usage/input/scaffold error.
Machine-readable command output stays on stdout; actionable errors go to stderr.

## Environment

| Variable | Default / use |
| --- | --- |
| `AGENTWORKFLOWS_API_KEY` | Required gateway credential, never a provider key; `local-development-only` for local human use, `demo-worker` for its worker |
| `AGENTWORKFLOWS_URL` | `http://127.0.0.1:8080` for CLI and worker |
| `AGENTWORKFLOWS_TEAM` | `demo`; worker queue defaults to `TEAM-workflows` |
| `TEMPORAL_ADDRESS` | `localhost:7233` for the worker |
| `TEMPORAL_NAMESPACE` | `default` for the worker |
| `TEMPORAL_TASK_QUEUE` | Optional existing override; must match the gateway's team queue |

## Workflow SDK

Import `WorkflowGateway`, `Budget`, and `ApprovalWorkflow` from `agentworkflows.workflows`.

| API | Result / behavior |
| --- | --- |
| `WorkflowGateway(Budget(10000, 5.0))` | Per-run token and USD ceilings, further restricted by team policy; these are the defaults |
| `await gateway.text(prompt, model=..., max_tokens=512)` | Text answer; omit model for the gateway default |
| `await gateway.model(messages, model=..., max_tokens=512)` | Full chat-completion dictionary when you need usage or structured message fields |
| `await gateway.tool(name, arguments)` | Approved HTTP/MCP tool's result |
| `await gateway.agent(name, arguments)` | Registered framework handler's result through governed adapters |
| `await gateway.container(name, arguments)` | Approved sandbox's output; no automatic retry of arbitrary code |
| `await self.approval(draft)` | In an ApprovalWorkflow subclass: await one human decision per run; returns bool, sets reviewer |
| `run_worker([WorkflowClass])` | Import from `agentworkflows.worker`; start an environment-configured worker with concurrency capped at two |

Activity defaults are three minutes per attempt, fifteen minutes overall, and five attempts
with exponential backoff. Policy denials are non-retryable; transient gateway failures can
retry. Existing Temporal `RetryPolicy` and `timedelta` parameters remain available on
`WorkflowGateway`. `data_classification` defaults to `internal`; confidential data needs an
approved self-hosted route. Approval expires after seven days with an actionable failure.

Template research budgets accept integer `token_limit` from 1 to 1,000,000,000 and finite
`cost_limit_usd` greater than zero up to 1,000,000. Policy ceilings still take precedence.

## Gateway client and errors

Use `GatewayClient(base_url, api_key=...)` as a context manager for authenticated calls.
Its `start_run`, `runs`, `run`, `approve_run`, `cancel_run`, and `retry_run` methods mirror
the CLI. [Client examples](client-examples.md) cover model calls and compatible framework SDKs.

`GatewayError` exposes `status_code`, `reason`, `request_id`, and `detail`. Validation errors
identify the invalid fields. CLI output includes a recovery action and request ID without
printing your credential. `GatewayRetryAfterError.retry_after` reports when the server asks
for a delay longer than the client's retry cap; transport errors are `httpx.HTTPError`.

| Error | Recovery |
| --- | --- |
| Authentication / wrong role | Set a valid team gateway key; ask the admin for builder or approver access as appropriate |
| `workflow_input_invalid` | Use the template's input.json and correct the named field before starting again |
| `workflow_not_allowed`, `model_not_allowed`, `tool_not_allowed` | Discover approved models with `models`; review team workflow policy with the admin |
| `prompt_secret_detected` | Remove credentials or sensitive personal data from the prompt/tool arguments |
| `approval_not_waiting` | Inspect the run's stage and existing decision; never approve an unseen draft |
| `workflow_start_conflict` | Use the original input for that request ID, or a new ID for a different run |
| `workflow_run_missing` | Use `runs list` with the correct team and project |
| `temporal_unavailable`, `worker_unavailable` | Check Temporal health and the team's worker/queue, then inspect again |
| Budget / rate limit | Inspect `usage`; wait for the window or ask an admin to review the limit |

The [authenticated API table](workflows.md#authenticated-workflow-api) documents the run
endpoints. The full [OpenAPI contract](https://github.com/RamazanKara/agentworkflows/blob/main/platform/api-contracts/inference-gateway.openapi.json)
is checked in and can be viewed at `/docs` on the running gateway when enabled.
