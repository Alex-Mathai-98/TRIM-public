#!/usr/bin/env python3
"""Minimize one agent trajectory against the kernel benchmark.

Single-trajectory entry point — takes one traj.json + bug_id, minimizes it,
returns results. Orchestration (directory walking, repo pooling, resume) lives
in the calling scripts (run_openhands_minimization.py, etc.).

Usage:
    python -m patch_minimizer.frontends.kernel.cli traj \\
        --agent openhands --traj /path/to/traj.json --bug-id <hash> \\
        --benchmark-folder /path/to/bugs --repo-dir /path/to/linux-501 \\
        --save-dir /tmp/out

    python -m patch_minimizer.frontends.kernel.cli traj \\
        --agent mini-swe-agent --traj /path/to/traj.json --bug-id <hash> \\
        --benchmark-folder /path/to/bugs --repo-dir /path/to/linux-501 \\
        --run-jobs --save-dir /tmp/out
"""
from __future__ import annotations

import argparse
import os
import sys

from patch_minimizer.frontends.kernel.cli.common import (
    load_bug_data,
    load_golden_subset,
)


# ---------------------------------------------------------------------------
# Adapter registration
# ---------------------------------------------------------------------------

_SUPPORTED_AGENTS = {
    "openhands": "patch_minimizer.agent_adapters.openhands_adapter",
    "mini-swe-agent": "patch_minimizer.agent_adapters.mini_swe_agent_adapter",
    "swe-agent": "patch_minimizer.agent_adapters.swe_agent_adapter",
}


