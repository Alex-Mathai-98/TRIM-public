"""OpenHands success rule shared by shell (``run`` action) edit statements."""
from __future__ import annotations

from typing import TYPE_CHECKING

from patch_minimizer.agent_adapters.agent_adapter_base.statements import Statement

if TYPE_CHECKING:
    from patch_minimizer.agent_adapters.openhands_adapter.parsers import (
        KarenaOpenHandsTrajectoryParser,
    )


class OpenHandsRunStatement(Statement):
    """A shell command inside an OpenHands ``run`` action.

    AIDEV-NOTE: success = OpenHands did not reject the action (no ``observation="error"``
    event caused by it). rm/cp/sed may touch untracked files, so there is no diff check.
    """

    def is_successful(self, parser: KarenaOpenHandsTrajectoryParser) -> bool:
        return str(self.step.get("id") or "") not in parser._run_error_ids
