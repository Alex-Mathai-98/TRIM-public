# SCBench Alternate Code-Slop Metric

Integration of the **SlopCodeBench (SCBench)** "code slop" quality metric
([paper](https://arxiv.org/abs/2603.24755), [repo](https://github.com/SprocketLab/slop-code-bench))
as an *independent, quality-based* cross-check on patch minimization, complementing
the paper's line-based `Δ_Slop`.

> The TRIM paper (`3_formulation.tex`) already cites SlopCodeBench as the
> "traditional notion of code quality that measures verbosity, duplication, or
> structural complexity." This module turns that citation into actual numbers:
> for each instance we measure SCBench slop on the **agent patch** and the
> **minimized patch** and report the reduction.

---

## 1. What the metric measures

SCBench scans a directory ("snapshot") of source and rolls up two composites
(see `docs/metrics-reference.md` in the SCBench repo):

| Composite | Definition | What it captures |
|---|---|---|
| **Verbosity** | `verbosity_flagged_sloc_lines / loc` = SLOC covered by **(clone lines ∪ ast-grep-flagged lines)** | code bloat / duplication / anti-patterns |
| **Erosion** | `mass.high_cc_pct` = share of complexity-mass `Σ cc·√sloc` in functions with cyclomatic complexity > 10 | structural degradation |

Components we also carry: `clone_lines` (tree-sitter AST-hash duplicate detector),
`ast_grep_violations` (a bundled **214-rule** "slop" ruleset), waste counts
(single-use functions, trivial wrappers, unused vars), and CC stats (`radon`).

**Headline signal = Δ verbosity (absolute flagged lines).** Erosion is a
*ratio* and can move non-monotonically under slop removal (removing low-complexity
scratch code raises the high-CC concentration), so it is reported but is not the
headline.

## 2. How it is invoked (architecture)

SCBench requires Python 3.12 + a heavy dependency tree, so it is treated as an
**external tool**, not imported into Kernel_Agent (Python 3.11):

```
Kernel_Agent (3.11)                         SCBench metric home (3.12 venv)
─────────────────────────                    ──────────────────────────────
scbench_runner.SCBenchMetricRunner  ──subprocess──▶  slop_measure_driver.py
   builds env (PATH, AST_GREP_RULES_PATH)              imports slop_code
   parses JSON on stdout            ◀──JSON────         measure_snapshot_quality()
```

- `slop_measure_driver.py` — the **only** file that imports `slop_code`; runs in
  the 3.12 venv, prints a flat JSON metrics dict (+ per-file breakdown) on stdout.
- `scbench_runner.py` — reusable library (3.11-safe): `SCBenchMetricRunner`,
  `SWEBenchRepoProvider`, patch utils, `evaluate_patch`, `evaluate_pair`.
- `run_swebench.py` — batch driver over the canonical SWE-bench corpus.
- `setup_env.sh` — builds the metric home (one-time).

### `evaluate_patch` contract

```python
runner   = SCBenchMetricRunner()                 # discovers $SCBENCH_METRIC_HOME
provider = SWEBenchRepoProvider(instance_id)     # clean base checkout from Docker
result   = evaluate_patch(runner, provider, patch_path, union_files, entry)
#   1. clean checkout of the touched files       (Docker /testbed at base commit)
#   2. apply the supplied patch                  (git apply / patch -p1)
#   3. run the SCBench metric                     (subprocess to the 3.12 driver)
#   4. return the numeric metric values          ({"all": ..., "source": ...})
#   5. leave nothing behind                       (work happens in a discarded tempdir)
```

`evaluate_pair(runner, provider, original_patch, minimized_patch)` runs the above
for both patches and returns `before`, `after`, and `reduction = before - after`.

## 3. Setup

```bash
bash src/Kernel_Agent/tools/solution_minimization/scbench_metric/setup_env.sh
source ~/.cache/scbench_metric/ENV.sh
```

This builds `~/.cache/scbench_metric/` = a Python-3.12 venv with `slop_code` and
its metric deps (`radon`, `tree-sitter==0.25.2`, `tree-sitter-language-pack==0.13.0`,
`ruff`, `ty`, `networkx`, ...), the prebuilt `ast-grep` binary, and the 214-rule
`slop_rules.yaml`. A `uv`→direct shim makes `slop_code`'s `uv run ruff/ty` calls
resolve the venv tools.

## 4. Running the SWE-bench evaluation

```bash
source ~/.cache/scbench_metric/ENV.sh
python src/Kernel_Agent/tools/solution_minimization/scbench_metric/run_swebench.py \
    --out alternate_metric/scbench_results --workers 4
```

Dataset (default): `z-validate-swebench-dataset/minimization_333` — the canonical
corpus behind the compaction numbers. Per
instance it reads `standalone__<id>/{original_clean.patch, minimized.patch}`.
Byte-identical pairs get reduction 0 with no Docker work.

### Output format

- `instances/<id>.json` — per instance: `changed_files`, `before`/`after`
  (each with `all` and `source` views), and `reduction` (before − after).
- `summary.json` — corpus aggregates for both views: sum/mean verbosity reduction,
  `n_positive` (instances with real reduction), clone/ast-grep reduction.

The two views: **`all`** counts every file the agent produced; **`source`**
excludes root-level scratch files (`reproduce_*.py`, `test_*.py`, ... — see
`is_scratch_file`, heuristic = "top-level file"), isolating slop removed from
legitimate source.

## 5. Assumptions & limitations

- **Python only.** SCBench registers only `PYTHON`/`.py`; every metric sits behind
  that language gate, so **non-`.py` files (incl. all C/kernel code) contribute 0
  to every score** — a `.c` snapshot returns `file_count: 0` silently. Live-kBench
  (kernel) patches therefore cannot be measured with SCBench as-is; supporting C
  would require a new `LanguageSpec` (tree-sitter-c symbols/clones + a C ast-grep
  ruleset + a C complexity backend).
- **Whole-file delta.** We measure the *whole* touched file in each state and take
  before − after; pre-existing repo slop cancels out, so the delta isolates the
  patch's effect. Absolute flagged-line counts look large because they include the
  file's baseline.
- **Changed-file union.** We measure the union of files touched by either patch,
  not the whole repo. For the absolute flagged-line delta this is *identical* to a
  whole-repo delta (untouched files cancel) but ~1000× faster.
- **Clean checkout = Docker.** Requires the local `sweb.eval.x86_64.<id>:latest`
  images. Missing images / non-applying patches are recorded as per-instance errors.
- **ast-grep is graceful.** If the `sg` binary is absent, verbosity falls back to
  clone-only (the 214 ast-grep rules contribute 0); the run still completes.
