#!/bin/bash
set +e

mkdir -p /logs/verifier

if [ -f /app/pipeline.py ]; then
  task_root="/app"
else
  task_root="$(cd "$(dirname "$0")/../environment" && pwd)"
fi

export PYTHONPATH="$task_root:${PYTHONPATH:-}"
export TASK_ROOT_FOR_TESTS="$task_root"

python -m pytest -rA /tests/test_outputs.py 2>&1
status=$?

if [ "$status" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

exit 0
