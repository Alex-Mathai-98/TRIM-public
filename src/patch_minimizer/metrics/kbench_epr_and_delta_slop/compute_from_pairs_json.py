"""Compute per-bug average modified lines from a pairs JSON file.

Reads the intermediate JSON produced by discover_pairs.py:
    { bug_id: { traj_N: { original: path, minimized: path } } }

For each bug, computes modified_lines for both original and minimized patches
in each trajectory, averages across trajectories, then averages across all bugs.

Usage:
    python compute_from_pairs_json.py --pairs pairs.json
    python compute_from_pairs_json.py --pairs pairs.json --out-json full_result.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from modified_lines import modified_lines


def compute(pairs: dict) -> dict:
    """Compute per-traj, per-bug, and dataset-level averages."""
    bug_results = {}

    for bug_id in sorted(pairs.keys()):
        trajs = pairs[bug_id]
        traj_results = {}

        for traj_key in sorted(trajs.keys()):
            entry = trajs[traj_key]
            orig_path = Path(entry["original"])
            mini_path = Path(entry["minimized"])

            if not orig_path.is_file():
                continue

            o = modified_lines(orig_path)
            m = modified_lines(mini_path) if mini_path.is_file() else o

            traj_entry = {"original": o, "minimized": m, "delta": o - m}
            if m > o:
                traj_entry["clamped"] = True
                traj_entry["pre_clamp_minimized"] = m
                m = o
                traj_entry["minimized"] = m
                traj_entry["delta"] = 0
            traj_results[traj_key] = traj_entry

        if not traj_results:
            continue

        orig_values = [t["original"] for t in traj_results.values()]
        mini_values = [t["minimized"] for t in traj_results.values()]

        avg_orig = sum(orig_values) / len(orig_values)
        avg_mini = sum(mini_values) / len(mini_values)

        bug_results[bug_id] = {
            "num_trajs": len(traj_results),
            "trajectories": traj_results,
            "avg_original": avg_orig,
            "avg_minimized": avg_mini,
            "avg_delta": avg_orig - avg_mini,
        }

    if not bug_results:
        return {"dataset": {"bugs": 0}, "per_bug": {}}

    all_orig = [b["avg_original"] for b in bug_results.values()]
    all_mini = [b["avg_minimized"] for b in bug_results.values()]
    all_delta = [b["avg_delta"] for b in bug_results.values()]

    return {
        "dataset": {
            "bugs": len(bug_results),
            "avg_original": sum(all_orig) / len(all_orig),
            "avg_minimized": sum(all_mini) / len(all_mini),
            "avg_delta": sum(all_delta) / len(all_delta),
            "pct_reduction": 100 * sum(all_delta) / sum(all_orig) if sum(all_orig) else 0,
        },
        "per_bug": bug_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute modified lines from pairs JSON")
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None, help="Text report output")
    parser.add_argument("--out-json", type=Path, default=None, help="Full JSON artifact output")
    args = parser.parse_args()

    pairs = json.loads(args.pairs.read_text())
    result = compute(pairs)

    # JSON artifact
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(result, indent=2))
        print(f"Wrote JSON: {args.out_json}")

    # Text report
    out = args.out.open("w") if args.out else sys.stdout
    try:
        ds = result["dataset"]
        if ds["bugs"] == 0:
            print("No data.", file=out)
            return

        # Per-bug details
        for bug_id, bug in result["per_bug"].items():
            print(f"## {bug_id} ({bug['num_trajs']} trajs)", file=out)
            for traj_key, tv in bug["trajectories"].items():
                print(f"  {traj_key}: orig={tv['original']} mini={tv['minimized']} delta={tv['delta']}", file=out)
            print(f"  avg: orig={bug['avg_original']:.1f} mini={bug['avg_minimized']:.1f} delta={bug['avg_delta']:.1f}", file=out)
            print(file=out)

        # Dataset summary
        print("---", file=out)
        print(f"Bugs: {ds['bugs']}", file=out)
        print(f"Avg original lines:  {ds['avg_original']:.2f}", file=out)
        print(f"Avg minimized lines: {ds['avg_minimized']:.2f}", file=out)
        print(f"Avg delta (reduction): {ds['avg_delta']:.2f}", file=out)
        print(f"Pct reduction: {ds['pct_reduction']:.1f}%", file=out)
    finally:
        if args.out:
            out.close()
            print(f"Wrote text: {args.out}")


if __name__ == "__main__":
    main()
