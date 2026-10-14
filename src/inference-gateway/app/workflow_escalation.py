"""Opt-in approval escalation: a review still waiting at its deadline notifies and widens who may approve.

Team admins configure escalation per workflow in the revision-checked team document. A run keeps the
configuration it started with. The gateway records when the run's approval gate opened, so the deadline
and the widened eligibility are derived from durable timestamps rather than workflow code: Temporal
histories are unchanged, and in-flight runs on existing workers escalate too.
"""

from __future__ import annotations

from datetime import UTC, datetime
from time import time
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.team_settings import effective_team_settings, match_revision, require_settings_admin, save_team_document
from app.workflow_budget import redis_call, run_key

Channel = Literal["slack", "email", "webhook"]
Reviewer = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
EVENT = "approval_escalated"
# Who wrote an escalation receipt: the team's policy, not the reviewer or viewer whose request noticed it.
POLICY_PRINCIPAL = {"auth": "policy", "key_id": "approval-escalation"}


class ApprovalEscalation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    after_seconds: int = Field(
        ge=60,
        le=604740,
        strict=True,
        description="Seconds a review may wait before it escalates; shorter than the approval timeout.",
    )
    approver_role: Literal["approver"] | None = Field(
        default=None, description="Let every approver review once escalated (for workflows that need an admin)."
    )
    reviewers: list[Reviewer] = Field(
        default_factory=list,
        max_length=20,
        description="Identities (OIDC subject or managed key ID) with an approver role who may review once escalated.",
    )
    channels: list[Channel] | None = Field(
        default=None,
        description="Where to send the escalation notice; omit for the team's alert channels, [] for none.",
    )

    @field_validator("reviewers", "channels")
    @classmethod
    def distinct(cls, values: list[str] | None) -> list[str] | None:
        return list(dict.fromkeys(values)) if values is not None else None


class TeamEscalation(ApprovalEscalation):
    workflow: str
    effective: bool = Field(description="False when the workflow's approval timeout ends the review first.")
    updated_by: str | None
    updated_at: float | None


class TeamEscalations(BaseModel):
    revision: int
    escalations: list[TeamEscalation]
    available_channels: list[Channel]


class EscalationSaved(BaseModel):
    revision: int
    escalation: TeamEscalation


class EscalationRemoved(BaseModel):
    revision: int
    removed: str


def configuration(document: dict[str, Any], workflow: str) -> dict[str, Any] | None:
    """The escalation a new run of ``workflow`` keeps for its whole life; invalid rows configure nothing."""
    row = (document.get("approval_escalations") or {}).get(workflow)
    if not row:
        return None
    try:
        return ApprovalEscalation.model_validate(
            {key: value for key, value in row.items() if key in ApprovalEscalation.model_fields}
        ).model_dump()
    except ValueError:
        return None


def opened_key(request: Request, run_id: str) -> str:
    return run_key(request, run_id) + ":approval-opened"


def escalated_key(request: Request, run_id: str) -> str:
    return run_key(request, run_id) + ":escalated"


async def open_gate(request: Request, run_id: str) -> None:
    """Remember when the run started waiting for review; a retried gate keeps the first time."""
    await redis_call(request, "setnx", opened_key(request, run_id), repr(time()))


async def escalation_state(
    request: Request, run_id: str, approval_policy: dict[str, Any], *, waiting: bool
) -> dict[str, Any] | None:
    """The run's escalation: its configuration, deadline, and whether it is in effect."""
    config = approval_policy.get("approval_escalation")
    if not config:
        return None
    opened = await redis_call(request, "get", opened_key(request, run_id))
    recorded = await redis_call(request, "get", escalated_key(request, run_id))
    waiting_since = float(opened) if opened else None
    escalates_at = waiting_since + config["after_seconds"] if waiting_since is not None else None
    recorded_at = float(recorded) if recorded else None
    return {
        **config,
        "waiting_since": waiting_since,
        "escalates_at": escalates_at,
        # Eligibility widens at the deadline itself; the receipt follows on the next look.
        "escalated": recorded_at is not None or (waiting and escalates_at is not None and time() >= escalates_at),
        "recorded_at": recorded_at,
    }


