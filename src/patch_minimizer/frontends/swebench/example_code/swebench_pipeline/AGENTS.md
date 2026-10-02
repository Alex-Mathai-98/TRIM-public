# SWE-bench Pipeline (steps 3-5)

> **Purpose:** Everything after minimization in the Table IV (RQ4) reproduction. Minimization
> itself is pipeline step 2 — `../run_swebench_minimization.py`.

| File | Pipeline step | Does |
|---|---|---|
| `postprocess.py` | 3 | Runs the **real** SWE-bench harness twice per instance — on the agent's submission (`oracle_pre`) and the minimized patch (`oracle_post`) — plus a structural comparison against the HF `patch` field (`gold_comparison`). Writes those back into each `minimization_results.json` and aggregates `oracle_transitions` + `gold_equivalence`. This is the *hidden* oracle: minimization never sees it. **Always pass `--force`**: reports are cached per instance in `logs/run_evaluation/posthoc_{pre,post}_<id>/` across *all* results folders, and without `--force` an old report is reused instead of testing the current patch. |
| `consolidate.py` | 4 (optional) | Merges manual-assert recovery runs from `--remin` into `--canon`. Only genuinely resolved instances merge (`post_clean`: resolved **and** patch_applied **and** zero F2P/P2P failures); flaky ones are skipped and reported. |
| `reconstruct.py` | 5 | Rebuilds each agent patch as if authored on a clean `base_commit`, dropping SWE-bench env churn, and writes `standalone__<id>/original_clean.patch` for SCBench. `--save-dir` (required), `--traj-dir`, `--workspace-root` replace old hardcoded paths. |

## Invariants

- **`consolidate` has a `main()` and a `__main__` guard.** Its body used to execute at
  *import* time, so `import consolidate` mutated a results directory. `CANON`/`REMIN` are
  now `--canon`/`--remin` with the old literals as defaults.
- **`postprocess.main()` and `reconstruct.main()` take an optional `argv`**, so
  `run_swebench_minimization.py --then-postprocess / --then-reconstruct` can call them
  in-process. Keep that parameter.
- **`__init__.py` imports no submodules**, so importing one sibling cannot drag
  `consolidate` in.
- **`postprocess` imports `environment` for its side effect only** — that import patches
  `MAP_REPO_VERSION_TO_SPECS` before `make_test_spec` runs. Keep the `# noqa: E402,F401`
  and the comment above it.
- **`reconstruct` belongs here, not in `metrics/`.** `metrics/AGENTS.md`'s first rule is
  read-only; this writes `original_clean.patch` into the results tree and mutates a git
  worktree. It is the *producer* of a metrics input.
- **Two "is it resolved?" predicates stay separate on purpose.** `consolidate.post_clean`
  is stricter than `reconstruct.resolved`, and `postprocess`'s inline check is looser than
  both. Each guards a different reported number — see `z-cpl-44`.
