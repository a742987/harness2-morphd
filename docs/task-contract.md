# Task contract v1

Every runtime executes the same JSON object. The store places it beside a sanitised workspace.
The consumer path builds the same contract from one session with `criteria.checks = []` and
`expected_deliverables = []`; it never fabricates checks. A score is therefore `None`, not 0.

```
<tasks>/<task>/contract.json
<tasks>/<task>/workspace/...
<tasks>/splits.json
```

```json
{
  "task_contract": 1,
  "task_ref": "dsh-session-abc",
  "instruction": "Fix the failing report generator and write out/report.md.",
  "workspace": {"dir": "workspace", "sha256": "<sha256 of the sanitised tree>"},
  "criteria": {
    "checks": [
      {"type": "file_exists", "path": "out/report.md"},
      {"type": "file_contains", "path": "out/report.md", "text": "Summary"},
      {"type": "json_equals", "path": "out/meta.json", "pointer": "/ok", "value": true}
    ]
  },
  "user_turns": [
    {"text": "Fix the failing report generator and write out/report.md.", "corrective": false},
    {"text": "The totals are still wrong; use the daily table.", "corrective": true}
  ],
  "expected_deliverables": ["out/report.md"],
  "domain": "reporting",
  "source_runtime": "dsh",
  "created_at": "2026-10-21T00:00:00+00:00",
  "provenance": {}
}
```

## Invariants

- `user_turns[0].text == instruction`; U1 is the task.
- `task_ref` has no slash and is unique; it is stable across runtimes and seeds.
- `workspace.sha256` is computed **after** sanitisation. The same contract is replayed on all
  runtimes with the same hash.
- Criteria are stored outside the workspace and never enter a prompt, workspace or trajectory.
  `hidden: true` marks answer-like text so `doctor` can assert leakage zero. Regex hashes,
  SHA-256 checks and JSON answers are treated as hidden by construction.
- Expected deliverables are paths/globs relative to the workspace; they are used for the paired
  "artifact presence" metric, not for grading the model's prose.
- At import/sanitisation, top-level leak directories (`rubrics/`, `expected/`, `reference/`,
  `solution/`, `criteria/`) and files (`RUBRICS.json`, `task.json`, `criteria.json`) are removed
  from the task workspace. The criteria live only in `contract.json` outside the workspace.
- No runtime-specific field is permitted. Fields not in the schema are rejected.

## Checks

| type | fields |
|---|---|
| `file_exists` / `file_not_exists` | `path` |
| `file_contains` | `path`, `text`, optional `case_sensitive` |
| `file_regex` | `path`, `pattern` |
| `files_glob` | `glob`, `min_count` |
| `file_sha256` | `path`, `sha256` |
| `json_equals` | `path`, `pointer`, `value` |

Checks are deterministic and offline. A missing rubric (`criteria.checks` empty) makes
`score()` return `None` — never zero — and the judge preserves prior non-null scores.

## Splits

`splits.json` is immutable by default:

```json
{"contract": 1, "created_at": "...", "assignments": {"task-a": "train", "task-b": "test", "task-c": "holdout"}}
```

`morphd tasks split --force` replaces it. The deterministic generator uses a stable hash of the
task ref; holdout defaults to 20% and the remaining tasks split 50/50 into train/test.

## Adding tasks directly

```python
from morphd.contract import TaskContract, TaskStore, UserTurn
from morphd import sanitize, store

sanitize.sanitize_tree(source_workspace, staging)
digest = store.tree_hash(staging)
task = TaskContract.new(
    task_ref="my-task-1",
    instruction="...",
    workspace_sha256=digest,
    domain="backend",
    checks=[{"type": "file_exists", "path": "out.txt"}],
    user_turns=[UserTurn("...")],
    expected_deliverables=["out.txt"],
)
TaskStore("~/.morphd/tasks").add_task(task, staging)
```
