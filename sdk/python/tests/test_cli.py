import json

import agentworkflows
import httpx
import pytest
from agentworkflows.cli import main
from agentworkflows.scaffold import TEMPLATES


@pytest.mark.parametrize(
    ("args", "method", "path", "body"),
    [
        (["keys", "list"], "GET", "/v1/team/keys", None),
        (["keys", "create", "--name", "Alice", "--role", "builder", "--project", "default"],
         "POST", "/v1/team/keys", {"name": "Alice", "role": "builder", "project": "default"}),
        (["keys", "update", "abc", "--name", "CI", "--expires-at", "2030-01-01T00:00:00Z"],
         "PATCH", "/v1/team/keys/abc", {"name": "CI", "expires_at": "2030-01-01T00:00:00Z"}),
        (["keys", "update", "abc", "--expires-at", "", "--project", ""],
         "PATCH", "/v1/team/keys/abc", {"expires_at": None, "project": None}),
        (["keys", "revoke", "abc"], "DELETE", "/v1/team/keys/abc", None),
    ],
)
def test_key_commands(monkeypatch, capsys, args, method, path, body):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "admin-key")

    def respond(request):
        assert request.method == method and request.url.path == path
        assert request.headers["Authorization"] == "Bearer admin-key"
        assert (json.loads(request.content) if request.content else None) == body
        return httpx.Response(200, json={"key_id": "abc"})

    original = httpx.Client
    monkeypatch.setattr(
        agentworkflows.httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond))
    )
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == {"key_id": "abc"}


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


@pytest.mark.parametrize("template", TEMPLATES)
def test_init_creates_editable_project_offline(monkeypatch, tmp_path, capsys, template):
    import ast

    monkeypatch.delenv("AGENTWORKFLOWS_API_KEY", raising=False)
    target = tmp_path / "project with spaces"
    assert main(["init", str(target), "--template", template]) == 0
    workflow = TEMPLATES[template][1]
    assert f"class {workflow}" in (target / "workflow.py").read_text()
    ast.parse((target / "workflow.py").read_text())
    ast.parse((target / "worker.py").read_text())
    assert json.loads((target / "input.json").read_text()) == TEMPLATES[template][2]
    schema = json.loads((target / "input-schema.json").read_text())
    assert schema["type"] == "object"
    assert set(schema["required"]) <= TEMPLATES[template][2].keys()
    assert "@input_schema(" in (target / "workflow.py").read_text()
    assert "worker.py" in (target / "README.md").read_text()
    assert f"templates/#{template}" in (target / "README.md").read_text()
    assert "releases/download/v0.9.0/agentworkflows-0.9.0-py3-none-any.whl" in (target / "requirements.txt").read_text()
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


def test_scaffold_pins_the_packaged_sdk_version() -> None:
    import tomllib
    from pathlib import Path

    from agentworkflows import scaffold

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == scaffold.SDK_VERSION


@pytest.mark.parametrize(
    "args,method,path,body,revision",
    [
        (["settings", "show"], "GET", "/v1/team/settings", None, None),
        (["settings", "set", "--fields", '{"cost_limit_usd":0}', "--revision", "0"],
         "PATCH", "/v1/team/settings", {"fields": {"cost_limit_usd": 0}}, "0"),
        (["settings", "reset", "model_routes.team/a & b", "--revision", "4"],
         "DELETE", "/v1/team/settings/model_routes.team%2Fa%20%26%20b", None, "4"),
    ],
)
def test_settings_commands(monkeypatch, capsys, args, method, path, body, revision):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "admin-key")
    calls = []

    def respond(request):
        calls.append(request)
        assert request.method == method and request.url.raw_path.decode() == path
        assert request.headers["Authorization"] == "Bearer admin-key"
        assert request.headers.get("If-Match") == revision
        assert (json.loads(request.content) if request.content else None) == body
        return httpx.Response(200, json={"revision": 5, "fields": {}})

    original = httpx.Client
    monkeypatch.setattr(
        agentworkflows.httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond))
    )
    assert main(args) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"revision": 5, "fields": {}} and not captured.err
    assert len(calls) == 1


