#!/usr/bin/env python3
"""SWE-bench minimization CLI dispatcher.

Usage:
    # one trajectory -> one result
    python -m patch_minimizer.frontends.swebench.cli traj \\
        --agent swe-agent \\
        --traj results/swe-bench-swe-agent-trajs/sphinx-doc__sphinx-8459.traj \\
        --bug-id sphinx-doc__sphinx-8459 --save-dir /tmp/min_one

A whole directory of trajectories is the batch driver's job, not a subcommand here:
    PYTHONPATH=src python src/patch_minimizer/frontends/swebench/example_code/\\
run_swebench_minimization.py --traj-dir ... --save-dir ... --num-parallel 8
"""
from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python -m patch_minimizer.frontends.swebench.cli <subcommand> [args...]")
        print()
        print("Subcommands:")
        print("  traj      Minimize one .traj file (one instance)")
        print()
        print("Run '<subcommand> --help' for subcommand-specific options.")
        print("For a whole directory, use example_code/run_swebench_minimization.py.")
        return 0

    subcommand = sys.argv[1]
    # AIDEV-NOTE: Strip the subcommand so the target's argparse sees only its own args.
    sys.argv = [sys.argv[0]] + sys.argv[2:]

    if subcommand == "traj":
        from patch_minimizer.frontends.swebench.cli.traj_cli import main as traj_main
        # AIDEV-NOTE: traj_main returns dict | None, not an exit code.
        return 0 if traj_main() is not None else 1

    print(f"Unknown subcommand: {subcommand}", file=sys.stderr)
    print("Use 'traj'. Run with --help for details.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
