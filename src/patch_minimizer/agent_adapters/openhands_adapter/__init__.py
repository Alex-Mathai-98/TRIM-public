"""OpenHands trajectory adapter.

Importing this package self-registers ``KarenaOpenHandsTrajectoryParser``
with ``agent_adapter_base.parser_registry``. Use
``agent_adapter_base.loader.load_trajectory_file`` for the file-based
entry point.
"""
from patch_minimizer.agent_adapters.openhands_adapter import (
    parsers,  # noqa: F401  — side-effect: register KarenaOpenHandsTrajectoryParser
)
from patch_minimizer.agent_adapters.openhands_adapter.file_editor_action_extractor import (
    FileEditorActionExtractor,
)
from patch_minimizer.agent_adapters.openhands_adapter.parsers import (
    KarenaOpenHandsTrajectoryParser,
)

__all__ = [
    "FileEditorActionExtractor",
    "KarenaOpenHandsTrajectoryParser",
]
