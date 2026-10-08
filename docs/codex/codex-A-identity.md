Repository: RamazanKara/agentworkflows, branch main. Pull first; commit and push straight to main when green. Read docs/PRODUCT-GAPS.md (gaps 1 and 2) before starting.

Goal: make sign-in and team membership real product features instead of YAML + redeploy. Keep every existing auth path working (API_KEY_SHA256S, API_KEY_RECORDS_PATH file, JWT/JWKS); the new pieces are additive and off unless configured, except the managed key store which is always available to admins.

1. Managed API keys (gateway, src/inference-gateway/app):
   - New Redis-backed key store next to the existing state (reuse the gateway's Redis client, schema-migration pattern in app/state_migrations.py / app/migrations).
   - Store only the SHA-256 of each key plus: key_id, team (sandbox), role (admin|builder|approver|viewer), project (optional), name/label, created_by, created_at, expires_at (optional), last_used_at (updated at most once per minute per key), revoked_at.
   - Auth lookup order: flat allowlist, key-records file, managed store, JWT. A managed key resolves to exactly the same principal shape as a key-records entry (sandbox binding, role, project), so all role checks in app/teams.py keep working unchanged.
   - Admin-only API, scoped to the caller's own team: `GET /v1/team/keys`, `POST /v1/team/keys` (returns the plaintext key exactly once, format `aw_` + 40 url-safe chars), `PATCH /v1/team/keys/{key_id}` (name, role, project, expires_at), `DELETE /v1/team/keys/{key_id}` (revoke; effective on the next request on every replica). An admin cannot revoke or demote the key they are currently using. Every create/change/revoke writes an audit receipt via the existing audit chain.
   - CLI in sdk/python/agentworkflows/cli.py: `agentworkflows keys list|create|update|revoke`.

2. Console sign-in with the company identity provider (OIDC):
   - Settings: OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET (optional for public clients), OIDC_REDIRECT_URL, OIDC_SCOPES, plus claim mapping for team, role and project (default to the existing JWT_TENANT_CLAIM / role / project claims), and OIDC_DEFAULT_ROLE (default viewer) for users whose token carries no role.
   - Endpoints: `GET /v1/auth/config` (public: which sign-in methods are enabled, never secrets), `GET /v1/auth/login` (authorization-code flow with PKCE + state + nonce, redirect), `GET /v1/auth/callback`, `POST /v1/auth/logout`, `GET /v1/auth/session`.
   - On callback validate the ID token against the issuer's JWKS (reuse app/jwks.py), then create a server-side session in Redis and set an HttpOnly, Secure (configurable off for http://localhost), SameSite=Lax cookie. Session TTL 12 h sliding, absolute max 7 days. Cookie-authenticated non-GET requests require a CSRF header (double-submit token returned by /v1/auth/session).
   - Also let a pasted API key create the same cookie session (`POST /v1/auth/session` with the key), so console users survive reloads without OIDC configured. The key itself is never stored in the browser.
   - The bearer/X-API-Key paths stay unchanged for automation.

3. Console (src/inference-gateway/console), minimal wiring only, the design pass happens afterwards:
   - api.ts: send `credentials: 'include'` and the CSRF header; on load call /v1/auth/session and skip the sign-in screen when a session exists.
   - Sign-in screen: "Sign in with <provider name>" button when /v1/auth/config says OIDC is enabled; keep the API key form below it.
   - New admin page "Members & keys" (route #keys): table of keys (name, role, project, last used, expires, status), create form that shows the new key once with a copy button, revoke with confirmation.
   - Playwright tests in tests/console.spec.ts with the existing route mocks for: session restore after reload, OIDC button visibility, create-shows-once, revoke.

4. Compose demo: keep `local-development-only` working. Add an optional local OIDC provider overlay only if it is small (e.g. a Dex container with one static user) and document it in docs/workflows.md; otherwise document configuration against Google/Okta/Entra.

5. Tests: unit tests for the store, auth order, revocation across two app instances sharing Redis (fakeredis is fine), expiry, role enforcement, CSRF, PKCE state mismatch, ID-token validation failures. `make lint` and `make test-gateway` must pass (they include the console build). Update docs/workflows.md (Teams section: replace "there is no membership UI"), docs/sdk-reference.md for the CLI, and CHANGELOG.md under a new "v0.5.0 - unreleased" heading.

Report: what changed, test output summary, anything not verified.
