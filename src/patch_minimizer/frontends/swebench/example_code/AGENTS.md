# SWE-bench Example / Offline Scripts

> **Purpose:** The runnable scripts of the reproduction pipeline, plus dataset-prep and
> verification tools.
>
> Note the split: `run_swebench_minimization.py` (pipeline step 2) and `swebench_pipeline/`
> (steps 3-5) are **maintained pipeline entry points**, not examples. The other three files
> here are one-off dataset-prep / verification tools and may lag. Single-instance minimization
> is not here at all — it is `python -m patch_minimizer.frontends.swebench.cli traj`.
>
> Moved here from `strategies/example_code/` — they are SWE-bench-specific and do not belong in
> the benchmark-agnostic `strategies/` layer. The kernel counterparts live in
> `../../kernel/example_code/`.

---

## File Overview

| File | Purpose |
|---|---|
| `run_swebench_minimization.py` | **Pipeline step 2**, batch only. Discovers `<instance_id>.traj` files, resolves every base commit up front, then runs `cli/common.py::minimize_one_traj` per instance on a clone from that repo's pool (`--clones-per-repo K`; jobs interleaved across repos). Resume-safe via `minimization_summary.json`; `--then-postprocess` / `--then-reconstruct` optionally chain steps 3 and 5. Contains no minimization logic — that is `cli/traj_cli.py`. |
| `swebench_pipeline/` | **Pipeline steps 3-5** — postprocess, consolidate, reconstruct. See [its AGENTS.md](swebench_pipeline/AGENTS.md). |
| `download_swe_bench_swe_agent_trajs.py` | **Step 1** of the reproduction recipe. Downloads SWE-agent SWE-bench Verified `.traj` files for resolved instances: reads `results.json` from the SWE-bench/experiments GitHub repo, then pulls trajectories from the public S3 submissions bucket. Dataset-prep tool (M1.A); the first link in the chain documented in [`../AGENTS.md`](../AGENTS.md). |
| ~~`verify_swe_bench_edit_applicability.py`~~ | Moved to `tests/parsing_benchmark_tests/swe_bench_swe_agent/` (it is a parsing test, not a pipeline step). |
| `profile_swe_bench_proxy_test_edits.py` | Profiles proxy-test-file edits across trajectories (M1.A): finds files the agent both created and ran, counts subsequent writes, and categorizes each as single-write / tweaked / rewritten to judge whether the trajectory-final test version can serve as the oracle. |

## Key patterns

- **Env bootstrap is `utils/env.py::bootstrap_env()`**, one copy, which **raises** when the
  project root is not found. Do not reintroduce a per-script copy with a fixed-depth fallback:
  a hardcoded `parents[N]` raises `IndexError` at one depth and silently resolves to the wrong
  directory at another, which is exactly how the old copies broke when files moved.
- **`PYTHONPATH=src` (or an editable install) is required.** `bootstrap_env` sets
  `KAGENT_PATH`/`BASE_PATH` but no longer manufactures import paths — reaching it already
  requires the package to be importable.

## Removed

`run_swebench_postprocessing.py` used to live here as a second copy. Git shows it was **not** a
parallel variant that drifted — it was the original (`c93dd61`, "first version", 2026-07-08),
superseded five days later by `../run_swebench_postprocessing.py` (`a28b568`, "add SWE-Bench
reproduction pipeline", 2026-07-13). Each had exactly one commit; the old one was simply never
removed. It lacked all five oracle-correctness functions of the maintained version
(`_strip_already_applied_hunks`, `_ensure_remote_image`, and three helpers) and had nothing
unique of its own, so running it would have produced oracle results missing those corrections
with no error to warn you. Deleted.

## Note

These scripts assume SWE-bench Docker images (`sweb.eval.x86_64.<instance_id>`) and repo clones
under `results/swe-bench-workspace/`. They are not part of the importable public API.
