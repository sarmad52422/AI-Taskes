#!/bin/bash
set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
task_root="$(cd "$script_dir/.." && pwd)"

if [ -f "$(pwd)/app/pipeline.py" ]; then
  cd "$(pwd)"
elif [ -f "$task_root/app/pipeline.py" ]; then
  cd "$task_root"
elif [ -f /app/app/pipeline.py ]; then
  cd /app
fi

if [ ! -f "$(pwd)/app/pipeline.py" ]; then
  found_pipeline="$(
    find /app "$task_root" "$(pwd)" /workspace /workspaces /harbor-workspace /tmp \
      -maxdepth 8 -path '*/app/pipeline.py' -print -quit 2>/dev/null
  )"
  if [ -n "$found_pipeline" ]; then
    cd "$(dirname "$(dirname "$found_pipeline")")"
  fi
fi

test_file="$task_root/tests/test_pipeline.py"
if [ -f /tests/test_pipeline.py ]; then
  test_file="/tests/test_pipeline.py"
elif [ ! -f "$test_file" ]; then
  test_file="tests/test_pipeline.py"
fi

export PYTHONPATH="$(pwd):/app:${PYTHONPATH:-}"

if [ -x venv/bin/python ]; then
  PYTHON_BIN="venv/bin/python"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  PYTHON_BIN="python3"
fi

"$PYTHON_BIN" -m pytest "$test_file" -q 2>&1
status=$?

if mkdir -p /logs/verifier 2>/dev/null; then
  if [ "$status" -eq 0 ]; then
    { printf '1\n' > /logs/verifier/reward.txt; } 2>/dev/null
  else
    { printf '0\n' > /logs/verifier/reward.txt; } 2>/dev/null
  fi
fi

exit "$status"
