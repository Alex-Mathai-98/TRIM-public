# live-kbench × SWE-agent (Gemini 3 Pro) parsing tests

> Regression + correctness tests for parsing the 1602 SWE-agent runs (534 kernel bugs × up to 3 runs,
> model `gemini-3-pro-preview` per `replay_config.agent.model.name`) on live-kbench, through the same
> entry point the minimizer uses:
> `frontends/kernel/cli/traj_cli.main([... "--agent", "swe-agent", "--only-parse"])`.

## Files

| Path | Purpose |
|---|---|
| `parser_baseline.py` | `build` writes goldens; `verify` re-parses every run and compares (exit 1 on any mismatch, prints the first diff). Clone-free, ~20 s. |
| `verify_final_diff.py` | Final-diff check: apply each run's parsed patch list to a Linux clone at `parentOfFixCommit`; the `git diff` must equal `info.submission` exactly after `normalize_diff`. Trimmed port of the monorepo's `test_edit_applicability.py` (same rule, same skip list; no mismatch classification). |
| `trajs/<bug_hash>/run_N.json` | 1602 raw SWE-agent trajectories (5.3 GB), copied from Kernel_Agent `results/sweagent_raw_trajectories/`. |
| `../live_kbench_common/bug_data/<bug_hash>_0.json` | 534 raw benchmark bug JSONs (582 MB), shared with `live_kbench_openhands_gemini_3_pro/` — source of `parentOfFixCommit` and the kernel git URL via `kernel/cli/common.load_bug_data`. |
| `trajectory_test_cases/<bug_hash>/run_N.json` | Goldens: candidate revisions (edits), patch list as `global_edit_idx` per node; `null` for runs with no candidate. |
| `best_score.json` | Best parser regression score (matched/total) for the runner `tests/run_parsing_benchmarks.sh`. |

## Usage

```bash
. .claude/prelude.sh

# 1. Parser regression (no repos, no network)
python tests/parsing_benchmark_tests/live_kbench_swe_agent_gemini_3_pro/parser_baseline.py verify

# 2. Final diff — 59 Linux clones in parallel. NEVER include linux-501 (clean reference repo;
#    the script refuses it).
python tests/parsing_benchmark_tests/live_kbench_swe_agent_gemini_3_pro/verify_final_diff.py \
    --linux-dirs $(for i in $(seq 502 560); do printf 'collection_of_linux_repos/linux-%s ' $i; done)

# Debug one bug on one clone
python tests/parsing_benchmark_tests/live_kbench_swe_agent_gemini_3_pro/verify_final_diff.py \
    --linux-dirs collection_of_linux_repos/linux-502 --bugs <bug_hash>

# Rebuild goldens after an intentional parser change (review the git diff of the goldens!)
python tests/parsing_benchmark_tests/live_kbench_swe_agent_gemini_3_pro/parser_baseline.py build
```

## Expected results (2026-09-30)

| Check | Result |
|---|---|
| `parser_baseline.py verify` | 1602/1602 matched |
| `verify_final_diff.py` (59 repos, ~16 min) | 1584 ok, 3 empty, 15 skipped; 0 mismatch / not_applicable / error — identical to the monorepo |

- **`empty` (3):** the parser finds no candidate with edits (agent edited only via unsupported methods,
  e.g. python scripts writing files). Not failures.
- **`skipped` (15):** `_SKIP_BUG_RUNS` — known docker anomalies where the run left state on disk that no
  parsed edit can reproduce. Copied unchanged from the monorepo.
- Anything `diff_mismatch` / `not_applicable` / `error` fails the run (exit 1).

## Relation to the monorepo

- Parser output is **identical** to the Kernel_Agent monorepo goldens for all 1602 runs (no drift —
  unlike SWE-bench, these runs never use `git checkout/restore <file>` undo).
- The ground-truth diff is read from `info.submission` in each run; the monorepo's `.baseline.diff`
  files were extracted from the same field, so they are not copied.
