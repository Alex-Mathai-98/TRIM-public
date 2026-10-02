"""Agent-patches path: ``--agent-patches-dir DIR`` / ``--agent-patches``.

``AgentPassesStore`` discovers passes (rglob for ``traj.json``, Karena DB ``bugId``
resolution, agent-family filter); each pass is then minimized by one ``traj_cli`` call via
``minimize_one_traj()``. Same worker the openhands and mini-swe runners use.

AIDEV-NOTE: The store parses every traj.json even though traj_cli parses it again.
Deliberate — the store drops trajectories with no edits, and that is
what makes a ``None`` back from traj_cli unambiguously a *failure* rather than "nothing to
do". Without it we could not record failed rows correctly.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Dict, List

# AIDEV-NOTE: Import order is load-bearing. ``core`` must be fully imported before
# ``agent_adapters``, or the known core/sed_parsing cycle bites (see example_code/AGENTS.md).
# cli.common is what satisfies that — it pulls core.rw_lock and strategies.minimize_core.
# Keep it above the agent_adapters imports.
from patch_minimizer.frontends.kernel.cli.common import (
    append_and_save,
    build_linux_pool_paths,
    check_summary_orphans,
    linux_dir_queue_from_paths,
    load_existing_summary,
    minimize_one_traj,
)

# AIDEV-NOTE: side-effect import — registers ClassicSWEAgentTrajectoryParser.
import patch_minimizer.agent_adapters.swe_agent_adapter  # noqa: F401
from patch_minimizer.agent_adapters.agent_adapter_base.evaluation import (
    AgentPass,
    AgentPassesStore,
)
from patch_minimizer.strategies.aggregate_feedback_stats import (
    aggregate_feedback_stats_from_summary,
)
from patch_minimizer.benchmark_utils.kernel.clean_and_update_all_linux_repos import (
    clean_all_linux_repos,
)

from patch_minimizer.frontends.kernel.example_code.run_swe_agent.karena_db import (
    agent_patch_has_kgym_evaluation,
    infer_swe_agent_config_id,
)


_AGENT = "swe-agent"
_MODEL_ID = "swe_trajectory"


def _gate_allows(pass_: AgentPass, db_agent_patch_allowed: Dict[str, bool]) -> bool:
    """Karena kgym gate for one pass.

    AIDEV-NOTE: This body used to exist twice — once in run_minimization_for_pass and once
    in build_bug_configs — which is how they drifted. Both messages are kept verbatim.
    """
    if pass_.agent_patch_id is None:
        print(
            f"Skip pass {pass_.pass_id} (bug_id={pass_.bug_id}): no agent_patch_id; "
            "cannot apply Karena kgym gate.",
            file=sys.stderr,
        )
        return False
    if not db_agent_patch_allowed.get(pass_.pass_id, False):
        print(
            f"Skip pass {pass_.pass_id} (bug_id={pass_.bug_id}, "
            f"agent_patch_id={pass_.agent_patch_id}): excluded by Karena kgym filter "
            "(--kgym-evaluation / --agent-config-id).",
            file=sys.stderr,
        )
        return False
    return True


def load_passes(
    args, karena_db: str | None, benchmark_folder: str
) -> tuple[List[AgentPass] | None, Dict[str, bool] | None]:
    """Store scan, ``_0`` bug_id fixup, Karena kgym gate and ``--limit``.

    Returns ``(passes_to_run, db_agent_patch_allowed)``; ``(None, None)`` when the store
    found nothing, which the caller treats as a clean exit. ``db_agent_patch_allowed`` is
    returned only so the runner can stamp ``db_filter`` on results — the filtering itself
    has already been applied here.
    """
    db_agent_patch_allowed: Dict[str, bool] | None = None
    store = AgentPassesStore()
    _, _ = store.load_from_agent_patches_directory(
        args.agent_patches_dir,
        karena_db_path=karena_db,
        db_agent_family=None if args.db_agent_family == "all" else args.db_agent_family,
    )

    if len(store) == 0:
        print("No passes with candidates found.")
        return None, None

    all_passes = store.get_all_passes()

    for pass_ in all_passes:
        p = os.path.join(benchmark_folder, f"{pass_.bug_id}.json")
        if not os.path.isfile(p) and not pass_.bug_id.endswith("_0"):
            p0 = os.path.join(benchmark_folder, f"{pass_.bug_id}_0.json")
            if os.path.isfile(p0):
                pass_.bug_id = f"{pass_.bug_id}_0"

    if karena_db:
        conn = sqlite3.connect(karena_db)
        try:
            agent_config_id = args.agent_config_id
            if agent_config_id is None:
                agent_config_id = infer_swe_agent_config_id(conn)
            db_agent_patch_allowed = {}
            for pass_ in all_passes:
                if pass_.agent_patch_id is None:
                    db_agent_patch_allowed[pass_.pass_id] = False
                else:
                    db_agent_patch_allowed[pass_.pass_id] = agent_patch_has_kgym_evaluation(
                        conn,
                        agent_config_id=agent_config_id,
                        agent_patch_id=pass_.agent_patch_id,
                        kGymEvaluation=args.kgym_evaluation,
                    )
        finally:
            conn.close()

    passes_to_run = list(all_passes)
    # AIDEV-NOTE: --limit before the gate, matching the original ordering (it limited passes
    # loaded from the store, and the gate was applied per-pass further downstream).
    if args.limit and args.limit > 0:
        passes_to_run = passes_to_run[: args.limit]
        print(f"Limit: running {len(passes_to_run)} pass(es) (--limit {args.limit}).")

    if db_agent_patch_allowed is not None:
        passes_to_run = [p for p in passes_to_run if _gate_allows(p, db_agent_patch_allowed)]

    return passes_to_run, db_agent_patch_allowed


def run(
    passes_to_run: List[AgentPass],
    db_agent_patch_allowed: Dict[str, bool] | None,
    args,
    *,
    benchmark_folder: str,
    golden_path: str,
    linux_dir: str,
    base_path: str,
    shared_config: dict,
    num_parallel: int,
) -> int:
    """Minimize every pass through ``traj_cli``, sequentially or in a thread pool."""
    if not passes_to_run:
        print("No minimization tasks after filtering.")
        return 0

    pool_paths = build_linux_pool_paths(
        linux_dirs=args.linux_dirs,
        linux_dir_start=args.linux_dir_start,
        linux_dir_end=args.linux_dir_end,
        linux_dir=linux_dir,
        num_parallel=num_parallel,
        base_path=base_path,
    )

    # AIDEV-NOTE: Metadata only — append_and_save()'s failure branch reads just result_key,
    # pass_id, bug_id and candidate_index. No patch_list needed, so the summary machinery
    # works unchanged without carrying parsed edits around.
    rows = [
        {"bug_id": p.bug_id, "pass_id": p.pass_id, "result_key": f"{p.pass_id}__c0"}
        for p in passes_to_run
    ]

    summary_state = None
    if args.save_dir:
        summary_path = Path(args.save_dir).resolve() / "minimization_summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        agg, succ, fail, seen = load_existing_summary(summary_path)
        # AIDEV-NOTE: "lock" is always present now. The old sequential path omitted it
        # because nothing contended; one _process_one serves both modes, so sequential
        # takes the lock too — it just never waits on it.
        summary_state = {
            "lock": Lock(),
            "aggregated": agg, "successful": succ, "failed": fail, "seen": seen,
            "path": summary_path,
            "config": {
                "results_dir": str(Path(args.save_dir).resolve()),
                "benchmark_folder": benchmark_folder,
                "golden_subset": golden_path,
                "linux_dirs": pool_paths,
                "model_id": _MODEL_ID,
                "run_jobs": args.run_jobs,
                "parallel": num_parallel,
            },
        }

    # AIDEV-NOTE: Prompts on stdin, so it must run before the ThreadPoolExecutor — never on
    # a worker thread. Keyed on pass_id, which is what the summary's folder field records.
    if summary_state:
        check_summary_orphans({p.pass_id for p in passes_to_run}, summary_state)

    clean_all_linux_repos(pool_paths, parallel=len(pool_paths) > 1)
    linux_dir_queue = linux_dir_queue_from_paths(pool_paths)

    def _process_one(pass_: AgentPass) -> dict | None:
        # AIDEV-NOTE: Keyed on pass_id (agent_patch_<id>), NOT bug_id — the openhands
        # runners use bug_id, and copying that here would relocate every output directory,
        # break resume and hide results from discover_pairs.py. Candidate index is always 0:
        # parse_trajectory() yields at most one CandidateTrajectory per file.
        out_dir = (
            os.path.join(args.save_dir, pass_.pass_id, "swe_candidate_0")
            if args.save_dir else None
        )
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        result = minimize_one_traj(
            agent=_AGENT,
            traj_json=pass_.run_json_path,
            bug_id=pass_.bug_id,
            linux_dir_queue=linux_dir_queue,
            shared_config=shared_config,
            save_dir=out_dir,
        )
        if summary_state:
            with summary_state["lock"]:
                if result:
                    # Stamped after the call so resumed (disk-loaded) results get them too.
                    result["pass_id"] = pass_.pass_id
                    if db_agent_patch_allowed is not None:
                        result["agent_patch_id"] = pass_.agent_patch_id
                        result["db_filter"] = "agent_patch_id"
                    append_and_save(result, summary_state)
                else:
                    append_and_save(
                        None, summary_state, bug_configs=rows,
                        key=f"{pass_.pass_id}__c0",
                    )
        return result

    all_results: List[dict] = []
    if num_parallel > 1:
        print(f"Running {len(passes_to_run)} pass(es) with {num_parallel} parallel workers.")
        with ThreadPoolExecutor(max_workers=num_parallel) as executor:
            futures = {executor.submit(_process_one, p): p.pass_id for p in passes_to_run}
            for future in as_completed(futures):
                pass_id = futures[future]
                try:
                    result = future.result()
                    if result:
                        all_results.append(result)
                except Exception as e:
                    print(f"Error processing {pass_id}: {e}", file=sys.stderr)
    else:
        print(f"Running {len(passes_to_run)} pass(es) sequentially.")
        for pass_ in passes_to_run:
            result = _process_one(pass_)
            if result:
                all_results.append(result)
                print(
                    f"  {pass_.pass_id}: reduction={result['reduction']}, "
                    f"minimized_edits={result['minimized_edit_count']}"
                )

    if summary_state:
        aggregate_feedback_stats_from_summary(str(summary_state["path"]))
    print(f"\nDone: {len(all_results)} minimization run(s).")
    return 0
