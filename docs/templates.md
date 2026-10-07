# Workflow template gallery

Pick one team task, run its fixture, then adapt its inputs and tools. Each walkthrough takes
about five minutes after the [quickstart](quickstart.md). All five run in the same Compose
trial without credentials for any external service, a GPU, or framework extras.

| Team task | `--template` | What you can inspect |
| --- | --- | --- |
| [PR review with human approval](#code-review) | `code-review` | Findings, a waiting draft, and an authenticated approve/reject decision |
| [Support ticket triage](#support-triage) | `support-triage` | Category, priority, suggested owner, and a reply draft |
| [Weekly report from several sources](#weekly-report) | `weekly-report` | Changes, support and incident snapshots alongside the report |
| [Incident summary from logs](#incident-summary) | `incident-summary` | UTC timeline, log references, mitigation and open questions |
| [Document Q&A with citations](#document-qa) | `document-qa` | Answer, source links and the exact retrieved excerpts |

## Before you start

Complete quickstart step 1 **from this checkout**, keep the fake model route, and return to
the checkout root. Keep `AGENTWORKFLOWS_API_KEY=local-development-only` set in your shell.
Every command below works in Bash and PowerShell. Replace `RUN_ID` with the UUID returned
by `runs start`; repeat `inspect` while a run is still working. The console at
<http://127.0.0.1:8080/console/> offers the same workflows and sample inputs under **Run workflow**.

`init` works offline and refuses to overwrite a nonempty directory. It writes `workflow.py`,
`worker.py`, `input.json`, a pinned SDK requirement, `.gitignore`, and local instructions.
The built-in worker already runs these unchanged examples. Input edits apply to the next
run immediately; source edits require [your own worker](#run-your-edits).

The fake returns **canned answers and synthetic data**, even if you change the input. It
proves the workflow, policy, approval and receipt paths; it does not evaluate model quality
or connect to your GitHub, ticketing, logging, or document system. The `example.test` source
links identify fixtures and are not live pages. All examples request the existing default
budget of 10,000 tokens / $5, further limited by team policy; fixture prices are synthetic.

## PR review with human approval {#code-review}

For a developer who wants a reviewable security/correctness draft before acting on a PR:

```sh
agentworkflows init my-review --template code-review
agentworkflows runs start CodeReviewWorkflow --input '@my-review/input.json'
agentworkflows runs inspect RUN_ID
```

The input's `diff` contains a patch to `auth.py`. At `progress.stage: awaiting_approval`,
read `progress.draft`: the fixture flags the unconditional `True` at `auth.py:10`, suggests
restoring the admin check, and asks for admin/non-admin tests. Then submit your decision:

```sh
agentworkflows runs approve RUN_ID
agentworkflows runs inspect RUN_ID
```

Expect `status: completed`, `result.approved: true`, the review and reviewer, and model and
approval receipts. Start another run and use `runs approve RUN_ID --reject` to see
`result.approved: false`. Both decisions finish the run; neither posts, executes or merges
code. Approval expires after seven days. The trial's Slack/webhook/email alerts go to the
[local notification inbox](#notifications-and-next-steps).

**Adapt it:** put a sanitized unified diff in `my-review/input.json`. Include file names and
hunk line numbers so findings can be checked. Use separate builder and approver identities
from [team setup](workflows.md#teams-projects-and-roles). A PR webhook needs an adapter that
fetches the diff and submits `{"diff":"..."}`; this template does not accept a raw GitHub
event. If you add a comment-posting tool, call it only after a true approval and make the
endpoint honor the gateway's idempotency key. Check findings against the actual diff.

## Support ticket triage {#support-triage}

For a support lead who needs routing and a reply draft while keeping customer communication
under the team's control:

```sh
agentworkflows init my-support --template support-triage
agentworkflows runs start SupportTriageWorkflow --input '@my-support/input.json'
agentworkflows runs inspect RUN_ID
```

The input's `ticket` describes three teammates unable to sign in after password resets.
Expect `status: completed` and a text `result` with category `access`, priority `high`, owner
`identity-support`, and a draft asking for the error and time without requesting secrets.
There is one model receipt and no customer message is sent. No approval is required to
create this internal draft.

**Adapt it:** replace `ticket` with a sanitized ticket body. Edit the prompt to use your
actual queues, severity definitions and escalation criteria. Connect a ticket-system
adapter through the [signed webhook contract](workflows.md#triggers-and-notifications),
normalizing input to `{"ticket":"..."}`. Check suggested priorities against known tickets
before routing live work; add human review before any customer-facing send action.

## Weekly report from several sources {#weekly-report}

For a team lead assembling a briefing from engineering, support and reliability:

```sh
agentworkflows init my-weekly --template weekly-report
agentworkflows runs start WeeklyReportWorkflow --input '@my-weekly/input.json'
agentworkflows runs inspect RUN_ID
```

The input's `period` is `2026-09-28/2026-10-04`. The workflow calls `report_source` separately
for `changes`, `support`, and `incidents`, then asks for a single report. Expect
`status: completed`, `result.sources` with three snapshots, and `result.report` linking each
source. The fixture reports two shipped changes, 12 opened / 9 closed tickets and INC-1042;
it explicitly flags the missing backlog baseline. The timeline has three tool receipts
and one model receipt. The report stays in the run result.

**Adapt it:** implement the existing `report_source` POST contract, accepting `source` and
`period` and returning `{id, url, period, text}`. Point its team tool URL at an authenticated
adapter for your repositories, helpdesk and incident tracker. Bound date ranges and response
sizes, and use stable source links. A failed source call fails the run after retries; this
template does not silently turn missing data into a successful report.

For a Monday 09:00 **UTC** run, merge this into the existing `WeeklyReportWorkflow` policy
in `deploy/compose/sandbox-policy.yaml`, preserving its models, tools, egress and budgets:

```yaml
triggers:
  weekly:
    kind: cron
    project: default
    cron: "0 9 * * 1"
    input: {period: previous week}
```

Recreate the gateway from the checkout root (`docker compose -f deploy/compose/compose.yaml
up -d --force-recreate inference-gateway`; prefix with `wsl.exe -d Ubuntu -e` on Windows).
Your real adapter must resolve `previous week` into a UTC range; the fake always returns
the same synthetic snapshots. Inspect and pause the schedule after trying it:

```sh
agentworkflows triggers list
agentworkflows triggers pause WeeklyReportWorkflow weekly
```

Use `triggers resume WeeklyReportWorkflow weekly` when ready. The manual run above exercises
the same report immediately, so there is no need to wait until Monday. See
[schedule semantics](workflows.md#triggers-and-notifications) for overlap and catch-up limits.

## Incident summary from logs {#incident-summary}

For an incident lead turning a bounded log export into a draft handoff:

```sh
agentworkflows init my-incident --template incident-summary
agentworkflows runs start IncidentSummaryWorkflow --input '@my-incident/input.json'
agentworkflows runs inspect RUN_ID
```

The input's `incident_id` is `INC-1042`. The `incident_logs` tool supplies four timestamped
log lines. Expect `status: completed`, `result.logs` retaining those lines, and
`result.summary` citing `[L1]` through `[L4]`: deployment at 09:00 UTC, errors at 09:05,
rollback at 09:12, and a return to baseline at 09:20. The fixture labels the deployment
as an unconfirmed cause and asks for the affected-user count. There is one tool and one
model receipt. It performs no remediation and declares no incident closed.

**Adapt it:** replace the `incident_logs` endpoint with a read-only log adapter accepting
`{incident_id}` and returning `{incident_id, source, lines}`. Enforce the team's access,
redaction, time window and size limits there. Keep UTC timestamps and stable log IDs.
The incident commander should verify the draft against logs and metrics before sharing it.
Raw logs and results enter Temporal history; receipt redaction alone does not remove that data.

## Document Q&A with citations {#document-qa}

For an internal helpdesk answering questions from a small, approved document collection:

```sh
agentworkflows init my-docs --template document-qa
agentworkflows runs start DocumentQAWorkflow --input '@my-docs/input.json'
agentworkflows runs inspect RUN_ID
```

The input's `question` asks who can approve a workflow and when approval expires. The
`search_documents` tool retrieves a handbook excerpt. Expect `status: completed`,
`result.answer` mentioning admins/approvers and seven days with `[S1]`, and
`result.citations` containing S1's title, URL, and exact excerpt. The timeline has one
retrieval tool receipt and one model receipt.

Change the question in `my-docs/input.json` to `What is the weather on Mars?` and start a new
run. The fixture retrieves nothing: expect `I don't know from the supplied documents.`,
empty citations, and no model call. Fixture retrieval recognizes approval and receipt
questions only; it is independent of the optional RAG service in Compose.

**Adapt it:** connect `search_documents` to your approved search/RAG backend. It accepts
`{query}` and returns a list of `{id, title, url, text}` excerpts, each with a unique stable
ID and source link. Enforce document access in that adapter and limit excerpt sizes.
The workflow requires JSON model output and checks that inline citation IDs match the
returned IDs and retrieved excerpts. Malformed JSON or fabricated/mismatched IDs fail the
run with a non-retryable error; an empty citation list yields the same abstention.
Review the error and source before starting a corrected run. Valid IDs do **not** prove
the cited text supports every claim; evaluate answer quality and abstention on your corpus.

## Research starter {#research}

The original default remains available: `agentworkflows init my-research`. Follow the
[quickstart](quickstart.md) to run `ResearchWorkflow` with a `topic`, inspect its two model
steps, approve its draft, and see the local publish fixture. It also accepts `token_limit`
and `cost_limit_usd`.

## Notifications and next steps

All template inputs accept an optional `model`, defaulting to `demo-openai`. Supply the
nonempty text field shown in each walkthrough; unknown fields are rejected before Temporal
starts the run. `agentworkflows usage` shows estimated spend across runs. Inspect each
timeline for receipt IDs; use the quickstart's [receipt export](quickstart.md#3-approve-and-inspect-the-receipts)
to verify the retained chain.

Compose sends approval-waiting, failure, and budget-threshold notifications to a local
fake inbox at <http://127.0.0.1:8025/> (JSON). Nothing goes to a real Slack workspace or
mailbox. Completed reports are run results, not automatic outbound report deliveries.
[Triggers and notifications](workflows.md#triggers-and-notifications) explains setup for
your team's authorized destinations.

Before real use, configure approved tools/models, separate team credentials and budgets,
and evaluate model output. Sanitize input before submitting it: workflow inputs, activity
results and drafts are retained in Temporal even though gateway receipts fingerprint prompts.
See [production readiness](production-readiness.md). When finished with the trial, follow
[Stop the trial](quickstart.md#stop-the-trial) to remove its containers and volumes.

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

This is the approval pattern used by the code-review template, with typed input:

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
