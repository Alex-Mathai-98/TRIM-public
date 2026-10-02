# Kernel Example / Offline Scripts

> **Purpose:** Standalone, runnable scripts that exercise kernel minimization end-to-end for
> offline experimentation, dataset preparation and verification. These are **examples and one-off
> tools** — the maintained kernel entry points are `../minimize_patches.py` (`minimize_edits`,
> `run_minimization_on_results`, `main()`) and `../../../runners/`. Treat these as reference
> implementations that may lag.
>
> Moved here from `strategies/example_code/` — they are kernel-specific and do not belong in the
> benchmark-agnostic `strategies/` layer. The SWE-bench counterparts live in
> `../../swebench/example_code/`.

---

## File Overview

| File | Purpose |
|---|---|
| `sample_minimize_edits.py` | Runnable demo of `minimize_edits()` on a real kernel bug using in-memory `Edit` objects. Shows `run_jobs=True` (KernelEnvironment, real kGym/kBDR builds) vs `run_jobs=False` (DryRunEnvironment). The canonical "how do I call the API" example. |
| `run_swe_minimization.py` | SWE-Agent **on kernel bugs**: parses Karena `traj.json` (`events[]`) → edits → `minimize_edits()`. Resolves `bugId` from `agentPatchId` via the Karena SQLite DB. Argument parsing only — discovery and execution live in `run_swe_agent/` (`agent_patches.load_passes()` → `agent_patches.run()`), which minimizes each pass through `traj_cli`. |
| `run_openhands_minimization.py` | OpenHands runner. Walks `results/<agent_config>/run_N/<bug_hash>/original/traj.json` and calls `traj_cli` per candidate via `minimize_one_traj()` from `cli/common.py`. |
| `run_mini_swe_minimization.py` | mini-SWE-Agent runner. Same `run_N/<bug_hash>/original/` layout; trajectory format is `messages[]`. Same `minimize_one_traj()` path. |
| `verify_swe_agent_dataset.py` | Dry verification (no kBDR jobs) run before `run_swe_minimization.py --run-jobs`: checks `agentPatchId`→`bugId` DB rows, benchmark JSON presence, and that parsed edits apply at the parent-of-fix commit. |

## Key patterns

- **Env bootstrap.** Each script infers `KAGENT_PATH` / `BASE_PATH` by walking up from `__file__`
  to the directory containing `src/patch_minimizer`, then uses `os.environ.setdefault(...)` so
  exported vars win. **This replaced a hardcoded `__file__.parents[6]`**, which broke when these
  files moved (it silently resolved to `/` at the new depth rather than raising).
- **One worker for all three agents.** Each runner discovers candidates and
  hands `(agent, traj_json, bug_id)` to `minimize_one_traj()` in `cli/common.py`, which takes a
  repo from the queue and calls `traj_cli.main()`. Sequential is the degenerate case: a
  one-entry pool driven by a `for` loop instead of a `ThreadPoolExecutor`.
- **Key on `pass_id`, not `bug_id`, for swe-agent.** Output is
  `save_dir/agent_patch_<id>/swe_candidate_0/`. The openhands runners key on `bug_id`; copying
  that into `run_swe_agent/agent_patches.py` would break resume and `discover_pairs.py`.
- **Parser registration by side-effect.** Scripts `import` the relevant `agent_adapters.*_adapter`
  module (`# noqa: F401`) to register its parser before parsing.

## Known issues

- `verify_swe_agent_dataset.py` **cannot currently be imported.** Its bootstrap is fixed, but it
  then hits a **pre-existing circular import** between `core/edit_applier.py:40` and
  `agent_adapters/.../sed_parsing.py` (each reaches into the other). Reproducible in two lines,
  unrelated to this directory:
  ```python
  import patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.sed_parsing
  import patch_minimizer.core   # ImportError: partially initialized module
  ```
  Fixing it means breaking the `core` → `agent_adapters` edge; `core/` should not depend upward.
- These scripts assume an internal env layout (`collection_of_linux_repos/linux-N`, `dev.env`,
  Karena SQLite backups) and external infra (kGym/kBDR). They are not part of the importable
  public API of `patch_minimizer`.
