FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[gcp]"

USER 65532:65532
ENTRYPOINT ["python", "-m", "entertainment_platform.jobs.collect_gcp_incremental"]
