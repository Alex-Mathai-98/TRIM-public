from __future__ import annotations
from typing import Dict, Any, Optional, Tuple, List, TYPE_CHECKING
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from queue import Queue
import logging
import os
import json
import threading

from patch_minimizer.benchmark_utils.kernel.util_funcs import get_base_commit
from patch_minimizer.agent_adapters.kagent_adapter import load_all_trajectories
from patch_minimizer.core.rw_lock import RWLock, GitRWLock
from patch_minimizer.strategies.aggregate_feedback_stats import aggregate_feedback_stats_from_summary
from patch_minimizer.strategies.minimize_core import (
    _save_summary_atomic,
    serialize_edit_path,
)
# AIDEV-NOTE: Lazy import SolutionMinimizationBase to avoid circular import with tools/__init__.py
# (tools/__init__.py → ToolCaller → sol_minimize → solution_minimization_base → tools.generate_patch.edit → tools/__init__.py)
from patch_minimizer.strategies.environments import (
    DryRunEnvironment,
    FeedbackReceiver,
    GitDiffEnvironment,
)
from patch_minimizer.frontends.kernel.environment import KernelEnvironment
from patch_minimizer.strategies.feedback_stats import _summarize_feedback_stats
from patch_minimizer.frontends.kernel.utils.results_discovery import (
    get_bug_ids_from_results,
    get_folder_paths_for_bug,
    folder_infos_from_success_paths as _folder_infos_from_success_paths,
)
from patch_minimizer.benchmark_utils.kernel.bug_data import BugData

if TYPE_CHECKING:
    from patch_minimizer.strategies.algorithms.solution_minimization_base import SolutionMinimizationBase

