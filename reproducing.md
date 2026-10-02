# Reproducing the paper's SWE-bench results (Table IV)

Goal: minimize **all 333** SWE-agent trajectories with `--manual-asserts-dir` (the 30 instances
that have a hand-written reproduction pick it up automatically; the other 303 run as usual), run
the hidden SWE-bench oracle, rebuild the clean agent patches, and print the paper tables.

**Before you start**, follow the [Installation](README.md#-installation) and
[SWE-bench Setup](README.md#5-swe-bench-setup) sections of the README.

---

## 1. Full run with the manual reproductions

Minimizes all 333 instances into the folder `results/remin`.

```bash
python src/patch_minimizer/frontends/swebench/example_code/run_swebench_minimization.py \
    --traj-dir results/swe-bench-swe-agent-trajs \
    --save-dir results/remin \
    --manual-asserts-dir results/swe-bench-manual-asserts \
    --clones-per-repo 8 --num-parallel 48 --minimize-at edit
```

- Instances with a folder in `results/swe-bench-manual-asserts/` (30 of them) get their `manual_assert_repro.py`
  **added** to the test commands, never replacing them (`traj_cli.py:70-109`). Instances
  without one are minimized as usual. Check `run.log` of an instance with a manual assert
  for `Manual-assert override: 1 file(s)=['manual_assert_repro.py'], 1 command(s)`.
- Expected time: ~1.5–2 h on a 96-core machine, set by django (168 instances, 8 at a time) and sympy-13878
  (about 1.5 h on its own).
- Resume-safe. If you stop it, rerun the same command **with `--retry-failed`**.
- **If your results don't match the expected ones (step 4), lower the concurrency to 8:** use
  `--num-parallel 8` here and in step 2. High concurrency can overload the CPU and make
  slow test runs time out, which changes the results.

---

## 2. Oracle on the re-minimized patches

Run it **after** step 1 has exited. Otherwise run it once more at the end: the batch's
final summary write drops the oracle fields from `minimization_summary.json` (the
per-instance files keep them).

```bash
python src/patch_minimizer/frontends/swebench/example_code/swebench_pipeline/postprocess.py \
    --save-dir results/remin --traj-dir results/swe-bench-swe-agent-trajs \
    --force --oracle-timeout 3600 --num-parallel 16
```

`--oracle-timeout 3600` gives each test run up to 1 hour. With the default 600 s, slow
instances (scikit-learn-14710, psf-2317) ended with no report (`no_report`).

`--force` is required: evaluation reports are cached in `logs/run_evaluation/posthoc_{pre,post}_<id>/`, shared by every results folder, and without `--force` an old `report.json` from an earlier run is silently reused instead of testing the current patch.

**If any instance ends with an oracle error (E).** List them:

```bash
python3 -c "
import json, glob
for f in sorted(glob.glob('results/remin/*/minimization_results.json')):
    j = json.load(open(f))
    if any((j.get(k) or {}).get('error') for k in ('oracle_pre', 'oracle_post')):
        print(f.split('/')[2], j['oracle_pre'].get('error'), j['oracle_post'].get('error'))
"
```

Re-run only those instances, one at a time so they don't compete for CPU:

```bash
python src/patch_minimizer/frontends/swebench/example_code/swebench_pipeline/postprocess.py \
    --save-dir results/remin --traj-dir results/swe-bench-swe-agent-trajs \
    --instance-filter <id1>,<id2> --force --oracle-timeout 3600 --num-parallel 1
```

This updates those instances and the totals in `minimization_summary.json`; nothing else
needs re-running. If an instance still shows `no_report`, the harness log at
`logs/run_evaluation/posthoc_post_<id>/minimized/<id>/run_instance.log` says why.


---

## 3. Reconstruct clean agent patches

`results/remin` already contains all 333 instances (the full run with manual asserts), so no
consolidation step is needed. Both `reconstruct.py` and `swebench_metrics.py` accept
`--save-dir` to point at any results directory.

```bash
python src/patch_minimizer/frontends/swebench/example_code/swebench_pipeline/reconstruct.py \
    --save-dir results/remin --write
```

This rebuilds each agent patch on a clean `base_commit` (strips SWE-bench env churn) and writes
`standalone__<id>/original_clean.patch`. Only runs over the resolved→resolved set.

Don't run this while step 1 is still running: it resets the same repository clones in
`results/swe-bench-workspace/`.

---

## 4. Print the paper tables (Table IVa / IVb / IVc + gold "18")

```bash
python src/patch_minimizer/metrics/swebench/swebench_metrics.py --save-dir results/remin
# or one table at a time: --table {compaction,categories,gold,all}
```

**Expected results** (paper, Table IV and RQ4, over the 327 resolved → resolved instances):

| Metric | Paper |
|---|---:|
| Edits compaction (Table IVa) | 23.6% |
| Lines compaction, total (Table IVa) | 63.5% |
| Lines compaction, modified files (Table IVa) | 20.0% |
| Source-file lines compaction (Table IVb/c) | 20.4% |
| Test-file lines compaction (Table IVb/c) | 18.6% |
| Minimized patches byte-identical to the developer fix (RQ4) | 18 |

---

## 5. (Optional) Table IVd — SlopCodeBench verbosity

Needs a separate Python 3.12 environment. Consumes `original_clean.patch` from step 3.

```bash
bash src/patch_minimizer/metrics/scbench_metric/setup_env.sh && source ~/.cache/scbench_metric/ENV.sh
python src/patch_minimizer/metrics/scbench_metric/run_swebench.py \
    --dataset results/remin --out results/scbench_results
```

Steps 4 and 5 are **parallel** — two independent views of the same run (structural vs.
quality-based).
