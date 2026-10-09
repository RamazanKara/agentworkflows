# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor

from app import invitations

from tests.test_managed_keys import auth_gateway  # noqa: F401
from tests.test_teams import auth


def invite(client, **fields):
    response = client.post(
        "/v1/team/invitations", headers=auth("admin"), json={"name": "Maya", "role": "approver", **fields}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_invitation_is_single_use_scoped_and_never_lists_secrets(auth_gateway, monkeypatch, caplog):
    client, _, store, _ = auth_gateway
    monkeypatch.setattr(invitations, "time", lambda: store.now)
    value = invite(client, project="private")
    assert value["token"] not in str(store.data) + caplog.text
    listing = client.get("/v1/team/invitations", headers=auth("admin")).json()
    assert listing == [{k: v for k, v in value.items() if k != "token"}]
    assert client.get("/v1/team/invitations", headers=auth("other")).json() == []
    assert client.get("/v1/team/invitations", headers=auth("viewer")).status_code == 403
    assert (
        client.post(
            "/v1/auth/invitations/accept", headers={"Origin": "https://attacker.test"}, json={"token": value["token"]}
        ).status_code
        == 403
    )
    accepted = client.post("/v1/auth/invitations/accept", json={"token": value["token"]})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["key"] not in str(store.data) + caplog.text
    access = {"Authorization": "Bearer " + accepted.json()["key"]}
    team = client.get("/v1/team", headers=access).json()
    assert team["role"] == "approver" and team["projects"] == ["private"]
    assert client.post("/v1/auth/invitations/accept", json={"token": value["token"]}).status_code == 401
    assert client.delete("/v1/team/invitations/" + value["invitation_id"], headers=auth("other")).status_code == 404
    assert client.delete("/v1/team/invitations/" + value["invitation_id"], headers=auth("admin")).status_code == 200
    assert client.get("/v1/team", headers=access).status_code == 401


def test_invitation_expiry_revocation_and_project_boundary(auth_gateway, monkeypatch):
    client, _, store, _ = auth_gateway
    monkeypatch.setattr(invitations, "time", lambda: store.now)
    assert client.post("/v1/team/invitations", headers=auth("project"), json={"name": "Unscoped"}).status_code == 403
    assert (
        client.post(
            "/v1/team/invitations", headers=auth("builder"), json={"name": "Admin", "role": "admin"}
        ).status_code
        == 403
    )
    value = invite(client)
    store.now += 86401
    assert client.post("/v1/auth/invitations/accept", json={"token": value["token"]}).status_code == 401
    fresh = invite(client)
    client.delete("/v1/team/invitations/" + fresh["invitation_id"], headers=auth("admin"))
    assert client.post("/v1/auth/invitations/accept", json={"token": fresh["token"]}).status_code == 401
    assert client.post("/v1/auth/invitations/accept", json={"token": "bad"}).status_code == 401


def test_only_one_concurrent_redeemer_receives_a_credential(auth_gateway):
    client, _, _, _ = auth_gateway
    value = invite(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: client.post("/v1/auth/invitations/accept", json={"token": value["token"]}), range(2))
        )
    assert sorted(response.status_code for response in results) == [200, 401]
