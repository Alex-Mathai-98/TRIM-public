# SCBench (SlopCodeBench) Verbosity Metric

> **Purpose:** Table IVd of the paper (RQ4). An independent, *quality-based* cross-check on minimization: for each SWE-bench instance, measure SlopCodeBench "code slop" (verbosity / erosion / structural components) on the agent patch vs. the TRIM-minimized patch and report how much slop minimization removed. This is **step 7** of the SWE-bench reproduction recipe (after step 5, `frontends/swebench/example_code/swebench_pipeline/reconstruct.py`, writes `original_clean.patch`). Steps 6 and 7 run in parallel — `metrics/swebench/` is the structural view, this is the quality view. See this directory's `README.md` for the metric definitions and setup details.

---

## File Overview

| File | Purpose |
|---|---|
| `run_swebench.py` | **Table IVd batch driver.** Iterates `z-validate-swebench-dataset/minimization_333`, reads each `standalone__<id>/{original_clean.patch, minimized.patch}`, runs a 3-state measurement per instance, and totals `introduced` vs `removed` verbosity across the corpus (with/without pure-deletion outliers). Writes `instances/<id>.json` + `summary.json`. |
| `scbench_runner.py` | **3.11-safe reusable library.** `SCBenchMetricRunner` (shells out to the 3.12 driver), `SWEBenchRepoProvider` (clean base checkout from the local `sweb.eval.x86_64.<id>:latest` Docker image), patch utilities (`changed_files`, `new_files`, `filter_patch_to_python`, `apply_patch`), and the `evaluate_patch` / `evaluate_pair` / `evaluate_triple` entry points. |
| `slop_measure_driver.py` | **The only file that imports `slop_code`.** Runs *inside* the SCBench Python-3.12 venv as a subprocess; reads a snapshot dir, runs `measure_snapshot_quality`, and prints a flat JSON metrics dict (+ per-file breakdown) on stdout. |
| `__init__.py` | Re-exports `SCBenchMetricRunner`, `SWEBenchRepoProvider`, `SCBenchError`, `changed_files`, `new_files`, `evaluate_patch`, `evaluate_pair`. |
| `README.md` | Metric definitions (verbosity / erosion composites), architecture, `setup_env.sh` usage, output format, limitations (Python-only). |
| `setup_env.sh` | One-time build of the metric home (`~/.cache/scbench_metric/`): Python-3.12 venv with `slop_code` + deps, the `ast-grep` binary, and the 214-rule `slop_rules.yaml`. |

## The Verbosity Metric Flow

Two-interpreter design — SCBench needs Python 3.12 + a heavy dep tree, so it is treated as an **external tool** and never imported into the 3.11 package:

```
patch_minimizer (3.11)                     SCBench metric home (3.12 venv)
──────────────────────                     ───────────────────────────────
SCBenchMetricRunner.measure()  ─subprocess─▶  slop_measure_driver.py
  builds env: PATH += home/bin,               imports slop_code
  home/venv/bin; AST_GREP_RULES_PATH          measure_snapshot_quality(entry, dir)
  parses stdout JSON            ◀───JSON────   prints flat metrics dict
```

Per instance (`run_swebench.py` → `evaluate_triple`), slop is measured at **three states** to isolate the patch's own contribution:

1. `original` — base commit, no patch (from `SWEBenchRepoProvider.extract_base_files`, `docker cp` from `/testbed`).
2. `agent` — base + `original_clean.patch`.
3. `minimized` — base + `minimized.patch`.

Then per file: `introduced = agent - original`, `removed = agent - minimized` (headline signal = `verbosity_flagged_lines`). Subtracting `original` cancels the large pre-existing whole-file slop so the delta reflects the patch, not the file. `run_swebench.py` sums `introduced`/`removed` across the corpus and reports `pct_of_introduced_removed`.

**Metric composites** (computed in `slop_measure_driver.py` from the raw snapshot):
- **Verbosity** = `verbosity_flagged_sloc_lines / loc` — SLOC covered by (clone lines ∪ ast-grep-flagged lines).
- **Erosion** = `mass.high_cc_pct` — share of complexity-mass `Σ cc·√sloc` in functions with cyclomatic complexity > 10. Reported but not headline (a ratio; moves non-monotonically under slop removal).
- Carried components (`METRIC_KEYS`): `verbosity_flagged_lines`, `verbosity_pct`, `erosion_high_cc_pct`, `clone_lines`, `ast_grep_violations`, `cc_sum`.

## CLI Usage

```bash
# One-time: build the 3.12 metric home
bash src/patch_minimizer/metrics/scbench_metric/setup_env.sh
source ~/.cache/scbench_metric/ENV.sh          # or export SCBENCH_METRIC_HOME=...

# Table IVd — batch over the canonical corpus
python src/patch_minimizer/metrics/scbench_metric/run_swebench.py \
    --out results/scbench_results [--workers 4] [--limit N] \
    [--only django__django-15128 ...] [--resume] [--dataset DIR]
```

Output: `--out/instances/<id>.json` (per instance: `introduced`, `removed`, `per_file`) and `--out/summary.json` (corpus totals for `all` and `excl_outliers`). Byte-identical patch pairs still get measured but yield `removed = 0` (agent == minimized).

## Key Patterns / Invariants

- **`SCBenchMetricRunner()` validates the metric home on construction** — raises `SCBenchError` if `$SCBENCH_METRIC_HOME/venv/bin/python` is missing (run `setup_env.sh` first).
- **Python-only.** Every metric sits behind SCBench's `.py` language gate; `filter_patch_to_python` drops non-`.py` diff blocks before apply, and non-`.py` files contribute 0. C/kernel patches cannot be measured as-is (see README limitations).
- **Changed-file union, not whole repo.** Only files touched by either patch are extracted/measured (`~1000×` faster; untouched files cancel in the delta).
- **Two scopes** mirror `swebench_metrics.py`'s compaction views: `unfiltered` (all touched files) and `filtered` (`keep_filtered`: drop NEW `+A` files the minimizer removed, keep modified files + surviving new files). `new_files`/`changed_files` reproduce the `is_new` detection so scopes match the paper's filtered patch-size view.
- **Everything runs in a discarded tempdir** (`tempfile.TemporaryDirectory`); the base checkout container is `docker rm -f`'d — nothing persists between evaluations.
- **`apply_patch`** tries `patch -p1 -l --fuzz=3` first, then `git apply --unsafe-paths` as fallback; non-applying patches / missing images are recorded as per-instance errors, not fatal.
