FROM registry.k8s.io/kubectl:v1.36.5@sha256:0667e3f3e6ef435d7cd4c16684244703ebd67314c17511c2a9f87ea44f6cdf6b AS kubectl
FROM python:3.14-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl
COPY sdk/python/requirements.lock /app/requirements.lock
# Keep installer-vendored packages out of the runtime, as in the service images.
RUN apk upgrade --no-cache \
    && python -m pip install --no-cache-dir --disable-pip-version-check --require-hashes -r requirements.lock \
    && python -m pip uninstall --yes pip \
    && adduser -D -H -u 10001 appuser
COPY sdk/python/agentworkflows /app/agentworkflows
USER 10001
CMD ["python", "-m", "agentworkflows.examples.research", "worker"]
