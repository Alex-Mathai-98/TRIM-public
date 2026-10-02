#!/usr/bin/env python3
"""Final-diff check for live-kbench x mini-swe-agent (Claude Opus 4.5) trajectories.

For every ``trajs/run_N/<bug_hash>/original/traj.json``: parse it with kernel
``traj_cli --agent mini-swe-agent --only-parse``, apply the chosen patch list to a Linux
clone at the bug's ``parentOfFixCommit``, and require the resulting ``git diff`` to equal
the agent's diff (sibling ``patch.diff``) exactly, after ``normalize_diff``.

    python verify_final_diff.py --linux-dirs collection_of_linux_repos/linux-{502..560}

AIDEV-NOTE: copy of ``live_kbench_openhands_gemini_3_pro/verify_final_diff.py`` (z-cpl-46):
same layout and ``patch.diff`` source; differs only in agent name, skip list and
``_KNOWN_NOT_APPLICABLE``. The skip list is the monorepo's
``test_edit_applicability_mini_swe_claude.py::_SKIP_BUG_RUNS``.
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

# AIDEV-NOTE: copied verbatim from the monorepo's test_edit_applicability_mini_swe_claude.py.
_SKIP_BUG_RUNS: set[tuple[str, str]] = {
    # Section 1 — Python file-writing scripts (17 bugs)
    ("2e389a5a110ab1b51c75694b8f71e89843f3dbe8", "run_1"),
    ("4a9156b1c9cb583347f044af0e047faf47714a3d", "run_1"),
    ("656b97dc331b9210d8b13cb7769e6885f9751fbf", "run_1"),
    ("e8e79bc8860e04746e75319f57c75a99ccb09e4f", "run_1"),
    ("1df0435f7a67907ac17c2e1da0d502b7897a8e5c", "run_1"),
    ("30082ca4e5f5935e81f35f2002045aaf87913a29", "run_1"),
    ("349e89baaeba44971b6454aa787c27cad5da1873", "run_1"),
    ("5349602aceca4df6a8ea675a6742185836f25b28", "run_1"),
    ("737b39836a0d31c3f6a7fb99b6b2225c2cbb5fac", "run_1"),
    ("7eedce59d748e85a76ffd05468bd31c6074d990e", "run_1"),
    ("0d8a851193b2df554779f74bdd75e1108ea3fd91", "run_1"),
    ("5108d1b6b512840d5f3ae80b09391548b7f5562f", "run_1"),
    ("944142187051024571c051eb86ffa76e6b4cb603", "run_1"),
    ("d032fa1fac0054b4a7175676d8860731162f4a68", "run_1"),
    ("85d680b6c68ae38fa006890f8f0cbf2633dabe4c", "run_1"),
    ("8ff0e85cd87a36c3d1da183c0fa124e47c5f28d5", "run_1"),
    ("cc32e291ddff2d0fc3a31050a2018b8d58d3d179", "run_1"),
    # Section 2 — Temp-file patch/script execution (25 bugs)
    ("88036fcf5ad742263d2bda054db3ddd5fa9648ad", "run_1"),
    ("a5b53d136cd6af3a56bdf319cfc973060027d9af", "run_1"),
    ("cc55e6f44ee6505b65c6cdca450fdcc3cc95151c", "run_1"),
    ("04af4753ce673298938371169375282f90690283", "run_1"),
    ("19087725c25cfb6d9a2650e592be0454c5ee10d9", "run_1"),
    ("1fa75cc7b4c09af1851a05db665290f8503103a6", "run_1"),
    ("2022e6350b785adab5572af570f7c3489a93cc52", "run_1"),
    ("54744c648f8404e3153da5da12482b4c70259f96", "run_1"),
    ("576c3908d9a4ea5c58a2b8e26f5fe7e8875a122c", "run_1"),
    ("5b254d69e403398d3a56ae4ab652f870a74cad61", "run_1"),
    ("764af1a679bc60c31e82cfaef246e88a01cf1cab", "run_1"),
    ("79cdf42e3c27b74190133da33b09694bcd6412dc", "run_1"),
    ("8129669e82081e24e67827fe3af257171c4fc626", "run_1"),
    ("a9807a63ad004f4a67c8d2d4e91e28a53b62f8eb", "run_1"),
    ("c336730ad7d408eeece8c37b2302be7d9dcf9c5c", "run_1"),
    ("d696252073981a083f85190be40c080063b73115", "run_1"),
    ("dcfbf59913ad505145968e89d3e4fdb58b2bdbec", "run_1"),
    ("e1b5cfc4d5f351d479d66e3c91ddf926d503b1ef", "run_1"),
    ("8adbfaca5e2e1bcf879d83d41be3bf4aa06dcc75", "run_1"),
    ("1d42c60fdccc13544c1bf8dab956c1f22910fb85", "run_1"),
    ("1eaa553d8a82de59255e3a9574d193885c6dadd4", "run_1"),
    ("3cb391a147cbb33edfeb4c802ca5099a685187d8", "run_1"),
    ("5660d54574d0a993317c7b4186f0172d0dcd3a18", "run_1"),
    ("6d4433661fa0d77403cbb343ea7e75c7ef2c8c6c", "run_1"),
    ("87240ca76039c38dbe8e773c3a63c871b6399d48", "run_1"),
    # Section 3 — Head/tail splice via temp files (15 bugs)
    ("b552fd21dcc819daf6e93017f72ee98cda4d978e", "run_1"),
    ("bef91d61cac414bf1191d4574ab34f041555d7dc", "run_1"),
    ("13ac69aefa01798532cbc878f4e404a7d15139eb", "run_1"),
    ("4946dc7ac24f5406d4686f5c7436725fff5b1f02", "run_1"),
    ("4f34adc84f4a3b080187c390eeef60611fd450e1", "run_1"),
    ("685e9d95fc4ea8f99eb80136422c7251f523cef4", "run_1"),
    ("1cfa39c0e28cf4d15012461b4b5646e43675282d", "run_1"),
    ("e233072dd1479e3b68f903d9f42949348b5839ea", "run_1"),
    ("510ec23753fb57699effc40981edd11120f3aff9", "run_1"),
    ("a61580535d56759dd0f4c49b97acdeb70f0844bf", "run_1"),
    ("06f06bab8820fbd2881e4e41235dd0e2be123ef7", "run_1"),
    ("25a9bc64acdc9738045c7c62f84e1ff4aecc549e", "run_1"),
    ("274508d9032a588fbe54ec4e0291880b07e4ef47", "run_1"),
    ("cb6ff9001f08cbb496165a21d9e28063afb4f690", "run_1"),
    ("d47fcea929219ef53b5b0f700f2102bed78b6202", "run_1"),
    # Known parser gap — sed curly-brace-grouped command {/pattern/a\text} (2 bugs)
    ("1c0bea493ac82d5f3642dd27710c4492463efa83", "run_1"),
    ("c99d4efc9242e53bd055011543dd487c83cb8e52", "run_1"),
    # Known parser gap — sed r (read file) command (2 bugs)
    ("3af8cab827896e644e2b769b40d7fc2595aeff63", "run_1"),
    ("c5418ebb2b54af3d4dd5db6958e70589905f6445", "run_1"),
}

# AIDEV-NOTE: accepted, not debugged (z-cpl-45 Q2 pattern). Empty: the monorepo reports no
# not_applicable runs for mini-swe Claude.
_KNOWN_NOT_APPLICABLE: set[tuple[str, str]] = set()


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
        "--agent", "mini-swe-agent", "--traj", str(run_path),
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
