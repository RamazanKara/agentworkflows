"""Render regressions; build chart dependencies and put Helm on PATH before running."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy/charts/agentworkflows"
HELM = shutil.which("helm")
TLS = {
    "enabled": True,
    "className": "nginx",
    "host": "agents.example.com",
    "tls": {"enabled": True, "secretName": "agents-tls"},
}
OIDC = {
    "issuer": "https://identity.example.com",
    "clientId": "agents",
    "existingSecret": {"name": "agents-oidc", "key": "client-secret"},
    "scopes": "openid profile email groups",
    "teamClaim": "team",
    "roleClaim": "access_role",
    "projectClaim": "project_id",
    "defaultRole": "viewer",
}


def resource(docs, kind, name):
    return next(doc for doc in docs if doc["kind"] == kind and doc["metadata"]["name"] == name)


def gateway_env(docs):
    deployment = resource(docs, "Deployment", "inference-gateway")
    return {item["name"]: item for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}


@unittest.skipUnless(
    HELM and yaml and (CHART / "charts").exists(), "Helm, PyYAML and built chart dependencies required"
)
class UmbrellaChartTests(unittest.TestCase):
    def test_batch_worker_requires_shared_queue(self):
        values = {"inference-gateway": {"batch": {
            "enabled": True, "worker": {"enabled": True},
            "objectStore": {"backend": "s3"}, "store": {"backend": "memory"},
        }}}
        self.assertIn("batch.store.backend must be redis", self.render(values, valid=False))

    def test_workflow_secrets_and_otlp_are_explicitly_enabled(self):
        defaults = gateway_env(self.render())
        self.assertNotIn("WORKFLOW_SECRETS_KEY", defaults)
        self.assertEqual(defaults["OTEL_METRICS_ENABLED"]["value"], "false")
        env = gateway_env(self.render({"inference-gateway": {
            "workflowSecrets": {"existingSecret": {"name": "workflow-encryption", "key": "fernet-key"}},
            "observability": {"metrics": {"enabled": True}, "tracing": {
                "enabled": True, "otlpEndpoint": "http://collector:4318",
            }},
        }}))
        self.assertEqual(env["WORKFLOW_SECRETS_KEY"]["valueFrom"]["secretKeyRef"], {
            "name": "workflow-encryption", "key": "fernet-key",
        })
        self.assertEqual(env["OTEL_METRICS_ENABLED"]["value"], "true")
        self.assertEqual(env["OTEL_TRACING_ENABLED"]["value"], "true")
        self.assertEqual(env["OTEL_EXPORTER_OTLP_ENDPOINT"]["value"], "http://collector:4318")

    def test_kind_quickstart_uses_candidate_images_and_local_model(self):
        values = yaml.safe_load((CHART / "values-quickstart.yaml").read_text())
        docs = self.render(values)
        gateway = resource(docs, "Deployment", "inference-gateway")
        self.assertEqual(gateway["spec"]["template"]["spec"]["containers"][0]["image"],
                         "agentworkflows-gateway:quickstart")
        self.assertEqual(gateway_env(docs)["COMPOSE_PROVIDER_KEY"]["valueFrom"]["secretKeyRef"]["name"],
                         "quickstart-model-key")
        model = values["inference-gateway"]["routing"]["policy"]["models"][0]
        self.assertTrue(model["simulated"])
        self.assertEqual(model["connection"]["baseUrl"], "http://cloud-fake:8000/openai/v1")

    def test_oidc_group_mapping_serialization_and_schema(self):
        mapping = {"default": {"Engineering / Reviewers": "approver", "Builders": "builder"}}
        values = {"inference-gateway": {"ingress": TLS, "auth": {"oidc": {
            **OIDC, "groupsClaim": "company.groups", "groupRoleMappings": mapping,
        }}}}
        env = gateway_env(self.render(values))
        self.assertEqual(env["OIDC_GROUPS_CLAIM"]["value"], "company.groups")
        self.assertEqual(json.loads(env["OIDC_GROUP_ROLE_MAPPINGS"]["value"]), mapping)
        self.assertEqual(json.loads(gateway_env(self.render())["OIDC_GROUP_ROLE_MAPPINGS"]["value"]), {})
        for invalid in ({"default": {"group": "owner"}}, {"Default": {}}, {"default": {"": "viewer"}}):
            values["inference-gateway"]["auth"]["oidc"]["groupRoleMappings"] = invalid
            self.assertIn("schema", self.render(values, valid=False))

    def test_external_gateway_postgres_secret_and_defaults(self):
        default = gateway_env(self.render())
        self.assertEqual(default["STORAGE_BACKEND"]["value"], "redis")
        values = {"inference-gateway": {"storage": {
            "backend": "postgres", "postgres": {"existingSecret": {"name": "gateway-db", "key": "url"}},
        }}}
        docs = self.render(values)
        env = gateway_env(docs)
        self.assertEqual(env["STORAGE_POSTGRES_DSN"]["valueFrom"]["secretKeyRef"], {
            "name": "gateway-db", "key": "url",
        })
        self.assertFalse(any(doc["metadata"]["name"] == "inference-gateway-postgres" for doc in docs))
        self.assertIn("existingSecret.name", self.render({"inference-gateway": {"storage": {
            "backend": "postgres",
        }}}, valid=False))

    def test_bundled_gateway_postgres_and_network_policy(self):
        values = {"networkPolicy": {"enabled": True}, "inference-gateway": {"storage": {
            "backend": "postgres", "postgres": {"bundled": {"enabled": True}},
        }}}
        docs = self.render(values)
        name = "inference-gateway-postgres"
        env = gateway_env(docs)
        self.assertEqual(env["STORAGE_POSTGRES_DSN"]["valueFrom"]["secretKeyRef"], {"name": name, "key": "dsn"})
        self.assertIn("dsn", resource(docs, "Secret", name)["data"])
        self.assertEqual(resource(docs, "StatefulSet", name)["spec"]["replicas"], 1)
        self.assertEqual(resource(docs, "NetworkPolicy", name)["spec"]["ingress"][0]["ports"][0]["port"], 5432)
        values["inference-gateway"]["storage"]["postgres"]["existingSecret"] = {"name": "external"}
        self.assertIn("no external", self.render(values, valid=False))

    def render(self, values=None, *, valid=True):
        with tempfile.TemporaryDirectory() as directory:
            overrides = Path(directory) / "values.yaml"
            overrides.write_text(yaml.safe_dump(values or {}), encoding="utf-8")
            result = subprocess.run(
                [HELM, "template", "aw", str(CHART), "--namespace", "aw", "-f", str(overrides)],
                capture_output=True,
                text=True,
                check=False,
            )
        if not valid:
            self.assertNotEqual(result.returncode, 0)
            return result.stderr
        self.assertEqual(result.returncode, 0, result.stderr)
        return [doc for doc in yaml.safe_load_all(result.stdout) if doc]

    def test_default_install(self):
        docs = self.render()
        env = gateway_env(docs)
        self.assertEqual(env["SESSION_COOKIE_SECURE"]["value"], "false")
        self.assertEqual(env["OIDC_ISSUER"]["value"], "")
        self.assertEqual(env["OIDC_REDIRECT_URL"]["value"], "")
        self.assertEqual(env["SANDBOX_BUDGET_REDIS_URL"]["value"], "redis://budget-redis:6379/0")
        self.assertEqual(resource(docs, "Deployment", "inference-gateway")["spec"]["replicas"], 1)
        self.assertTrue(resource(docs, "StatefulSet", "temporal-postgres"))
        self.assertTrue(resource(docs, "Deployment", "budget-redis"))
        self.assertFalse(any(doc["kind"] in {"Ingress", "NetworkPolicy"} for doc in docs))
        self.assertFalse(any(
            doc["kind"] == "PodDisruptionBudget" and doc["metadata"]["name"] == "inference-gateway"
            for doc in docs
        ))

    def test_tls_ingress_forces_secure_cookie_without_enabling_oidc(self):
        docs = self.render({"inference-gateway": {"ingress": TLS, "adminConsole": {"cookieSecure": False}}})
        ingress = resource(docs, "Ingress", "inference-gateway")["spec"]
        self.assertEqual(ingress["ingressClassName"], "nginx")
        self.assertEqual(ingress["tls"], [{"hosts": ["agents.example.com"], "secretName": "agents-tls"}])
        path = ingress["rules"][0]["http"]["paths"][0]
        self.assertEqual(path["path"], "/")
        self.assertEqual(path["backend"]["service"], {"name": "inference-gateway", "port": {"number": 8080}})
        self.assertEqual(gateway_env(docs)["SESSION_COOKIE_SECURE"]["value"], "true")
        self.assertEqual(gateway_env(docs)["OIDC_REDIRECT_URL"]["value"], "")

    def test_http_ingress_keeps_cookie_setting(self):
        docs = self.render({"inference-gateway": {"ingress": {"enabled": True, "host": "agents.example.com"}}})
        self.assertNotIn("tls", resource(docs, "Ingress", "inference-gateway")["spec"])
        self.assertEqual(gateway_env(docs)["SESSION_COOKIE_SECURE"]["value"], "false")

    def test_cert_manager_annotations(self):
        annotations = {"cert-manager.io/cluster-issuer": "letsencrypt"}
        docs = self.render({"inference-gateway": {"ingress": {**TLS, "annotations": annotations}}})
        self.assertEqual(resource(docs, "Ingress", "inference-gateway")["metadata"]["annotations"], annotations)

    def test_oidc_secret_claims_and_derived_callback(self):
        docs = self.render({"inference-gateway": {"ingress": TLS, "auth": {"oidc": OIDC}}})
        env = gateway_env(docs)
        for key, value in {
            "OIDC_ISSUER": OIDC["issuer"], "OIDC_CLIENT_ID": "agents",
            "OIDC_REDIRECT_URL": "https://agents.example.com/v1/auth/callback",
            "OIDC_SCOPES": "openid profile email groups", "OIDC_TEAM_CLAIM": "team",
            "OIDC_ROLE_CLAIM": "access_role", "OIDC_PROJECT_CLAIM": "project_id", "OIDC_DEFAULT_ROLE": "viewer",
        }.items():
            self.assertEqual(env[key]["value"], value)
        self.assertNotIn("value", env["OIDC_CLIENT_SECRET"])
        self.assertEqual(env["OIDC_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"], OIDC["existingSecret"])

    def test_explicit_oidc_callback_is_preserved(self):
        callback = "https://other.example.com/v1/auth/callback"
        docs = self.render({"inference-gateway": {
            "ingress": TLS, "auth": {"oidc": {**OIDC, "redirectUrl": callback}},
        }})
        self.assertEqual(gateway_env(docs)["OIDC_REDIRECT_URL"]["value"], callback)

    def test_ha_selectors_and_resources(self):
        docs = self.render({"inference-gateway": {
            "replicaCount": 2, "antiAffinity": {"enabled": True}, "podDisruptionBudget": {"enabled": True},
        }})
        deployment = resource(docs, "Deployment", "inference-gateway")["spec"]
        self.assertEqual(deployment["replicas"], 2)
        pod = deployment["template"]["spec"]
        preference = pod["affinity"]["podAntiAffinity"]["preferredDuringSchedulingIgnoredDuringExecution"][0]
        self.assertEqual(preference["weight"], 100)
        term = preference["podAffinityTerm"]
        self.assertEqual(term["topologyKey"], "kubernetes.io/hostname")
        self.assertEqual(term["labelSelector"], deployment["selector"])
        pdb = resource(docs, "PodDisruptionBudget", "inference-gateway")["spec"]
        self.assertEqual(pdb["minAvailable"], 1)
        self.assertEqual(pdb["selector"], deployment["selector"])
        self.assertEqual(pod["containers"][0]["resources"], {
            "requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "500m", "memory": "512Mi"},
        })

    def test_network_policy_peers_and_ports(self):
        docs = self.render({"networkPolicy": {
            "enabled": True, "ingressControllerNamespace": "edge",
            "externalEgress": [{"cidr": "203.0.113.10/32", "port": 443}],
        }})
        policy = resource(docs, "NetworkPolicy", "inference-gateway")["spec"]
        ingress = policy["ingress"][0]
        self.assertEqual(ingress["from"], [
            {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "edge"}}},
            {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "workflow-worker"}}},
        ])
        self.assertEqual(ingress["ports"], [{"protocol": "TCP", "port": "http"}])
        rules = policy["egress"]
        self.assertEqual(len(rules), 5)
        self.assertEqual(rules[0]["to"], [{
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
            "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
        }])
        for rule, name, port in zip(
            rules[1:4], ("budget-redis", "temporal", "research-tools"), (6379, 7233, 8000), strict=True
        ):
            self.assertEqual(rule["to"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/name"], name)
            self.assertEqual(rule["ports"], [{"protocol": "TCP", "port": port}])
        self.assertEqual(rules[2]["to"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/component"], "frontend")
        self.assertEqual(rules[-1], {
            "to": [{"ipBlock": {"cidr": "203.0.113.10/32"}}], "ports": [{"protocol": "TCP", "port": 443}],
        })
        redis = resource(docs, "NetworkPolicy", "budget-redis")["spec"]
        self.assertEqual(redis["egress"], [])
        self.assertEqual(redis["ingress"][0]["from"], [{"podSelector": policy["podSelector"]}])

    def test_external_redis_references_every_store(self):
        secret = {"name": "managed-redis", "key": "connection-url"}
        docs = self.render({
            "budget-redis": {"enabled": False}, "inference-gateway": {"redis": {"existingSecret": secret}},
        })
        env = gateway_env(docs)
        for name in (
            "SANDBOX_BUDGET_REDIS_URL", "AUDIT_CHAIN_STORE_REDIS_URL", "RESPONSE_CACHE_REDIS_URL",
            "BATCH_REDIS_URL", "RESPONSES_REDIS_URL",
        ):
            self.assertNotIn("value", env[name])
            self.assertEqual(env[name]["valueFrom"]["secretKeyRef"], secret)
        self.assertFalse(any(doc["metadata"]["name"] in {"budget-redis", "managed-redis"} for doc in docs))

    def test_external_redis_batch_processor(self):
        secret = {"name": "managed-redis", "key": "url"}
        docs = self.render({"inference-gateway": {
            "redis": {"existingSecret": secret},
            "batch": {
                "enabled": True, "store": {"backend": "redis"},
                "objectStore": {"backend": "s3", "s3": {"bucket": "batches"}},
            },
        }})
        batch = resource(docs, "Deployment", "inference-gateway-batch-processor")
        env = {item["name"]: item for item in batch["spec"]["template"]["spec"]["containers"][0]["env"]}
        self.assertEqual(env["BATCH_REDIS_URL"]["valueFrom"]["secretKeyRef"], secret)

    def test_external_postgres_omits_bundled_database_and_generated_secret(self):
        sql = {
            "connectAddr": "postgres.example.com:5432", "user": "aw",
            "existingSecret": "managed-postgres", "createDatabase": False,
        }
        docs = self.render({"workflows": {
            "postgres": {"enabled": False},
            "temporal": {"server": {"config": {"persistence": {"datastores": {
                "default": {"sql": {**sql, "databaseName": "aw_history"}},
                "visibility": {"sql": {**sql, "databaseName": "aw_visibility"}},
            }}}}},
        }})
        self.assertFalse(any(
            doc["metadata"]["name"] in {"temporal-postgres", "temporal-postgres-auth", "managed-postgres"}
            for doc in docs
        ))
        config = resource(docs, "ConfigMap", "temporal-config")["data"]["config_template.yaml"]
        for expected in ("postgres.example.com:5432", "aw_history", "aw_visibility"):
            self.assertIn(expected, config)
        job = next(doc for doc in docs if doc["kind"] == "Job" and "schema" in doc["metadata"]["name"])
        containers = job["spec"]["template"]["spec"]["initContainers"]
        self.assertEqual(len(containers), 2)
        for container in containers:
            password = next(item for item in container["env"] if item["name"] == "SQL_PASSWORD")
            self.assertEqual(password["valueFrom"]["secretKeyRef"], {"name": "managed-postgres", "key": "password"})

    def test_disabled_components_do_not_leave_policy_peers(self):
        docs = self.render({
            "networkPolicy": {"enabled": True}, "workflows": {"enabled": False}, "budget-redis": {"enabled": False},
        })
        policy = resource(docs, "NetworkPolicy", "inference-gateway")["spec"]
        self.assertEqual(len(policy["ingress"][0]["from"]), 1)
        self.assertEqual(len(policy["egress"]), 1)
        self.assertNotIn("TEMPORAL_ADDRESS", gateway_env(docs))

    def test_large_retention_values_are_decimal_strings(self):
        docs = self.render({"inference-gateway": {
            "workflowRecords": {"runRecordRetentionSeconds": 31536000, "contentRetentionSeconds": 10000000},
            "traceability": {"auditViewRetentionSeconds": 31536000},
        }})
        env = gateway_env(docs)
        for key in ("RUN_RECORD_RETENTION_SECONDS", "AUDIT_VIEW_RETENTION_SECONDS"):
            self.assertEqual(env[key]["value"], "31536000")
        self.assertEqual(env["CONTENT_RETENTION_SECONDS"]["value"], "10000000")

    def test_rejects_invalid_feature_values(self):
        for values in (
            {"inference-gateway": {"ingress": {"enabled": True}}},
            {"inference-gateway": {"ingress": {**TLS, "tls": {"enabled": True, "secretName": ""}}}},
            {"inference-gateway": {"auth": {"oidc": {"defaultRole": "owner"}}}},
            {"inference-gateway": {"auth": {"oidc": {"scopes": "profile"}}}},
            {"networkPolicy": {"externalEgress": [{"cidr": "0.0.0.0/0", "port": 443}]}},
            {"networkPolicy": {"externalEgress": [{"cidr": "0:0:0:0:0:0:0:0/0", "port": 443}]}},
            {"networkPolicy": {"externalEgress": [{"cidr": "203.0.113.10/32", "port": 0}]}},
            {"networkPolicy": {"enabled": True}, "inference-gateway": {"networkPolicy": {"enabled": True}}},
        ):
            with self.subTest(values=values):
                self.render(values, valid=False)


if __name__ == "__main__":
    unittest.main()
