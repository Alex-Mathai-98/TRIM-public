"""Agent-agnostic helpers: path normalization, patch-list construction, file loading.

No format-specific knowledge lives here. Format adapters (``swe_agent_adapter``,
``openhands``, ``mini_swe_agent_adapter``) register their parsers with
``parser_registry`` on import; ``parse_trajectory_with_patch_lists`` dispatches
via the registry and returns the shape every minimization entry point consumes.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Tuple

from patch_minimizer.core.edit import Edit
from patch_minimizer.agent_adapters.agent_adapter_base.parsers import (
    UnknownTrajectoryFormat,
    parse_trajectory_payload as _registry_parse,
)
from patch_minimizer.agent_adapters.agent_adapter_base.parsers.trajectory_types import (
    CandidateTrajectory,
)

logger = logging.getLogger(__name__)

REPO_TREE_PREFIX: str | None = None

PatchLists = List[List[Tuple[None, List[Edit]]]]


def is_linux_tree_path(raw_path: str) -> bool:
    """Return True if *raw_path* resolves to a file inside the repo tree."""
    assert REPO_TREE_PREFIX is not None, (
        "REPO_TREE_PREFIX not set — parser __init__ must set it before parsing"
    )
    if raw_path.startswith(REPO_TREE_PREFIX):
        return True
    return raw_path.lstrip("/").startswith("linux/")


def normalize_linux_repo_path(raw_path: str) -> str:
    """Strip the repo tree prefix so paths match the local repo layout.

    AIDEV-NOTE: Host scratch under ``/tmp/`` normalizes to ``tmp/...`` after
    ``lstrip("/")``; that is not inside the checked-out tree. Return
    ``""`` so callers drop those edits. Paths under ``<prefix>/tmp/`` still map
    to ``tmp/...`` via the ``REPO_TREE_PREFIX`` branch first (SWE / kGym).
    """
    assert REPO_TREE_PREFIX is not None, (
        "REPO_TREE_PREFIX not set — parser __init__ must set it before parsing"
    )
    if raw_path.startswith(REPO_TREE_PREFIX):
        return raw_path[len(REPO_TREE_PREFIX):]
    if raw_path.startswith("/tmp/") or raw_path.rstrip("/") == "/tmp":
        return ""
    stripped = raw_path.lstrip("/")
    if stripped.startswith("linux/"):
        return stripped[len("linux/"):]
    if stripped.startswith("tmp/") or stripped == "tmp":
        return ""
    return stripped


def to_patch_list(
    candidate: CandidateTrajectory,
) -> List[Tuple[None, List[Edit]]]:
    """Convert a ``CandidateTrajectory`` to ``patch_list``.

    ``parent_diff`` is always ``None`` (no instrumentation). Edits with an
    empty filename are dropped; revisions whose edits are all empty are
    omitted from the patch list entirely.
    """
    patch_list: List[Tuple[None, List[Edit]]] = []
    for revision in candidate.revisions:
        live_edits = [e for e in revision.edits if e.filename]
        if live_edits:
            patch_list.append((None, live_edits))
    return patch_list


def stamp_global_edit_idx(
    patch_list: List[Tuple[None, List[Edit]]],
) -> None:
    """Assign monotonic ``global_edit_idx`` to every edit in one patch list.

    Mutates in place. Call once per candidate's patch list so indices are
    unique within the candidate but not across candidates.
    """
    idx = 0
    for _parent_diff, edits in patch_list:
        for e in edits:
            e.global_edit_idx = idx
            idx += 1


def parse_trajectory_with_patch_lists(
    data: dict, *, source_label: str
) -> Tuple[List[CandidateTrajectory], PatchLists]:
    """Dispatch via ``parser_registry``, build patch lists, stamp ``global_edit_idx``.

    Returns ``([], [])`` when no registered parser accepts ``data`` — matches
    the pre-refactor behavior that every caller depends on.
    """
    try:
        candidates = _registry_parse(data, source_label=source_label)
    except UnknownTrajectoryFormat as exc:
        logger.debug("Skip %s: %s", source_label, exc)
        return [], []

    if not candidates:
        logger.info("No successful candidates in %s", source_label)
        return [], []

    patch_lists: PatchLists = []
    non_empty = 0
    for cand in candidates:
        pl = to_patch_list(cand)
        patch_lists.append(pl)
        if pl:
            non_empty += 1
        else:
            logger.warning(
                "Candidate at step %d produced empty patch_list "
                "(all revisions had 0 edits)",
                cand.success_step,
            )

    for pl in patch_lists:
        stamp_global_edit_idx(pl)

    logger.info(
        "Parsed %s: %d candidates, %d with edits",
        source_label,
        len(candidates),
        non_empty,
    )
    return candidates, patch_lists


def _extract_file_paths_from_command(command: str) -> list[str]:
    """Extract ``.py`` file paths from a feedback command string.

    Splits on whitespace and collects tokens ending in ``.py``,
    stripping pytest ``::Class::test`` suffixes.
    """
    paths: list[str] = []
    for token in command.split():
        base = token.split("::")[0]
        if base.endswith(".py"):
            paths.append(base)
    return paths


# AIDEV-NOTE: Must be called AFTER parsing (REPO_TREE_PREFIX set by parser __init__).
def extract_test_files_from_trajectory(
    candidate: CandidateTrajectory,
) -> dict[str, str]:
    """Collect final content of files referenced by feedback commands.

    Returns ``{relative_path: file_content}`` for each file that (1) appears
    in a feedback command and (2) was created or modified by an edit in the
    trajectory.  Uses the **last** edit's ``after`` content (final version).
    """
    referenced: set[str] = set()
    for revision in candidate.revisions:
        for cmd_entry in revision.feedback_commands:
            for path in _extract_file_paths_from_command(cmd_entry["command"]):
                normalized = normalize_linux_repo_path(path) if path.startswith("/") else path
                if normalized:
                    referenced.add(normalized)

    if not referenced:
        return {}

    test_files: dict[str, str] = {}
    for revision in candidate.revisions:
        for edit in revision.edits:
            if edit.filename in referenced:
                test_files[edit.filename] = edit.after

    return test_files


def load_trajectory_file(
    run_json_path: str | Path,
) -> Tuple[List[CandidateTrajectory], PatchLists]:
    """Open ``run_json_path`` and delegate to ``parse_trajectory_with_patch_lists``."""
    logger.info("Loading trajectory from %s", run_json_path)
    with open(run_json_path, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, list):
        # AIDEV-NOTE: OpenHands traj.json files are a bare list of event dicts.
        # Wrap in {"events": ...} so the OpenHands parser can match on the key.
        raw = {"events": raw}
    elif not isinstance(raw, dict):
        logger.warning(
            "%s: expected JSON object or array, got %s",
            run_json_path,
            type(raw).__name__,
        )
        return [], []
    return parse_trajectory_with_patch_lists(
        raw, source_label=str(run_json_path)
    )
