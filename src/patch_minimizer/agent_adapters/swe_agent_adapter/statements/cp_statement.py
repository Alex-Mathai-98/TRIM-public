"""SWE-agent ``cp`` statement."""
from __future__ import annotations

from typing import ClassVar

from patch_minimizer.agent_adapters.agent_adapter_base.statements import CpStatementBase
from patch_minimizer.agent_adapters.swe_agent_adapter.statements.swe_edit_statement import (
    SWEEditStatement,
)


class CpStatement(CpStatementBase, SWEEditStatement):
    """``cp <src> <dest>`` in a SWE-agent ``action``; bypasses the ``state.diff`` gate."""

    bypasses_diff_gate: ClassVar[bool] = True
