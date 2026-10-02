"""mini-swe whole-tree ``git reset --hard`` revert statement (detection-only)."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from patch_minimizer.agent_adapters.agent_adapter_base.statements import RevertStatement

if TYPE_CHECKING:
    from patch_minimizer.agent_adapters.agent_adapter_base import TrajectoryParser

_GIT_RESET_HARD_ALL_RE = re.compile(
    r"\bgit\s+reset\s+--hard\b(?:\s+HEAD)?\s*(?:[;&|\n]|$)"
)


class GitResetAllStatement(RevertStatement):
    """``git reset --hard [HEAD]`` (no file args) in one bash block."""

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        return bool(_GIT_RESET_HARD_ALL_RE.search(action))

    def is_successful(self, parser: TrajectoryParser) -> bool:
        """AIDEV-NOTE: check "HEAD is now at" rather than rc — compound commands
        (``git reset --hard && cat …``) can have rc!=0 from a later command."""
        return "HEAD is now at" in self.step.get("user_response", "")