class ToolSolnMinimize():
    """ A tool to minimize the solution patch. """

    def __init__(self, bug_id, folder_path, folder_name,
            benchmark_folder, golden_subset_data, linux_dir, code_dir_lock, model_id, run_jobs=False, kernel_base_url=None, syzkaller_rollback_tag:(str|None)=None, minimize_at:str="edit", node_refinement:bool=True, edit_refinement:bool=True, use_result_cache:bool=True):
        self.bug_id = bug_id
        self.folder_path = folder_path
        self.folder_name = folder_name
        self.benchmark_folder = benchmark_folder
        self.golden_subset_data = golden_subset_data
        self.linux_dir = linux_dir
        self.code_dir_lock = code_dir_lock
        self.model_id = model_id
        self.run_jobs = run_jobs
        self.kernel_base_url = kernel_base_url
        self.syzkaller_rollback_tag = syzkaller_rollback_tag
        # AIDEV-NOTE: minimize_at controls granularity: "node" (patch-level) or "edit" (individual edit-level)
        assert minimize_at in ("node", "edit"), f"minimize_at must be 'node' or 'edit', got '{minimize_at}'"
        self.minimize_at = minimize_at
        self.node_refinement = node_refinement
        self.edit_refinement = edit_refinement
        self.use_result_cache = use_result_cache
    
    def setup(self):
        """Load data and create shared objects for minimization.

        Returns:
            tuple: (succ_edit_paths, solution_trajectories, smb, feedback_aggregator, cleanup)
            None: if no successful paths found or pkl missing
        """
        base_commit = get_base_commit(self.benchmark_folder,
                                bug_id=self.bug_id,
                                parent_commit_flag=True)

        # AIDEV-NOTE: Extract kernel_base_url from bug data if not provided in constructor
        kernel_base_url = self.kernel_base_url
        if kernel_base_url is None:
            bug_path = os.path.join(self.benchmark_folder, self.bug_id + ".json")
            bug_data = BugData.from_json_file(bug_path)
            kernel_base_url = bug_data.get_kernel_source_git()

        # AIDEV-NOTE: Handle missing .pkl files from incomplete benchmark runs
        try:
            succ_edit_paths, solution_trajectories = load_all_trajectories(
                folder_path=self.folder_path,
                bug_id=self.bug_id,
                model_id=self.model_id,
                base_commit=base_commit,
            )
        except AssertionError as e:
            print(f"Cannot load LLM response history for bug {self.bug_id}: {e}")
            print(f"This indicates an incomplete benchmark run (missing .pkl file or node attributes)")
            return None

        if len(solution_trajectories) == 0:
            print(f"No successful solution paths found for bug {self.bug_id}.")
            return None

        # AIDEV-NOTE: Lazy import to avoid circular import with tools/__init__.py
        from patch_minimizer.strategies.algorithms.node.node_minimizer import NodeMinimizer
        from patch_minimizer.strategies.algorithms.edit.edit_minimizer import EditMinimizer

        # AIDEV-NOTE: Create minimizer once per bug (not per trajectory) to avoid redundant git resets
        # AIDEV-NOTE: Use getLogger (not Logger) to inherit Hydra's root logger configuration
        # This ensures minimization logs go to both console AND run_benchmark.log file
        logger = logging.getLogger(__file__)
        logger.setLevel(logging.INFO)

        # AIDEV-NOTE: Conditional instantiation based on minimize_at granularity
        if self.minimize_at == "node":
            smb = NodeMinimizer(self.linux_dir, self.code_dir_lock, base_commit, logger, kernel_base_url=kernel_base_url, syzkaller_rollback_tag=self.syzkaller_rollback_tag, use_result_cache=self.use_result_cache)
        else:
            smb = EditMinimizer(self.linux_dir, self.code_dir_lock, base_commit, logger, kernel_base_url=kernel_base_url, syzkaller_rollback_tag=self.syzkaller_rollback_tag, use_result_cache=self.use_result_cache)

        # AIDEV-NOTE: Instantiate cleanup for execution-based strategies
        cleanup = None
        if "execution" in self.folder_path.lower():
            raise AssertionError(
                "TracePrintkInstrumentation cleanup is not supported in standalone mode. "
                "Use the Kernel_Agent version of ToolSolnMinimize for execution-based strategies."
            )

        # AIDEV-NOTE: Construct environment once — KernelEnvironment for real runs, DryRunEnvironment otherwise
        # AIDEV-NOTE: Wrap in FeedbackReceiver to add patch_stats to feedback
        if self.run_jobs:
            runtime_env = KernelEnvironment(
                self.benchmark_folder, self.bug_id, self.golden_subset_data,
                self.linux_dir, logger, self.syzkaller_rollback_tag,
            )
        else:
            runtime_env = DryRunEnvironment()
        git_diff_env = GitDiffEnvironment()
        feedback_aggregator = FeedbackReceiver(runtime_env, git_diff_env=git_diff_env)

        return succ_edit_paths, solution_trajectories, smb, feedback_aggregator, cleanup

    def minimize_one_path(self, edit_path, solution_trajectory, smb, feedback_aggregator, cleanup, coupling_map=None):
        """Minimize a single successful solution path.

        Args:
            edit_path: List of (parent_diff, List[Edit]) tuples for one trajectory
            solution_trajectory: Trajectory node names
            smb: SolutionMinimizationBase instance
            feedback_aggregator: FeedbackReceiver
            cleanup: InstrumentationCleanUp or None
            coupling_map: CouplingMap for this path (None if no coupling)

        Returns:
            tuple: (short_path, minimized_patch, feedback_stats_dict)
        """
        smb.feedback_stats = {}
        orig_length = len(edit_path)
        if orig_length == 1:
            print(f"Only one node")

        node_name = f"{self.model_id}__{self.bug_id}/{solution_trajectory[-1]}"
        # AIDEV-NOTE: Dispatch to node-level or edit-level minimization based on minimize_at
        minimize_fn = smb.find_independent_nodes if self.minimize_at == "node" else smb.find_independent_edits

        kwargs = dict(
            bug_id=self.bug_id,
            feedback_aggregator=feedback_aggregator,
            patch_list=edit_path,
            phase_2=True,
            node_refinement=self.node_refinement,
            save_dir=self.folder_path,
            node_name=node_name,
            cleanup=cleanup,
        )
        if self.minimize_at == "edit":
            kwargs["coupling_map"] = coupling_map
            kwargs["edit_refinement"] = self.edit_refinement

        short_path, minimized_patch = minimize_fn(**kwargs)

        new_length = len(short_path)
        # AIDEV-NOTE: At node level, 1 node in → 1 node out. At edit level, 1 node with N edits
        # can flatten to N items, so assertion only valid for node-level minimization.
        if orig_length == 1 and self.minimize_at == "node":
            assert new_length == 1

        return short_path, minimized_patch, dict(smb.feedback_stats)

    def minimize_solution(self):
        """Minimize the solution patch.

        Returns:
            tuple: (short_paths, minimized_patches, succ_edit_paths, solution_trajectories, feedback_stats)
                   if minimization succeeds, or (False, False, False, False) if no successful
                   solution paths are found (including when .pkl file is missing due to
                   incomplete benchmark run)
        """
        setup = self.setup()
        if setup is None:
            return False, False, False, False

        succ_edit_paths, solution_trajectories, smb, feedback_aggregator, cleanup = setup

        short_paths = []
        minimized_patches = []
        all_stats = []

        for edit_path, solution_trajectory in zip(succ_edit_paths, solution_trajectories):
            short_path, minimized_patch, stats = self.minimize_one_path(
                edit_path, solution_trajectory, smb, feedback_aggregator, cleanup,
            )
            short_paths.append(short_path)
            minimized_patches.append(minimized_patch)
            all_stats.append(stats)

        # AIDEV-NOTE: Merge all trajectory stats — integer counters are summed,
        # non-integer metadata (lists, etc.) keeps the last trajectory's value.
        merged_stats = {}
        for s in all_stats:
            for k, v in s.items():
                if isinstance(v, (int, float)):
                    merged_stats[k] = merged_stats.get(k, 0) + v
                else:
                    merged_stats[k] = v

        feedback_stats = _summarize_feedback_stats(merged_stats)
        return short_paths, minimized_patches, succ_edit_paths, solution_trajectories, feedback_stats

    # =========================================================================
    # ENVIRONMENT UPDATE METHODS
    # =========================================================================

    @staticmethod
    def update_environment(
        result: Dict[str, Any],
        env: "Environment",
        node_name: str,
    ) -> None:
        """
        Update environment based on solution minimization result.

        Args:
            result: Dictionary with solution minimization result
            env: Environment instance to update
            node_name: Node name for tracking
        """
        pass

    @staticmethod
    def get_schema():
        """Get JSON schema describing tool parameters and methods."""
        return {
            "name": "solution_minimizer",
            "description": "Minimize solution patches to the bare minimum required to fix kernel bugs. Analyzes successful edit paths and finds independent edits.",
            "methods": {
                "minimize_solution": {
                    "description": "Minimize the solution patch by finding independent edits",
                    "parameters": {},
                    "returns": "Tuple of (short_paths, minimized_patches, succ_edit_paths, solution_trajectories) or None"
                }
            },
            "config_params": {
                "bug_id": {"type": "string", "description": "Kernel bug identifier", "required": True},
                "folder_path": {"type": "string", "description": "Path to folder containing solution data", "required": True},
                "folder_name": {"type": "string", "description": "Name of the folder", "required": True},
                "benchmark_folder": {"type": "string", "description": "Path to kernel benchmark folder", "required": True},
                "golden_subset_data": {"type": "object", "description": "Golden subset data for validation", "required": True},
                "linux_dir": {"type": "string", "description": "Path to Linux repository directory", "required": True},
                "code_dir_lock": {"type": "object", "description": "Lock object for code directory access", "required": True},
                "model_id": {"type": "string", "description": "Model identifier used for solution generation", "required": True},
                "run_jobs": {"type": "boolean", "default": False, "description": "Whether to run validation jobs"},
                "kernel_base_url": {"type": "string", "description": "Base URL for kernel git repository (optional, extracted from bug data if not provided)", "required": False}
            }
        }


