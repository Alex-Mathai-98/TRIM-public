# live-kbench shared test data

> Data shared by the live-kbench parsing tests. No code lives here.

| Path | Purpose | Used by |
|---|---|---|
| `bug_data/<bug_hash>_0.json` | 534 raw benchmark bug JSONs (582 MB), copied from Kernel_Agent `src/Kernel_Agent/deployment_related_code/random_test_processed/`. Read via `frontends/kernel/cli/common.load_bug_data` for `parentOfFixCommit` (base commit) and the kernel git URL. | `live_kbench_swe_agent_gemini_3_pro/verify_final_diff.py` (534 bugs), `live_kbench_openhands_gemini_3_pro/verify_final_diff.py` (460 bugs), `live_kbench_mini_swe_claude_opus_4_5/verify_final_diff.py` (396 bugs) — both subsets |

When porting another live-kbench agent, check its bugs are all here before copying more bug JSONs.
