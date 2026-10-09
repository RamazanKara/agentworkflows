"""Single-use invitations exchange an opaque link for one scoped managed credential."""

import base64
import hashlib
import json
import secrets
from time import time
from typing import Annotated, Any
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, SecretStr

from app.managed_keys import CreatedKey, KeyCreate, change_key, issue_key, key_receipt, public_record
from app.sessions import check_origin
from app.settings import validate_sandbox_id
from app.storage import storage_call
from app.team_data import enter_team_request, leave_team_request, scan_keys
from app.teams import require_role
from app.workflow_budget import redis_call

ACCEPT = """
local secret = redis.call('GET', KEYS[1])
local raw = redis.call('GET', KEYS[2])
if not secret or not raw then return nil end
local invite = cjson.decode(raw)
if invite.accepted_at ~= cjson.null or invite.revoked_at ~= cjson.null
   or invite.expires_at <= tonumber(ARGV[1]) then return nil end
invite.accepted_at = tonumber(ARGV[1])
redis.call('SET', KEYS[2], cjson.encode(invite), 'KEEPTTL')
redis.call('DEL', KEYS[1])
return secret
"""


class Invitation(BaseModel):
    invitation_id: str
    key_id: str
    name: str
    role: str
    project: str | None
    created_at: float
    expires_at: float
    accepted_at: float | None
    revoked_at: float | None


class CreatedInvitation(Invitation):
    token: str


class InvitationAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr


def invitation_cipher(token: str) -> Fernet:
    # The lookup digest cannot derive the encryption key from a Redis dump.
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"invitation encryption\0" + token.encode()).digest()))


def invitation_key(request: Request, invitation_id: str, kind: str = "metadata") -> str:
    prefix = request.app.state.settings.sandbox_budget_key_prefix
    return f"{prefix}:{request.state.sandbox_id}:invitation-{kind}:{invitation_id}"


def public_invitation(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "digest"}


def register_invitation_routes(app: FastAPI) -> None:
    @app.get("/v1/team/invitations", tags=["teams"], response_model=list[Invitation])
    async def list_invitations(request: Request) -> list[dict[str, Any]]:
        principal = require_role(request, "admin")
        rows = []
        for key in await scan_keys(request, invitation_key(request, "*")):
            raw = await redis_call(request, "get", key)
            if raw:
                row = json.loads(raw)
                if not principal.get("project") or principal["project"] == row["project"]:
                    rows.append(public_invitation(row))
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)

    @app.post(
        "/v1/team/invitations",
        tags=["teams"],
        status_code=201,
        response_model=CreatedInvitation,
        summary="Create a one-day invitation; share its token through a trusted channel",
    )
    async def create_invitation(request: Request, body: KeyCreate) -> dict[str, Any]:
        key = await issue_key(request, body)
        invitation_id = uuid4().hex
        token = f"{request.state.sandbox_id}.{invitation_id}.{secrets.token_urlsafe(32)}"
        record = {
            "invitation_id": invitation_id,
            "key_id": key["key_id"],
            "name": key["name"],
            "role": key["role"],
            "project": key["project"],
            "created_at": time(),
            "expires_at": min(time() + 86400, key["expires_at"] or time() + 86400),
            "accepted_at": None,
            "revoked_at": None,
            "digest": hashlib.sha256(token.encode()).hexdigest(),
        }
        if record["expires_at"] <= time():
            await change_key(request, key["key_id"], {"revoked_at": time()}, "revoke")
            raise HTTPException(422, detail="Invitation access must expire in the future.")
        try:
            await redis_call(request, "setex", invitation_key(request, invitation_id), 30 * 86400, json.dumps(record))
            await redis_call(
                request,
                "setex",
                invitation_key(request, record["digest"], "token"),
                max(1, int(record["expires_at"] - time())),
                invitation_cipher(token).encrypt(key["key"].encode()).decode(),
            )
        except HTTPException:
            await change_key(request, key["key_id"], {"revoked_at": time()}, "revoke")
            raise
        key_receipt(request, "invitation_created", {k: v for k, v in key.items() if k != "key"})
        return {**public_invitation(record), "token": token}

    @app.delete("/v1/team/invitations/{invitation_id}", tags=["teams"], response_model=Invitation)
    async def revoke_invitation(
        request: Request, invitation_id: Annotated[str, Path(pattern="^[0-9a-f]{32}$")]
    ) -> dict[str, Any]:
        require_role(request, "admin")
        raw = await redis_call(request, "get", invitation_key(request, invitation_id))
        if not raw:
            raise HTTPException(404, detail="Invitation not found in your team.")
        record = json.loads(raw)
        await change_key(request, record["key_id"], {"revoked_at": time()}, "invitation_revoked")
        record["revoked_at"] = time()
        await redis_call(request, "delete", invitation_key(request, record["digest"], "token"))
        await redis_call(request, "setex", invitation_key(request, invitation_id), 30 * 86400, json.dumps(record))
        return public_invitation(record)

    @app.post(
        "/v1/auth/invitations/accept",
        tags=["auth"],
        response_model=CreatedKey,
        summary="Exchange a single-use invitation for its scoped API key (returned once)",
    )
    async def accept_invitation(request: Request, body: InvitationAccept) -> dict[str, Any]:
        check_origin(request)
        token = body.token.get_secret_value()
        try:
            team, invitation_id, nonce = token.rsplit(".", 2)
            validate_sandbox_id(team)
            if len(invitation_id) != 32 or any(c not in "0123456789abcdef" for c in invitation_id) or len(nonce) != 43:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(401, detail="Invitation expired, revoked, or already used.") from exc
        if team not in request.app.state.sandbox_policy_set.policies:
            raise HTTPException(401, detail="Invitation access is no longer valid.")
        request.state.sandbox_id = team
        digest = hashlib.sha256(token.encode()).hexdigest()
        path = invitation_key(request, digest, "token")
        encrypted = await redis_call(request, "get", path)
        if not encrypted:
            raise HTTPException(401, detail="Invitation expired, revoked, or already used.")
        try:
            key = invitation_cipher(token).decrypt(encrypted.encode()).decode()
        except InvalidToken as exc:
            raise HTTPException(401, detail="Invalid invitation.") from exc
        record = await storage_call(request, "lookup_key", hashlib.sha256(key.encode()).hexdigest(), time())
        if (
            not record
            or record["team"] != team
            or record["revoked_at"] is not None
            or (record["expires_at"] is not None and record["expires_at"] <= time())
        ):
            raise HTTPException(401, detail="Invitation access is no longer valid.")
        request.state.principal = {
            "key_id": record["key_id"],
            "role": record["role"],
            "name": record["name"],
            "project": record["project"],
        }
        request.state.sandbox_bound = True
        entered = await enter_team_request(request)
        try:
            taken = await redis_call(request, "eval", ACCEPT, 2, path, invitation_key(request, invitation_id), time())
            if not taken:
                raise HTTPException(401, detail="Invitation expired, revoked, or already used.")
            key_receipt(request, "invitation_accepted", record)
            return {**public_record(record), "key": key}
        finally:
            if entered:
                await leave_team_request(request)
