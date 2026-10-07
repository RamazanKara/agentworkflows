# Workflows Chart

Temporal execution, dedicated PostgreSQL, and a governed Python workflow worker.

<!-- chart-docs:start -->
## Values

| Value | Default |
| --- | --- |
| `postgres.existingSecret` | `temporal-postgres-auth` |
| `postgres.image` | `postgres:16-alpine` |
| `postgres.storage` | `10Gi` |
| `temporal.admintools.enabled` | `false` |
| `temporal.fullnameOverride` | `temporal` |
| `temporal.schema.useHelmHooks` | `false` |
| `temporal.server.config.namespaces.create` | `true` |
| `temporal.server.config.namespaces.namespace` | `[{"name": "default", "retention": "30d"}]` |
| `temporal.server.config.namespaces.useHelmHooks` | `false` |
| `temporal.server.config.persistence.datastores.default.sql.connectAddr` | `temporal-postgres:5432` |
| `temporal.server.config.persistence.datastores.default.sql.databaseName` | `temporal` |
| `temporal.server.config.persistence.datastores.default.sql.existingSecret` | `temporal-postgres-auth` |
| `temporal.server.config.persistence.datastores.default.sql.maxConns` | `5` |
| `temporal.server.config.persistence.datastores.default.sql.maxIdleConns` | `5` |
| `temporal.server.config.persistence.datastores.default.sql.pluginName` | `postgres12` |
| `temporal.server.config.persistence.datastores.default.sql.user` | `temporal` |
| `temporal.server.config.persistence.datastores.visibility.sql.connectAddr` | `temporal-postgres:5432` |
| `temporal.server.config.persistence.datastores.visibility.sql.databaseName` | `temporal_visibility` |
| `temporal.server.config.persistence.datastores.visibility.sql.existingSecret` | `temporal-postgres-auth` |
| `temporal.server.config.persistence.datastores.visibility.sql.maxConns` | `5` |
| `temporal.server.config.persistence.datastores.visibility.sql.maxIdleConns` | `5` |
| `temporal.server.config.persistence.datastores.visibility.sql.pluginName` | `postgres12` |
| `temporal.server.config.persistence.datastores.visibility.sql.user` | `temporal` |
| `temporal.server.config.persistence.numHistoryShards` | `4` |
| `temporal.server.replicaCount` | `1` |
| `temporal.server.resources.limits.cpu` | `1` |
| `temporal.server.resources.limits.memory` | `1Gi` |
| `temporal.server.resources.requests.cpu` | `100m` |
| `temporal.server.resources.requests.memory` | `256Mi` |
| `temporal.web.resources.limits.cpu` | `500m` |
| `temporal.web.resources.limits.memory` | `256Mi` |
| `temporal.web.resources.requests.cpu` | `50m` |
| `temporal.web.resources.requests.memory` | `64Mi` |
| `worker.existingSecret` | `workflow-gateway-key` |
| `worker.gatewayUrl` | `http://inference-gateway.inference.svc.cluster.local:8080` |
| `worker.image` | `ghcr.io/ramazankara/agentworkflows/workflow-worker:v0.1.0` |
| `worker.resources.limits.cpu` | `1` |
| `worker.resources.limits.memory` | `512Mi` |
| `worker.resources.requests.cpu` | `100m` |
| `worker.resources.requests.memory` | `128Mi` |
| `worker.workspaces` | `[]` |
<!-- chart-docs:end -->
