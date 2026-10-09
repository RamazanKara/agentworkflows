# Quickstart

For Node.js workflows, use the [TypeScript quickstart](quickstart-typescript.md).

Run research → draft → human approval → publication, then inspect its receipts in about
five minutes. You need **Git, Python 3.12+, and Docker with Compose** already installed.
Initial image/package downloads can take longer on a slow connection. No GPU, Kubernetes,
Make, Node.js, or cloud account is needed for this path.

These instructions exercise the **unreleased 1.0 candidate from source** (package version
0.9.0). Start at the repository root if you already have this checkout; skip clone/cd.
The command verification and remaining Docker/WSL checks are recorded in
[release verification](release-verification.md#candidate-readiness-pass).

Choose your shell tab and stay in it. On Windows, use native Git and Python in PowerShell;
the commands below use Docker in the Ubuntu WSL distribution. Keep the checkout on Windows.

## 1. Install and start

=== "Bash (Linux/macOS)"

    ```bash
    git clone --branch main --depth 1 https://github.com/RamazanKara/agentworkflows.git
    cd agentworkflows
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install ./sdk/python
    docker compose -f deploy/compose/compose.yaml up -d --wait --build workflow-worker temporal-ui
    export AGENTWORKFLOWS_API_KEY=local-development-only
    ```

=== "PowerShell (Docker in WSL)"

    ```powershell
    git clone --branch main --depth 1 https://github.com/RamazanKara/agentworkflows.git
    cd agentworkflows
    python -m venv .venv
    $env:Path = "$PWD\.venv\Scripts;$env:Path"
    python -m pip install ./sdk/python
    wsl.exe -d Ubuntu -e docker compose -f deploy/compose/compose.yaml up -d --wait --build workflow-worker temporal-ui
    $env:AGENTWORKFLOWS_API_KEY = 'local-development-only'
    ```

Compose builds the gateway and Python worker from this checkout and pulls their dependencies.
Locally built images are not release-signed. `--wait` waits for gateway, Redis, PostgreSQL, and Temporal
health; the included worker runs every [gallery template](templates.md). The public demo key is a local admin
identity. [Team setup](workflows.md#teams-projects-and-roles) separates builders and approvers.

For the older published v0.5.1 trial, clone that tag and use `--no-build` instead;
it does not include the newer settings, history, SSO group or quorum features described here.
Generated projects pin the SDK's package version. Until matching release artifacts exist,
keep using the source SDK installed above instead of the scaffold's release-wheel URL.

This checkout includes [multi-reviewer approval policies](workflows.md#approval-policies).
The trial still defaults to one reviewer and seven days. Build both gateway and worker
from this checkout; upgrade the gateway before SDK workers on an existing installation.

The default **fake** returns canned text and synthetic prices without contacting a cloud.
For real generated text, select the [OpenAI option](#use-a-real-openai-key) now, then continue
with exactly the same workflow steps. Research and publish tools remain local fixtures in
both modes: no external document is published.

## 2. Create and run a project

These commands work in either shell:

```sh
agentworkflows init my-research
cd my-research
agentworkflows runs start ResearchWorkflow --input '@input.json'
```

You now have editable `workflow.py`, `worker.py`, `input.json`, and `input-schema.json`, plus a README with
worker instructions. `init --help` lists **research**, **code-review**, **support-triage**,
**weekly-report**, **incident-summary**, and **document-qa**.
It never overwrites an existing project. The built-in worker runs the unchanged templates;
follow [Edit your workflow](templates.md#run-your-edits) when you change the source.

Copy the returned `run_id` into the variable below (keep the quotes):

=== "Bash (Linux/macOS)"

    ```bash
    RUN_ID='paste-the-run_id-here'
    agentworkflows runs inspect "$RUN_ID"
    ```

=== "PowerShell (Docker in WSL)"

    ```powershell
    $RUN_ID = 'paste-the-run_id-here'
    agentworkflows runs inspect "$RUN_ID"
    ```

Look for `progress.stage: awaiting_approval` and read `progress.draft`. If it is still
working, repeat `inspect` after a few seconds. A fixture draft is deliberately synthetic.

## 3. Approve and inspect the receipts

After reviewing that draft, in the same shell:

```sh
agentworkflows runs approve "$RUN_ID"
agentworkflows runs inspect "$RUN_ID"
agentworkflows usage
```

Repeat `inspect` if the run is still finishing. Success is **`status: completed`**, a
**`result.status: published`**, and a timeline with `research`, two model calls, your
approval and `publish`, plus six notification receipts from the local Slack, webhook and
email fakes. Every entry has a `receipt_id`. The draft call falls back from OpenAI to Anthropic on purpose. The approval records the identity
of your gateway key. `approve --reject` instead finishes without publishing.

Open <http://127.0.0.1:8080/console/> and sign in with `local-development-only` to see the
same run, draft, result, timeline, and costs. Expand **Prompt and response** on a model
step or **Arguments and result** on a tool step to see
what it sent and received, with redaction and truncation notes. The demo captures redacted
content for seven days; terminal run records expire after 30 days. Receipt hashes contain
no captured text. Temporal history is at <http://127.0.0.1:8233> and has its own retention.

Save completed runs before their retention deadline with
`agentworkflows runs export --status completed --output runs.jsonl`. This follows
cursor pages and includes results, receipts and retained step content. Settings and
the audit log are available to team admins in the console. Run and audit exports
are retained views, not backups; see [CLI paging and export](sdk-reference.md#cli).

Since v0.7.0, open **Triggers → Run history** to inspect runs started by
each schedule or webhook. New launches carry their trigger name through the run page
and receipts. **Costs → Export CSV**, or `agentworkflows usage --output usage.csv`,
downloads the current UTC month. The total and provider/workflow rows overlap; do not
add them together. Both SDKs support the same history filter and CSV export; see the
[reference](sdk-reference.md#trigger-history-and-usage-csv-v070-candidate).

You can also choose **Run workflow**, select any starter, and fill in its generated form.
Forms come from the workflow policy's `inputSchema`; workflows without one use a JSON box.
For your edited project, keep `workflow.py`'s schema declaration and the reviewed policy
in sync using `input-schema.json`. Invalid fields are reported before a run starts.

To verify the retained receipt chain, return to the checkout root:

=== "Bash (Linux/macOS)"

    ```bash
    cd ..
    docker compose -f deploy/compose/compose.yaml logs --no-color --no-log-prefix inference-gateway > receipts.log
    python scripts/audit-verify.py receipts.log
    ```

=== "PowerShell (Docker in WSL)"

    ```powershell
    cd ..
    wsl.exe -d Ubuntu -e docker compose -f deploy/compose/compose.yaml logs --no-color --no-log-prefix inference-gateway | Out-File -Encoding utf8 receipts.log
    python scripts/audit-verify.py receipts.log
    ```

Expect `OK` and a nonzero record count. Hash verification detects edits and internal gaps;
[external anchors](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/audit-chain.md)
are needed to detect truncation or a complete rewrite. Save the export before removing the stack.

## Try a two-reviewer policy

1. Sign in to the console with `local-development-only`. In **Team settings / Research**,
   set **Required reviewers** to `2` and **Approval expiry (seconds)** to `3600`, then save.
2. In **Members & keys**, create a separate key with the **Approver** role. Keep the key
   for the second sign-in. Each key is a distinct identity in this local fixture.
3. Start a new Research run and approve its draft with the admin identity. The run must
   still be waiting, showing **1 of 2 approvals received** and a deadline.
4. Use **Switch account** with the new approver key and approve the same draft. Inspect
   the completed run and the two approval receipts. Repeating the first identity's vote
   cannot supply the missing approval; any reviewer's rejection ends the gate.
5. To exercise expiry, set it to `60`, start another run and leave it waiting. It fails
   with `ApprovalExpired` after its review deadline without publishing. Reset both
   settings to their policy defaults when finished. Existing runs keep their saved rules.

## Stop the trial

From the checkout root, stop and remove this trial's containers and volumes:

=== "Bash (Linux/macOS)"

    ```bash
    docker compose -f deploy/compose/compose.yaml down -v
    ```

=== "PowerShell (Docker in WSL)"

    ```powershell
    wsl.exe -d Ubuntu -e docker compose -f deploy/compose/compose.yaml down -v
    ```

Use `stop` instead of `down -v` to keep history and budgets. These commands also clean up
when the OpenAI override was selected.

## Use a real OpenAI key

Optional: from the checkout root after step 1, replace the model route with the included
OpenAI override. Read the key into your shell without saving it in the repository:

=== "Bash (Linux/macOS)"

    ```bash
    read -rs -p 'OpenAI API key: ' OPENAI_API_KEY; echo
    export OPENAI_API_KEY
    docker compose -f deploy/compose/compose.yaml -f deploy/compose/openai.yaml up -d --wait inference-gateway
    unset OPENAI_API_KEY
    ```

=== "PowerShell (Docker in WSL)"

    ```powershell
    $secret = Read-Host 'OpenAI API key' -AsSecureString
    $env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
    $savedWSLENV = $env:WSLENV
    $env:WSLENV = (@($savedWSLENV, 'OPENAI_API_KEY') | Where-Object { $_ }) -join ':'
    wsl.exe -d Ubuntu -e docker compose -f deploy/compose/compose.yaml -f deploy/compose/openai.yaml up -d --wait inference-gateway
    $env:WSLENV = $savedWSLENV
    Remove-Item Env:OPENAI_API_KEY
    Remove-Variable secret
    ```

Continue at step 2. The gateway alias `demo-openai` now uses **GPT-4.1 Mini** with no fake
fallback. Only the gateway receives the real key. This makes paid calls using the model's
[documented token rates](https://developers.openai.com/api/docs/models/gpt-4.1-mini);
review `deploy/compose/openai-model-routing.yaml` against your account pricing. The demo
limits each research run to 10,000 tokens / $5 and the team to $50. Estimates are not invoices.
Authentication or quota errors require a valid funded API account; gateway readiness alone
does not verify account access. The automated walkthrough uses fakes only.

## If something goes wrong

| Symptom | Next action |
| --- | --- |
| Docker cannot connect | Start your Docker engine; verify `docker info` (prefix with `wsl.exe -d Ubuntu -e` on Windows) |
| Port 8080 already in use | Use the alternate-port commands below before retrying `up`; keep the other service running |
| 404 Not Found even though Compose is healthy | Another Windows app may own port 8080 while WSL reports success; use the alternate-port commands below |
| CLI not found / wrong Python | Re-enter the environment from step 1; `python -m agentworkflows.cli --help` also works |
| 401 | Set `AGENTWORKFLOWS_API_KEY` to the gateway key, not a cloud key; the demo uses `local-development-only` |
| Invalid JSON / 422 | Edit the scaffold's `input.json`; pass it as quoted `'@input.json'` to avoid shell quoting problems |
| Worker unavailable / no draft | Check `docker compose -f deploy/compose/compose.yaml logs workflow-worker`; start its worker and retry inspection |
| Approval rejected | Inspect the run; only an undecided `awaiting_approval` draft can be approved by admin or approver |
| Model, tool, or egress denied | Use the team's approved policy; ask an admin to review it, rather than bypassing governance |
| Budget exhausted | Inspect `agentworkflows usage`; wait for the team window or ask an admin to review the limit |

For an occupied gateway port, from the checkout root in the same shell:

=== "Bash (Linux/macOS)"

    ```bash
    echo 'AGENTWORKFLOWS_GATEWAY_PORT=18080' >> deploy/compose/.env
    export AGENTWORKFLOWS_URL=http://127.0.0.1:18080
    docker compose -f deploy/compose/compose.yaml up -d --wait workflow-worker temporal-ui
    ```

=== "PowerShell (Docker in WSL)"

    ```powershell
    Add-Content -LiteralPath deploy/compose/.env -Value 'AGENTWORKFLOWS_GATEWAY_PORT=18080' -Encoding utf8
    $env:AGENTWORKFLOWS_URL = 'http://127.0.0.1:18080'
    wsl.exe -d Ubuntu -e docker compose -f deploy/compose/compose.yaml up -d --wait workflow-worker temporal-ui
    ```

The console is then at <http://127.0.0.1:18080/console/>. The same existing port mechanism
is available for Temporal (`AGENTWORKFLOWS_TEMPORAL_PORT`) and its UI
(`AGENTWORKFLOWS_TEMPORAL_UI_PORT`); add those to the same ignored `deploy/compose/.env` file if needed.

Next: [workflow concepts](concepts.md), [edit a template](templates.md),
[CLI and SDK reference](sdk-reference.md), or [full smoke test and Kubernetes lab](local-evaluation.md).
