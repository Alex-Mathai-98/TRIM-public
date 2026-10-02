# SWE-bench Frontend

> **Purpose:** Everything SWE-bench-specific — the Docker test environment, the submission-path
> filter, and the maintained CLI entry points for the SWE-bench-Verified reproduction of the
> paper's Table IV (RQ4). The pipeline chains end-to-end: minimize the 333 SWE-agent trajectories
> → hidden-oracle + gold comparison → (optional) recovery merge → churn-free agent-patch
> reconstruction → Table IVa/b/c + the "18" gold-identical count. Table IVd (SlopCodeBench
> verbosity) lives in `metrics/scbench_metric/`. Run all CLIs from the **repository root**.

## Layering

This package is **L2** in the layer model (see `claude_plans/frontend-split-clean-design.md`): it depends on
`core/` and `strategies/`, and nothing in those layers may import from here. Benchmark-agnostic
minimization lives in `strategies/minimize_core.py::minimize_edit_list`, which this frontend
calls after building its own `SWEBenchEnvironment` and minimizer.

| Path | Role |
|---|---|
| `environment.py` | `SWEBenchEnvironment` — Docker-based test execution |
| `submission_filter.py` | `filter_to_submission_paths` — drops scratch edits the agent never submitted |
| `cli/` | The single-instance entry point (`traj_cli`) + shared orchestration infra (`common.py`). See [`cli/AGENTS.md`](cli/AGENTS.md). |
| `utils/` | Domain helpers — env bootstrap, diff parsing, HF access, instance ids. See [`utils/AGENTS.md`](utils/AGENTS.md). |
| `example_code/run_swebench_minimization.py` | Pipeline step 2, the batch driver |
| `example_code/swebench_pipeline/` | Pipeline steps 3–5. See [its AGENTS.md](example_code/swebench_pipeline/AGENTS.md). |

**Step numbering follows the canonical recipe in [`README.md`](../../../../README.md) →
"Reproducing the paper's Table IV (RQ4)" — steps 1–7.** Three of those seven live outside this
directory: step 1 in `example_code/`, and the two reporting steps (6, 7) under `metrics/`. All
seven are listed in the File Overview below so the full chain stays readable in one table.

---

## File Overview

| File | Purpose |
|---|---|
| `example_code/download_swe_bench_swe_agent_trajs.py` | **Step 1.** Download the SWE-agent SWE-bench Verified `.traj` files (~3 GB) for the pinned submission: reads `results.json` from the SWE-bench/experiments GitHub repo, then pulls trajectories from the public S3 submissions bucket. See [`example_code/AGENTS.md`](example_code/AGENTS.md). |
| `example_code/run_swebench_minimization.py` | **Step 2.** Batch only: discovers `<instance_id>.traj` files, resolves every base commit up front from SWE-bench_Verified, then minimizes each through `cli/traj_cli.py` on a clone taken from that repo's clone pool (`--clones-per-repo`). Emits per-instance `minimization_results.json` + `standalone__<id>/minimized.patch`, plus a resume-safe `minimization_summary.json`. `--then-postprocess` / `--then-reconstruct` chain steps 3 and 5. **One instance is `python -m patch_minimizer.frontends.swebench.cli traj` instead.** |
| `example_code/swebench_pipeline/postprocess.py` | **Step 3. Always run with `--force`.** Post-hoc hidden-oracle over an existing save-dir: runs `swebench.harness.run_evaluation.run_instance` on the agent submission (`oracle_pre`) and the minimized patch (`oracle_post`), plus a structural gold comparison vs the HF `patch` field (`gold_comparison`). Writes these blocks back into each `minimization_results.json` and aggregates `oracle_transitions` + `gold_equivalence` into `minimization_summary.json`. |
| `example_code/swebench_pipeline/consolidate.py` | **Step 4 (optional).** Merge manual-assert recovery runs (from `REMIN`, default `/tmp/remin`) into the canonical `CANON` (`z-validate-swebench-dataset/minimization_333`). Only instances whose `oracle_post` is genuinely resolved (applied, zero F2P/P2P failures) are merged; flaky ones are skipped and reported. Re-aggregates `aggregated_results`/`oracle_transitions`/`gold_equivalence`. `--canon` / `--remin` (defaults are the canonical paths); has a `main()` and a `__main__` guard, so importing it no longer runs the merge. |
| `example_code/swebench_pipeline/reconstruct.py` | **Step 5.** For the resolved→resolved set, rebuild each agent patch as if authored on a **clean** `base_commit` (SWE-bench env churn removed) by keeping only agent-authored diff blocks (edit-list files, new scratch files, or `rm`-collateral deletions) and `git apply`ing them to a clean tree. Writes `standalone__<id>/original_clean.patch` (feeds SCBench) with `--write`, and prints an edits/hunks/lines aggregate table. |
| `metrics/swebench/swebench_metrics.py` *(elsewhere)* | **Step 6.** Print Table IVa (compaction: edits/hunks/lines, filtered + unfiltered), IVb/IVc (file categories: source/tests/docs/build/scratch), and RQ4 gold agreement (the "18" byte-identical). Pure stdlib; reads the canonical save-dir. `--table {compaction,categories,gold,all}`. See [`../../metrics/swebench/AGENTS.md`](../../metrics/swebench/AGENTS.md). |
| `metrics/scbench_metric/run_swebench.py` *(elsewhere)* | **Step 7.** Table IVd — an independent, *quality-based* cross-check: measures SlopCodeBench "code slop" (verbosity / erosion / structural components) on the agent patch vs. the TRIM-minimized patch and reports how much slop minimization removed. Consumes `standalone__<id>/original_clean.patch` from step 5. See [`../../metrics/scbench_metric/AGENTS.md`](../../metrics/scbench_metric/AGENTS.md). |

