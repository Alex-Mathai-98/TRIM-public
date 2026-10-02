"""OpenHands ``cp <src> <dest>`` statement (inside a ``run`` action)."""
from __future__ import annotations

from patch_minimizer.agent_adapters.agent_adapter_base.statements import CpStatementBase
from patch_minimizer.agent_adapters.openhands_adapter.statements.openhands_run_statement import (
    OpenHandsRunStatement,
)


class CpStatement(CpStatementBase, OpenHandsRunStatement):
    """``cp <src> <dest>`` run by an OpenHands ``TerminalAction``; success = not rejected by OpenHands."""
