# live-kbench × OpenHands (Gemini 3 Pro) parsing tests

> Regression + correctness tests for parsing the 1168 OpenHands runs (460 kernel bugs × up to 3 runs,
> `openhands-c-unlimited_gemini-3-pro-preview_5`) on live-kbench, through the same entry point the
> minimizer uses: `frontends/kernel/cli/traj_cli.main([... "--agent", "openhands", "--only-parse"])`.

## Files

| Path | Purpose |
|---|---|
| `parser_baseline.py` | `build` writes goldens; `verify` re-parses every run and compares (exit 1 on any mismatch, prints the first diff). Clone-free, ~15 s. |
| `verify_final_diff.py` | Final-diff check: apply each run's parsed patch list to a Linux clone at `parentOfFixCommit`; the `git diff` must equal the sibling `patch.diff` exactly after `normalize_diff`. Copy of `live_kbench_swe_agent_gemini_3_pro/verify_final_diff.py` with the OpenHands agent, layout, diff source and skip list. |
| `trajs/run_N/<bug_hash>/original/{traj.json,patch.diff}` | 1168 raw OpenHands trajectories (bare event lists) + the agent's diff for each, 448 MB. Copied as-is from Kernel_Agent `results/openhands-c-unlimited_gemini-3-pro-preview_5/`. |
| `trajectory_test_cases/<bug_hash>/run_N.json` | Goldens: candidate revisions (edits), patch list as `global_edit_idx` per node; `null` for the 10 runs with no candidate edits. |
| `../live_kbench_common/bug_data/` | Base commit + kernel git URL per bug (shared with `live_kbench_swe_agent_gemini_3_pro/`). |
| `best_score.json` | Best parser regression score (matched/total) for the runner `tests/run_parsing_benchmarks.sh`. |

## Usage

```bash
. .claude/prelude.sh

# 1. Parser regression (no repos, no network)
python tests/parsing_benchmark_tests/live_kbench_openhands_gemini_3_pro/parser_baseline.py verify

# 2. Final diff — 59 Linux clones in parallel. NEVER include linux-501 (the script refuses it).
python -u tests/parsing_benchmark_tests/live_kbench_openhands_gemini_3_pro/verify_final_diff.py \
    --linux-dirs $(for i in $(seq 502 560); do printf 'collection_of_linux_repos/linux-%s ' $i; done)

# Rebuild goldens after an intentional parser change (review the git diff of the goldens!)
python tests/parsing_benchmark_tests/live_kbench_openhands_gemini_3_pro/parser_baseline.py build
```

## Expected results (2026-09-30)

| Check | Result |
|---|---|
| `parser_baseline.py verify` | 1168/1168 matched |
| `verify_final_diff.py` (59 repos, ~17 min) | 1143 ok, 1 empty, 19 skipped, 5 known not_applicable; 0 mismatch / error; exit 0 — same runs as the monorepo |

- **`skipped` (19):** `_SKIP_BUG_RUNS`, copied unchanged from the monorepo — mostly `run_ipython` Python
  file edits the parser can't see, OpenHands editor-buffer anomalies, and unparseable `sed` forms.
- **`_KNOWN_NOT_APPLICABLE`:** runs whose edits don't apply, accepted and not debugged (z-cpl-45 Q2);
  printed but don't fail the run. Exactly the monorepo's 5: `3582619f…/run_2`, `5908492d…/run_2`,
  `94334cd3…/run_1`, `948bb20d…/run_2`, `fee812e6…/run_1`.
- **`empty` (1):** `d47fcea9…/run_3` — no candidate edits. The monorepo counted empty runs as ok, hence
  its 1144 ok = 1143 + 1.
- Any other `diff_mismatch` / `not_applicable` / `error` fails the run (exit 1).

## Relation to the monorepo

- Parser output is **identical** to the Kernel_Agent monorepo goldens
  (`trajectory_test_cases_openhands_gemini-3-pro-preview/`) for all 1168 runs.
- The monorepo's `.baseline.diff` files are copies of the same `patch.diff` files, so they are not copied.
- Monorepo result (`current_best_stats.md`): 1144 all_ok, 5 not_applicable, 19 skipped.
