# Kernel Frontend

> **Purpose:** Everything specific to the kernel (kGym / Syzkaller) benchmark — the runtime
> environment that submits real kernel build+test jobs, CLI entry points for minimization,
> and batch orchestration. Benchmark-agnostic minimization lives in
> `strategies/minimize_core.py`; this package supplies the kernel half.

## Layering

**L2** in the layer model. Depends on `core/` (L0), `strategies/` (L1) and
`benchmark_utils/kernel/`. Nothing in `core/` or `strategies/` may import from here.

---

## Directory Overview

| Dir / File | Purpose |
|---|---|
| `cli/` | CLI entry points and shared helpers; `cli/kagent/` holds the kAgent `.pkl` path. See `cli/AGENTS.md`. |
| `environment.py` | `KernelEnvironment` — submits a candidate patch to kGym/kBDR and returns `RuntimeFeedback`. |
| `utils/` | `results_discovery.py` (bug/folder discovery in results trees), `environment_reconstruction.py` (EnvironmentSnapshot), `run_kernel_job.py` (standalone kGym job submission). |
| `example_code/` | Orchestration scripts for non-kAgent agents + offline demos. See its own `AGENTS.md`. |

---

## Which entry point do I want?

```
Have a patch_list already in memory?           → cli/minimize_edits.minimize_edits()
Have one trajectory file + bug_id?             → python -m patch_minimizer.frontends.kernel.cli traj ...
Have a saved results tree (.pkl) for kAgent?    → cli/kagent/minimize_edits_for_kagent.ToolSolnMinimize
Have a whole results directory (kAgent)?        → python -m patch_minimizer.frontends.kernel.cli kagent ...
Batch over openhands/mini-swe trajectories?     → example_code/run_openhands_minimization.py
Have a Karena agent-patches export (swe-agent)? → example_code/run_swe_minimization.py
```

| Use case | Entry point |
|---|---|
| Bring-your-own `patch_list` | `cli.minimize_edits.minimize_edits(...)` |
| One trajectory file (any agent) | `python -m patch_minimizer.frontends.kernel.cli traj --agent X --traj ... --bug-id ...` |
| One bug, from kAgent `.pkl` tree | `cli.kagent.minimize_edits_for_kagent.ToolSolnMinimize(...).minimize_solution()` |
| Many bugs, kAgent batch | `python -m patch_minimizer.frontends.kernel.cli kagent --results-dir ...` |
| Many bugs, openhands/mini-swe batch | `example_code/run_openhands_minimization.py --results-dir ...` |
| Many bugs, swe-agent (Karena export) | `example_code/run_swe_minimization.py --agent-patches-dir ...` |

---

## Key patterns / invariants

- **Repo pool discipline.** Each batch worker `get()`s exactly one `(linux_dir, code_dir_lock)`
  tuple and `put()`s it back in `finally`. On error the clone is force-cleaned
  (`reset --hard`, `clean -fd`, remove `index.lock`) before being returned.
- **One minimizer per bug, not per trajectory.** `ToolSolnMinimize.setup()` builds the minimizer
  and `FeedbackReceiver` once and reuses them across every trajectory. This is load-bearing: the
  minimizer owns `PatchCandidateTester._result_cache` and the aggregator owns
  `GitDiffEnvironment._baseline_total`. Rebuilding them per trajectory would discard the cache
  (extra kGym jobs) and reset the diff baseline (different drop decisions).
- **Incremental summaries.** `run_minimization_on_results` writes `minimization_summary.json`
  atomically (temp + `os.replace`) after every result, so an interrupted run resumes. Successful
  bugs are never retried; failed ones only with `retry_failed_only`.
- **`traj_cli.main()` returns `dict | None`** — a single result dict from `minimize_edit_list()`.
  One trajectory file produces one candidate. Orchestration scripts call `minimize_one_traj()`
  from `cli/common.py`, which wraps `traj_cli.main()` with repo-pool management.

## Known gaps

- **`ToolSolnMinimize` does not use `minimize_core`.** `minimize_one_path()` has its own
  dispatch, bypassing `minimize_edit_list()`. Two code paths for the same logic.
- **No behavioural test gate.** Kernel changes can only be verified structurally unless real
  Linux clones and kGym access are available.
- ~~**SWE-agent CLI support deferred.**~~ Closed. `traj_cli.py` supports all
  three agents. `run_swe_minimization.py` still owns discovery (`AgentPassesStore` +
  Karena DB) but minimizes each pass through `traj_cli`, so every trajectory agent now
  reaches `minimize_edits` by one route: `minimize_one_traj` → `traj_cli`.
