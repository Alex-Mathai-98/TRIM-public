#!/usr/bin/env python3
"""Sample usage of minimize_edits() — a runnable demo using a real kernel bug.

AIDEV-NOTE: This script demonstrates how to call the standalone minimization
entry point using in-memory Edit objects from a real agent run. For SWE-agent
runs with parsed trajectories, use run_swe_minimization.py + SWEAgentPassesStore.

Prerequisites
=============
1. Activate the conda environment and source dev.env::

       source <KAGENT_PATH>/.claude/prelude.sh
       # e.g. source <KAGENT_PATH>/.claude/prelude.sh

   This sets up:
     - conda env ``deployment_kernel_agent``
     - env vars from ``dev.env`` (KAGENT_PATH, BASE_PATH, KBDR_*, etc.)
     - PYTHONPATH so ``Kernel_Agent`` package is importable

2. Run::

       python sample_minimize_edits.py

How it works
============
The minimization algorithm tries to **remove edits one-by-one** and keeps
only those whose removal would break the patch (i.e., it can no longer be
applied cleanly, or the runtime test fails).

This demo uses ``run_jobs=True`` (**KernelEnvironment**): it submits real
kernel build+test jobs to the kGym/kBDR infrastructure.  Each job takes ~15
minutes, so the full run may take a while.  An edit survives minimization if
removing it causes the remaining patch to either **fail to apply** or **fail
the runtime test** (i.e., the crash reappears).

The script loads ``benchmark_folder`` and ``golden_subset_data`` from
``BENCHMARK_FOLDER`` (the ``KBENCH_PATH`` from ``run_minimization_only.sh``).
The KBDR env vars (``KBDR_BUCKET_NAME``, ``KBDR_RUNNER_API_BASE_URL``) are
set by ``dev.env`` via the prelude.

To run without kernel infrastructure, set ``run_jobs=False`` to use
**DryRunEnvironment** instead (always reports "pass" — only tests structural
patch applicability).

Bug used
========
Bug ID: ``e30eafa41838054ed71bff63e92b4ca38ccd46b2``
Title:  "possible deadlock in __nilfs_error (3)"
File:   ``fs/nilfs2/the_nilfs.c``
Category: Superset → Identical (equivalent to ground truth after minimization)

The edits come from a real agent run (gemini-3-pro, tree_4, run_10000).
The agent produced 4 edits across 2 nodes:
  - Node 0: 2 edits — the real fix (reorder lock acquisition to avoid deadlock)
  - Node 1: 2 edits — cosmetic comment additions (with parent_diff from Node 0)
After minimization: 4 → 1 edit (the comment edits and one fix edit are dropped).
"""
# ---------------------------------------------------------------------------
# Environment variables (set by `source .claude/prelude.sh` → dev.env)
# ---------------------------------------------------------------------------
# Required:
#   BASE_PATH                — Parent dir containing collection_of_linux_repos/
#                              (e.g. /home/.../Kernel_Playground)
#   KAGENT_PATH              — Project root (e.g. /home/.../Kernel_Agent)
#
# Required when run_jobs=True (KernelEnvironment — real kernel builds):
#   KBDR_RUNNER_API_BASE_URL — kGym/kBDR API endpoint for job submission
#                              = "https://your-kgym-api-endpoint.example.com"
#                              (used in kgym_utils/util_funcs.py:get_kgym_api_url)
#   KBDR_BUCKET_NAME         — GCS bucket for kernel build/test results
#                              = "your-gcs-bucket"
#                              (used in kgym_utils/job_analyzer.py, job_downloader.py)
#   PROJECT_ID               — Google Cloud project ID for GCS authentication
#                              = "your-gcp-project-id"
#                              (used in kgym_utils/job_downloader.py)
# ---------------------------------------------------------------------------
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Environment variables — replace these dummy values with your actual paths
# ---------------------------------------------------------------------------
os.environ["BASE_PATH"] = "/path/to/Kernel_Playground"
os.environ["KAGENT_PATH"] = "/path/to/Kernel_Playground/Kernel_Agent"
os.environ["KBENCH_PATH"] = "/path/to/Kernel_Playground/random_test_processed"
os.environ["KBDR_RUNNER_API_BASE_URL"] = "https://your-kgym-api-endpoint.example.com"
os.environ["KBDR_BUCKET_NAME"] = "your-gcs-bucket"
os.environ["PROJECT_ID"] = "your-gcp-project-id"

