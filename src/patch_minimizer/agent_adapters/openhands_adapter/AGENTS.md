# OpenHands Adapter

> **Purpose:** Parse the OpenHands `events[]` trajectory format into the canonical `patch_list` (`CandidateTrajectory`s) for the minimization pipeline.
>
> **Scope:** Trajectory parsing + edit extraction. Bulk loading and the evaluation harness live in `agent_adapter_base/evaluation/`.

---

## File Overview

| File | Purpose |
|---|---|
| `parsers.py` | `KarenaOpenHandsTrajectoryParser` (`TrajectoryParser` subclass). Parses top-level `events[]` from Karena `traj.json` exports. Self-registers with `parser_registry` at module bottom; `payload_keys() == ("events",)`. `__init__` sets `utils.REPO_TREE_PREFIX = "/linux/"`. |
| `file_editor_action_extractor.py` | `FileEditorActionExtractor` (`ActionEditExtractor` subclass). Turns a verified `FileEditorAction` event into a single `Edit`; also reads `FileEditorObservation` events for the success/diff maps. |
| `statements/` | Concrete statements: `RUN_STATEMENTS` (sed/rm/cp), `FileEditorStatement`, `UndoEditStatement`, `GitResetFileStatement`, `GitResetAllStatement`. See `statements/AGENTS.md`. |
| `__init__.py` | Side-effect import of `parsers` (registration) + re-exports both classes. |

## Registration & Dispatch

```python
import patch_minimizer.agent_adapters.openhands_adapter  # noqa: F401
# -> parser_registry.register(KarenaOpenHandsTrajectoryParser)
```

Dispatched by the registry on any payload with a truthy `events` key (default `can_parse`).

## How OpenHands Differs From SWE-Agent / Mini-SWE

| Aspect | OpenHands | SWE-Agent | Mini-SWE |
|---|---|---|---|
| Trajectory key | `events[]` | `trajectory[]` | `messages[]` |
| Edit mechanism | structured `FileEditorAction` dict (`str_replace` / `insert` / `create`) + shell `run` actions (sed/rm/cp) | `str_replace_editor` CLI + shell (sed/rm/cp) | raw bash (sed, cat/tee heredoc, patch, str_replace_editor, rm/cp) |
| Success check | matching `FileEditorObservation` (`"has been edited"` / `"File created successfully"`) **and**, for edits, an `extras.diff` | observation text + `state.diff` changed | `<returncode>0</returncode>` (or edit-success/`patching file` text) |
| Match mode (str-based edits) | `SEMI_FUZZY` (exact, then strip-retry) | `EXACT` | varies by helper (line-based sed/heredoc; `str_replace_editor` sub-edits use `EXACT`) |
| Feedback boundary | `run_kernel` in a `run` action's command | `run_kernel` in `action` | `run_kernel` in a bash block |

## Edit Extraction Flow

Two paths, both gated by `is_edit_step` + `is_successful_edit_step`. Dispatch is statement-based: `run` events over `RUN_STATEMENTS`, `edit` events over `FILE_EDITOR_STATEMENTS` (first match wins; see `statements/AGENTS.md`):

```text
events[] iteration
  ├─ action == "edit"  (FileEditorAction)
  │    → success: observation map has "has been edited"/"File created successfully"
  │      AND (for str_replace/insert) an extras.diff exists
  │    → FileEditorActionExtractor.extract_from_event()
  │        str_replace → Edit(REPLACE, MatchMode.SEMI_FUZZY)
  │        insert      → Edit(INSERT_AFTER_LINE, starting_line=insert_line+1)
  │        create      → Edit(WHOLE_FILE_ACTIONS)
  │      → stamp edit_diff (per-edit extras.diff) on the last edit
  └─ action == "run"   (shell command)
       sed → sed_edits() · rm → parse_rm_command() · cp → parse_cp_command()
       success: the run's id is not in _run_error_ids
```

## Observation Map (`store_observations`)

`parse()` first walks all events once via `store_observations()` to build three lookups keyed by the action id that each observation `cause`s:

- `_obs_map` — observation text per edit action id.
- `_diff_map` — per-edit `extras.diff` (only when non-empty).
- `_run_error_ids` — action ids for `run` actions whose observation was `"error"` (rejected commands).

`is_successful_edit_step` reads these maps: edit actions require the success text **and** (for `str_replace`) a diff; `run` actions succeed unless their id is in `_run_error_ids`.

## Undo & Reset Hooks

- `is_undo_step` / `is_successful_undo_step` — `FileEditorAction` with `command == "undo_edit"`; success requires `"undone successfully"` and rejects `"No edit history"` / `"is not an absolute path"`.
- `perform_undo_step` — (1) `undo_edit` pops the most recent edit on that file; (2) `git checkout`/`git restore <files>` in a `run` action filters **all** prior edits on the reverted files (targets via `extract_git_reset_file_targets`). Returns `False` so the walk still falls through to `parse_edit_step`.
- `is_reset_all_step` — whole-tree `git reset --hard` (no file args) in a `run` action.

## Key Patterns

- **Per-edit diff, not cumulative.** OpenHands `extras.diff` describes only the single edit (computed against the file after prior edits), so it is stored in `edit_diff`, not `expected_diff` (which is SWE-Agent's cumulative `state.diff`).
- **Path normalization.** All paths go through `normalize_linux_repo_path()` (strips `REPO_TREE_PREFIX`).
- **Shared helpers.** `sed`/`rm`/`cp`/`git-reset` parsing comes from `agent_adapter_base/edit_parsing_helpers/`; `AgentPass` & co. live in `agent_adapter_base/evaluation/`.
