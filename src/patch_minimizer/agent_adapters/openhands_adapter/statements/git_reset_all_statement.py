"""OpenHands whole-tree ``git reset --hard`` revert statement (detection-only)."""
from __future__ import annotations

import re

from patch_minimizer.agent_adapters.agent_adapter_base.statements import RevertStatement

# AIDEV-NOTE: Only matches whole-tree reset (no file args). Per-file
# ``git reset --hard <file>`` is handled by ``GitResetFileStatement`` instead.
_GIT_RESET_HARD_ALL_RE = re.compile(
    r"\bgit\s+reset\s+--hard\b"
    r"(?:\s+HEAD)?"
    r"\s*(?:[;&|\n]|$)"
)


class GitResetAllStatement(RevertStatement):
    """``git reset --hard [HEAD]`` in a ``run`` action; assumed successful (no diff to check)."""

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        return step.get("action") == "run" and bool(_GIT_RESET_HARD_ALL_RE.search(action))
