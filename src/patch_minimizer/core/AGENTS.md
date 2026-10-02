# Core Types & Utilities

> **Purpose:** Foundational data types, the edit-application engine, concurrency primitives, and git repo helpers that the rest of `patch_minimizer` depends on. `core/` sits at the bottom of the import graph — it does not import from `strategies/` or `agent_adapters/` (no exceptions).

---

## File Overview

| File | Purpose |
|---|---|
| `edit.py` | `Edit` dataclass — the atomic unit of code modification (before/after + metadata). Also `EditType` (30 semantic variants) and `MatchMode` (3 matching algorithms) enums, plus `Edit.generate_search_replace_diff()`/`generate_structured_format()` serializers and the `get_common_lines`/`extract_prefix` helpers. |
| `node.py` | `Node` dataclass — a group of semantically-related `Edit`s (SEuF). Iterable/`len`-able wrapper (behaves like `List[Edit]`) with a stable `global_node_idx` for tracking through minimization phases. |
| `edit_applier.py` | `EditApplier` — dispatches an `Edit` to the correct in-memory match/replace routine and writes the result to disk. Contains the exact/fuzzy/semi-fuzzy match engine, per-`EditType` `perform_in_memory_edit_*` methods, sed BRE→Python-regex conversion, the `ReplaceResult` dataclass, and module-level `check_edit_application`/`apply_edit_to_linux_code` wrappers over a singleton applier. Requires the caller to hold `RWLock` (write) for the two public entry points. |
| `coupling_types.py` | `CouplingMap` and `CouplingGroup` — map edit indices to coupling groups for O(1) lookup during coupling-aware minimization. |
| `rw_lock.py` | `RWLock` (readers-writer lock, two-mutex algorithm) and `GitRWLock` (subclass that sweeps stale `.git/index.lock` on release). Guards shared Linux-repo access during parallel minimization. |
| `utils.py` | `Utils` class with git repo helpers (`repo_clean_changes`, `repo_reset_and_clean_checkout`, `set_correct_branch`, `apply_git_diff`, `run_command`, `find_file`) plus module-level `repair_missing_head` and `sweep_stale_git_locks` self-healing functions, and `unescape_sed_replacement` (sed text unescaping shared with `sed_parsing`). |
| `__init__.py` | Re-exports: `Edit`, `EditType`, `MatchMode`, `Node`, `RWLock`, `GitRWLock`, `CouplingMap`, `EditApplier`. |

> **Note:** `bug_data.py`, `llm_response_history.py`, `kgym_types.py`, `conversation_types.py`, `util_funcs.py`, and `clean_and_update_all_linux_repos.py` **no longer live here** — they moved to `benchmark_utils/kernel/`. `BugData` is no longer re-exported from `core/__init__` — import it directly from `patch_minimizer.benchmark_utils.kernel.bug_data`. See `benchmark_utils/kernel/AGENTS.md`.

## Key Abstractions

### EditType — 30 operation variants

`EditType` is a `str` enum grouped by operation family. `EditApplier.check_edit_application` dispatches on it.

| Category | Types | Origin |
|---|---|---|
| **Replace** | `REPLACE`, `REPLACE_PATCH_HUNK`, `REPLACE_ONCE_AT_LINE`, `REPLACE_MULTI_AT_LINE`, `REPLACE_ONCE_PER_LINE`, `REPLACE_MULTI_PER_LINE`, `REPLACE_MULTI_PER_LINE_IN_RANGE`, `REPLACE_ONCE_PER_LINE_IN_RANGE`, `REPLACE_ONCE_PER_LINE_PATTERN`, `REPLACE_JOINED_NEXT` | str_replace, patch hunks, sed `s///` |
| **Delete** | `DELETE_LINE`, `DELETE_RANGE`, `DELETE_PATTERN`, `DELETE_PATTERN_RANGE`, `DELETE_PATTERN_NEXT` | sed `d` |
| **Insert** | `INSERT_BEFORE_LINE`, `INSERT_AFTER_LINE`, `INSERT_AFTER_PATTERN`, `INSERT_BEFORE_PATTERN` | sed `a`/`i`, str_replace_editor insert |
| **Change** | `CHANGE_LINE`, `CHANGE_RANGE`, `CHANGE_RANGE_PER_LINE`, `CHANGE_PATTERN_RANGE` | sed `c` |
| **File-level** | `COPY_FILE`, `WHOLE_FILE_DELETE`, `WHOLE_FILE_ACTIONS`, `WHOLE_FILE_REWRITE`, `WHOLE_FILE_CREATE` | `cp`, `rm`, cat/tee heredoc, str_replace_editor create |

