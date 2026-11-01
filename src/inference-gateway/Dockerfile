FROM node:24-alpine@sha256:8a32c8cf649a49915a66e81e7a609223c708d0adabf288ee3f4077d765542f85 AS console
WORKDIR /build/console
ENV RAYON_NUM_THREADS=2
COPY console/package*.json ./
RUN npm ci --no-audit --no-fund
COPY console/ ./
RUN npm run build

# Install the locked runtime dependencies into a separate prefix and strip debug symbols
# from their native extensions. Only that prefix reaches the runtime; binutils stays here.
FROM python:3.14-alpine@sha256:eb2c62658b8d51bff0acc408504b76521d02030de56a8a3c1e830083e89b89d2 AS deps
WORKDIR /build
COPY requirements.lock .
RUN apk add --no-cache binutils \
    && python -m pip install --no-cache-dir --disable-pip-version-check --no-warn-script-location \
       --prefix=/install --require-hashes -r requirements.lock \
    && find /install -type f -name '*.so*' -exec strip --strip-unneeded {} +

FROM python:3.14-alpine@sha256:eb2c62658b8d51bff0acc408504b76521d02030de56a8a3c1e830083e89b89d2 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
# The service needs only the locked runtime dependencies, not pip or its vendored build tooling.
RUN apk upgrade --no-cache \
    && python -m pip uninstall --yes pip \
    && adduser -D -H -u 10001 appuser
COPY --from=deps /install /usr/local
COPY app ./app
COPY --from=console /build/app/console ./app/console

# OCI image metadata. VCS_REF/IMAGE_SOURCE are wired from CI build-args; the defaults keep
# labels meaningful for local builds. Declared last so a new revision reuses every layer above.
ARG VCS_REF=unknown
ARG IMAGE_SOURCE=https://github.com/RamazanKara/agentworkflows
LABEL org.opencontainers.image.source="${IMAGE_SOURCE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.description="Inference gateway service for the AgentWorkflows" \
      org.opencontainers.image.licenses="Apache-2.0"

USER 10001
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-graceful-shutdown", "180"]
