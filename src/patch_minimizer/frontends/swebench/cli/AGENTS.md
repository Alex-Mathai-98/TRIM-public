# SWE-bench CLI

> **Purpose:** The single-instance entry point plus the orchestration infra the
> `example_code/` runners share. Mirrors `frontends/kernel/cli/`.

## Usage

```bash
python -m patch_minimizer.frontends.swebench.cli traj \
    --agent swe-agent \
    --traj results/swe-bench-swe-agent-trajs/sphinx-doc__sphinx-8459.traj \
    --bug-id sphinx-doc__sphinx-8459 --save-dir /tmp/min_one --minimize-at edit
```

`--agent` is required, same as the kernel CLI, but only `swe-agent` is accepted; any other value
is a `parser.error` (exit 2) until SWE-bench gets parsers for other scaffolds. `minimize_traj()`
callers (batch driver, parsing tests) bypass argparse and are unaffected.

Add `--only-parse` to stop after parsing and print the prepared patch list (no repo, Docker or HF
needed).

A whole directory is the batch driver's job —
`example_code/run_swebench_minimization.py --traj-dir ...` — not a subcommand here.

## File Overview

| File | Purpose |
|---|---|
| `main.py` | Subcommand dispatcher. One subcommand: `traj`. |
| `__main__.py` | Enables `python -m patch_minimizer.frontends.swebench.cli`. |
| `traj_cli.py` | The atomic unit. `minimize_traj(**kwargs) -> dict \| None` plus a thin argparse `main()`. Was `_process_instance` + `_run_single` inside the batch script. `only_parse=True` / `--only-parse` returns the prepared inputs (`candidate`, `patch_list`, `test_files`, `feedback_commands`, `sub_paths`) before any HF/Docker work — used by `tests/parsing_benchmark_tests/swe_bench_swe_agent/`. |
| `common.py` | `write_json_atomic`, `instance_logger` / `close_instance_logger`, `setup_dispatcher_logging`, `parse_instance_filter`, `minimize_one_traj`. |

## Key design decisions

- **`traj_cli` is stateless.** No resume, no summary, no pool — those are batch concerns
  and stay in the driver. Pushing resume in here would make a deliberate single-instance
  re-run silently return a cached result.
- **No argv round-trip.** Kernel's `minimize_one_traj` builds a list of strings for
  argparse to parse straight back, which forces `code_dir_lock` to bypass argv as a
  keyword-only argument. swebench needs two such values (the `RWLock` and a per-instance
  `Logger` with a file handler), so `traj_cli` is split into a keyword function plus an
  argparse wrapper and `common` calls the function.
- **No `common` ↔ `traj_cli` cycle.** `instance_to_repo_prefix` lives in `utils/`
  precisely so `traj_cli` never imports `common`. The one edge — `minimize_one_traj` →
  `minimize_traj` — is function-level. Kernel has the cycle and resolves it
  asymmetrically; do not reintroduce it here.
- **Clone pool, same shape as kernel.** `minimize_one_traj` takes
  `repo_dir_queue` — a `Queue` of `(clone path, RWLock)` for one repo, built by the
  driver's `_build_clone_pools`. The **queue** gives exclusivity (one worker per clone);
  the `RWLock` is passed through as `code_dir_lock` and guards the working tree. In
  `finally` a still-held write lock is force-released (as in
  `kernel/cli/common.py:228-235`) before the entry goes back, so a crashed instance
  can't poison that clone. When called directly (the single-instance CLI), `minimize_traj`
  still mints its own `RWLock`, since none is passed.
