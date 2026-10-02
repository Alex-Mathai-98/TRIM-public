# mini-swe-agent Statements

> Concrete `Statement` / `RevertStatement` classes for `MiniSweMessagesTrajectoryParser` and
> `BashEditExtractor`. Contract + shared bases:
> [`agent_adapter_base/statements/AGENTS.md`](../../agent_adapter_base/statements/AGENTS.md).
> `action` = **one bash block** (or the assistant text outside blocks, for
> `str_replace_editor`). `step` = the mini-swe step dict, or `BashEditExtractor._statement_context()`
> (patch memo + response) when called via `extract_edits`. #architecture

## 1. Edit statements — `BashEditExtractor.EDIT_STATEMENTS` (**all** matches contribute)

| Order | Class | File | Notes |
|---|---|---|---|
| 1 | `CatHeredocStatement` | `cat_heredoc_statement.py` | file creation first so later edits can target it |
| 2 | `TeeHeredocStatement` | `tee_heredoc_statement.py` | |
| 3 | `SedStatement` | `sed_statement.py` | |
| 4 | `StrReplaceEditorStatement` | `str_replace_editor_statement.py` | also applied to text outside bash blocks |
| 5 | `RMStatement` | `rm_statement.py` | |
| 6 | `CpStatement` | `cp_statement.py` | drives the parser's external-file → repo remap |
| 7 | `PatchStatement` | `patch_statement.py` | `is_edit` needs a memoized body; `matches` = any patch cmd |

Success is **step-level** for all of them: `mini_swe_edit_statement.step_succeeded`
(rc 0, str_replace_editor success text, or `patching file`). `parser_gap_detector.py`
duplicates this rule — keep them in sync.

## 2. Revert statements

| Class | File | Hook | Success rule | `apply` result |
|---|---|---|---|---|
| `GitResetFileStatement` | `git_reset_file_statement.py` | `UNDO_STATEMENTS` over every block; `is_successful_undo_step` → `reset_file_succeeded` | "Updated N path(s)" (N>0) before rc | always `False` |
| `GitResetAllStatement` | `git_reset_all_statement.py` | `is_reset_all_step` | `"HEAD is now at"` in response | detection-only |

## 3. Invariants

- Run `bash tests/run_parsing_benchmarks.sh live_kbench_mini_swe_claude_opus_4_5` after changes.
