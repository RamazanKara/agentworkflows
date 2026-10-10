# ruff: noqa: F811
import logging
from dataclasses import replace

import pytest
from app.policy import SandboxPolicy, SandboxPolicySet, ToolRoute

from tests.test_audit_verify import _double_logged_lines, _load_verifier
from tests.test_teams import auth, team_gateway  # noqa: F401

PATH = "/v1/team/workflows"
SCHEMA = {
    "type": "object",
    "properties": {"ticket": {"type": "string", "minLength": 1}, "urgent": {"type": "boolean"}},
    "required": ["ticket"],
    "additionalProperties": False,
}
BODY = {"allowed_models": ["primary"], "allowed_tools": ["team.search"], "cost_limit_usd": 0.5, "token_limit": 500}


@pytest.fixture
def gateway(team_gateway):
    client, app = team_gateway
    team = app.state.sandbox_policy_set.policies["team"]
    tool = ToolRoute.model_validate({"url": "http://tools.test:9000/search", "costUsd": 0})
    app.state.sandbox_policy_set.policies["team"] = replace(
        team, tools={"team.search": tool, "team.publish": tool}, estimated_token_budget=2000
    )
    return client, app


def put(client, name="Triage", body=None, revision=0, role="admin"):
    return client.put(
        f"{PATH}/{name}", headers={**auth(role), "If-Match": str(revision)}, json={**BODY, **(body or {})}
    )


def test_registration_reads_and_writes_need_an_unrestricted_admin(gateway):
    client, _ = gateway
    for role in ("builder", "approver", "viewer", "project"):
        assert client.get(PATH, headers=auth(role)).status_code == 403
        assert put(client, role=role).status_code == 403
        assert client.delete(f"{PATH}/Triage", headers={**auth(role), "If-Match": "0"}).status_code == 403
    assert client.get(PATH).status_code == 401
    initial = client.get(PATH, headers=auth("admin")).json()
    assert initial["revision"] == 0 and initial["enabled"] is True and initial["workflows"] == []
    assert initial["options"] == {
        "providers": ["anthropic", "openai"],
        "models": [
            {"id": "primary", "provider": "openai", "simulated": False},
            {"id": "backup", "provider": "anthropic", "simulated": False},
        ],
        "tools": ["team.publish", "team.search"],
    }
    assert initial["limits"] == {"token_limit": 2000, "cost_limit_usd": 1}
    assert "ResearchWorkflow" in initial["reserved"] and "CodeReviewWorkflow" in initial["reserved"]


