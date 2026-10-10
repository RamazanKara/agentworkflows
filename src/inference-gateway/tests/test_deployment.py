# ruff: noqa: F811
from dataclasses import replace

from cryptography.fernet import Fernet

from tests.test_teams import auth, team_gateway  # noqa: F401


def test_deployment_configuration_is_admin_only_and_never_returns_secrets(team_gateway):
    client, app = team_gateway
    path = "/v1/team/deployment"
    assert client.get(path, headers=auth("viewer")).status_code == 403
    assert client.get(path, headers=auth("builder")).status_code == 403
    response = client.get(path, headers=auth("admin"))
    assert response.status_code == 200, response.text
    checks = {item["id"]: item for item in response.json()["checks"]}
    assert checks["authentication"]["configured"]
    assert not checks["records"]["configured"]
    assert not checks["encryption"]["configured"]
    app.state.settings = replace(
        app.state.settings,
        storage_backend="postgres",
        storage_postgres_dsn="postgresql://user:private-password@db/records",
        workflow_secrets_key=Fernet.generate_key().decode(),
        session_cookie_secure=True,
    )
    response = client.get(path, headers=auth("admin"))
    checks = {item["id"]: item for item in response.json()["checks"]}
    assert checks["records"]["configured"] and checks["encryption"]["configured"]
    assert checks["cookies"]["configured"]
    assert any("agentworkflows check" in item for item in response.json()["verification_required"])
    assert "private-" not in response.text and "postgresql://" not in response.text
