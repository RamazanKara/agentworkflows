# Team lifecycle APIs

These rc.2 APIs require the source gateway and SDKs. OpenAPI snapshots in
`platform/api-contracts/inference-gateway.openapi.json` define request and response schemas. The console's
Workflow templates, Workflow secrets, Data & privacy, and run detail pages use these same APIs.
All calls are scoped to the authenticated team; a project-scoped admin cannot administer
team settings, secrets or erasure. Redis accounting remains required with either storage backend.

| HTTP contract | Access and behavior | Python / TypeScript |
| --- | --- | --- |
| `GET /v1/workflow-templates` | Team member; version, schema, availability, installed version | `workflow_templates()` / `workflowTemplates()` |
| `POST /v1/workflow-templates/{id}/install` with `version` | Admin; pin a reviewed bundled version, idempotent | `install_template(id, version=...)` / `installTemplate(id, version)` |
| `POST /v1/workflow-runs/{id}/cancel` | Builder/admin in the project; requests Temporal cancellation | `cancel_run(id)` / `cancelRun(id)` |
| `POST /v1/workflow-runs/{id}/retry` | Builder/admin; failed/canceled/timed-out/terminated only, otherwise 409 | `retry_run(id)` / `retryRun(id)` |
| `GET /v1/workflows/{workflow}/secrets` | Admin; names, versions and timestamps only | `workflow_secrets(workflow)` / `workflowSecrets(workflow)` |
| `PUT /v1/workflows/{workflow}/secrets/{name}` | Admin; `{value, expected_version}`; 0 creates, current version rotates | `set_workflow_secret(workflow, name, value, expected_version=...)` / `setWorkflowSecret(workflow, name, value, version)` |
| `POST /v1/workflow-runs/{id}/secrets/{name}/resolve` | Running workflow; worker scope `workflows:execute`, matching run/step headers and project | `resolve_workflow_secret(id, step, name)` / `resolveWorkflowSecret(id, step, name)` |
| `GET`, `PUT /v1/team/retention` | Admin; PUT requires `If-Match` settings revision and all three periods | `team_retention()`, `set_team_retention(...)` / `teamRetention()`, `setTeamRetention(policy, revision)` |
| `GET /v1/team/data` | Admin; erasure status and external follow-up | `team_data()` / `teamData()` |
| `GET /v1/team/data/export` | Admin; JSON attachment of retained team data | `export_team_data()` / `exportTeamData()` |
| `DELETE /v1/team/data` with `confirm_team` | Admin; explicit matching team ID, resumable erasure | `delete_team_data(confirm_team=...)` / `deleteTeamData(team)` |
| `GET /v1/team/telemetry` | Admin; configured trace/metric export status, no credentials | `team_telemetry()` / `teamTelemetry()` |

Cancel and retry produce `workflow_operation` receipts with the actor and action. Retry records
the new run ID on the original timeline and starts a fresh budget using current policy. Repeating
retry on one original run returns the same retry execution. Tools already invoked can have external
side effects; the console confirms cancellation and explains that a retry may repeat them.
There is no new Helm switch: existing workflow/project/role policy governs these operations.

## Secret storage and rotation

Generate a Fernet key using the gateway environment's existing cryptography dependency:

```sh
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Store it securely as `WORKFLOW_SECRETS_KEY` for Compose, or in a Kubernetes Secret referenced by
`inference-gateway.workflowSecrets.existingSecret.name` and `.key` (default `fernet-key`).
Use one persistent key across all replicas and include it in your encrypted recovery procedure.
Without it secret writes/resolution return 503. The console never receives saved plaintext;
values are 1–16384 UTF-8 bytes, encrypted with their team/workflow/name binding before persistence.
Audit contains name, version, actor and activity step, never plaintext or ciphertext.

Rotation replaces the old ciphertext immediately and increments the version; a stale expected
version returns 409. A worker that already read the old value may finish with it. Resolve inside
an activity, use the result directly with the service, and never return it into workflow history,
log it, or put it into captured model/tool input. The SDK passes run/step headers; authorization
also checks the credential's team/project and the run's actual registered workflow. A worker
credential can access workflows in its authorized project; it is not an attestation of activity code.

This API rotates credential values. Changing the Fernet master key without migrating stored
ciphertexts makes them unreadable; keep the master key stable during normal rotation.

## Retention, export and erasure

Retention uses seconds, each an integer from 60 through 31536000:
`run_seconds`, `content_seconds`, `audit_seconds`. PUT uses the shared team-settings revision;
a concurrent settings change returns 409 and requires reloading. Run periods apply when runs
close, content periods when content is captured, and audit periods when views are read/pruned.
Existing expiry deadlines are not extended or rewritten. Helm defaults remain
`workflowRecords.runRecordRetentionSeconds`, `workflowRecords.contentRetentionSeconds` and
`traceability.auditViewRetentionSeconds` on the
inference-gateway chart; team overrides are durable settings, not deployment flags.
Optional Responses and batch/file stores keep their existing subsystem retention settings.

Export includes retained run metadata, start intents, receipts, captured step content, team
settings, managed key metadata, and Temporal histories. Enabled shared Responses and batch stores
also include responses, batches and base64 file content. Authentication tokens, key hashes and
secret ciphertext/plaintext are excluded; captured user content may itself contain sensitive data.
Export is a current retained-data view, not a transactionally frozen point-in-time backup. Store the
download securely. It cannot recover already expired data. Large exports need an operator-sized
HTTP timeout and sufficient memory; exports are currently a single JSON response.

Before DELETE, pause triggers, cancel running workflows and active batches, stop clients and let
requests/streams drain. Disable the response cache and wait its configured TTL if it was enabled.
In-memory Responses/batch stores cannot support shared-team export/erasure: use their Redis backends.
An active request/run/batch returns 409. Erasure freezes new requests across replicas, removes
Temporal executions/schedules, gateway records, managed keys/sessions, captured content, budgets,
and enabled shared auxiliary data. A minimal team tombstone (`erased`) blocks future work.
Background reconciliation respects that tombstone. Temporal visibility is supplemented with
durable run/start records to catch executions not yet indexed.

Storage/Temporal failure leaves `erasing` and returns an error. Restore the dependency and repeat
DELETE with the team's bootstrap admin key; completed deletions tolerate already missing records.
If a gateway process dies with an active request lease, stop **all** gateway replicas and workers,
verify no requests remain, then let the operator clear that team's `team-data:{team}:active` Redis
counter before resuming. A killed process or lost Redis connection during erasure can also leave a `team-data:{team}:lock`;
clear that operation lock only after stopping every replica and confirming the eraser is gone.
Never clear these while a replica can still write. Keep the erasure tombstone.
Duplicate erasers receive 409. A failed retry never unfreezes an already-erasing team.
Team gauges are removed locally after erasure and on other replicas' next 30-second refresh;
previously exported telemetry remains an external follow-up item.

The response lists external follow-up: provider/tool systems, delivered notifications, downloaded
exports, telemetry, operator audit logs, backups, object-store versions and Temporal archival.
Remove static team policies/bootstrap keys and identity-provider membership when retiring the team.
Those systems have independent retention/legal requirements. These endpoints support portability
and erasure operations; they do not certify GDPR compliance or erase records outside their stores.

See [OTLP operations](../runbooks/observability.md) for collector and dashboard configuration.
