# Harbor/Pluto Debugging Task Recipe

Use this recipe to create medium-difficulty Harbor/Pluto software debugging tasks like the approved inventory, voice alignment, receipt OCR, billing proration, embedding cache, and feature rollout tasks.

The goal is not to make a huge project. The goal is to create a compact production incident where the fix requires reasoning across state transitions, persisted JSON, helper modules, repeated runs, and stale recovery state.

## Target Shape

Each task should feel like a real backend worker that reads one JSON file from `/app`, processes jobs, writes the same file back, and returns the persisted document.

Use this layout exactly:

```text
instruction.md
task.toml
environment/
  Dockerfile
  *_worker.py
  *_store.py
  *_rules.py
  data.json
solution/
  solve.sh
tests/
  test.sh
  test_outputs.py
```

Do not add root `app/`, root `data/`, `verifier/`, `venv/`, `.git/`, `.codex/`, `.agents/`, `.pytest_cache/`, or `__pycache__` to the task folder.

Runtime files live under `environment/`. The Dockerfile copies them into `/app`. Tests are mounted at `/tests`. The solution is mounted at `/solution`.

## Dockerfile

Keep the Dockerfile boring and explicit.

```Dockerfile
FROM python:3.11-slim

RUN pip install --no-cache-dir pytest==8.4.1 uv==0.9.7
RUN uvx pytest --version

WORKDIR /app
COPY worker.py store.py rules.py data.json /app/

CMD ["sleep", "infinity"]
```

Do not use `COPY *.py`; quality checks may reject broad wildcard copies.

## tests/test.sh

Use this exact verifier style:

```bash
#!/bin/bash
set +e

mkdir -p /logs/verifier

cd /app || exit 0

uvx pytest -rA /tests/test_outputs.py
status=$?

if [ "$status" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

exit 0
```

The script must print normal pytest output to stdout. It must always exit `0` after writing the reward file.

## task.toml

Use the same simple Harbor-compatible metadata style unless the target platform explicitly asks for a sectioned schema.

```toml
name = "domain-specific-task-name"
author_name = "Muhammad Nauman"
author_email = "nomanawantest1@gmail.com"
category = "software_engineering"
tags = ["python", "debugging", "backend", "state-management"]
difficulty = "medium"
expert_time_estimate_hours = 2
difficulty_explanation = """
Explain the interacting state problems and why this is not a one-line fix.
"""
solution_explanation = """
Summarize the intended repair without giving exact code.
"""
verification_explanation = """
Summarize behavioral coverage.
"""
domain = "backend"
timeout = 120
cpu = 1
memory = "1GiB"
disk = "1GiB"
```

## Instruction Style

Write `instruction.md` like a real engineer ticket. Keep it natural and direct.

Good instruction ingredients:

- short incident paragraph
- exact runtime command, such as `python /app/worker.py`
- exact JSON path, such as `/app/jobs.json`
- complete enough schema for quality checks
- status list, terminal states, and retry behavior
- business expectations in plain language
- explicit idempotency requirements
- stale failure metadata cleanup requirements
- ownership/conflict rules if present
- reminder to keep the fix inside `/app`

Avoid:

- test names
- step-by-step solution hints
- magic constants hidden only in tests
- long marketing-style prose
- generic AI-sounding phrases
- overexplaining obvious Python basics

The approved instructions usually say what the system should preserve and what must never be rewritten. That matters more than long background.

## Good Task Mechanics

Approved tasks work because they combine several small behaviors. Use 2-5 interacting concepts, not one deterministic transformation.

Reliable mechanics:

- pending/retrying jobs are processed, terminal jobs are unchanged
- partial work moves to retrying before failed
- exhausted retries become failed
- successful retry removes stale failure state but keeps unrelated metadata
- repeated runs are stable
- ids are normalized and not duplicated
- counters are not inflated on rerun
- existing partial state is reused or topped up
- stale rows/holds/fields/cache entries are refreshed only when needed
- records owned by another job/team are not overwritten
- conflict retries keep protected records unchanged
- final persisted JSON matches the worker return value

Do not make the main bug just sorting, parsing, or applying a formula. Those become too easy.

## Difficulty Tuning

Target:

- NOP must fail.
- Oracle must pass.
- Sonnet should fail at least some tests.
- Opus should not fail everything, but should not pass every run either.

What tends to be too easy:

- pure ranking pipelines
- one-pass transformations
- deterministic sorting only
- isolated scoring helpers
- simple parser bugs
- a single missing filter
- tests that reveal one clean function rewrite

What works better:

- stale persisted state plus fresh source data
- partial recovery from existing state
- ownership conflicts
- terminal records that must stay byte-for-byte unchanged
- repeated-run stability
- retry exhaustion with careful attempt counts
- cleanup of stale metadata after success
- keeping original metadata while removing retry fields
- preserving valid existing rows while refreshing stale rows
- order constraints interacting with state reuse

Do not add obscure hidden traps. The difficulty should come from realistic interactions, not guessing.

## Suggested Domains

Vary the domain so tasks do not look like renamed copies.

Good domains:

- inventory reservations
- voice transcript alignment
- receipt OCR reconciliation
- subscription proration
- embedding cache/indexing
- feature rollout assignment
- permissions inheritance repair
- notification digest batching
- marketplace payout settlement
- calendar recurrence repair
- import/export entity merge
- analytics late-event aggregation