`WHOLE_FILE_ACTIONS` is a parser-level placeholder: the applier resolves it to `WHOLE_FILE_REWRITE` (file exists) or `WHOLE_FILE_CREATE` (file absent) at apply time. `COPY_FILE` and `WHOLE_FILE_ACTIONS` are dispatched *before* the file is read, because the target may not exist yet.

### MatchMode — 3 matching algorithms (for `EditType.REPLACE`)

`perform_in_memory_edit_REPLACE` sub-dispatches on `edit.match_mode`:

| Mode | Algorithm | Used by |
|---|---|---|
| `FUZZY` | Whitespace-tolerant line-by-line matching (`perform_fuzzy_match`, strips each line before comparing). Empty `before` with no `starting_line` falls through to whole-file rewrite. | Kernel Agent |
| `SEMI_FUZZY` | Exact match first (`perform_exact_match`); on miss, retry with `before`/`after` stripped. | OpenHands |
| `EXACT` | Literal substring match (`perform_exact_match`). | SWE-Agent (str_replace) |

### `ReplaceResult`

Returned by every `perform_in_memory_edit_*` method. Carries `orig_prog_lines`, `new_content`, `start_line`/`end_line` (0-indexed match span), and a `match_type` tag. The module-level sentinel `_DEFAULT_RESULT` (all-`None` fields) signals "no match" — callers compare against it or check `new_content is None`.

### Coordinate System (`Edit.starting_line`)

- `is_dd_hunk=True` → `starting_line` is in ORIGINAL-file coords (DD baseline hunks only; `repo_patch_manager` adjusts by the cumulative per-file offset from prior edits).
- `is_dd_hunk=False` (default, all agent adapters) → LIVE-file coords. **No** offset adjustment.

## Key Patterns

- **Singleton applier.** `edit_applier._default_applier` backs the module-level `check_edit_application`/`apply_edit_to_linux_code` functions so callers need not instantiate `EditApplier`.
- **sed BRE handling.** `edit.before` arrives raw (escaped) from the sed parser. The applier tries a literal (unescaped) match first, then falls back to `_bre_to_ere` regex. `_bre_to_ere` protects BRE specials with sentinels before escaping BRE-literal chars (known gap: does not handle `sed -E`/`-r` ERE mode).
- **No upward imports.** `edit_applier.py` used to import `_unescape_sed_replacement` from `agent_adapters/.../sed_parsing`, which caused an import cycle when `agent_adapter_base` was imported first. The function now lives in `utils.py` (`unescape_sed_replacement`) and `sed_parsing` imports it from here. Keep it that way.
- **`mkdir -p` before write.** `apply_edit_to_linux_code` creates parent dirs before writing (COPY_FILE and the generic write path), so agents targeting new nested paths (e.g. SWE-bench `create /testbed/newpkg/x.py`) succeed.

## Key Invariants

- **RWLock discipline.** `check_edit_application` and `apply_edit_to_linux_code` both `assert code_dir_lock.is_w_locked()` — the caller must hold the write lock. Same for the repo-mutating `Utils` methods (`repo_clean_changes`, `repo_reset_and_clean_checkout`, `set_correct_branch`, `apply_git_diff`) when `use_lock=False`.
- **Non-reentrant write lock.** `RWLock.w_acquire_nowait` raises `AssertionError` on same-thread re-acquire (deadlock guard) and on cross-thread contention (fail-fast). Used only in the minimization module.
- **`global_edit_idx` / `global_node_idx` are ephemeral.** Stamped per-path for tracking through minimization passes — not serialized, not stable across runs.
- **`DELETE_RANGE` requires empty `after`.** `perform_in_memory_edit_DELETE_RANGE` asserts `edit.after == ""`.
- **Git self-healing on checkout.** `repo_reset_and_clean_checkout` sweeps stale locks and repairs a missing `.git/HEAD` before resetting, and fetches orphaned commits as a remote ref when a plain checkout fails (requires `kernel_base_url`).
