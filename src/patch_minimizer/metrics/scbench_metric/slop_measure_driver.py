"""SCBench slop-metric measurement driver (runs INSIDE the SCBench 3.12 env).

AIDEV-NOTE: This is the ONLY file that imports ``slop_code``. It is executed as
a subprocess by ``SCBenchMetricRunner`` using the metric-home venv python
(``$SCBENCH_METRIC_HOME/venv/bin/python``, Python 3.12) — NOT the Kernel_Agent
3.11 interpreter. It reads a directory of source, runs SCBench's
``measure_snapshot_quality`` and prints a flat JSON metrics dict on stdout.
Everything else (structlog logs, ast-grep warnings, graph-skip notices) goes to
stderr and is ignored by the caller.

The two headline SCBench composites are reproduced here from the raw snapshot:
  * verbosity = verbosity_flagged_sloc_lines / loc
        (SLOC covered by clone lines UNION ast-grep-flagged lines)
  * erosion   = mass.high_cc_pct
        (fraction of complexity-mass  sum(cc * sqrt(sloc))  in CC>10 functions)
See ``docs/metrics-reference.md`` in the slop-code-bench repo.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from slop_code.metrics.driver import _collect_ast_grep_sloc_lines
from slop_code.metrics.driver import _collect_clone_sloc_lines
from slop_code.metrics.driver import _get_sloc_lines
from slop_code.metrics.driver import measure_snapshot_quality


def _per_file_breakdown(files, snapshot_dir: Path) -> dict:
    """Per-file flagged-line counts, mirroring driver.compute_aggregates exactly.

    ``verbosity_flagged_sloc_lines`` is computed per file as
    ``len(clone_sloc_lines | ast_grep_sloc_lines)`` then summed. We reproduce
    that here so the caller can aggregate the snapshot two ways (all files vs.
    source-only) from a single measurement.
    """
    out: dict[str, dict] = {}
    for fm in files:
        source_path = snapshot_dir / fm.file_path
        sloc_lines = _get_sloc_lines(source_path)
        clone = _collect_clone_sloc_lines(fm, sloc_lines)
        sg = _collect_ast_grep_sloc_lines(fm, sloc_lines)
        mass = high = 0.0
        for s in fm.symbols:
            if s.type in ("function", "method") and s.sloc > 0:
                m = s.complexity * math.sqrt(s.sloc)
                mass += m
                if s.complexity > 10:
                    high += m
        out[fm.file_path] = {
            "loc": fm.lines.total_lines,
            "verbosity_flagged_lines": len(clone | sg),
            "clone_lines": fm.redundancy.clone_lines if fm.redundancy else 0,
            "ast_grep_violations": len(fm.ast_grep_violations),
            "cc_mass": mass,
            "cc_mass_high": high,
        }
    return out


def measure(snapshot_dir: Path, entry_file: str) -> dict:
    """Measure SCBench slop metrics for every supported file in ``snapshot_dir``."""
    snap, files = measure_snapshot_quality(entry_file, snapshot_dir)

    # erosion = high-CC mass share  (mass.high_cc_pct), recomputed from symbols
    total_mass = high_mass = 0.0
    for fm in files:
        for s in fm.symbols:
            if s.type in ("function", "method") and s.sloc > 0:
                m = s.complexity * math.sqrt(s.sloc)
                total_mass += m
                if s.complexity > 10:
                    high_mass += m

    loc = snap.lines.total_lines
    flagged = snap.verbosity_flagged_sloc_lines
    return {
        "per_file": _per_file_breakdown(files, snapshot_dir),
        "file_count": snap.file_count,
        "loc": loc,
        "sloc": snap.lines.loc,
        # --- headline composites ---
        "verbosity_flagged_lines": flagged,
        "verbosity_pct": (flagged / loc) if loc else 0.0,
        "erosion_high_cc_pct": (high_mass / total_mass) if total_mass else 0.0,
        # --- components ---
        "clone_lines": snap.redundancy.clone_lines,
        "cloned_sloc_lines": snap.redundancy.cloned_sloc_lines,
        "ast_grep_violations": snap.ast_grep.violations,
        "ast_grep_weighted": snap.ast_grep.weighted,
        "single_use_functions": snap.waste.single_use_functions,
        "trivial_wrappers": snap.waste.trivial_wrappers,
        "unused_variables": snap.waste.unused_variables,
        "cc_sum": snap.functions.cc_sum,
        "cc_max": snap.functions.cc_max,
        "cc_high_count": snap.functions.cc_high_count,
    }


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: slop_measure_driver.py <snapshot_dir> <entry_file>",
              file=sys.stderr)
        return 2
    snapshot_dir = Path(sys.argv[1])
    entry_file = sys.argv[2]
    result = measure(snapshot_dir, entry_file)
    # ONLY the JSON goes to stdout so the caller can parse it cleanly.
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
