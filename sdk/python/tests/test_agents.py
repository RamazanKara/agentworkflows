import asyncio
import json
import sys
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import httpx2
import pytest
from agentworkflows.activities import GatewayActivities
from agentworkflows.containers import run_container, validate_workspace
from agentworkflows.examples.frameworks import AGENTS, FrameworkWorkflow
from agentworkflows.workflows import Call, WorkflowGateway
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner


def mock_clients(monkeypatch, respond):
    class Client(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)

    def framework_respond(request):
        response = respond(request)
        return httpx2.Response(response.status_code, content=response.content, headers=response.headers)

    class FrameworkClient(httpx2.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx2.MockTransport(framework_respond), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(httpx2, "AsyncClient", FrameworkClient)


def test_framework_workflow_loads_in_temporal_sandbox():
    from temporalio.workflow import _Definition

    async def validate():
        SandboxedWorkflowRunner().prepare_workflow(_Definition.must_from_class(FrameworkWorkflow))

    asyncio.run(validate())


@pytest.mark.parametrize("name", list(AGENTS))
def test_real_framework_clients_use_governed_activity_context(monkeypatch, name):
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "PUT":
            assert json.loads(request.content)["workflow"] == "FrameworkWorkflow"
            return httpx.Response(200, json={})
        if request.url.path.startswith("/v1/tools"):
            return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": "source"}]}})
        if request.url.path == "/v1/messages":
            return httpx.Response(
                200,
                json={
                    "id": "msg-fixture",
                    "model": "demo-anthropic",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "governed"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 5, "output_tokens": 2},
                },
            )
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "id": "chat-fixture",
                "object": "chat.completion",
                "created": 1,
                "model": "demo-openai",
                "choices": [
                    {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "governed"}}
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            },
        )

    mock_clients(monkeypatch, respond)
    environment = ActivityEnvironment()
    environment.info = replace(
        environment.info, workflow_type="FrameworkWorkflow", workflow_run_id="run", activity_id="7"
    )
    result = asyncio.run(
        environment.run(
            GatewayActivities("http://gateway", "team-key", agents=AGENTS).call,
            Call("agent", {"topic": "test"}, tool=name),
        )
    )
    assert result == {"result": "governed"}
    for index, request in enumerate(requests[1:], start=1):
        assert request.url.host == "gateway"
        assert request.headers["X-Workflow-Run-ID"] == "run"
        assert request.headers["X-Workflow-Step-ID"] == f"7/{index}"
        assert request.headers["Authorization"] == "Bearer team-key"


def test_agent_policy_failure_does_not_retry_inside_framework(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "PUT":
            return httpx.Response(200, json={})
        return httpx.Response(403, json={"detail": {"reason": "workflow_model_denied", "message": "denied"}})

    mock_clients(monkeypatch, respond)
    with pytest.raises(ApplicationError) as error:
        asyncio.run(
            ActivityEnvironment().run(
                GatewayActivities("http://gateway", "team-key", agents=AGENTS).call,
                Call("agent", {"topic": "test"}, tool="agents-sdk"),
            )
        )
    assert error.value.non_retryable
    assert len(requests) == 2


def test_container_step_does_not_retry_arbitrary_code(monkeypatch):
    execute = AsyncMock(return_value={"result": "done"})
    monkeypatch.setattr("agentworkflows.workflows.workflow.execute_activity", execute)
    assert asyncio.run(WorkflowGateway().container("coder", {"task": "test"})) == "done"
    assert execute.call_args.kwargs["retry_policy"].maximum_attempts == 1


def workspace():
    spec = {
        "automountServiceAccountToken": False,
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 10001,
            "runAsGroup": 10001,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "containers": [
            {
                "name": "workspace",
                "image": "approved-image",
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "capabilities": {"drop": ["ALL"]},
                },
            }
        ],
        "volumes": [{"name": "workspace", "persistentVolumeClaim": {"claimName": "workspace"}}],
    }
    sandbox = {
        "apiVersion": "agents.x-k8s.io/v1beta1",
        "metadata": {"uid": "owner"},
        "spec": {"podTemplate": {"spec": deepcopy(spec)}},
    }
    pod = {
        "metadata": {"ownerReferences": [{"uid": "owner", "kind": "Sandbox"}]},
        "spec": spec,
        "status": {"phase": "Running"},
    }
    policies = {
        "items": [
            {
                "metadata": {"name": "agent-workspace-default-deny"},
                "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]},
            },
            {
                "metadata": {"name": "agent-workspace-approved-egress"},
                "spec": {
                    "podSelector": {},
                    "policyTypes": ["Egress"],
                    "egress": [
                        {
                            "to": [
                                {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "inference"}}}
                            ],
                            "ports": [{"protocol": "TCP", "port": 8080}],
                        }
                    ],
                },
            },
        ]
    }
    return sandbox, pod, policies


