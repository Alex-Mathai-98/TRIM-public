# Agent Adapter Base

> **Purpose:** Agent-agnostic foundation for trajectory → `patch_list` conversion. Defines the parser ABC, the registry pattern, shared path normalization, shared edit-extraction helpers, and re-exports the public API. No concrete agent adapter lives here — each adapter package (`swe_agent_adapter`, `mini_swe_agent_adapter`, `openhands_adapter`) registers its parser on import.

---

## File Overview

| File | Purpose |
|---|---|
| `__init__.py` | Central re-export hub. Pulls from `parsers/`, `edit_parsing_helpers/`, and `utils.py` and exposes the 13-symbol public API of `agent_adapter_base` (see table below). |
| `utils.py` | Agent-agnostic helpers and the module-level `REPO_TREE_PREFIX` global. Owns: `is_linux_tree_path`, `normalize_linux_repo_path`, `to_patch_list`, `stamp_global_edit_idx`, `parse_trajectory_with_patch_lists`, `extract_test_files_from_trajectory`, `load_trajectory_file`. Defines the `PatchLists` type alias. |

## Subdirectories

| Dir | Purpose | Has AGENTS.md |
|---|---|---|
| `parsers/` | `TrajectoryParser` ABC, `ParserRegistry` + `parser_registry` singleton, `parse_trajectory_payload` dispatch, `UnknownTrajectoryFormat`, `CandidateTrajectory` / `Revision` types | Yes |
| `edit_parsing_helpers/` | `ActionEditExtractor` ABC + shared command extractors (sed, cat/tee heredoc, cp, rm, str_replace_editor, patch-file, python-script, tmp-file splice, git-reset detection) | Yes |
| `evaluation/` | `AgentPass`, `AgentPassesStore`, `AgentPatchesLoadStats` — bulk loading from Karena agent-patches exports | Yes |

## Registry / Dispatch Flow

Each concrete adapter registers its `TrajectoryParser` subclass with `parser_registry` (a `ParserRegistry` instance) on import. Dispatch is driven by `load_trajectory_file`:

```
load_trajectory_file(run_json_path)                 [utils.py]
  → json.load(path)
  → if top-level JSON is a list: wrap as {"events": <list>}  (bare OpenHands traj.json)
  → parse_trajectory_with_patch_lists(data, source_label=path)
      → parse_trajectory_payload(data, source_label)         [parser_registry.py]
          → parser_registry.match(data)                       # first parser whose can_parse() accepts data
          → parser_cls().parse(data, source_label=...)        # returns list[CandidateTrajectory]
          → raises UnknownTrajectoryFormat if none match
      → (UnknownTrajectoryFormat is caught → returns ([], []))
      → to_patch_list(candidate) for each candidate           # drop empty-filename edits / all-empty revisions
      → stamp_global_edit_idx(patch_list) for each list       # monotonic idx per candidate
  → returns (candidates, patch_lists)
```

`parse_trajectory_with_patch_lists` returns `([], [])` (not an exception) when no parser matches or no candidates have edits — every caller depends on this. A non-dict / non-list JSON payload also yields `([], [])`.

## `REPO_TREE_PREFIX` Global & Path Normalization

`utils.REPO_TREE_PREFIX` is a module-level `str | None`, initialized to `None`. A parser's package `__init__` must set it (to the linux checkout prefix used inside the agent's Docker container) **before** parsing. Both path helpers assert it is not `None`.

- `is_linux_tree_path(raw_path)` → `True` if `raw_path` starts with `REPO_TREE_PREFIX`, or (after `lstrip("/")`) starts with `linux/`.
- `normalize_linux_repo_path(raw_path)` → strips the prefix to get a repo-relative path:
  - starts with `REPO_TREE_PREFIX` → strip the prefix (this branch wins first, so `<prefix>/tmp/...` maps to `tmp/...`).
  - starts with `/tmp/` (or is exactly `/tmp`) → `""` (host scratch, not in tree — callers drop the edit).
  - else `lstrip("/")`, then strip a leading `linux/`; a leading `tmp/` (or bare `tmp`) → `""`.

Edits whose normalized filename is `""` are dropped by `to_patch_list`. External absolute paths (e.g. `/tmp/`, `/home/`) are deliberately preserved raw by several extractors (via `is_linux_tree_path`) for adapter-level stashing/remap.

## Public API (re-exports from `__init__.py`)

| Symbol | Source | Purpose |
|---|---|---|
| `TrajectoryParser` | `parsers/` | ABC for all format-specific parsers |
| `ParserRegistry` | `parsers/` | Ordered registry class for auto-dispatch |
| `parser_registry` | `parsers/` | The shared registry instance adapters register with |
| `parse_trajectory_payload` | `parsers/` | Registry dispatch (raises `UnknownTrajectoryFormat`) |
| `UnknownTrajectoryFormat` | `parsers/` | Raised when no parser accepts a payload |
| `CandidateTrajectory` | `parsers/trajectory_types.py` | One parsed candidate (revisions + `success_step`) |
| `Revision` | `parsers/trajectory_types.py` | Feedback-delimited segment of a trajectory |
| `ActionEditExtractor` | `edit_parsing_helpers/` | ABC for action-text → `Edit` conversion |
| `normalize_linux_repo_path` | `utils.py` | Strip repo prefix from file paths |
| `REPO_TREE_PREFIX` | `utils.py` | Module-level prefix string (set by parser `__init__`) |
| `load_trajectory_file` | `utils.py` | JSON load + registry dispatch |
| `parse_trajectory_with_patch_lists` | `utils.py` | Full parse pipeline (dispatch → patch_lists → stamp) |
| `stamp_global_edit_idx` | `utils.py` | Assign monotonic `global_edit_idx` per candidate |
| `to_patch_list` | `utils.py` | Convert one `CandidateTrajectory` → `patch_list` |

> Note: `ActionEditExtractor` is re-exported directly from `edit_parsing_helpers.action_edit_extractor`. `extract_test_files_from_trajectory` and `is_linux_tree_path` live in `utils.py` but are **not** re-exported from `__init__.py`.

## Key Patterns

- **`patch_list` shape** — `List[Tuple[None, List[Edit]]]`; `parent_diff` is always `None` (no instrumentation here). `PatchLists` = `List[patch_list]` (one per candidate).
- **`to_patch_list`** drops edits with empty `filename`, and omits any revision whose edits are all empty.
- **`stamp_global_edit_idx`** indices are unique within a candidate, not across candidates.
- **`extract_test_files_from_trajectory`** collects the final `after` content of files referenced in `feedback_commands` (via `.py` tokens, pytest `::` suffixes stripped) that were also created/modified by an edit. Must be called **after** parsing (needs `REPO_TREE_PREFIX` set).