# # example concrete values of environment variables
# os.environ["BASE_PATH"] = "/path/to/Kernel_Playground"
# os.environ["KAGENT_PATH"] = "/path/to/Kernel_Playground/Kernel_Agent"
# os.environ["KBENCH_PATH"] = "/path/to/Kernel_Playground/Kernel_Agent/src/Kernel_Agent/deployment_related_code/random_test_processed"
# os.environ["KBDR_RUNNER_API_BASE_URL"] = "https://your-kgym-api-endpoint.example.com"
# os.environ["KBDR_BUCKET_NAME"] = "your-gcs-bucket"
# os.environ["PROJECT_ID"] = "your-gcp-project-id"

# AIDEV-NOTE: BASE_PATH must be on sys.path so Kernel_Agent and KBDr_Runner are importable
sys.path.insert(0, os.environ["BASE_PATH"])

from patch_minimizer.core.edit import Edit
from patch_minimizer.agent_adapters.agent_adapter_base.utils import (
    load_trajectory_file,
)
# AIDEV-NOTE: side-effect import — registers ClassicSWEAgentTrajectoryParser.
import patch_minimizer.agent_adapters.swe_agent_adapter  # noqa: F401
from patch_minimizer.frontends.kernel.cli.minimize_edits import minimize_edits
from patch_minimizer.core.rw_lock import RWLock


# ---------------------------------------------------------------------------
# Configuration — concrete values for this demo
# ---------------------------------------------------------------------------

# AIDEV-NOTE: LINUX_DIR is now constructed from --linux-dir-start at runtime.
# LINUX_DIR = os.path.join(os.getenv("BASE_PATH"), "collection_of_linux_repos/linux-501")

# KBENCH_PATH — the directory containing per-bug JSON files (SyzbotData format)
# and golden_subset.json.
BENCHMARK_FOLDER = os.getenv("KBENCH_PATH")

# AIDEV-NOTE: BUG_ID is now passed via --bug-id CLI arg.
# BUG_ID = "e30eafa41838054ed71bff63e92b4ca38ccd46b2_0"


# ---------------------------------------------------------------------------
# Load bug metadata from the SyzbotData JSON
# ---------------------------------------------------------------------------
def load_bug_data(benchmark_folder: str, bug_id: str) -> tuple[str, str]:
    """Load base_commit and kernel_base_url from the bug's JSON file.

    The JSON files follow the SyzbotData schema (see syzbot_models.py).
    Filenames are ``<bugId>_0.json``.  We read:
      - ``parentOfFixCommit``           → base_commit
            e.g. "dac2a4f663c4bcd75add07aedf9d30c9f8e5ead5"
      - ``crashes[0].kernelSourceGit``  → kernel_base_url
            e.g. "https://git.kernel.org/.../linux.git/log/?id=..."

    Returns:
        (base_commit, kernel_base_url)
    """
    bug_json_path = os.path.join(benchmark_folder, f"{bug_id}.json")
    with open(bug_json_path) as f:
        data = json.load(f)

    base_commit = data["parentOfFixCommit"]
    assert base_commit, f"Bug {bug_id} has no parentOfFixCommit in {bug_json_path}"

    # crashes is stored as a JSON-encoded string in the SyzbotData schema
    crashes = data["crashes"]
    if isinstance(crashes, str):
        crashes = json.loads(crashes)
    kernel_base_url = crashes[0].get("kernelSourceGit", crashes[0].get("kernel-source-git"))

    print(f"Loaded bug from: {bug_json_path}")
    print(f"  bugId:              {bug_id}")
    print(f"  parentOfFixCommit:  {base_commit}")
    print(f"  kernelSourceGit:    {kernel_base_url}\n")
    return base_commit, kernel_base_url