def test_settings_set_reads_json_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "admin-key")
    path = tmp_path / "settings with spaces.json"
    fields = {"cost_limit_usd": 0, "workflows.Report.approval_required": False,
              "workflows.Report.allowed_providers": ["openai"]}
    path.write_text(json.dumps(fields), encoding="utf-8-sig")

    def respond(request):
        assert json.loads(request.content) == {"fields": fields}
        assert request.headers["If-Match"] == "0"
        return httpx.Response(200, json={"revision": 1})

    original = httpx.Client
    monkeypatch.setattr(
        agentworkflows.httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond))
    )
    assert main(["settings", "set", "--fields", f"@{path}", "--revision", "0"]) == 0
    assert json.loads(capsys.readouterr().out) == {"revision": 1}


@pytest.mark.parametrize("fields", ["{}", "[]", "null", "broken", '{"cost_limit_usd": NaN}'])
def test_settings_set_rejects_invalid_input(fields, capsys):
    with pytest.raises(SystemExit) as error:
        main(["settings", "set", "--fields", fields, "--revision", "0"])
    assert error.value.code == 2
    assert "--fields" in capsys.readouterr().err


@pytest.mark.parametrize("args", [["settings", "set", "--fields", '{"cost_limit_usd":5}'],
                                 ["settings", "reset", "cost_limit_usd"]])
def test_settings_writes_require_a_reviewed_revision(args, capsys):
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2 and "--revision" in capsys.readouterr().err


@pytest.mark.parametrize("operation", ["set", "reset"])
@pytest.mark.parametrize("status,reason,message", [
    (409, "team_settings_conflict", "Settings changed. Reload and review them before saving again."),
    (422, "team_settings_invalid", "cost_limit_usd: Use a finite USD amount between 0 and 1000000."),
])
def test_settings_errors_are_actionable(monkeypatch, capsys, operation, status, reason, message):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "private-admin-key")
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, headers={"X-Request-ID": "settings-request"},
                              json={"detail": {"reason": reason, "message": message}})

    original = httpx.Client
    monkeypatch.setattr(
        agentworkflows.httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond))
    )
    args = ["settings", operation, "--revision", "0"]
    args += ["--fields", '{"cost_limit_usd":-1}'] if operation == "set" else ["cost_limit_usd"]
    assert main(args) == 1
    captured = capsys.readouterr()
    assert not captured.out and len(calls) == 1
    assert message in captured.err and "settings show" in captured.err
    assert "settings-request" in captured.err and "private-admin-key" not in captured.err
    assert "--revision" in captured.err if status == 409 else "named fields" in captured.err


@pytest.mark.parametrize("operation", ["list", "verify"])
def test_audit_commands_send_filters(monkeypatch, capsys, operation):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "admin-key")
    args = ["audit", operation, "--from", "0", "--to", "100.5"]
    expected = {"from": "0.0", "to": "100.5"}
    result = {"enabled": True, "ok": True, "checked": 0, "first_break": None, "boundaries": []}
    if operation == "list":
        args += ["--event-type", "team_key", "--actor", "Ada & Bob", "--project", "a/b", "--run-id", "run",
                 "--cursor", "9-0", "--limit", "200"]
        expected.update(
            event_type="team_key", actor="Ada & Bob", project="a/b", run_id="run", cursor="9-0", limit="200"
        )
        result = {"enabled": True, "events": [], "next_cursor": "5-0"}

    def respond(request):
        assert request.method == "GET" and request.url.path == "/v1/team/audit" + (
            "/verify" if operation == "verify" else "")
        assert request.headers["Authorization"] == "Bearer admin-key"
        assert dict(request.url.params) == expected
        return httpx.Response(200, json=result)

    original = httpx.Client
    monkeypatch.setattr(
        agentworkflows.httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond))
    )
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == result


