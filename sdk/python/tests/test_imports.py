import asyncio
import json
import subprocess
import sys
import textwrap

import agentworkflows
import httpx
import pytest
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from temporalio.workflow import _Definition


def test_cli_and_client_start_without_importing_temporal():
    probe = (
        "import sys, agentworkflows.cli, agentworkflows\n"
        "assert agentworkflows.GatewayClient\n"
        "print(sorted(name for name in ('temporalio', 'importlib.metadata') if name in sys.modules))"
    )
    output = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True).stdout
    assert json.loads(output.replace("'", '"')) == []


def test_version_is_resolved_on_first_use(capsys):
    from agentworkflows.cli import main

    assert isinstance(agentworkflows.__version__, str) and agentworkflows.__version__
    from agentworkflows import __version__

    assert __version__ == agentworkflows.__version__
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out == f"agentworkflows {__version__}\n"


def test_workflow_sandbox_shares_the_host_httpx(tmp_path, monkeypatch):
    # Re-importing httpx for every sandboxed workflow costs memory and time; the SDK passes it through.
    (tmp_path / "sandbox_probe.py").write_text(textwrap.dedent("""
        import json

        from agentworkflows.workflows import ApprovalWorkflow
        from temporalio import workflow

        import agentworkflows

        assert agentworkflows.httpx is json.sandbox_probe_httpx


        @workflow.defn
        class ProbeWorkflow(ApprovalWorkflow):
            @workflow.run
            async def run(self) -> None:
                return None
    """))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(json, "sandbox_probe_httpx", httpx, raising=False)
    import sandbox_probe

    async def prepare() -> None:
        SandboxedWorkflowRunner().prepare_workflow(_Definition.must_from_class(sandbox_probe.ProbeWorkflow))

    asyncio.run(prepare())
