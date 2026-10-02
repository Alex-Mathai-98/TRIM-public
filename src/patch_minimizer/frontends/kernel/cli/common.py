"""Shared helpers for kernel minimization CLIs.

AIDEV-NOTE: Extracted from example_code/run_swe_minimization.py — these helpers
are agent-agnostic infrastructure (bug data loading, golden subset parsing,
summary management, repo pooling) reused by traj_cli.py and the example_code/ runners.

AIDEV-NOTE: The bug_config chain (minimize_one_bug, minimize_and_save,
run_parallel_with_queue, run_parallel) and the count-based check_summary_consistency were
removed. All three trajectory agents now reach minimize_edits through one
route: minimize_one_traj -> traj_cli. Resume/orphan checks use check_summary_orphans.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path
from queue import Queue
from typing import Tuple

from patch_minimizer.core.rw_lock import RWLock
from patch_minimizer.strategies.feedback_stats import _summarize_feedback_stats
from patch_minimizer.strategies.minimize_core import _save_summary_atomic


# ---------------------------------------------------------------------------
# Bug data + golden subset loading
# ---------------------------------------------------------------------------

def load_bug_data(benchmark_folder: str, bug_id: str) -> Tuple[str, str]:
    """Load base_commit and kernel_base_url from bug JSON (tries ``hash_0.json`` if needed)."""
    bug_json_path = os.path.join(benchmark_folder, f"{bug_id}.json")
    if not os.path.isfile(bug_json_path) and not bug_id.endswith("_0"):
        alt = os.path.join(benchmark_folder, f"{bug_id}_0.json")
        if os.path.isfile(alt):
            bug_json_path = alt
    with open(bug_json_path) as f:
        data = json.load(f)
    base_commit = data["parentOfFixCommit"]
    if not base_commit:
        raise ValueError(f"Bug {bug_id} has no parentOfFixCommit in {bug_json_path}")
    crashes = data["crashes"]
    if isinstance(crashes, str):
        crashes = json.loads(crashes)
    kernel_base_url = crashes[0].get("kernelSourceGit", crashes[0].get("kernel-source-git"))
    return base_commit, kernel_base_url


def load_golden_subset(path: str) -> dict:
    """Load golden_subset.json; supports bugs-array or dict keyed by bug_id."""
    with open(path) as f:
        raw = json.load(f)
    if "bugs" in raw and isinstance(raw["bugs"], list):
        return {
            entry["id"]: {"image": entry["image"], "kcache": entry.get("kcache")}
            for entry in raw["bugs"]
        }
    return raw


# ---------------------------------------------------------------------------
# Summary management (resume-safe incremental writes)
# ---------------------------------------------------------------------------

def result_folder_name(result: dict) -> str:
    """Stable ``folder`` string for ``successful_bugs``."""
    pid = result.get("pass_id") or "unknown_pass"
    ci = result.get("candidate_index", 0)
    return f"{pid}/swe_candidate_{ci}"


def load_existing_summary(summary_path: Path) -> tuple[dict, list, list, set]:
    """Resume-aware loading — pre-populate summary_state from existing file."""
    if not summary_path.is_file():
        return {}, [], [], set()
    with open(summary_path) as f:
        data = json.load(f)
    aggregated = data.get("aggregated_results", {})
    successful = [
        (b["bug_id"], b["folder"], b.get("best_reduction"), b.get("feedback_stats"))
        for b in data.get("successful_bugs", [])
    ]
    failed = [
        (b["bug_id"], b["folder"])
        for b in data.get("failed_bugs", [])
    ]
    # AIDEV-NOTE: Only mark successful folders as seen — failed folders must remain
    # retryable so they can transition to successful on re-run.
    seen = {s[1] for s in successful}
    return aggregated, successful, failed, seen


def append_and_save(result, summary_state, bug_configs=None, key=None):
    """Incremental summary write after each bug."""
    agg = summary_state["aggregated"]
    succ = summary_state["successful"]
    fail = summary_state["failed"]
    seen = summary_state.get("seen", set())
    if result:
        folder = result_folder_name(result)
        if folder in seen:
            return
        seen.add(folder)
        # AIDEV-NOTE: Remove from failed list if this was a retry that succeeded
        fail[:] = [f for f in fail if f[1] != folder]
        red = result.get("reduction")
        if red:
            agg[red] = agg.get(red, 0) + 1
        fs = result.get("feedback_stats")
        # AIDEV-NOTE: Skip if already structured (cached results)
        if fs and "total_feedbacks" not in fs:
            fs = _summarize_feedback_stats(dict(fs))
        succ.append((result["bug_id"], folder, red, fs or None))
    elif bug_configs and key:
        cfg = next(c for c in bug_configs if c.get("result_key", c["bug_id"]) == key)
        folder = f"{cfg['pass_id']}/swe_candidate_{cfg.get('candidate_index', 0)}"
        if folder in seen:
            return
        seen.add(folder)
        fail[:] = [f for f in fail if f[1] != folder]
        fail.append((cfg["bug_id"], folder))
    _save_summary_atomic(summary_state["path"], agg, succ, fail, summary_state["config"])


def check_summary_orphans(discovered_bug_ids: set[str], summary_state: dict) -> None:
    """Warn when the summary holds rows for work this run will not do.

    AIDEV-NOTE: Identity-based, NOT count-based — and that difference is load-bearing.
    The traj flow parses lazily inside traj_cli, so the number of edit-bearing candidates
    is unknown upfront; comparing len(candidates) against summary rows false-positives on
    every candidate that turns out to have no edits (such a candidate never produces a row),
    and since this prompts on stdin that would hang an unattended resume. The summary
    already records *which* work it did, so compare identities instead.

    Only the shrink direction is checked. `recorded - discovered` is unambiguous: the summary
    holds work this run will not do, so the inputs really did change. The reverse conflates
    "new candidate", "candidate has no edits" and "not processed yet", and growth is benign
    anyway since resume skips completed work.

    Join on the folder prefix (pass_id), never row[0] (bug_id): the old builder mutated
    bug_id to ``{bug_id}_0`` when the benchmark JSON used that name, while pass_id never was,
    so row[0] would spuriously orphan every _0 bug in a summary written by the old code.
    """
    rows = list(summary_state["successful"]) + list(summary_state["failed"])
    if not rows:
        return
    orphaned = {row[1].split("/")[0] for row in rows} - discovered_bug_ids
    if not orphaned:
        return

    shown = sorted(orphaned)
    preview = ", ".join(shown[:5]) + (f" (+{len(shown) - 5} more)" if len(shown) > 5 else "")
    print(
        f"\nWARNING: the summary has {len(orphaned)} bug(s) that are not in this run's "
        f"inputs:\n  {preview}\nThe input contents or --limit changed between runs. "
        f"Continuing will blend both runs' results in the summary."
    )
    print("  [c] Continue — keep existing results, skip already-processed bugs")
    print("  [o] Override — clear existing summary and start fresh")
    print("  [a] Abort")
    answer = input("Choose [c/o/a]: ").strip().lower()
    if answer == "o":
        summary_state["successful"].clear()
        summary_state["failed"].clear()
        summary_state["aggregated"].clear()
        summary_state["seen"].clear()
        print("Existing summary cleared. Starting fresh.\n")
    elif answer == "c":
        print("Continuing with existing results. Already-processed bugs will be skipped.\n")
    else:
        print("Aborting.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Single-trajectory minimization worker (wraps traj_cli)
# ---------------------------------------------------------------------------

def minimize_one_traj(
    agent: str,
    traj_json: str,
    bug_id: str,
    linux_dir_queue: Queue,
    shared_config: dict,
    save_dir: str | None = None,
) -> dict | None:
    """Acquire a repo from the queue, call traj_cli.main(), return the repo.

    Returns the result dict from traj_cli, or None on failure. Already-minimized
    bugs are returned from disk without re-running.
    """
    # AIDEV-NOTE: Resume check runs BEFORE acquiring a repo, so a skipped bug never
    # occupies a pool slot. Without it a resumed run redoes every bug (real kGym builds
    # under run_jobs) and append_and_save then drops the result once the folder is in
    # `seen`. traj_cli stays stateless by design — resume is orchestration's job.
    if save_dir:
        existing = os.path.join(save_dir, "minimization_results.json")
        if os.path.isfile(existing):
            with open(existing) as f:
                result = json.load(f)
            print(f"  Skipping already processed: {bug_id}")
            return result

    linux_dir, code_dir_lock = linux_dir_queue.get()
    try:
        argv = [
            "--agent", agent,
            "--traj", traj_json,
            "--bug-id", bug_id,
            "--benchmark-folder", shared_config["benchmark_folder"],
            "--repo-dir", linux_dir,
            "--minimize-at", shared_config.get("minimize_at", "edit"),
            "--syzkaller-rollback-tag", shared_config.get("syzkaller_rollback_tag", "master"),
        ]
        if shared_config.get("run_jobs"):
            argv.append("--run-jobs")
        if save_dir:
            argv.extend(["--save-dir", save_dir])
        from patch_minimizer.frontends.kernel.cli.traj_cli import main as traj_main
        return traj_main(argv, code_dir_lock=code_dir_lock)
    except Exception as e:
        print(f"Error minimizing {bug_id} from {traj_json}: {e}")
        traceback.print_exc()
        return None
    finally:
        # AIDEV-NOTE: RWLock is non-reentrant and the minimizer acquires via w_locked_nowait(),
        # which fails fast. A lock returned to the pool still held poisons that repo for the rest
        # of the run — the next worker dies in RepoPatchManager.__init__ before doing any work.
        # w_release() (not the raw w_lock.release()) also clears _w_lock_owner; leaving it stale
        # makes a reused ThreadPoolExecutor thread hit a false "deadlock detected" later.
        if code_dir_lock.is_w_locked():
            print(f"Force-released write lock for {linux_dir}")
            code_dir_lock.w_release()
        linux_dir_queue.put((linux_dir, code_dir_lock))


# ---------------------------------------------------------------------------
# Linux repo pool helpers
# ---------------------------------------------------------------------------

def linux_dir_queue_from_paths(paths: list[str]) -> Queue:
    """Build queue from explicit repo paths."""
    linux_dir_queue: Queue = Queue()
    for p in paths:
        linux_dir_queue.put((p, RWLock()))
    return linux_dir_queue


def parse_linux_index_from_path(linux_dir: str) -> int | None:
    """Extract the numeric index from a path like ``…/linux-650``."""
    m = re.search(r"linux-(\d+)\s*$", linux_dir.rstrip("/").replace("\\", "/"))
    return int(m.group(1)) if m else None


def build_linux_pool_paths(
    linux_dirs: list[str] | None,
    linux_dir_start: int | None,
    linux_dir_end: int | None,
    linux_dir: str,
    num_parallel: int,
    base_path: str,
) -> list[str]:
    """Resolve linux repo pool paths from CLI args.

    Supports three modes:
    - Explicit ``--linux-dirs`` list
    - ``--linux-dir-start`` / ``--linux-dir-end`` range
    - Inferred range from ``--linux-dir`` index
    """
    if num_parallel <= 1:
        return [linux_dir]

    if linux_dirs:
        pool_paths = linux_dirs[:num_parallel]
        if len(pool_paths) < num_parallel:
            print(
                f"Error: --linux-dirs must provide at least {num_parallel} paths.",
                file=sys.stderr,
            )
            sys.exit(1)
        # AIDEV-NOTE: Repo exclusivity comes from the Queue (one worker per entry), not from
        # the per-entry RWLock — linux_dir_queue_from_paths() mints a fresh lock per entry, so
        # duplicate paths would hand two workers the same working tree with different locks.
        # Only this branch takes user-supplied paths; the range branch is unique by construction.
        if len(set(pool_paths)) != len(pool_paths):
            dupes = sorted({p for p in pool_paths if pool_paths.count(p) > 1})
            print(
                f"Error: --linux-dirs must be unique (each worker needs its own repo). "
                f"Duplicated: {', '.join(dupes)}",
                file=sys.stderr,
            )
            sys.exit(1)
        for p in pool_paths:
            if not os.path.isdir(p):
                print(f"Error: linux dir not found: {p}", file=sys.stderr)
                sys.exit(1)
        return pool_paths

    start = linux_dir_start
    end = linux_dir_end
    if start is None or end is None:
        idx = parse_linux_index_from_path(linux_dir)
        if idx is None:
            print(
                "Error: parallel mode needs --linux-dirs or --linux-dir-start/--linux-dir-end.",
                file=sys.stderr,
            )
            sys.exit(1)
        start = idx
        end = idx + num_parallel - 1
    indices = list(range(start, end + 1))
    if len(indices) < num_parallel:
        print(
            f"Error: linux index range must span at least {num_parallel} repos "
            f"(got {len(indices)} from {start}..{end}).",
            file=sys.stderr,
        )
        sys.exit(1)
    pool_paths = [
        os.path.join(base_path, "collection_of_linux_repos", f"linux-{i}")
        for i in indices[:num_parallel]
    ]
    for p in pool_paths:
        if not os.path.isdir(p):
            print(f"Error: linux dir not found: {p}", file=sys.stderr)
            sys.exit(1)
    return pool_paths
