# kBench EPR & Δ_Slop Metrics

> **Purpose:** Line-based patch-size metrics for the **kernel (Live-kBench)** side of the evaluation — the counterpart to the SWE-bench `runners/`+`metrics/scbench_metric/` scripts. These scripts discover (original, minimized) patch pairs across the kBench results-folder layouts, then compute the **Δ_Slop** (modified-lines) reduction that minimization achieves — the average number of `.c`/`.h` diff lines removed per bug. This drives the kBench EPR / Δ_Slop tables in the paper.

---

## File Overview

| File | Purpose |
|---|---|
| `modified_lines.py` | Core count. `modified_lines(patch)` = added + removed lines across `.c`/`.h` files, **excluding newly-added files** (methodology_3). Strips `collection_of_linux_repos/linux-*/` path prefixes, then sums `hunk.added + hunk.removed` via `unidiff.PatchSet`. |
| `discover_pairs.py` | **Step 1.** Walk a kBench results tree and emit a pairs JSON: `{ bug_id: { traj_N: { original: path, minimized: path [, fallback: reason] } } }`. Supports three result layouts via `--layout {nightly,swe,agentic}`. |
| `compute_from_pairs_json.py` | **Step 2.** Read the pairs JSON, apply `modified_lines` to each original/minimized patch, and average per-traj → per-bug → dataset. Emits a text report and/or a full JSON artifact with `avg_original`, `avg_minimized`, `avg_delta`, and `pct_reduction`. |

## What the Metric Computes

- **Δ_Slop (modified-line reduction)** — the headline. Per trajectory, `delta = modified_lines(original) - modified_lines(minimized)`. `compute_from_pairs_json.py` averages `delta` across a bug's trajectories, then across all bugs (`avg_delta`), and reports `pct_reduction = 100 * Σ delta / Σ original`. The count is `.c`/`.h`-only, excluding new files — i.e. slop measured on real modified kernel source, matching the SWE-bench "lines (mod. files)" spirit but for C.
- **EPR framing (Edit Precision/Recall context).** The pair-discovery step is what feeds EPR-style comparisons: `discover_pairs.py` validates that the minimized patch's modified `.c`/`.h` file set is a **subset** of the original's (`_files_match`), and falls back to the original (no reduction) when it is not — so a minimized patch can never claim credit for touching files the agent did not. `modified_lines` itself computes only line counts; there is no separate precision/recall function in these three files.

## The Three Result Layouts (`discover_pairs.py`)

| `--layout` | Original patch | Minimized patch | Notes |
|---|---|---|---|
| `nightly` (CrashFixer) | `tree_N/<BUG_PREFIX><id>/<last-node>/final.patch` | same node's `--patch-name` (default `minimized.patch`) | Skips bugs where `passed_validation.txt != "True"` (hybrid folders lack this file and are skipped unless `hybrid` in folder name). `<BUG_PREFIX>` = `gemini-3-pro-preview__`. |
| `swe` | `--orig-folder/run_N/<bug_id>/original/patch.diff` | `--min-folders` (one per traj) → `standalone__<id>[_0]/minimized.patch`, nested (`<id>/swe_candidate_0/standalone__*`) or flat (hybrid) | Each `--min-folder` is one trajectory/run. |
| `agentic` | `<results-folder>/run_N/<bug_id>/original/patch.diff` | `<bug_id>/<--strategy>/patch.diff` | `--strategy` ∈ `{minimized, minimized_diff_and_traj, minimized_traj_only}`; `--runs N ...`. Falls back to original when `kgym_eval.json` status ≠ `notReproduced`, patch missing, or file mismatch. |

**Fallback semantics** (all layouts, via `_resolve_minimized` / inline): when the minimized patch is missing, empty, fails the `.c`/`.h` subset check, or (agentic) failed kGym reproduction, the entry's `minimized` is set to the **original** path and a `fallback` reason is recorded, yielding `delta = 0` for that trajectory (no unearned reduction).

## CLI Usage

```bash
# Step 1 — discover pairs (nightly / CrashFixer)
python discover_pairs.py --layout nightly \
    --base-folder results/.../<...>_edit_minimize_guarantee_cache \
    --patch-name minimized.patch --out pairs_crashfixer.json

# Step 1 — SWE (e.g. openhands, one --min-folder per run)
python discover_pairs.py --layout swe \
    --min-folders results/openhands_runs/openhands_run1_edit_full \
                  results/openhands_runs/openhands_run2_edit_full \
    --orig-folder results/openhands-c-unlimited_gemini-3-pro-preview_5 \
    --out pairs_openhands.json

# Step 1 — agentic (mini-swe / claude, min-diff strategy)
python discover_pairs.py --layout agentic \
    --results-folder results/mini-swe-agent-c-5.6_claude-opus-4.5_8 \
    --strategy minimized --runs 1 --out pairs_agentic.json

# Step 2 — compute Δ_Slop from the pairs JSON
python compute_from_pairs_json.py --pairs pairs_crashfixer.json
python compute_from_pairs_json.py --pairs pairs_crashfixer.json \
    --out report.txt --out-json full_result.json
```

## Key Patterns / Invariants

- **Modules import as siblings, not as a package.** `compute_from_pairs_json.py` does `from modified_lines import modified_lines` — run these scripts from **within this directory** (or add it to `sys.path`).
- **Clamping.** If `modified_lines(minimized) > modified_lines(original)` for a traj, `compute_from_pairs_json.py` clamps `minimized := original`, sets `delta = 0`, and records `clamped`/`pre_clamp_minimized`. Minimization can never *increase* the reported line count.
- **`.c`/`.h`-only, new files excluded.** Both `modified_lines` and the `_files_match` subset check ignore non-C/H files and `is_added_file` blocks (methodology_3). Scratch/new files never count toward slop.
- **Linux-dir prefix stripping.** `_LINUX_DIR_RE` removes `collection_of_linux_repos/linux-*/` prefixes before parsing so diffs generated against a nested checkout parse correctly.
- **Missing files are skipped, not fatal.** `compute` skips a traj whose `original` path is absent; a bug with zero valid trajs is dropped from the dataset average.