def minimize_bug_solution(
    bug_id: str,
    folder_path: str,
    folder_name: str,
    benchmark_folder: str,
    golden_subset_data: dict,
    linux_dir_queue: Queue,
    model_id: str,
    run_jobs: bool = False,
    syzkaller_rollback_tag: (str | None) = None,
    minimize_at: str = "edit",
    node_refinement: bool = True,
    edit_refinement: bool = True,
    use_result_cache: bool = True,
) -> Optional[dict]:
    """
    Minimize solution for a single bug using ToolSolnMinimize.

    Args:
        bug_id: The bug identifier
        folder_path: Full path to the bug's results folder
        folder_name: Name of the folder (tree_X/bug_id format)
        benchmark_folder: Path to the benchmark folder
        golden_subset_data: Dictionary containing golden subset data
        linux_dir_queue: Thread-safe queue containing (linux_dir, code_dir_lock) tuples
        model_id: Model identifier used for naming
        run_jobs: Whether to run KBDR jobs for validation

    Returns:
        Dictionary with minimization results or None if failed
    """
    # AIDEV-NOTE: Allocate a (linux_dir, code_dir_lock) tuple from queue (blocks if none available)
    linux_dir, code_dir_lock = linux_dir_queue.get()

    try:
        print(f"\n{'='*80}")
        print(f"Minimizing solution for bug: {bug_id} ({folder_name})")
        print(f"Using Linux repo: {linux_dir}")
        print(f"{'='*80}")

        # AIDEV-NOTE: Call ToolSolnMinimize directly (no MinimizationAgent/EnvironmentSnapshot).
        # Matches the pattern from Kernel_Agent/deployment_related_code/run_failed_minimization.py.
        tool = ToolSolnMinimize(
            bug_id=bug_id,
            folder_path=folder_path,
            folder_name=folder_name,
            benchmark_folder=benchmark_folder,
            golden_subset_data=golden_subset_data,
            linux_dir=linux_dir,
            code_dir_lock=code_dir_lock,
            model_id=model_id,
            run_jobs=run_jobs,
            syzkaller_rollback_tag=syzkaller_rollback_tag,
            minimize_at=minimize_at,
            node_refinement=node_refinement,
            edit_refinement=edit_refinement,
            use_result_cache=use_result_cache,
        )
        result = tool.minimize_solution()

        if result and result == (False, False, False, False) :
            print(f"No successful solution paths found for {bug_id} ({folder_name})")
            return -1
        elif result is None:
            return None

        # Handle both old 4-tuple and new 5-tuple returns
        if len(result) == 5:
            short_paths, minimized_patches, succ_edit_paths, solution_trajectories, feedback_stats = result
        else:
            short_paths, minimized_patches, succ_edit_paths, solution_trajectories = result
            feedback_stats = {"total_feedbacks": 0, "detailed": {}}

        # AIDEV-NOTE: Calculate best reduction extent (smallest minimized path among all solutions)
        best_reduction_extent = None
        best_minimization = float('inf')

        for short_path, orig_path in zip(short_paths, succ_edit_paths):
            # AIDEV-NOTE: For edit-level minimization, count total edits across all nodes.
            # orig_path is List[(parent_diff, List[Edit])]; short_path is flattened to [[edit], ...]
            if minimize_at == "edit":
                orig_len = sum(len(node[1]) for node in orig_path if node[1] is not None)
            else:
                # Node-level: count nodes
                orig_len = len(orig_path)
            min_len = len(short_path)

            # Track the best reduction (smallest minimized path)
            if min_len < best_minimization:
                best_minimization = min_len
                best_reduction_extent = f"{orig_len}_to_{min_len}"

        # AIDEV-NOTE: Save inside the bug folder, not at the tree level
        bug_dir = Path(folder_path) / f"{model_id}__{bug_id}"
        bug_dir.mkdir(parents=True, exist_ok=True)
        output_file = bug_dir / "minimization_results.json"
        minimization_data = {
            "bug_id": bug_id,
            "folder_name": folder_name,
            "num_solutions": len(short_paths),
            "best_reduction": best_reduction_extent,
            "feedback_stats": feedback_stats,
            "solutions": []
        }

        for idx, (short_path, minimized_patch, orig_path, trajectory) in enumerate(
            zip(short_paths, minimized_patches, succ_edit_paths, solution_trajectories)
        ):
            # AIDEV-NOTE: Convert Edit objects to JSON-serializable dicts
            minimization_data["solutions"].append({
                "solution_index": idx,
                "original_path_length": len(orig_path),
                "minimized_path_length": len(short_path),
                "minimized_patch": minimized_patch,
                "trajectory": trajectory,
                "short_path": serialize_edit_path(short_path),
                "original_path": serialize_edit_path(orig_path)
            })

        with open(output_file, 'w') as f:
            json.dump(minimization_data, f, indent=2)

        print(f"Minimization completed for {bug_id} ({folder_name})")
        print(f"  - Solutions minimized: {len(short_paths)}")
        print(f"  - Best reduction: {best_reduction_extent}")
        print(f"  - Results saved to: {output_file}")

        return minimization_data

    except Exception as e:
        print(f"Error minimizing {bug_id} ({folder_name}): {e}")
        import traceback
        traceback.print_exc()

        # AIDEV-NOTE: Force-release write lock if stuck (non-reentrant lock may be held).
        # Must be w_release(), NOT the raw w_lock.release(): the raw form skips
        # `self._w_lock_owner = None`, and w_acquire_nowait() checks that field first —
        # so a reused ThreadPoolExecutor thread drawing this repo again would hit a false
        # "Deadlock detected" on a lock that is actually free. is_w_locked() is exactly
        # w_lock.locked(), so the guard is unchanged.
        try:
            if code_dir_lock.is_w_locked():
                code_dir_lock.w_release()
                print(f"Force-released write lock for {linux_dir}")
        except:
            pass  # Best effort

        # AIDEV-NOTE: Clean git state before returning repo to queue
        try:
            import subprocess
            git_dir = os.path.join(linux_dir, ".git")
            index_lock = os.path.join(git_dir, "index.lock")
            if os.path.exists(index_lock):
                os.remove(index_lock)
            subprocess.run(["git", "--git-dir", git_dir, "--work-tree", linux_dir,
                           "reset", "--hard"], capture_output=True, timeout=30)
            subprocess.run(["git", "--git-dir", git_dir, "--work-tree", linux_dir,
                           "clean", "-fd"], capture_output=True, timeout=30)
        except:
            pass  # Best effort - repo may be in bad state but at least it's returned

        return None

    finally:
        # AIDEV-NOTE: Always return the (linux_dir, code_dir_lock) tuple to queue for reuse
        linux_dir_queue.put((linux_dir, code_dir_lock))
        print(f"Released Linux repo back to queue: {linux_dir}")


