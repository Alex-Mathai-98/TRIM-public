"""Shared orchestration infra for the SWE-bench CLIs.

Logging, atomic JSON writes, argument parsing fragments and the per-instance worker.
Imported by the ``example_code/`` runners; domain helpers live in ``utils/`` instead.

AIDEV-NOTE: There is deliberately NO ``common`` <-> ``traj_cli`` cycle.
``instance_to_repo_prefix`` lives in ``utils/instance_ids.py`` so ``traj_cli`` depends only
on ``utils/``. The single edge is one-way — ``minimize_one_traj`` -> ``traj_cli`` — and it
is function-level so this module still imports cleanly on its own. Kernel does have the
cycle and resolves it asymmetrically; avoiding it is better, so do not "tidy"
``instance_to_repo_prefix`` into this file. See z-cpl-44 step 2.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from queue import Queue


# ---------------------------------------------------------------------------
# Atomic JSON write
# ---------------------------------------------------------------------------

def write_json_atomic(path: Path | str, data: dict) -> None:
    """Write JSON via temp file + ``os.replace``.

    AIDEV-NOTE: Replaces four hand-rolled copies (postprocessing :467-472 and :647-650,
    consolidate :57-59 and :115-117). ``default=str`` matches the postprocessing copy,
    which is the one that writes result dicts containing non-JSON-native values.

    Not to be confused with ``strategies.minimize_core._save_summary_atomic``, which is
    for the *summary* and takes its five components separately.
    """
    p = Path(path)
    tmp = str(p) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, p)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def instance_logger(namespace: str, instance_id: str, log_path: Path | str) -> logging.Logger:
    """Per-instance file logger. Replaces the copies at minimization :371-378 and
    postprocessing :483-491.

    AIDEV-NOTE: ``propagate = False`` keeps per-instance lines out of the dispatcher log,
    and ``handlers.clear()`` matters because ``getLogger`` returns a singleton — a resumed
    or retried instance would otherwise accumulate handlers and duplicate every line.
    Pair with ``close_instance_logger`` in a ``finally``.
    """
    logger = logging.getLogger(f"{namespace}.{instance_id}")
    logger.handlers.clear()
    fh = logging.FileHandler(log_path)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def close_instance_logger(logger: logging.Logger) -> None:
    """Flush and detach handlers. Replaces minimization :406-410 / postprocessing :583-586."""
    for h in logger.handlers:
        h.flush()
        h.close()
    logger.handlers.clear()


def setup_dispatcher_logging(save_dir: Path | str, prefix: str = "run") -> Path:
    """Timestamped top-level log + stdout. Replaces minimization :475-485 /
    postprocessing :682-688. Returns the log path."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    top_log = Path(save_dir) / f"{prefix}_{ts}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(top_log), logging.StreamHandler(sys.stdout)],
        force=True,
    )
    return top_log


# ---------------------------------------------------------------------------
# Argument helpers
# ---------------------------------------------------------------------------

def parse_instance_filter(raw: str | None) -> set[str] | None:
    """``"a, b ,c"`` -> ``{"a","b","c"}``; ``None``/empty -> ``None`` (meaning no filter).

    Replaces the identical blocks at minimization :493-495 and postprocessing :701-703.
    """
    if not raw:
        return None
    wanted = {x.strip() for x in raw.split(",") if x.strip()}
    return wanted or None


# ---------------------------------------------------------------------------
# Per-instance worker
# ---------------------------------------------------------------------------

def minimize_one_traj(
    *,
    traj_path: Path,
    instance_id: str,
    save_dir: Path,
    repo_dir_queue: Queue,
    minimize_at: str = "edit",
    base_commit: str | None = None,
    manual_asserts_dir: str | None = None,
    logger: logging.Logger | None = None,
) -> dict | None:
    """Take a free ``(clone, RWLock)`` of this repo, run one ``minimize_traj``, return it.

    Mirrors ``frontends/kernel/cli/common.py:minimize_one_traj`` (same queue shape, same
    force-release), except that ``minimize_traj`` is called directly instead of through an
    argv round-trip, and resume/error handling stay in the batch driver.

    AIDEV-NOTE: Clone exclusivity comes from the queue — one worker per entry — not from
    the RWLock. The entry is held for the whole call: the clone's HEAD has to stay at this
    instance's base_commit across every inner candidate test.
    """
    # AIDEV-NOTE: function-level import on purpose — traj_cli is a sibling that must not
    # be imported at module scope. See the module docstring.
    from patch_minimizer.frontends.swebench.cli.traj_cli import minimize_traj

    repo_dir, code_dir_lock = repo_dir_queue.get()
    if logger is not None:
        logger.info("Using clone %s", repo_dir)
    try:
        return minimize_traj(
            traj_path=traj_path,
            instance_id=instance_id,
            save_dir=save_dir,
            repo_dir=repo_dir,
            code_dir_lock=code_dir_lock,
            minimize_at=minimize_at,
            base_commit=base_commit,
            manual_asserts_dir=manual_asserts_dir,
            logger=logger,
        )
    finally:
        # AIDEV-NOTE: same as kernel/cli/common.py:228-235 — a lock returned to the pool
        # still held makes the next instance on this clone fail at once. w_release() (not
        # the raw w_lock.release()) also clears the owner, avoiding a false deadlock report.
        if code_dir_lock.is_w_locked():
            if logger is not None:
                logger.warning("Force-released write lock for %s", repo_dir)
            code_dir_lock.w_release()
        repo_dir_queue.put((repo_dir, code_dir_lock))
