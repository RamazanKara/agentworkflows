import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("release_evidence", ROOT / "scripts/evidence-pack.py")
assert SPEC is not None and SPEC.loader is not None
evidence = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = evidence
SPEC.loader.exec_module(evidence)

OVERLAY_SPEC = importlib.util.spec_from_file_location(
    "customer_overlay", ROOT / "scripts/configure-customer-overlay.py"
)
assert OVERLAY_SPEC is not None and OVERLAY_SPEC.loader is not None
overlay = importlib.util.module_from_spec(OVERLAY_SPEC)
OVERLAY_SPEC.loader.exec_module(overlay)


def test_current_release_documents_cloud_and_self_hosted_deployments():
    controls = {item.area: item for item in evidence.static_controls()}
    assert controls["Governed cloud and optional self-hosted deployments"].status == "pass"
    assert controls["API contract governance"].status == "pass"


@pytest.mark.parametrize("readme", ["cloud providers only", "self-hosted only", "local customer-owned clusters"])
def test_deployment_guidance_requires_both_cloud_and_self_hosted(monkeypatch, readme):
    original = evidence.read_text
    monkeypatch.setattr(evidence, "read_text", lambda path: readme if path == "README.md" else original(path))
    assert evidence.static_controls()[0].status == "fail"


@pytest.fixture
def customer_overlay(tmp_path, monkeypatch):
    for name in ("ROOT_APP", "CUSTOMER_APPS", "APPPROJECTS"):
        source = getattr(overlay, name)
        target = tmp_path / source.name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.setattr(overlay, name, target)
    return overlay


def test_customer_overlay_keeps_the_reviewed_temporal_repository(customer_overlay):
    assert customer_overlay.check_overlay() == []
    customer_overlay.configure_overlay("https://github.com/example/team.git", "v0.2.0", "nvidia", False)
    assert customer_overlay.check_overlay() == []
    project = customer_overlay.load_yaml_documents(customer_overlay.APPPROJECTS)[0]
    assert project["spec"]["sourceRepos"] == [
        "https://github.com/example/team.git",
        "https://go.temporal.io/helm-charts/",
    ]


@pytest.mark.parametrize("repositories", [["*"], ["https://github.com/RamazanKara/agentworkflows.git"]])
def test_customer_overlay_rejects_incomplete_or_unrestricted_sources(customer_overlay, repositories):
    projects = customer_overlay.load_yaml_documents(customer_overlay.APPPROJECTS)
    projects[0]["spec"]["sourceRepos"] = repositories
    customer_overlay.write_yaml_documents(customer_overlay.APPPROJECTS, projects)
    assert any("sourceRepos" in error for error in customer_overlay.check_overlay())
