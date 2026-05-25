FROM python:3.11-slim

RUN pip install --no-cache-dir pytest==8.4.1 uv==0.9.7
RUN uvx pytest --version

WORKDIR /app
COPY manifest_worker.py manifest_store.py manifest_rules.py route_state.json /app/

CMD ["sleep", "infinity"]