def run_minimization_on_results(
    results_dir: str,
    benchmark_folder: str,
    golden_subset_path: str,
    linux_dirs: List[str],
    model_id: str,
    bug_ids: Optional[List[str]] = None,
    num_parallel: int = 1,
    run_jobs: bool = False,
    syzkaller_rollback_tag: (str|None) = None,
    retry_failed_only: bool = False,
    minimize_at: str = "edit",
    node_refinement: bool = True,
    edit_refinement: bool = True,
    use_result_cache: bool = True,
    success_paths: Optional[Dict[str, List[str]]] = None,
) -> dict:
    """
    Run minimization on benchmark results.

    This is the core reusable function that can be called from other scripts
    (e.g., run_benchmark.py) or used standalone via the CLI.

    Args:
        results_dir: Path to the results directory containing bug solutions
        benchmark_folder: Path to benchmark folder
        golden_subset_path: Path to golden subset JSON file
        linux_dirs: List of Linux repository paths for parallel processing
        model_id: Model ID used in the experiment
        bug_ids: List of specific bug IDs to minimize (None = all bugs)
        num_parallel: Number of bugs to process in parallel
        run_jobs: Whether to run KBDR jobs to validate minimized patches

    Returns:
        Dictionary with minimization summary

    Note:
        Requires len(linux_dirs) >= num_parallel to avoid blocking.
        Each worker gets exclusive access to one (linux_dir, code_dir_lock) tuple.
        The code_dir_lock prevents git conflicts within each specific Linux repo.
    """
    results_dir_path = Path(results_dir).resolve()
    if not results_dir_path.exists():
        raise ValueError(f"Results directory does not exist: {results_dir_path}")

    # AIDEV-NOTE: Validate we have enough Linux repos for parallel processing
    if len(linux_dirs) < num_parallel:
        raise ValueError(
            f"Insufficient Linux repositories for parallel processing: "
            f"need at least {num_parallel} repos but only {len(linux_dirs)} provided. "
            f"Either reduce num_parallel or provide more Linux repos."
        )

    print("=" * 80)
    print("Patch Minimization Configuration")
    print("=" * 80)
    print(f"Results directory: {results_dir_path}")
    print(f"Benchmark path: {benchmark_folder}")
    print(f"Model ID: {model_id}")
    print(f"Run KBDR jobs: {run_jobs}")
    print(f"Parallel workers: {num_parallel}")
    print(f"Linux repositories: {len(linux_dirs)}")
    print("=" * 80)

    # Validate paths
    if not Path(golden_subset_path).exists():
        raise ValueError(f"Golden subset file does not exist: {golden_subset_path}")

    # AIDEV-NOTE: Validate all Linux repos exist before starting
    for linux_dir in linux_dirs:
        if not Path(linux_dir).exists():
            raise ValueError(f"Linux repository does not exist: {linux_dir}")

    # Load golden subset data
    print(f"Loading golden subset from: {golden_subset_path}")
    with open(golden_subset_path, 'r') as f:
        golden_subset_json = json.load(f)

    # AIDEV-NOTE: Check if golden subset is in canonical array format and transform if needed
    # Canonical format: {"bugs": [{"id": "...", "image": "...", "kcache": "..."}]}
    # Required format: {bug_id: {"image": "...", "kcache": "..."}}
    if "bugs" in golden_subset_json and isinstance(golden_subset_json["bugs"], list):
        # Transform from array format to dict format
        golden_subset_data = {}
        for bug_entry in golden_subset_json["bugs"]:
            bug_id = bug_entry["id"]
            golden_subset_data[bug_id] = {
                "image": bug_entry["image"],
                "kcache": bug_entry.get("kcache")
            }
        print(f"Transformed golden subset from array format to dict (found {len(golden_subset_data)} bugs)")
    else:
        # Already in dict format
        golden_subset_data = golden_subset_json
        print(f"Golden subset already in dict format")

    print(f"Linux repositories: {linux_dirs[0]} ... {linux_dirs[-1]} ({len(linux_dirs)} total)")

    # Get bug IDs to process
    if bug_ids:
        print(f"\nProcessing {len(bug_ids)} specified bugs")
    else:
        bug_ids = get_bug_ids_from_results(results_dir_path)
        print(f"\nFound {len(bug_ids)} bugs in results directory")

    if not bug_ids:
        print("No bugs found to process!")
        return {
            "total_processed": 0,
            "successful": 0,
            "failed": 0,
            "successful_bugs": [],
            "failed_bugs": []
        }

    print(f"Bug IDs: {bug_ids[:10]}" + ("..." if len(bug_ids) > 10 else ""))

    # AIDEV-NOTE: Create thread-safe queue with (linux_dir, code_dir_lock) tuples
    # Each Linux repo gets its own GitRWLock to prevent git conflicts within that repo
    # GitRWLock automatically cleans .git/index.lock on write lock release
    # Workers get() a tuple, use the repo with its lock, then put() it back when done
    linux_dir_queue = Queue()
    for linux_dir in linux_dirs:
        code_dir_lock = GitRWLock(git_dir=os.path.join(linux_dir, ".git"))
        linux_dir_queue.put((linux_dir, code_dir_lock))

    # AIDEV-NOTE: Initialize aggregated results tracker for reduction patterns
    # Node-level: fixed keys (max 4 nodes in tree). Edit-level: free dict (dynamic keys)
    if minimize_at == "node":
        aggregated_results = {
            "4_to_4": 0, "4_to_3": 0, "4_to_2": 0, "4_to_1": 0,
            "3_to_3": 0, "3_to_2": 0, "3_to_1": 0,
            "2_to_2": 0, "2_to_1": 0,
            "1_to_1": 0
        }
    else:
        # Edit-level: empty dict, keys added dynamically as reductions occur
        aggregated_results = {}

    # Process bugs
    successful = []
    failed = []

    # AIDEV-NOTE: Setup for incremental summary writes with crash recovery
    results_lock = threading.Lock()
    summary_file = results_dir_path / "minimization_summary.json"
    config = {
        "results_dir": str(results_dir_path),
        "benchmark_folder": benchmark_folder,
        "golden_subset": golden_subset_path,
        "linux_dirs": linux_dirs,
        "model_id": model_id,
        "run_jobs": run_jobs,
        "parallel": num_parallel
    }

    # AIDEV-NOTE: Crash recovery - load existing state if summary file exists
    processed = set()
    if summary_file.exists():
        try:
            with open(summary_file) as f:
                existing = json.load(f)
            # Always load successful bugs - never retry these
            for bug in existing.get("successful_bugs", []):
                processed.add((bug["bug_id"], bug["folder"]))
                successful.append((bug["bug_id"], bug["folder"], bug.get("best_reduction"), bug.get("feedback_stats", bug.get("stats"))))
            # Handle failed bugs based on retry mode
            failed_from_summary = existing.get("failed_bugs", [])
            if retry_failed_only:
                # AIDEV-NOTE: Retry mode - filter bug_ids to only failed bugs
                failed_bug_ids = set(bug["bug_id"] for bug in failed_from_summary)
                if not failed_bug_ids:
                    print("No failed bugs found to retry!")
                else:
                    if bug_ids is None:
                        bug_ids = sorted(list(failed_bug_ids))
                    else:
                        bug_ids = [b for b in bug_ids if b in failed_bug_ids]
                    print(f"Retrying {len(bug_ids)} failed bugs: {bug_ids[:5]}...")
                # Don't add failed bugs to processed - they will be retried
            else:
                # Normal behavior: skip failed bugs too
                for bug in failed_from_summary:
                    processed.add((bug["bug_id"], bug["folder"]))
                    failed.append((bug["bug_id"], bug["folder"]))
            aggregated_results = existing.get("aggregated_results", aggregated_results)
            print(f"Resuming: loaded {len(processed)} already-processed bugs from {summary_file}")
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Warning: Failed to load existing summary, starting fresh: {e}")

    if num_parallel > 1:
        print(f"\nProcessing bugs in parallel with {num_parallel} workers...")
        with ThreadPoolExecutor(max_workers=num_parallel) as executor:
            futures = {}

            for bug_id in bug_ids:
                # AIDEV-NOTE: Use success_paths when available to only minimize actually-successful trees
                if success_paths and bug_id in success_paths:
                    folder_infos = _folder_infos_from_success_paths(success_paths[bug_id], results_dir_path, bug_id)
                else:
                    folder_infos = get_folder_paths_for_bug(results_dir_path, bug_id, model_id)

                for folder_path, folder_name in folder_infos:
                    # AIDEV-NOTE: Skip already processed bugs (crash recovery)
                    if (bug_id, folder_name) in processed:
                        print(f"Skipping already processed: {bug_id} ({folder_name})")
                        continue

                    future = executor.submit(
                        minimize_bug_solution,
                        bug_id=bug_id,
                        folder_path=folder_path,
                        folder_name=folder_name,
                        benchmark_folder=benchmark_folder,
                        golden_subset_data=golden_subset_data,
                        linux_dir_queue=linux_dir_queue,
                        model_id=model_id,
                        run_jobs=run_jobs,
                        syzkaller_rollback_tag=syzkaller_rollback_tag,
                        minimize_at=minimize_at,
                        node_refinement=node_refinement,
                        edit_refinement=edit_refinement,
                        use_result_cache=use_result_cache
                    )
                    futures[future] = (bug_id, folder_name)

            for future in as_completed(futures):
                bug_id, folder_name = futures[future]
                try:
                    result = future.result()
                    # AIDEV-NOTE: result can be: dict (success), -1 (no solutions), or None (error)
                    with results_lock:
                        if isinstance(result, dict):
                            # Success - extract best_reduction and feedback_stats from minimization_data
                            best_reduction = result.get("best_reduction")
                            feedback_stats = result.get("feedback_stats")
                            successful.append((bug_id, folder_name, best_reduction, feedback_stats))

                            # Update aggregated_results - dynamically add key if not present (edit-level)
                            if best_reduction:
                                aggregated_results[best_reduction] = aggregated_results.get(best_reduction, 0) + 1
                        elif result == -1:
                            # No successful solution paths found - not a failure, just skip
                            pass
                        else:
                            # None or other error
                            failed.append((bug_id, folder_name))
                        # AIDEV-NOTE: Save incrementally after each result
                        _save_summary_atomic(summary_file, aggregated_results, successful, failed, config)
                except Exception as e:
                    print(f"Exception processing {bug_id} ({folder_name}): {e}")
                    with results_lock:
                        failed.append((bug_id, folder_name))
                        _save_summary_atomic(summary_file, aggregated_results, successful, failed, config)
    else:
        print("\nProcessing bugs sequentially...")
        for bug_id in bug_ids:
            # AIDEV-NOTE: Use success_paths when available to only minimize actually-successful trees
            if success_paths and bug_id in success_paths:
                folder_infos = _folder_infos_from_success_paths(success_paths[bug_id], results_dir_path, bug_id)
            else:
                folder_infos = get_folder_paths_for_bug(results_dir_path, bug_id, model_id)

            for folder_path, folder_name in folder_infos:
                # AIDEV-NOTE: Skip already processed bugs (crash recovery)
                if (bug_id, folder_name) in processed:
                    print(f"Skipping already processed: {bug_id} ({folder_name})")
                    continue

                try:
                    result = minimize_bug_solution(
                        bug_id=bug_id,
                        folder_path=folder_path,
                        folder_name=folder_name,
                        benchmark_folder=benchmark_folder,
                        golden_subset_data=golden_subset_data,
                        linux_dir_queue=linux_dir_queue,
                        model_id=model_id,
                        run_jobs=run_jobs,
                        syzkaller_rollback_tag=syzkaller_rollback_tag,
                        minimize_at=minimize_at,
                        node_refinement=node_refinement,
                        edit_refinement=edit_refinement,
                        use_result_cache=use_result_cache
                    )

                    # AIDEV-NOTE: result can be: dict (success), -1 (no solutions), or None (error)
                    if isinstance(result, dict):
                        # Success - extract best_reduction and feedback_stats from minimization_data
                        best_reduction = result.get("best_reduction")
                        feedback_stats = result.get("feedback_stats")
                        successful.append((bug_id, folder_name, best_reduction, feedback_stats))

                        # Update aggregated_results - dynamically add key if not present (edit-level)
                        if best_reduction:
                            aggregated_results[best_reduction] = aggregated_results.get(best_reduction, 0) + 1
                    elif result == -1:
                        # No successful solution paths found - not a failure, just skip
                        pass
                    else:
                        # None or other error
                        failed.append((bug_id, folder_name))
                except Exception as e:
                    print(f"Error minimizing {bug_id} ({folder_name}): {e}")
                    failed.append((bug_id, folder_name))

                # AIDEV-NOTE: Save incrementally after each result (no lock needed - sequential)
                _save_summary_atomic(summary_file, aggregated_results, successful, failed, config)

    # Print summary
    print("\n" + "=" * 80)
    print("Minimization Summary")
    print("=" * 80)
    print(f"Total processed: {len(successful) + len(failed)}")
    print(f"Successful: {len(successful)}")
    print(f"Failed: {len(failed)}")

    if failed:
        print("\nFailed bugs:")
        for bug_id, folder_name in failed:
            print(f"  - {bug_id} ({folder_name})")

    print("=" * 80)

    # AIDEV-NOTE: No need to save here - already saved incrementally after each result
    print(f"\nSummary saved to: {summary_file}")

    # Return summary data for callers
    # AIDEV-NOTE: successful is list of (bug_id, folder, best_reduction, stats_or_feedback) tuples
    # The 4th element is either a feedback_stats dict (from minimization) or a full
    # DD stats dict (from DD benchmark) containing tests_run, not_applicable, etc.
    successful_bugs = []
    for item in successful:
        entry = {"bug_id": item[0], "folder": item[1], "best_reduction": item[2]}
        if len(item) > 3 and item[3]:
            entry["feedback_stats"] = item[3]
        successful_bugs.append(entry)

    summary = {
        "aggregated_results": aggregated_results,
        "total_processed": len(successful) + len(failed),
        "successful": len(successful),
        "failed": len(failed),
        "successful_bugs": successful_bugs,
        "failed_bugs": [
            {"bug_id": item[0], "folder": item[1], **({"stats": item[2]} if len(item) > 2 and item[2] else {})}
            for item in failed
        ],
        "configuration": config
    }

    # AIDEV-NOTE: Aggregate feedback_stats at the very end using standalone function
    # This reads from successful_bugs in the summary file and adds aggregated_feedback_stats
    aggregate_feedback_stats_from_summary(str(summary_file))

    return summary


