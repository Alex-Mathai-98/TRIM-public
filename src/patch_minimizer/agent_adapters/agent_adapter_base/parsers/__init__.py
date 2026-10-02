"""Parser ABC + registry + registry-driven dispatch for trajectory adapters."""
from patch_minimizer.agent_adapters.agent_adapter_base.parsers.parser_registry import (
    ParserRegistry,
    UnknownTrajectoryFormat,
    parse_trajectory_payload,
    parser_registry,
)
from patch_minimizer.agent_adapters.agent_adapter_base.parsers.trajectory_parser import (
    TrajectoryParser,
)

__all__ = [
    "ParserRegistry",
    "TrajectoryParser",
    "UnknownTrajectoryFormat",
    "parse_trajectory_payload",
    "parser_registry",
]
