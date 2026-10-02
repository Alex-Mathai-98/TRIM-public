#!/usr/bin/env python3
"""OpenHands trajectory minimization runner.

Walks ``results/<agent_config>/run_N/<bug_hash>/original/traj.json`` layout,
discovers candidates, and calls ``traj_cli.main()`` for each one via the
``minimize_one_traj()`` worker (which manages the Linux repo pool).

Usage:
    python run_openhands_minimization.py \\
        --results-dir results/openhands-c-unlimited_gemini-3-pro-preview_5 \\
        --benchmark-folder /path/to/bugs --linux-dirs /path/linux-501 ...
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed

from patch_minimizer.frontends.kernel.cli.common import (
    append_and_save,
    build_linux_pool_paths,
    check_summary_orphans,
    linux_dir_queue_from_paths,
    load_existing_summary,
    minimize_one_traj,
    result_folder_name,
)
from patch_minimizer.strategies.aggregate_feedback_stats import (
    aggregate_feedback_stats_from_summary,
)
from patch_minimizer.benchmark_utils.kernel.clean_and_update_all_linux_repos import clean_all_linux_repos


_AGENT = "openhands"
_MODEL_ID = "openhands_trajectory"


# ---------------------------------------------------------------------------
# Discovery — walk run_N/<bug_hash>/original/ layout
# ---------------------------------------------------------------------------

@dataclass
class OpenHandsCandidate:
    bug_id: str
    run_number: int
    traj_path: str
    agent_patch_id: int | None
    kgym_evaluation: str | None


_RUN_DIR_RE = re.compile(r"^run_(\d+)$")


def _read_agent_patch_id(original_dir: Path) -> int | None:
    p = original_dir / "agent_patch_id.txt"
    if not p.is_file():
        return None
    txt = p.read_text().strip()
    return int(txt) if txt.isdigit() else None


def _read_kgym_evaluation(original_dir: Path) -> str | None:
    p = original_dir / "kgym_eval.json"
    if not p.is_file():
        return None
    with open(p) as f:
        data = json.load(f)
    return data.get("kGymEvaluation")


def _discover_openhands_candidates(
    results_dir: str,
    runs: list[int] | None = None,
    kgym_evaluation_filter: str | None = "notReproduced",
) -> list[OpenHandsCandidate]:
    """Walk results_dir/run_N/<bug_hash>/original/ and return matching candidates."""
    root = Path(results_dir)
    candidates: list[OpenHandsCandidate] = []

    run_dirs = sorted(root.iterdir())
    for run_dir in run_dirs:
        if not run_dir.is_dir():
            continue
        m = _RUN_DIR_RE.match(run_dir.name)
        if not m:
            continue
        run_number = int(m.group(1))
        if runs and run_number not in runs:
            continue

        for bug_dir in sorted(run_dir.iterdir()):
            if not bug_dir.is_dir():
                continue
            original = bug_dir / "original"
            traj = original / "traj.json"
            if not traj.is_file():
                continue

            kgym_eval = _read_kgym_evaluation(original)
            if kgym_evaluation_filter and kgym_eval != kgym_evaluation_filter:
                continue

            candidates.append(OpenHandsCandidate(
                bug_id=bug_dir.name,
                run_number=run_number,
                traj_path=str(traj),
                agent_patch_id=_read_agent_patch_id(original),
                kgym_evaluation=kgym_eval,
            ))

    return candidates


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run minimization on OpenHands agent trajectories (run_N/<bug_hash>/original/)."
    )
    parser.add_argument(
        "--results-dir", required=True, metavar="DIR",
        help="Root results directory (e.g. results/openhands-c-unlimited_gemini-3-pro-preview_5).",
    )
    parser.add_argument(
        "--runs", type=int, nargs="+", default=None, metavar="N",
        help="Run numbers to process (default: all discovered run_N/ dirs).",
    )
    parser.add_argument(
        "--benchmark-folder", default=None,
        help="Benchmark folder (per-bug JSONs + golden_subset.json). Default: KBENCH_PATH.",
    )
    parser.add_argument(
        "--linux-dir", default=None,
        help="Linux repo path for sequential mode. Default: $BASE_PATH/collection_of_linux_repos/linux-650.",
    )
    parser.add_argument("--linux-dir-start", type=int, default=None, metavar="N",
                        help="Parallel pool: first linux-{N} index.")
    parser.add_argument("--linux-dir-end", type=int, default=None, metavar="N",
                        help="Parallel pool: last linux-{N} index (inclusive).")
    parser.add_argument("--linux-dirs", nargs="+", metavar="DIR", default=None,
                        help="Explicit Linux repo paths for the worker pool.")
    parser.add_argument("--num-parallel", type=int, default=1, metavar="N",
                        help="Number of parallel minimization workers. Default: 1 (sequential).")
    parser.add_argument("--run-jobs", action="store_true", help="Submit real kernel build/test jobs.")
    parser.add_argument("--minimize-at", choices=("node", "edit"), default="edit",
                        help="Minimization granularity.")
    parser.add_argument("--save-dir", metavar="DIR", help="Output directory for results + summary.")
    parser.add_argument("--syzkaller-rollback-tag", default="master",
                        help="Syzkaller tag for reproducer.")
    parser.add_argument("--kgym-evaluation", default="notReproduced",
                        help="kGymEvaluation value to filter for (local kgym_eval.json). Default: notReproduced.")
    parser.add_argument("--limit", type=int, default=0, metavar="N",
                        help="Process at most the first N candidates (0 = all).")
    args = parser.parse_args()

    if not os.path.isdir(args.results_dir):
        print(f"Error: results directory not found: {args.results_dir}", file=sys.stderr)
        return 1

    _bf = args.benchmark_folder
    if _bf is not None and not str(_bf).strip():
        _bf = None
    benchmark_folder = _bf or os.environ.get("KBENCH_PATH")
    if not benchmark_folder or not os.path.isdir(benchmark_folder):
        print("Error: benchmark folder missing. Set KBENCH_PATH or pass --benchmark-folder.",
              file=sys.stderr)
        return 1

    base_path = os.environ.get("BASE_PATH", "")
    linux_dir = args.linux_dir or os.path.join(base_path, "collection_of_linux_repos", "linux-650")
    num_parallel = max(1, args.num_parallel)
    if num_parallel <= 1 and not os.path.isdir(linux_dir):
        print(f"Error: linux_dir not found: {linux_dir}", file=sys.stderr)
        return 1

    # AIDEV-NOTE: Only the path is needed here (for the summary's config record). traj_cli
    # loads the file itself — it is the sole consumer, passing the data to KernelEnvironment.
    # Loading it here too would be dead weight: minimize_one_traj crosses a CLI boundary and
    # can only forward strings, so a dict in shared_config never reaches traj_cli.
    golden_path = os.path.join(benchmark_folder, "golden_subset.json")
    if not os.path.isfile(golden_path):
        print("Warning: golden_subset.json not found; KernelEnvironment may fail.", file=sys.stderr)

    # ---- Discover candidates ------------------------------------------------
    candidates = _discover_openhands_candidates(
        args.results_dir,
        runs=args.runs,
        kgym_evaluation_filter=args.kgym_evaluation,
    )
    if not candidates:
        print("No candidates found after local kgym_eval filtering.")
        return 0
    print(f"Discovered {len(candidates)} candidate(s) across "
          f"{len(set(c.run_number for c in candidates))} run(s).")

    if args.limit and args.limit > 0:
        candidates = candidates[: args.limit]
        print(f"Limit: processing {len(candidates)} candidate(s) (--limit {args.limit}).")

    shared_config = {
        "benchmark_folder": benchmark_folder,
        "run_jobs": args.run_jobs,
        "syzkaller_rollback_tag": args.syzkaller_rollback_tag,
        "minimize_at": args.minimize_at,
    }

    # ---- Build linux dir pool -----------------------------------------------
    pool_paths = build_linux_pool_paths(
        linux_dirs=args.linux_dirs,
        linux_dir_start=args.linux_dir_start,
        linux_dir_end=args.linux_dir_end,
        linux_dir=linux_dir,
        num_parallel=num_parallel,
        base_path=base_path,
    )

    # ---- Set up summary state -----------------------------------------------
    summary_state = None
    if args.save_dir:
        summary_path = Path(args.save_dir).resolve() / "minimization_summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        agg, succ, fail, seen = load_existing_summary(summary_path)
        summary_state = {
            "lock": Lock(),
            "aggregated": agg,
            "successful": succ,
            "failed": fail,
            "seen": seen,
            "path": summary_path,
            "config": {
                "results_dir": str(Path(args.save_dir).resolve()),
                "benchmark_folder": benchmark_folder,
                "golden_subset": golden_path,
                "linux_dirs": pool_paths,
                "model_id": _MODEL_ID,
                "run_jobs": args.run_jobs,
                "parallel": num_parallel,
            },
        }

    # AIDEV-NOTE: Prompts on stdin, so it must run before the ThreadPoolExecutor — never
    # on a worker thread. Pass the post---limit candidate set: a lowered --limit is exactly
    # one of the input changes this is meant to catch.
    if summary_state:
        check_summary_orphans({c.bug_id for c in candidates}, summary_state)

    # ---- Run minimization via traj_cli --------------------------------------
    clean_all_linux_repos(pool_paths, parallel=len(pool_paths) > 1)
    linux_dir_queue = linux_dir_queue_from_paths(pool_paths)
    all_results = []

    def _process_one(cand: OpenHandsCandidate) -> dict | None:
        # AIDEV-NOTE: The swe_candidate_0 level is load-bearing, not decoration — three places
        # depend on it: result_folder_name() writes it into the summary's `folder` field,
        # append_and_save() builds the same string for failure rows, and
        # metrics/kbench_epr_and_delta_slop/discover_pairs.py hardcodes it when locating
        # minimized.patch. Index is always 0: parse_trajectory() yields at most one
        # CandidateTrajectory per file. Nothing downstream creates this dir — traj_cli hands
        # save_dir straight to save_minimization_results(), which open()s without mkdir.
        out_dir = (
            os.path.join(args.save_dir, cand.bug_id, "swe_candidate_0")
            if args.save_dir else None
        )
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        result = minimize_one_traj(
            agent=_AGENT,
            traj_json=cand.traj_path,
            bug_id=cand.bug_id,
            linux_dir_queue=linux_dir_queue,
            shared_config=shared_config,
            save_dir=out_dir,
        )
        if result and summary_state:
            result["pass_id"] = cand.bug_id
            with summary_state["lock"]:
                append_and_save(result, summary_state)
        return result

    if num_parallel > 1:
        print(f"Running {len(candidates)} candidate(s) with {num_parallel} parallel workers.")
        with ThreadPoolExecutor(max_workers=num_parallel) as executor:
            futures = {executor.submit(_process_one, c): c.bug_id for c in candidates}
            for future in as_completed(futures):
                bug_id = futures[future]
                try:
                    result = future.result()
                    if result:
                        all_results.append(result)
                except Exception as e:
                    print(f"Error processing {bug_id}: {e}", file=sys.stderr)
    else:
        print(f"Running {len(candidates)} candidate(s) sequentially.")
        for cand in candidates:
            result = _process_one(cand)
            if result:
                all_results.append(result)
                print(f"  {cand.bug_id}: "
                      f"reduction={result['reduction']}, minimized_edits={result['minimized_edit_count']}")

    if summary_state:
        aggregate_feedback_stats_from_summary(str(summary_state["path"]))

    print(f"\nDone: {len(all_results)} minimization run(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
