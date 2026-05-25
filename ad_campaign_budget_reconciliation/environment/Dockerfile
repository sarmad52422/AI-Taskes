FROM python:3.11-slim

RUN pip install --no-cache-dir pytest==8.4.1 uv==0.9.7
RUN uvx pytest --version

WORKDIR /app
COPY budget_worker.py budget_store.py budget_rules.py ad_state.json /app/

CMD ["sleep", "infinity"]
