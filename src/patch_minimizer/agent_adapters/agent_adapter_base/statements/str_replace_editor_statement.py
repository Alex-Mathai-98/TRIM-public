"""Shared ``str_replace_editor`` CLI statement (SWE-agent + mini-swe)."""
from __future__ import annotations

from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.str_replace_editor_extractor import (
    StrReplaceEditorExtractor,
    is_str_replace_editor_command,
)
from patch_minimizer.agent_adapters.agent_adapter_base.statements.statement import (
    Statement,
)
from patch_minimizer.core.edit import Edit


class StrReplaceEditorStatementBase(Statement):
    """Any ``str_replace_editor`` invocation → edits for its str_replace/insert/create subcmds.

    ``matches`` accepts *any* subcommand (``view`` included) because that is the dispatch
    predicate the parsers used; ``view``-only actions simply yield no edits.
    """

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        return is_str_replace_editor_command(action)

    def to_edits(self, explanation_prefix: str) -> list[Edit]:
        # AIDEV-NOTE: extractor is stateless apart from its default prefix, and
        # ``extract_edits`` falls back to that default when the prefix is "" — so building
        # it with ``explanation_prefix`` reproduces both SWE and mini-swe call styles.
        return StrReplaceEditorExtractor(explanation_prefix=explanation_prefix).extract_edits(
            self.action
        )
