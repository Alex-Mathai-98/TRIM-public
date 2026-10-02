"""SWE-agent ``rm`` statement."""
from __future__ import annotations

from typing import ClassVar

from patch_minimizer.agent_adapters.agent_adapter_base.statements import RMStatementBase
from patch_minimizer.agent_adapters.swe_agent_adapter.statements.swe_edit_statement import (
    SWEEditStatement,
)


class RMStatement(RMStatementBase, SWEEditStatement):
    """``rm <paths>`` in a SWE-agent ``action``.

    Success: always (observation is empty), and ``state.diff`` gate bypassed because
    deleted files may be untracked/gitignored.
    """

    bypasses_diff_gate: ClassVar[bool] = True
