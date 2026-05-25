FROM python:3.11-slim

RUN pip install --no-cache-dir pytest==8.4.1 uv==0.9.7
RUN uvx pytest --version

WORKDIR /app
COPY lab_worker.py lab_store.py lab_rules.py lab_data.json /app/

CMD ["sleep", "infinity"]
