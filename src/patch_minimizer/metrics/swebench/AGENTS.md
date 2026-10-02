# SWE-bench Metrics

> **Purpose:** Reporting over a completed SWE-bench minimization run — Table IVa (compaction),
> IVb/IVc (file categories) and the RQ4 gold-agreement count. **Step 6** of the reproduction recipe; the steps that
> produce its input live in `frontends/swebench/`. Table IVd (SlopCodeBench verbosity) is a
> separate metric in `metrics/scbench_metric/`.

---

## File Overview

| File | Purpose |
|---|---|
| `swebench_metrics.py` | Prints Table IVa (edits / hunks / lines, filtered + unfiltered), IVb/IVc (source / tests / docs / build / scratch), and RQ4 gold agreement — the "18" minimized patches byte-identical to the developer fix. `--table {compaction,categories,gold,all}`. |

## Usage

```bash
python src/patch_minimizer/metrics/swebench/swebench_metrics.py --save-dir results/remin   # all tables
python src/patch_minimizer/metrics/swebench/swebench_metrics.py --save-dir results/remin --table compaction
# --traj-dir <dir> if the .traj files are not in results/swe-bench-swe-agent-trajs
```

## Key facts

- **Stdlib only, plus one optional import.** Imports nothing from `patch_minimizer`. The RQ4
  pre/post-vs-gold movement imports `datasets` (HF SWE-bench_Verified) if available and skips that
  part otherwise. It reads artifacts off disk; it does not participate in minimization.
- **`--save-dir` is required** (no default). Paths are resolved **relative to the current
  working directory**, not to `__file__`, so run it from the repository root.
- **Reads, never writes.** Consumes `minimization_summary.json`,
  `<id>/minimization_results.json` and `<id>/standalone__<id>/minimized.patch` (pipeline steps
  2–5), plus each agent's original submission from `<traj-dir>/<id>.traj`.
- **Metrics are reported over the resolved→resolved instances** (the paper's 327), not all 333.
  The headers print the actual count for the results being read.

## Why it lives here and not in `frontends/swebench/`

It is reporting, not pipeline machinery: pure stdlib, read-only, and with no dependency on the
SWE-bench frontend. Grouping it under `metrics/` puts it alongside `kbench_epr_and_delta_slop/`
(the kernel metrics) and mirrors `metrics/scbench_metric/`, which already sat outside the frontend.

Note the consequence: the reproduction pipeline's six steps now span two directories — steps 2–5
in `frontends/swebench/`, steps 6 and 7 under `metrics/`. The pipeline diagram in
[`../../frontends/swebench/AGENTS.md`](../../frontends/swebench/AGENTS.md) is the single place
that shows the whole chain.
