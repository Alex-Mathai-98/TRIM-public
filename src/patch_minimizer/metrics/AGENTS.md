# Metrics

> **Purpose:** Reporting over completed minimization runs. Nothing here participates in
> minimization — these are read-only consumers of artifacts already on disk. Each subdirectory
> produces one family of numbers for the paper.

## Which directory produces which table?

| Directory | Benchmark | Produces |
|---|---|---|
| [`kbench_epr_and_delta_slop/`](kbench_epr_and_delta_slop/AGENTS.md) | Kernel (Live-kBench) | EPR + **Δ_Slop** — average `.c`/`.h` diff lines removed per bug |
| [`swebench/`](swebench/AGENTS.md) | SWE-bench | **Table IVa** (compaction), **IVb/IVc** (file categories), **RQ4** gold agreement — the "18" byte-identical |
| [`scbench_metric/`](scbench_metric/AGENTS.md) | SWE-bench | **Table IVd** — SlopCodeBench verbosity: code-slop removed, agent patch vs. minimized |

`swebench/` and `scbench_metric/` are two independent views of the same run: one structural
(how much smaller), one quality-based (how much slop removed). They are **steps 6 and 7** of the
reproduction recipe and run in **parallel** — both consume step 5's output; neither feeds the other.

## Shared properties

- **Read-only.** They consume `minimization_summary.json`, `<id>/minimization_results.json`,
  `standalone__<id>/minimized.patch` and `original_clean.patch`. They never write into a results
  tree.
- **Run from the repository root.** Input locations are resolved relative to the *current working
  directory*, not to `__file__` (e.g. `swebench/swebench_metrics.py --save-dir results/remin`).
  Moving these files is safe; running them from the wrong directory is not.
- **No dependency on `frontends/`.** Metrics import nothing from a benchmark frontend, which is
  why they live here rather than beside the pipelines that generate their input.

## Where the inputs come from

| Metric | Produced by |
|---|---|
| `kbench_epr_and_delta_slop/` | kernel runs — `frontends/kernel/` |
| `swebench/`, `scbench_metric/` | SWE-bench pipeline steps 2–5 — `frontends/swebench/` |

The full SWE-bench chain (steps 2→6, including both metric branches) is documented in one place:
[`../frontends/swebench/AGENTS.md`](../frontends/swebench/AGENTS.md).

## Note on packaging

`metrics/` has no `__init__.py` — it is an implicit namespace package, and
`kbench_epr_and_delta_slop/` and `swebench/` are plain script collections. Only
`scbench_metric/` is a real package (it exports a public API via its `__init__.py`), so it is the
only one meant to be imported rather than executed.
