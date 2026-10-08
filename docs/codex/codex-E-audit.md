Repository: RamazanKara/agentworkflows, branch main. Pull first; commit and push straight to main when green. Read docs/PRODUCT-GAPS.md (gap 10) and src/inference-gateway/app/audit.py before starting. Other changes (team settings, console polish) may land on main while you work; rebase on them, don't revert them.

Goal: a team admin reads the audit trail in the console and checks that it is intact, without shell access to gateway logs.

1. Queryable audit store (gateway):
   - Keep writing the hash-chained audit log exactly as today (the chain format and scripts/audit-verify.py must keep working).
   - Also append each chained event for a team to a per-team Redis stream with a retention bound (`AUDIT_VIEW_RETENTION_SECONDS`, default 90 days, plus a max length), storing the event as written (already redacted) with its chain id, sequence and hash.
   - Disabled cleanly when Redis is not configured (the page then says the audit view is off and how to enable it).

2. Admin API:
   - `GET /v1/team/audit` with filters: time range, event type, actor, project, run id; cursor pagination, newest first, page size cap.
   - `GET /v1/team/audit/verify?from=&to=`: recompute the hash chain over the stored range and return ok, the number of events checked, and the first break (sequence and reason) if any. Gaps from retention are reported as a range boundary, not a break.
   - Admin role only (403 with the existing `team_role_required` shape otherwise). Add to the OpenAPI contract (make api-contract).

3. Console (src/inference-gateway/console), minimal wiring; the design pass and screenshot gate happen afterwards in the cloud:
   - New admin-only "Audit log" page: filterable list (time, actor, event, project, run link), row expands to the event JSON (escaped text), a "Verify chain" action with the result, and an export of the filtered range as JSON Lines.
   - Playwright tests with route mocks: list, filter, expand, verify ok, verify break, non-admin hidden.

4. Tests: gateway unit tests for stream writes, retention trimming, filters, pagination, verify ok/break/retention-gap, and role checks. `make -j2 lint test-scripts test-gateway` and `make api-contract config-contract` must pass. Update docs (audit page in the product docs, runbooks/audit-chain or the closest existing runbook) and add a CHANGELOG entry under "Unreleased".

Report: what changed, test output summary, anything not verified.
