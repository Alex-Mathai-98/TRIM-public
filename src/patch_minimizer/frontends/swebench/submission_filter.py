"""SWE-bench submission-path filtering.

AIDEV-NOTE: Moved out of ``minimize_edits()`` — this is SWE-bench-specific
preprocessing and belongs in the SWE-bench frontend, not in the generic core.
"""
from __future__ import annotations

import logging


def filter_to_submission_paths(patch_list: list, submission_paths: set[str], logger=None) -> list:
    """Restrict ``patch_list`` to edits whose ``filename`` is in ``submission_paths``.

    ``submission_paths`` is parsed from the agent's ``info.submission`` ``+++ b/...``
    headers. This drops scratch-file CREATE edits (e.g. ``debug_tensor.py``) that came
    from ``to_patch_list(cand)`` but were never part of the agent's final submitted fix.
    Without this filter, scratch CREATEs in the candidate patch would clobber the
    container's correctly-injected ``test_files`` entries with str_replace fragments.
    See validation_plan.md section F (option gamma).

    Args:
        patch_list: List of ``(parent_diff, List[Edit])`` tuples.
        submission_paths: Filenames present in the agent's submitted diff.
        logger: Optional logger; dropped paths are reported at INFO.

    Returns:
        A new patch_list containing only edits on submitted paths. Nodes left with
        no surviving edits are omitted.
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    filtered_patch_list: list = []
    dropped_paths: set[str] = set()
    for parent_diff, edits in patch_list:
        kept = [e for e in edits if e.filename in submission_paths]
        dropped = [e for e in edits if e.filename not in submission_paths]
        if dropped:
            dropped_paths.update(e.filename for e in dropped)
        if kept:
            filtered_patch_list.append((parent_diff, kept))
    if dropped_paths:
        logger.info(
            "[SWEBench] filtered patch_list to submission paths — "
            "dropped %d non-submission file(s): %s",
            len(dropped_paths), sorted(dropped_paths),
        )
    return filtered_patch_list
