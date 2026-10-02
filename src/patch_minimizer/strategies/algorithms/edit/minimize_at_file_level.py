from __future__ import annotations
from collections import defaultdict
from typing import List, TYPE_CHECKING

from patch_minimizer.core.edit import Edit
from patch_minimizer.strategies.algorithms.greedy_removal import unrestricted_greedy_removal

if TYPE_CHECKING:
    from patch_minimizer.strategies.algorithms.patch_candidate_tester import PatchCandidateTester


# AIDEV-NOTE: File-level minimization — groups edits by filename, applies unrestricted greedy removal
def minimize_at_file_level(bug_id, patch_tester: PatchCandidateTester, feedback_aggregator,
                           edits: List[Edit], first_diff, rule_engine, logger):
    """Remove unnecessary files via unrestricted greedy removal.

    Groups edits by filename, then applies greedy removal to file groups.
    Returns List[Edit] with unnecessary file edits removed.
    """
    edits_by_file: dict[str, List[Edit]] = defaultdict(list)
    for edit in edits:
        edits_by_file[edit.filename].append(edit)

    if len(edits_by_file) <= 1:
        logger.info(f"[{bug_id}] File-level: Only 1 file, skipping")
        return edits

    # Each file becomes [edit1, edit2, ...] - all edits for that file as one unit
    file_patches = [file_edits for file_edits in edits_by_file.values()]

    patch_tester._granularity = "file"
    print(f"[{bug_id}] Starting File-level minimization: {len(file_patches)} files")

    file_minimized = unrestricted_greedy_removal(bug_id, patch_tester, feedback_aggregator, file_patches, first_diff, rule_engine, logger)

    # AIDEV-NOTE: Flatten back to List[Edit], sorted by global_edit_idx to preserve original order.
    result = [edit for file_edits in file_minimized for edit in file_edits]
    result.sort(key=lambda e: e.global_edit_idx)
    return result
