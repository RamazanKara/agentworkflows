"""Revision-checked team alerts using operator-approved delivery destinations."""

from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.team_settings import effective_team_settings, match_revision, require_settings_admin, save_team_document

Event = Literal["awaiting_approval", "failed", "budget_threshold", "slow_step"]
Channel = Literal["slack", "email", "webhook"]


class AlertRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[Event] = Field(max_length=4)
    channels: list[Channel] = Field(max_length=3)
    budget_threshold: float = Field(gt=0, le=1, allow_inf_nan=False)
    slow_step_ms: int = Field(ge=1, le=86400000, strict=True)


class TeamAlertRules(AlertRules):
    revision: int
    available_channels: list[Channel]


async def effective_alert_rules(request: Request) -> tuple[Any, AlertRules]:
    from app.workflow_notifications import channels

    settings = await effective_team_settings(request)
    config = settings.team.notifications if settings.team else None
    rules = settings.document.get("alert_rules") or {
        "events": ["awaiting_approval", "failed", "budget_threshold"],
        "channels": channels(config) if config else [],
        "budget_threshold": config.budget_threshold if config else 0.8,
        "slow_step_ms": 30000,
    }
    return settings, AlertRules.model_validate(rules)


def register_alert_routes(app: FastAPI) -> None:
    from app.workflow_notifications import channels

    @app.get("/v1/team/alert-rules", tags=["observability"], response_model=TeamAlertRules)
    async def get_rules(request: Request) -> dict[str, Any]:
        require_settings_admin(request)
        settings, rules = await effective_alert_rules(request)
        config = settings.team.notifications if settings.team else None
        return {
            **rules.model_dump(),
            "revision": settings.document["revision"],
            "available_channels": channels(config) if config else [],
        }

    @app.put(
        "/v1/team/alert-rules",
        tags=["observability"],
        response_model=TeamAlertRules,
        summary="Choose team run alerts without changing approved destinations",
    )
    async def put_rules(
        request: Request, body: AlertRules, if_match: Annotated[str, Header(alias="If-Match")]
    ) -> dict[str, Any]:
        require_settings_admin(request)
        settings, _ = await effective_alert_rules(request)
        if match_revision(if_match) != settings.document["revision"]:
            raise HTTPException(409, detail="Team settings changed. Reload alert rules before saving.")
        config = settings.team.notifications if settings.team else None
        available = channels(config) if config else []
        if set(body.channels) - set(available):
            raise HTTPException(422, detail="Ask your operator to configure the selected notification destinations.")
        saved = await save_team_document(
            request,
            settings.document,
            {"alert_rules": body.model_dump()},
            "alert_rules_changed",
            rules=body.model_dump(),
        )
        return {**body.model_dump(), "revision": saved["revision"], "available_channels": available}
