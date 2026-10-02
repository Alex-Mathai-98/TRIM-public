"""``Statement`` / ``RevertStatement`` ABCs — one recognised command kind inside an agent step.

A *statement* bundles the three things a trajectory parser needs to know about one kind of
command: does this text contain it (``matches`` / ``is_edit``), did it succeed
(``is_successful``), and what does it do (``to_edits`` for edit statements, ``apply`` for
revert statements).

Shared ``*StatementBase`` classes in this package hold the adapter-agnostic parsing; each
adapter's ``statements/`` package subclasses them to add its own success rule.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from patch_minimizer.agent_adapters.agent_adapter_base.parsers.trajectory_parser import (
        TrajectoryParser,
    )
    from patch_minimizer.agent_adapters.agent_adapter_base.parsers.trajectory_types import (
        Revision,
    )
    from patch_minimizer.core.edit import Edit


class _StatementCommon(ABC):
    """Detection + construction shared by edit and revert statements.

    ``action`` is the command payload the adapter dispatches on (a shell command, one bash
    block, or an OpenHands tool-command name); ``step`` is the full trajectory step, kept
    for success checks and context lookups (observation, ``state.diff``, patch memo).
    ``step_idx`` is the step's position in the trajectory, when the caller knows it; only
    statements whose edit labels embed it (OpenHands file editor) require it.
    """

    def __init__(self, action: str, step: dict, *, step_idx: int | None = None) -> None:
        self.action = action
        self.step = step
        self.step_idx = step_idx
        self._parse()

    def _parse(self) -> None:
        """Parse ``self.action`` once at construction. Override to store parsed fields."""

    @classmethod
    @abstractmethod
    def matches(cls, action: str, step: dict) -> bool:
        """Return ``True`` if ``action`` is this kind of command (dispatch predicate)."""

    @classmethod
    def from_action(cls, action: str, step: dict, *, step_idx: int | None = None) -> Any:
        """Build and parse a statement, or return ``None`` if ``action`` doesn't match."""
        if not cls.matches(action, step):
            return None
        return cls(action, step, step_idx=step_idx)

    def is_successful(self, parser: TrajectoryParser) -> bool:
        """Return ``True`` if the command took effect. Adapter-specific; default ``True``."""
        return True


class Statement(_StatementCommon):
    """A command that produces ``Edit`` objects (``rm``, ``sed -i``, ``str_replace`` …)."""

    @classmethod
    def is_edit(cls, action: str, step: dict) -> bool:
        """Edit-step gate (``TrajectoryParser.is_edit_step``).

        AIDEV-NOTE: defaults to ``matches``; override only when an adapter's edit-step gate
        is narrower/wider than its dispatch predicate (e.g. SWE ``str_replace_editor``:
        gate = str_replace/insert/create only, dispatch = any ``str_replace_editor``).
        """
        return cls.matches(action, step)

    @abstractmethod
    def to_edits(self, explanation_prefix: str) -> list[Edit]:
        """Return the edits this command performs (``[]`` if none).

        AIDEV-NOTE: one argument everywhere. Anything else a statement needs for its labels
        (e.g. ``step_idx``) is passed when the statement is built, not here.
        """


class RevertStatement(_StatementCommon):
    """A command that undoes earlier edits (``undo_edit``, ``git checkout <f>``, stash …).

    Revert statements have no ``to_edits``. Those that remove specific edits implement
    ``apply``; whole-state ones (reset-all, stash) are detection-only and the base
    ``TrajectoryParser.parse_trajectory`` loop performs the state change.
    """

    def apply(
        self,
        bucket: list[Edit],
        revisions: list[Revision],
        *,
        parser: TrajectoryParser,
        source_label: str,
        step_idx: int,
    ) -> bool:
        """Remove the reverted edits in place; return the ``perform_undo_step`` result."""
        raise NotImplementedError(f"{type(self).__name__} is detection-only")
