#!/usr/bin/env python3
"""Pipeline step 2 — minimize a directory of SWE-bench trajectories, in parallel.

    run_swebench_minimization.py --traj-dir <dir> \\
        --save-dir z-validate-swebench-dataset/minimization_333 --num-parallel 8

Optionally chain the later steps (step 4, consolidate, is not chainable — it needs
hand-authored reproductions in between):

    ... --then-postprocess --then-reconstruct

ONE instance is a different entry point — this script is batch-only:

    python -m patch_minimizer.frontends.swebench.cli traj \\
        --agent swe-agent \\
        --traj sympy__sympy-13372.traj --bug-id sympy__sympy-13372 \\
        --save-dir /tmp/min_one

AIDEV-NOTE: The minimization itself lives in cli/traj_cli.py. What remains here is
discovery, the clone pool, threading, resume and chaining — no minimization logic.

Outputs per instance: <save-dir>/<instance_id>/{minimization_results.json,
run.log, standalone__<instance_id>/minimized.patch}. Multi-mode also writes
<save-dir>/minimization_summary.json (atomic, resume-safe) and a top-level
dispatcher log.

Concurrency: each repo has a pool of ``--clones-per-repo`` (K) independent clones
(``<owner>__<repo>``, ``<owner>__<repo>__copy1`` ...). An instance holds one clone for
its whole ``minimize_edit_list`` call, because that clone's HEAD must stay at its
base_commit, so up to K instances per repo run at once. Jobs are interleaved across
repos; set ``--num-parallel`` to about 12*K.

Requires the SWE-bench instance image (built via
``swebench.harness.prepare_images``) and a clone at
``<PROJECT_ROOT>/results/swe-bench-workspace/<owner>__<repo>/``.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from itertools import zip_longest
from pathlib import Path
from queue import Queue


# AIDEV-NOTE: bootstrap_env() runs before the heavy package imports below (hence E402) —
# it sets KAGENT_PATH / BASE_PATH that those modules read at import time.
from patch_minimizer.frontends.swebench.utils.env import bootstrap_env  # noqa: E402

bootstrap_env()

from patch_minimizer.core.rw_lock import RWLock  # noqa: E402
from patch_minimizer.strategies.minimize_core import _save_summary_atomic  # noqa: E402
from patch_minimizer.frontends.swebench.utils.hf_dataset import base_commits_for  # noqa: E402
from patch_minimizer.frontends.swebench.utils.instance_ids import (  # noqa: E402
    instance_to_repo_prefix,
)
from patch_minimizer.frontends.swebench.cli.common import (  # noqa: E402
    close_instance_logger,
    instance_logger as make_instance_logger,
    minimize_one_traj,
    parse_instance_filter,
    setup_dispatcher_logging,
)


# AIDEV-NOTE: Clone pool. Same shape as kernel's linux_dir_queue: one Queue of
# (clone path, RWLock) per repo. Copies are `git clone --local` (a real .git dir), NOT
# worktrees — sweep_stale_git_locks (core/utils.py:79) expects `<clone>/.git/` to be a dir.
def _build_clone_pools(
    instance_ids: list[str], workspace_root: str, clones_per_repo: int,
) -> dict[str, Queue]:
    """Copy 0 is the existing clone; copies 1..K-1 are created if missing. Never more
    copies than the repo has instances."""
    counts = Counter(instance_to_repo_prefix(iid) for iid in instance_ids)
    pools: dict[str, Queue] = {}
    for prefix, n in counts.items():
        original = os.path.join(workspace_root, prefix)
        pools[prefix] = Queue()
        for i in range(min(clones_per_repo, n)):
            path = original if i == 0 else f"{original}__copy{i}"
            if not os.path.isdir(os.path.join(path, ".git")):
                logging.info("Creating clone %s", path)
                subprocess.run(
                    ["git", "clone", "--local", "--quiet", original, path], check=True,
                )
            pools[prefix].put((path, RWLock()))
    return pools


def _interleave_by_repo(jobs: list[tuple[Path, str]]) -> list[tuple[Path, str]]:
    """Round-robin across repos, largest first, so every repo is busy from the start
    instead of the pool's window being all one repo (alphabetical order)."""
    by_repo: dict[str, list] = defaultdict(list)
    for traj, iid in jobs:
        by_repo[instance_to_repo_prefix(iid)].append((traj, iid))
    groups = sorted(by_repo.values(), key=len, reverse=True)
    return [job for batch in zip_longest(*groups) for job in batch if job is not None]


