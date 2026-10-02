"""SWE-agent success semantics shared by every SWE edit statement."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from patch_minimizer.agent_adapters.agent_adapter_base.statements import Statement

if TYPE_CHECKING:
    from patch_minimizer.agent_adapters.swe_agent_adapter.parsers import (
        ClassicSWEAgentTrajectoryParser,
    )

logger = logging.getLogger(__name__)


class SWEEditStatement(Statement):
    """Two-gate SWE success rule: observation text, then ``state.diff`` changed.

    Subclasses tune the gates with ``observation_ok`` and ``bypasses_diff_gate``.
    """

    # AIDEV-NOTE: rm/cp operate on potentially untracked/gitignored files —
    # state.diff won't reflect the change, so they bypass the diff-equality check.
    bypasses_diff_gate: ClassVar[bool] = False

    @property
    def observation(self) -> str:
        return str(self.step.get("observation") or "")

    def observation_ok(self) -> bool:
        """Gate 1. Default ``True`` — shell commands (sed/rm/cp) have empty observations."""
        return True

    def is_successful(self, parser: ClassicSWEAgentTrajectoryParser) -> bool:
        if not self.observation_ok():
            return False
        # AIDEV-NOTE: Gate 2 — state.diff is ground truth from the docker env; observations
        # can be unreliable (concatenated outputs). Skipped for file creations: created files
        # outside the repo (e.g. /patch_tun.py) don't appear in state.diff.
        if "File created successfully" in self.observation:
            return True
        # AIDEV-NOTE: bypass is keyed on *any* bypassing statement matching the action, not
        # just the dispatched one (``rm x && sed -i …`` dispatches to sed but still bypasses).
        if any(
            cls.bypasses_diff_gate and cls.matches(self.action, self.step)
            for cls in parser.EDIT_STATEMENTS
        ):
            return True
        current_diff = self.step.get("state", {}).get("diff", None)
        if current_diff is not None and current_diff == parser._last_diff:
            logger.debug("step diff unchanged — edit had no effect")
            return False
        return True