def main():
    """Main entry point for standalone CLI usage."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Minimize patches in benchmark results using ToolSolnMinimize"
    )
    parser.add_argument(
        "--results-dir", type=str, required=True,
        help="Path to the results directory containing bug solutions",
    )
    parser.add_argument(
        "--benchmark-folder", type=str, default=None,
        help="Path to benchmark folder (defaults to KBENCH_PATH env var)",
    )
    parser.add_argument(
        "--golden-subset", type=str, required=True,
        help="Path to golden subset JSON file.",
    )
    parser.add_argument(
        "--linux-dirs", nargs="+", metavar="DIR", required=True,
        help="Linux repo paths for parallel processing",
    )
    parser.add_argument(
        "--model-id", type=str, default="gemini-2.5-pro",
        help="Model ID used in the experiment (default: gemini-2.5-pro)",
    )
    parser.add_argument("--run-jobs", action="store_true",
                        help="Run KBDR jobs to validate minimized patches")
    parser.add_argument(
        "--bug-ids", type=str, nargs="+", default=None,
        help="Specific bug IDs to minimize (default: all bugs in results dir)",
    )
    parser.add_argument(
        "--parallel", type=int, default=1,
        help="Number of bugs to process in parallel (default: 1, sequential)",
    )
    parser.add_argument(
        "--from-success-paths", action="store_true",
        help="Extract bug IDs from success_paths.json (only minimize solved bugs)",
    )
    parser.add_argument(
        "--syzkaller-rollback-tag", type=str, default=None,
        help="Syzkaller rollback tag for reproducer configuration (e.g., 'kgym-latest')",
    )
    parser.add_argument(
        "--minimize-at", type=str, choices=["node", "edit"], default="edit",
        help="Minimization granularity: 'node' or 'edit'. Default: edit",
    )
    parser.add_argument(
        "--node-refinement", action=argparse.BooleanOptionalAction, default=True,
        help="Run node-level 1-minimal guarantee refinement (default: True)",
    )
    parser.add_argument(
        "--edit-refinement", action=argparse.BooleanOptionalAction, default=True,
        help="Run edit-level 1-minimal guarantee refinement (default: True)",
    )
    parser.add_argument(
        "--use-result-cache", action=argparse.BooleanOptionalAction, default=True,
        help="Cache environment results by edit-set (default: True)",
    )

    args = parser.parse_args()

    benchmark_path = args.benchmark_folder or os.getenv("KBENCH_PATH")
    if not benchmark_path:
        raise ValueError("KBENCH_PATH environment variable not set or --benchmark-folder not provided")

    golden_subset_path = args.golden_subset
    linux_dirs = args.linux_dirs

    print(f"Linux repos: {len(linux_dirs)}")

    # AIDEV-NOTE: Handle bug_ids extraction based on --from-success-paths flag
    bug_ids = args.bug_ids
    if args.from_success_paths:
        success_paths_file = Path(args.results_dir) / "success_paths.json"
        if not success_paths_file.exists():
            raise FileNotFoundError(
                f"success_paths.json not found: {success_paths_file}\n"
                f"--from-success-paths requires evaluation to run first.\n"
                f"Either run evaluation first or remove --from-success-paths flag."
            )

        print(f"\nLoading bug IDs from: {success_paths_file}")
        with open(success_paths_file, 'r') as f:
            success_paths = json.load(f)

        bug_ids = list(success_paths.keys())
        print(f"Found {len(bug_ids)} solved bugs in success_paths.json")
        print(f"Bug IDs: {bug_ids[:10]}" + ("..." if len(bug_ids) > 10 else ""))
        print()

    run_minimization_on_results(
        results_dir=args.results_dir,
        benchmark_folder=benchmark_path,
        golden_subset_path=golden_subset_path,
        linux_dirs=linux_dirs,
        model_id=args.model_id,
        bug_ids=bug_ids,
        num_parallel=args.parallel,
        run_jobs=args.run_jobs,
        syzkaller_rollback_tag=args.syzkaller_rollback_tag,
        minimize_at=args.minimize_at,
        node_refinement=args.node_refinement,
        edit_refinement=args.edit_refinement,
        use_result_cache=args.use_result_cache,
    )


if __name__ == "__main__":
    main()