## Pipeline Position

```
1. example_code/download_swe_bench_swe_agent_trajs.py   → results/swe-bench-swe-agent-trajs/*.traj
  └─▶ 2. example_code/run_swebench_minimization.py        → minimization_333/<id>/{minimization_results.json, standalone__<id>/minimized.patch}, minimization_summary.json
        └─▶ 3. swebench_pipeline/postprocess.py → + oracle_pre / oracle_post / gold_comparison / gold_equivalence, oracle_transitions
              └─▶ 4. swebench_pipeline/consolidate.py  (optional; merges /tmp/remin recoveries → exact 327 set)
                    └─▶ 5. swebench_pipeline/reconstruct.py → standalone__<id>/original_clean.patch
                          ├─▶ 6. metrics/swebench/swebench_metrics.py → Table IVa / IVb / IVc + gold "18"
                          └─▶ 7. metrics/scbench_metric/run_swebench.py → Table IVd (SlopCodeBench verbosity)
```

Steps 6 and 7 are **parallel**, not sequential — two independent views of the same run (structural
vs. quality-based). Numbering matches `README.md` → "Reproducing the paper's Table IV (RQ4)".

Instance set: the 333 SWE-agent (Claude-Sonnet-4) trajectories of submission `20250522_sweagent_claude-4-sonnet-20250514`; metrics reported over the **327** resolved→resolved instances (`resolved(oracle_pre) and resolved(oracle_post)`).

## CLI Usage

```bash
# Step 1 — download the trajectories (~3 GB)
python src/patch_minimizer/frontends/swebench/example_code/download_swe_bench_swe_agent_trajs.py \
    --out results/swe-bench-swe-agent-trajs

# Step 2 — single instance (the atomic unit; not the batch script)
PYTHONPATH=src python -m patch_minimizer.frontends.swebench.cli traj \
    --agent swe-agent \
    --traj results/swe-bench-swe-agent-trajs/sphinx-doc__sphinx-8459.traj \
    --bug-id sphinx-doc__sphinx-8459 \
    --save-dir /tmp/min_one --minimize-at edit
# Optional: --repo-dir (else auto = $KAGENT_PATH/results/swe-bench-workspace/<owner>__<repo>),
#           --manual-asserts-dir  (base commit is always looked up in SWE-bench_Verified)

# Step 2 — parallel directory (resume-safe; writes minimization_summary.json)
PYTHONPATH=src python src/patch_minimizer/frontends/swebench/example_code/run_swebench_minimization.py \
    --traj-dir results/swe-bench-swe-agent-trajs \
    --save-dir results/minimization_333 --num-parallel 8 --minimize-at edit
# Optional: --instance-filter a,b,c   --retry-failed   --workspace-root DIR
#           --clones-per-repo K  (K clones per repo; pair with --num-parallel ~12*K)
#           --then-postprocess --then-reconstruct  (chains steps 3 and 5)

# Step 3 — hidden oracle + gold comparison
python src/patch_minimizer/frontends/swebench/example_code/swebench_pipeline/postprocess.py \
    --save-dir results/minimization_333 --traj-dir results/swe-bench-swe-agent-trajs \
    --force --num-parallel 8 [--oracle-timeout 600] [--instance-filter ...]
# ALWAYS pass --force — see "Stale oracle reports" under Key Patterns.

# Step 4 — merge recoveries (--canon / --remin; defaults are the canonical paths)
python src/patch_minimizer/frontends/swebench/example_code/swebench_pipeline/consolidate.py

# Step 5 — reconstruct clean agent patches (add --write to emit artifacts)
python src/patch_minimizer/frontends/swebench/example_code/swebench_pipeline/reconstruct.py --write
# Optional: --save-dir DIR   --limit N   --ids <id> ...

# Step 6 — Table IVa / IVb / IVc + the "18"
python src/patch_minimizer/metrics/swebench/swebench_metrics.py            # all
python src/patch_minimizer/metrics/swebench/swebench_metrics.py --save-dir results/remin
python src/patch_minimizer/metrics/swebench/swebench_metrics.py --table compaction

# Step 7 — Table IVd (separate Python-3.12 env)
bash src/patch_minimizer/metrics/scbench_metric/setup_env.sh && source ~/.cache/scbench_metric/ENV.sh
python src/patch_minimizer/metrics/scbench_metric/run_swebench.py --out results/scbench_results
```