async def escalate_if_due(
    request: Request, run_id: str, workflow: str, state: dict[str, Any] | None, *, waiting: bool
) -> bool:
    """Write the escalation receipt and queue its notice once, for a review still waiting past its deadline."""
    if (
        not state
        or not waiting
        or state["recorded_at"] is not None
        or state["escalates_at"] is None
        or time() < state["escalates_at"]
    ):
        return False
    now = time()
    if not await redis_call(request, "setnx", escalated_key(request, run_id), repr(now)):
        return False
    from app.workflow_notifications import queue_event
    from app.workflow_operations import operation_receipt

    request.state.workflow_name = workflow
    await operation_receipt(
        request,
        run_id,
        EVENT,
        principal=POLICY_PRINCIPAL,
        after_seconds=state["after_seconds"],
        approver_role=state["approver_role"],
        reviewers=state["reviewers"],
        escalates_at=state["escalates_at"],
    )
    if state["channels"] != []:
        await queue_event(request, run_id, EVENT)
    state.update(escalated=True, recorded_at=now)
    return True


def escalation_admits(state: dict[str, Any] | None, principal: dict[str, Any], reviewer: str | None) -> bool:
    return bool(
        state
        and state["escalated"]
        and (principal.get("role") == state["approver_role"] or (reviewer and reviewer in state["reviewers"]))
    )


def review_access(principal: dict[str, Any], role: str, state: dict[str, Any] | None) -> dict[str, Any]:
    """Whether the caller may review this waiting run now, why, and when escalation would admit them."""
    reviewer = principal.get("sub") or principal.get("key_id")
    if principal.get("role") in {"admin", role}:
        return {"eligible": True, "via": "role", "eligible_at": None}
    if principal.get("role") != "approver":
        return {"eligible": False, "via": None, "eligible_at": None}
    if escalation_admits(state, principal, reviewer):
        return {"eligible": True, "via": "escalation", "eligible_at": None}
    later = state and escalation_admits({**state, "escalated": True}, principal, reviewer)
    return {"eligible": False, "via": None, "eligible_at": state["escalates_at"] if later and state else None}


def review_denied(role: str, state: dict[str, Any] | None) -> dict[str, Any]:
    message = f"Use a team-bound credential with role admin or {role}; ask your team administrator."
    if role == "admin":
        message = "Use a team-bound credential with role admin; ask your team administrator."
    if state and not state["escalated"] and state["escalates_at"] is not None:
        moment = datetime.fromtimestamp(state["escalates_at"], UTC).isoformat(timespec="seconds")
        message += f" If you are named in its escalation, this review opens to you at {moment}."
    return {"reason": "team_role_required", "message": message}


def team_row(name: str, row: dict[str, Any], timeout: int | None) -> dict[str, Any]:
    config = configuration({"approval_escalations": {name: row}}, name) or {}
    return {
        "workflow": name,
        **config,
        "effective": timeout is not None and config.get("after_seconds", timeout) < timeout,
        "updated_by": row.get("updated_by"),
        "updated_at": row.get("updated_at"),
    }


