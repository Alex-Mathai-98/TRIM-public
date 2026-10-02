# SWE-bench × SWE-agent parsing tests

> Regression + correctness tests for parsing the 333 SWE-agent trajectories on SWE-bench Verified
> (submission `20250522_sweagent_claude-4-sonnet-20250514`), through the same entry point the
> minimizer uses: `frontends/swebench/cli/traj_cli.minimize_traj(..., only_parse=True)`.

## Files

| Path | Purpose |
|---|---|
| `parser_baseline.py` | `build` writes goldens; `verify` re-parses every trajectory and compares (exit 1 on any mismatch, prints the first diff). Clone-free, ~20 s. |
| `verify_swe_bench_edit_applicability.py` | Final-diff check: parse each `.traj`, apply the edits to the repo clone at `base_commit`, compare per file with the submitted `model_patch`. Moved here from `frontends/swebench/example_code/`. |
| `trajs/<instance_id>.traj` | The 333 raw SWE-agent trajectories (3.1 GB, copied from `results/swe-bench-swe-agent-trajs/`, which is gitignored). |
| `trajectory_test_cases/<instance_id>.json` | Goldens: candidate revisions (edits + `feedback_commands`), patch list after `filter_to_submission_paths` (as `global_edit_idx` per node), test files, candidate-level feedback commands, submission paths. |
| `best_score.json` | Best parser regression score (matched/total) for the runner `tests/run_parsing_benchmarks.sh`. |

## Usage

```bash
. .claude/prelude.sh

# 1. Parser regression (no repos, no Docker, no network)
python tests/parsing_benchmark_tests/swe_bench_swe_agent/parser_baseline.py verify

# 2. Final diff: apply parsed edits at base_commit, compare per file with the submitted patch.
#    Clones repos into --workspace if missing; fetches base_commit (HF) + model_patch (S3).
python tests/parsing_benchmark_tests/swe_bench_swe_agent/verify_swe_bench_edit_applicability.py \
    --trajs-dir tests/parsing_benchmark_tests/swe_bench_swe_agent/trajs \
    --submission 20250522_sweagent_claude-4-sonnet-20250514 \
    --workspace results/swe-bench-workspace

# Rebuild goldens after an intentional parser change (review the git diff of the goldens!)
python tests/parsing_benchmark_tests/swe_bench_swe_agent/parser_baseline.py build
```

## Expected results (2026-09-30)

| Check | Result |
|---|---|
| `parser_baseline.py verify` | 333/333 matched |
| Final diff | 333/333 `ok`, gate PASS |

"ok" = every file present in **both** the reconstructed diff and the submitted patch is byte-identical
(index lines stripped). Files only on one side don't count:

- 26 submissions contain files the parser does not reconstruct. 25 are SWE-bench environment
  artifacts the agent never edited (`setup.py`, `tox.ini`, `pyproject.toml`, …). One is a known,
  accepted parser miss: `django__django-14089` — `str_replace_editor create /testbed/final_test.py`
  at step 44 is dropped.
- 325 reconstructions contain agent scratch/proxy files absent from the submission (expected).

## Why the goldens differ from the monorepo's

The goldens were built with this repo's parser, which is newer than the Kernel_Agent monorepo copy.
Against the monorepo goldens (`trajectory_test_cases_swe_bench_swe_agent/`) the parser output is:

| Outcome | Count | Cause |
|---|---|---|
| Identical | 298 | — |
| `feedback_commands` only | 17 | feedback carried across `git stash` / reset-all |
| Edits differ | 18 | `git checkout -- <file>` / `git restore <file>` treated as file-level undo |

The same undo handling is why `sphinx-doc__sphinx-10673` applies here (it was `not_applicable` in the
monorepo).

## Notes

- `parser_baseline.py` has no per-file error handling on purpose: a parser crash is a traceback naming
  the trajectory.
- The final-diff script parses with `SWEBenchSWEAgentTrajectoryParser` directly and uses the last
  candidate, not `traj_cli`. Equivalent today: every trajectory yields exactly one candidate, and
  `filter_to_submission_paths` only drops files the per-file comparison ignores anyway.
