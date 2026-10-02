"""Detect ``git checkout/restore/reset --hard <files>`` and extract target paths.

AIDEV-NOTE: Shared by mini-swe and OpenHands adapters for per-file revert detection.
"""
from __future__ import annotations

import re

from patch_minimizer.agent_adapters.agent_adapter_base.utils import (
    normalize_linux_repo_path,
)

_GIT_RESET_FILES_RE = re.compile(
    r"\bgit\s+(?:-C\s+\S+\s+)?(?P<subcmd>checkout|restore|reset\s+--hard)\s+"
    r"(?P<args>[^\n;&|]+)"
)

_RESET_FILE_EXT_RE = re.compile(
    r"\.(?:c|h|S|rs|sh|py|md|txt|ya?ml|json|cfg|conf|ld|dts|dtsi|s)$",
    re.IGNORECASE,
)
_RESET_FILE_NAME_RE = re.compile(
    r"^(?:Makefile|Kconfig|Kbuild|MAINTAINERS)(?:\..+)?$"
)


def _is_staged_only_restore(m: re.Match) -> bool:
    """``git restore --staged`` without ``--worktree`` is an unstage-only no-op."""
    if m.group("subcmd") != "restore":
        return False
    args = m.group("args")
    return "--staged" in args and "--worktree" not in args


def is_git_reset_file_command(text: str) -> bool:
    """Return ``True`` if *text* contains ``git checkout/restore/reset --hard <files>``."""
    return any(
        not _is_staged_only_restore(m)
        for m in _GIT_RESET_FILES_RE.finditer(text)
    )


def extract_git_reset_file_targets(text: str) -> list[str]:
    """Extract and normalize file paths from ``git checkout/restore/reset --hard``.

    Filters out flags, branch names, and non-file tokens. Paths are normalized
    via ``normalize_linux_repo_path`` so they match edit filenames 1:1.
    """
    out: list[str] = []
    for m in _GIT_RESET_FILES_RE.finditer(text):
        if _is_staged_only_restore(m):
            continue
        tail = m.group("args").strip()
        tokens = tail.split()
        while tokens and tokens[0] in {"HEAD", "--"}:
            tokens.pop(0)
        for tok in tokens:
            if not tok or tok.startswith("-"):
                continue
            if "/" not in tok and not (
                _RESET_FILE_EXT_RE.search(tok) or _RESET_FILE_NAME_RE.search(tok)
            ):
                continue
            fn = normalize_linux_repo_path(tok)
            if fn:
                out.append(fn)
    return out
