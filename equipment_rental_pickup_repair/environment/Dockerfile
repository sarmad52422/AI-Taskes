FROM python:3.11-slim

RUN pip install --no-cache-dir pytest==8.4.1 uv==0.9.7
RUN uvx pytest --version

WORKDIR /app
COPY pickup_worker.py pickup_store.py pickup_rules.py pickup_reservations.json /app/

CMD ["sleep", "infinity"]