def test_registered_workflow_is_enforced_like_reviewed_policy(gateway):
    client, app = gateway
    assert client.post(
        "/v1/workflow-runs", headers=auth("builder"), json={"workflow": "Triage", "input": {"ticket": "T-1"}}
    ).json()["detail"]["reason"] == "workflow_not_allowed"
    response = put(client, body={"input_schema": SCHEMA, "required_approvals": 2})
    assert response.status_code == 200, response.text
    assert response.headers["etag"] == '"1"'
    workflow = response.json()["workflow"]
    assert response.json()["revision"] == 1
    assert workflow["name"] == "Triage" and workflow["registered_by"] == "admin" and workflow["registered_at"] > 0
    assert workflow["allowed_providers"] == ["openai"] and workflow["allowed_tools"] == ["team.search"]
    assert workflow["allowed_egress"] == ["http://fake", "http://tools.test:9000"]
    policy = client.get("/v1/workflow-policies", headers=auth("viewer")).json()["workflows"]["Triage"]
    assert policy["requiredApprovals"] == 2 and policy["tokenLimit"] == 500 and policy["costLimitUsd"] == 0.5
    assert policy["inputSchema"]["required"] == ["ticket"]
    assert policy["captureContent"] == "none"
    assert "Triage" not in client.get("/v1/workflow-policies", headers=auth("other")).json()["workflows"]
    bad = client.post("/v1/workflow-runs", headers=auth("builder"), json={"workflow": "Triage", "input": {}})
    assert bad.status_code == 422 and bad.json()["detail"]["reason"] == "workflow_input_invalid"
    started = client.post(
        "/v1/workflow-runs", headers=auth("builder"), json={"workflow": "Triage", "input": {"ticket": "T-1"}}
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["run_id"]
    initialized = client.put(
        f"/v1/workflow-runs/{run_id}",
        headers={**auth("worker"), "X-Workflow-ID": started.json()["workflow_id"]},
        json={"workflow": "Triage", "token_limit": 9000, "cost_limit_usd": 9},
    )
    assert initialized.status_code == 200, initialized.text
    assert initialized.json()["token_limit"] == 500 and initialized.json()["cost_limit_usd"] == 0.5
    step = {**auth("worker"), "X-Workflow-Run-ID": run_id, "X-Workflow-Step-ID": "draft"}
    allowed = client.post(
        "/v1/chat/completions", headers=step,
        json={"model": "primary", "max_tokens": 20, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert allowed.status_code == 200, allowed.text
    denied = client.post(
        "/v1/chat/completions", headers={**step, "X-Workflow-Step-ID": "other"},
        json={"model": "backup", "max_tokens": 20, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert denied.status_code == 403 and denied.json()["detail"]["reason"] == "workflow_model_denied"
    assert app.state.runtime_client.calls == 1
    tools = client.get("/v1/tools", headers=step).json()["tools"]
    assert tools == ["team.search"]
    listed = client.get(PATH, headers=auth("admin")).json()
    assert [row["name"] for row in listed["workflows"]] == ["Triage"]
    fields = client.get("/v1/team/settings", headers=auth("admin")).json()["fields"]
    assert fields["workflows.Triage.required_approvals"] == {"value": 2, "source": "policy", "policy_default": 2}


def test_registration_can_only_narrow_the_operator_envelope(gateway):
    client, app = gateway
    cases = [
        ({"allowed_models": ["gpt-unlisted"]}, "allowed_models"),
        ({"allowed_models": []}, "allowed_models"),
        ({"allowed_tools": ["team.delete_everything"]}, "allowed_tools"),
        ({"allowed_providers": ["bedrock"]}, "allowed_providers"),
        ({"allowed_providers": ["anthropic"]}, "allowed_providers"),
        ({"allowed_providers": []}, "allowed_providers"),
        ({"cost_limit_usd": 1.01}, "cost_limit_usd"),
        ({"token_limit": 2001}, "token_limit"),
        ({"input_schema": {"type": "object", "properties": {"x": {"type": "object"}}}}, "input_schema"),
        ({"input_schema": {"type": "array"}}, "input_schema"),
        ({"input_schema": {"type": "object", "properties": {}, "padding": "x" * 21000}}, "input_schema"),
    ]
    for override, field in cases:
        response = put(client, body=override)
        assert response.status_code == 422, (override, response.text)
        detail = response.json()["detail"]
        if isinstance(detail, dict):
            assert detail["reason"] == "team_workflow_invalid"
            assert field in {item["field"] for item in detail["fields"]}
        else:  # schema-level validation by the request model
            assert field in str(detail)
    # Egress is derived from the chosen models and tools; callers cannot add origins.
    for field, value in (("allowed_egress", ["https://evil.test"]), ("required_approvals", 0), ("owner", "x")):
        assert put(client, body={field: value}).status_code == 422
    team = app.state.sandbox_policy_set.policies["team"]
    app.state.sandbox_policy_set.policies["team"] = replace(team, allowed_models=("backup",))
    narrowed = put(client, body={"allowed_models": ["primary"]})
    assert narrowed.status_code == 422 and "primary" in narrowed.json()["detail"]["message"]
    assert put(client, body={"allowed_models": ["backup"], "allowed_tools": []}).status_code == 200
    assert client.get("/v1/team/settings", headers=auth("admin")).json()["revision"] == 1


@pytest.mark.parametrize("name", ["ResearchWorkflow", "CodeReviewWorkflow", "1bad", "has-dash", "a" * 65, "with space"])
def test_reserved_and_malformed_names_are_refused(gateway, name):
    client, _ = gateway
    response = put(client, name=name.replace(" ", "%20"))
    assert response.status_code == 422
    assert response.json()["detail"]["fields"][0]["field"] == "name"


def test_revision_checks_idempotency_replacement_and_removal(gateway):
    client, _ = gateway
    assert put(client, revision=7).status_code == 409
    assert put(client, revision="oops").status_code == 422
    first = put(client)
    assert first.status_code == 200 and first.json()["revision"] == 1
    same = put(client, revision=1)
    assert same.status_code == 200 and same.json()["revision"] == 1
    assert same.json()["workflow"]["registered_at"] == first.json()["workflow"]["registered_at"]
    assert put(client, revision=0).status_code == 409
    changed = put(client, revision=1, body={"cost_limit_usd": 0.25})
    assert changed.json()["revision"] == 2 and changed.json()["workflow"]["cost_limit_usd"] == 0.25
    override = client.patch(
        "/v1/team/settings", headers={**auth("admin"), "If-Match": "2"},
        json={"fields": {"workflows.Triage.token_limit": 100}},
    )
    assert override.status_code == 200, override.text
    effective = client.get("/v1/workflow-policies", headers=auth("viewer")).json()["workflows"]["Triage"]
    assert effective["tokenLimit"] == 100 and effective["costLimitUsd"] == 0.25
    started = client.post(
        "/v1/workflow-runs", headers=auth("builder"), json={"workflow": "Triage", "input": {"ticket": "T"}}
    ).json()
    run_headers = {**auth("worker"), "X-Workflow-ID": started["workflow_id"]}
    assert client.put(
        f"/v1/workflow-runs/{started['run_id']}", headers=run_headers, json={"workflow": "Triage"}
    ).status_code == 200
    step = {**auth("worker"), "X-Workflow-Run-ID": started["run_id"], "X-Workflow-Step-ID": "draft"}
    call = {"model": "primary", "max_tokens": 20, "messages": [{"role": "user", "content": "hi"}]}
    assert client.post("/v1/chat/completions", headers=step, json=call).status_code == 200
    assert client.delete(f"{PATH}/Triage", headers={**auth("admin"), "If-Match": "2"}).status_code == 409
    removed = client.delete(f"{PATH}/Triage", headers={**auth("admin"), "If-Match": "3"})
    assert removed.status_code == 200 and removed.json() == {"revision": 4, "removed": "Triage"}
    assert client.delete(f"{PATH}/Triage", headers={**auth("admin"), "If-Match": "4"}).status_code == 404
    assert "Triage" not in client.get("/v1/workflow-policies", headers=auth("viewer")).json()["workflows"]
    denied = client.post(
        "/v1/workflow-runs", headers=auth("builder"), json={"workflow": "Triage", "input": {"ticket": "T"}}
    )
    assert denied.json()["detail"]["reason"] == "workflow_not_allowed"
    in_flight = client.post("/v1/chat/completions", headers=step, json=call)
    assert in_flight.status_code == 403 and in_flight.json()["detail"]["reason"] == "workflow_not_allowed"
    settings = client.get("/v1/team/settings", headers=auth("admin")).json()
    assert not [field for field in settings["fields"] if field.startswith("workflows.Triage.")]
    assert put(client, revision=4, body={"cost_limit_usd": 0.3}).json()["workflow"]["token_limit"] == 500
    reregistered = client.get("/v1/workflow-policies", headers=auth("viewer")).json()["workflows"]["Triage"]
    assert reregistered["tokenLimit"] == 500


def test_each_team_has_its_own_registrations_and_limit(gateway):
    client, _ = gateway
    assert put(client).status_code == 200
    assert client.get(PATH, headers=auth("other")).status_code == 403
    assert "Triage" not in client.get("/v1/workflow-policies", headers=auth("other")).json()["workflows"]
    from app.team_workflows import MAX_WORKFLOWS

    revision = 1
    for index in range(MAX_WORKFLOWS - 1):
        revision = put(client, name=f"Flow{index}", revision=revision).json()["revision"]
    overflow = put(client, name="TooMany", revision=revision)
    assert overflow.status_code == 422 and "at most" in overflow.json()["detail"]["message"]
    assert put(client, name="Triage", revision=revision, body={"cost_limit_usd": 0.4}).status_code == 200


def test_operators_can_keep_workflows_in_reviewed_policy(gateway):
    client, app = gateway
    assert put(client).status_code == 200
    team = app.state.sandbox_policy_set.policies["team"]
    app.state.sandbox_policy_set.policies["team"] = replace(team, self_service_workflows=False)
    page = client.get(PATH, headers=auth("admin")).json()
    assert page["enabled"] is False and page["workflows"] == []
    refused = put(client, name="Another", revision=1)
    assert refused.status_code == 403 and refused.json()["detail"]["reason"] == "team_workflows_disabled"
    assert client.delete(f"{PATH}/Triage", headers={**auth("admin"), "If-Match": "1"}).status_code == 403
    assert "Triage" not in client.get("/v1/workflow-policies", headers=auth("viewer")).json()["workflows"]
    assert client.post(
        "/v1/workflow-runs", headers=auth("builder"), json={"workflow": "Triage", "input": {"ticket": "T"}}
    ).status_code == 403
    app.state.sandbox_policy_set.policies["team"] = team
    assert "Triage" in client.get("/v1/workflow-policies", headers=auth("viewer")).json()["workflows"]


def test_reviewed_policy_wins_if_a_name_is_later_defined_by_the_operator(gateway):
    client, app = gateway
    assert put(client).status_code == 200
    team = app.state.sandbox_policy_set.policies["team"]
    reviewed = team.workflows["ResearchWorkflow"].model_copy(update={"token_limit": 77})
    app.state.sandbox_policy_set.policies["team"] = replace(team, workflows={**team.workflows, "Triage": reviewed})
    policy = client.get("/v1/workflow-policies", headers=auth("viewer")).json()["workflows"]["Triage"]
    assert policy["tokenLimit"] == 77
    assert client.get(PATH, headers=auth("admin")).json()["workflows"] == []


def test_registration_and_removal_are_audited_without_secrets(gateway, caplog):
    client, _ = gateway
    caplog.set_level(logging.INFO)
    assert put(client).status_code == 200
    assert put(client, revision=1).status_code == 200
    assert client.delete(f"{PATH}/Triage", headers={**auth("admin"), "If-Match": "1"}).status_code == 200
    verifier = _load_verifier()
    events = verifier.deduplicate(verifier.extract_audit_events(_double_logged_lines(caplog)))
    assert [event["event"] for event in events] == ["team_workflow_registered", "team_workflow_removed"]
    assert events[0]["workflow"] == "Triage" and events[0]["replaced"] is False
    assert events[0]["policy"]["allowedModels"] == ["primary"]
    assert "secret" not in str(events).lower()


def test_policy_file_validates_the_operator_switch(tmp_path):
    template = (
        "apiVersion: platform.ai/v1alpha1\nkind: SandboxPolicySet\nspec:\n  policies:\n"
        "    - sandboxId: team\n      selfServiceWorkflows: VALUE\n"
    )
    path = tmp_path / "policy.yaml"
    path.write_text(template.replace("VALUE", "false"))
    assert SandboxPolicySet.from_path(path).policies["team"].self_service_workflows is False
    path.write_text(template.replace("VALUE", '"yes"'))
    with pytest.raises(ValueError, match="selfServiceWorkflows"):
        SandboxPolicySet.from_path(path)
    path.write_text(template.replace("      selfServiceWorkflows: VALUE\n", ""))
    assert SandboxPolicySet.from_path(path).policies["team"] == SandboxPolicy("team")
