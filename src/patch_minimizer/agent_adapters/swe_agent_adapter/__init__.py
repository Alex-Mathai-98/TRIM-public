"""
Classic SWE-Agent trajectory parsing for the minimization pipeline.

Importing this package self-registers ``ClassicSWEAgentTrajectoryParser``
with ``agent_adapter_base.parser_registry`` (via the ``parsers`` submodule).
Use ``agent_adapter_base.utils.load_trajectory_file`` for the file-based
entry point; callers no longer need a SWE-specific alias.
"""
from patch_minimizer.agent_adapters.swe_agent_adapter import (
    parsers,  # noqa: F401  — side-effect: register ClassicSWEAgentTrajectoryParser
)
from patch_minimizer.agent_adapters.swe_agent_adapter.parsers import (
    ClassicSWEAgentTrajectoryParser,
)
from patch_minimizer.agent_adapters.swe_agent_adapter.swebench_parsers import (
    SWEBenchSWEAgentTrajectoryParser,
)
from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.str_replace_editor_extractor import (
    StrReplaceEditorExtractor,
)

__all__ = [
    "ClassicSWEAgentTrajectoryParser",
    "SWEBenchSWEAgentTrajectoryParser",
    "StrReplaceEditorExtractor",
]
