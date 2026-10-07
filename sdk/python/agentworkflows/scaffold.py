"""Create editable projects from the same examples shipped in the SDK."""

from __future__ import annotations

import json
import re
from importlib.resources import files
from pathlib import Path

TEMPLATES = {
    "research": ("research", "ResearchWorkflow", {"topic": "How should our team evaluate AI agents?"}),
    "support-triage": (
        "support_triage",
        "SupportTriageWorkflow",
        {"ticket": "I cannot sign in after resetting my password. Can you help?"},
    ),
    "code-review": (
        "code_review",
        "CodeReviewWorkflow",
        {"diff": "- return user.is_admin\n+ return True"},
    ),
}


def init_project(destination: Path, template: str) -> None:
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError(f"{destination} is not an empty directory. Choose a new directory; existing files are kept.")
    module, workflow, example = TEMPLATES[template]
    source = files("agentworkflows.examples").joinpath(f"{module}.py").read_text(encoding="utf-8")
    # The research module also keeps the legacy operator CLI; projects use the authenticated run API.
    source = source.split("\n\nasync def main()", 1)[0].rstrip() + "\n"
    # Both SDKs are third-party imports outside the installed package.
    source = re.sub(
        r"from temporalio import workflow\n\n(from agentworkflows.workflows import [^\n]+)",
        r"\1\nfrom temporalio import workflow",
        source,
    )
    destination.mkdir(parents=True, exist_ok=True)
    contents = {
        "workflow.py": source,
        "worker.py": (
            "from agentworkflows.worker import run_worker\n"
            f"from workflow import {workflow}\n\n"
            'if __name__ == "__main__":\n'
            f"    run_worker([{workflow}])\n"
        ),
        "input.json": json.dumps(example, indent=2) + "\n",
        "requirements.txt": "agentworkflows==0.2.0\n",
        ".gitignore": ".venv/\n.env\n__pycache__/\n",
        "README.md": f"""# {workflow}

Edit `workflow.py` and `input.json`. The SDK is already installed if you used `agentworkflows init`.
On another machine, install `requirements.txt` from your package index, or install
`sdk/python` from the same AgentWorkflows checkout until this version is published.

Start the stack using the [quickstart](https://ramazankara.github.io/agentworkflows/latest/quickstart/).
Its built-in worker already runs the unchanged template. From this directory:

```sh
agentworkflows runs start {workflow} --input '@input.json'
agentworkflows runs list
```

Set `AGENTWORKFLOWS_API_KEY=local-development-only` in your shell for the local demo.
Use the printed run UUID with `agentworkflows runs inspect RUN_ID`. Research and code review
wait at `awaiting_approval`: read the draft, then `agentworkflows runs approve RUN_ID`
(or add `--reject`). Support triage completes with a suggested reply; it sends nothing.
Inspect again for the result, budget, and timeline with receipt IDs.

## Run your edited workflow

From the checkout root, stop the built-in worker:

```sh
docker compose -f deploy/compose/compose.yaml stop workflow-worker
```

On Windows with Docker in WSL, prefix that command with `wsl.exe -d Ubuntu -e`.
In a separate terminal, activate the SDK environment, enter this project, and start your worker:

```sh
export AGENTWORKFLOWS_API_KEY=demo-worker
python worker.py
```

In PowerShell, use `$env:AGENTWORKFLOWS_API_KEY='demo-worker'` instead of `export`.
Wait for `Worker ready on demo-workflows`, then start runs from the original terminal.
Only this template is registered on your worker; stop it with Ctrl+C before restoring
the built-in worker with `docker compose -f deploy/compose/compose.yaml up -d workflow-worker`.
Do not run different workflow implementations on the same queue at the same time.

For your own team, use its worker credential, set `AGENTWORKFLOWS_TEAM` to its team ID,
`AGENTWORKFLOWS_URL` to its gateway, and `TEMPORAL_ADDRESS` to its Temporal server.
Ask the team administrator to approve this workflow's models, tools, egress, and budgets
in the existing sandbox policy before starting it. Provider keys belong on the gateway.
See the [workflow guide](https://ramazankara.github.io/agentworkflows/latest/workflows/).
""",
    }
    for name, content in contents.items():
        with (destination / name).open("x", encoding="utf-8", newline="\n") as output:
            output.write(content)
