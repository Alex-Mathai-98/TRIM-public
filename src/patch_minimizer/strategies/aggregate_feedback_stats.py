"""
Standalone utility to aggregate feedback_stats from minimization summary files.

As standalone script:
    python src/Kernel_Agent/tools/solution_minimization/solution_minimization_strategies/aggregate_feedback_stats.py /path/to/minimization_summary.json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from patch_minimizer.strategies.feedback_stats import _summarize_feedback_stats


def aggregate_feedback_stats_from_summary(summary_path: str, save: bool = True) -> dict:
    """
    Aggregate feedback_stats from successful_bugs in an existing minimization_summary.json.

    Use this to add aggregated_feedback_stats to a summary file that was created
    before this feature was implemented.

    Args:
        summary_path: Path to minimization_summary.json file
        save: If True, save the updated summary back to the file

    Returns:
        The aggregated_feedback_stats dict (or None if no stats found)
    """
    summary_file = Path(summary_path)
    if not summary_file.exists():
        raise FileNotFoundError(f"Summary file not found: {summary_path}")

    with open(summary_file, 'r') as f:
        summary_data = json.load(f)

    # Aggregate detailed stats from each successful bug
    aggregated = {}
    for bug in summary_data.get("successful_bugs", []):
        fb_stats = bug.get("feedback_stats", bug.get("stats", {}))
        detailed = fb_stats.get("detailed", {})
        for k, v in detailed.items():
            aggregated[k] = aggregated.get(k, 0) + v

    # Summarize
    aggregated_feedback_stats = _summarize_feedback_stats(aggregated) if aggregated else None

    if aggregated_feedback_stats:
        summary_data["aggregated_feedback_stats"] = aggregated_feedback_stats

        if save:
            # Atomic write
            temp_file = str(summary_file) + ".tmp"
            with open(temp_file, 'w') as f:
                json.dump(summary_data, f, indent=2)
            os.replace(temp_file, summary_file)
            print(f"Updated {summary_path} with aggregated_feedback_stats")
            print(f"  Total feedbacks: {aggregated_feedback_stats['total_feedbacks']}")
    else:
        print(f"No feedback_stats found in successful_bugs of {summary_path}")

    return aggregated_feedback_stats


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python aggregate_feedback_stats.py <path_to_minimization_summary.json>")
        sys.exit(1)
    aggregate_feedback_stats_from_summary(sys.argv[1])
