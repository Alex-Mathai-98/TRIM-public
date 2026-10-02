# Kernel CLI

> **Purpose:** CLI entry points and shared helpers for kernel minimization. Two paths:
> `traj` (single trajectory file → `minimize_edits()`) and `kagent` (kAgent `.pkl` batch).

## Usage

```bash
# Dispatcher
python -m patch_minimizer.frontends.kernel.cli <subcommand> [args...]

# Single trajectory (openhands or mini-swe-agent)
python -m patch_minimizer.frontends.kernel.cli traj \
    --agent openhands --traj /path/to/traj.json --bug-id <hash> \
    --benchmark-folder /path/to/bugs --repo-dir /path/to/linux-501

# kAgent batch
python -m patch_minimizer.frontends.kernel.cli kagent \
    --results-dir ... --benchmark-folder ... --golden-subset ... --linux-dirs ...
```

---

## File Overview

| File | Purpose |
|---|---|
| `main.py` | Subcommand dispatcher: `kagent` → `kagent/kagent_cli`, `traj` → `traj_cli`. Maps `traj_cli.main()`'s `dict \| None` to exit code 0/1 (it used to pass the dict to `sys.exit`, printing it and exiting 1) and calls it with `print_summary=True`. |
| `__main__.py` | Enables `python -m patch_minimizer.frontends.kernel.cli`. |
| `traj_cli.py` | Single-trajectory CLI. Takes `--agent`, `--traj`, `--bug-id`, `--repo-dir`. Registers the adapter, parses the trajectory, calls `minimize_edits()`. Returns `dict \| None`. Supports `openhands`, `mini-swe-agent` and `swe-agent`. `--only-parse` returns `{candidate, patch_list}` right after parsing and skips the benchmark-folder / `--repo-dir` checks (`--repo-dir` is then optional) — used by `tests/parsing_benchmark_tests/live_kbench_*/`. With `print_summary=True` (command-line runs only) it first prints a `Parsed:` summary (nodes, edits, files changed); Python callers and the tests leave it `False` so ~3,200 test calls stay quiet. |
| `kagent/` | The kAgent/`.pkl` path, kept apart from the trajectory agents. See its `__init__.py`. |
| `kagent/kagent_cli.py` | Thin wrapper — imports and calls `minimize_edits_for_kagent.main()`. |
| `minimize_edits.py` | `minimize_edits()` — bring-your-own `patch_list` API. Builds `KernelEnvironment`/`DryRunEnvironment`, calls `minimize_edit_list()` from `strategies/minimize_core`. |
| `kagent/minimize_edits_for_kagent.py` | `ToolSolnMinimize` class (loads `.pkl` via `kagent_adapter`, loops over trajectories), `minimize_bug_solution()` (per-bug wrapper), `run_minimization_on_results()` (batch driver with repo pool, threading, resume), `main()` (CLI). |
| `common.py` | Shared helpers for orchestration scripts: `load_bug_data()`, `load_golden_subset()`, summary management (`load_existing_summary`, `append_and_save`, `check_summary_orphans`), the `minimize_one_traj` worker, and linux pool helpers (`linux_dir_queue_from_paths`, `build_linux_pool_paths`). The bug_config chain (`minimize_one_bug`, `run_parallel*`) and the count-based `check_summary_consistency` were removed. |

## Key design decisions

- **`traj_cli.main()` is the atomic unit** — one trajectory file, one bug, one result.
  Orchestration (directory walking, repo pooling, resume) lives in the calling scripts
  (`example_code/run_openhands_minimization.py`, etc.), not here.
- **`traj_cli.main()` returns `dict | None`**, not an exit code. One trajectory produces
  one `CandidateTrajectory`, so the return is a single result dict. Callers get results
  immediately for real-time summary updates.
- **`minimize_one_traj()` in `common.py`** wraps `traj_cli.main()` with repo-pool
  management — acquires a linux dir from the queue, builds argv, calls `traj_cli.main()`,
  returns the result, releases the repo.
