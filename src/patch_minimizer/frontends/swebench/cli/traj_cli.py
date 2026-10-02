#!/usr/bin/env python3
"""Minimize one SWE-bench trajectory — the atomic unit.

One ``.traj`` + one ``instance_id`` -> one result dict. Orchestration (directory walking,
repo pooling, resume, summaries) lives in the calling scripts, not here. Mirrors
``frontends/kernel/cli/traj_cli.py``.

AIDEV-NOTE: Extracted from run_swebench_minimization.py — ``minimize_traj`` was
``_process_instance`` (lines 246-356), ``main`` was ``_run_single`` (413-462). Bodies are
moved, not rewritten. Two substitutions only: ``patch_file_paths`` replaces the inlined
``_submission_paths``, and ``base_commits_for`` replaces the per-instance HF stream.

AIDEV-NOTE: Imports ONLY ``utils/`` from this package — never ``cli/common.py``. common
imports back into this module inside ``minimize_one_traj``, so a module-level import here
would create a cycle. See z-cpl-44 step 2.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

from patch_minimizer.strategies.minimize_core import (
    minimize_edit_list,
    save_minimization_results,
)
from patch_minimizer.strategies.algorithms.node.node_minimizer import NodeMinimizer
from patch_minimizer.strategies.algorithms.edit.edit_minimizer import EditMinimizer
from patch_minimizer.strategies.environments import FeedbackReceiver, GitDiffEnvironment
from patch_minimizer.agent_adapters.swe_agent_adapter.swebench_parsers import (
    SWEBenchSWEAgentTrajectoryParser,
)
from patch_minimizer.agent_adapters.agent_adapter_base.utils import (
    extract_test_files_from_trajectory,
    to_patch_list,
    stamp_global_edit_idx,
)
from patch_minimizer.frontends.swebench.environment import SWEBenchEnvironment
from patch_minimizer.frontends.swebench.submission_filter import filter_to_submission_paths
from patch_minimizer.frontends.swebench.utils.diff_utils import patch_file_paths
from patch_minimizer.frontends.swebench.utils.hf_dataset import base_commits_for
from patch_minimizer.frontends.swebench.utils.instance_ids import instance_to_repo_prefix
from patch_minimizer.core.rw_lock import RWLock


def _get_full_patch_from_trajectory(data: dict) -> str:
    patch = data.get("info", {}).get("submission", "")
    if patch and not patch.endswith("\n"):
        patch += "\n"
    return patch


def _collect_feedback_commands(candidate) -> list[dict]:
    seen: set[str] = set()
    commands: list[dict] = []
    for rev in candidate.revisions:
        for cmd_entry in rev.feedback_commands:
            if cmd_entry["command"] not in seen:
                seen.add(cmd_entry["command"])
                commands.append(cmd_entry)
    return commands



# AIDEV-NOTE: Manual-assert override. For weak-oracle
# over-min cases (agent proxy test only print()s → minimizer drops the F2P fix),
# drop a strengthened reproduction in <manual_asserts_dir>/<instance_id>/. Every
# *.py there is injected into the container as a test file and (via commands.txt,
# or a default `cd /testbed && python <file>`) AUGMENTS — never replaces — the
# feedback commands, so existing P2P-protective commands are retained and the
# minimizer can only keep MORE. Zero blast radius: instances without a sidecar
# are untouched.
def _load_manual_asserts(
    instance_id: str, manual_dir: str | None, logger: logging.Logger
) -> tuple[dict[str, str], list[dict]]:
    """Return ({filename: content}, [feedback-command dicts]) for an instance's
    manual-assert sidecar, or ({}, []) if none exists."""
    if not manual_dir:
        return {}, []
    inst_dir = os.path.join(manual_dir, instance_id)
    if not os.path.isdir(inst_dir):
        return {}, []
    files: dict[str, str] = {}
    for name in sorted(os.listdir(inst_dir)):
        if name.endswith(".py"):
            with open(os.path.join(inst_dir, name)) as f:
                files[name] = f.read()
    commands: list[dict] = []
    cmds_path = os.path.join(inst_dir, "commands.txt")
    if os.path.isfile(cmds_path):
        with open(cmds_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    commands.append({"step_index": -1, "command": line})
    else:
        for name in files:
            commands.append({"step_index": -1, "command": f"cd /testbed && python {name}"})
    if files or commands:
        logger.info(
            "Manual-assert override: %d file(s)=%s, %d command(s)",
            len(files), sorted(files), len(commands),
        )
    return files, commands




def minimize_traj(
    *,
    traj_path: Path,
    instance_id: str,
    save_dir: Path,
    repo_dir: str,
    minimize_at: str = "edit",
    base_commit: str | None = None,
    manual_asserts_dir: str | None = None,
    code_dir_lock: RWLock | None = None,
    logger: logging.Logger | None = None,
    only_parse: bool = False,
) -> dict | None:
    """Run minimization for one instance. Returns the result dict or None on failure.

    With ``only_parse=True`` it stops after parsing and returns the prepared inputs
    (``candidate``, ``patch_list``, ``test_files``, ``feedback_commands``, ``sub_paths``)
    instead — no HuggingFace lookup, Docker, or minimizer.
    """
    # AIDEV-NOTE: defaults the old positional signature made the caller supply.
    if code_dir_lock is None:
        code_dir_lock = RWLock()
    if logger is None:
        logger = logging.getLogger(f"inst.{instance_id}")
    instance_logger = logger

    started = time.time()
    instance_logger.info("=== START %s ===", instance_id)

    with open(traj_path) as f:
        data = json.load(f)

    parser = SWEBenchSWEAgentTrajectoryParser()
    candidates = parser.parse(data, source_label=instance_id)
    if not candidates:
        instance_logger.warning("No candidates with edits in %s", traj_path)
        return None
    candidate = candidates[0]
    instance_logger.info("Candidate revisions=%d", len(candidate.revisions))

    patch_list = to_patch_list(candidate)
    stamp_global_edit_idx(patch_list)
    total_edits = sum(len(edits) for _, edits in patch_list)
    if total_edits == 0:
        instance_logger.warning("Zero edits in candidate")
        return None
    instance_logger.info("Total edits before filter: %d", total_edits)

    test_files = extract_test_files_from_trajectory(candidate)
    feedback_commands = _collect_feedback_commands(candidate)
    # AIDEV-NOTE: augment (not replace) with manual-assert sidecar if present.
    ma_files, ma_cmds = _load_manual_asserts(instance_id, manual_asserts_dir, instance_logger)
    if ma_files:
        test_files = {**test_files, **ma_files}
    if ma_cmds:
        seen_cmds = {c["command"] for c in feedback_commands}
        feedback_commands = feedback_commands + [c for c in ma_cmds if c["command"] not in seen_cmds]
    instance_logger.info(
        "Test files=%d, feedback commands=%d",
        len(test_files), len(feedback_commands),
    )

    submission = _get_full_patch_from_trajectory(data)
    sub_paths = set(patch_file_paths(submission)) if submission else set()
    instance_logger.info("Submission paths (%d): %s", len(sub_paths), sorted(sub_paths))
    if not sub_paths:
        instance_logger.warning("No submission paths — instance has empty info.submission")
        return None
    # AIDEV-NOTE: Drop scratch-file edits the agent never submitted; without this they
    # clobber the container's injected test_files with str_replace fragments. Pure, so it
    # sits before the HF/Docker steps to let only_parse return the final patch list.
    patch_list = filter_to_submission_paths(patch_list, sub_paths, instance_logger)

    if only_parse:
        # AIDEV-NOTE: parse-only path for tests; returns before HF/Docker/minimizer.
        # Used by tests/parsing_benchmark_tests/swe_bench_swe_agent/parser_baseline.py.
        return {
            "candidate": candidate,
            "patch_list": patch_list,
            "test_files": test_files,
            "feedback_commands": feedback_commands,
            "sub_paths": sorted(sub_paths),
        }

    if base_commit is None:
        instance_logger.info("Loading base_commit from HuggingFace SWE-bench dataset...")
        base_commit = base_commits_for([instance_id])[instance_id]
    instance_logger.info("Base commit: %s", base_commit)

    env = SWEBenchEnvironment(
        instance_id=instance_id,
        feedback_commands=feedback_commands,
        test_files=test_files,
        logger=instance_logger,
    )
    dropped = env.validate_feedback_commands(submission)
    feedback_commands = env.feedback_commands
    instance_logger.info(
        "Pre-flight kept=%d dropped=%d", len(feedback_commands), len(dropped),
    )
    if not feedback_commands:
        instance_logger.warning("All feedback commands failed pre-flight")
        return None

    if not os.path.isdir(repo_dir):
        instance_logger.error("Repo dir not found: %s", repo_dir)
        return None

    # AIDEV-NOTE: swebench_mode tolerates edits on files absent from the local clone —
    # the container is the only gate. See repo_patch_manager._resolve_file_path.
    minimizer_cls = NodeMinimizer if minimize_at == "node" else EditMinimizer
    minimizer = minimizer_cls(
        repo_dir, code_dir_lock, base_commit, instance_logger,
        kernel_base_url=None, swebench_mode=True,
    )
    # AIDEV-NOTE: Reuse the env built above — it is stateless per call, and after
    # validate_feedback_commands its feedback_commands are already the validated set.
    feedback_aggregator = FeedbackReceiver(env, git_diff_env=GitDiffEnvironment())

    instance_logger.info("Running minimize_edit_list at %s level...", minimize_at)
    result = minimize_edit_list(
        bug_id=instance_id,
        patch_list=patch_list,
        minimizer=minimizer,
        feedback_aggregator=feedback_aggregator,
        minimize_at=minimize_at,
        save_dir=str(save_dir),
    )
    save_minimization_results(result, patch_list, str(save_dir))
    elapsed = time.time() - started
    instance_logger.info(
        "=== DONE %s in %.1fs — reduction=%s (orig=%s, min=%s) ===",
        instance_id, elapsed, result.get("reduction"),
        result.get("original_edit_count"), result.get("minimized_edit_count"),
    )
    return result



def main(argv: list[str] | None = None, *, code_dir_lock=None, logger=None) -> dict | None:
    """CLI wrapper. Same signature shape as ``frontends/kernel/cli/traj_cli.py``.

    AIDEV-NOTE: Body is ``_run_single`` (run_swebench_minimization.py:413-462) — validate,
    auto-derive repo_dir, default save_dir, build a stdout logger — then delegate. It
    returns the result dict (or None), not an exit code; ``__main__`` maps that to one.
    """
    parser = argparse.ArgumentParser(
        description="Minimize one SWE-bench trajectory (single instance)."
    )
    # AIDEV-NOTE: --agent is required, same as the kernel traj CLI. SWE-bench only has a
    # SWE-agent parser today, so any other value is rejected below rather than mis-parsed.
    # Callers of minimize_traj() (batch driver, parsing tests) don't go through argparse.
    parser.add_argument("--agent", required=True,
                        help="Agent scaffold that produced the trajectory. "
                             "Only 'swe-agent' is supported for SWE-bench.")
    parser.add_argument("--traj", required=True, help="Path to the SWE-agent .traj file.")
    # AIDEV-NOTE: --traj / --bug-id / --repo-dir are the same names as the kernel traj CLI.
    # The old --instance-id alias was removed (2026-10-02); dest is still instance_id.
    parser.add_argument("--bug-id", dest="instance_id", required=True,
                        metavar="BUG_ID",
                        help="SWE-bench instance id, e.g. django__django-16527.")
    parser.add_argument("--repo-dir", default=None,
                        help="Clone of the instance's repo. Default: "
                             "$KAGENT_PATH/results/swe-bench-workspace/<owner>__<repo>.")
    parser.add_argument("--save-dir", default=None,
                        help="Output directory. Default: /tmp/min_<instance_id>.")
    parser.add_argument("--minimize-at", choices=("node", "edit"), default="edit",
                        help="Minimization granularity.")
    parser.add_argument("--manual-asserts-dir", default=None,
                        help="Sidecar dir of strengthened reproductions (augments the proxy).")
    parser.add_argument("--only-parse", action="store_true",
                        help="Parse only: print the prepared patch list and stop (no repo/Docker).")
    args = parser.parse_args(argv)
    if args.agent != "swe-agent":
        parser.error(
            f"--agent {args.agent!r} is not supported for SWE-bench yet; only 'swe-agent' is. "
            "(The kernel traj CLI also accepts 'openhands' and 'mini-swe-agent'.)"
        )

    if not os.path.isfile(args.traj):
        print(f"Error: trajectory not found: {args.traj}", file=sys.stderr)
        return None
    if args.repo_dir is None:
        repo_prefix = instance_to_repo_prefix(args.instance_id)
        kagent = os.environ.get("KAGENT_PATH", os.getcwd())
        args.repo_dir = os.path.join(kagent, "results", "swe-bench-workspace", repo_prefix)
        print(f"  Auto-derived --repo-dir: {args.repo_dir}")
    if not args.only_parse and not os.path.isdir(args.repo_dir):
        print(
            f"Error: repo dir not found: {args.repo_dir}\n"
            f"Hint: clone {instance_to_repo_prefix(args.instance_id)} into "
            f"`results/swe-bench-workspace/` or pass --repo-dir explicitly.",
            file=sys.stderr,
        )
        return None

    save_dir = Path(args.save_dir) if args.save_dir else Path("/tmp") / f"min_{args.instance_id}"
    save_dir.mkdir(parents=True, exist_ok=True)

    if logger is None:
        logger = logging.getLogger(f"inst.{args.instance_id}")
        logger.handlers.clear()
        logger.addHandler(logging.StreamHandler(sys.stdout))
        logger.setLevel(logging.INFO)
        logger.propagate = False

    try:
        result = minimize_traj(
            traj_path=Path(args.traj),
            instance_id=args.instance_id,
            save_dir=save_dir,
            repo_dir=args.repo_dir,
            minimize_at=args.minimize_at,
            # AIDEV-NOTE: no --base-commit flag (removed 2026-10-02); always looked up in
            # SWE-bench_Verified. minimize_traj(base_commit=) stays for the batch driver.
            manual_asserts_dir=args.manual_asserts_dir,
            code_dir_lock=code_dir_lock,
            logger=logger,
            only_parse=args.only_parse,
        )
    except Exception as e:
        print(f"Error during minimization: {e}", file=sys.stderr)
        traceback.print_exc()
        return None

    if result is None:
        print("Minimization did not produce a result.", file=sys.stderr)
        return None

    if args.only_parse:
        edits = [e for _, node_edits in result["patch_list"] for e in node_edits]
        print("\nParsed:")
        print(f"  nodes:              {len(result['patch_list'])}")
        print(f"  edits:              {len(edits)}")
        print(f"  submission paths:   {result['sub_paths']}")
        print(f"  test files:         {sorted(result['test_files'])}")
        print(f"  feedback commands:  {len(result['feedback_commands'])}")
        return result

    print("\nResult:")
    print(f"  reduction:        {result.get('reduction')}")
    print(f"  original_edits:   {result.get('original_edit_count')}")
    print(f"  minimized_edits:  {result.get('minimized_edit_count')}")
    print(f"  saved to:         {save_dir}")
    return result


if __name__ == "__main__":
    sys.exit(0 if main() is not None else 1)
