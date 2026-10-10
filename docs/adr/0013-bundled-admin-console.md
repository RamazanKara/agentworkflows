# 0013. Opt-in bundled read-only admin console

- Status: Accepted
- Date: 2026-07-04
- Deciders: Platform maintainer

Milestone 5 update (2026-10-07): the team workflow console supersedes the single-file,
read-only implementation below. It uses a locked React/Vite build bundled in the same
gateway image and the same opt-in mount, served entirely by the gateway. Existing
authenticated APIs enforce roles for mutations. Credentials are held in memory and cleared
on reload/sign-out. See the [console guide](../workflows.md#web-console); the original
decision remains below as historical context.

## Context

The platform ships the data layer a console builds on (`/v1/usage`, `/v1/sandbox/budget`,
`/v1/models`, Prometheus metrics, the client SDK). The requirement is a lightweight read-only
console so an operator can see a sandbox's usage, budget, and approved models directly in the
browser.

Constraints shape the design. At the time of this decision the repo deliberately kept a
**backend-only toolchain** and valued a lean, hash-pinned, dependency-light footprint. A browser
single-page app calling the gateway from a different origin would need CORS on the gateway. The
console keeps the existing auth model and a light stack.

## Decision

Ship a **self-contained static console**: a single HTML file with inline CSS and vanilla
JavaScript, served without a framework, build step, or external/CDN resources (CSP-safe). The
operator enters a gateway API key and sandbox id in the page; the console fetches the existing
read-only endpoints and renders health, usage, budget, and the model allowlist. It is
**read-only**.

The gateway optionally serves it at `/console` via a Starlette static mount, **off by default**
(`ADMIN_CONSOLE_ENABLED`). Serving it **same-origin** with the API means the browser's fetches
are same-origin, with a single deployment and no CORS configuration. `/console` serves static
HTML publicly; the API calls the page makes carry the operator's key. Starlette's built-in
`StaticFiles` serves it, and the console lives inside the existing gateway image
(`app/console/`).

## Consequences

- Operators get a zero-dependency read-only console by flipping one flag; with the flag off, the
  gateway serves only its API. The console is an authenticated caller of the governed endpoints,
  so it sees exactly what the supplied API key allows.
- Bundling the UI into the API image gives a single same-origin deployment. The console stays
  read-only and tiny (one HTML file).
- The API key is entered client-side and held only in the browser session (`sessionStorage`); the
  console itself carries no secrets.

## Alternatives considered

- **A React/Vue SPA with a build pipeline.** Richer. For a read-only dashboard at the time, the
  single HTML file kept the backend-only, fully hash-pinned toolchain. (The Milestone 5 console
  above later adopted a locked React/Vite build.)
- **A separate nginx-served static site (its own chart).** Cleaner separation. The same-origin
  gateway mount was chosen because it avoids cross-origin calls and extra path routing and keeps
  one chart.
- **Server-rendered HTML from the gateway.** A static page calling the JSON APIs was chosen so the
  gateway stays API-only in spirit.
- **Grafana + curl only.** Grafana owns metrics dashboards; this console adds a lightweight
  per-sandbox usage/budget/model view. It is **opt-in**, so operators choose whether to serve it.
