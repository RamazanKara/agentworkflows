import hashlib
import json
import logging
import re
from dataclasses import replace
from time import time
from unittest.mock import AsyncMock

import pytest
from app import managed_keys, sessions
from app.budget import RedisSandboxBudgetTracker
from app.main import create_app
from app.policy import SandboxPolicy, SandboxPolicySet
from app.state_migrations import SCHEMA_VERSION, migrate
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError

from tests.gateway_support import _tool_settings
from tests.test_teams import TeamRedis, auth


class AuthRedis(TeamRedis):
    def __init__(self):
        super().__init__()
        self.now = float(int(time()))
        self.expires = {}

    def get(self, key):
        if self.expires.get(key, float("inf")) <= self.now:
            self.data.pop(key, None)
        return self.data.get(key)

    def setex(self, key, seconds, value):
        self.data[key] = value
        self.expires[key] = self.now + seconds

    def getdel(self, key):
        value = self.get(key)
        self.delete(key)
        return value

    def smembers(self, key):
        return self.data.get(key, set())

    def hmget(self, key, fields):
        return [self.data.get(key, {}).get(field) for field in fields]

    def eval(self, script, numkeys, *values):
        keys, args = values[:numkeys], values[numkeys:]
        if script == managed_keys.CREATE:
            key_id, digest, raw = args
            self.data.setdefault(keys[0], {})[key_id] = raw
            self.data.setdefault(keys[1], {})[digest] = key_id
            self.data.setdefault(keys[2], set()).add(key_id)
            return raw
        if script == managed_keys.LOOKUP:
            digest, now = args
            key_id = self.data.get(keys[1], {}).get(digest)
            raw = self.data.get(keys[0], {}).get(key_id)
            if not raw:
                return None
            record = json.loads(raw)
            if (
                record["revoked_at"] is None
                and (record["expires_at"] is None or record["expires_at"] > now)
                and (record["last_used_at"] is None or now - record["last_used_at"] >= 60)
            ):
                record["last_used_at"] = now
                raw = json.dumps(record)
                self.data[keys[0]][key_id] = raw
            return raw
        if script == managed_keys.CHANGE:
            key_id, team, project, changes = args
            raw = self.data.get(keys[0], {}).get(key_id)
            if not raw:
                return None
            record = json.loads(raw)
            if record["team"] != team or (project and record["project"] != project):
                return None
            if record["revoked_at"] is not None:
                return "revoked"
            record.update(json.loads(changes))
            self.data[keys[0]][key_id] = json.dumps(record)
            return json.dumps(record)
        if script == sessions.TOUCH:
            raw = self.get(keys[0])
            if not raw:
                return None
            remaining = int(json.loads(raw)["absolute_expires_at"] - args[0])
            if remaining <= 0:
                self.delete(keys[0])
                return None
            self.expires[keys[0]] = self.now + min(remaining, args[1])
            return raw
        return super().eval(script, numkeys, *values)


@pytest.fixture
def auth_gateway(tmp_path, monkeypatch):
    records = [
        {"name": role, "sha256": hashlib.sha256(role.encode()).hexdigest(), "sandbox": "team", "role": role}
        for role in ("admin", "builder", "approver", "viewer")
    ]
    records.extend([
        {"name": "other", "sha256": hashlib.sha256(b"other").hexdigest(), "sandbox": "other", "role": "admin"},
        {
            "name": "project", "sha256": hashlib.sha256(b"project").hexdigest(),
            "sandbox": "team", "role": "admin", "project": "private",
        },
    ])
    path = tmp_path / "keys.json"
    path.write_text(json.dumps(records))
    settings = _tool_settings(
        api_key_auth_enabled=True, api_key_records_path=path, sandbox_budget_backend="redis",
        sandbox_budget_enabled=True, audit_log_enabled=True, admin_console_enabled=True,
    )
    store = AuthRedis()
    monkeypatch.setattr(sessions, "time", lambda: store.now)
    monkeypatch.setattr(managed_keys, "time", lambda: store.now)
    monkeypatch.setattr("app.request_context.time", lambda: store.now)

    def build(**overrides):
        config = replace(settings, **overrides)
        app = create_app(config)
        app.state.budget_tracker = RedisSandboxBudgetTracker(config, client=store)
        app.state.sandbox_policy_set = SandboxPolicySet({
            "team": SandboxPolicy("team", projects=("default", "private")),
            "other": SandboxPolicy("other", projects=("default",)),
        })
        return TestClient(app, base_url="https://localhost"), app

    first, app = build()
    return first, app, store, build


