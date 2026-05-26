FROM python:3.11-slim

RUN pip install --no-cache-dir pytest==8.4.1 uv==0.9.7
RUN uvx pytest --version

WORKDIR /app
COPY payout_worker.py payout_store.py payout_rules.py payout_locks.json /app/

CMD ["sleep", "infinity"]
