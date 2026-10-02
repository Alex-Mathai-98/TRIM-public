"""SWE-agent ``str_replace_editor`` statement."""
from __future__ import annotations

from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.str_replace_editor_extractor import (
    is_str_replace_editor_create,
    is_str_replace_editor_insert,
    is_str_replace_editor_str_replace,
)
from patch_minimizer.agent_adapters.agent_adapter_base.statements import (
    StrReplaceEditorStatementBase,
)
from patch_minimizer.agent_adapters.swe_agent_adapter.statements.swe_edit_statement import (
    SWEEditStatement,
)


class StrReplaceEditorStatement(StrReplaceEditorStatementBase, SWEEditStatement):
    """``str_replace_editor str_replace|insert|create …`` in a SWE-agent ``action``.

    AIDEV-NOTE: ``is_edit`` (edit-step gate) is narrower than ``matches`` (dispatch): only
    str_replace/insert/create make a step an edit step, but once it is one, *any*
    ``str_replace_editor`` in the action wins dispatch over sed/rm/cp (first in the list).
    """

    @classmethod
    def is_edit(cls, action: str, step: dict) -> bool:
        return (
            is_str_replace_editor_str_replace(action)
            or is_str_replace_editor_insert(action)
            or is_str_replace_editor_create(action)
        )

    def observation_ok(self) -> bool:
        obs = self.observation
        return "has been edited" in obs or "File created successfully" in obs
