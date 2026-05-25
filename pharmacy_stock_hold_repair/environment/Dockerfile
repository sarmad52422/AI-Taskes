FROM python:3.11-slim

RUN pip install --no-cache-dir pytest==8.4.1 uv==0.9.7
RUN uvx pytest --version

WORKDIR /app
COPY hold_worker.py hold_store.py hold_rules.py stock_holds.json /app/

CMD ["sleep", "infinity"]
