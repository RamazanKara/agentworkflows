# AgentWorkflows Chart

Cloud-first AgentWorkflows: gateway and console, Redis, Temporal, PostgreSQL and a workflow
worker in one namespace. Self-hosted models and retrieval are opt-in. See the
[Kubernetes install guide](../../../docs/install-kubernetes.md) for bootstrap credentials,
provider Secrets, image compatibility, upgrades and uninstalling.

<!-- chart-docs:start -->
## Values

| Value | Default |
| --- | --- |
| `bootstrapAdmin.existingSecret` | `agentworkflows-admin` |
| `budget-redis.enabled` | `true` |
| `budget-redis.fullnameOverride` | `budget-redis` |
| `budget-redis.namespace.create` | `false` |
| `budget-redis.networkPolicy.enabled` | `false` |
| `inference-gateway.adminConsole.cookieSecure` | `false` |
| `inference-gateway.adminConsole.enabled` | `true` |
| `inference-gateway.antiAffinity.enabled` | `false` |
| `inference-gateway.auth.enabled` | `true` |
| `inference-gateway.auth.keyRecords.existingSecret.key` | `key-records.json` |
| `inference-gateway.auth.keyRecords.existingSecret.name` | `agentworkflows-key-records` |
| `inference-gateway.auth.oidc.clientId` | `""` |
| `inference-gateway.auth.oidc.defaultRole` | `viewer` |
| `inference-gateway.auth.oidc.existingSecret.key` | `oidc-client-secret` |
| `inference-gateway.auth.oidc.existingSecret.name` | `""` |
| `inference-gateway.auth.oidc.issuer` | `""` |
| `inference-gateway.auth.oidc.projectClaim` | `project` |
| `inference-gateway.auth.oidc.redirectUrl` | `""` |
| `inference-gateway.auth.oidc.roleClaim` | `role` |
| `inference-gateway.auth.oidc.scopes` | `openid profile email` |
| `inference-gateway.auth.oidc.teamClaim` | `""` |
| `inference-gateway.budget.backend` | `redis` |
| `inference-gateway.budget.redisUrl` | `redis://budget-redis:6379/0` |
| `inference-gateway.deployment.create` | `false` |
| `inference-gateway.enabled` | `true` |
| `inference-gateway.fullnameOverride` | `inference-gateway` |
| `inference-gateway.ingress.annotations` | `{}` |
| `inference-gateway.ingress.className` | `""` |
| `inference-gateway.ingress.enabled` | `false` |
| `inference-gateway.ingress.host` | `""` |
| `inference-gateway.ingress.tls.enabled` | `false` |
| `inference-gateway.ingress.tls.secretName` | `""` |
| `inference-gateway.keda.enabled` | `false` |
| `inference-gateway.namespace.create` | `false` |
| `inference-gateway.networkPolicy.enabled` | `false` |
| `inference-gateway.podDisruptionBudget.enabled` | `false` |
| `inference-gateway.podDisruptionBudget.minAvailable` | `1` |
| `inference-gateway.receipts.enabled` | `true` |
| `inference-gateway.redis.existingSecret.key` | `url` |
| `inference-gateway.redis.existingSecret.name` | `""` |
| `inference-gateway.replicaCount` | `1` |
| `inference-gateway.resources.limits.cpu` | `500m` |
| `inference-gateway.resources.limits.memory` | `512Mi` |
| `inference-gateway.resources.requests.cpu` | `100m` |
| `inference-gateway.resources.requests.memory` | `128Mi` |
| `inference-gateway.responseCache.redisUrl` | `redis://budget-redis:6379/1` |
| `inference-gateway.routing.policy.enabled` | `true` |
| `inference-gateway.routing.policy.models` | `[{"aliases": ["demo-openai"], "backend": "openai", "connection": {"baseUrl": "https://api.openai.com/v1", "credential...` |
| `inference-gateway.runtime.allowedModels` | `["research", "anthropic"]` |
| `inference-gateway.runtime.modelId` | `research` |
| `inference-gateway.runtime.ollamaBaseUrl` | `http://ollama:11434` |
| `inference-gateway.runtime.vllmBaseUrl` | `http://vllm:8000` |
| `inference-gateway.sandboxPolicy.policy.enabled` | `true` |
| `inference-gateway.sandboxPolicy.policy.policies` | `[{"budgets": {"costLimitUsd": 50, "estimatedTokenLimit": 200000}, "projects": ["default"], "providerCredentials": {"a...` |
| `inference-gateway.serviceMonitor.enabled` | `false` |
| `inference-gateway.traceability.auditChainStore.backend` | `redis` |
| `inference-gateway.traceability.auditChainStore.redisUrl` | `redis://budget-redis:6379/0` |
| `inference-gateway.traceability.defaultSandboxId` | `default` |
| `inference-gateway.workflows.temporalAddress` | `temporal-frontend:7233` |
| `networkPolicy.enabled` | `false` |
| `networkPolicy.externalEgress` | `[]` |
| `networkPolicy.ingressControllerNamespace` | `ingress-nginx` |
| `ollama.enabled` | `false` |
| `ollama.fullnameOverride` | `ollama` |
| `ollama.namespace.create` | `false` |
| `ollama.networkPolicy.enabled` | `false` |
| `providers.anthropic.existingSecret` | `""` |
| `providers.openai.existingSecret` | `""` |
| `qdrant-vector-store.enabled` | `false` |
| `qdrant-vector-store.fullnameOverride` | `qdrant-vector-store` |
| `qdrant-vector-store.namespace.create` | `false` |
| `qdrant-vector-store.networkPolicy.enabled` | `false` |
| `qdrant-vector-store.serviceMonitor.enabled` | `false` |
| `rag-service.enabled` | `false` |
| `rag-service.fullnameOverride` | `rag-service` |
| `rag-service.namespace.create` | `false` |
| `rag-service.networkPolicy.enabled` | `false` |
| `rag-service.serviceMonitor.enabled` | `false` |
| `vllm.enabled` | `false` |
| `vllm.fullnameOverride` | `vllm` |
| `vllm.namespace.create` | `false` |
| `vllm.networkPolicy.enabled` | `false` |
| `vllm.serviceMonitor.enabled` | `false` |
| `workflows.enabled` | `true` |
| `workflows.postgres.enabled` | `true` |
| `workflows.postgres.existingSecret` | `temporal-postgres-auth` |
| `workflows.temporal.web.enabled` | `false` |
| `workflows.worker.existingSecret` | `workflow-gateway-key` |
| `workflows.worker.gatewayUrl` | `http://inference-gateway:8080` |
| `workflows.worker.team` | `default` |
<!-- chart-docs:end -->
