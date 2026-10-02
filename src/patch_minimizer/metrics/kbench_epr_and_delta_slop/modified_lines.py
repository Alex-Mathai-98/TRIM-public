"""Compute modified lines from a patch file using methodology_3.

methodology_3: count added + removed lines in .c/.h files, excluding new files.
"""
from __future__ import annotations

import re
from pathlib import Path

from unidiff import PatchSet

_LINUX_DIR_RE = re.compile(r"collection_of_linux_repos/linux-[A-Za-z0-9_-]+/")


def modified_lines(patch: str | Path) -> int:
    """Return the number of modified lines (.c/.h, excluding new files).

    Args:
        patch: Either a patch string or a Path to a patch file.
    """
    if isinstance(patch, Path):
        patch = patch.read_text(errors="replace")
    normalized = _LINUX_DIR_RE.sub("", patch)
    ps = PatchSet.from_string(normalized)
    return sum(
        sum(h.added + h.removed for h in pf)
        for pf in ps
        if _is_c_or_h(pf) and not pf.is_added_file
    )


def _is_c_or_h(pf) -> bool:
    path = pf.path.removeprefix("a/").removeprefix("b/")
    return path.endswith(".c") or path.endswith(".h")