# AIDEV-NOTE: Module-level eager loading commented out — now done at runtime in main().
# BASE_COMMIT, KERNEL_BASE_URL = load_bug_data(BENCHMARK_FOLDER, BUG_ID)
# GOLDEN_SUBSET_PATH = os.path.join(BENCHMARK_FOLDER, "golden_subset.json")


def load_golden_subset(path: str) -> dict:
    """Load golden_subset.json and transform to dict keyed by bug_id.

    Handles both formats:
      - Array: ``{"bugs": [{"id": "...", "image": "...", "kcache": "..."}, ...]}``
      - Dict:  ``{bug_id: {"image": "...", "kcache": "..."}, ...}``

    Returns:
        ``{bug_id: {"image": str, "kcache": str | None}, ...}``
    """
    # AIDEV-NOTE: Same transform as run_minimization_on_results() in minimize_patches.py.
    with open(path) as f:
        raw = json.load(f)

    if "bugs" in raw and isinstance(raw["bugs"], list):
        return {
            entry["id"]: {"image": entry["image"], "kcache": entry.get("kcache")}
            for entry in raw["bugs"]
        }
    return raw


# GOLDEN_SUBSET_DATA = load_golden_subset(GOLDEN_SUBSET_PATH)


# ---------------------------------------------------------------------------
# AIDEV-NOTE: Old hardcoded patch_list code commented out.
# Now using adapter.py's parse_swe_agent_trajectory() + to_patch_list().
# ---------------------------------------------------------------------------
if False:  # noqa — preserved for reference, not dead code removal
    # ---------------------------------------------------------------------------
    # Build the patch_list from in-memory Edit objects
    # ---------------------------------------------------------------------------

    # AIDEV-NOTE: The parent_diff for Node 1 is the git diff produced by applying
    # Node 0's edits to the repo at base_commit. This is the exact diff string
    # from the agent's real run (tree_4, run_10000, gemini-3-pro).
    _NODE_1_PARENT_DIFF = (
        "diff --git a/collection_of_linux_repos/linux-522/fs/nilfs2/the_nilfs.c"
        " b/collection_of_linux_repos/linux-522/fs/nilfs2/the_nilfs.c\n"
        "index cb01ea81724d..945605ca7a60 100644\n"
        "--- a/collection_of_linux_repos/linux-522/fs/nilfs2/the_nilfs.c\n"
        "+++ b/collection_of_linux_repos/linux-522/fs/nilfs2/the_nilfs.c\n"
        "@@ -705,14 +705,13 @@ int init_nilfs(struct the_nilfs *nilfs, struct super_block *sb)\n"
        " \tint blocksize;\n"
        " \tint err;\n"
        " \n"
        "-\tdown_write(&nilfs->ns_sem);\n"
        "-\n"
        " \tblocksize = sb_min_blocksize(sb, NILFS_MIN_BLOCK_SIZE);\n"
        " \tif (!blocksize) {\n"
        " \t\tnilfs_err(sb, \"unable to set blocksize\");\n"
        "-\t\terr = -EINVAL;\n"
        "-\t\tgoto out;\n"
        "+\t\treturn -EINVAL;\n"
        " \t}\n"
        "+\n"
        "+\tdown_write(&nilfs->ns_sem);\n"
        " \terr = nilfs_load_super_block(nilfs, sb, blocksize, &sbp);\n"
        " \tif (err)\n"
        " \t\tgoto out;\n"
        "@@ -747,12 +746,14 @@ int init_nilfs(struct the_nilfs *nilfs, struct super_block *sb)\n"
        " \t\t\tgoto failed_sbh;\n"
        " \t\t}\n"
        " \t\tnilfs_release_super_block(nilfs);\n"
        "+\t\tup_write(&nilfs->ns_sem);\n"
        "+\n"
        " \t\tif (!sb_set_blocksize(sb, blocksize)) {\n"
        " \t\t\tnilfs_err(sb, \"bad blocksize %d\", blocksize);\n"
        "-\t\t\terr = -EINVAL;\n"
        "-\t\t\tgoto out;\n"
        "+\t\t\treturn -EINVAL;\n"
        " \t\t}\n"
        " \n"
        "+\t\tdown_write(&nilfs->ns_sem);\n"
        " \t\terr = nilfs_load_super_block(nilfs, sb, blocksize, &sbp);\n"
        " \t\tif (err)\n"
        " \t\t\tgoto out;\n"
    )


    def build_patch_list() -> List[Tuple[str | None, List[Edit]]]:
        """Build the patch_list from in-memory Edit objects.

        AIDEV-NOTE: These are the exact edits from a real agent run on bug
        e30eafa41838054ed71bff63e92b4ca38ccd46b2 (gemini-3-pro, tree_4, run_10000).

        The returned patch_list has format: List[(parent_diff | None, List[Edit])]
          - parent_diff: a git-diff string applied *before* the edits (or None)
          - Each tuple is one "node" (step) in the agent's trajectory

        Node 0 (parent_diff=None): 2 edits — the real deadlock fix
          - Edit 0: Move down_write(&nilfs->ns_sem) after sb_min_blocksize()
          - Edit 1: Release ns_sem before sb_set_blocksize(), re-acquire after

        Node 1 (parent_diff=Node 0's diff): 2 edits — cosmetic comment additions
          - Edit 0: Add comment explaining lock ordering for sb_min_blocksize
          - Edit 1: Add inline comment on up_write() documenting deadlock avoidance

        After minimization: 4 → 1 edit (only Edit 0 from Node 0 survives).
        """
        # --- Node 0: The real fix (reorder lock acquisition) ---
        node_0_edit_0 = Edit(
            filename="fs/nilfs2/the_nilfs.c",
            before=(
                "\tdown_write(&nilfs->ns_sem);\n"
                "\n"
                "\tblocksize = sb_min_blocksize(sb, NILFS_MIN_BLOCK_SIZE);\n"
                "\tif (!blocksize) {\n"
                '\t\tnilfs_err(sb, "unable to set blocksize");\n'
                "\t\terr = -EINVAL;\n"
                "\t\tgoto out;\n"
                "\t}\n"
                "\terr = nilfs_load_super_block(nilfs, sb, blocksize, &sbp);"
            ),
            after=(
                "\tblocksize = sb_min_blocksize(sb, NILFS_MIN_BLOCK_SIZE);\n"
                "\tif (!blocksize) {\n"
                '\t\tnilfs_err(sb, "unable to set blocksize");\n'
                "\t\treturn -EINVAL;\n"
                "\t}\n"
                "\n"
                "\tdown_write(&nilfs->ns_sem);\n"
                "\terr = nilfs_load_super_block(nilfs, sb, blocksize, &sbp);"
            ),
            explanation=(
                "\nMove the initial call to sb_min_blocksize() to the beginning of "
                "init_nilfs(), before down_write(&nilfs->ns_sem) is called. This "
                "prevents holding the semaphore while acquiring "
                "mapping.invalidate_lock inside sb_min_blocksize(), breaking part "
                "of the circular dependency.\n"
            ),
            global_edit_idx=0,
        )

        node_0_edit_1 = Edit(
            filename="fs/nilfs2/the_nilfs.c",
            before=(
                "\t\tnilfs_release_super_block(nilfs);\n"
                "\t\tif (!sb_set_blocksize(sb, blocksize)) {\n"
                '\t\t\tnilfs_err(sb, "bad blocksize %d", blocksize);\n'
                "\t\t\terr = -EINVAL;\n"
                "\t\t\tgoto out;\n"
                "\t\t}\n"
                "\n"
                "\t\terr = nilfs_load_super_block(nilfs, sb, blocksize, &sbp);"
            ),
            after=(
                "\t\tnilfs_release_super_block(nilfs);\n"
                "\t\tup_write(&nilfs->ns_sem);\n"
                "\n"
                "\t\tif (!sb_set_blocksize(sb, blocksize)) {\n"
                '\t\t\tnilfs_err(sb, "bad blocksize %d", blocksize);\n'
                "\t\t\treturn -EINVAL;\n"
                "\t\t}\n"
                "\n"
                "\t\tdown_write(&nilfs->ns_sem);\n"
                "\t\terr = nilfs_load_super_block(nilfs, sb, blocksize, &sbp);"
            ),
            explanation=(
                "\nTemporarily release the ns_sem semaphore before calling "
                "sb_set_blocksize() and re-acquire it immediately after. This "
                "avoids holding the semaphore while acquiring "
                "mapping.invalidate_lock, which breaks the circular locking "
                "dependency.\n"
            ),
            global_edit_idx=1,
        )

        # --- Node 1: Cosmetic comment additions (applied on top of Node 0) ---
        node_1_edit_0 = Edit(
            filename="fs/nilfs2/the_nilfs.c",
            before=(
                "\tblocksize = sb_min_blocksize(sb, NILFS_MIN_BLOCK_SIZE);\n"
                "\tif (!blocksize) {\n"
                '\t\tnilfs_err(sb, "unable to set blocksize");\n'
                "\t\treturn -EINVAL;\n"
                "\t}\n"
                "\n"
                "\tdown_write(&nilfs->ns_sem);"
            ),
            after=(
                "\t/*\n"
                "\t * Call sb_min_blocksize() before acquiring ns_sem to avoid a circular\n"
                "\t * locking dependency with mapping->invalidate_lock.\n"
                "\t */\n"
                "\tblocksize = sb_min_blocksize(sb, NILFS_MIN_BLOCK_SIZE);\n"
                "\tif (!blocksize) {\n"
                '\t\tnilfs_err(sb, "unable to set blocksize");\n'
                "\t\treturn -EINVAL;\n"
                "\t}\n"
                "\n"
                "\tdown_write(&nilfs->ns_sem);"
            ),
            explanation=(
                "\nAdd a comment to ensure sb_min_blocksize() remains before "
                "acquiring ns_sem, preserving the fix for the circular locking "
                "dependency with mapping->invalidate_lock.\n"
            ),
            global_edit_idx=2,
        )

        node_1_edit_1 = Edit(
            filename="fs/nilfs2/the_nilfs.c",
            before=(
                "\t\tnilfs_release_super_block(nilfs);\n"
                "\t\tup_write(&nilfs->ns_sem);\n"
                "\n"
                "\t\tif (!sb_set_blocksize(sb, blocksize)) {"
            ),
            after=(
                "\t\tnilfs_release_super_block(nilfs);\n"
                "\t\tup_write(&nilfs->ns_sem); /* Release ns_sem to avoid deadlock */\n"
                "\n"
                "\t\tif (!sb_set_blocksize(sb, blocksize)) {"
            ),
            explanation=(
                "\nAdd a comment to document the explicit release of ns_sem before "
                "calling sb_set_blocksize, ensuring the deadlock avoidance mechanism "
                "is clear and preserved.\n"
            ),
            global_edit_idx=3,
        )

        patch_list: List[Tuple[str | None, List[Edit]]] = [
            (None, [node_0_edit_0, node_0_edit_1]),                # Node 0: fix edits
            (_NODE_1_PARENT_DIFF, [node_1_edit_0, node_1_edit_1]), # Node 1: comment edits
        ]

        # Print what we built
        total_edits = sum(len(edits) for _, edits in patch_list)
        print(f"Built patch_list in-memory:")
        print(f"  Nodes: {len(patch_list)}, Total edits: {total_edits}")
        for i, (parent_diff, edits) in enumerate(patch_list):
            has_diff = "yes" if parent_diff else "None"
            print(f"  Node {i}: {len(edits)} edits, parent_diff={has_diff}")
            for e in edits:
                print(f"    [{e.global_edit_idx}] {e.filename}: {(e.explanation or '').strip()[:70]}")
        print()

        return patch_list


