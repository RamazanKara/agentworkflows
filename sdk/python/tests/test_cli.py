import json

import agentworkflows
import httpx
import pytest
from agentworkflows.cli import main


def test_help_needs_no_credentials(monkeypatch, capsys):
    monkeypatch.delenv("AGENTWORKFLOWS_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "AGENTWORKFLOWS_API_KEY" in capsys.readouterr().out


def test_missing_key_is_actionable(monkeypatch, capsys):
    monkeypatch.delenv("AGENTWORKFLOWS_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["models"])
    assert exc.value.code == 2
    assert "set AGENTWORKFLOWS_API_KEY" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("args", "path", "body", "output"),
    [
        (["models"], "/v1/models", {"data": [{"id": "demo-openai"}]}, "demo-openai"),
        (
            ["chat", "hello", "--model", "demo-openai"],
            "/v1/chat/completions",
            {"choices": [{"message": {"content": "hello team"}}]},
            "hello team",
        ),
        (["usage"], "/v1/usage", {"sandbox_id": "demo"}, '"sandbox_id": "demo"'),
    ],
)
def test_commands_use_gateway(monkeypatch, capsys, args, path, body, output):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "test-key")
    monkeypatch.setenv("AGENTWORKFLOWS_URL", "http://gateway.test")

    def handler(request):
        assert request.url.path == path
        assert request.headers["Authorization"] == "Bearer test-key"
        if args[0] == "chat":
            assert b'"model":"demo-openai"' in request.content
        return httpx.Response(200, json=body)

    real_client = httpx.Client
    monkeypatch.setattr(
        agentworkflows.httpx,
        "Client",
        lambda **kwargs: real_client(
            **kwargs,
            transport=httpx.MockTransport(handler),
        ),
    )
    assert main(args) == 0
    assert output in capsys.readouterr().out


def test_denied_model_reports_remedy_and_request_id(monkeypatch, capsys):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        agentworkflows.httpx,
        "Client",
        lambda **kwargs: real_client(
            **kwargs,
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    400,
                    json={"detail": {"reason": "model_not_allowed", "request_id": "req-test"}},
                )
            ),
        ),
    )
    assert main(["chat", "hello", "--model", "denied"]) == 1
    error = capsys.readouterr().err
    assert "agentworkflows models" in error
    assert "req-test" in error
    assert "test-key" not in error


def test_connection_failure_is_actionable(monkeypatch, capsys):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "test-key")

    def unavailable(*_args, **_kwargs):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(agentworkflows.GatewayClient, "models", unavailable)
    assert main(["models"]) == 1
    assert "Check AGENTWORKFLOWS_URL" in capsys.readouterr().err


@pytest.mark.parametrize("operation", ["start", "list", "inspect", "cancel", "retry", "approve"])
def test_run_commands_use_authenticated_gateway(monkeypatch, capsys, operation):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "role-key")
    run_id = "14bf0bba-d747-4d2f-afab-2eb35663977b"
    args = ["runs", operation]
    if operation == "start":
        args += ["--input", '{"topic":"test"}', "--project", "research"]
    elif operation != "list":
        args += [run_id]

    def respond(request):
        assert request.headers["Authorization"] == "Bearer role-key"
        if operation in {"start", "list"}:
            assert request.url.path == "/v1/workflow-runs"
        else:
            assert request.url.path == f"/v1/workflow-runs/{run_id}" + (
                "" if operation == "inspect" else f"/{operation}"
            )
        if operation == "start":
            body = json.loads(request.content)
            assert body["workflow"] == "ResearchWorkflow" and body["project"] == "research"
            assert body["input"]["topic"] == "test" and body["request_id"]
        if operation == "approve":
            assert json.loads(request.content) == {"approved": True}
        return httpx.Response(200, json={"run_id": run_id})

    real_client = httpx.Client
    monkeypatch.setattr(
        agentworkflows.httpx, "Client", lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(respond))
    )
    assert main(args) == 0
    assert run_id in capsys.readouterr().out


def test_ambiguous_start_keeps_request_id_for_retry(monkeypatch, capsys):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "role-key")

    def unavailable(*args, **kwargs):
        raise httpx.ReadTimeout("response lost")

    monkeypatch.setattr(agentworkflows.GatewayClient, "start_run", unavailable)
    assert main(["runs", "start", "--input", "{}"]) == 1
    assert "--request-id" in capsys.readouterr().err