@pytest.mark.parametrize(
    "violation", ["token", "network", "owner", "root", "override", "sidecar", "hostpath", "extra-policy"]
)
def test_container_refuses_workspace_isolation_bypasses(violation):
    sandbox, pod, policies = workspace()
    gateway = "http://gateway.inference.svc.cluster.local:8080"
    validate_workspace(sandbox, pod, policies, gateway)
    if violation == "token":
        pod["spec"]["volumes"].append({"name": "token", "projected": {}})
    elif violation == "network":
        policies["items"][1]["spec"]["egress"].append({})
    elif violation == "owner":
        pod["metadata"]["ownerReferences"] = []
    elif violation == "root":
        pod["spec"]["securityContext"]["runAsUser"] = 0
    elif violation == "override":
        pod["spec"]["containers"][0]["securityContext"]["runAsUser"] = 0
    elif violation == "sidecar":
        pod["spec"]["containers"].append(pod["spec"]["containers"][0])
    elif violation == "hostpath":
        pod["spec"]["volumes"].append({"name": "host", "hostPath": {"path": "/"}})
    else:
        policies["items"].append({"metadata": {"name": "allow-all"}, "spec": {"egress": [{}]}})
    with pytest.raises(ApplicationError, match="not isolated"):
        validate_workspace(sandbox, pod, policies, gateway)


@pytest.mark.parametrize("failure", [False, True])
def test_container_exec_uses_stdin_grant_and_always_finishes(monkeypatch, failure):
    sandbox, pod, policies = workspace()
    requests = []
    executions = []

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("/start"):
            return httpx.Response(
                200,
                json={
                    "namespace": "team-code",
                    "sandbox": "coder",
                    "command": ["python", "/app/agent.py"],
                    "arguments": {"task": "test"},
                    "timeout_seconds": 120,
                    "api_key": "awf_short-lived",
                    "credential_id": "a" * 64,
                    "expires_at": 1,
                },
            )
        return httpx.Response(200, json={"result": "safe output"})

    async def kubectl(*args, **kwargs):
        if "exec" in args:
            executions.append((args, kwargs))
            if failure:
                raise ApplicationError("fixture failed", non_retryable=True)
            return json.dumps({"exit_code": 0, "output": "raw output"}).encode()
        return json.dumps(sandbox if "sandbox" in args else pod if "pod" in args else policies).encode()

    monkeypatch.setattr("agentworkflows.containers._kubectl", kubectl)

    async def run():
        async with httpx.AsyncClient(
            base_url="http://gateway.inference.svc.cluster.local:8080", transport=httpx.MockTransport(respond)
        ) as client:
            return await run_container(
                client,
                Call("container", {"arguments": {}}, tool="coder"),
                {"X-Workflow-Run-ID": "run", "X-Workflow-Step-ID": "1"},
            )

    if failure:
        with pytest.raises(ApplicationError, match="fixture failed"):
            asyncio.run(run())
    else:
        assert asyncio.run(run()) == {"result": "safe output"}
    assert requests[-1].url.path.endswith("/finish")
    finish = json.loads(requests[-1].content)
    assert finish["exit_code"] == (-1 if failure else 0)
    args, options = executions[0]
    assert "awf_short-lived" not in str(args)
    assert json.loads(options["data"])["api_key"] == "awf_short-lived"


@pytest.mark.skipif(sys.platform != "linux", reason="The workspace runner executes in Linux containers")
@pytest.mark.parametrize("behavior", ["success", "timeout", "output-limit"])
def test_workspace_runner_limits_output_without_limiting_workspace_files(monkeypatch, tmp_path, capsys, behavior):
    import io
    import time

    from agentworkflows.container_runner import main

    commands = {
        "success": "from pathlib import Path; Path('large.txt').write_text('x' * 100000); print('done')",
        "timeout": "import time; time.sleep(30)",
        "output-limit": "print('x' * 100000)",
    }
    plan = {
        "command": [sys.executable, "-c", commands[behavior]],
        "arguments": {},
        "expires_at": time.time() + (0.2 if behavior == "timeout" else 10),
        "api_key": "fake-step",
        "gateway_url": "http://gateway",
        "headers": {"X-Workflow-Run-ID": "run", "X-Workflow-Step-ID": "1"},
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(plan)))
    create = asyncio.create_subprocess_exec

    async def start(*args, **kwargs):
        assert kwargs["cwd"] == "/workspace"
        kwargs["cwd"] = str(tmp_path)
        return await create(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    asyncio.run(main())
    result = json.loads(capsys.readouterr().out)
    assert result["exit_code"] == (0 if behavior == "success" else -1)
    if behavior == "success":
        assert (tmp_path / "large.txt").stat().st_size == 100000
        assert result["output"] == "done\n"