def create_key(client, **fields):
    response = client.post("/v1/team/keys", headers=auth("admin"), json={"name": "Alice", **fields})
    assert response.status_code == 201, response.text
    return response.json()


def test_store_hashes_only_and_audits_changes(auth_gateway, caplog):
    client, app, store, _ = auth_gateway
    caplog.set_level(logging.INFO, logger="agentworkflows.audit")
    key = create_key(client, role="builder", project="private")
    assert re.fullmatch(r"aw_[A-Za-z0-9_-]{40}", key["key"])
    assert key["created_by"] == "admin" and key["team"] == "team"
    assert key["key"] not in repr(store.data)
    digest = hashlib.sha256(key["key"].encode()).hexdigest()
    assert digest in repr(store.data)
    listing = client.get("/v1/team/keys", headers=auth("admin"))
    assert listing.json()["keys"] == [{k: v for k, v in key.items() if k != "key"}]
    assert digest not in listing.text
    path = f"/v1/team/keys/{key['key_id']}"
    response = client.patch(
        path, headers=auth("admin"), json={"name": "Alice CI", "role": "viewer", "expires_at": None}
    )
    assert response.status_code == 200
    assert client.delete(path, headers=auth("admin")).status_code == 200
    events = [json.loads(record.message) for record in caplog.records if '"event": "team_key"' in record.message]
    assert [event["action_type"] for event in events] == ["create", "update", "revoke"]
    assert events[1]["prev_hash"] == events[0]["record_hash"]
    assert events[2]["prev_hash"] == events[1]["record_hash"]
    assert key["key"] not in caplog.text and digest not in caplog.text
    assert app.state.audit_chain_count >= 3


def test_revocation_and_role_changes_apply_on_both_replicas_and_sessions(auth_gateway):
    client, _, _, build = auth_gateway
    replica, _ = build()
    key = create_key(client, role="admin")
    path = f"/v1/team/keys/{key['key_id']}"
    assert replica.get("/v1/team", headers=auth(key["key"])).json()["role"] == "admin"
    assert replica.post("/v1/auth/session", json={"key": key["key"]}).status_code == 200
    assert client.patch(path, headers=auth("admin"), json={"role": "viewer"}).status_code == 200
    assert replica.get("/v1/team").json()["role"] == "viewer"
    assert replica.get("/v1/team/keys").status_code == 403
    assert client.delete(path, headers=auth("admin")).status_code == 200
    for target in (client, replica):
        assert target.get("/v1/team", headers=auth(key["key"])).status_code == 401
    assert replica.get("/v1/auth/session").status_code == 401
    assert client.patch(path, headers=auth("admin"), json={"role": "admin"}).status_code == 409


def test_expiry_and_last_use_are_shared_and_throttled(auth_gateway):
    client, _, store, build = auth_gateway
    replica, _ = build()
    key = create_key(client, expires_at=store.now + 120)
    assert client.get("/v1/team", headers=auth(key["key"])).status_code == 200
    first_used = client.get("/v1/team/keys", headers=auth("admin")).json()["keys"][0]["last_used_at"]
    store.now += 59
    assert replica.get("/v1/team", headers=auth(key["key"])).status_code == 200
    assert client.get("/v1/team/keys", headers=auth("admin")).json()["keys"][0]["last_used_at"] == first_used
    store.now += 1
    assert replica.get("/v1/team", headers=auth(key["key"])).status_code == 200
    assert client.get("/v1/team/keys", headers=auth("admin")).json()["keys"][0]["last_used_at"] == store.now
    store.now += 60
    assert replica.get("/v1/team", headers=auth(key["key"])).json()["detail"]["reason"] == "api_key_expired"


@pytest.mark.parametrize("role", ["builder", "approver", "viewer"])
@pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
def test_key_endpoints_are_admin_only(auth_gateway, role, method):
    client, _, _, _ = auth_gateway
    key = create_key(client)
    path = "/v1/team/keys" + (f"/{key['key_id']}" if method in {"patch", "delete"} else "")
    kwargs = {"json": {"name": "Denied"}} if method in {"post", "patch"} else {}
    assert client.request(method.upper(), path, headers=auth(role), **kwargs).status_code == 403


