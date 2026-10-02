"""Detect ``patch`` / ``git apply`` commands in bash blocks."""
from __future__ import annotations

import re

# AIDEV-NOTE: The ``\bpatch\s+<(?!<)`` form is critical: matching ``patch\s+<``
# alone false-positives on ``cat > /tmp/fix.patch << 'EOF'`` (the substring
# ``patch <`` appears between the heredoc filename and the heredoc operator).
# The negative lookahead ``(?!<)`` excludes the ``<<`` heredoc form.
# ``\b`` ensures we match the standalone ``patch`` command, not ``fix.patch``.
_PATCH_CMD_RE = re.compile(
    r"\bpatch\s+(?:[^|&;\n]*\s)?-p\d|\bpatch\s+<(?!<)|\bgit\s+apply\b",
)


def is_patch_command(bash_block: str) -> bool:
    """Return ``True`` if the block applies a patch file."""
    return bool(_PATCH_CMD_RE.search(bash_block))


# AIDEV-NOTE: Back-compat alias — remove once all callers migrate.
detect_patch_cmd = is_patch_command
