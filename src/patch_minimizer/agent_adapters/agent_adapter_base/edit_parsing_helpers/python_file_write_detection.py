"""Detect inline Python scripts that write files (without extracting edits)."""
from __future__ import annotations

import re

_PYTHON_FILE_WRITE_RE = re.compile(
    r"""python[3c]?\s+(?:-c\s+)?['"].*?open\s*\(""",
    re.DOTALL,
)

_PYTHON_SCRIPT_RE = re.compile(
    r"""
    python[3c]?\s*<<\s*       # python with heredoc
    (?P<quoted>'|")?
    (?P<delim>[A-Za-z_]\w*)
    (?(quoted)(?P=quoted))
    \s*\n
    (?P<body>.*?)
    \n(?P=delim)
    """,
    re.VERBOSE | re.DOTALL,
)


def detect_python_file_edit(bash_block: str) -> bool:
    """Return ``True`` if the block contains a Python script that writes files.

    AIDEV-NOTE: We detect but do not extract edits from inline Python
    scripts because reliably parsing arbitrary Python for before/after
    content is infeasible. The parser logs a warning so coverage gaps
    are visible.
    """
    return bool(
        _PYTHON_FILE_WRITE_RE.search(bash_block)
        or _PYTHON_SCRIPT_RE.search(bash_block)
    )
