"""M1.A — Profile proxy-test-file edits across SWE-agent SWE-bench trajectories.

For each ``.traj`` file under ``--trajs-dir``:

  1. Walk the trajectory's ``action`` log and record, per file, every
     str_replace_editor create / str_replace / insert event AND every
     ``python <file>`` / ``pytest <file>`` run event.
  2. A "proxy test candidate" is any file that has at least one create
     event AND at least one run event (the agent both wrote it and
     used it).
  3. Count subsequent write events on each candidate (re-create, str_replace,
     insert) AFTER the first create.
  4. Categorize each candidate:
       single-write : 0 subsequent edits
       tweaked      : 1-3 subsequent edits, no re-create
       rewritten    : 4+ subsequent edits OR any re-create
  5. Per trajectory, the "primary" proxy test = the candidate with the
     most run-events after first-create (ties broken by latest create step).

The categorization answers the M1.A gate question (see ``swe-bench-plan.md``
§13): can we safely treat the trajectory-final version of the proxy test
file as the oracle? "single-write" trajectories are unambiguously safe;
"tweaked" trajectories are likely safe (final ≈ versions used at runs);
"rewritten" trajectories may need per-revision snapshots.

Usage:
    python profile_swe_bench_proxy_test_edits.py \\
        --trajs-dir <dir of .traj files> \\
        [--output-json <path to per-trajectory detail>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

# AIDEV-NOTE: regexes match against the trajectory step's `action` field. The
# str_replace_editor tool is the dominant create/edit primitive across recent
# SWE-agent runs (claude-3.7+ / claude-4). `python <path>` and `pytest <path>`
# are the dominant ways the agent invokes its proxy test. [AI-M1.A]
RE_CREATE = re.compile(r"\bstr_replace_editor\s+create\s+(\S+)")
RE_STR_REPLACE = re.compile(r"\bstr_replace_editor\s+str_replace\s+(\S+)")
RE_INSERT = re.compile(r"\bstr_replace_editor\s+insert\s+(\S+)")
RE_PYTHON_RUN = re.compile(r"\bpython\d*\s+(?:-m\s+\S+\s+)?([^\s|;&]+\.py)\b")
RE_PYTEST_RUN = re.compile(r"\bpytest\s+([^\s|;&]+)")


def _norm(p: str) -> str:
    """Use basename as the canonical key for matching create/run/edit events.

    SWE-agent on SWE-bench creates files in /testbed/, runs them with both
    absolute (`python /testbed/foo.py`) and cwd-relative (`cd /testbed &&
    python foo.py`) forms. Basename matches across both. Multiple files
    with the same basename in different dirs would collide; this is rare in
    practice and acceptable for a profiling pass.
    """
    p = p.strip().rstrip(",").strip("'\"")
    return os.path.basename(p)


def _action_targets(action: str) -> list[tuple[str, str]]:
    """Return list of (op, normalized_path) tuples for one action string.

    op in {"create", "str_replace", "insert", "run"}. We allow multiple
    matches per action because some actions chain commands (e.g.
    `cd /testbed && python foo.py && cat foo.py`).
    """
    out: list[tuple[str, str]] = []
    for m in RE_CREATE.finditer(action):
        out.append(("create", _norm(m.group(1))))
    for m in RE_STR_REPLACE.finditer(action):
        out.append(("str_replace", _norm(m.group(1))))
    for m in RE_INSERT.finditer(action):
        out.append(("insert", _norm(m.group(1))))
    for m in RE_PYTHON_RUN.finditer(action):
        out.append(("run", _norm(m.group(1))))
    for m in RE_PYTEST_RUN.finditer(action):
        target = m.group(1).split("::", 1)[0]
        if target.endswith(".py"):
            out.append(("run", _norm(target)))
    return out


def _categorize(re_creates: int, str_replaces: int, inserts: int) -> str:
    n = re_creates + str_replaces + inserts
    if n == 0:
        return "single-write"
    if re_creates > 0 or n >= 4:
        return "rewritten"
    return "tweaked"


def profile_trajectory(traj_path: Path) -> dict[str, Any]:
    """Return a profile dict for one trajectory."""
    try:
        with open(traj_path) as f:
            data = json.load(f)
    except Exception as e:
        return {"instance_id": traj_path.stem, "error": f"{type(e).__name__}: {e}"}

    steps = data.get("trajectory", [])
    events_by_file: dict[str, list[tuple[int, str]]] = {}
    for i, step in enumerate(steps):
        action = str(step.get("action") or "")
        for op, path in _action_targets(action):
            events_by_file.setdefault(path, []).append((i, op))

    profiles: dict[str, dict[str, Any]] = {}
    for path, events in events_by_file.items():
        ops = [op for (_, op) in events]
        if "create" not in ops or "run" not in ops:
            continue
        first_create = next(i for (i, op) in events if op == "create")
        re_creates = sum(1 for (i, op) in events if i > first_create and op == "create")
        str_replaces = sum(1 for (i, op) in events if i > first_create and op == "str_replace")
        inserts = sum(1 for (i, op) in events if i > first_create and op == "insert")
        runs_after = sum(1 for (i, op) in events if i > first_create and op == "run")
        cat = _categorize(re_creates, str_replaces, inserts)
        profiles[path] = {
            "first_create_step": first_create,
            "re_creates": re_creates,
            "str_replaces": str_replaces,
            "inserts": inserts,
            "subsequent_edits": re_creates + str_replaces + inserts,
            "runs_after_create": runs_after,
            "category": cat,
        }

    primary: Optional[str] = None
    if profiles:
        primary = max(
            profiles.items(),
            key=lambda kv: (kv[1]["runs_after_create"], kv[1]["first_create_step"]),
        )[0]

    return {
        "instance_id": traj_path.stem,
        "n_steps": len(steps),
        "n_proxy_test_candidates": len(profiles),
        "primary_proxy_test": primary,
        "primary_category": profiles[primary]["category"] if primary else "no-proxy-test",
        "all_candidates": profiles,
    }


def _print_table(title: str, counts: Counter[str], total: int, ordered: list[str]) -> None:
    print(title)
    print("=" * 60)
    for cat in ordered:
        c = counts.get(cat, 0)
        pct = 100 * c / total if total else 0
        print(f"  {cat:18s}  {c:5d} ({pct:5.1f}%)")
    print(f"  {'total':18s}  {total:5d}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--trajs-dir", required=True, help="Directory of .traj files")
    ap.add_argument(
        "--output-json", default=None,
        help="Write per-trajectory profile detail to this path (JSON)",
    )
    args = ap.parse_args()

    traj_files = sorted(Path(args.trajs_dir).glob("*.traj"))
    print(f"Profiling {len(traj_files)} trajectories...", file=sys.stderr)
    profiles = [profile_trajectory(p) for p in traj_files]
    error_count = sum(1 for p in profiles if "error" in p)

    primary_counts: Counter[str] = Counter(
        p.get("primary_category", "error") for p in profiles if "error" not in p
    )
    n_clean = len(profiles) - error_count
    print()
    _print_table(
        "M1.A: primary proxy-test categorization (one per trajectory)",
        primary_counts, n_clean,
        ["single-write", "tweaked", "rewritten", "no-proxy-test"],
    )
    if error_count:
        print(f"  ({error_count} trajectories failed to load — see --output-json for details)")
        print()

    all_cats: list[str] = []
    for p in profiles:
        for info in p.get("all_candidates", {}).values():
            all_cats.append(info["category"])
    all_counts: Counter[str] = Counter(all_cats)
    _print_table(
        "M1.A: per-candidate categorization (every agent-created + run file)",
        all_counts, len(all_cats),
        ["single-write", "tweaked", "rewritten"],
    )

    safe_primary = primary_counts.get("single-write", 0) + primary_counts.get("tweaked", 0)
    print(
        f"=> {safe_primary}/{n_clean} ({100 * safe_primary / n_clean:.1f}%) of trajectories "
        f"have a primary proxy test that is single-write or tweaked.\n"
        f"   {primary_counts.get('rewritten', 0)} are rewritten "
        f"({primary_counts.get('no-proxy-test', 0)} have no agent-created+run file)."
    )

    if args.output_json:
        Path(args.output_json).write_text(json.dumps(profiles, indent=2))
        print(f"\nPer-trajectory detail written to {args.output_json}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
