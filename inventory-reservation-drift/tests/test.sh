#!/bin/bash

set -u

mkdir -p /logs/verifier

if command -v uv >/dev/null 2>&1; then
  uv tool run pytest /tests/test_outputs.py -rA
  status=$?
else
  python -m pytest /tests/test_outputs.py -rA
  status=$?
fi

if [ "$status" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

exit "$status"
