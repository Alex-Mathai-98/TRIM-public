"""mini-swe ``cat << EOF > file`` / ``cat > file << EOF`` statement."""
from __future__ import annotations

from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.cat_heredoc_parsing import (
    heredoc_edits,
    is_cat_heredoc,
)
from patch_minimizer.agent_adapters.mini_swe_agent_adapter.statements.mini_swe_edit_statement import (
    MiniSweEditStatement,
)
from patch_minimizer.core.edit import Edit


class CatHeredocStatement(MiniSweEditStatement):
    """Whole-file overwrite via ``cat`` heredoc (both redirect orderings)."""

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        return is_cat_heredoc(action)

    def to_edits(self, explanation_prefix: str) -> list[Edit]:
        return heredoc_edits(self.action, explanation_prefix)
