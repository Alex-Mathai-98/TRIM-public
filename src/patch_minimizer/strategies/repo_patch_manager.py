"""
RepoPatchManager — owns all repo cleaning, patch application, and diff generation logic.

AIDEV-NOTE: Extracted from SolutionMinimizationBase to separate repo operations from minimization logic.
"""
from __future__ import annotations

import os
import subprocess
from collections import defaultdict
from os.path import join as pjoin
from typing import List

from patch_minimizer.core.edit import Edit, EditType
from patch_minimizer.core.edit_applier import EditApplier
from patch_minimizer.core.rw_lock import RWLock
from patch_minimizer.core.utils import Utils


class RepoPatchManager:
    """Manages repo cleaning, patch application, and diff generation for a single Linux repo."""

    def __init__(
        self,
        code_dir: str,
        code_dir_lock: RWLock,
        base_commit: str,
        logger,
        kernel_base_url: str | None = None,
        swebench_mode: bool = False,
    ) -> None:
        self.code_dir = code_dir
        self.code_dir_lock = code_dir_lock
        self.base_commit = base_commit
        self.logger = logger
        self.kernel_base_url = kernel_base_url
        # AIDEV-NOTE: swebench_mode short-circuits the clone-side pre-validation
        # in check_patch_range. The clone is still needed for apply_single_patch /
        # generate_git_diff (which render the candidate patch text), but the pre-flight
        # assertion that fires on scratch files (debug_*.py, reproduce_*.py — they
        # only exist inside the container) is skipped. The submission-paths filter
        # in minimize_edits ensures only real source files reach apply_single_patch,
        # so the clone-side dance works without modification. See validation_plan.md §F.
        self.swebench_mode = swebench_mode
        self.apputils = Utils(self.logger)
        # AIDEV-NOTE: z-cpl-74 - Use EditApplier instead of KernelWriteUtils
        self._edit_applier = EditApplier()

        # Perform first checkout - repo_reset_and_clean_checkout handles lock cleanup internally
        self.apputils.repo_reset_and_clean_checkout(
            self.base_commit,
            use_lock=True,
            code_dir_lock=self.code_dir_lock,
            git_dir=pjoin(self.code_dir, ".git"),
            work_tree=self.code_dir,
            kernel_base_url=self.kernel_base_url,
        )

    def _resolve_file_path(
        self, edit: Edit, created_files: dict[str, str]
    ) -> tuple[str | None, bool]:
        """Resolve the on-disk path for an edit's target file.

        Returns (found_file, is_new_file). Returns (None, False) when
        the edit should be skipped (best_effort file not found).
        Raises AssertionError for non-best_effort files that can't be found.
        """
        target_file = edit.filename
        found_file = self.apputils.find_file(self.code_dir, target_file)
        is_new_file = False
        if found_file is None:
            if edit.edit_type in (EditType.WHOLE_FILE_ACTIONS, EditType.COPY_FILE):
                found_file = os.path.join(self.code_dir, target_file)
                created_files[target_file] = found_file
                is_new_file = True
            elif edit.edit_type == EditType.WHOLE_FILE_DELETE:
                if target_file not in created_files:
                    self.logger.warning(
                        "WHOLE_FILE_DELETE for '%s' but file not found in repo or created_files — "
                        "likely missed a file creation command (e.g. shell redirection '> file') in trajectory. Skipping.",
                        target_file,
                    )
                    return None, False
                found_file = created_files[target_file]
            elif target_file in created_files:
                found_file = created_files[target_file]
            elif edit.best_effort:
                return None, False
            elif self.swebench_mode:
                # AIDEV-NOTE: Safety net for swebench_mode — any edit that
                # slipped past the submission-paths filter (e.g. a scratch
                # `debug_*.py` not present in the agent's submitted diff)
                # is silently skipped rather than asserting. The container
                # injects scratch files via _copy_test_files_to_container
                # independently of the candidate patch.
                if self.logger:
                    self.logger.warning(
                        "[SWEBench] skipping edit on %s — file not in clone (likely scratch file)",
                        target_file,
                    )
                return None, False
            else:
                assert False, f"File {target_file} not found even in a valid patch - something is seriously wrong."
        return found_file, is_new_file

    def clean_repo(self, use_lock: bool = False) -> None:
        """Clean all the changes until the current commit."""
        self.apputils.repo_clean_changes(
            use_lock=use_lock,
            code_dir_lock=self.code_dir_lock,
            git_dir=pjoin(self.code_dir, ".git"),
            work_tree=self.code_dir,
        )

    def apply_single_patch(
        self, patch: List[Edit], use_lock: bool = False,
        created_files: dict[str, str] | None = None,
    ) -> None:
        """Applies a single patch to the codebase."""

        def base_function() -> None:
            assert self.code_dir_lock is not None and self.code_dir_lock.is_w_locked(), "apply_single_patch.base_function() requires write lock to be held"
            assert len(patch) > 0, "Patch has no edits - something is seriously wrong."
            nonlocal created_files
            if created_files is None:
                created_files = {}
            for edit in patch:
                found_file, _ = self._resolve_file_path(edit, created_files)
                if found_file is None:
                    continue
                applied_file = self._edit_applier.apply_edit_to_linux_code(
                    edit, found_file, self.code_dir_lock
                )
                if applied_file is None:
                    if edit.best_effort:
                        continue
                    assert False, f"Edit could not be applied to {edit.filename}"
                if edit.edit_type == EditType.WHOLE_FILE_DELETE:
                    created_files.pop(edit.filename, None)

        if use_lock:
            with self.code_dir_lock.w_locked_nowait():
                return base_function()
        else:
            return base_function()

    def check_single_patch_application(
        self,
        patch: List[Edit],
        prefix_patches: List | None = None,
        use_lock: bool = False,
    ) -> bool:
        """Returns True if the entire patch can be applied. Else returns False."""

        def base_function() -> bool:
            assert self.code_dir_lock is not None and self.code_dir_lock.is_w_locked(), "check_single_patch_application.base_function() requires write lock to be held"
            patch_applied = True
            assert len(patch) > 0, "Patch has no edits - something is seriously wrong."

            # AIDEV-NOTE: Group edits by file to maintain in-memory state per file.
            # created_files tracks files from WHOLE_FILE_ACTIONS so subsequent
            # edits on the same file don't fail find_file.
            edits_by_file: dict[str, list[Edit]] = defaultdict(list)
            created_files: dict[str, str] = {}

            for edit in patch:
                found_file, _ = self._resolve_file_path(edit, created_files)
                if found_file is None:
                    continue
                edits_by_file[found_file].append(edit)

            # AIDEV-NOTE: Check each file's edits sequentially, maintaining in-memory state
            for found_file, file_edits in edits_by_file.items():
                current_content = None

                for edit in file_edits:
                    match_start, match_end = None, None

                    result = self._edit_applier.check_edit_application(
                             edit, found_file, self.code_dir_lock, current_content
                         )
                    
                    if result:
                        match_start = result.start_line
                        match_end = result.end_line
                        modified_content = result.new_content

                    if match_start is None or match_end is None:
                        if edit.best_effort:
                            continue
                        patch_applied = False
                        return patch_applied

                    current_content = modified_content

            return patch_applied

        if use_lock:
            with self.code_dir_lock.w_locked_nowait():
                return base_function()
        else:
            return base_function()

    def apply_parent_diff(self, parent_diff: str) -> None:
        """Apply a git diff to the repo.

        AIDEV-NOTE: Extracted from repeated inline usage in SolutionMinimizationBase.
        Caller must hold write lock or ensure exclusive access.
        """
        self.apputils.apply_git_diff(
            git_diff=parent_diff,
            repo_path=self.code_dir,
            code_dir_lock=self.code_dir_lock,
            git_dir=pjoin(self.code_dir, ".git"),
            work_tree=self.code_dir,
        )

    def check_patch_range(
        self,
        parent_diff: str | None,
        patch_range: List[List[Edit]],
        leave_patches: bool = False,
        use_lock: bool = True,
    ) -> bool:
        """Check if a sequence of patches can be applied after an optional parent diff.

        AIDEV-NOTE: Added use_lock parameter to allow caller to hold lock across multiple calls,
        fixing race condition in find_independent_nodes/find_independent_edits(). See minimization_locking_fix_plan.md.

        """

        def base_function() -> bool:
            assert self.code_dir_lock is not None and self.code_dir_lock.is_w_locked(), \
                "check_patch_range.base_function() requires write lock to be held"
            self.clean_repo(use_lock=False)

            if parent_diff is not None:
                self.apply_parent_diff(parent_diff)

            # AIDEV-NOTE: Save original starting_line values — DD reuses the same
            # Edit objects across many calls, so we must not permanently mutate them.
            saved = [
                (edit, edit.starting_line)
                for patch in patch_range if patch
                for edit in patch
            ]

            ans = True
            # AIDEV-NOTE: Track cumulative line shift PER FILE — edits in different
            # files must not cross-contaminate offsets (bug: 4946dc7 multi-file patch).
            line_offset_by_file: dict[str, int] = {}
            created_files: dict[str, str] = {}
            try:
                for patch in patch_range:
                    if patch is None:
                        continue
                    # AIDEV-NOTE: Two coord systems for starting_line (see Edit.is_dd_hunk):
                    #   - DD hunks (is_dd_hunk=True): ORIGINAL-file coords, need the
                    #     cumulative per-file offset from prior hunks applied.
                    #   - Everything else (is_dd_hunk=False, incl. agent adapters):
                    #     LIVE-file coords at the step the edit was produced. Already
                    #     accounts for prior edits — must NOT receive an offset.
                    for edit in patch:
                        if edit.starting_line is not None and edit.is_dd_hunk:
                            offset = line_offset_by_file.get(edit.filename, 0)
                            if offset != 0:
                                edit.starting_line += offset
                    flag = self.check_single_patch_application(patch, use_lock=False)
                    if flag:
                        self.apply_single_patch(patch, use_lock=False, created_files=created_files)
                        # line_offset_by_file is read only by DD hunks (above), so only
                        # DD hunks need to contribute to it.
                        for edit in patch:
                            if edit.starting_line is not None and edit.is_dd_hunk:
                                before_count = len(edit.before.split("\n"))
                                after_count = len(edit.after.split("\n"))
                                line_offset_by_file[edit.filename] = \
                                    line_offset_by_file.get(edit.filename, 0) + (after_count - before_count)
                    else:
                        # AIDEV-NOTE: If all edits in the patch are best_effort,
                        # skip this patch and continue to the next one.
                        if all(e.best_effort for e in patch):
                            continue
                        ans = False
                        break
            finally:
                # AIDEV-NOTE: Restore starting_line values so Edit objects are
                # unchanged for the next DD iteration.
                for edit, orig_sl in saved:
                    edit.starting_line = orig_sl

            # git add -N for new files that still exist (created but not deleted)
            for f in created_files.values():
                if os.path.exists(f):
                    result = subprocess.run(
                        ["git", "--git-dir", pjoin(self.code_dir, ".git"),
                         "--work-tree", self.code_dir, "add", "-N", f],
                        capture_output=True,
                    )
                    if result.returncode != 0:
                        stderr = result.stderr.decode().strip()
                        if "ignored" in stderr.lower() or ".gitignore" in stderr:
                            self.logger.warning(
                                "git add -N skipped .gitignored file '%s': %s", f, stderr,
                            )
                        else:
                            raise subprocess.CalledProcessError(
                                result.returncode, result.args, result.stdout, result.stderr,
                            )

            if not leave_patches:
                self.clean_repo(use_lock=False)
            return ans

        if use_lock:
            with self.code_dir_lock.w_locked_nowait():
                return base_function()
        else:
            assert self.code_dir_lock is not None and self.code_dir_lock.is_w_locked(), (
                "When use_lock=False, caller must hold the write lock"
            )
            return base_function()

    def generate_git_diff(self) -> str:
        """Generate the git diff for the repo.

        AIDEV-NOTE: Caller MUST hold the write lock before calling this function.
        """
        # AIDEV-NOTE: Assert lock is held - caller must hold the lock to avoid race conditions
        assert self.code_dir_lock.is_w_locked(), "generate_git_diff() requires caller to hold write lock"

        # AIDEV-NOTE: Kernel callers embed `collection_of_linux_repos/<repo>/` into
        # the diff prefixes so apply_git_diff's path rewriter can map them back. Other
        # callers (SWE-bench, generic Python repos) get plain `a/`/`b/` prefixes —
        # git diff's native default — which apply cleanly with a standard `git apply`.
        # [AI-M2.repo]
        diff_command = [
            "git",
            "--git-dir", pjoin(self.code_dir, ".git"),
            "--work-tree", self.code_dir,
            "diff",
        ]
        if "collection_of_linux_repos/" in self.code_dir:
            start_idx = self.code_dir.find("collection_of_linux_repos/")
            src_prefix = "a/" + self.code_dir[start_idx:] + "/"
            dst_prefix = "b/" + self.code_dir[start_idx:] + "/"
            diff_command.extend(["--src-prefix", src_prefix, "--dst-prefix", dst_prefix])
        diff = self.apputils.run_command(diff_command).stdout
        return diff
