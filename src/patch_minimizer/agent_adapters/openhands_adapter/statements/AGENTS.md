# OpenHands Statements

> Concrete `Statement` / `RevertStatement` classes for `KarenaOpenHandsTrajectoryParser`.
> Contract + shared bases: [`agent_adapter_base/statements/AGENTS.md`](../../agent_adapter_base/statements/AGENTS.md).
> `action` = the event's `args.command` (a shell string for `run` events, the file-editor
> sub-command name for `edit` events — see `parsers._command`). #architecture

## 1. Edit statements (first match wins)

| List | Class | File | Matches | Success rule |
|---|---|---|---|---|
| `RUN_STATEMENTS` | `SedStatement` → `RMStatement` → `CpStatement` | `sed_/rm_/cp_statement.py` | shell command in a `run` event | `OpenHandsRunStatement`: event id not in `parser._run_error_ids` |
| `FILE_EDITOR_STATEMENTS` | `FileEditorStatement` | `file_editor_statement.py` | `edit` event, command ∈ str_replace/insert/create | obs has "has been edited"/"File created successfully" **and**, for edits, an `extras.diff` |

`FileEditorStatement.to_edits` ignores the prefix and uses the `step_idx` given to `from_action` (labels like
`openhands edit step N`); it raises `ValueError` if `step_idx` wasn't given, rather than labelling edits
"step 0". `parse_edit_step` passes it via `_edit_statement(step, step_idx=idx)`, then stamps the per-edit
`extras.diff` as `edit_diff`.

## 2. Revert statements

| Class | File | Hook | Success rule | `apply` result |
|---|---|---|---|---|
| `UndoEditStatement` | `undo_edit_statement.py` | `UNDO_STATEMENTS[0]`, `is_undo_step`, `is_successful_undo_step` | obs contains `"undone successfully"` | `True` / `False` (shared pop) |
| `GitResetFileStatement` | `git_reset_file_statement.py` | `UNDO_STATEMENTS[1]` | none (no diff available) | always `False` — step still reaches `parse_edit_step` |
| `GitResetAllStatement` | `git_reset_all_statement.py` | `is_reset_all_step` | assumed | detection-only |

## 3. Invariants

- Run `bash tests/run_parsing_benchmarks.sh live_kbench_openhands_gemini_3_pro` after changes.
