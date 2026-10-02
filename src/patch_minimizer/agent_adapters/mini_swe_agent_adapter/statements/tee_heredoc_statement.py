"""mini-swe ``tee <file> << EOF`` statement."""
from __future__ import annotations

from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.tee_heredoc_parsing import (
    is_tee_heredoc,
    tee_heredoc_edits,
)
from patch_minimizer.agent_adapters.mini_swe_agent_adapter.statements.mini_swe_edit_statement import (
    MiniSweEditStatement,
)
from patch_minimizer.core.edit import Edit


class TeeHeredocStatement(MiniSweEditStatement):
    """Whole-file overwrite via ``tee`` heredoc."""

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        return is_tee_heredoc(action)

    def to_edits(self, explanation_prefix: str) -> list[Edit]:
        return tee_heredoc_edits(self.action, explanation_prefix)
