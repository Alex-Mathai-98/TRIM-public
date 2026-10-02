"""Parse ``cat <<EOF > file`` heredoc patterns into ``WHOLE_FILE_ACTIONS`` edits."""
from __future__ import annotations

import re
from typing import Iterator

from patch_minimizer.core.edit import Edit, EditType
from patch_minimizer.agent_adapters.agent_adapter_base.utils import (
    normalize_linux_repo_path,
)

def is_cat_heredoc(text: str) -> bool:
    """Return ``True`` if ``text`` contains a ``cat << > file`` heredoc pattern."""
    return "cat" in text and "<<" in text and ">" in text


# AIDEV-NOTE: ``cat << EOF > file`` — heredoc operator first, redirect after.
_HEREDOC_RE = re.compile(
    r"""
    cat\s+                     # cat command
    <<\s*                      # heredoc operator
    (?P<quoted>'|")?           # optional quoting of delimiter
    (?P<delim>[A-Za-z_]\w*)    # delimiter word (e.g. EOF)
    (?(quoted)(?P=quoted))     # matching close quote
    \s*>\s*                    # redirection
    (?P<filepath>\S+)          # target file
    \s*\n                      # newline before body
    (?P<body>.*?)              # heredoc body (non-greedy)
    \n(?P=delim)               # closing delimiter on its own line
    (?:\s*$|\n)                # end of block or more commands after
    """,
    re.VERBOSE | re.DOTALL,
)

# AIDEV-NOTE: ``cat > file << EOF`` — redirect first, heredoc operator after.
# Both orderings are valid bash and seen in mini-swe trajectories. The
# ``patch -p1 < /tmp/fix.patch`` flow uses the redirect-first form, so
# missing this regex variant produced empty candidates (Phase 3.2 fix).
_HEREDOC_RE_REDIRECT_FIRST = re.compile(
    r"""
    cat\s+                     # cat command
    >\s*                       # redirection (target file)
    (?P<filepath>\S+)          # target file
    \s*<<\s*                   # heredoc operator
    (?P<quoted>'|")?           # optional quoting of delimiter
    (?P<delim>[A-Za-z_]\w*)    # delimiter word (e.g. EOF)
    (?(quoted)(?P=quoted))     # matching close quote
    \s*\n                      # newline before body
    (?P<body>.*?)              # heredoc body (non-greedy)
    \n(?P=delim)               # closing delimiter on its own line
    (?:\s*$|\n)                # end of block or more commands after
    """,
    re.VERBOSE | re.DOTALL,
)


def iter_cat_heredocs(bash_block: str) -> Iterator[tuple[str, str]]:
    """Yield ``(filepath, body)`` for every ``cat`` heredoc in ``bash_block``,
    handling both ``cat << EOF > file`` and ``cat > file << EOF`` orderings.
    """
    for m in _HEREDOC_RE.finditer(bash_block):
        yield m.group("filepath"), m.group("body")
    for m in _HEREDOC_RE_REDIRECT_FIRST.finditer(bash_block):
        yield m.group("filepath"), m.group("body")


def heredoc_edits(bash_block: str, explanation_prefix: str) -> list[Edit]:
    """Extract ``Edit`` objects from ``cat <<EOF > file`` in a bash block."""
    edits: list[Edit] = []
    for raw_path, body in iter_cat_heredocs(bash_block):
        filename = normalize_linux_repo_path(raw_path)
        if not filename or not body.strip():
            continue
        edits.append(
            Edit(
                filename=filename,
                before="",
                after=body,
                explanation=f"{explanation_prefix} cat heredoc overwrite",
                edit_type=EditType.WHOLE_FILE_ACTIONS,
            )
        )
    return edits
