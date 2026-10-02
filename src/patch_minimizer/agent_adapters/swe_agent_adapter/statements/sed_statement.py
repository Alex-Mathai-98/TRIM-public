"""SWE-agent ``sed -i`` statement."""
from __future__ import annotations

from patch_minimizer.agent_adapters.agent_adapter_base.statements import (
    SedStatementBase,
)
from patch_minimizer.agent_adapters.swe_agent_adapter.statements.swe_edit_statement import (
    SWEEditStatement,
)


class SedStatement(SedStatementBase, SWEEditStatement):
    """``sed -i`` in a SWE-agent ``action``.

    AIDEV-NOTE: sed -i always produces an empty observation across all 1602 trajectories —
    success vs no-op is indistinguishable from the text, so gate 1 passes and the
    ``state.diff`` gate (plus downstream ``check_patch_range``) decides.
    """
