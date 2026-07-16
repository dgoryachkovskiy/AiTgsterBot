# Day 13 Check

## What This Task Does

`day13_task_state_machine.py` implements a formal task state machine:

- `stage`
- `current_step`
- `expected_action`
- allowed transitions in code
- pause and resume from saved JSON state
- DeepSeek API response based on saved state

State files are generated in:

```text
day13_task_state_store/
```

This folder is ignored by git and can be recreated by the demo.

## Run

From PowerShell:

```powershell
cd C:\Users\pospi\Documents\Codex
```

Check syntax:

```powershell
.venv\Scripts\python.exe -m py_compile day13_task_state_machine.py
```

Run full demo through DeepSeek API:

```powershell
.venv\Scripts\python.exe day13_task_state_machine.py demo
```

The demo prints each stage as soon as DeepSeek answers:

```text
goal: ...
[planning]
step=... | expected=approve_plan | pause_ok=True
DeepSeek response:
stage: planning
step: ...
expected: approve_plan
result: ...
transition=approve_plan -> execution
artifact=planning_result: ...
```

Print the full compact report to console:

```powershell
.venv\Scripts\python.exe day13_task_state_machine.py demo --print-report
```

Run silently and only save the report:

```powershell
.venv\Scripts\python.exe day13_task_state_machine.py demo --quiet
```

Show saved task state:

```powershell
.venv\Scripts\python.exe day13_task_state_machine.py show-state
```

Resume saved task and ask DeepSeek to continue from current state:

```powershell
.venv\Scripts\python.exe day13_task_state_machine.py continue
```

## Expected Result

Demo creates:

```text
DAY13_TASK_STATE_REPORT.md
day13_task_state_store\task_state.json
day13_task_state_store\state_events.json
```

Report must show:

- transitions: `planning -> execution -> validation -> done`
- `Pause / Resume Checks` all have `"pause_preserved_state": true`
- final state has `"stage": "done"`
- DeepSeek answers continue from saved stage instead of asking to explain task again

## If DeepSeek Times Out

Run with larger timeout:

```powershell
$env:MEMORY_DEEPSEEK_TIMEOUT_SECONDS="60"
$env:MEMORY_DEEPSEEK_RETRIES="3"
.venv\Scripts\python.exe day13_task_state_machine.py demo
```
