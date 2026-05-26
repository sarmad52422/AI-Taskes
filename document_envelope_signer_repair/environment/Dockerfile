FROM python:3.11-slim

RUN pip install --no-cache-dir pytest==8.4.1 uv==0.9.7
RUN uvx pytest --version

WORKDIR /app
COPY signer_worker.py signer_store.py signer_rules.py envelope_signers.json /app/

CMD ["sleep", "infinity"]
