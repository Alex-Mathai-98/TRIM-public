"""mini-swe ``sed -i`` statement."""
from __future__ import annotations

from patch_minimizer.agent_adapters.agent_adapter_base.statements import SedStatementBase
from patch_minimizer.agent_adapters.mini_swe_agent_adapter.statements.mini_swe_edit_statement import (
    MiniSweEditStatement,
)


class SedStatement(SedStatementBase, MiniSweEditStatement):
    """``sed -i`` in a mini-swe bash block; success is step-level (see ``step_succeeded``)."""