Avoid creating another task whose only identity is `pending/retrying/completed/failed` plus duplicate ids. Keep the state ideas, but change the data model and business surface.

## Runtime Code Pattern

Use three runtime modules:

```text
*_store.py      JSON loading/saving and low-level state access
*_rules.py      status/retry/eligibility/helper logic
*_worker.py     orchestration and job processing
```

The broken implementation should be plausible. It should collect tests and fail behaviorally, not crash on import.

Good broken-code patterns:

- processes terminal jobs accidentally
- appends ids instead of normalizing
- rewrites already fresh cache rows
- increments counters on rerun
- clears all metadata instead of stale retry fields only
- treats partial failure as failed too early
- ignores existing partial state
- overwrites another owner/job's record
- increments attempts even when already exhausted
- writes partial index/cache state before validation

Keep comments out of code unless absolutely necessary. The approved tasks generally use no code comments.

## Test Suite Pattern

Create 10-17 behavioral pytest tests. Use helpers to build documents so tests are readable.

Core tests to include:

- worker return value equals persisted JSON
- happy path processes a pending/queued job
- partial missing input moves to retrying without partial writes
- retrying job recovers when source data becomes complete
- exhausted retry moves to failed
- terminal success state remains unchanged
- terminal failed/cancelled state remains unchanged
- repeated runs do not double-count or duplicate ids
- stale retry metadata is cleared after recovery
- unrelated metadata is preserved
- existing partial state is reused
- stale same-owner state is refreshed
- other-owner conflict is not overwritten
- conflict retry uses the correct reason
- exhausted conflict fails without touching protected records

Use exact assertions on persisted state, counters, ids, status, metadata cleanup, and protected rows. Do not hardcode reward values in tests.

## Oracle Solution Pattern

`solution/solve.sh` should patch only runtime files under `/app`.

Allowed style:

```bash
#!/bin/bash
set -e

cat > /app/store.py <<'PY'
...
PY

cat > /app/rules.py <<'PY'
...
PY

cat > /app/worker.py <<'PY'
...
PY
```

Do not edit `/tests`, `/logs`, or reward files from the solution.

The oracle should solve the real bug, not special-case test data. It should be a normal implementation that would make sense in production.

## Validation Commands

Before calling a task ready, run:

```bash
bash -n tests/test.sh
bash -n solution/solve.sh
python3 -c "import tomllib; tomllib.load(open('task.toml','rb')); print('toml ok')"
docker build -t task-test environment
docker run --rm -v "$(pwd)/tests:/tests" task-test bash -c "bash /tests/test.sh && cat /logs/verifier/reward.txt"
docker run --rm -v "$(pwd)/tests:/tests" -v "$(pwd)/solution:/solution" task-test bash -c "bash /solution/solve.sh && bash /tests/test.sh && cat /logs/verifier/reward.txt"
```

Expected:

- NOP reward `0`
- Oracle reward `1`

After Docker runs, remove root-owned pytest artifacts with Docker if needed:

```bash
docker run --rm -v "$(pwd)/tests:/work" task-test rm -rf /work/.pytest_cache /work/__pycache__
```

Remove generated zips and scan:

```bash
find . -maxdepth 4 \( -name '.pytest_cache' -o -name '__pycache__' -o -name '*.zip' -o -name '.git' -o -name '.codex' -o -name '.agents' -o -name 'venv' -o -name 'verifier' \) -print
```

The final scan should print nothing.

## Lessons From The Search Merchandising Task

The search merchandising task is still in pipeline and was harder to tune. Main lesson: deterministic ranking plus cache write is too easy, even with many tests.

If using ranking/search/recommendations, add real state interactions:

- protected owners
- partial cache reuse
- stale row cleanup
- manual slot preservation
- conflict retry behavior
- final order constraints
- no partial row writes after conflict
- off-page stale protected rows are removable
- manual rows are protected for exact slot, not just product id

Even then, be careful: if the instruction spells out every edge too directly, Opus may pass all tests. If it is too vague, quality checks or strong models may fail for the wrong reason. The sweet spot is clear business rules with edge cases that require combining two rules.

## Common Rejection Causes

Avoid these:

- incomplete structured data schema in `instruction.md`
- wildcard Docker copies like `COPY *.py`
- tests or solution files copied into `/app`
- root `app/` or `data/` folders
- cache folders included in final task
- `test.sh` exits nonzero before writing reward
- no `uvx pytest` in `test.sh`
- oracle modifies tests or reward files
- NOP passes all tests
- oracle fails any test
- broken implementation crashes before tests exercise behavior
- task is only a one-function deterministic transform

## Quick Checklist

Before shipping, verify:

- [ ] Harbor folder structure is exact.
- [ ] Dockerfile copies only environment runtime files into `/app`.
- [ ] `instruction.md` documents the JSON schema and business behavior.
- [ ] Runtime code has no noisy comments.
- [ ] Tests cover repeated runs, terminal records, retry recovery, metadata cleanup, and persisted state.
- [ ] NOP fails behaviorally.
- [ ] Oracle patches only `/app` runtime files.
- [ ] Oracle passes all tests.
- [ ] Reward file is written by `tests/test.sh`.
- [ ] Final scan has no cache, zip, git, venv, verifier, or hidden agent folders.
# NOTE
Replace worker.py, store.py, rules.py, and data.json with the exact runtime filenames for the task. Do not use wildcard COPY.