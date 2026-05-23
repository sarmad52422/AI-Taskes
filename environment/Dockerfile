FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir pytest uv

COPY pipeline.py /app/pipeline.py
COPY model_client.py /app/model_client.py
COPY queue_manager.py /app/queue_manager.py
COPY storage.py /app/storage.py
COPY jobs.json /app/jobs.json

CMD ["sleep", "infinity"]
