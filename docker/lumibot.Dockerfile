# LumiBot worker (Alpaca PAPER only). Multi-arch.
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 BREADMAKER_DATA_DIR=/data BREADMAKER_CONFIG=/app/config/breadmaker.yaml \
    PYTHONPATH=/app:/app/engines/lumibot ALPACA_PAPER=true
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements/lumibot.lock requirements/lumibot.lock
RUN pip install -r requirements/lumibot.lock
COPY breadmaker ./breadmaker
COPY config ./config
COPY engines/lumibot ./engines/lumibot
RUN useradd -u 10001 -m bm && mkdir -p /data && chown bm /data
USER bm
WORKDIR /app/engines/lumibot
CMD ["python", "run_paper.py"]
