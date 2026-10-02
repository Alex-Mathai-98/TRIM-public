#!/usr/bin/env python3
"""Karena ``traj.json`` (``events``) → ``minimize_edits()`` (same idea as ``sample_minimize_edits.py``).

Parsing trajectories is **always** required to build edits. The Karena SQLite file is required to
resolve ``bugId`` from ``agentPatchId`` and to filter by DB evaluation; defaults to
``<KAGENT_PATH>/karena.db.backup_20260125_203200`` (override with ``KARENA_DB_PATH`` / ``--karena-db-path``).

**No ``dev.env`` required:** ``KAGENT_PATH`` / ``BASE_PATH`` are inferred from this file's location
(``…/Kernel_Agent/src/Kernel_Agent/tools/.../run_swe_minimization.py`` → repo root). Exported env
vars still win (``setdefault``). Activate your venv, then ``python run_swe_minimization.py ...``.

``_bootstrap_env_like_sample()`` sets ``KBENCH_PATH`` / ``AGENT_PATCHES_DIR`` / ``KARENA_DB_PATH`` /
``KBDR_*`` and ``sys.path`` like ``sample_minimize_edits.py`` (``BASE_PATH`` first for KBDr_Runner).

AIDEV-NOTE: This file is argument parsing and validation only. Discovery and execution
live in run_swe_agent/ — ``agent_patches.load_passes()`` then ``agent_patches.run()``,
which minimizes each pass through ``traj_cli`` (sequential and parallel share one worker).
"""
from __future__ import annotations

import argparse
import os
import sys


# AIDEV-NOTE: This file is now a dispatcher. The two runners, the Karena DB helpers and the
# shared parallel path were split into run_swe_agent/ — see z-cpl-73.md. Behaviour is
# unchanged; the code was moved, not rewritten.
# AIDEV-NOTE: bootstrap_env() must run before the runners are imported — it sets sys.path
# and the KBENCH_PATH / AGENT_PATCHES_DIR / KARENA_DB_PATH env vars the original relied on
# having in place before its heavy imports.
from patch_minimizer.frontends.kernel.example_code.run_swe_agent import utils

utils.bootstrap_env()