## Key Patterns

- **`bootstrap_env()`** now lives once in `utils/env.py` and **raises** if the project root is not found. The two copies it replaced fell back to a hardcoded `parents[3]`, which silently resolved to the wrong directory after a move.
- **One trajectory = `cli/traj_cli.py`.** `minimize_traj()` is the atomic unit; the batch driver reaches it through `cli/common.py::minimize_one_traj`, which holds a clone from the pool. Also runnable as `python -m patch_minimizer.frontends.swebench.cli traj`.
- **`consolidate` no longer runs on import.** It has a `main()` and a `__main__` guard; `CANON`/`REMIN` are `--canon`/`--remin`.
- **Per-clone exclusivity (clone pool).** `run_swebench_minimization.py` builds one `Queue` of `(clone path, RWLock)` per repo — the same shape as kernel's `linux_dir_queue`. `--clones-per-repo K` (default 1) gives each repo K independent clones: the existing `<owner>__<repo>` plus `<owner>__<repo>__copyN`, created once at startup with `git clone --local` (capped at the repo's instance count; existing copies reused). `cli/common.py::minimize_one_traj` takes a clone for the whole `minimize_edit_list` call (its HEAD must stay at that instance's `base_commit`), force-releases a still-held write lock, and returns it in `finally`. Up to K instances per repo run at once; jobs are interleaved across repos (largest first), so set `--num-parallel` to about 12*K. **Copies are real clones, not `git worktree`** — `sweep_stale_git_locks` expects `<clone>/.git/` to be a directory.
- **Resume / atomic summary.** `_SummaryState` reloads any existing `minimization_summary.json`, skips already-done (and, unless `--retry-failed`, previously-failed) instances, and flushes atomically via `_save_summary_atomic` after each result.
- **Manual-assert override** (`--manual-asserts-dir`). A `<instance_id>/` sidecar of `*.py` (+ optional `commands.txt`) **augments** — never replaces — the proxy oracle for weak-signal instances; instances without a sidecar are untouched. Feeds the step-4 recovery loop.
- **Stale oracle reports — always run postprocess with `--force`.** Evaluation reports are cached in `logs/run_evaluation/posthoc_{pre,post}_<instance_id>/`, a name that does **not** include the results folder. `run_instance` returns an existing `report.json` without re-testing, so a second results folder (e.g. a manual-assert re-run) silently gets the first folder's verdicts for the old patch. This happened for 26/30 instances in `results/remin` (2026-10-02). `--force` deletes the cached report before evaluating.
- **Oracle drift correction** (postprocessing). `_PRINCETON_IMAGE_INSTANCES` are evaluated against Princeton's published `swebench/sweb.eval.*` images (local build drifted deps); `_UPSTREAM_NONREPRODUCIBLE` are gold-fails-in-canonical-image instances left in bucket D. Both are per-instance gated (zero blast radius). Importing `swebench_environment` at load also patches `MAP_REPO_VERSION_TO_SPECS` (adds `roman` for sphinx).
- **`_strip_already_applied_hunks`** (pre-min oracle only) drops per-file blocks already at post-state in the image (env-setup commit overlap) so `patch --batch` doesn't auto-reverse the whole model_patch.

## Key Invariants

- Reads `info.submission` for the agent's full patch; instances with empty submission or zero edits are skipped (return `None`).
- `--save-dir` is **required** by the batch driver (`run_swebench_minimization.py`); the single-instance `traj` CLI requires `--traj` and `--bug-id` and defaults `--save-dir` to `/tmp/min_<bug_id>`.
- `gold_comparison` uses the public HF dataset `patch` field only (developer fix) — never `test_patch`/FAIL_TO_PASS; gold is reporting-only and never fed back into minimization.
- The default results dir is `z-validate-swebench-dataset/minimization_333`; `reconstruct.py` and `swebench_metrics.py` accept `--save-dir` to override it. `consolidate.py` uses `--canon`/`--remin`.
