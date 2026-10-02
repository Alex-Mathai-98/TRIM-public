"""Parser registry + registry-driven dispatch.

``ParserRegistry`` is a tiny ordered list of ``TrajectoryParser`` classes;
``parse_trajectory_payload`` asks it for the first parser whose
``can_parse`` accepts a top-level trajectory dict and dispatches to it.

Adapters register themselves at import time (side effect of
``from ... import parsers``), so this module has zero hardcoded knowledge
of any specific format.
"""
from __future__ import annotations

import logging
from typing import Iterator, List

from patch_minimizer.agent_adapters.agent_adapter_base.parsers.trajectory_parser import (
    TrajectoryParser,
)
from patch_minimizer.agent_adapters.agent_adapter_base.parsers.trajectory_types import (
    CandidateTrajectory,
)

logger = logging.getLogger(__name__)


class UnknownTrajectoryFormat(ValueError):
    """Raised when no registered parser accepts a trajectory payload."""


class ParserRegistry:
    """Ordered collection of ``TrajectoryParser`` classes.

    First registered = first asked. Adapters that want priority register
    early; the default order is import order.
    """

    def __init__(self) -> None:
        self._parsers: list[type[TrajectoryParser]] = []

    def register(
        self, parser_cls: type[TrajectoryParser]
    ) -> type[TrajectoryParser]:
        """Register ``parser_cls``. Usable as a class decorator."""
        if parser_cls not in self._parsers:
            self._parsers.append(parser_cls)
        return parser_cls

    def match(self, data: dict) -> type[TrajectoryParser] | None:
        """Return the first registered parser whose ``can_parse`` accepts ``data``."""
        for parser_cls in self._parsers:
            if parser_cls.can_parse(data):
                return parser_cls
        return None

    def clear(self) -> None:
        """Drop every registered parser. Intended for tests."""
        self._parsers.clear()

    def __iter__(self) -> Iterator[type[TrajectoryParser]]:
        return iter(self._parsers)

    def __len__(self) -> int:
        return len(self._parsers)


parser_registry = ParserRegistry()


def parse_trajectory_payload(
    data: dict, *, source_label: str
) -> List[CandidateTrajectory]:
    """Dispatch ``data`` to the first registered parser that accepts it.

    Args:
        data: Top-level trajectory dict already loaded from JSON.
        source_label: Human-readable id for logs (path, URI, run id).

    Returns:
        List of ``CandidateTrajectory``. Empty if the parser found no
        successful candidates.

    Raises:
        UnknownTrajectoryFormat: no registered parser accepted ``data``.
    """
    parser_cls = parser_registry.match(data)
    if parser_cls is None:
        raise UnknownTrajectoryFormat(
            f"{source_label}: no registered TrajectoryParser accepted "
            f"top-level keys {list(data.keys())[:12]}"
        )
    logger.debug(
        "%s: dispatching to %s", source_label, parser_cls.__name__
    )
    return parser_cls().parse(data, source_label=source_label)
