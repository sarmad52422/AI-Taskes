FROM python:3.12-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
RUN uv tool install pytest==8.4.1

WORKDIR /app
COPY reservation_worker.py inventory_store.py reservation_rules.py jobs.json inventory.json /app/
