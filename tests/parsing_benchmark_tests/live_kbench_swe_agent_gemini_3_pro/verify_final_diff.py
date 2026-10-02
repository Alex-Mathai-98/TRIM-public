#!/usr/bin/env python3
"""Final-diff check for live-kbench x SWE-agent trajectories.

For every ``trajs/<bug_hash>/run_N.json``: parse it with kernel ``traj_cli --only-parse``,
apply the chosen patch list to a Linux clone at the bug's ``parentOfFixCommit``, and require
the resulting ``git diff`` to equal the agent's submitted diff (``info.submission``) exactly,
after ``normalize_diff``.

    python verify_final_diff.py --linux-dirs collection_of_linux_repos/linux-{502..560}

AIDEV-NOTE: trimmed port of the monorepo's ``test_edit_applicability.py`` (z-cpl-44 Q2): same
pass/fail rule, same ``normalize_diff``, same ``_SKIP_BUG_RUNS``; no mismatch classification
or ``.generated.diff`` dumps. NEVER pass linux-501 — it is the clean reference repo.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from queue import Queue

from patch_minimizer.benchmark_utils.kernel.clean_and_update_all_linux_repos import (
    clean_single_repo,
)
from patch_minimizer.core.rw_lock import RWLock
from patch_minimizer.core.utils import Utils
from patch_minimizer.frontends.kernel.cli import traj_cli
from patch_minimizer.frontends.kernel.cli.common import load_bug_data
from patch_minimizer.strategies.repo_patch_manager import RepoPatchManager

HERE = Path(__file__).resolve().parent
TRAJS_DIR = HERE / "trajs"
BUG_DATA_DIR = HERE.parent / "live_kbench_common" / "bug_data"

logger = logging.getLogger(__name__)

# AIDEV-NOTE: copied verbatim from the monorepo's edit_applicability_utils.py.
# Root-level files (no directory component) are agent-created artifacts
# (receipt.json, offset_check.c, validation.log, ...); kernel source always lives in a subdir.
_LINUX_DIR_RE = re.compile(r"collection_of_linux_repos/linux-[A-Za-z0-9_-]+/")
_RECEIPT_JSON_RE = re.compile(
    r"diff --git a/receipt\.json b/receipt\.json\n.*?(?=diff --git |\Z)",
    re.DOTALL,
)
_ROOT_LEVEL_FILE_RE = re.compile(
    r"diff --git a/([^\s/]+) b/\1\n.*?(?=diff --git |\Z)",
    re.DOTALL,
)
_ORIG_FILE_RE = re.compile(
    r"diff --git a/\S+\.(?:orig|rej) b/\S+\.(?:orig|rej)\n.*?(?=diff --git |\Z)",
    re.DOTALL,
)
_BAK_FILE_RE = re.compile(
    r"diff --git a/(\S+\.bak) b/\1\n.*?(?=diff --git |\Z)",
    re.DOTALL,
)

# AIDEV-NOTE: copied verbatim from the monorepo's test_edit_applicability.py — known docker
# anomalies where the run left state on disk that no parsed Edit can reproduce.
_SKIP_BUG_RUNS: set[tuple[str, str]] = {
    ("039bf53503778d3401b657cb99a68e5803f69eae", "run_1"),
    ("04af4753ce673298938371169375282f90690283", "run_1"),
    ("4524c18102bf43fb350830e4efcb16620335a35c", "run_1"),
    ("4a9156b1c9cb583347f044af0e047faf47714a3d", "run_3"),
    ("ad934134fa4dbc4df3901c3c737af2e0a6bf77f6", "run_3"),
    ("be63bb59ce1661d46dc6951b5bfd151a7d0cab90", "run_2"),
    ("2fe12e3265f82d1c12bf65107ab45e6f2678128f", "run_1"),
    ("66f3dd148e81e6160cef2d2ca37c1f5eb1a97f0f", "run_3"),
    ("c74a5c4bc64af20f9621a9b19ac2d073cec60dde", "run_1"),
    ("e0ea1d9288d0cf8017c7fefa745690b4dc5c329f", "run_2"),
    ("ed373ea253116a7a9b09588e4f5820dbfbfdd4a9", "run_2"),
    ("f7456ae08448638df3766b712aebb2a825fc9d76", "run_2"),
    ("87240ca76039c38dbe8e773c3a63c871b6399d48", "run_3"),
    ("edc431200af523909e1f15cb14ca4a5316a87e79", "run_1"),
    ("1e8afd76570cff2b1012b27bf042286cab3ecafa", "run_1"),
}


def normalize_diff(diff: str) -> str:
    diff = _LINUX_DIR_RE.sub("", diff)
    diff = _RECEIPT_JSON_RE.sub("", diff)
    diff = _ROOT_LEVEL_FILE_RE.sub("", diff)
    diff = _ORIG_FILE_RE.sub("", diff)
    diff = _BAK_FILE_RE.sub("", diff)
    return diff


def check_run(run_path: Path, repo_mgr: RepoPatchManager, lock: RWLock) -> str:
    """Return the status of one run: ok / empty / not_applicable / diff_mismatch."""
    parsed = traj_cli.main([
        "--agent", "swe-agent", "--traj", str(run_path),
        "--bug-id", run_path.parent.name, "--only-parse",
    ])
    if parsed is None:
        return "empty"
    patch_range = [edits for _, edits in parsed["patch_list"]]

    with lock.w_locked_nowait():
        applied = repo_mgr.check_patch_range(
            None, patch_range, leave_patches=True, use_lock=False,
        )
        generated = repo_mgr.generate_git_diff() if applied else None
        repo_mgr.clean_repo(use_lock=False)
    if not applied:
        return "not_applicable"

    with open(run_path, encoding="utf-8") as f:
        submission = json.load(f).get("info", {}).get("submission") or ""
    same = normalize_diff(generated).strip() == normalize_diff(submission).strip()
    return "ok" if same else "diff_mismatch"


def check_bug(bug: str, runs: list[Path], repo_queue: Queue) -> dict[str, str]:
    """Check all runs of one bug on one borrowed Linux clone. Returns {run_stem: status}."""
    linux_dir, lock = repo_queue.get()
    try:
        base_commit, kernel_base_url = load_bug_data(str(BUG_DATA_DIR), bug)
        # AIDEV-NOTE: checkout can fail on concurrent git fetches; retry like the monorepo.
        for attempt in range(3):
            try:
                repo_mgr = RepoPatchManager(
                    linux_dir, lock, base_commit, logger, kernel_base_url=kernel_base_url,
                )
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))
        return {
            run.stem: "skipped" if (bug, run.stem) in _SKIP_BUG_RUNS
            else check_run(run, repo_mgr, lock)
            for run in runs
        }
    finally:
        repo_queue.put((linux_dir, lock))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--linux-dirs", nargs="+", required=True,
                    help="Linux clones to use in parallel (never linux-501).")
    ap.add_argument("--bugs", nargs="+", default=None,
                    help="Only these bug hashes (debugging).")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    linux_dirs = [str(Path(d).absolute()) for d in args.linux_dirs]
    if any(Path(d).name == "linux-501" for d in linux_dirs):
        sys.exit("linux-501 is the clean reference repo; do not use it for tests.")

    runs_by_bug: dict[str, list[Path]] = defaultdict(list)
    for run in sorted(TRAJS_DIR.glob("*/run_*.json")):
        if args.bugs is None or run.parent.name in args.bugs:
            runs_by_bug[run.parent.name].append(run)

    print(f"Cleaning {len(linux_dirs)} linux repos ...")
    utils = Utils(logger)
    with ThreadPoolExecutor(max_workers=len(linux_dirs)) as pool:
        failed = [d for d, ok in zip(linux_dirs, pool.map(
            lambda d: clean_single_repo(utils, d), linux_dirs)) if not ok]
    if failed:
        sys.exit(f"failed to clean: {failed}")

    repo_queue: Queue = Queue()
    for d in linux_dirs:
        repo_queue.put((d, RWLock()))

    statuses: dict[str, str] = {}
    counts: Counter = Counter()
    with ThreadPoolExecutor(max_workers=len(linux_dirs)) as pool:
        futures = {pool.submit(check_bug, bug, runs, repo_queue): bug
                   for bug, runs in runs_by_bug.items()}
        for i, fut in enumerate(as_completed(futures), 1):
            bug = futures[fut]
            # AIDEV-NOTE: the only error boundary — one bug's failure (e.g. checkout) is
            # reported with its traceback and fails the run, without killing the other bugs.
            try:
                per_run = fut.result()
            except Exception:
                per_run = {run.stem: "error" for run in runs_by_bug[bug]}
                print(f"  {bug}: ERROR\n{traceback.format_exc()}", file=sys.stderr)
            for stem, status in per_run.items():
                statuses[f"{bug}/{stem}"] = status
                counts[status] += 1
            print(f"  [{i}/{len(futures)}] {bug}: "
                  + " ".join(f"{s}={per_run[s]}" for s in sorted(per_run)))

    print(f"\nfinal diff: total={sum(counts.values())} " + " ".join(
        f"{k}={counts[k]}" for k in ("ok", "empty", "skipped", "diff_mismatch",
                                     "not_applicable", "error")))
    bad = sorted(k for k, s in statuses.items()
                 if s in ("diff_mismatch", "not_applicable", "error"))
    if bad:
        print("failing runs:", " ".join(bad), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
