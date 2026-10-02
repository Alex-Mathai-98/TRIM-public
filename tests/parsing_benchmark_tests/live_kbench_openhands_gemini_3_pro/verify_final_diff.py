#!/usr/bin/env python3
"""Final-diff check for live-kbench x OpenHands (Gemini 3 Pro) trajectories.

For every ``trajs/run_N/<bug_hash>/original/traj.json``: parse it with kernel
``traj_cli --agent openhands --only-parse``, apply the chosen patch list to a Linux clone at the
bug's ``parentOfFixCommit``, and require the resulting ``git diff`` to equal the agent's diff
(sibling ``patch.diff``) exactly, after ``normalize_diff``.

    python verify_final_diff.py --linux-dirs collection_of_linux_repos/linux-{502..560}

AIDEV-NOTE: copy of ``live_kbench_swe_agent_gemini_3_pro/verify_final_diff.py`` (z-cpl-45 Q3) — differs only
in agent name, run layout, diff source, skip list and ``_KNOWN_NOT_APPLICABLE``. Skip list
is the monorepo's ``test_edit_applicability_openhands.py::_SKIP_BUG_RUNS``.
NEVER pass linux-501 — it is the clean reference repo.
"""
from __future__ import annotations

import argparse
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

# AIDEV-NOTE: copied verbatim from the monorepo's test_edit_applicability_openhands.py.
_SKIP_BUG_RUNS: set[tuple[str, str]] = {
    # Parser gap #1 — run_ipython with Python file I/O as fallback editing
    # empty_candidate_runs (all edits via ipython, 0 successful action=edit)
    ("40ce46547eefc8c25c0cf21738f13a8d85a488d8", "run_3"),
    ("4a9156b1c9cb583347f044af0e047faf47714a3d", "run_3"),
    ("7eedce59d748e85a76ffd05468bd31c6074d990e", "run_3"),
    ("8590e8ff94024a32c1f081589f209974250bcb83", "run_1"),
    ("ade0cd4a2aa7b6d2ef501d6d46f35351d4496ce8", "run_1"),
    ("b18d5ca8417d1beb9ed07984b42c65756a0809cd", "run_2"),
    ("bb8973de0f232992a6351f66217742eee168f51c", "run_3"),
    ("d3900f87873289bd30b9b5accfccd913392f8c92", "run_3"),
    # diff_mismatch (ipython writes alongside parsed edits — diff incomplete)
    ("e8eda7c7b7a999684d5e57f1f949451e39f9cf51", "run_1"),
    ("4e17d55bbd3cfe45bab16873fb277084cfe749ef", "run_2"),
    ("c5418ebb2b54af3d4dd5db6958e70589905f6445", "run_2"),
    ("2388239bd70accc3407dca8392d4250fda22114e", "run_3"),
    ("aa2608e9b8079922323fe0a0a0a2621b5aff4952", "run_3"),
    # error→diff_mismatch (was error_run, now applies but diff incomplete)
    ("5474a82eaf76601109a3c1637253929840c25e4e", "run_2"),
    # OpenHands editor buffer not reset by git checkout/restore
    ("7f7dca2338925485d7e29f3b7cf2040140980388", "run_2"),
    ("d3900f87873289bd30b9b5accfccd913392f8c92", "run_1"),
    # Empty extras.diff + unparseable sed fallback (out-of-scope anomaly)
    ("1fa75cc7b4c09af1851a05db665290f8503103a6", "run_2"),
    # Multiline sed split by OpenHands into separate commands (observation="run"
    # but content has garbled output — sed never executed, file untouched)
    ("5349602aceca4df6a8ea675a6742185836f25b28", "run_1"),
    # Parser gap #2 — sed multi-command block: /pattern/{n;a\...\n}
    ("8adbfaca5e2e1bcf879d83d41be3bf4aa06dcc75", "run_2"),
}

# AIDEV-NOTE: accepted, not debugged (z-cpl-45 Q2) — exactly the 5 runs the monorepo also
# reports as not_applicable (no .generated.diff there). Still counted and printed; they don't
# fail the run.
_KNOWN_NOT_APPLICABLE: set[tuple[str, str]] = {
    ("3582619f2175815726ca9e50f5fae0afef5d2f30", "run_2"),
    ("5908492d151d9e8ea172fb1b1b3364eff1738c91", "run_2"),
    ("94334cd3869e2b0bdcbabb8e8d0e38e911d10130", "run_1"),
    ("948bb20d6e917093af5d7ce9b8cd32d824837020", "run_2"),
    ("fee812e6bf152e18c9530516e256dbc2304b019f", "run_1"),
}


def normalize_diff(diff: str) -> str:
    diff = _LINUX_DIR_RE.sub("", diff)
    diff = _RECEIPT_JSON_RE.sub("", diff)
    diff = _ROOT_LEVEL_FILE_RE.sub("", diff)
    diff = _ORIG_FILE_RE.sub("", diff)
    diff = _BAK_FILE_RE.sub("", diff)
    return diff


def run_id(traj_path: Path) -> tuple[str, str]:
    """``(bug_hash, run_N)`` for a ``run_N/<bug_hash>/original/traj.json`` path."""
    return traj_path.parents[1].name, traj_path.parents[2].name


def check_run(run_path: Path, repo_mgr: RepoPatchManager, lock: RWLock) -> str:
    """Return the status of one run: ok / empty / not_applicable / diff_mismatch."""
    parsed = traj_cli.main([
        "--agent", "openhands", "--traj", str(run_path),
        "--bug-id", run_id(run_path)[0], "--only-parse",
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

    submission = (run_path.parent / "patch.diff").read_text(encoding="utf-8")
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
            run_id(run)[1]: "skipped" if run_id(run) in _SKIP_BUG_RUNS
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
    for run in sorted(TRAJS_DIR.glob("run_*/*/original/traj.json")):
        bug = run_id(run)[0]
        if args.bugs is None or bug in args.bugs:
            runs_by_bug[bug].append(run)

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
                per_run = {run_id(run)[1]: "error" for run in runs_by_bug[bug]}
                print(f"  {bug}: ERROR\n{traceback.format_exc()}", file=sys.stderr)
            for stem, status in per_run.items():
                statuses[f"{bug}/{stem}"] = status
                counts[status] += 1
            print(f"  [{i}/{len(futures)}] {bug}: "
                  + " ".join(f"{s}={per_run[s]}" for s in sorted(per_run)))

    print(f"\nfinal diff: total={sum(counts.values())} " + " ".join(
        f"{k}={counts[k]}" for k in ("ok", "empty", "skipped", "diff_mismatch",
                                     "not_applicable", "error")))
    known = sorted(f"{b}/{r}" for b, r in _KNOWN_NOT_APPLICABLE
                   if statuses.get(f"{b}/{r}") == "not_applicable")
    if known:
        print(f"known not_applicable (accepted, {len(known)}):", " ".join(known))
    bad = sorted(k for k, s in statuses.items()
                 if s in ("diff_mismatch", "not_applicable", "error") and k not in known)
    if bad:
        print("failing runs:", " ".join(bad), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
