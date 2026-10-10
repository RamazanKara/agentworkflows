# Team lifecycle APIs

These APIs require a v0.9.0 gateway and SDKs. OpenAPI snapshots in
`platform/api-contracts/inference-gateway.openapi.json` define request and response schemas. The console's
Workflow templates, Workflow secrets, Data & privacy, and run detail pages use these same APIs.
All calls are scoped to the authenticated team. Team settings, secrets and erasure are
administered by unrestricted team admins. Redis accounting is required with either storage backend.

| HTTP contract | Access and behavior | Python / TypeScript |
| --- | --- | --- |
| `GET /v1/workflow-templates` | Team member; version, schema, availability, installed version | `workflow_templates()` / `workflowTemplates()` |
| `POST /v1/workflow-templates/{id}/install` with `version` | Admin; pin a reviewed bundled version, idempotent | `install_template(id, version=...)` / `installTemplate(id, version)` |
| `POST /v1/workflow-runs/{id}/cancel` | Builder/admin in the project; requests Temporal cancellation | `cancel_run(id)` / `cancelRun(id)` |
| `POST /v1/workflow-runs/{id}/retry` | Builder/admin; failed/canceled/timed-out/terminated runs (409 for other states) | `retry_run(id)` / `retryRun(id)` |
| `GET /v1/workflows/{workflow}/secrets` | Admin; names, versions and timestamps only | `workflow_secrets(workflow)` / `workflowSecrets(workflow)` |
| `PUT /v1/workflows/{workflow}/secrets/{name}` | Admin; `{value, expected_version}`; 0 creates, current version rotates | `set_workflow_secret(workflow, name, value, expected_version=...)` / `setWorkflowSecret(workflow, name, value, version)` |
| `POST /v1/workflow-runs/{id}/secrets/{name}/resolve` | Running workflow; worker scope `workflows:execute`, matching run/step headers and project | `resolve_workflow_secret(id, step, name)` / `resolveWorkflowSecret(id, step, name)` |
| `GET`, `PUT /v1/team/retention` | Admin; PUT requires `If-Match` settings revision and all three periods | `team_retention()`, `set_team_retention(...)` / `teamRetention()`, `setTeamRetention(policy, revision)` |
| `GET /v1/team/data` | Admin; erasure status and external follow-up | `team_data()` / `teamData()` |
| `GET /v1/team/data/export` | Admin; JSON attachment of retained team data | `export_team_data()` / `exportTeamData()` |
| `DELETE /v1/team/data` with `confirm_team` | Admin; explicit matching team ID, resumable erasure | `delete_team_data(confirm_team=...)` / `deleteTeamData(team)` |
| `GET /v1/team/telemetry` | Admin; configured trace/metric export status, with credentials kept on the server | `team_telemetry()` / `teamTelemetry()` |

Cancel and retry produce `workflow_operation` receipts with the actor and action. Retry records
the new run ID on the original timeline and starts a fresh budget using current policy. Repeating
retry on one original run returns the same retry execution. The console confirms cancellation
and explains that a retry runs the workflow's tools again, so review their external side effects first.
Existing workflow/project/role policy governs these operations.

## Secret storage and rotation

Generate a Fernet key using the gateway environment's existing cryptography dependency:

```sh
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Store it securely as `WORKFLOW_SECRETS_KEY` for Compose, or in a Kubernetes Secret referenced by
`inference-gateway.workflowSecrets.existingSecret.name` and `.key` (default `fernet-key`).
Use one persistent key across all replicas and include it in your encrypted recovery procedure.
Secret writes and resolution require this key (503 until it is set). Saved plaintext stays on the
gateway; values are 1–16384 UTF-8 bytes, encrypted with their team/workflow/name binding before persistence.
Audit contains name, version, actor and activity step.

Rotation replaces the old ciphertext immediately and increments the version; a stale expected
version returns 409. A worker that already read the old value finishes with it. Resolve inside
an activity and use the result directly with the service. Keep it out of workflow history, logs,
and captured model/tool input. The SDK passes run/step headers; authorization
also checks the credential's team/project and the run's actual registered workflow. A worker
credential can access workflows in its authorized project.

This API rotates credential values. Keep the Fernet master key stable during normal rotation;
changing it requires migrating stored ciphertexts.

## Retention, export and erasure

Retention uses seconds, each an integer from 60 through 31536000:
`run_seconds`, `content_seconds`, `audit_seconds`. PUT uses the shared team-settings revision;
a concurrent settings change returns 409; reload and reapply. Run periods apply when runs
close, content periods when content is captured, and audit periods when views are read/pruned.
Existing expiry deadlines stay as set. Helm defaults remain
`workflowRecords.runRecordRetentionSeconds`, `workflowRecords.contentRetentionSeconds` and
`traceability.auditViewRetentionSeconds` on the
inference-gateway chart; team overrides are durable team settings.
Optional Responses and batch/file stores keep their existing subsystem retention settings.

Export includes retained run metadata, start intents, receipts, captured step content, team
settings, managed key metadata, and Temporal histories. Enabled shared Responses and batch stores
also include responses, batches and base64 file content. Authentication tokens, key hashes and
secret ciphertext/plaintext are excluded. Captured user content can contain sensitive data, so store the
download securely. Export is a current view of retained data. Each export is a single JSON
response; give large exports an operator-sized HTTP timeout and sufficient memory.

Before DELETE, pause triggers, cancel running workflows and active batches, stop clients and let
requests/streams drain. Disable the response cache and wait its configured TTL if it was enabled.
Use the Redis backends for Responses/batch stores to include them in shared-team export and erasure.
An active request/run/batch returns 409. Erasure freezes new requests across replicas, removes
Temporal executions/schedules, gateway records, managed keys/sessions, captured content, budgets,
and enabled shared auxiliary data. A minimal team tombstone (`erased`) blocks future work.
Background reconciliation respects that tombstone. Durable run/start records supplement Temporal
visibility so recently started executions are included.

Storage/Temporal failure leaves `erasing` and returns an error. Restore the dependency and repeat
DELETE with the team's bootstrap admin key; repeated deletions skip records already removed.
If a gateway process dies with an active request lease, stop **all** gateway replicas and workers,
verify no requests remain, then let the operator clear that team's `team-data:{team}:active` Redis
counter before resuming. A killed process or lost Redis connection during erasure can also leave a `team-data:{team}:lock`;
clear that operation lock only after stopping every replica and confirming the eraser is gone.
Clear these only when no replica can write. Keep the erasure tombstone.
Duplicate erasers receive 409. An erasing team stays frozen through retries.
Team gauges are removed locally after erasure and on other replicas' next 30-second refresh;
previously exported telemetry remains an external follow-up item.

The response lists external follow-up: provider/tool systems, delivered notifications, downloaded
exports, telemetry, operator audit logs, backups, object-store versions and Temporal archival.
Remove static team policies/bootstrap keys and identity-provider membership when retiring the team.
Those systems have independent retention/legal requirements. These endpoints support portability
and erasure operations for the gateway's own stores.

See [OTLP operations](../runbooks/observability.md) for collector and dashboard configuration.