def _import_adapter(agent: str) -> None:
    """Side-effect import to register the agent's trajectory parser."""
    module_path = _SUPPORTED_AGENTS[agent]
    __import__(module_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None, *, code_dir_lock=None,
         print_summary: bool = False) -> dict | None:
    parser = argparse.ArgumentParser(
        description="Minimize one agent trajectory against the kernel benchmark."
    )
    parser.add_argument(
        "--agent", required=True, choices=sorted(_SUPPORTED_AGENTS),
        help="Agent whose adapter to use for trajectory parsing.",
    )
    # AIDEV-NOTE: --traj / --bug-id / --repo-dir are the same names as the SWE-bench traj CLI.
    # The old --traj-json / --linux-dir aliases were removed (2026-10-02); dests unchanged.
    parser.add_argument(
        "--traj", dest="traj_json", required=True, metavar="PATH",
        help="Path to the trajectory file.",
    )
    parser.add_argument(
        "--bug-id", required=True,
        help="Kernel bug identifier (used to load benchmark data).",
    )
    parser.add_argument(
        "--benchmark-folder", default=None,
        help="Benchmark folder (per-bug JSONs + golden_subset.json). Default: KBENCH_PATH.",
    )
    parser.add_argument(
        "--repo-dir", dest="linux_dir", default=None, metavar="PATH",
        help="Path to a Linux repository clone (required unless --only-parse).",
    )
    parser.add_argument("--run-jobs", action="store_true",
                        help="Submit real kernel build/test jobs.")
    parser.add_argument(
        "--minimize-at", choices=("node", "edit"), default="edit",
        help="Minimization granularity.",
    )
    parser.add_argument("--save-dir", metavar="DIR",
                        help="Output directory for minimization_results.json.")
    parser.add_argument(
        "--syzkaller-rollback-tag", default="master",
        help="Syzkaller tag for reproducer.",
    )
    parser.add_argument(
        "--only-parse", action="store_true",
        help="Parse only: return the chosen patch list and stop (no bug data, repo or jobs).",
    )

    args = parser.parse_args(argv)
    if not args.only_parse and args.linux_dir is None:
        parser.error("--repo-dir is required unless --only-parse is given")

    # ---- Validate inputs ---------------------------------------------------
    if not os.path.isfile(args.traj_json):
        print(f"Error: trajectory file not found: {args.traj_json}", file=sys.stderr)
        return None

    # AIDEV-NOTE: --only-parse needs no benchmark folder or repo; skip their checks.
    if not args.only_parse:
        _bf = args.benchmark_folder
        if _bf is not None and not str(_bf).strip():
            _bf = None
        benchmark_folder = _bf or os.environ.get("KBENCH_PATH")
        if not benchmark_folder or not os.path.isdir(benchmark_folder):
            print(
                "Error: benchmark folder missing. Set KBENCH_PATH or pass --benchmark-folder.",
                file=sys.stderr,
            )
            return None

        if not os.path.isdir(args.linux_dir):
            print(f"Error: linux dir not found: {args.linux_dir}", file=sys.stderr)
            return None

        golden_path = os.path.join(benchmark_folder, "golden_subset.json")
        if not os.path.isfile(golden_path):
            print("Warning: golden_subset.json not found; KernelEnvironment may fail.", file=sys.stderr)
        golden_subset_data = load_golden_subset(golden_path) if os.path.isfile(golden_path) else {}

    # ---- Register adapter --------------------------------------------------
    _import_adapter(args.agent)

    # ---- Load trajectory ---------------------------------------------------
    # AIDEV-NOTE: Lazy import to avoid pre-existing circular import
    # (core/edit_applier.py ↔ agent_adapters/.../sed_parsing.py)
    from patch_minimizer.agent_adapters.agent_adapter_base.utils import load_trajectory_file
    from patch_minimizer.frontends.kernel.cli.minimize_edits import minimize_edits
    from patch_minimizer.core.rw_lock import RWLock

    candidates, patch_lists = load_trajectory_file(args.traj_json)
    if not patch_lists:
        print(f"No candidates with edits in {args.traj_json}")
        return None

    # AIDEV-NOTE: parse_trajectory() returns 0 or 1 CandidateTrajectory per file,
    # so patch_lists has at most one entry. Take the first non-empty one.
    patch_list = None
    for pl in patch_lists:
        if pl and any(edits for _, edits in pl):
            patch_list = pl
            break

    if patch_list is None:
        print(f"No candidate with edits in {args.traj_json}")
        return None

    if args.only_parse:
        # AIDEV-NOTE: parse-only path for tests; returns before bug data / repo / minimizer.
        # Used by tests/parsing_benchmark_tests/live_kbench_*/. The summary prints only for
        # command-line runs (print_summary=True from cli/main.py); the tests call main()
        # ~3,200 times and must stay quiet. Mirrors swebench/cli/traj_cli.py's "Parsed:".
        if print_summary:
            edits = [e for _, node_edits in patch_list for e in node_edits]
            print("\nParsed:")
            print(f"  nodes:          {len(patch_list)}")
            print(f"  edits:          {len(edits)}")
            print(f"  files changed:  {sorted({e.filename for e in edits})}")
        return {"candidate": candidates[0], "patch_list": patch_list}

    # ---- Load bug data -----------------------------------------------------
    base_commit, kernel_base_url = load_bug_data(benchmark_folder, args.bug_id)

    # ---- Minimize ----------------------------------------------------------
    # AIDEV-NOTE: Library callers (minimize_one_traj) pass the pool's lock so it is the same
    # object guarding this repo everywhere. A bare CLI invocation has no pool, so it mints its
    # own. Keyword-only and defaulted — argv cannot carry a Python object.
    if code_dir_lock is None:
        code_dir_lock = RWLock()

    result = minimize_edits(
        bug_id=args.bug_id,
        patch_list=patch_list,
        base_commit=base_commit,
        linux_dir=args.linux_dir,
        code_dir_lock=code_dir_lock,
        benchmark_folder=benchmark_folder,
        golden_subset_data=golden_subset_data,
        kernel_base_url=kernel_base_url,
        run_jobs=args.run_jobs,
        syzkaller_rollback_tag=args.syzkaller_rollback_tag,
        minimize_at=args.minimize_at,
        save_dir=args.save_dir,
    )

    if result:
        print(
            f"  reduction={result['reduction']}, "
            f"minimized_edits={result['minimized_edit_count']}"
        )

    return result


if __name__ == "__main__":
    result = main(print_summary=True)
    sys.exit(0 if result is not None else 1)
