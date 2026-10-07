# Create a workflow project

After the [quickstart](quickstart.md), scaffold one of three editable projects:

```sh
agentworkflows init my-research
agentworkflows init my-support --template support-triage
agentworkflows init my-review --template code-review
```

`init` needs no network, gateway, or credentials. It accepts a new or empty directory and
refuses to overwrite any file. It writes the workflow source, worker entry point, sample
input, pinned SDK requirement, `.gitignore`, and instructions. No framework extras are required.

| Template | Workflow name | Input | Outcome |
| --- | --- | --- | --- |
| `research` (default) | `ResearchWorkflow` | `topic` | Research, two model calls, human review, then the approved publish tool |
| `support-triage` | `SupportTriageWorkflow` | `ticket` | Category, priority, suggested owner, draft reply; never sends a message |
| `code-review` | `CodeReviewWorkflow` | `diff` | Review a supplied diff and await human approval; never executes, posts, or merges code |

Each input is a JSON object with a nonempty text field from the table and an optional
`model` (default `demo-openai`). Research also accepts `token_limit` and `cost_limit_usd`.
The fake stack already approves all three workflows. Try support triage from its directory:

```sh
cd my-support
agentworkflows runs start SupportTriageWorkflow --input '@input.json'
agentworkflows runs list
```

Inspect the printed UUID with `agentworkflows runs inspect RUN_ID`. Completed runs include
their result and receipted timeline. Code review and research expose `progress.draft` until
an admin/approver runs `agentworkflows runs approve RUN_ID` (or adds `--reject`).

## Run your edits

The Compose worker runs the installed examples. To execute your edited `workflow.py`,
stop that worker **from the checkout root** before starting yours:

```sh
docker compose -f deploy/compose/compose.yaml stop workflow-worker
```

On Windows, prefix Compose commands with `wsl.exe -d Ubuntu -e`. In a second terminal,
activate the same SDK environment as in the quickstart, enter the generated project,
and start the worker:

=== "Bash"

    ```bash
    export AGENTWORKFLOWS_API_KEY=demo-worker
    python worker.py
    ```

=== "PowerShell"

    ```powershell
    $env:AGENTWORKFLOWS_API_KEY = 'demo-worker'
    python worker.py
    ```

Wait for `Worker ready on demo-workflows`. Start and inspect runs from the original
terminal using its human credential. Your worker registers only this project's workflow;
do not start other templates on that queue until restoring the built-in worker.
Restart your worker after changing source. Stop it with Ctrl+C, then restore Compose:

```sh
docker compose -f deploy/compose/compose.yaml up -d workflow-worker
```

If you chose a different gateway port, set `AGENTWORKFLOWS_URL` in the worker terminal too.
If you chose a different Temporal port, set `TEMPORAL_ADDRESS=localhost:PORT` there.
For your own team, set `AGENTWORKFLOWS_TEAM` and its worker key; the queue defaults to
`TEAM-workflows`. Register the workflow name, approved models/tools/egress, and budgets in
the existing [team policy](workflows.md#teams-projects-and-roles).

## A workflow in about 20 lines

This is the code-review template, with its input supplied as a typed `CodeReviewRequest`:

```python
from dataclasses import dataclass
from temporalio import workflow
from agentworkflows.workflows import ApprovalWorkflow, WorkflowGateway

@dataclass
class CodeReviewRequest:
    diff: str
    model: str = "demo-openai"

@workflow.defn
class CodeReviewWorkflow(ApprovalWorkflow):
    @workflow.run
    async def run(self, request: CodeReviewRequest) -> dict:
        review = await WorkflowGateway().text(
            f"Review this diff for correctness and security. Cite lines and suggest tests.\n{request.diff}",
            model=request.model,
        )
        approved = await self.approval(review)
        return {"approved": approved, "review": review, "reviewer": self.reviewer}
```

The approval base implements the signal, query, update, and expiry behavior used by the
run API. `text` schedules the same governed model activity as the lower-level `model` method.
See [SDK reference](sdk-reference.md) for defaults and [bring your agent](workflows.md#bring-your-agent)
for OpenAI, Anthropic, Agents SDK, LangGraph, MCP, and sandboxed code steps.
