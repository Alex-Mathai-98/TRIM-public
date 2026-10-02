"""Shared ``sed -i`` statement — keeps the raw command; parsing stays in ``sed_parsing``."""
from __future__ import annotations

from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers import (
    is_sed_command,
    sed_edits,
)
from patch_minimizer.agent_adapters.agent_adapter_base.statements.statement import (
    Statement,
)
from patch_minimizer.core.edit import Edit


class SedStatementBase(Statement):
    """``sed -i …`` → edits from ``sed_edits``.

    AIDEV-NOTE: deliberately no intermediate parsed form — ``sed_parsing.py`` is the single
    source of truth for sed grammar; duplicating it here would drift.
    """

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        return is_sed_command(action)

    def to_edits(self, explanation_prefix: str) -> list[Edit]:
        return sed_edits(self.action, explanation_prefix)