# ---------------------------------------------------------------------------
# Parallel minimization — Queue + ThreadPoolExecutor pattern
# (same as minimize_patches.py:628-757)
# ---------------------------------------------------------------------------

# AIDEV-NOTE: Worker function for parallel minimization. Acquires a linux repo
# from the shared queue, runs minimize_edits(), and returns the repo in finally.
def _minimize_one_bug(bug_config: dict, linux_dir_queue: Queue, shared_config: dict) -> dict | None:
    """Worker: acquire a linux repo from queue, run minimize_edits, return repo."""
    linux_dir, code_dir_lock = linux_dir_queue.get()
    try:
        base_commit, kernel_base_url = load_bug_data(
            shared_config["benchmark_folder"], bug_config["bug_id"]
        )
        result = minimize_edits(
            bug_id=bug_config["bug_id"],
            patch_list=bug_config["patch_list"],
            base_commit=base_commit,
            linux_dir=linux_dir,
            code_dir_lock=code_dir_lock,
            benchmark_folder=shared_config["benchmark_folder"],
            golden_subset_data=shared_config["golden_subset_data"],
            kernel_base_url=kernel_base_url,
            run_jobs=shared_config["run_jobs"],
            syzkaller_rollback_tag=shared_config.get("syzkaller_rollback_tag", "master"),
            minimize_at=shared_config.get("minimize_at", "edit"),
            coupling_map=bug_config.get("coupling_map"),
            save_dir=bug_config.get("save_dir"),
        )
        return result
    except Exception as e:
        print(f"Error minimizing {bug_config['bug_id']}: {e}")
        traceback.print_exc()
        return None
    finally:
        linux_dir_queue.put((linux_dir, code_dir_lock))


