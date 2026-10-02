"""Statement abstraction shared by all agent adapters.

See ``AGENTS.md`` in this directory. Per-adapter concrete statements live in
``<adapter>/statements/``.
"""
from patch_minimizer.agent_adapters.agent_adapter_base.statements.cp_statement import (
    CpStatementBase,
)
from patch_minimizer.agent_adapters.agent_adapter_base.statements.git_reset_file_statement import (
    GitResetFileStatementBase,
    drop_edits_on_files,
)
from patch_minimizer.agent_adapters.agent_adapter_base.statements.rm_statement import (
    RMStatementBase,
)
from patch_minimizer.agent_adapters.agent_adapter_base.statements.sed_statement import (
    SedStatementBase,
)
from patch_minimizer.agent_adapters.agent_adapter_base.statements.statement import (
    RevertStatement,
    Statement,
)
from patch_minimizer.agent_adapters.agent_adapter_base.statements.str_replace_editor_statement import (
    StrReplaceEditorStatementBase,
)
from patch_minimizer.agent_adapters.agent_adapter_base.statements.undo_edit_statement import (
    UndoEditStatementBase,
)

__all__ = [
    "CpStatementBase",
    "GitResetFileStatementBase",
    "RMStatementBase",
    "RevertStatement",
    "SedStatementBase",
    "Statement",
    "StrReplaceEditorStatementBase",
    "UndoEditStatementBase",
    "drop_edits_on_files",
]