def register_escalation_routes(app: FastAPI) -> None:
    from app.workflow_notifications import channels

    @app.get(
        "/v1/team/approval-escalations",
        tags=["workflows"],
        response_model=TeamEscalations,
        summary="List per-workflow approval escalation (team admin)",
    )
    async def list_escalations(request: Request, response: Response) -> dict[str, Any]:
        require_settings_admin(request)
        settings = await effective_team_settings(request)
        workflows = settings.team.workflows if settings.team else {}
        config = settings.team.notifications if settings.team else None
        response.headers["Cache-Control"] = "no-store"
        return {
            "revision": settings.document["revision"],
            "escalations": [
                team_row(name, row, workflows[name].approval_timeout_seconds if name in workflows else None)
                for name, row in sorted((settings.document.get("approval_escalations") or {}).items())
            ],
            "available_channels": channels(config) if config else [],
        }

    @app.put(
        "/v1/team/workflows/{name}/approval-escalation",
        tags=["workflows"],
        response_model=EscalationSaved,
        summary="Escalate reviews of this workflow that wait too long; applies to runs started afterwards",
    )
    async def put_escalation(
        request: Request,
        response: Response,
        name: str,
        body: ApprovalEscalation,
        if_match: Annotated[str, Header(alias="If-Match")],
    ) -> dict[str, Any]:
        principal = require_settings_admin(request)
        settings = await effective_team_settings(request)
        if match_revision(if_match) != settings.document["revision"]:
            raise HTTPException(
                409,
                detail={
                    "reason": "approval_escalation_conflict",
                    "message": "Team settings changed. Reload approval escalations before saving again.",
                },
            )
        policy = settings.team.workflows.get(name) if settings.team else None
        if policy is None:
            raise HTTPException(
                404,
                detail={
                    "reason": "workflow_not_found",
                    "message": "Choose a workflow from GET /v1/workflow-policies.",
                },
            )
        errors = []
        if body.after_seconds >= policy.approval_timeout_seconds:
            errors.append(
                {
                    "field": "after_seconds",
                    "message": f"Escalate before the approval timeout of {policy.approval_timeout_seconds} seconds.",
                }
            )
        config = settings.team.notifications if settings.team else None
        available = channels(config) if config else []
        if body.channels and set(body.channels) - set(available):
            errors.append(
                {
                    "field": "channels",
                    "message": "Ask your operator to configure the selected notification destinations.",
                }
            )
        if errors:
            raise HTTPException(
                422,
                detail={
                    "reason": "approval_escalation_invalid",
                    "message": " ".join(f"{error['field']}: {error['message']}" for error in errors),
                    "fields": errors,
                },
            )
        escalations = dict(settings.document.get("approval_escalations") or {})
        existing = escalations.get(name)
        config_row = body.model_dump()
        if existing and configuration(settings.document, name) == config_row:
            row, revision = existing, settings.document["revision"]
        else:
            row = {**config_row, "updated_by": principal.get("sub") or principal.get("key_id"), "updated_at": time()}
            escalations[name] = row
            saved = await save_team_document(
                request,
                settings.document,
                {"approval_escalations": escalations},
                "approval_escalation_changed",
                workflow=name,
                escalation=config_row,
                replaced=existing is not None,
            )
            revision = saved["revision"]
        response.headers["ETag"] = f'"{revision}"'
        response.headers["Cache-Control"] = "no-store"
        return {"revision": revision, "escalation": team_row(name, row, policy.approval_timeout_seconds)}

    @app.delete(
        "/v1/team/workflows/{name}/approval-escalation",
        tags=["workflows"],
        response_model=EscalationRemoved,
        summary="Stop escalating reviews of this workflow for runs started afterwards",
    )
    async def delete_escalation(
        request: Request, response: Response, name: str, if_match: Annotated[str, Header(alias="If-Match")]
    ) -> dict[str, Any]:
        require_settings_admin(request)
        settings = await effective_team_settings(request)
        if match_revision(if_match) != settings.document["revision"]:
            raise HTTPException(
                409,
                detail={
                    "reason": "approval_escalation_conflict",
                    "message": "Team settings changed. Reload approval escalations before removing one.",
                },
            )
        escalations = dict(settings.document.get("approval_escalations") or {})
        if name not in escalations:
            raise HTTPException(
                404,
                detail={
                    "reason": "approval_escalation_missing",
                    "message": "This workflow has no approval escalation.",
                },
            )
        del escalations[name]
        saved = await save_team_document(
            request,
            settings.document,
            {"approval_escalations": escalations},
            "approval_escalation_removed",
            workflow=name,
        )
        response.headers["ETag"] = f'"{saved["revision"]}"'
        response.headers["Cache-Control"] = "no-store"
        return {"revision": saved["revision"], "removed": name}
