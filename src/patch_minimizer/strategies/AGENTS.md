# Solution Minimization Strategies

> **Purpose:** Top-level minimization orchestration for TRIM (patch-minimizer). This directory holds the standalone `minimize_edits()` entry point, git repo/patch application (`RepoPatchManager`), post-run solution-path analysis, and feedback-stat aggregation. The actual removal algorithms, pluggable test environments, and rule engines live in the subdirectories listed below (owned/documented by their own `AGENTS.md`).

---

## Top-level files

| File | Purpose |
|---|---|
| `minimize_core.py` | **The benchmark-agnostic core.** `minimize_edit_list()` takes a `patch_list` plus an already-built minimizer and `FeedbackReceiver`, dispatches to `find_independent_nodes` / `find_independent_edits` by `minimize_at`, and returns the result dict. Knows nothing about any benchmark — callers build their own environment. Also holds `serialize_edit_path()`, `save_minimization_results()` (writes `minimization_results.json`) and `_save_summary_atomic()` (writes `minimization_summary.json`); both output files have shared readers, so these are deliberately *not* duplicated per benchmark. The minimizer and aggregator are parameters, not constructed here, because they carry state that must survive across calls (result cache; rolling diff baseline). |
| `feedback_stats.py` | `_summarize_feedback_stats()` — converts detailed feedback counters into the summary shape. Pure stdlib dict work; lives here because `aggregate_feedback_stats.py` (core) depends on it. |
| `repo_patch_manager.py` | `RepoPatchManager` — git repo cleaning, patch application, and diff generation for a single repo clone. Uses `EditApplier` and consumes its `ReplaceResult` (`.start_line`/`.end_line`/`.new_content`). `check_patch_range` sequentially applies patches after an optional `parent_diff`, tracking a per-file cumulative `line_offset_by_file`. `swebench_mode` short-circuits clone-side pre-validation for container-only scratch files. `_resolve_file_path` handles `WHOLE_FILE_ACTIONS`/`COPY_FILE`/`WHOLE_FILE_DELETE` and `best_effort` edits. |
| `aggregate_feedback_stats.py` | `aggregate_feedback_stats_from_summary()` — standalone utility that sums `feedback_stats.detailed` across `successful_bugs` in a `minimization_summary.json`, summarizes via `feedback_stats._summarize_feedback_stats`, and writes back `aggregated_feedback_stats` atomically. Has a `__main__` CLI. |

## Subdirectories

| Dir | Purpose (see the dir's own `AGENTS.md`) |
|---|---|
| `algorithms/` | Minimization drivers: `NodeMinimizer`, `EditMinimizer`, greedy/coupling removal logic. |
| `environments/` | Benchmark-agnostic environment contract and generic implementations: `BaseEnvironment`, `DryRunEnvironment`, `GitDiffEnvironment`, `NeuralEnvironment`, feedback types, and `FeedbackReceiver` aggregator. Benchmark-specific environments live in `frontends/`. |
| `rule_engines/` | Rule engines deciding which edits to try removing each round. |

## Key invariants

- **DD / agent coord-system split.** `Edit.starting_line` is interpreted in ORIGINAL-file coordinates when `edit.is_dd_hunk=True` and in LIVE-file coordinates otherwise (agent adapters, LLM patches, tests). `check_patch_range` is the sole enforcement point: it applies the cumulative per-file offset only to DD hunks and leaves non-DD edits untouched.
- **Per-file offset tracking.** `line_offset_by_file` is a `dict` keyed by filename, not a single global offset — line shifts in one file must not leak into another (multi-file patch correctness).
- **Edit objects are reused across iterations.** `check_patch_range` saves each edit's `starting_line` and restores it in a `try/finally`, so the same `Edit` instances can be passed through many rounds without permanent mutation.
- **Write lock discipline.** `apply_single_patch`, `check_single_patch_application`, `check_patch_range` (with `use_lock=False`), and `generate_git_diff` assert the caller holds the write lock. Kernel diffs embed `collection_of_linux_repos/<repo>/` prefixes; SWE-bench / generic repos get plain `a/`/`b/` prefixes.
- **Repo pool safety.** In the batch driver, each worker `get()`s exactly one `(linux_dir, code_dir_lock)` tuple and `put()`s it back in `finally`; on error the git state is force-cleaned (`reset --hard`, `clean -fd`, remove `index.lock`) before returning the repo.
- **Incremental summaries.** `minimization_summary.json` is written atomically (temp + `os.replace`) after every result so an interrupted run can resume; successful bugs are never retried, failed bugs are retried only with `retry_failed_only`.
