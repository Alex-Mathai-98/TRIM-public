# live-kbench × mini-swe-agent (Claude Opus 4.5) parsing tests

> Regression + correctness tests for parsing the 396 mini-swe-agent runs (396 kernel bugs × 1 run,
> `mini-swe-agent-c-5.6_claude-opus-4.5_8`) on live-kbench, through the same entry point the minimizer
> uses: `frontends/kernel/cli/traj_cli.main([... "--agent", "mini-swe-agent", "--only-parse"])`.

## Files

| Path | Purpose |
|---|---|
| `parser_baseline.py` | `build` writes goldens; `verify` re-parses every run and compares (exit 1 on any mismatch, prints the first diff). Clone-free, a few seconds. |
| `verify_final_diff.py` | Final-diff check: apply each run's parsed patch list to a Linux clone at `parentOfFixCommit`; the `git diff` must equal the sibling `patch.diff` exactly after `normalize_diff`. Copy of the OpenHands script with the mini-swe agent and skip list. |
| `trajs/run_1/<bug_hash>/original/{traj.json,patch.diff}` | 396 raw mini-swe trajectories (`messages[]`) + the agent's diff for each, 58 MB. Copied as-is from Kernel_Agent `results/mini-swe-agent-c-5.6_claude-opus-4.5_8/`. |
| `trajectory_test_cases/<bug_hash>/run_1.json` | Goldens: candidate revisions (edits), patch list as `global_edit_idx` per node; `null` for the 38 runs with no candidate edits (all 38 are in the skip list). |
| `../live_kbench_common/bug_data/` | Base commit + kernel git URL per bug (shared). |
| `best_score.json` | Best parser regression score (matched/total) for the runner `tests/run_parsing_benchmarks.sh`. |

## Usage

```bash
. .claude/prelude.sh

# 1. Parser regression (no repos, no network)
python tests/parsing_benchmark_tests/live_kbench_mini_swe_claude_opus_4_5/parser_baseline.py verify

# 2. Final diff — 59 Linux clones in parallel. NEVER include linux-501 (the script refuses it).
python -u tests/parsing_benchmark_tests/live_kbench_mini_swe_claude_opus_4_5/verify_final_diff.py \
    --linux-dirs $(for i in $(seq 502 560); do printf 'collection_of_linux_repos/linux-%s ' $i; done)

# Rebuild goldens after an intentional parser change (review the git diff of the goldens!)
python tests/parsing_benchmark_tests/live_kbench_mini_swe_claude_opus_4_5/parser_baseline.py build
```

## Expected results (2026-09-30)

| Check | Result |
|---|---|
| `parser_baseline.py verify` | 396/396 matched |
| `verify_final_diff.py` (59 repos, ~14 min) | 335 ok, 61 skipped; 0 empty / mismatch / not_applicable / error; exit 0 — identical to the monorepo |

- **`skipped` (61):** `_SKIP_BUG_RUNS`, copied unchanged from the monorepo with its groups — python
  file-writing scripts (17), temp-file patch/script execution (25), head/tail splice via temp files (15),
  sed curly-brace-grouped command (2), sed `r` (2).
- **`_KNOWN_NOT_APPLICABLE`:** empty — the monorepo reports no not_applicable runs here.
- Any `diff_mismatch` / `not_applicable` / `error` fails the run (exit 1).

## Relation to the monorepo

- Parser output is **identical** to the Kernel_Agent monorepo goldens
  (`trajectory_test_cases_mini_swe-claude-success/`) for all 396 runs.
- `patch.diff` is byte-identical to the monorepo's `.baseline.diff` for all 396; `info.submission` is empty
  in every trajectory, so the monorepo's Karena-DB fallback is not needed.
- Monorepo result (`current_best_stats.md`): 335 all_ok, 61 skipped.
