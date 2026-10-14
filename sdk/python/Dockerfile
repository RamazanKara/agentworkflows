# Built from the repository root; Dockerfile.dockerignore beside this file limits the context
# to the SDK package and its lock.
FROM registry.k8s.io/kubectl:v1.37.1@sha256:b7cab618e281b1ee7484e7b706a96e2135fbb6e072c2a573a7dab4e87d7f2385 AS kubectl

# Install the locked dependencies into a separate prefix and strip debug symbols from their
# native extensions. Only that prefix reaches the runtime; binutils stays here.
FROM python:3.14-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72 AS deps
WORKDIR /build
COPY sdk/python/requirements.lock requirements.lock
RUN apk add --no-cache binutils \
    && python -m pip install --no-cache-dir --disable-pip-version-check --no-warn-script-location \
       --prefix=/install --require-hashes -r requirements.lock \
    && find /install -type f -name '*.so*' -exec strip --strip-unneeded {} +

FROM python:3.14-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72

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
