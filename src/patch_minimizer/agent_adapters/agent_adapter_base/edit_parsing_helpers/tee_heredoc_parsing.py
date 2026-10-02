"""Parse ``tee <file> <<EOF`` heredoc patterns into ``WHOLE_FILE_ACTIONS`` edits."""
from __future__ import annotations

import re

from patch_minimizer.core.edit import Edit, EditType
from patch_minimizer.agent_adapters.agent_adapter_base.utils import (
    normalize_linux_repo_path,
)

def is_tee_heredoc(text: str) -> bool:
    """Return ``True`` if ``text`` contains a ``tee ... <<`` heredoc pattern."""
    return "tee" in text and "<<" in text


_TEE_HEREDOC_RE = re.compile(
    r"""
    tee\s+                     # tee command
    (?P<filepath>\S+)          # target file
    \s*<<\s*                   # heredoc operator
    (?P<quoted>'|")?           # optional quoting
    (?P<delim>[A-Za-z_]\w*)
    (?(quoted)(?P=quoted))
    \s*\n
    (?P<body>.*?)
    \n(?P=delim)
    (?:\s*$|\n)
    """,
    re.VERBOSE | re.DOTALL,
)


def tee_heredoc_edits(bash_block: str, explanation_prefix: str) -> list[Edit]:
    """Extract edits from ``tee <file> <<EOF`` patterns."""
    edits: list[Edit] = []
    for m in _TEE_HEREDOC_RE.finditer(bash_block):
        raw_path = m.group("filepath")
        body = m.group("body")
        filename = normalize_linux_repo_path(raw_path)
        if not filename or not body.strip():
            continue
        edits.append(
            Edit(
                filename=filename,
                before="",
                after=body,
                explanation=f"{explanation_prefix} tee heredoc",
                edit_type=EditType.WHOLE_FILE_ACTIONS,
            )
        )
    return edits
