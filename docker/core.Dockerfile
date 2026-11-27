# BreadMaker core (supervisor, dashboard, research). Multi-arch: amd64 + arm64 (Oracle Ampere A1).
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 BREADMAKER_DATA_DIR=/data BREADMAKER_CONFIG=/app/config/breadmaker.yaml
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements/core.lock requirements/core.lock
RUN pip install -r requirements/core.lock
COPY pyproject.toml ./
COPY breadmaker ./breadmaker
COPY config ./config
RUN pip install --no-deps . && useradd -u 10001 -m bm && mkdir -p /data && chown bm /data
USER bm
HEALTHCHECK --interval=60s --timeout=10s --retries=3 CMD python -c "import sqlite3,os;sqlite3.connect(os.environ['BREADMAKER_DATA_DIR']+'/breadmaker.sqlite').execute('select 1')"
CMD ["breadmaker", "supervisor"]
