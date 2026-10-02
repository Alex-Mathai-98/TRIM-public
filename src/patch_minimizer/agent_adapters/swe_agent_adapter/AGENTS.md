# SWE-Agent Adapter

> **Purpose:** Parse SWE-Agent `trajectory[]` payloads into the canonical `patch_list` (`CandidateTrajectory`s) for the minimization pipeline. Covers both the kernel (kArena / `run_kernel`) and SWE-bench (`/testbed/`, pytest) variants.
>
> **Scope:** Trajectory parsing + edit extraction only. Bulk loading and the evaluation harness live in `agent_adapter_base/evaluation/`.

---

## File Overview

| File | Purpose |
|---|---|
| `__init__.py` | Side-effect import of `parsers` (registers `ClassicSWEAgentTrajectoryParser`) + re-exports both parser classes and `StrReplaceEditorExtractor`. |
| `parsers.py` | `ClassicSWEAgentTrajectoryParser` (`TrajectoryParser` subclass). Parses `trajectory[]` JSON. Registers with `parser_registry` at module bottom. `__init__` sets `utils.REPO_TREE_PREFIX = "/linux/"`. |
| `swebench_parsers.py` | `SWEBenchSWEAgentTrajectoryParser` — subclass of the classic parser for SWE-bench trajectories. Overrides only `step_requests_feedback` (test commands as revision boundaries) and `__init__` (`REPO_TREE_PREFIX = "/testbed/"`). **Not registry-registered** — callers instantiate it directly. |
| `statements/` | Concrete statements (`RMStatement`, `CpStatement`, `SedStatement`, `StrReplaceEditorStatement`, `UndoEditStatement`, `GitResetFileStatement`, `GitResetAllStatement`, `GitStash{Save,Restore}Statement`). See `statements/AGENTS.md`. |
| `other-code-for-information/swebench-feedback-coverage.py` | Standalone reference/analysis script (not imported by the package). Measures test-invocation detection coverage across a `.traj` corpus; documents the 4 test-command regexes kept in sync with `swebench_parsers.py`. |

## Registration & Dispatch

Importing the package triggers side-effect registration in `parsers.py`:

```python
import patch_minimizer.agent_adapters.swe_agent_adapter  # noqa: F401
# -> parser_registry.register(ClassicSWEAgentTrajectoryParser)
```

- `payload_keys() == ("trajectory",)` — the classic parser is dispatched by the registry on any payload with a truthy `trajectory` key (default `can_parse`).
- The SWE-bench parser shares the same `trajectory` payload shape and is **not** registered, so it never wins auto-dispatch; use it by direct instantiation.

## Concrete Parser Classes

| Class | Trajectory format | Feedback boundary | Repo prefix |
|---|---|---|---|
| `ClassicSWEAgentTrajectoryParser` | `trajectory[]` with `action` / `observation` / `state.diff` per step | `run_kernel` / `/KBDr/run_kernel` | `/linux/` |
| `SWEBenchSWEAgentTrajectoryParser` | same `trajectory[]` shape | pytest / python `<script>.py` / `python -c` / `python -m <module>` | `/testbed/` |

## Template Hooks (`ClassicSWEAgentTrajectoryParser`)

Overrides these `TrajectoryParser` hooks:

