"""Shared ``cp`` statement: parses ``(source, dest)`` pairs once; adapters add the success rule."""
from __future__ import annotations

from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.cp_command_parsing import (
    cp_edits_for_pairs,
    extract_cp_pairs,
    is_cp_command,
)
from patch_minimizer.agent_adapters.agent_adapter_base.statements.statement import (
    Statement,
)
from patch_minimizer.core.edit import Edit


class CpStatementBase(Statement):
    """``cp <src> <dest>`` → one ``COPY_FILE`` edit per pair (``before`` = source).

    AIDEV-NOTE: like ``rm``, detection is ``startswith("cp ")`` only.
    """

    pairs: list[tuple[str, str]]

    def _parse(self) -> None:
        self.pairs = extract_cp_pairs(self.action)

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        return is_cp_command(action)

    def to_edits(self, explanation_prefix: str) -> list[Edit]:
        return cp_edits_for_pairs(self.pairs, explanation_prefix)
