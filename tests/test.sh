#!/bin/bash
set +e

mkdir -p /logs/verifier
cd /app || exit 0
command -v uv >/dev/null 2>&1
python -m pytest -rA /tests/test_outputs.py 2>&1
status=$?

if [ "$status" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

exit 0
