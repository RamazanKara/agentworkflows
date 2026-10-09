# Bug hunt: AgentWorkflows
Date: 2026-10-09. Changes were made directly in the supplied Windows worktree and remain uncommitted. The supplied checkout was `codex-awBH` at `479710d`; it was not switched to `main`. Dependency manifests are unchanged. No features, commits, pushes, PRs, or GitHub operations were added.
## Inventory and review coverage
This was a source review with local regression tests, not proof that every runtime or deployment path is defect-free.
| System | Areas examined | Evidence and limits |
| --- | --- | --- |
| Gateway admission and authentication | Request schemas, payload sizes, parameter filtering, API keys, JWT/JWKS, OIDC, tenant boundaries, secret and output guardrails | Gateway tests; fixes BH-06 and BH-07. |
| Inference execution | Runtime routing, cloud adapters, SSE termination, retries, failover, shadow tasks, body and concurrency limits | Gateway and SDK tests; no live provider traffic. |
| Accounting and observability | UTC budget periods, reservations and settlement, rate limiting, audit chains, receipts, tracing | Existing unit tests and contract checks; external services not exercised. |
| Persistence and migration | Memory/Redis/Postgres stores, retention, response chaining, migrations and import/export ordering | Fix BH-05; Redis/Postgres failure and migration tests use the repository's local test fixtures. No live restore drill. |
| Files and batch processing | Queue claims, awaited chunks, cancellation, expiry, checkpoints, result assembly, object-store cleanup | Fixes BH-01 through BH-04; restart and claim-loss regressions. |
| Workflow control | Temporal adapters, approvals, triggers, secrets, notifications, run operations and container helpers | Gateway and SDK suites; actual Temporal recovery and Kubernetes execution remain caller checks. |
| RAG | Ingestion/chunking, manifest updates, embeddings, retrieval, reranking, validation and fallback paths | Full RAG suite; fixes BH-08 and BH-09. |
| React console | Landing routes, pagination, asynchronous loading/aborts, transcript rendering, forms, polling/listener cleanup, downloads, locale formatting, responsive and accessible controls | 78 Edge/Playwright tests, build and screenshot inspection; fixes BH-10 and BH-11. Screen-reader and physical-device behavior remain unverified. |
| Python SDK and workers | Streaming, error translation, retries, workflow/activity operations, subprocess cancellation and resource cleanup | 203 tests passed; fix BH-12. Three Linux-container tests skipped on Windows. |
| TypeScript SDK and workers | Transport/error paths, async workflow/activity ordering, cancellation and build/types | Build, lint and all 79 tests passed; no additional confirmed defect fixed. |
| Deployment and tooling | Nine Helm charts, umbrella dependencies, Compose/GitOps/policy configuration, backup/restore and validation scripts | Fix BH-13; 47 script tests, including 20 Helm rendering tests, and all nine chart lints passed. No cluster was deployed. |
| Native/mobile/media | Tracked source inventory for Go, Android, Capacitor, WebGL, Canvas and audio lifecycles | No Go service, Android/Capacitor project, or WebGL/Canvas/audio runtime was found. No APK rebuild applies. |
## Confirmed bugs and minimal fixes
Severity describes impact when the trigger occurs. Every item has a regression that failed against the corresponding previous behavior and passes with the fix. The expiry regression initially tried to mutate an immutable field; it was corrected to advance the clock and then reproduced the old final-chunk behavior before verification.
| ID / severity | Bug and root cause | Fix | Regression test |
| --- | --- | --- | --- |
| BH-01 / High | A restart while finalizing a mixed batch loses already-assembled results: output parts were deleted before the error file and terminal batch state were saved. | In `app/batch_worker.py`, retain all parts until both files and terminal state are persisted. Log cleanup I/O failures without turning a completed batch into a failure. | `test_restart_during_finalization_keeps_result_parts`: interrupt error-file creation, restart, verify both nonempty files, counts and cleanup. |
| BH-02 / High | A worker whose claim was reclaimed during its final awaited request could still checkpoint and finalize stale results. Ownership was checked only before the await. | Recheck the claim immediately after gathering a chunk, before any result is persisted. | `test_lost_claim_during_final_chunk_does_not_checkpoint`: reclaim in the request handler; verify no checkpoint, result parts or terminal state, and that the queued claim remains available. |
| BH-03 / Medium | Cancellation or expiry during the last request was ignored because no subsequent chunk boundary ran. | Reload cancellation state and check expiry before the final checkpoint/finalization. Preserve completed partial results. | `test_final_chunk_observes_cancellation_and_expiry`, both cancellation and clock-advanced expiry cases. |
| BH-04 / Medium | Batch redirects were counted as successful model output: only status codes at least 400 were considered errors. | Treat only HTTP 2xx as success, matching the worker's documented contract. | `test_redirected_item_is_an_error`, using a 302 response. |
| BH-05 / Medium | The memory response store retained every unread expired conversation. Expiry only removed records when individually fetched; exact-expiry reads/deletes also disagreed with TTL semantics. | In `app/response_store.py`, reclaim expired records on writes in expiry order, move renewed records to the end, and consistently treat the expiry instant as expired. No scan of all live conversations on every write. | New `test_response_store.py` cases cover unread expiry, renewal ordering, and get/delete at the exact expiry time. |
| BH-06 / Low | Padded base64 payloads were overestimated by one or two bytes, incorrectly rejecting inline images exactly at the byte limit. | In `app/admission.py`, subtract base64 padding from the decoded-size estimate. | `test_data_url_padding_does_not_count_as_image_bytes`: 1, 2, 3 and 1,048,576 bytes; three cases failed before the fix. |
| BH-07 / High | `best_of` filtering checked only Python integers. JSON `3.0` and strings `"3"`/`"3.0"` passed the gateway's compute restriction and were forwarded to runtimes that can coerce integer values. | In `app/params.py`, apply the greater-than-one check to numeric values and numeric strings. Preserve the existing explicit operator override. | Expanded `test_completions_allow_best_of_one_but_not_more`: four representations, with `best_of=1` still accepted. Three representations bypassed the policy before the fix. |
| BH-08 / Medium | Malformed embedding response shapes raised uncaught indexing/type errors; NaN and infinities passed through as vector elements. | In RAG `app/embeddings.py`, validate the response shape and finite numeric elements; normalize conversion failures to the existing `ValueError` error path. | `test_invalid_embedding_raises_value_error`, eight malformed/nonfinite response cases. |
| BH-09 / Medium | NaN/infinite reranker scores poisoned ordering and downstream JSON instead of invoking the existing retrieval fallback. | In RAG `app/reranker.py`, reject nonfinite scores and normalize invalid numeric conversions to `ValueError`. | `test_nonfinite_reranker_score_raises_value_error`, three scores in `tests/test_provider_validation.py`. |
| BH-10 / Medium | Console landing could send users to Get started despite older pending approvals. A filtered page can be empty while still carrying a continuation cursor. | In console `src/main.tsx`, follow continuation pages until a matching run is found or pagination ends; use the existing 100-item page size. Keep cancellation through the existing abort signal. | `landing follows empty filtered pages to pending approvals`: sign in, follow an empty page, reach Approvals and its action button. |
| BH-11 / Medium | A legitimate assistant message containing tool calls but no `content` crashed the run transcript renderer. Formatting `undefined` did not yield the string expected downstream. | In console `src/runs.tsx`, render absent/null content as empty text. | `tool-call messages without content keep the run detail readable`: expand transcript, verify timeline and assistant role, assert no page errors. |
| BH-12 / Medium | Python `chat_stream` continued reading after SSE `[DONE]`; trailing data could yield extra content or an error after a completed answer, and a still-open connection could prolong iteration. | Return at `[DONE]`, allowing the existing stream context manager to close the response; update its docstring. | `test_chat_stream_stops_at_done`: a valid delta, `[DONE]`, then an error event; only the delta is returned. |
| BH-13 / High | Helm allowed a separate batch worker with the process-local memory queue. Gateway and worker pods could never see each other's queued batches, even with shared object storage. | In the inference-gateway batch-worker template, reject an enabled worker unless `batch.store.backend` is `redis`. Preserve the existing shared-object-store requirement. | `test_batch_worker_requires_shared_queue`: worker + S3 + memory must fail rendering with a clear diagnostic. |
Gateway paths in the table are under `src/inference-gateway`; RAG paths are under `src/rag-service`. Tests accompany the fixes in those services, `sdk/python/tests`, the console's existing Playwright spec, and `scripts/tests/test_umbrella_chart.py`.
## Local verification
Native Windows checks used Python 3.12.14, the repository's pinned Python requirements, Node from `C:\nodejs`, installed Edge, and Helm from `C:\tools`. Service and Python SDK test environments were isolated because their pinned OpenTelemetry versions differ. All required dependency downloads succeeded; no network installation was blocked.
| Check | Result |
| --- | --- |
| Gateway pytest, excluding `tests/live` | An initial run reported 1,100 passed / 7 skipped. The repeat exited 1 with 1,099 passed, 7 skipped and one existing Git Bash timeout; isolated retry passed. See runner notes below. |
| RAG pytest | 140 passed. |
| Python SDK pytest | 203 passed, 3 Linux-container tests skipped on Windows. |
| TypeScript SDK build / lint / tests | Passed; 79 tests. |
| Console TypeScript/Vite build | Passed. |
| Console Playwright/Edge | 78 passed, including both new regressions; widths 360, 393 and 1440 tested. |
| Script unittest discovery | 47 passed, including 20 Helm tests. |
| Helm lint | All 9 charts passed after building the pinned Temporal and umbrella dependencies. |
| API contract / config contract / chart docs | All passed. |
| Python SDK mypy | Passed, 21 source files. |
| Repository lint / format / service mypy | Pre-existing failures, detailed below; not claimed green. |
| Repository hygiene | Three pre-existing failures, detailed below. |
| Diff whitespace / patch applicability | Passed. |
No Browser plugin was available. Rendered QA used the repository's Playwright tests with a temporary ignored configuration selecting `msedge`, `--lang=en-US`, and an independently managed Vite preview on port 4175. Commands ran from the console directory. Running from the repository root initially broke relative screenshot paths; this was an invocation error, not a code defect. On this Windows host, Edge's default number locale remained `de-DE` despite Playwright's context locale until `--lang=en-US` was supplied. No application locale behavior was changed.
Inspected screenshot evidence (ignored local artifacts):
- `.out/console-v1.0.0-rc.2/393-approvals.png`: readable phone layout and actionable approval controls.
- `.out/console-v1.0.0-rc.2/1440-run-step-output.png`: full timeline, expanded tool output and model transcript.
The release layout tests also check horizontal overflow, word wrapping, page titles and page errors. Screenshot inspection found no new clipping or overlap in these views. Regression and check logs are retained under ignored `.tools/`.
### Existing failures left unchanged
An untouched `HEAD` archive reproduced the same Ruff and service mypy failures:
- Ruff lint: `E501` at `src/inference-gateway/tests/test_approval_policies.py:67`.
- Ruff format: 32 existing files under `src` would be reformatted. New service test files conform to the formatter; no bulk formatting was performed.
- Gateway mypy: 26 errors in 13 files, including settings, Redis typing, JWKS options, managed keys, audit/workflow APIs and response typing. The Redis typing diagnostic in `response_store.py` is also present in `HEAD`; its line number moves with this fix.
- RAG mypy: the existing JWT options type error in `app/jwks.py:260`.
Repository hygiene reports unchanged problems: gateway/RAG `app/tracing.py` copies differ; `scripts/first-approved-run.py` lacks tracked executable mode `100755`; and `docs/team-settings.md` links to missing `runbooks/audit-chain.md`. These were not changed to hide a baseline failure. Full repository quality/validation is therefore not green. All regressions for the 13 fixes pass; the full gateway gate also showed the native runner instability below.
### Native runner results
One full gateway run reported 1,100 passing tests and 7 skips but emitted native Windows `_wmi_query` / access-violation diagnostics; its background process exit code was not retained. A repeat with explicit subprocess exit capture had empty stderr and exited 1: 1,099 passed, 7 skipped, and `test_sync_script.py::test_local_direct_apply_centrally_owns_namespaces` exceeded its existing 10-second Git Bash subprocess timeout. That unchanged test then passed alone in 8.68 seconds with exit 0. Its timeout was not increased. The gate is intermittent on this host and is not represented as a clean full-suite pass.
The seven gateway skips are six real PostgreSQL tests requiring `TEST_POSTGRES_DSN` and one approval/SDK integration test whose SDK package is absent from the gateway-only environment. The three SDK skips are Linux-container workspace-runner cases. These are explicit coverage limits, not passes.
Logs: `.tools/gateway-wmi-run.log`, `.tools/gateway-wmi-run.err`, `.tools/gateway-tests.log`, `.tools/gateway-tests.err`, `.tools/gateway-exit.txt`, and `.tools/sync-retry.log`.
A review patch at `.tools/bughunt.patch` includes tracked edits and all three new files. It was checked against the untouched `HEAD` snapshot. The changes already exist in the worktree.
## Caller checks: WSL, real services and devices
No repository folder blocked reading or writing. WSL execution is blocked in this sandbox and was not attempted. Full Bash/WSL orchestration, container/cluster recovery and physical-device checks are outstanding. Gateway shell unit fixtures did run through installed Git Bash. Local mocks do not replace the outstanding integration checks. No emulator or adb was started.
From the caller's WSL/Linux shell, with the documented validation toolchain installed:
```sh
cd /mnt/c/src/aw-BH
make toolchain-doctor TOOLCHAIN_PROFILE=strict
make validate-full
make coverage
```
`make validate-full` will still encounter the baseline quality/hygiene failures above until they are resolved separately. It is the remaining complete Bash/toolchain gate, including checks not reproduced natively here.
With Docker available, run the existing Compose integration gate:
```sh
make compose-up
make compose-smoke
make compose-down
```
On a disposable, configured local cluster with Temporal and the storage services available, run the existing recovery gates:
```sh
make workflow-upgrade-test
make workflow-helm-upgrade-test
make workflow-restore-drill
```
With `TEST_POSTGRES_DSN` exported for a disposable PostgreSQL database and the service environment prepared by the repository scripts, also run:
```sh
cd src/inference-gateway
PYTHONPATH=.:../../sdk/python .venv/bin/python -m pytest -q tests/test_postgres_integration.py tests/test_approval_policies.py
cd ../..
```
Run `make test-live-providers` only with the caller's configured provider endpoints and credentials. In addition to those scripted gates, exercise two real batch-worker replicas against Redis and S3: reclaim during a slow request, kill a worker while assembling mixed result files, and cancel/expire the final chunk. The new tests reproduce these transitions locally, but do not establish atomic fencing across every cross-process storage operation or quantify long-term orphan-part cleanup.
### Suspected device-only issues to exercise, not confirmed defects
There is no native Android or Capacitor lifecycle to rebuild or instrument. For the browser console on physical Android/iOS devices, the remaining risks are background/resume with expired authentication or interrupted networking, browser-back navigation between run details and lists, rotation and keyboard changes to the viewport, safe-area spacing, and reload after process death. Verify that pending operations refresh coherently and unsaved form state is understandable after recovery. Desktop viewport emulation does not prove these behaviors.
Screen-reader announcements, touch focus/target behavior with the virtual keyboard, and modal focus restoration also need device/assistive-technology validation. No device-specific failure is asserted without reproduction. WebGL/Canvas/audio resource checks and an APK rebuild are not applicable to this checkout.
