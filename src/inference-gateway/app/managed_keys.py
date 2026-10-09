"""Team-managed credential metadata; only digests are persisted."""

import hashlib
import secrets
from time import time
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.audit import chain_audit_event, emit_audit_record
from app.key_records import KeyRecord
from app.settings import validate_sandbox_id
from app.storage import storage_call
from app.teams import project_access, require_role

Role = Literal["admin", "builder", "approver", "viewer"]
Name = Annotated[str, Field(min_length=1, max_length=128, pattern=r"\S")]

CREATE = """
redis.call('HSET', KEYS[1], ARGV[1], ARGV[3])
redis.call('HSET', KEYS[2], ARGV[2], ARGV[1])
redis.call('SADD', KEYS[3], ARGV[1])
return ARGV[3]
"""

LOOKUP = """
local id = redis.call('HGET', KEYS[2], ARGV[1])
if not id then return nil end
local raw = redis.call('HGET', KEYS[1], id)
if not raw then return nil end
local record = cjson.decode(raw)
local now = tonumber(ARGV[2])
if record.revoked_at == cjson.null and
   (record.expires_at == cjson.null or record.expires_at > now) and
   (record.last_used_at == cjson.null or now - record.last_used_at >= 60) then
  record.last_used_at = now
  raw = cjson.encode(record)
  redis.call('HSET', KEYS[1], id, raw)
end
return raw
"""

CHANGE = """
local raw = redis.call('HGET', KEYS[1], ARGV[1])
if not raw then return nil end
local record = cjson.decode(raw)
if record.team ~= ARGV[2] or (ARGV[3] ~= '' and record.project ~= ARGV[3]) then return nil end
if record.revoked_at ~= cjson.null then return 'revoked' end
local changes = cjson.decode(ARGV[4])
for field, value in pairs(changes) do record[field] = value end
raw = cjson.encode(record)
redis.call('HSET', KEYS[1], ARGV[1], raw)
return raw
"""


class KeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    role: Role = "viewer"
    project: str | None = None
    expires_at: AwareDatetime | None = None

    @field_validator("project")
    @classmethod
    def valid_project(cls, value: str | None) -> str | None:
        return validate_sandbox_id(value) if value is not None else None


class KeyUpdate(KeyCreate):
    name: Name | None = None
    role: Role | None = None


class ManagedKey(BaseModel):
    key_id: str
    team: str
    name: str
    role: Role
    project: str | None
    created_by: str
    created_at: float
    expires_at: float | None
    last_used_at: float | None
    revoked_at: float | None


class CreatedKey(ManagedKey):
    key: str


class KeyList(BaseModel):
    keys: list[ManagedKey]


def public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "sha256"}


async def lookup_key(request: Request, digest: str) -> KeyRecord | None:
    record = await storage_call(request, "lookup_key", digest, time())
    if not record:
        return None
    if record["revoked_at"] is not None:
        raise HTTPException(401, detail={"reason": "api_key_revoked", "message": "This API key was revoked."})
    return KeyRecord(
        sha256=digest, key_id=record["key_id"], sandbox=record["team"], role=record["role"],
        project=record["project"], expires_at=record["expires_at"], name=record.get("name"),
    )


def key_receipt(request: Request, action: str, record: dict[str, Any]) -> None:
    event = {
        "event": "team_key", "action_type": action, "chain_id": request.app.state.audit_chain_id,
        "sandbox_id": request.state.sandbox_id, "request_id": request.state.request_id,
        "principal": request.state.principal, "ts": time(), "key": public_record(record),
    }
    chain_audit_event(request, event)
    emit_audit_record(event)


def check_project(request: Request, project: str | None) -> None:
    if project is not None:
        project_access(request, project)
    elif request.state.principal.get("project"):
        raise HTTPException(403, detail="A project-bound admin can only manage keys in their project.")


async def change_key(request: Request, key_id: str, changes: dict[str, Any], action: str) -> dict[str, Any]:
    principal = require_role(request, "admin")
    if principal.get("key_id") == key_id and ("revoked_at" in changes or changes.get("role", "admin") != "admin"):
        raise HTTPException(409, detail="You cannot revoke or demote the key you are currently using.")
    record = await storage_call(
        request, "change_key", request.state.sandbox_id, key_id, principal.get("project") or "", changes,
    )
    if record is None:
        raise HTTPException(404, detail="Key not found in your team or project.")
    if record == "revoked":
        raise HTTPException(409, detail="This key is already revoked.")
    key_receipt(request, action, record)
    return public_record(record)


def register_key_routes(app: FastAPI) -> None:
    @app.get("/v1/team/keys", tags=["teams"], response_model=KeyList)
    async def list_keys(request: Request) -> dict[str, Any]:
        principal = require_role(request, "admin")
        rows = await storage_call(request, "list_keys", request.state.sandbox_id)
        keys = [public_record(row) for row in rows]
        if principal.get("project"):
            keys = [row for row in keys if row["project"] == principal["project"]]
        return {"keys": sorted(keys, key=lambda row: row["created_at"], reverse=True)}

    @app.post("/v1/team/keys", status_code=201, tags=["teams"], response_model=CreatedKey)
    async def create_key(request: Request, body: KeyCreate) -> dict[str, Any]:
        principal = require_role(request, "admin")
        check_project(request, body.project)
        key = "aw_" + secrets.token_urlsafe(30)
        record = {
            "sha256": hashlib.sha256(key.encode()).hexdigest(), "key_id": uuid4().hex,
            "team": request.state.sandbox_id, "role": body.role, "project": body.project, "name": body.name,
            "created_by": principal.get("sub") or principal["key_id"], "created_at": time(),
            "expires_at": body.expires_at.timestamp() if body.expires_at else None,
            "last_used_at": None, "revoked_at": None,
        }
        await storage_call(request, "create_key", record)
        key_receipt(request, "create", record)
        return {**public_record(record), "key": key}

    @app.patch("/v1/team/keys/{key_id}", tags=["teams"], response_model=ManagedKey)
    async def update_key(request: Request, key_id: str, body: KeyUpdate) -> dict[str, Any]:
        require_role(request, "admin")
        changes = body.model_dump(exclude_unset=True)
        if any(changes[field] is None for field in ("name", "role") if field in changes):
            raise HTTPException(422, detail="name and role cannot be null.")
        if "project" in changes:
            check_project(request, body.project)
        if "expires_at" in changes:
            changes["expires_at"] = body.expires_at.timestamp() if body.expires_at else None
        return await change_key(request, key_id, changes, "update")

    @app.delete("/v1/team/keys/{key_id}", tags=["teams"], response_model=ManagedKey)
    async def revoke_key(request: Request, key_id: str) -> dict[str, Any]:
        return await change_key(request, key_id, {"revoked_at": time()}, "revoke")
