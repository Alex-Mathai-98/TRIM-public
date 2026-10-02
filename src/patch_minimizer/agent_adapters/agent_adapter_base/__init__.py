"""Generic base for agent trajectory → patch_list adapters.

Holds types, the parser registry/ABC, and agent-agnostic helpers. No
concrete adapter lives here — each adapter package (``swe_agent_adapter``,
``openhands``, ``mini_swe_agent_adapter``) registers its parser with the
shared registry on import.
"""
from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.action_edit_extractor import (
    ActionEditExtractor,
)
from patch_minimizer.agent_adapters.agent_adapter_base.parsers import (
    ParserRegistry,
    TrajectoryParser,
    UnknownTrajectoryFormat,
    parse_trajectory_payload,
    parser_registry,
)
from patch_minimizer.agent_adapters.agent_adapter_base.parsers.trajectory_types import (
    CandidateTrajectory,
    Revision,
)
from patch_minimizer.agent_adapters.agent_adapter_base.utils import (
    REPO_TREE_PREFIX,
    load_trajectory_file,
    normalize_linux_repo_path,
    parse_trajectory_with_patch_lists,
    stamp_global_edit_idx,
    to_patch_list,
)

__all__ = [
    "ActionEditExtractor",
    "CandidateTrajectory",
    "REPO_TREE_PREFIX",
    "ParserRegistry",
    "Revision",
    "TrajectoryParser",
    "UnknownTrajectoryFormat",
    "load_trajectory_file",
    "normalize_linux_repo_path",
    "parse_trajectory_payload",
    "parse_trajectory_with_patch_lists",
    "parser_registry",
    "stamp_global_edit_idx",
    "to_patch_list",
]
