# ruff: noqa: F811  (shared gateway fixtures are imported, then requested by name)
from pathlib import Path

import pytest
from app.policy import SandboxPolicySet, WorkflowPolicy
from app.workflow_schema import InputSchema
from pydantic import ValidationError

from tests.test_teams import auth, start, team_gateway  # noqa: F401

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "description": "Example workflow",
    "properties": {
        "name": {"type": "string", "description": "Name", "default": "Ada", "examples": ["Grace"]},
        "count": {"type": "integer"},
        "price": {"type": "number"},
        "enabled": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "mode": {"type": "string", "enum": ["fast", "thorough"]},
    },
    "required": ["name"],
    "additionalProperties": False,
}


@pytest.mark.parametrize(("value", "field"), [
    ([], "input"), ({}, "input.name"), ({"name": 12}, "input.name"),
    ({"name": "ok", "count": True}, "input.count"), ({"name": "ok", "count": 1.5}, "input.count"),
    ({"name": "ok", "price": "1"}, "input.price"), ({"name": "ok", "price": False}, "input.price"),
    ({"name": "ok", "enabled": 1}, "input.enabled"), ({"name": "ok", "tags": ["a", 1]}, "input.tags"),
    ({"name": "ok", "tags": "a"}, "input.tags"), ({"name": "ok", "mode": "other"}, "input.mode"),
    ({"name": "ok", "extra": 1}, "input.extra"), ({"name": None}, "input.name"),
])
def test_schema_validation_has_field_errors_before_any_start(team_gateway, value, field):
    client, app = team_gateway
    policy = app.state.sandbox_policy_set.policies["team"].workflows["ResearchWorkflow"]
    app.state.sandbox_policy_set.policies["team"].workflows["FormWorkflow"] = WorkflowPolicy.model_validate({
        **policy.model_dump(by_alias=True), "inputSchema": SCHEMA,
    })
    response = start(client, workflow="FormWorkflow", input=value)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "workflow_input_invalid"
    fields = body["detail"]["fields"]
    assert len(fields) == 1 and fields[0]["field"] == field
    assert set(fields[0]) == {"field", "message"} and isinstance(fields[0]["message"], str)
    assert field in body["error"]["message"]
    assert not app.state.temporal_client.executions
    assert not any(":start:" in key for key in app.state.budget_tracker.client.data)


def test_all_supported_types_and_discovery_preserve_schema(team_gateway):
    client, app = team_gateway
    old = app.state.sandbox_policy_set.policies["team"].workflows["ResearchWorkflow"]
    app.state.sandbox_policy_set.policies["team"].workflows["ResearchWorkflow"] = WorkflowPolicy.model_validate({
        **old.model_dump(by_alias=True), "inputSchema": SCHEMA,
    })
    discovered = client.get("/v1/workflow-policies", headers=auth("viewer")).json()
    assert discovered["workflows"]["ResearchWorkflow"]["inputSchema"] == SCHEMA
    value = {"name": "", "count": 1.0, "price": 1.5, "enabled": False, "tags": ["a", "b"], "mode": "fast"}
    assert start(client, input=value).status_code == 201
    errors = InputSchema.model_validate(SCHEMA).errors({"count": "1", "mode": "invalid"})
    assert errors == [
        {"field": "input.name", "message": "Field is required."},
        {"field": "input.count", "message": "Must be an integer."},
        {"field": "input.mode", "message": "Must be one of the declared enum values."},
    ]


def test_no_schema_keeps_arbitrary_workflow_inputs(team_gateway):
    client, app = team_gateway
    old = app.state.sandbox_policy_set.policies["team"].workflows["ResearchWorkflow"]
    app.state.sandbox_policy_set.policies["team"].workflows["ResearchWorkflow"] = old.model_copy(
        update={"input_schema": None}
    )
    assert start(client, input="legacy string").status_code == 201


@pytest.mark.parametrize("changes", [
    {"type": "array"}, {"required": ["missing"]}, {"required": ["name", "name"]},
    {"properties": {"x": {"type": "object"}}},
    {"properties": {"x": {"type": "array", "items": {"type": "number"}}}},
    {"properties": {"x": {"type": "array"}}},
    {"properties": {"x": {"type": "string", "default": None}}},
    {"properties": {"x": {"type": "integer", "enum": [True]}}},
    {"properties": {"x": {"type": "string", "enum": ["yes"], "default": "no"}}},
    {"properties": {"x": {"type": "boolean", "examples": ["yes"]}}},
])
def test_policy_rejects_unsupported_or_inconsistent_schemas(changes):
    with pytest.raises(ValidationError):
        InputSchema.model_validate({**SCHEMA, **changes})


def test_compose_templates_declare_schemas():
    path = Path(__file__).resolve().parents[3] / "deploy/compose/sandbox-policy.yaml"
    team = SandboxPolicySet.from_path(path).policies["demo"]
    assert team.capture_content == "redacted"
    for name, field in [
        ("ResearchWorkflow", "topic"), ("CodeReviewWorkflow", "diff"), ("SupportTriageWorkflow", "ticket"),
        ("WeeklyReportWorkflow", "period"), ("IncidentSummaryWorkflow", "incident_id"),
        ("DocumentQAWorkflow", "question"),
    ]:
        schema = team.workflows[name].input_schema
        assert schema is not None
        assert schema.errors({field: "example"}) == []
        assert schema.errors({})[0]["field"] == f"input.{field}"
