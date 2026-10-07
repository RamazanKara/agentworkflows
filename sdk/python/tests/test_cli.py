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
        (["triggers", "list"], "/v1/workflow-triggers", {"triggers": []}, '"triggers": []'),
        (
            ["triggers", "pause", "Report", "daily"],
            "/v1/workflow-triggers/Report/daily",
            {"paused": True},
            '"paused": true',
        ),
        (
            ["triggers", "resume", "Report", "daily"],
            "/v1/workflow-triggers/Report/daily",
            {"paused": False},
            '"paused": false',
        ),
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
        if args[0] == "triggers" and args[1] != "list":
            assert request.method == "PATCH"
            assert json.loads(request.content) == {"paused": args[1] == "pause"}
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


def test_wrong_service_404_points_to_url_and_port(monkeypatch, capsys):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "local-development-only")
    real_client = httpx.Client
    monkeypatch.setattr(
        agentworkflows.httpx,
        "Client",
        lambda **kwargs: real_client(
            **kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(404, json={"detail": "Not Found"}))
        ),
    )
    assert main(["team"]) == 1
    error = capsys.readouterr().err
    assert "AGENTWORKFLOWS_URL" in error and "same port" in error


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


@pytest.mark.parametrize("template", ["research", "support-triage", "code-review"])
def test_init_creates_editable_project_offline(monkeypatch, tmp_path, capsys, template):
    import ast

    from agentworkflows.scaffold import TEMPLATES

    monkeypatch.delenv("AGENTWORKFLOWS_API_KEY", raising=False)
    target = tmp_path / "project with spaces"
    assert main(["init", str(target), "--template", template]) == 0
    workflow = TEMPLATES[template][1]
    assert f"class {workflow}" in (target / "workflow.py").read_text()
    ast.parse((target / "workflow.py").read_text())
    ast.parse((target / "worker.py").read_text())
    assert json.loads((target / "input.json").read_text()) == TEMPLATES[template][2]
    assert "worker.py" in (target / "README.md").read_text()
    assert "agentworkflows==" in (target / "requirements.txt").read_text()
    assert "--input '@input.json'" in capsys.readouterr().out
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    with pytest.raises(SystemExit) as exc:
        main(["init", str(target)])
    assert exc.value.code == 2
    assert "existing files are kept" in capsys.readouterr().err
    assert before == {p.name: p.read_bytes() for p in target.iterdir()}


def test_init_defaults_to_empty_current_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    assert (tmp_path / "workflow.py").is_file()


@pytest.mark.parametrize("existing", ["file", "directory", "hidden"])
def test_init_preserves_existing_content(tmp_path, existing, capsys):
    target = tmp_path / "project"
    if existing == "file":
        target.write_text("keep me")
    else:
        target.mkdir()
        (target / (".env" if existing == "hidden" else "notes.txt")).write_text("keep me")
    with pytest.raises(SystemExit) as exc:
        main(["init", str(target)])
    assert exc.value.code == 2
    assert "empty directory" in capsys.readouterr().err
    assert not (target / "workflow.py").exists()


def test_input_file_handles_utf8_bom_and_reports_invalid_json(tmp_path):
    import argparse

    from agentworkflows.cli import workflow_input

    path = tmp_path / "input with spaces.json"
    path.write_text('{"topic":"Grüße"}', encoding="utf-8-sig")
    assert workflow_input(f"@{path}") == {"topic": "Grüße"}
    with pytest.raises(argparse.ArgumentTypeError, match="Cannot read input file"):
        workflow_input(f"@{tmp_path / 'missing.json'}")
    with pytest.raises(argparse.ArgumentTypeError, match="valid JSON"):
        workflow_input('{"topic":')
    with pytest.raises(argparse.ArgumentTypeError, match="valid JSON"):
        workflow_input('{"cost_limit_usd": NaN}')
