# Built from the repository root; Dockerfile.dockerignore beside this file limits the context
# to the SDK package and its lock.
FROM registry.k8s.io/kubectl:v1.37.2@sha256:fe049faa65ae483ac200009a949a68fa4c9985daea12c9cb1195c9c7965301fc AS kubectl

# Install the locked dependencies into a separate prefix and strip debug symbols from their
# native extensions. Only that prefix reaches the runtime; binutils stays here.
FROM python:3.14-alpine@sha256:eb2c62658b8d51bff0acc408504b76521d02030de56a8a3c1e830083e89b89d2 AS deps
WORKDIR /build
COPY sdk/python/requirements.lock requirements.lock
RUN apk add --no-cache binutils \
    && python -m pip install --no-cache-dir --disable-pip-version-check --no-warn-script-location \
       --prefix=/install --require-hashes -r requirements.lock \
    && find /install -type f -name '*.so*' -exec strip --strip-unneeded {} +

FROM python:3.14-alpine@sha256:eb2c62658b8d51bff0acc408504b76521d02030de56a8a3c1e830083e89b89d2

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# Keep installer-vendored packages out of the runtime, as in the service images.
RUN apk upgrade --no-cache \
    && python -m pip uninstall --yes pip \
    && adduser -D -H -u 10001 appuser
COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl
COPY --from=deps /install /usr/local
COPY sdk/python/agentworkflows /app/agentworkflows

# OCI image metadata, as in the service images; declared last so a new revision reuses every layer.
ARG VCS_REF=unknown
ARG IMAGE_SOURCE=https://github.com/RamazanKara/agentworkflows
LABEL org.opencontainers.image.source="${IMAGE_SOURCE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.description="Python workflow worker for the AgentWorkflows" \
      org.opencontainers.image.licenses="Apache-2.0"

USER 10001
CMD ["python", "-m", "agentworkflows.examples.research", "worker"]
