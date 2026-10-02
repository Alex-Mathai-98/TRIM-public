"""mini-swe-agent trajectory adapter.

Importing this package self-registers ``MiniSweMessagesTrajectoryParser``
with ``agent_adapter_base.parser_registry``. Use
``agent_adapter_base.utils.load_trajectory_file`` for the file-based
entry point.
"""
from patch_minimizer.agent_adapters.mini_swe_agent_adapter import (
    parsers,  # noqa: F401  — side-effect: register MiniSweMessagesTrajectoryParser
)
from patch_minimizer.agent_adapters.mini_swe_agent_adapter.bash_edit_extractor import (
    BashEditExtractor,
)
from patch_minimizer.agent_adapters.mini_swe_agent_adapter.parsers import (
    MiniSweMessagesTrajectoryParser,
)

__all__ = [
    "BashEditExtractor",
    "MiniSweMessagesTrajectoryParser",
]
