"""OpenHands ``sed -i`` statement (inside a ``run`` action)."""
from __future__ import annotations

from patch_minimizer.agent_adapters.agent_adapter_base.statements import SedStatementBase
from patch_minimizer.agent_adapters.openhands_adapter.statements.openhands_run_statement import (
    OpenHandsRunStatement,
)


class SedStatement(SedStatementBase, OpenHandsRunStatement):
    """``sed -i`` run by an OpenHands ``TerminalAction``; success = not rejected by OpenHands."""
