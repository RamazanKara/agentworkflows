FROM registry.k8s.io/kubectl:v1.34.1 AS kubectl
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl
COPY sdk/python/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && useradd --uid 10001 --no-create-home appuser
COPY sdk/python/agentworkflows /app/agentworkflows
USER 10001
CMD ["python", "-m", "agentworkflows.examples.research", "worker"]