- `step_requests_feedback(step)` — inspects **only** the step's own `action` field for `run_kernel` / `/kbdr/run_kernel`. It deliberately does **not** scan the `query` field: `query` is the cumulative conversation history, and scanning it mis-classified every post-first `run_kernel` step as a boundary and dropped subsequent edits (see AIDEV-NOTE; verified across 1602 trajectories).
- `is_edit_step(step)` — true if any class in `EDIT_STATEMENTS` (`StrReplaceEditorStatement` → `SedStatement` → `RMStatement` → `CpStatement`) reports `is_edit` for `action`; `str_replace_editor` counts only for `str_replace`/`insert`/`create`.
- `is_successful_edit_step(step)` — first-matching `EDIT_STATEMENTS` entry's `is_successful` (rule in `statements/swe_edit_statement.py`); two gates: (1) observation text (`"has been edited"` / `"File created successfully"`; `sed`/`rm`/`cp` are assumed OK because their observations are empty/unreliable), then (2) `state.diff` changed vs `_last_diff` (ground truth from the Docker env). Diff gate is bypassed for `create` (files outside the repo don't show in `state.diff`) and for `rm`/`cp` (may touch untracked/gitignored files). `_last_diff` starts at `""` (clean checkout) and is updated only in `parse_edit_step`'s `finally` when the step is both an edit AND successful.
- `is_reset_all_step(step)` — `git reset --hard` in action AND `state.diff == ""` (confirms wipe); also resets `_last_diff = ""`.
- `is_stash_save_step(step)` / `is_stash_restore_step(step)` — `git stash` (excluding pop/apply/show/list/drop) with empty diff saves state; `git stash pop`/`apply` with non-empty diff restores it, via the base class `_perform_stash_save` / `_perform_stash_restore`.
- `is_undo_step(step)` / `is_successful_undo_step(step)` — detects `str_replace_editor undo_edit <file>`; undo fails (returns keep-edit) when observation contains `"No edit history"` or `"is not an absolute path"`.
- `perform_undo_step(step, bucket, revisions, *, source_label, step_idx)` — iterates `UNDO_STATEMENTS` (`UndoEditStatement`, `GitResetFileStatement`); two undo idioms: (1) `str_replace_editor undo_edit` pops the most recent edit on that file; (2) `git checkout HEAD -- <files>` / `git restore <files>` / `git reset --hard <files>` filters **all** prior edits on the reverted files, but only after confirming via `state.diff` that each target's diff block actually disappeared (staged edits make `git checkout -- <file>` a no-op).
- `parse_edit_step(step, idx, extractor)` — builds the first-matching `EDIT_STATEMENTS` statement once and calls `to_edits("classic_swe step N")` (the `extractor` argument is unused). Order = old `if/elif` precedence: `str_replace_editor` > `sed` > `rm` > `cp`. Stamps `step_index` on every edit and `expected_diff = state.diff` on the last edit of the step.

## Edit Extraction Notes

- **`str_replace_editor` matching.** `StrReplaceEditorExtractor` (shared, in `agent_adapter_base/edit_parsing_helpers/`) emits `Edit`s with `MatchMode.EXACT` — SWE-Agent's `str_replace` requires the `old_str` to match verbatim.
- **`sed -i` observations are empty** across the whole corpus, so success can't be read from the observation; the parser trusts the `state.diff` gate and downstream applicability checks.
- **`expected_diff`** carries SWE-Agent's cumulative `state.diff` (contrast with OpenHands' per-edit `edit_diff`).

## SWE-bench Variant Details

`SWEBenchSWEAgentTrajectoryParser` swaps the feedback boundary from `run_kernel` to test invocations, detected via four regexes (each allows an optional `cd <dir> &&` prefix and `&&`-chained compounds):

| Pattern | Matches |
|---|---|
| pytest | `pytest` or `python -m pytest` |
| python script | `python <file>.py` (excludes `-c`) |
| python -c | `python -c ...` |
| python -m module | `python -m <module>` (excludes pytest) |

A failed pytest (observation contains `"No module named pytest"`) is **not** counted as a boundary. Coverage analysis behind these patterns lives in `other-code-for-information/swebench-feedback-coverage.py`.

## Shared With Other Adapters

- `StrReplaceEditorExtractor`, `sed`/`rm`/`cp`/`git-reset` helpers live in `agent_adapter_base/edit_parsing_helpers/` (shared with mini-SWE and OpenHands adapters).
- `AgentPass`, `AgentPassesStore`, `AgentPatchesLoadStats` live in `agent_adapter_base/evaluation/`.
