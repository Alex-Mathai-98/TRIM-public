#!/usr/bin/env python3
"""Parser regression baseline for live-kbench x SWE-agent trajectories.

Runs every ``trajs/<bug_hash>/run_N.json`` through the real kernel entry point,
``frontends/kernel/cli/traj_cli.main([... "--only-parse"])``, and compares the result against
``trajectory_test_cases/<bug_hash>/run_N.json``.

    python parser_baseline.py build    # (re)write all goldens
    python parser_baseline.py verify   # re-parse and compare; exit 1 on any mismatch

AIDEV-NOTE: no per-file try/except on purpose — a parser crash surfaces as a traceback
naming the trajectory. Final-diff correctness is checked separately by sibling
``verify_final_diff.py``.
"""
from __future__ import annotations

import dataclasses
import difflib
import json
import logging
import sys
from pathlib import Path

from patch_minimizer.frontends.kernel.cli import traj_cli

HERE = Path(__file__).resolve().parent
TRAJS_DIR = HERE / "trajs"
GOLDENS_DIR = HERE / "trajectory_test_cases"


def iter_runs() -> list[Path]:
    """All ``trajs/<bug_hash>/run_N.json``, sorted."""
    return sorted(TRAJS_DIR.glob("*/run_*.json"))


def parse(run_path: Path) -> str:
    """Parse one run via kernel traj_cli and return its stable JSON serialization."""
    result = traj_cli.main([
        "--agent", "swe-agent",
        "--traj", str(run_path),
        "--bug-id", run_path.parent.name,
        "--only-parse",
    ])
    if result is not None:
        candidate = result["candidate"]
        result = {
            "success_step": candidate.success_step,
            "revisions": [
                {
                    "revision_index": r.revision_index,
                    "start_step": r.start_step,
                    "end_step": r.end_step,
                    "edits": [dataclasses.asdict(e) for e in r.edits],
                }
                for r in candidate.revisions
            ],
            "patch_list_shape": [
                [e.global_edit_idx for e in edits] for _, edits in result["patch_list"]
            ],
        }
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def golden_path(run_path: Path) -> Path:
    return GOLDENS_DIR / run_path.parent.name / run_path.name


def build() -> int:
    runs = iter_runs()
    for run in runs:
        out = golden_path(run)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(parse(run), encoding="utf-8")
    print(f"built {len(runs)} goldens -> {GOLDENS_DIR}")
    return 0


def verify() -> int:
    runs = iter_runs()
    mismatched = []
    for run in runs:
        expected = golden_path(run).read_text(encoding="utf-8")
        actual = parse(run)
        if actual != expected:
            name = f"{run.parent.name}/{run.stem}"
            if not mismatched:
                diff = difflib.unified_diff(
                    expected.splitlines(keepends=True), actual.splitlines(keepends=True),
                    fromfile=f"{name} (golden)", tofile=f"{name} (current)",
                )
                print("".join(list(diff)[:40]), file=sys.stderr)
            mismatched.append(name)
    print(f"verify: total={len(runs)} matched={len(runs) - len(mismatched)} "
          f"mismatched={len(mismatched)}")
    if mismatched:
        print("mismatched:", " ".join(mismatched), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    logging.disable(logging.WARNING)
    modes = {"build": build, "verify": verify}
    if len(sys.argv) != 2 or sys.argv[1] not in modes:
        sys.exit(f"usage: {sys.argv[0]} build|verify")
    sys.exit(modes[sys.argv[1]]())
