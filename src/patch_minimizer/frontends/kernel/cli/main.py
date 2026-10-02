#!/usr/bin/env python3
"""Kernel minimization CLI dispatcher.

Usage:
    # kAgent / CrashFixer — loads .pkl trajectories, batch driver over results tree
    python -m patch_minimizer.frontends.kernel.cli kagent \\
        --results-dir ... --benchmark-folder ... --golden-subset ... --linux-dirs ...

    # OpenHands — single trajectory file
    python -m patch_minimizer.frontends.kernel.cli traj \\
        --agent openhands --traj ... --bug-id ... --benchmark-folder ... --repo-dir ...

    # mini-SWE-agent — same format, different trajectory parser
    python -m patch_minimizer.frontends.kernel.cli traj \\
        --agent mini-swe-agent --traj ... --bug-id ... --benchmark-folder ... --repo-dir ...
"""
from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python -m patch_minimizer.frontends.kernel.cli <subcommand> [args...]")
        print()
        print("Subcommands:")
        print("  kagent    CrashFixer/kAgent path (.pkl → ToolSolnMinimize)")
        print("  traj      Trajectory-based path (openhands, mini-swe-agent, swe-agent)")
        print()
        print("Run '<subcommand> --help' for subcommand-specific options.")
        return 0

    subcommand = sys.argv[1]
    # AIDEV-NOTE: Strip the subcommand from argv so each CLI's argparse sees only its own args
    sys.argv = [sys.argv[0]] + sys.argv[2:]

    if subcommand == "kagent":
        from patch_minimizer.frontends.kernel.cli.kagent.kagent_cli import main as kagent_main
        kagent_main()
        return 0
    elif subcommand == "traj":
        from patch_minimizer.frontends.kernel.cli.traj_cli import main as traj_main
        # AIDEV-NOTE: traj_main() returns the result dict (or None), not an exit code —
        # passing the dict to sys.exit printed it and exited 1. Same as swebench/cli/main.py.
        return 0 if traj_main(print_summary=True) is not None else 1
    else:
        print(f"Unknown subcommand: {subcommand}", file=sys.stderr)
        print("Use 'kagent' or 'traj'. Run with --help for details.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
