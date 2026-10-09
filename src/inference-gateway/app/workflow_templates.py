"""Version-pinned installations of the built-in, policy-approved worker templates."""

from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.team_settings import effective_team_settings, require_settings_admin, save_team_document
from app.teams import require_role

TEMPLATE_VERSION = "0.9.0"
TEMPLATES = {
    "research": ("ResearchWorkflow", "Research", "Research a topic, review a draft, then publish."),
    "code-review": ("CodeReviewWorkflow", "Code review", "Review a patch and approve the findings."),
    "support-triage": ("SupportTriageWorkflow", "Support triage", "Classify a ticket and draft a reply."),
    "weekly-report": ("WeeklyReportWorkflow", "Weekly report", "Combine changes, support and incidents."),
    "incident-summary": ("IncidentSummaryWorkflow", "Incident summary", "Build a timeline from incident logs."),
    "document-qa": ("DocumentQAWorkflow", "Document Q&A", "Answer a question with source citations."),
}


class TemplateInstall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["0.9.0"]


class InstalledTemplate(BaseModel):
    id: str
    version: str
    workflow: str


class WorkflowTemplate(InstalledTemplate):
    name: str
    description: str
    installable: bool
    installed_version: str | None
    input_schema: dict[str, Any] | None


def register_template_routes(app: FastAPI) -> None:
    @app.get(
        "/v1/workflow-templates",
        tags=["workflows"],
        response_model=list[WorkflowTemplate],
        summary="Browse versioned templates and this team's installations",
    )
    async def gallery(request: Request) -> list[dict[str, Any]]:
        require_role(request, "admin", "builder", "approver", "viewer")
        settings = await effective_team_settings(request)
        installed = settings.document.get("templates", {})
        workflows = settings.team.workflows if settings.team else {}
        return [
            {
                "id": key,
                "version": TEMPLATE_VERSION,
                "workflow": workflow,
                "name": name,
                "description": description,
                "installable": workflow in workflows,
                "installed_version": installed.get(key, {}).get("version"),
                "input_schema": workflows[workflow].model_dump(by_alias=True)["inputSchema"]
                if workflow in workflows
                else None,
            }
            for key, (workflow, name, description) in TEMPLATES.items()
        ]

    @app.post(
        "/v1/workflow-templates/{template_id}/install",
        tags=["workflows"],
        response_model=InstalledTemplate,
        summary="Install a reviewed template version for this team",
    )
    async def install(request: Request, template_id: str, body: TemplateInstall) -> dict[str, Any]:
        require_settings_admin(request)
        if template_id not in TEMPLATES:
            raise HTTPException(404, detail="Template not found.")
        settings = await effective_team_settings(request)
        workflow = TEMPLATES[template_id][0]
        if not settings.team or workflow not in settings.team.workflows:
            raise HTTPException(
                409, detail="Register this built-in workflow on the team worker and approve its policy first."
            )
        installed = {"id": template_id, "version": body.version, "workflow": workflow}
        templates = settings.document.get("templates", {})
        if templates.get(template_id) != installed:
            await save_team_document(
                request,
                settings.document,
                {"templates": {**templates, template_id: installed}},
                "template_installed",
                template=installed,
            )
        return installed
