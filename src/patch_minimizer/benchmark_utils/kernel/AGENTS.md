# Kernel Benchmark Utilities

> **Purpose:** Kernel-benchmark-specific types and helpers used when minimizing patches for Syzbot/Linux-kernel bugs. Covers bug-metadata abstraction, the LLM decision-tree structure produced by the original Kernel Agent runs (loaded via pickle for replay/analysis), kGym job-result types, Anthropic-format conversation history, and batch git-repo maintenance. These modules were moved here from `core/`; kernel handling is a *benchmark* concern, not part of the generic minimization core.

---

## File Overview

| File | Purpose |
|---|---|
| `bug_data.py` | `BugData` and `CrashData` dataclasses — abstract JSON access for kernel bug data. `from_dict`/`from_json_file` accept both legacy kebab-case (`kernel-source-git`) and camelCase (`kernelSourceGit`) field names. `BugData` exposes getters (`get_base_commit`, `get_fix_commit`, `get_architecture`, `get_compiler`, `get_kernel_config_data`, …) that pull from the first crash and raise `ValueError`/`IndexError` on missing data. |
| `llm_response_history.py` | `LLMResponseHistory` — a NetworkX `DiGraph` decision tree recording every LLM interaction from a Kernel Agent run (node = one attempt with git diff, LLM response, valid edits, job status, crash report, conversation history, …). All node/edge access is gated by an injected `RWLock`. Serialized via `pickle`; `__setstate__` strips deprecated node attributes for backward compatibility. |
| `kgym_types.py` | Shared kGym job types (kept separate to avoid circular imports): `SpecialConditions`, `JobStatus`, `LLMHistoryNodeErrors` enums, the `PreBuilderResults`/`RunnerResults`/`BuilderResults` dataclasses, and `ResponseExtracted` — a parser that normalizes a raw job response into status + builder/runner/prebuilder result objects. |
| `conversation_types.py` | Conversation-history types in Anthropic API message format: `TextBlock`, `ToolUseBlock`, `ToolResultBlock`, `ConversationMessage`, the `ConversationHistory` container, and the `ToolNames` enum. Messages are stored as plain dicts for pickle compatibility. |
| `util_funcs.py` | Standalone helpers: `instantiate_llm_response_history` (loads a `.pkl` via `CustomUnpickler`, validating `save_dir`/`base_commit`/`bug_id`/`model_id`), the `CustomUnpickler` class (two-stage remap: legacy Kernel_Agent paths → current Kernel_Agent paths → local `patch_minimizer` equivalents, so `.pkl` files load without Kernel_Agent installed), and `get_base_commit` (reads a bug JSON → `BugData.get_base_commit`). |
| `clean_and_update_all_linux_repos.py` | Batch git-clean utility for a pool of parallel linux repos. `clean_single_repo` retries with self-healing (`sweep_stale_git_locks` + `repair_missing_head` from `core.utils`); `clean_all_linux_repos` runs them via `ThreadPoolExecutor` and tolerates ≤50% failures. `__main__` cleans `$BASE_PATH/collection_of_linux_repos/linux-1..70`. |
| `__init__.py` | Package docstring only — no re-exports. |

## Key Abstractions

### `BugData` / `CrashData`

- A `BugData` holds a list of `CrashData`; most getters read `crashes[0]`.
- `get_base_commit(parent_commit_flag)` returns `parent_of_fix_commit` when the flag is set, else the first crash's `kernel_source_commit` — this is the base checkout for minimization.
- `get_compiler()` classifies the crash's `compiler_description` string into `"gcc"`/`"clang"`.
- `from_dict` pulls the fix SHA out of the `fix-commits`/`fixCommits` list (first entry's `hash`/`hashValue`).
- `from_syzbot_data` is intentionally commented out (it depended on the Kernel Agent nightly pipeline, unused by minimization).

### `LLMResponseHistory` — decision tree

| Concept | Detail |
|---|---|
| Structure | `networkx.DiGraph`; root node is `"0"`; node ids are stringified integers from `max_index`. |
| Node attributes | Fixed list from `get_node_attributes()` (`parent_git_diff`, `llm_response`, `valid_edits`, `final_git_diff`, `job_id`, `job_status`, `crash_report`, `bug_resolved`, `node_error`, `depth`, `conversation_history`, …). `add_node` initializes them all to `None`. |
| Traversal helpers | `get_parent_of_node`, `get_children_of_node`, `collect_all_nodes` (BFS), `collect_solved_nodes` (`bug_resolved is True`), `collecting_remaining_nodes`, `get_history_of_edits`. |
| Persistence | `save_interactions` pickles a deep copy; per-node artifacts written under `save_dir/<model_id>__<bug_id>/<node>/`. `get_unique_id(model_id, bug_id)` builds the `<model_id>__<bug_id>` key. |
| Conversation history | `get_or_create_conversation_history`, `inherit_conversation_history` (child copies parent's), `add_tool_invocation_to_history` (also persists per-node and a global `conversation_history_global.json`). |

### `ResponseExtracted` (kgym_types)

Wraps a raw job response. `populate_status` maps a status string → `JobStatus`; `populate_special_status` maps crash/message text → `SpecialConditions` (e.g. `MESSAGE_NO_CRASH` ⇒ bug resolved). Getters (`get_status`, `get_crash_description`, `get_vm_image_url`, `get_patch_feedback`, …) expose the sub-result objects, returning `None` when absent. `wait_for_job()` is True while status is pending/in-progress/waiting.

## Key Patterns & Invariants

- **RWLock everywhere in `LLMResponseHistory`.** Nearly every method takes `use_lock`/`rw_lock`. When `use_lock=True` the method acquires the lock (write for mutations, read for reads); when `False` the caller must already hold it. Internal `base_function`s assert `rw_lock.is_w_locked()`. This dual-path style avoids re-entrant deadlock on the non-reentrant `RWLock`.
- **Pickle backward compatibility.** `LLMResponseHistory.__setstate__` deletes deprecated node attributes (`applied_lessons`, `system_prompt`, …) from old pickles. `CustomUnpickler.find_class` does two-stage remapping: first legacy Kernel_Agent renames (e.g. `kernel_write_strategies.edit` → `tools.generate_patch.edit`), then Kernel_Agent → `patch_minimizer` equivalents (e.g. `Kernel_Agent.environment.llm_response_history` → `patch_minimizer.benchmark_utils.kernel.llm_response_history`) so `.pkl` files load without Kernel_Agent installed.
- **Messages as dicts.** `ConversationHistory` stores each message as a plain dict (via each block's `to_dict()`), not as dataclass instances, so the whole history pickles cleanly. Use `to_list()` to get Anthropic-API-ready message dicts.
- **Circular-import avoidance.** `kgym_types.py` exists purely to hold enums/dataclasses shared across modules without import cycles; `conversation_types.py` and `bug_data.py` are similarly dependency-light. `llm_response_history.py` imports from both plus `core.rw_lock`.
- **Self-healing repo cleaning.** `clean_single_repo` sweeps stale `*.lock` files and repairs a missing `.git/HEAD` before each `git reset --hard`/`git clean -fd`, retrying up to `max_retries`. `clean_all_linux_repos` only raises when >50% of repos fail.