from patch_minimizer.frontends.kernel.example_code.run_swe_agent import agent_patches  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run minimization for Karena agent-patches trajectories (events[] traj.json)."
    )
    parser.add_argument(
        "--agent-patches-dir",
        metavar="DIR",
        help="Extracted Karena agent-patches tree (<agentPatchId>/traj.json). Default: AGENT_PATCHES_DIR.",
    )
    parser.add_argument(
        "--agent-patches",
        action="store_true",
        help="Use AGENT_PATCHES_DIR (same default as sample layout: $KAGENT_PATH/agent-patches).",
    )
    parser.add_argument(
        "--benchmark-folder",
        default=None,
        help="Benchmark folder (per-bug JSONs + golden_subset.json). Default: KBENCH_PATH after bootstrap.",
    )
    parser.add_argument(
        "--linux-dir",
        default=None,
        help="Linux repo path. Default: $BASE_PATH/collection_of_linux_repos/linux-501.",
    )
    parser.add_argument(
        "--linux-dir-start",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Parallel pool: first linux-{N} index under collection_of_linux_repos. "
            "Used with --linux-dir-end when --num-parallel > 1 and --linux-dirs omitted."
        ),
    )
    parser.add_argument(
        "--linux-dir-end",
        type=int,
        default=None,
        metavar="N",
        help="Parallel pool: last linux-{N} index (inclusive).",
    )
    parser.add_argument(
        "--run-jobs",
        action="store_true",
        help="Submit real kernel build/test jobs (kBDR). Default: dry run.",
    )
    parser.add_argument(
        "--minimize-at",
        choices=("node", "edit"),
        default="edit",
        help="Minimization granularity.",
    )
    parser.add_argument(
        "--save-dir",
        metavar="DIR",
        help=(
            "Directory for per-candidate minimization_results.json; also writes "
            "minimization_summary.json (same mechanism as minimize_patches.run_minimization_on_results)."
        ),
    )
    parser.add_argument(
        "--syzkaller-rollback-tag",
        default="master",
        help="Syzkaller tag for reproducer.",
    )
    parser.add_argument(
        "--karena-db-path",
        default=None,
        metavar="PATH",
        help="Path to karena.db SQLite. If set, only minimize passes that match kGym evaluation for agentPatchId.",
    )
    parser.add_argument(
        "--agent-config-id",
        type=int,
        default=None,
        metavar="ID",
        help="agentConfigId in Karena DB. If omitted, uses DB default for swe-agent-c-unlimited.",
    )
    parser.add_argument(
        "--kgym-evaluation",
        default="notReproduced",
        help="Value of patch_crash_resolution_evaluations.kGymEvaluation to treat as crash resolved.",
    )
    parser.add_argument(
        "--db-agent-family",
        choices=("all", "swe-agent", "mini-swe-agent", "openhands", "crashfixer"),
        default="swe-agent",
        help=(
            "When --karena-db-path is set, pre-filter trajectory loading to this "
            "agent_configurations.agent family. Default: swe-agent."
        ),
    )
    parser.add_argument(
        "--num-parallel",
        type=int,
        default=1,
        metavar="N",
        help="Number of minimization workers (Queue pool size). Default: 1 (sequential).",
    )
    parser.add_argument(
        "--linux-dirs",
        nargs="+",
        metavar="DIR",
        default=None,
        help="Explicit Linux repo paths for the worker pool. Required count >= --num-parallel if set.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Process at most the first N loaded passes (0 = all). Same ordering as verify_swe_agent_dataset.",
    )
    args = parser.parse_args()

    if args.agent_patches:
        args.agent_patches_dir = args.agent_patches_dir or os.environ.get("AGENT_PATCHES_DIR")
    if not args.agent_patches_dir:
        parser.error("Provide --agent-patches-dir DIR or --agent-patches")

    # AIDEV-NOTE: ``--benchmark-folder "$KBENCH_PATH"`` with unset KBENCH passes ""; treat as missing.
    _bf = args.benchmark_folder
    if _bf is not None and not str(_bf).strip():
        _bf = None
    benchmark_folder = _bf or os.environ.get("KBENCH_PATH")
    if not benchmark_folder or not os.path.isdir(benchmark_folder):
        print(
            "Error: benchmark folder missing or not a directory. "
            "Set KBENCH_PATH or pass --benchmark-folder /path/to/benchmark (per-bug JSONs).",
            file=sys.stderr,
        )
        return 1

    karena_db: str | None = args.karena_db_path
    if karena_db is not None and not str(karena_db).strip():
        karena_db = None
    if karena_db is None:
        _cand = os.environ.get("KARENA_DB_PATH")
        if _cand and os.path.isfile(_cand):
            karena_db = _cand
    elif not os.path.isfile(karena_db):
        raise FileNotFoundError(f"karena DB not found: {karena_db}")

    if args.agent_patches_dir and not os.path.isdir(args.agent_patches_dir):
        kagent = os.environ.get("KAGENT_PATH", "")
        zip_hint = os.path.join(kagent, "agent-patches.zip") if kagent else ""
        msg = (
            f"Error: agent-patches directory missing: {args.agent_patches_dir}\n"
            "Extract agent-patches.zip there (mkdir + unzip or python zipfile.extractall), "
            "or pass --agent-patches-dir /path/to/extracted/root."
        )
        if zip_hint and os.path.isfile(zip_hint):
            msg += f"\nFound zip: {zip_hint} (run extract in {kagent!r})."
        print(msg, file=sys.stderr)
        return 1

    base_path = os.environ.get("BASE_PATH", "")
    linux_dir = args.linux_dir or os.path.join(
        base_path, "collection_of_linux_repos", "linux-501"
    )
    if args.num_parallel <= 1 and not os.path.isdir(linux_dir):
        print(f"Error: linux_dir not found: {linux_dir}", file=sys.stderr)
        return 1

    # AIDEV-NOTE: Only the path is needed (for the summary's config record). traj_cli loads
    # golden_subset.json itself; minimize_one_traj crosses a CLI boundary and can only forward
    # strings, so a dict in shared_config would never reach it.
    golden_path = os.path.join(benchmark_folder, "golden_subset.json")
    if not os.path.isfile(golden_path):
        print("Warning: golden_subset.json not found; KernelEnvironment may fail.", file=sys.stderr)

    shared_config = {
        "benchmark_folder": benchmark_folder,
        "run_jobs": args.run_jobs,
        "syzkaller_rollback_tag": args.syzkaller_rollback_tag,
        "minimize_at": args.minimize_at,
    }

    # ---- Dispatch ----------------------------------------------------------
    # AIDEV-NOTE: The single-file path (--traj-json/--bug-id, run_swe_agent/single_traj.py)
    # was removed: it wrote to the constant save_dir/traj_file__c0/ regardless of bug_id, so
    # runs overwrote each other, resume returned the wrong bug's result, and
    # metrics/.../discover_pairs.py (which expects */swe_candidate_0/) could not see it.
    # For one trajectory use cli/traj_cli.py --agent swe-agent --traj ... .
    num_parallel = max(1, args.num_parallel)

    passes_to_run, db_agent_patch_allowed = agent_patches.load_passes(
        args, karena_db, benchmark_folder
    )
    if passes_to_run is None:
        return 0

    return agent_patches.run(
        passes_to_run, db_agent_patch_allowed, args,
        benchmark_folder=benchmark_folder, golden_path=golden_path,
        linux_dir=linux_dir, base_path=base_path, shared_config=shared_config,
        num_parallel=num_parallel,
    )


if __name__ == "__main__":
    sys.exit(main())
