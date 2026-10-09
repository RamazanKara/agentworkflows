# agentworkflows

Python client for the [AgentWorkflows](https://github.com/RamazanKara/agentworkflows)
inference gateway: a cloud-first, OpenAI- and Anthropic-compatible LLM gateway that enforces
model allowlists, sandbox budgets, and guardrails, and writes a tamper-evident receipt for every call.

The package uses `httpx` and the official `temporalio` SDK, with inline type annotations (`py.typed`).

Start with the [five-minute quickstart](https://ramazankara.github.io/agentworkflows/latest/quickstart/)
to run, approve, and verify your first workflow. `agentworkflows init my-research` scaffolds
an editable project. The [template gallery](https://ramazankara.github.io/agentworkflows/latest/templates/)
also includes `code-review`, `support-triage`, `weekly-report`, `incident-summary`, and
`document-qa`. Every template runs against local Compose fakes; no server or key is needed to scaffold.

Inside Temporal workflows, `WorkflowGateway.text(prompt)` returns text from a governed
model activity. Inherit `ApprovalWorkflow` and call `await self.approval(draft)` for the
standard console/CLI review flow. Start your worker with `run_worker([MyWorkflow])` from
`agentworkflows.worker`. All model and tool activities retain budgets, DLP, and receipts.
See [templates](https://ramazankara.github.io/agentworkflows/latest/templates/) and the
[CLI/SDK reference](https://ramazankara.github.io/agentworkflows/latest/sdk-reference/).

`WorkflowGateway.agent` adapts existing framework agents, `.tool` calls approved HTTP/MCP
tools, and `.container` runs code in an approved agent-sandbox workspace. Install
`python -m pip install './sdk/python[frameworks]'` for the OpenAI, Anthropic, Agents SDK,
and LangGraph examples. The base package does not require those frameworks.

```bash
python -m pip install ./sdk/python
```

```python
from agentworkflows import GatewayClient, GatewayError

with GatewayClient("http://127.0.0.1:8080", api_key="local-development-only") as gw:
    reply = gw.chat([{"role": "user", "content": "Summarize the release notes."}])
    print(reply["choices"][0]["message"]["content"])

    for chunk in gw.chat_stream([{"role": "user", "content": "Stream a haiku."}]):
        print(chunk, end="", flush=True)

    gw.messages([{"role": "user", "content": "Hello"}], max_tokens=128)  # Anthropic shape
    print(gw.usage())                                                    # tokens and cost so far

    try:
        gw.chat([{"role": "user", "content": "hi"}], model="not-approved")
    except GatewayError as exc:
        print(exc.status_code, exc.reason, exc.request_id)  # 400 model_not_allowed req-...
```

## Command line

From the repository root, install as above, then use the Compose trial:

```bash
export AGENTWORKFLOWS_API_KEY=local-development-only
agentworkflows models
agentworkflows chat "Hello, AgentWorkflows!" --model demo-openai
agentworkflows usage
```

`AGENTWORKFLOWS_URL` defaults to `http://127.0.0.1:8080`. Set it to your team's gateway
for remote access. `agentworkflows --help` lists commands; errors include the next action
and the gateway request ID when available. The API key is read from the environment.

## What it covers

| Area | Methods |
| --- | --- |
| OpenAI-compatible inference | `chat`, `chat_stream`, `completions`, `embeddings`, `moderations`, `batch` |
| Anthropic Messages | `messages` |
| Files and asynchronous batches | `upload_batch_file`, `create_batch`, `get_batch`, `list_batches`, `cancel_batch`, file accessors |
| Responses API | `create_response`, `get_response`, `delete_response`, `response_input_items` |
| Agent-action receipts | `record_receipt` |
| Accounting and health | `models`, `usage`, `sandbox_budget`, `ready` |
| Team spend and capture | `team_spend`, `set_spend_limits`, `set_content_capture` (revision checked settings writes) |
| Managed-key access | `list_keys`, `create_key`, `update_key`, `revoke_key` |

## Behavior worth knowing

- **Sandbox header.** `sandbox_id` is sent as `X-Sandbox-ID` only when you set it. Leave it
  unset for a credential the gateway binds to a sandbox; naming a different sandbox is rejected.
- **Errors.** Every error response raises `GatewayError` (a subclass of
  `httpx.HTTPStatusError`) with `status_code`, the gateway's machine-readable `reason`, and the
  `request_id` to look the call up in the audit trail.
- **Retries.** Transient failures (429 and 5xx, connection errors) are retried with
  exponential backoff, honoring `Retry-After` up to `retry_after_cap` (30 s by default). A
  longer advertised delay, typically an exhausted budget window, raises
  `GatewayRetryAfterError` immediately with a `retry_after` attribute. Calls that create
  server-side state (uploads, batches, stored responses, receipts) are retried only when the
  gateway provably did not act: a connection failure, a 429, or a 503.
- **Streaming** is never retried once bytes flow; a terminal gateway error event raises
  `GatewayStreamError` so a truncated stream is not mistaken for a complete one.
- **Transport.** Pass `transport=` (any `httpx.BaseTransport`), `verify=` (a CA bundle path),
  or `default_headers=` (for example a `traceparent`) to the constructor.

For async I/O, typed response models, or the full OpenAI and Anthropic parameter surface,
point the official `openai` or `anthropic` SDK at the gateway's base URL; the gateway applies
the same governance either way. See the
[client examples](https://ramazankara.github.io/agentworkflows/latest/client-examples/).

Licensed under Apache-2.0.

Team operations use the authenticated gateway: `agentworkflows team`, `runs start`,
`runs list`, `runs inspect RUN_ID`, `runs cancel RUN_ID`, `runs retry RUN_ID`, and
`runs approve RUN_ID`. Use `agentworkflows runs start --help` for JSON input examples.
Approvals use the credential's verified identity; `GatewayClient` exposes matching methods.
See the [team walkthrough](../../docs/workflows.md) for roles, projects, budgets, and timelines.