@pytest.mark.parametrize("ok", [False, None])
def test_audit_verify_fails_for_broken_or_disabled_views(monkeypatch, capsys, ok):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "admin-key")
    verification = {
        "enabled": ok is not None, "ok": ok, "checked": 0, "boundaries": [],
        "first_break": {"chain_id": "c", "sequence": 2, "reason": "broken_view_link"} if ok is False else None,
        "message": "Enable Redis." if ok is None else None,
    }
    original = httpx.Client
    monkeypatch.setattr(agentworkflows.httpx, "Client", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=verification))))
    assert main(["audit", "verify"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == verification
    assert ("first_break" if ok is False else "Enable Redis") in captured.err


@pytest.mark.parametrize("destination", ["default", "stdout", "file"])
def test_audit_export_writes_original_json_lines_and_pages(monkeypatch, tmp_path, capsys, destination):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "admin-key")
    output = tmp_path / "audit log.jsonl"
    events = [{"event": "team_key", "name": "Grüße\nCI"}, {"event": "team_key", "name": "older"}]
    calls = []

    def respond(request):
        calls.append(request)
        assert dict(request.url.params) == {"from": "0.0", "to": "100.0", "actor": "Ada & Bob", "limit": "1",
                                            "cursor": "9-0" if len(calls) == 1 else "5-0"}
        return httpx.Response(200, json={"enabled": True, "events": [{"event": events[len(calls) - 1]}],
                                        "next_cursor": "5-0" if len(calls) == 1 else None})

    original = httpx.Client
    monkeypatch.setattr(
        agentworkflows.httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond))
    )
    args = ["audit", "export", "--from", "0", "--to", "100", "--actor", "Ada & Bob", "--limit", "1", "--cursor", "9-0"]
    if destination != "default":
        args += ["--output", str(output) if destination == "file" else "-"]
    assert main(args) == 0
    captured = capsys.readouterr()
    lines = output.read_text(encoding="utf-8") if destination == "file" else captured.out
    assert [json.loads(line) for line in lines.splitlines()] == events
    assert lines.endswith("\n") and len(calls) == 2 and not captured.err
    if destination == "file":
        assert not captured.out
        assert b"\r\n" not in output.read_bytes()


def test_audit_export_reports_disabled_view(monkeypatch, capsys):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "admin-key")
    original = httpx.Client
    monkeypatch.setattr(agentworkflows.httpx, "Client", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(
            200, json={"enabled": False, "message": "Enable Redis."}))))
    assert main(["audit", "export"]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "Enable Redis" in captured.err


def test_audit_export_reports_unwritable_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "admin-key")
    assert main(["audit", "export", "--output", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "audit export failed" in captured.err


@pytest.mark.parametrize("destination", ["stdout", "file"])
def test_run_export_writes_json_lines(monkeypatch, tmp_path, capsys, destination):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "viewer-key")
    output = tmp_path / "run history.jsonl"
    record = {"run_id": "example", "result": "Grüße\nteam"}

    def export(self, **filters):
        assert filters == {"project": "demo", "status": "completed", "limit": 1, "cursor": "older"}
        yield json.dumps(record) + "\n"

    monkeypatch.setattr(agentworkflows.GatewayClient, "export_runs", export)
    args = ["runs", "export", "--project", "demo", "--status", "completed", "--limit", "1", "--cursor", "older"]
    assert main([*args, *(["--output", str(output)] if destination == "file" else [])]) == 0
    captured = capsys.readouterr()
    text = output.read_text(encoding="utf-8") if destination == "file" else captured.out
    assert [json.loads(line) for line in text.splitlines()] == [record]
    assert not captured.err
    if destination == "file":
        assert not captured.out


def test_run_export_reports_unwritable_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "viewer-key")
    assert main(["runs", "export", "--output", str(tmp_path)]) == 1
    assert "run export failed" in capsys.readouterr().err


@pytest.mark.parametrize("destination", ["-", "file", "unwritable"])
def test_usage_csv_output(monkeypatch, tmp_path, capsys, destination):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "viewer-key")
    csv = 'name,cost_usd\r\n"Grüße, team",1.000000001\r\n'
    monkeypatch.setattr(agentworkflows.GatewayClient, "export_usage", lambda self: csv)
    path = tmp_path / "usage.csv" if destination == "file" else tmp_path
    assert main(["usage", "--output", "-" if destination == "-" else str(path)]) == (
        1 if destination == "unwritable" else 0
    )
    captured = capsys.readouterr()
    if destination == "unwritable":
        assert "usage export failed" in captured.err
    elif destination == "file":
        assert path.read_bytes() == csv.encode() and not captured.out
    else:
        assert captured.out == csv