class _SummaryState:
    """Thread-safe accumulator that flushes ``minimization_summary.json``
    atomically after every update."""

    def __init__(self, summary_path: Path, config: dict) -> None:
        self.summary_path = summary_path
        self.config = config
        self.lock = threading.Lock()
        self.aggregated: dict[str, int] = {}
        self.successful: list = []
        self.failed: list = []
        self.seen: set[str] = set()
        if summary_path.exists():
            try:
                with open(summary_path) as f:
                    prior = json.load(f)
                self.aggregated = dict(prior.get("aggregated_results", {}))
                for entry in prior.get("successful_bugs", []):
                    self.successful.append((
                        entry["bug_id"],
                        entry.get("folder", ""),
                        entry.get("best_reduction", "0_to_0"),
                        entry.get("feedback_stats", {}),
                    ))
                    self.seen.add(entry["bug_id"])
                for entry in prior.get("failed_bugs", []):
                    self.failed.append((entry["bug_id"], entry.get("folder", "")))
                logging.info(
                    "Resume: loaded %d successful, %d failed from %s",
                    len(self.successful), len(self.failed), summary_path,
                )
            except Exception as exc:
                logging.warning("Could not parse existing summary %s: %s", summary_path, exc)

    def already_done(self, instance_id: str) -> bool:
        with self.lock:
            return instance_id in self.seen

    def record_success(self, instance_id: str, folder: str, reduction: str, stats: dict) -> None:
        with self.lock:
            self.aggregated[reduction] = self.aggregated.get(reduction, 0) + 1
            self.successful.append((instance_id, folder, reduction, stats))
            self.seen.add(instance_id)
            self._flush()

    def record_failure(self, instance_id: str, folder: str, error: str) -> None:
        with self.lock:
            self.failed.append((instance_id, folder, {"error": error}))
            self._flush()

    def _flush(self) -> None:
        _save_summary_atomic(
            self.summary_path, self.aggregated,
            self.successful, self.failed, self.config,
        )


def _process_instance_threadsafe(
    traj_path: Path,
    instance_id: str,
    save_dir: Path,
    clone_pools: dict[str, Queue],
    minimize_at: str,
    summary: _SummaryState,
    base_commit: str,
    manual_asserts_dir: str | None = None,
) -> None:
    """Multi-mode worker — per-instance log file, repo lock, summary record.

    AIDEV-NOTE: The minimization itself now lives in cli/traj_cli.py. This is the batch
    wrapper: logger, lock, bookkeeping. ``base_commit`` arrives resolved (see _run_multi)
    instead of being looked up per instance from inside the lock.
    """
    instance_dir = save_dir / instance_id
    instance_dir.mkdir(parents=True, exist_ok=True)
    logger = make_instance_logger("inst", instance_id, instance_dir / "run.log")

    try:
        repo_prefix = instance_to_repo_prefix(instance_id)
        logger.info("Waiting for a free %s clone ...", repo_prefix)
        # AIDEV-NOTE: minimize_one_traj holds the clone for the whole call — its HEAD
        # must stay at this instance's base_commit across every candidate test.
        result = minimize_one_traj(
            traj_path=traj_path,
            instance_id=instance_id,
            save_dir=instance_dir,
            repo_dir_queue=clone_pools[repo_prefix],
            minimize_at=minimize_at,
            base_commit=base_commit,
            manual_asserts_dir=manual_asserts_dir,
            logger=logger,
        )
        if result is None:
            summary.record_failure(instance_id, str(instance_dir), "no_result")
        else:
            summary.record_success(
                instance_id, str(instance_dir),
                result.get("reduction", "0_to_0"),
                result.get("feedback_stats", {}),
            )
    except Exception as exc:
        logger.exception("=== FAIL %s ===", instance_id)
        summary.record_failure(instance_id, str(instance_dir), f"{type(exc).__name__}: {exc}")
    finally:
        close_instance_logger(logger)


