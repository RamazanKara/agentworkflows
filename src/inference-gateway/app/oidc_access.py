"""Team-scoped access derived from verified company sign-in claims."""

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel

from app.jwks import JwtAuthError
from app.settings import Settings

Role = Literal["admin", "builder", "approver", "viewer"]


class TeamSSO(BaseModel):
    team_id: str
    enabled: bool
    provider_name: str | None
    role_source: Literal["groups", "claim"]
    team_claim: str
    project_claim: str
    role_claim: str | None
    default_role: Role | None
    groups_claim: str | None
    group_role_mappings: dict[str, Role]


def group_role(settings: Settings, team: str, claims: dict[str, Any]) -> str:
    groups = claims.get(settings.oidc_groups_claim)
    if not isinstance(groups, list) or any(not isinstance(group, str) or not group.strip() for group in groups):
        raise JwtAuthError("groups claim must be an array of nonempty strings")
    mapping = settings.oidc_group_role_mappings.get(team, {})
    roles = {mapping[group] for group in groups if group in mapping}
    # Builder and approver are separate duties, so there is no implicit role hierarchy.
    if len(roles) != 1:
        raise JwtAuthError("no unique mapped role for this team")
    return roles.pop()


def access_policy_digest(settings: Settings, team: str) -> str:
    policy = [
        settings.oidc_issuer, settings.oidc_client_id, settings.oidc_team_claim or settings.jwt_tenant_claim,
        settings.oidc_project_claim, settings.oidc_role_claim, settings.oidc_default_role,
        settings.oidc_groups_claim, bool(settings.oidc_group_role_mappings),
        settings.oidc_group_role_mappings.get(team, {}),
    ]
    return hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