def test_run_list_sends_filters_and_rejects_mixed_paging(monkeypatch, capsys):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "viewer-key")

    def runs(self, **filters):
        assert filters == {"workflow": "ResearchWorkflow", "trigger": "daily", "status": "awaiting_approval",
                           "limit": 5, "cursor": "older"}
        return {"runs": [], "next_cursor": None, "next_offset": None}

    monkeypatch.setattr(agentworkflows.GatewayClient, "runs", runs)
    assert main(["runs", "list", "--workflow", "ResearchWorkflow", "--trigger", "daily",
                 "--status", "awaiting_approval",
                 "--limit", "5", "--cursor", "older"]) == 0
    assert json.loads(capsys.readouterr().out)["next_cursor"] is None
    with pytest.raises(SystemExit) as exc:
        main(["runs", "list", "--offset", "1", "--cursor", "older"])
    assert exc.value.code == 2


@pytest.mark.parametrize("command", [["settings"], ["settings", "set"], ["settings", "reset"],
                                     ["audit"], ["audit", "list"], ["audit", "verify"], ["audit", "export"]])
def test_admin_command_help_needs_no_credentials(monkeypatch, capsys, command):
    monkeypatch.delenv("AGENTWORKFLOWS_API_KEY", raising=False)
    with pytest.raises(SystemExit) as error:
        main([*command, "--help"])
    assert error.value.code == 0 and capsys.readouterr().out


def test_workflows_commands_register_list_and_remove(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "admin-key")
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"type": "object", "properties": {"ticket": {"type": "string"}}}))
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path, request.headers.get("If-Match"),
                      json.loads(request.content) if request.content else None))
        assert request.headers["Authorization"] == "Bearer admin-key"
        return httpx.Response(200, json={"revision": 3, "workflows": []})

    original = httpx.Client
    monkeypatch.setattr(
        agentworkflows.httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond))
    )
    assert main(["workflows", "list"]) == 0
    assert main([
        "workflows", "register", "Triage", "--model", "primary", "--model", "backup", "--tool", "team.search",
        "--token-limit", "500", "--cost-limit", "0.5", "--reviewers", "2", "--input-schema", f"@{schema}",
        "--revision", "3",
    ]) == 0
    assert main(["workflows", "register", "Quick", "--model", "primary", "--no-approval"]) == 0
    assert main(["workflows", "remove", "Triage", "--revision", "3"]) == 0
    assert calls[0][:2] == ("GET", "/v1/team/workflows")
    assert calls[1] == ("PUT", "/v1/team/workflows/Triage", "3", {
        "allowed_models": ["primary", "backup"], "allowed_tools": ["team.search"], "token_limit": 500,
        "cost_limit_usd": 0.5, "required_approvals": 2,
        "input_schema": {"type": "object", "properties": {"ticket": {"type": "string"}}},
    })
    assert calls[2][0] == "GET" and calls[3] == (
        "PUT", "/v1/team/workflows/Quick", "3", {"allowed_models": ["primary"], "approval_required": False},
    )
    assert calls[4] == ("DELETE", "/v1/team/workflows/Triage", "3", None)
    assert capsys.readouterr().out.count('"revision": 3') == 4


@pytest.mark.parametrize(("status", "reason", "hint"), [
    (409, "team_workflows_conflict", "workflows list"),
    (403, "team_workflows_disabled", "reviewed policy"),
    (422, "team_workflow_invalid", "models and tools shown"),
])
def test_workflows_errors_are_actionable(monkeypatch, capsys, status, reason, hint):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "private-admin-key")
    original = httpx.Client
    respond = lambda request: httpx.Response(  # noqa: E731
        status, headers={"X-Request-ID": "workflows-request"}, json={"detail": {"reason": reason, "message": "No."}}
    )
    monkeypatch.setattr(
        agentworkflows.httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond))
    )
    assert main(["workflows", "remove", "Triage", "--revision", "1"]) == 1
    captured = capsys.readouterr()
    assert hint in captured.err and "workflows-request" in captured.err and "private-admin-key" not in captured.err
