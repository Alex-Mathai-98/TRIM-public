"""OpenHands ``rm <paths>`` statement (inside a ``run`` action)."""
from __future__ import annotations

from patch_minimizer.agent_adapters.agent_adapter_base.statements import RMStatementBase
from patch_minimizer.agent_adapters.openhands_adapter.statements.openhands_run_statement import (
    OpenHandsRunStatement,
)


class RMStatement(RMStatementBase, OpenHandsRunStatement):
    """``rm <paths>`` run by an OpenHands ``TerminalAction``; success = not rejected by OpenHands."""
