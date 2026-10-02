from __future__ import annotations
from patch_minimizer.core.edit import Edit
from os.path import join as pjoin
from typing import List
import os
import re

from patch_minimizer.strategies.rule_engines import KernelOnlyRuleEngine
from patch_minimizer.strategies.repo_patch_manager import RepoPatchManager
from patch_minimizer.strategies.algorithms.patch_candidate_tester import PatchCandidateTester


# AIDEV-NOTE: Slimmed base — owns repo, rule_engine, diff gen/save. Algorithms extracted to standalone functions.
# NodeMinimizer and EditMinimizer inherit from this class.
class SolutionMinimizationBase():

    def __init__(self, code_dir, code_dir_lock, base_commit, logger, kernel_base_url: (str | None) = None, syzkaller_rollback_tag: (str | None) = None, use_result_cache: bool = True, swebench_mode: bool = False) -> None:
        self.code_dir = code_dir
        self.code_dir_lock = code_dir_lock
        self.base_commit = base_commit
        self.logger = logger
        self.kernel_base_url = kernel_base_url
        self.syzkaller_rollback_tag = syzkaller_rollback_tag
        # AIDEV-NOTE: Repo operations delegated to RepoPatchManager (extracted from this class)
        # AIDEV-NOTE: swebench_mode skips all clone-side validation; container is the only gate (see validation_plan.md §F).
        self.repo = RepoPatchManager(code_dir, code_dir_lock, base_commit, logger, kernel_base_url, swebench_mode=swebench_mode)
        # AIDEV-NOTE: Rule engine handles KEEP/DROP decisions
        self.rule_engine = KernelOnlyRuleEngine(logger)
        # AIDEV-NOTE: PatchCandidateTester owns feedback state + try_candidate loop
        self.patch_tester = PatchCandidateTester(self.repo, code_dir_lock, use_result_cache=use_result_cache)

    # AIDEV-NOTE: Delegation property — external callers (sol_minimize.py, minimize_patches.py)
    # access smb.feedback_stats; this delegates to patch_tester transparently
    @property
    def feedback_stats(self):
        return self.patch_tester.feedback_stats

    @feedback_stats.setter
    def feedback_stats(self, value):
        self.patch_tester.feedback_stats = value

    # AIDEV-NOTE: Decoupled from _save_minimized_patch so diff is always available even without save_dir
    def _generate_minimized_diff(self, bug_id, first_diff: str, patch_range: List[List[Edit]], cleanup=None, use_lock: bool = True):
        """Generate the minimized git diff by applying edits to the repo.

        Args:
            bug_id: Kernel bug identifier
            first_diff: Parent diff (may contain instrumentation for execution strategies)
            patch_range: List of patches to apply
            cleanup: InstrumentaionCleanUp object to remove instrumentation (optional)
            use_lock: Whether to acquire write lock (False if caller already holds it)

        Returns:
            str: The normalized minimized git diff
        """
        def base_function():
            assert self.code_dir_lock.is_w_locked(), "_generate_minimized_diff.base_function() requires write lock to be held"
            self.repo.clean_repo(use_lock=False)

            if first_diff is not None:
                self.repo.apply_parent_diff(first_diff)

            for patch in patch_range:
                self.repo.apply_single_patch(patch, use_lock=False)

            # Generate the git diff
            minimized_diff = self.repo.generate_git_diff()

            # AIDEV-NOTE: Normalize linux directory paths from /linux-(\d+)/ to /linux-TEMP/ for easy comparison
            minimized_diff = re.sub(r'/linux-\d+/', '/linux-TEMP/', minimized_diff)

            # Clean the repo after generating diff
            self.repo.clean_repo(use_lock=False)

            return minimized_diff

        if use_lock:
            with self.code_dir_lock.w_locked_nowait():
                return base_function()
        else:
            assert self.code_dir_lock.is_w_locked(), "_generate_minimized_diff(use_lock=False) requires caller to hold write lock"
            return base_function()

    def _save_minimized_patch(self, bug_id, first_diff: str, patch_range: List[List[Edit]], save_dir: str, node_name: str, cleanup=None, use_lock: bool = True):
        """Generate minimized diff, apply cleanup, and save to node folder.

        Args:
            bug_id: Kernel bug identifier
            first_diff: Parent diff (may contain instrumentation for execution strategies)
            patch_range: List of patches to apply and save
            save_dir: Directory containing node folder
            node_name: Node folder name (e.g., "gemini-1.5-pro-002__<bug_id>/<node_num>")
            cleanup: InstrumentaionCleanUp object to remove instrumentation (optional)
            use_lock: Whether to acquire write lock (False if caller already holds it)
        """
        def base_function():
            assert self.code_dir_lock.is_w_locked(), "_save_minimized_patch.base_function() requires write lock to be held"

            # Construct the save path
            node_folder_path = pjoin(save_dir, node_name)
            if not os.path.exists(node_folder_path):
                raise FileNotFoundError(f"Node folder does not exist: {node_folder_path}")

            # AIDEV-NOTE: Clean up instrumentation if cleanup object provided (execution strategies)
            if cleanup is not None:
                # AIDEV-NOTE: Generate raw (un-normalized) diff for cleanup — cleanup needs
                # real linux-NNN paths to git-apply the patch and remove instrumentation.
                # _generate_minimized_diff normalizes paths to linux-TEMP which breaks git apply.
                self.repo.clean_repo(use_lock=False)
                if first_diff is not None:
                    self.repo.apply_parent_diff(first_diff)
                for patch in patch_range:
                    self.repo.apply_single_patch(patch, use_lock=False)
                raw_diff = self.repo.generate_git_diff()
                self.repo.clean_repo(use_lock=False)

                # Let cleanup process create cleaned_final.patch
                # use_lock=False because we already hold the write lock
                cleanup.cleanup_diff(raw_diff, node_folder_path, use_lock=False)

                # Read the cleaned version and normalize paths
                cleaned_patch_path = pjoin(node_folder_path, "cleaned_final.patch")
                with open(cleaned_patch_path, 'r') as f:
                    minimized_diff = f.read()
                minimized_diff = re.sub(r'/linux-\d+/', '/linux-TEMP/', minimized_diff)

                # remove the extra file
                os.remove(cleaned_patch_path)
            else:
                minimized_diff = self._generate_minimized_diff(bug_id, first_diff, patch_range, cleanup, use_lock=False)

            minimized_patch_path = pjoin(node_folder_path, "minimized.patch")

            # Save the minimized patch
            with open(minimized_patch_path, 'w') as f:
                f.write(minimized_diff)
                print(f"[{bug_id}] {self.patch_tester._granularity.capitalize()}-level: Written minimized patch to {node_name}")

            return minimized_diff

        if use_lock:
            with self.code_dir_lock.w_locked_nowait():
                return base_function()
        else:
            assert self.code_dir_lock.is_w_locked(), "_save_minimized_patch(use_lock=False) requires caller to hold write lock"
            return base_function()