def _run_multi(args: argparse.Namespace) -> int:
    traj_dir = Path(args.traj_dir).resolve()
    save_dir = Path(args.save_dir).resolve()
    save_dir.mkdir(parents=True, exist_ok=True)
    if args.workspace_root is None:
        args.workspace_root = os.path.join(
            os.environ.get("KAGENT_PATH", os.getcwd()),
            "results", "swe-bench-workspace",
        )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    setup_dispatcher_logging(save_dir, prefix="run")
    logging.info("traj_dir=%s save_dir=%s num_parallel=%d minimize_at=%s",
                 traj_dir, save_dir, args.num_parallel, args.minimize_at)

    if not traj_dir.is_dir():
        logging.error("traj_dir not found: %s", traj_dir)
        return 1
    all_trajs = sorted(traj_dir.glob("*.traj"))
    wanted = parse_instance_filter(args.instance_filter)
    if wanted:
        all_trajs = [p for p in all_trajs if p.stem in wanted]
    logging.info("Discovered %d trajectories", len(all_trajs))
    if not all_trajs:
        logging.error("No trajectories matched.")
        return 1

    summary_path = save_dir / "minimization_summary.json"
    config = {
        "traj_dir": str(traj_dir),
        "save_dir": str(save_dir),
        "num_parallel": args.num_parallel,
        "clones_per_repo": args.clones_per_repo,
        "minimize_at": args.minimize_at,
        "workspace_root": args.workspace_root,
        "started_at": ts,
    }
    summary = _SummaryState(summary_path, config)
    failed_ids = set() if args.retry_failed else {f[0] for f in summary.failed}

    to_run: list[tuple[Path, str]] = []
    skipped = 0
    for traj in all_trajs:
        iid = traj.stem
        if summary.already_done(iid) or iid in failed_ids:
            skipped += 1
            continue
        to_run.append((traj, iid))
    logging.info("To run: %d (skipped %d already-done)", len(to_run), skipped)
    if not to_run:
        logging.info("Nothing to do.")
        return 0

    # AIDEV-NOTE: One cached HF load for the whole batch, resolved HERE — after the skip
    # set, before the pool. After, because resolving earlier would let a resumed run abort
    # over an instance it was never going to process. Before, because the old code looked
    # each id up per instance by re-streaming the dataset from inside the per-repo lock.
    # An unresolvable id now aborts up front naming every offender (see base_commits_for).
    base_commits = base_commits_for([iid for _, iid in to_run])
    logging.info("Resolved %d base commits from HuggingFace", len(base_commits))

    # AIDEV-NOTE: Clones are created here, single-threaded, before any worker starts —
    # so creation can't race, and a failed `git clone` aborts before any minimization.
    clone_pools = _build_clone_pools(
        [iid for _, iid in to_run], args.workspace_root, args.clones_per_repo,
    )
    to_run = _interleave_by_repo(to_run)
    with ThreadPoolExecutor(max_workers=args.num_parallel, thread_name_prefix="swebench-min") as ex:
        futures = {
            ex.submit(
                _process_instance_threadsafe,
                traj, iid, save_dir,
                clone_pools, args.minimize_at, summary,
                base_commits[iid],
                args.manual_asserts_dir,
            ): iid
            for traj, iid in to_run
        }
        completed = 0
        for fut in as_completed(futures):
            iid = futures[fut]
            completed += 1
            try:
                fut.result()
            except Exception as exc:
                logging.error("Worker for %s raised: %s", iid, exc)
            if completed % 5 == 0 or completed == len(futures):
                logging.info(
                    "Progress: %d/%d (success=%d, failed=%d)",
                    completed, len(futures), len(summary.successful), len(summary.failed),
                )

    logging.info(
        "Final: %d successful, %d failed. Summary at %s",
        len(summary.successful), len(summary.failed), summary_path,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SWE-bench trajectory minimization over a directory (pipeline step 2).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # AIDEV-NOTE: This is the BATCH driver only. Single-instance moved to
    # cli/traj_cli.py — `python -m patch_minimizer.frontends.swebench.cli traj`.
    # --traj/--bug-id/--repo-dir deliberately do not exist here:
    # two CLI routes to the same job was the redundancy worth removing.
    parser.add_argument(
        "--traj-dir", required=True, metavar="DIR",
        help="Directory of <instance_id>.traj files.",
    )
    parser.add_argument(
        "--num-parallel", type=int, default=1,
        help="Worker count (default: 1).",
    )
    parser.add_argument(
        "--clones-per-repo", type=int, default=1, metavar="K",
        help="Independent clones per repo, so up to K instances of one repo run at once "
             "(default: 1 = one at a time). Set --num-parallel to about 12*K.",
    )
    parser.add_argument(
        "--instance-filter", default=None,
        help="Comma-separated subset of instance IDs.",
    )
    parser.add_argument(
        "--workspace-root", default=None,
        help="Root for per-repo clones (default: $KAGENT_PATH/results/swe-bench-workspace).",
    )
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Also retry previously-failed instances (default: skip).",
    )
    parser.add_argument(
        "--save-dir", required=True, metavar="DIR",
        help="Output directory; also holds minimization_summary.json.",
    )
    parser.add_argument(
        "--minimize-at", choices=("node", "edit"), default="edit",
        help="Minimization granularity (default: edit).",
    )
    parser.add_argument(
        "--manual-asserts-dir", default=None, metavar="DIR",
        help="Optional dir of <instance_id>/ sidecars with strengthened "
             "reproductions (*.py [+ commands.txt]) that augment the proxy "
             "oracle for weak-signal instances.",
    )
    # AIDEV-NOTE: Opt-in chaining, both default off. Pipeline step 4 (consolidate) is NOT
    # chainable: it needs hand-authored reproductions and a second minimize+postprocess
    # run into /tmp/remin before it has anything to merge. So 2->3->5 only.
    parser.add_argument(
        "--then-postprocess", action="store_true",
        help="After a successful batch, run pipeline step 3 (hidden oracle + gold compare).",
    )
    parser.add_argument(
        "--then-reconstruct", action="store_true",
        help="After a successful batch, run pipeline step 5 (churn-free agent patch).",
    )
    args = parser.parse_args()
    # AIDEV-NOTE: K=0 would build empty clone pools and every worker would block forever.
    if args.clones_per_repo < 1:
        parser.error("--clones-per-repo must be >= 1")

    rc = _run_multi(args)
    if rc != 0:
        return rc

    if args.then_postprocess:
        logging.info("Chaining pipeline step 3: postprocess")
        from patch_minimizer.frontends.swebench.example_code.swebench_pipeline import postprocess
        rc = postprocess.main(
            ["--save-dir", args.save_dir, "--traj-dir", args.traj_dir,
             "--num-parallel", str(args.num_parallel)]
        )
        if rc != 0:
            return rc
    if args.then_reconstruct:
        logging.info("Chaining pipeline step 5: reconstruct")
        from patch_minimizer.frontends.swebench.example_code.swebench_pipeline import reconstruct
        reconstruct.main(
            ["--save-dir", args.save_dir, "--traj-dir", args.traj_dir,
             "--workspace-root", args.workspace_root, "--write"]
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
