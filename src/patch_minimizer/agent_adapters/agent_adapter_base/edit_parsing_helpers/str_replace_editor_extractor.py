"""``str_replace_editor`` CLI edit extractor.

Tokenizes the action text with ``shlex.split(..., posix=True)`` and
extracts ``str_replace_editor str_replace`` / ``str_replace_editor insert``
invocations via ``argparse.parse_known_args``. Used by both classic SWE
and mini-swe payloads (same CLI surface). OpenHands has its own extractor.

AIDEV-NOTE: the shell-quote parsing is delegated to stdlib ``shlex`` —
it is the reference implementation for POSIX single/double-quote concat
(incl. the ``'"'"'`` idiom common in SWE-agent trajectories).
``parse_known_args`` tolerates stray flags like ``--file_text ''``.
"""
from __future__ import annotations

import argparse
import shlex

from patch_minimizer.core.edit import Edit, EditType, MatchMode
from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.action_edit_extractor import (
    ActionEditExtractor,
)
from patch_minimizer.agent_adapters.agent_adapter_base.utils import (
    normalize_linux_repo_path,
)


def is_str_replace_editor_command(text: str) -> bool:
    """Return ``True`` if ``text`` contains any ``str_replace_editor`` invocation."""
    return "str_replace_editor" in text


def is_str_replace_editor_str_replace(text: str) -> bool:
    return "str_replace_editor str_replace" in text


def is_str_replace_editor_insert(text: str) -> bool:
    return "str_replace_editor insert" in text


def is_str_replace_editor_create(text: str) -> bool:
    return "str_replace_editor create" in text


def _build_ap() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("path")
    ap.add_argument("--old_str")
    ap.add_argument("--new_str")
    ap.add_argument("--insert_line", type=int)
    ap.add_argument("--file_text")  # absorbed; ignored
    return ap


_AP = _build_ap()


def _tokenize(action: str) -> list[str] | None:
    try:
        return shlex.split(action, posix=True)
    except ValueError:
        return None


def _find_all_invocations(tokens: list[str]) -> list[tuple[str, int, int]]:
    """Return ``(subcmd, arg_start, arg_end)`` for every ``str_replace_editor`` invocation, in order."""
    boundaries: list[int] = [
        i for i, t in enumerate(tokens) if t == "str_replace_editor"
    ]
    result: list[tuple[str, int, int]] = []
    for k, start in enumerate(boundaries):
        if start + 1 >= len(tokens):
            continue
        subcmd = tokens[start + 1]
        arg_start = start + 2
        arg_end = boundaries[k + 1] if k + 1 < len(boundaries) else len(tokens)
        result.append((subcmd, arg_start, arg_end))
    return result


def _parse_one_str_replace(tokens: list[str], start: int, end: int) -> Edit | None:
    try:
        ns, _ = _AP.parse_known_args(tokens[start:end])
    except SystemExit:
        return None
    if ns.path is None or ns.old_str is None or ns.new_str is None:
        return None
    return Edit(
        filename=normalize_linux_repo_path(ns.path),
        before=ns.old_str,
        after=ns.new_str,
        explanation="",
        edit_type=EditType.REPLACE,
        match_mode=MatchMode.EXACT,
    )


def _parse_one_insert(tokens: list[str], start: int, end: int) -> Edit | None:
    # AIDEV-NOTE: insert_line+1 aligns SWE-agent "insert AFTER line N" with EditApplier's 1-based indexing
    try:
        ns, _ = _AP.parse_known_args(tokens[start:end])
    except SystemExit:
        return None
    if ns.path is None or ns.new_str is None or ns.insert_line is None:
        return None
    return Edit(
        filename=normalize_linux_repo_path(ns.path),
        before="",
        after=ns.new_str,
        starting_line=ns.insert_line + 1,
        explanation="",
        edit_type=EditType.INSERT_AFTER_LINE,
        is_str_replace_cmd=True,
    )


def _parse_one_create(tokens: list[str], start: int, end: int) -> Edit | None:
    try:
        ns, _ = _AP.parse_known_args(tokens[start:end])
    except SystemExit:
        return None
    if ns.path is None or ns.file_text is None:
        return None
    return Edit(
        filename=normalize_linux_repo_path(ns.path),
        before="",
        after=ns.file_text,
        explanation="",
        edit_type=EditType.WHOLE_FILE_ACTIONS,
    )


_SUBCMD_PARSERS = {
    "str_replace": _parse_one_str_replace,
    "insert": _parse_one_insert,
    "create": _parse_one_create,
}


class StrReplaceEditorExtractor(ActionEditExtractor):
    """Turns one ``str_replace_editor`` CLI blob into a list of ``Edit``.

    Used by both ``ClassicSWEAgentTrajectoryParser`` and
    ``MiniSweMessagesTrajectoryParser``; they construct it with a
    format-specific ``explanation_prefix`` so downstream tools can trace
    each edit back to its source (e.g. ``"classic_swe step 17"``).

    Handles ``str_replace``, ``insert``, and ``create`` commands.
    """

    def __init__(self, explanation_prefix: str = "str_replace_editor edit") -> None:
        self.explanation_prefix = explanation_prefix

    def extract_edits(
        self, action_text: str, explanation_prefix: str | None = None
    ) -> list[Edit]:
        prefix = explanation_prefix or self.explanation_prefix
        tokens = _tokenize(action_text)
        if tokens is None:
            return []
        edits: list[Edit] = []
        for subcmd, start, end in _find_all_invocations(tokens):
            parser = _SUBCMD_PARSERS.get(subcmd)
            if parser is None:
                continue
            edit = parser(tokens, start, end)
            if edit is not None:
                label = "" if subcmd == "str_replace" else f" {subcmd}"
                edit.explanation = f"{prefix}{label} #{len(edits) + 1}"
                edits.append(edit)
        return edits
