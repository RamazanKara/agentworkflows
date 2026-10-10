"""Request and response types for the gateway's team administration APIs."""

from typing import Any, Literal, NotRequired, TypedDict

Role = Literal["admin", "builder", "approver", "viewer"]
TeamSettingValue = str | int | float | bool | list[str] | None
CaptureMode = Literal["none", "redacted", "full"]


class TeamSSO(TypedDict):
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


class SpendAlert(TypedDict):
    id: str
    level: Literal["soft", "hard"]
    limit_usd: float
    reserved_and_spent_usd: float
    requested_usd: float
    created_at: float
    webhook_status: Literal["disabled", "pending", "delivered", "failed"]
    attempts: int


class TeamSpend(TypedDict):
    team_id: str
    window_start: int
    window_end: int
    soft_limit_usd: float | None
    hard_limit_usd: float | None
    reserved_and_spent_usd: float
    status: Literal["ok", "soft_limit", "hard_limit"]
    alerts: list[SpendAlert]


class KeyOptions(TypedDict, total=False):
    role: Role
    project: str | None
    expires_at: str | None


class KeyUpdate(KeyOptions, total=False):
    name: str


class ManagedKey(TypedDict):
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


class KeyList(TypedDict):
    keys: list[ManagedKey]


class TeamSetting(TypedDict):
    value: TeamSettingValue
    source: Literal["policy", "override"]
    policy_default: TeamSettingValue


class TeamSettings(TypedDict):
    revision: int
    updated_by: str | None
    updated_at: float | None
    fields: dict[str, TeamSetting]
    routes: list[str]
    providers: list[str]
    approver_roles: list[Literal["admin", "approver"]]


class AuditFilters(TypedDict, total=False):
    from_time: float
    to: float
    event_type: str
    actor: str
    project: str
    run_id: str
    cursor: str
    limit: int


RunStatus = Literal[
    "running", "awaiting_approval", "completed", "failed", "canceled", "terminated", "timed_out", "continued_as_new"
]


class RunFilters(TypedDict, total=False):
    project: str | None
    workflow: str | None
    trigger: str | None
    status: RunStatus | None
    cursor: str | None
    offset: int
    limit: int


class RunPage(TypedDict):
    runs: list[dict[str, Any]]
    next_offset: int | None
    next_cursor: str | None


class AuditEntry(TypedDict):
    id: str
    chain_id: str
    sequence: int
    team_sequence: int
    record_hash: str
    view_prev_hash: str
    view_hash: str
    event: dict[str, Any]


class AuditPage(TypedDict):
    enabled: bool
    message: NotRequired[str | None]
    events: list[AuditEntry]
    next_cursor: str | None


class AuditPosition(TypedDict):
    chain_id: str
    sequence: int
    reason: str


class AuditVerification(TypedDict):
    enabled: bool
    message: NotRequired[str | None]
    ok: bool | None
    checked: int
    first_break: AuditPosition | None
    boundaries: list[AuditPosition]


class WorkflowRegistrationOptions(TypedDict, total=False):
    allowed_providers: list[str]
    allowed_tools: list[str]
    token_limit: int
    cost_limit_usd: float
    approval_required: bool
    approver_role: Literal["admin", "approver"]
    required_approvals: int
    approval_timeout_seconds: int
    input_schema: dict[str, Any] | None


class RegisteredWorkflow(TypedDict):
    name: str
    allowed_models: list[str]
    allowed_providers: list[str]
    allowed_tools: list[str]
    allowed_egress: list[str]
    token_limit: int
    cost_limit_usd: float
    approval_required: bool
    approver_role: Literal["admin", "approver"]
    required_approvals: int
    approval_timeout_seconds: int
    input_schema: dict[str, Any] | None
    registered_by: str | None
    registered_at: float | None


class WorkflowModelOption(TypedDict):
    id: str
    provider: str
    simulated: bool


class WorkflowOptions(TypedDict):
    providers: list[str]
    models: list[WorkflowModelOption]
    tools: list[str]


class TeamWorkflows(TypedDict):
    revision: int
    enabled: bool
    workflows: list[RegisteredWorkflow]
    reserved: list[str]
    options: WorkflowOptions
    limits: dict[str, float]


class WorkflowRegistration(TypedDict):
    revision: int
    workflow: RegisteredWorkflow


class StepSummary(TypedDict):
    step_id: str
    action: str
    calls: int
    duration_ms: float
    tokens: int
    cost_usd: float


class RunSummary(TypedDict):
    elapsed_seconds: float | None
    model_calls: int
    tool_calls: int
    model_ms: float
    tool_ms: float
    review_seconds: float | None
    review_open: bool
    tokens: int
    cost_usd: float
    slowest_step: StepSummary | None
    costliest_step: StepSummary | None
    by_step: list[StepSummary]


class WorkflowInsight(TypedDict):
    workflow: str
    runs: int
    outcomes: dict[str, int]
    completion_rate: float | None
    median_seconds: float | None
    p95_seconds: float | None
    median_review_seconds: float | None
    average_cost_usd: float | None
    total_cost_usd: float
    slowest_step: dict[str, Any] | None
    costliest_step: dict[str, Any] | None


class WorkflowInsights(TypedDict):
    window: dict[str, float]
    projects: list[str]
    scanned: int
    skipped: int
    truncated: bool
    workflows: list[WorkflowInsight]
