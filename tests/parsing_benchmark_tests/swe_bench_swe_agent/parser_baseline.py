#!/usr/bin/env python3
"""Parser regression baseline for SWE-bench x SWE-agent trajectories.

Runs every ``trajs/<instance_id>.traj`` through the real SWE-bench entry point,
``traj_cli.minimize_traj(..., only_parse=True)``, and compares the result against
``trajectory_test_cases/<instance_id>.json``.

    python parser_baseline.py build    # (re)write all goldens
    python parser_baseline.py verify   # re-parse and compare; exit 1 on any mismatch

AIDEV-NOTE: no per-file try/except on purpose — a parser crash surfaces as a traceback
naming the trajectory. Final-diff correctness is checked separately by
sibling ``verify_swe_bench_edit_applicability.py``.
"""
from __future__ import annotations

import dataclasses
import difflib
import json
import logging
import sys
from pathlib import Path

from patch_minimizer.frontends.swebench.cli.traj_cli import minimize_traj

HERE = Path(__file__).resolve().parent
TRAJS_DIR = HERE / "trajs"
GOLDENS_DIR = HERE / "trajectory_test_cases"

_QUIET = logging.getLogger("parser_baseline")
_QUIET.setLevel(logging.WARNING)


def parse(traj_path: Path) -> str:
    """Parse one trajectory via traj_cli and return its stable JSON serialization."""
    result = minimize_traj(
        traj_path=traj_path,
        instance_id=traj_path.stem,
        save_dir=Path("/unused"),
        repo_dir="/unused",
        logger=_QUIET,
        only_parse=True,
    )
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
                    "feedback_commands": r.feedback_commands,
                }
                for r in candidate.revisions
            ],
            # AIDEV-NOTE: patch_list is after filter_to_submission_paths, i.e. what the
            # minimizer actually receives; its edits are already captured in revisions.
            "patch_list_shape": [
                [e.global_edit_idx for e in edits] for _, edits in result["patch_list"]
            ],
            "test_files": sorted(result["test_files"]),
            "feedback_commands": result["feedback_commands"],
            "sub_paths": result["sub_paths"],
        }
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def build() -> int:
    GOLDENS_DIR.mkdir(exist_ok=True)
    trajs = sorted(TRAJS_DIR.glob("*.traj"))
    for traj in trajs:
        (GOLDENS_DIR / f"{traj.stem}.json").write_text(parse(traj), encoding="utf-8")
    print(f"built {len(trajs)} goldens -> {GOLDENS_DIR}")
    return 0


def verify() -> int:
    trajs = sorted(TRAJS_DIR.glob("*.traj"))
    mismatched = []
    for traj in trajs:
        expected = (GOLDENS_DIR / f"{traj.stem}.json").read_text(encoding="utf-8")
        actual = parse(traj)
        if actual != expected:
            if not mismatched:
                diff = difflib.unified_diff(
                    expected.splitlines(keepends=True), actual.splitlines(keepends=True),
                    fromfile=f"{traj.stem} (golden)", tofile=f"{traj.stem} (current)",
                )
                print("".join(list(diff)[:40]), file=sys.stderr)
            mismatched.append(traj.stem)
    print(f"verify: total={len(trajs)} matched={len(trajs) - len(mismatched)} "
          f"mismatched={len(mismatched)}")
    if mismatched:
        print("mismatched:", " ".join(mismatched), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    modes = {"build": build, "verify": verify}
    if len(sys.argv) != 2 or sys.argv[1] not in modes:
        sys.exit(f"usage: {sys.argv[0]} build|verify")
    sys.exit(modes[sys.argv[1]]())
