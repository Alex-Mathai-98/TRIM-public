"""mini-swe ``patch`` / ``git apply`` statement (body looked up in the heredoc memo)."""
from __future__ import annotations

from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.patch_command_detection import (
    is_patch_command,
)
from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.patch_file_extractor import (
    extract_edits_from_patch_cmd,
    is_valid_patch_command,
)
from patch_minimizer.agent_adapters.mini_swe_agent_adapter.statements.mini_swe_edit_statement import (
    MiniSweEditStatement,
)
from patch_minimizer.core.edit import Edit


class PatchStatement(MiniSweEditStatement):
    """``patch -p1 < /tmp/fix.patch`` / ``git apply …`` → per-hunk edits.

    Reads ``step["patch_file_bodies"]`` (cumulative heredoc memo built by
    ``_messages_to_steps``) and ``step["user_response"]`` (drops ``Hunk #N FAILED`` hunks).

    AIDEV-NOTE: ``is_edit`` (a memoized body exists) is stricter than ``matches`` (any
    patch/git-apply command). ``matches`` drives extraction so that an unresolvable patch
    still reaches ``BashEditExtractor``'s "detected but cannot extract" warning.
    """

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        return is_patch_command(action)

    @classmethod
    def is_edit(cls, action: str, step: dict) -> bool:
        return is_valid_patch_command(action, step.get("patch_file_bodies") or {})

    def to_edits(self, explanation_prefix: str) -> list[Edit]:
        return extract_edits_from_patch_cmd(
            self.action,
            self.step.get("patch_file_bodies"),
            explanation_prefix,
            user_response=self.step.get("user_response", ""),
        )
