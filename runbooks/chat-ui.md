# End-User Chat UI Runbook (Open WebUI)

Use this runbook to put a self-hosted end-user chat UI in front of the inference gateway so
humans can type into a chat box, while every request still flows through the gateway's auth,
model allowlist, admission, budget, and audit controls.

## Scope

The platform ships an opt-in read-only operator console at `/console`. A human-facing multi-user
chat UI is an **operator-owned example** that runs in front of the gateway (see the
[product boundary](https://github.com/RamazanKara/agentworkflows/blob/main/docs/scope-and-non-goals.md)).
[Open WebUI](https://github.com/open-webui/open-webui) is the common choice and is used here;
any OpenAI-compatible chat frontend works. The platform ships a copy-adapt manifest at
[`docs/examples/open-webui.yaml`](https://github.com/RamazanKara/agentworkflows/blob/main/docs/examples/open-webui.yaml)
(Deployment + Service + NetworkPolicy + config) and this runbook; you own the image pinning,
storage, ingress/TLS, and identity wiring.

## Architecture

    human ──HTTPS+SSO──▶ Open WebUI ──OpenAI /v1 + gateway API key──▶ inference gateway ──▶ runtime
                          (chat-ui ns)                                  (inference ns)

The UI is just another gateway client. It points its OpenAI-compatible base URL at the gateway's
`/v1` and presents a gateway API key as a bearer token. All UI traffic goes through the gateway: the
UI namespace runs default-deny egress that allows only DNS and the gateway (port 8080), which
keeps the runtimes, Redis, and the internet out of the UI's direct reach.

## 1. Machine auth: how the UI authenticates to the gateway

Open WebUI authenticates to the gateway with a single **gateway API key** sent as
`Authorization: Bearer <key>` (the gateway accepts a bearer token equivalently to the `X-API-Key`
header; see [API access](api-access.md)).

1. Mint a gateway key and add its SHA-256 to the gateway's `auth.apiKeyHashes` (or issue a key
   **record** with scopes/expiry/sandbox binding; see [API access](api-access.md)).
2. Put the **plaintext** key in the `open-webui-gateway-key` Secret, sourced from your secret
   manager via External Secrets.
3. Set `OPENAI_API_BASE_URL` to
   `http://inference-gateway-inference-gateway.inference.svc.cluster.local:8080/v1` (the bundled
   chart's in-cluster Service DNS) and `OPENAI_API_KEY` from the Secret. Both are wired in the
   example manifest.

The UI offers the models the gateway allow-lists; its model picker reflects `GET /v1/models`.

## 2. Choose the X-Sandbox-ID mapping

The gateway attributes budgets, rate limits, and audit records **per `X-Sandbox-ID`**. Open
WebUI presents **one** bearer token for all of its users, so by default **all UI traffic lands in
a single sandbox**. Choose deliberately:

| Option | What you get | How |
| --- | --- | --- |
| (a) One shared sandbox | The whole UI is one budgeted/audited sandbox. Simplest; correct when the UI is one team's shared workspace. | Bind the UI's API key to a sandbox with a **key record** (`record.sandbox`), or set the gateway `DEFAULT_SANDBOX_ID`. The gateway then forces every UI request into that sandbox. |
| (b) Fixed per-connection sandbox | A fixed non-default sandbox for the UI. | Newer Open WebUI supports custom headers per OpenAI connection: add `X-Sandbox-ID: <sandbox>`. |
| (c) Per-user sandboxes | Each human is metered and audited separately. | Front the gateway with a small header-stamping proxy that maps the authenticated Open WebUI user (from its OIDC session) to an `X-Sandbox-ID`. This option gives per-user budgets and attribution. |

Option (a) via a **sandbox-bound key record** is recommended for most deployments: it is
fail-closed (the gateway rebinds every UI request to the record's sandbox, whatever header it
sends) and runs without an extra proxy.

## 3. Human auth: SSO into the UI

Put the UI behind HTTPS and human SSO: the gateway's API key authenticates the *UI process*, and
SSO authenticates the *person* using it. Wire Open WebUI's OIDC to the **same IdP** you use for the
platform dashboards.

- The Grafana and Argo CD SSO templates in [API access](api-access.md) (OIDC via Keycloak/Auth0/
  Okta/Entra ID/Google Workspace) are the pattern to mirror: resolve the issuer/auth/token/JWKS
  URLs from the IdP discovery document, source the client secret from your secret manager, and
  map an IdP group to admin.
- Open WebUI reads standard OIDC settings (issuer/client-id/client-secret/scopes) from its
  environment; set them the same way and put an Ingress with TLS in front. The operator owns human
  SSO for this UI ([Security overview](https://github.com/RamazanKara/agentworkflows/blob/main/docs/security-overview.md)).

If you also enable gateway JWT auth with a tenant claim, option (c) above can forward the user's
verified token straight through and let the gateway derive the sandbox from the JWT tenant claim,
the strongest per-user attribution, based on the verified token.

## 4. Apply and validate

    # Adapt docs/examples/open-webui.yaml first (image digest, key Secret, sandbox choice, Ingress).
    kubectl apply -f docs/examples/open-webui.yaml

    # Confirm the UI reaches the gateway and sees only allow-listed models:
    kubectl -n chat-ui exec deploy/open-webui -- \
      wget -qO- --header="Authorization: Bearer $KEY" \
      http://inference-gateway-inference-gateway.inference.svc.cluster.local:8080/v1/models

Then open the UI (through your Ingress), sign in via SSO, and send a chat. Verify in the gateway
audit stream that the request is attributed to the expected sandbox and that the budget headers
(`x-ratelimit-*`) reflect that sandbox's allowance.

## Hardening checklist

- [ ] Image pinned to a scanned, mirrored **digest**.
- [ ] Gateway key sourced from the secret manager; its hash allow-listed; rotated on the same
      schedule as other keys ([API access](api-access.md)).
- [ ] Sandbox story chosen (a/b/c) and budgets sized for that sandbox ([Budget controls](budget-controls.md)).
- [ ] Default-deny egress enforced (requires a policy-capable CNI); UI can reach only DNS + gateway.
- [ ] HTTPS Ingress + human SSO in front of the UI.
- [ ] Persistent volume for the UI's data (the example uses `emptyDir`).

## Related runbooks

- [API access](api-access.md): gateway key model, rotation, and the Grafana/Argo CD SSO templates.
- [Budget controls](budget-controls.md): sizing the sandbox the UI runs in.
- [External / managed stores](external-managed-stores.md): HA backing stores for a production UI deployment.
