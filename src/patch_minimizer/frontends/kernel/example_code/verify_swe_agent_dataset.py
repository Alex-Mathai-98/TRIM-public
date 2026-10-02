#!/usr/bin/env python3
"""Dry verification for all SWE-agent trajectories: DB rows, benchmark JSON, patch applicability.

Run **before** ``run_swe_minimization.py --run-jobs`` to confirm ``agentPatchId`` → ``bugId``,
benchmark data, and that parsed edits apply at ``parentOfFixCommit`` (no kBDR submission).

Example::

    python verify_swe_agent_dataset.py --agent-patches \\
      --karena-db-path \"\\$KAGENT_PATH/karena.db.backup_20260125_203200\" \\
      --benchmark-folder \"\\$KBENCH_PATH\" \\
      --linux-dir \"\\$BASE_PATH/collection_of_linux_repos/linux-501\"

``--skip-apply`` only checks DB + benchmark JSON (fast). Omit it for full apply checks.

At the end, **--- Summary ---** includes a **Loader** block (traj scan + skip counts) and a
**Verification** block (DB / benchmark / apply). No flags required for that summary.

``-v`` / ``--verbose`` prints **loader WARNING+** during the scan (skips, missing ids). Use
``--debug-loader`` for full per-trajectory INFO (Added pass / Loading trajectory).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap (same layout as run_swe_minimization.py)
# ---------------------------------------------------------------------------


def _bootstrap_env_like_sample() -> None:
    _here = Path(__file__).resolve()
    # AIDEV-NOTE: Walk up to find the project root (dir containing src/patch_minimizer).
    # Was a hardcoded parents[6], which silently resolved to "/" after this file moved.
    for _parent in _here.parents:
        if (_parent / "src" / "patch_minimizer").is_dir():
            default_kagent = str(_parent)
            break
    else:
        raise RuntimeError(f"Cannot locate project root (src/patch_minimizer) above {_here}")
    default_base = str(Path(default_kagent).parent)

    os.environ.setdefault("KAGENT_PATH", default_kagent)
    os.environ.setdefault("BASE_PATH", default_base)
    os.environ.setdefault("KBENCH_PATH", os.path.join(os.environ["BASE_PATH"], "random_test_processed"))
    os.environ.setdefault(
        "AGENT_PATCHES_DIR",
        os.path.join(os.environ["KAGENT_PATH"], "agent-patches"),
    )
    os.environ.setdefault(
        "KARENA_DB_PATH",
        os.path.join(os.environ["KAGENT_PATH"], "karena.db.backup_20260125_203200"),
    )
    sys.path.insert(0, os.environ["BASE_PATH"])
    _src = os.path.join(os.environ["KAGENT_PATH"], "src")
    if os.path.isdir(os.path.join(_src, "Kernel_Agent")):
        sys.path.insert(0, _src)


_bootstrap_env_like_sample()

from patch_minimizer.agent_adapters.agent_adapter_base.evaluation import (
    AgentPatchesLoadStats,
    AgentPassesStore,
)
from patch_minimizer.strategies.repo_patch_manager import (
    RepoPatchManager,
)
from patch_minimizer.core.rw_lock import RWLock


def _configure_loader_logging(*, verbose: bool, debug_loader: bool) -> None:
    """Keep root ERROR; attach loader-only handlers so INFO success lines stay quiet with ``-v``.

    AIDEV-NOTE: Per-pass INFO (``Added pass``, ``Loading Karena trajectory``) needs root INFO to
    show if we only used basicConfig — dedicated handlers + propagate=False avoid that for ``-v``.
    """
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(name)s: %(message)s")
    if not verbose and not debug_loader:
        return
    fmt = logging.Formatter("%(levelname)s %(name)s: %(message)s")
    if debug_loader:
        level = logging.INFO
        names = (
            "Kernel_Agent.tools.solution_minimization.agent_adapters.agent_adapter_base.evaluation.agent_passes_store",
        )
    else:
        level = logging.WARNING
        names = (
            "Kernel_Agent.tools.solution_minimization.agent_adapters.agent_adapter_base.evaluation.agent_passes_store",
        )
    for name in names:
        lg = logging.getLogger(name)
        lg.setLevel(level)
        lg.propagate = False
        h = logging.StreamHandler()
        h.setLevel(level)
        h.setFormatter(fmt)
        lg.addHandler(h)


def load_bug_data(benchmark_folder: str, bug_id: str) -> tuple[str, str]:
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
    return base_commit, str(kernel_base_url) if kernel_base_url else ""


def _normalize_bug_id_for_benchmark(benchmark_folder: str, bug_id: str) -> str:
    p = os.path.join(benchmark_folder, f"{bug_id}.json")
    if not os.path.isfile(p) and not bug_id.endswith("_0"):
        p0 = os.path.join(benchmark_folder, f"{bug_id}_0.json")
        if os.path.isfile(p0):
            return f"{bug_id}_0"
    return bug_id


def _print_load_stats(stats: AgentPatchesLoadStats) -> None:
    """One block for verify stdout (no per-pass INFO)."""
    print("  Loader (trajectory scan):")
    print(f"    traj files scanned:            {stats.traj_files_scanned}")
    print(f"    loaded (passes with edits):  {stats.loaded_passes}")
    rows: list[tuple[str, int]] = [
        ("skipped (no events/trajectory/messages in head)", stats.skipped_unsupported_format),
        ("skipped (no agentPatchId)", stats.skipped_no_agent_patch_id),
        ("skipped (not swe-agent in DB filter)", stats.skipped_not_agent_family),
        ("skipped (agentPatchId not in DB)", stats.skipped_not_in_db),
        ("skipped (no bugId; need KARENA_DB_PATH)", stats.skipped_no_bugid),
        ("skipped (parsed, no edits)", stats.skipped_no_edits),
    ]
    if any(n for _, n in rows):
        for label, n in rows:
            if n:
                print(f"    {label}: {n}")


def _db_row_for_patch(
    conn: sqlite3.Connection, agent_patch_id: int
) -> tuple[str | None, int | None, str | None]:
    """Return (bugId, agentConfigId, agent_family) from joined tables."""
    row = conn.execute(
        """
        SELECT ap.bugId, ap.agentConfigId, ac.agent
        FROM agent_patches ap
        JOIN agent_configurations ac ON ac.agentConfigId = ap.agentConfigId
        WHERE ap.agentPatchId = ?
        LIMIT 1
        """,
        (agent_patch_id,),
    ).fetchone()
    if not row:
        return None, None, None
    return str(row[0]), int(row[1]), str(row[2]) if row[2] else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify SWE-agent trajectories: DB + benchmark + optional patch apply (no kBDR)."
    )
    parser.add_argument("--agent-patches-dir", metavar="DIR", help="Agent-patches root (nested ok).")
    parser.add_argument("--agent-patches", action="store_true", help="Use AGENT_PATCHES_DIR.")
    parser.add_argument("--karena-db-path", default=None, help="karena.db (default KARENA_DB_PATH).")
    parser.add_argument(
        "--benchmark-folder",
        default=None,
        help="Per-bug JSONs (default KBENCH_PATH).",
    )
    parser.add_argument(
        "--linux-dir",
        default=None,
        help="Linux checkout used for apply check (default BASE_PATH/.../linux-501).",
    )
    parser.add_argument(
        "--skip-apply",
        action="store_true",
        help="Only DB + benchmark JSON checks (skip RepoPatchManager).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Process at most N passes (0 = all).",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on first failure.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Loader WARNING+ only (skips, missing metadata) — not per-pass INFO success lines.",
    )
    parser.add_argument(
        "--debug-loader",
        action="store_true",
        help="Full loader INFO (every Added pass / Loading trajectory); implies verbose-style output.",
    )
    args = parser.parse_args()

    if args.agent_patches:
        args.agent_patches_dir = args.agent_patches_dir or os.environ.get("AGENT_PATCHES_DIR")
    if not args.agent_patches_dir:
        parser.error("Provide --agent-patches-dir or --agent-patches")

    kdb = args.karena_db_path or os.environ.get("KARENA_DB_PATH")
    if not kdb or not os.path.isfile(kdb):
        parser.error("Set --karena-db-path or KARENA_DB_PATH to a valid karena.db")

    benchmark_folder = args.benchmark_folder or os.environ.get("KBENCH_PATH")
    if not benchmark_folder or not os.path.isdir(benchmark_folder):
        parser.error(
            "Invalid benchmark folder (need per-bug JSONs): "
            f"{benchmark_folder!r} — set --benchmark-folder or KBENCH_PATH to an existing directory "
            "(unset KBENCH_PATH to use default under BASE_PATH/random_test_processed)."
        )

    base_path = os.environ.get("BASE_PATH", "")
    linux_dir = args.linux_dir or os.path.join(
        base_path, "collection_of_linux_repos", "linux-501"
    )
    if not args.skip_apply and (not linux_dir or not os.path.isdir(linux_dir)):
        parser.error(f"Linux dir not found: {linux_dir} (or use --skip-apply)")

    _configure_loader_logging(verbose=args.verbose or args.debug_loader, debug_loader=args.debug_loader)
    logger = logging.getLogger("verify_swe_agent_dataset")

    store = AgentPassesStore()
    _, load_stats = store.load_from_agent_patches_directory(
        args.agent_patches_dir,
        karena_db_path=kdb,
        db_agent_family="swe-agent",
    )
    passes = store.get_all_passes()
    if args.limit and args.limit > 0:
        passes = passes[: args.limit]

    conn = sqlite3.connect(kdb)
    ok_db = ok_bench = ok_apply = 0
    failures: list[tuple[str, str]] = []

    try:
        for pass_ in passes:
            pid = pass_.pass_id
            aid = pass_.agent_patch_id
            if aid is None:
                failures.append((pid, "missing agent_patch_id"))
                if args.fail_fast:
                    break
                continue

            db_bug, cfg_id, family = _db_row_for_patch(conn, aid)
            if db_bug is None:
                failures.append((pid, f"agentPatchId={aid} not in agent_patches join"))
                if args.fail_fast:
                    break
                continue
            if family != "swe-agent":
                failures.append((pid, f"DB agent={family!r}, expected swe-agent"))
                if args.fail_fast:
                    break
                continue
            if db_bug != pass_.bug_id:
                failures.append(
                    (pid, f"bugId mismatch DB={db_bug!r} pass={pass_.bug_id!r}")
                )
                if args.fail_fast:
                    break
                continue
            ok_db += 1

            bug_id = _normalize_bug_id_for_benchmark(benchmark_folder, pass_.bug_id)
            try:
                base_commit, kernel_base_url = load_bug_data(benchmark_folder, bug_id)
            except Exception as e:
                failures.append((pid, f"benchmark/json: {e}"))
                if args.fail_fast:
                    break
                continue
            ok_bench += 1

            if args.skip_apply:
                continue

            patch_list = pass_.patch_lists[0] if pass_.patch_lists else []
            patch_range = [t[1] for t in patch_list if t[1]]
            if not patch_range:
                failures.append((pid, "empty patch_range"))
                if args.fail_fast:
                    break
                continue

            lock = RWLock()
            try:
                repo = RepoPatchManager(
                    linux_dir,
                    lock,
                    base_commit,
                    logger,
                    kernel_base_url or None,
                )
                applicable = repo.check_patch_range(None, patch_range, leave_patches=False)
            except Exception as e:
                failures.append((pid, f"apply check: {e}"))
                if args.fail_fast:
                    break
                continue

            if not applicable:
                failures.append(
                    (pid, "check_patch_range returned False (edits do not apply at base_commit)")
                )
                if args.fail_fast:
                    break
                continue
            ok_apply += 1
    finally:
        conn.close()

    print("\n--- Summary ---")
    _print_load_stats(load_stats)
    if args.limit and args.limit > 0:
        print(f"  Verification (after --limit {args.limit}):")
    else:
        print("  Verification (this run):")
    print(f"    DB bugId + swe-agent row OK:     {ok_db}")
    print(f"    Benchmark JSON OK:              {ok_bench}")
    if not args.skip_apply:
        print(f"    Patch apply (check_patch_range): {ok_apply}")
    print(f"    Failures:                        {len(failures)}")
    if failures:
        print("\n--- Failures (pass_id, reason) ---")
        for p, msg in failures[:50]:
            print(f"  {p}: {msg}")
        if len(failures) > 50:
            print(f"  ... and {len(failures) - 50} more")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