def run_parallel(
    bug_configs: list[dict],
    linux_dir_indices: list[int],
    num_parallel: int,
    shared_config: dict,
) -> dict:
    """Run minimize_edits() on multiple bugs in parallel.

    AIDEV-NOTE: Same Queue-based resource pool pattern as minimize_patches.py.
    Requires len(linux_dir_indices) >= num_parallel.
    """
    base_path = os.getenv("BASE_PATH")

    # Build queue — same pattern as minimize_patches.py:628-631
    linux_dir_queue: Queue = Queue()
    for idx in linux_dir_indices:
        path = os.path.join(base_path, "collection_of_linux_repos", f"linux-{idx}")
        linux_dir_queue.put((path, RWLock()))

    results = {}
    with ThreadPoolExecutor(max_workers=num_parallel) as executor:
        futures = {
            executor.submit(_minimize_one_bug, cfg, linux_dir_queue, shared_config): cfg["bug_id"]
            for cfg in bug_configs
        }
        for future in as_completed(futures):
            bug_id = futures[future]
            result = future.result()
            results[bug_id] = result
            status = result["reduction"] if result else "FAILED"
            print(f"[{len(results)}/{len(bug_configs)}] {bug_id}: {status}")

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _print_result(result: dict) -> None:
    """Print a single minimization result."""
    print(f"  Reduction       : {result['reduction']}")
    print(f"  Original edits  : {result['original_edit_count']}")
    print(f"  Minimized edits : {result['minimized_edit_count']}")
    print(f"  Feedback stats  : {result['feedback_stats']}")
    print(f"\n  Minimized patch (git diff):\n")
    print(result["minimized_patch"] or "  (empty — all edits were removed)")
    print("\n  Surviving edits (short_path):")
    for item in result["short_path"]:
        print(f"    {item}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone minimize_edits() demo")
    parser.add_argument("--traj-json", required=True, help="Path to Karena traj.json")
    parser.add_argument("--bug-id", required=True, help="Bug ID for minimize_edits()")
    parser.add_argument("--parallel", type=int, default=0,
                        help="Number of parallel workers (0 = single-bug mode)")
    parser.add_argument("--linux-dir-start", type=int, default=501)
    parser.add_argument("--linux-dir-end", type=int, default=510)
    parser.add_argument("--run-jobs", action="store_true", default=True)
    parser.add_argument("--no-run-jobs", dest="run_jobs", action="store_false")
    parser.add_argument("--minimize-at", default="edit", choices=["edit", "node"])
    args = parser.parse_args()

    # AIDEV-NOTE: Load data at runtime (no more module-level eager loading)
    bug_id = args.bug_id
    golden_subset_data = load_golden_subset(os.path.join(BENCHMARK_FOLDER, "golden_subset.json"))
    candidates, patch_lists = load_trajectory_file(args.traj_json)

    if not patch_lists:
        print(f"No candidates found in {args.traj_json}")
        return

    print(f"Loaded {len(patch_lists)} candidates from {args.traj_json}")
    for i, cand in enumerate(candidates):
        total = sum(len(r.edits) for r in cand.revisions)
        print(f"  Candidate {i}: {len(cand.revisions)} revisions, {total} edits, success_step={cand.success_step}")
    print()

    if args.parallel > 0:
        # ---- Parallel mode ------------------------------------------------
        # AIDEV-NOTE: Multi-bug parallel minimization using Queue-based resource
        # pool. Each worker gets exclusive access to one linux repo. Same pattern
        # as minimize_patches.py run_minimization_on_results().
        shared_config = {
            "benchmark_folder": BENCHMARK_FOLDER,
            "golden_subset_data": golden_subset_data,
            "run_jobs": args.run_jobs,
            "syzkaller_rollback_tag": "master",
            "minimize_at": args.minimize_at,
        }
        # AIDEV-NOTE: One bug_config per candidate from the trajectory.
        bug_configs = [
            {"bug_id": bug_id, "patch_list": pl}
            for pl in patch_lists
        ]
        linux_indices = list(range(args.linux_dir_start, args.linux_dir_end + 1))
        assert args.parallel == len(linux_indices), (
            f"--parallel ({args.parallel}) must equal number of linux dirs "
            f"(--linux-dir-start..--linux-dir-end = {len(linux_indices)})"
        )

        print("=" * 60)
        print("Running parallel minimize_edits()")
        print(f"  workers        : {args.parallel}")
        print(f"  linux repos    : linux-{args.linux_dir_start}..linux-{args.linux_dir_end}")
        print(f"  candidates     : {len(bug_configs)}")
        print(f"  run_jobs       : {args.run_jobs}")
        print(f"  minimize_at    : {args.minimize_at}")
        print("=" * 60 + "\n")

        results = run_parallel(bug_configs, linux_indices, args.parallel, shared_config)

        print("\n" + "=" * 60)
        print("RESULTS SUMMARY")
        print("=" * 60)
        for bid, result in results.items():
            if result:
                print(f"\n  --- {bid} ---")
                _print_result(result)
            else:
                print(f"\n  --- {bid} --- FAILED")

    else:
        # ---- Single-bug mode ------------------------------------------------
        # AIDEV-NOTE: Construct linux_dir from --linux-dir-start (no hardcoded LINUX_DIR)
        linux_dir = os.path.join(
            os.getenv("BASE_PATH"), "collection_of_linux_repos", f"linux-{args.linux_dir_start}"
        )
        base_commit, kernel_base_url = load_bug_data(BENCHMARK_FOLDER, bug_id)

        for i, patch_list in enumerate(patch_lists):
            total_edits = sum(len(edits) for _, edits in patch_list)

            print("=" * 60)
            print(f"Minimizing candidate {i + 1}/{len(patch_lists)}")
            print(f"  bug_id       : {bug_id}")
            print(f"  linux_dir    : {linux_dir}")
            print(f"  base_commit  : {base_commit}")
            print(f"  run_jobs     : {args.run_jobs}")
            print(f"  minimize_at  : {args.minimize_at}")
            print(f"  # nodes      : {len(patch_list)}")
            print(f"  # total edits: {total_edits}")
            print("=" * 60 + "\n")

            result = minimize_edits(
                bug_id=bug_id,
                patch_list=patch_list,
                base_commit=base_commit,
                linux_dir=linux_dir,
                code_dir_lock=RWLock(),
                benchmark_folder=BENCHMARK_FOLDER,
                golden_subset_data=golden_subset_data,
                kernel_base_url=kernel_base_url,
                run_jobs=args.run_jobs,
                syzkaller_rollback_tag="master",
                minimize_at=args.minimize_at,
            )

            print("\n" + "=" * 60)
            print(f"RESULTS — Candidate {i + 1}/{len(patch_lists)}")
            print("=" * 60)
            _print_result(result)


if __name__ == "__main__":
    main()
