"""Interface for turning a raw agent action/tool-call string into `Edit` objects.

Concrete extractors live in the adapter packages (e.g.
``swe_agent_adapter/str_replace_editor_extractor.py``). The base package is
deliberately unaware of any specific CLI dialect.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from patch_minimizer.core.edit import Edit


class ActionEditExtractor(ABC):
    """Converts the text of a single agent action into zero or more ``Edit``s."""

    @abstractmethod
    def extract_edits(self, action_text: str) -> list[Edit]:
        """Parse ``action_text`` and return the edits it encodes.

        Return ``[]`` if the action performs no edits (e.g. a shell command
        or a ``run_kernel`` invocation). Raising is reserved for malformed
        input that should abort the whole trajectory.
        """