def test_team_and_project_isolation_and_self_protection(auth_gateway):
    client, _, _, _ = auth_gateway
    key = create_key(client, role="admin")
    path = f"/v1/team/keys/{key['key_id']}"
    assert client.get("/v1/team/keys", headers=auth("other")).json() == {"keys": []}
    assert client.delete(path, headers=auth("other")).status_code == 404
    assert client.patch(path, headers=auth("other"), json={"name": "stolen"}).status_code == 404
    assert client.get("/v1/team", headers={**auth(key["key"]), "X-Sandbox-ID": "other"}).status_code == 403
    assert client.delete(path, headers=auth(key["key"])).status_code == 409
    assert client.patch(path, headers=auth(key["key"]), json={"role": "viewer"}).status_code == 409
    assert client.patch(path, headers=auth(key["key"]), json={"name": "Renamed"}).status_code == 200
    assert client.get("/v1/team/keys", headers=auth("project")).json() == {"keys": []}
    assert client.delete(path, headers=auth("project")).status_code == 404
    for project in (None, "default", "missing"):
        response = client.post("/v1/team/keys", headers=auth("project"), json={"name": "No", "project": project})
        assert response.status_code in {403, 404}
    response = client.post("/v1/team/keys", headers=auth("project"), json={"name": "Yes", "project": "private"})
    assert response.status_code == 201


@pytest.mark.parametrize("body", [
    {"role": None}, {"name": None}, {"name": " "}, {"role": "owner"},
    {"expires_at": "bad"}, {"team": "other"}, {"sha256": "a" * 64},
])
def test_update_validates_mutable_fields(auth_gateway, body):
    client, _, _, _ = auth_gateway
    key = create_key(client)
    assert client.patch(f"/v1/team/keys/{key['key_id']}", headers=auth("admin"), json=body).status_code == 422


def test_auth_order_preserves_flat_file_managed_and_jwt(auth_gateway, monkeypatch):
    _, _, _, build = auth_gateway
    client, app = build(api_key_sha256s=(hashlib.sha256(b"flat").hexdigest(),), jwt_auth_enabled=True,
                        jwt_jwks_url="https://issuer/keys", jwt_tenant_claim="team")
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(managed_keys, "lookup_key", lookup)
    verifier = AsyncMock(return_value={"sub": "alice", "team": "team", "role": "viewer", "exp": time() + 300})
    app.state.jwt_verifier.verify = verifier
    assert client.get("/v1/models", headers=auth("flat")).status_code == 200
    assert client.get("/v1/team", headers=auth("admin")).json()["role"] == "admin"
    lookup.assert_not_called()
    verifier.assert_not_called()
    assert client.get("/v1/team", headers=auth("a.b.c")).json()["role"] == "viewer"
    lookup.assert_awaited_once()
    verifier.assert_awaited_once_with("a.b.c")
    assert client.get("/v1/team", headers={"X-API-Key": "a.b.c"}).status_code == 401


def test_store_failure_is_closed_but_bootstrap_keys_still_work(auth_gateway, monkeypatch):
    client, _, store, _ = auth_gateway
    monkeypatch.setattr(store, "eval", lambda *args: (_ for _ in ()).throw(ConnectionError()))
    assert client.get("/v1/team", headers=auth("aw_unknown")).status_code == 503
    assert client.get("/v1/team", headers=auth("admin")).status_code == 200


def test_managed_keys_precede_jwt_and_work_without_the_console(auth_gateway):
    _, _, _, build = auth_gateway
    client, app = build(admin_console_enabled=False, jwt_auth_enabled=True, jwt_jwks_url="https://issuer/keys")
    app.state.jwt_verifier.verify = AsyncMock(side_effect=AssertionError("JWT should not be tried"))
    key = create_key(client, role="builder", project="private")
    team = client.get("/v1/team", headers={"X-API-Key": key["key"]})
    assert team.status_code == 200
    assert team.json()["role"] == "builder" and team.json()["projects"] == ["private"]
    app.state.jwt_verifier.verify.assert_not_called()
    assert client.post("/v1/auth/session", json={"key": key["key"]}).status_code == 404


def test_additive_migration_retains_existing_state(auth_gateway):
    _, app, store, _ = auth_gateway
    prefix = app.state.settings.sandbox_budget_key_prefix
    store.data[f"{prefix}:team"] = {"tokens": 17}
    store.data[f"{prefix}:schema-version"] = "1"
    migrate(store, prefix)
    migrate(store, prefix)
    assert store.data[f"{prefix}:schema-version"] == str(SCHEMA_VERSION)
    assert store.data[f"{prefix}:team"] == {"tokens": 17}
    store.data[f"{prefix}:schema-version"] = "999"
    with pytest.raises(RuntimeError, match="Unsupported"):
        migrate(store, prefix)
