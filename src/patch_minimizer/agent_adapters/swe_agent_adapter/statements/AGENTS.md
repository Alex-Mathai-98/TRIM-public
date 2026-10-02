# SWE-agent Statements

> Concrete `Statement` / `RevertStatement` classes for `ClassicSWEAgentTrajectoryParser` (and its
> `SWEBenchSWEAgentTrajectoryParser` subclass). Contract + shared bases:
> [`agent_adapter_base/statements/AGENTS.md`](../../agent_adapter_base/statements/AGENTS.md).
> `action` = the step's `action` string. #architecture

## 1. Edit statements — `ClassicSWEAgentTrajectoryParser.EDIT_STATEMENTS` (first match wins)

| # | Class | File | `is_edit` gate | Gate 1 (observation) | `state.diff` gate |
|---|---|---|---|---|---|
| 1 | `StrReplaceEditorStatement` | `str_replace_editor_statement.py` | str_replace / insert / create only | `"has been edited"` or `"File created successfully"` | yes |
| 2 | `SedStatement` | `sed_statement.py` | `is_sed_command` | always passes (obs empty) | yes |
| 3 | `RMStatement` | `rm_statement.py` | `is_rm_command` | always passes | **bypassed** |
| 4 | `CpStatement` | `cp_statement.py` | `is_cp_command` | always passes | **bypassed** |

`SWEEditStatement` (`swe_edit_statement.py`) implements the shared rule:
gate 1 → `"File created successfully"` short-circuit → bypass if **any** bypassing statement
matches the action (so `rm x && sed -i …` bypasses even though sed wins dispatch) → diff
must differ from `parser._last_diff`.

## 2. Revert statements

| Class | File | Used by hook | Success rule | `apply` result |
|---|---|---|---|---|
| `UndoEditStatement` | `undo_edit_statement.py` | `UNDO_STATEMENTS[0]` → `perform_undo_step`, `is_undo_step`, `is_successful_undo_step` | obs lacks "no edit history" / "is not an absolute path" | `True` (also on failed undo); `False` if no prior edit on file |
| `GitResetFileStatement` | `git_reset_file_statement.py` | `UNDO_STATEMENTS[1]` → `perform_undo_step` | some target's diff block vanished from `state.diff` (`actually_reverted`) | `True` only if something was reverted |
| `GitResetAllStatement` | `git_reset_all_statement.py` | `is_reset_all_step` (also resets `_last_diff`) | `state.diff == ""` | detection-only |
| `GitStashSaveStatement` | `git_stash_statements.py` | `is_stash_save_step` | `state.diff == ""` | detection-only |
| `GitStashRestoreStatement` | `git_stash_statements.py` | `is_stash_restore_step` | `state.diff != ""` | detection-only |

## 3. Invariants

- List order = historical `if/elif` precedence. Changing it changes goldens.
- Run `bash tests/run_parsing_benchmarks.sh swe_bench_swe_agent live_kbench_swe_agent_gemini_3_pro`
  after any change here (all suites if you touch `agent_adapter_base/`).
