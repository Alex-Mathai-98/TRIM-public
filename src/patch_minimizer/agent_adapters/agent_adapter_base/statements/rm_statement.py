"""Shared ``rm`` statement: parses deleted paths once; adapters add the success rule."""
from __future__ import annotations

from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.rm_command_parsing import (
    extract_rm_targets,
    is_rm_command,
    rm_edits_for_targets,
)
from patch_minimizer.agent_adapters.agent_adapter_base.statements.statement import (
    Statement,
)
from patch_minimizer.core.edit import Edit


class RMStatementBase(Statement):
    """``rm <paths>`` → one ``WHOLE_FILE_DELETE`` edit per repo path.

    AIDEV-NOTE: ``is_rm_command`` is ``startswith("rm ")`` — ``cd x && rm f`` is NOT
    detected. Widening it changes benchmark goldens; do it deliberately, not in a refactor.
    """

    targets: list[str]

    def _parse(self) -> None:
        self.targets = extract_rm_targets(self.action)

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        return is_rm_command(action)

    def to_edits(self, explanation_prefix: str) -> list[Edit]:
        return rm_edits_for_targets(self.targets, explanation_prefix)
