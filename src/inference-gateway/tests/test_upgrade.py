import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
import redis
import yaml
from app.settings import Settings
from app.state_migrations import SCHEMA_VERSION, migrate

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = sorted(Path(__file__).with_name("fixtures").joinpath("upgrade").glob("*.json"))


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda path: path.stem)
def test_previous_config_loads_without_changing_retained_state_settings(fixture, monkeypatch):
    previous = json.loads(fixture.read_text(encoding="utf-8"))
    contract = json.loads((ROOT / "platform/config-contracts/inference-gateway.config.json").read_text())
    for row in contract["environment"]:
        for name in (row["name"], *row["aliases"]):
            monkeypatch.delenv(name, raising=False)
    for name, row in previous["environment"].items():
        value = row["value"]
        monkeypatch.setenv(name, str(value).lower() if isinstance(value, bool) else str(value))
    settings = Settings.from_env()
    for row in previous["environment"].values():
        assert getattr(settings, row["field"]) == row["value"]


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda path: path.stem)
@pytest.mark.parametrize("chart,field", [("inference-gateway", "gateway_values"), ("workflows", "worker_values")])
def test_previous_helm_values_keep_gateway_and_worker_connections(fixture, chart, field, tmp_path):
    helm = shutil.which("helm")
    if not helm:
        pytest.skip("Install Helm to test historical values against current charts")
    previous = json.loads(fixture.read_text(encoding="utf-8"))[field]
    values = tmp_path / "values.json"
    values.write_text(json.dumps(previous), encoding="utf-8")
    rendered = subprocess.run(
        [helm, "template", "upgrade", str(ROOT / "deploy/charts" / chart), "-f", str(values)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    deployments = [item for item in yaml.safe_load_all(rendered.stdout) if item and item["kind"] == "Deployment"]
    name = "worker" if chart == "workflows" else "gateway"
    container = next(
        container
        for item in deployments
        for container in item["spec"]["template"]["spec"]["containers"]
        if container["name"] == name
    )
    env = {row["name"]: row for row in container["env"]}
    if chart == "workflows":
        assert env["AGENTWORKFLOWS_TEAM"]["value"] == previous["worker"]["team"]
        assert env["TEMPORAL_TASK_QUEUE"]["value"] == previous["worker"]["team"] + "-workflows"
        assert env["AGENTWORKFLOWS_URL"]["value"] == previous["worker"]["gatewayUrl"]
        secret = env["AGENTWORKFLOWS_API_KEY"]["valueFrom"]["secretKeyRef"]
        assert secret["name"] == previous["worker"]["existingSecret"]
    else:
        assert env["RUNTIME_MAX_RETRIES"]["value"] == str(previous["runtime"]["maxRetries"])
        assert env["REQUEST_TIMEOUT_SECONDS"]["value"] == str(previous["runtime"]["requestTimeoutSeconds"])
        assert env["SANDBOX_BUDGET_BACKEND"]["value"] == previous["budget"]["backend"]
        assert env["SANDBOX_BUDGET_REDIS_URL"]["value"] == previous["budget"]["redisUrl"]
        assert env["STORAGE_BACKEND"]["value"] == previous.get("storage", {}).get("backend", "redis")


@pytest.fixture
def redis_state():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL to exercise real Lua migrations in an isolated key prefix")
    client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=1, socket_connect_timeout=1)
    prefix = "aw-upgrade-" + uuid4().hex
    client.set(prefix + ":retained", "run-receipt-budget", ex=300)
    try:
        yield client, prefix
    finally:
        client.delete(prefix + ":retained", prefix + ":schema-version")
        client.close()


@pytest.mark.parametrize("version", [None, "0", "1", "2"])
def test_real_redis_upgrade_preserves_records_and_ttl_and_rejects_future(redis_state, version):
    client, prefix = redis_state
    key = prefix + ":schema-version"
    if version is not None:
        client.set(key, version)
    migrate(client, prefix)
    migrate(client, prefix)
    assert client.get(key) == str(SCHEMA_VERSION)
    assert client.get(prefix + ":retained") == "run-receipt-budget"
    assert 0 < client.ttl(prefix + ":retained") <= 300
    client.set(key, str(SCHEMA_VERSION + 1))
    with pytest.raises(RuntimeError, match="Unsupported"):
        migrate(client, prefix)
    assert client.get(key) == str(SCHEMA_VERSION + 1)


def test_real_redis_migration_resumes_after_committed_reply_is_lost(redis_state, monkeypatch):
    client, prefix = redis_state
    original = client.eval

    def lost_reply(*args):
        original(*args)
        raise redis.TimeoutError("Migration committed; reply lost")

    monkeypatch.setattr(client, "eval", lost_reply)
    with pytest.raises(redis.TimeoutError):
        migrate(client, prefix)
    assert client.get(prefix + ":schema-version") == "1"
    monkeypatch.setattr(client, "eval", original)
    migrate(client, prefix)
    assert client.get(prefix + ":schema-version") == str(SCHEMA_VERSION)
    assert client.get(prefix + ":retained") == "run-receipt-budget"
    assert 0 < client.ttl(prefix + ":retained") <= 300
