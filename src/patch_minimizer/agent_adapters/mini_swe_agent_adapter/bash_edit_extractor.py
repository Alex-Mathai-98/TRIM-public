"""Bash command → ``Edit`` extractor for mini-swe-agent trajectories.

mini-swe-agent uses raw bash commands (``sed -i``, ``cat <<EOF > file``,
inline Python scripts, ``patch``, etc.) rather than the structured
``str_replace_editor`` CLI. This module provides ``BashEditExtractor``
which parses common editing patterns from bash code blocks embedded in
assistant messages.

AIDEV-NOTE: bashlex (pip install bashlex) is used for robust command
tokenization when available; shlex is the stdlib fallback. sed expression
parsing is handled by dedicated regex-based parsers because neither
library understands sed's internal grammar.
"""
from __future__ import annotations

import logging
import re
from typing import ClassVar

from patch_minimizer.core.edit import Edit
from patch_minimizer.agent_adapters.agent_adapter_base import (
    ActionEditExtractor,
)

from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.python_file_write_detection import (
    detect_python_file_edit as _detect_python_file_edit,
)
from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.patch_command_detection import (
    is_patch_command as _is_patch_command,
)

from patch_minimizer.agent_adapters.mini_swe_agent_adapter.statements import (
    CatHeredocStatement,
    CpStatement,
    MiniSweEditStatement,
    PatchStatement,
    RMStatement,
    SedStatement,
    StrReplaceEditorStatement,
    TeeHeredocStatement,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Markdown bash-block extraction
# ---------------------------------------------------------------------------
_BASH_BLOCK_RE = re.compile(r"```(?:bash|sh)\s*\n(.*?)\n```", re.DOTALL)


def extract_bash_blocks(text: str) -> list[str]:
    """Return the contents of every fenced ``bash`` / ``sh`` code block."""
    return _BASH_BLOCK_RE.findall(text)


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------


class BashEditExtractor(ActionEditExtractor):
    """Extracts ``Edit`` objects from bash commands in mini-swe-agent messages.

    Handles:
    - ``cat <<EOF > file`` heredoc overwrites
    - ``tee file <<EOF`` heredocs
    - ``sed -i`` commands (substitution, append, insert, change, delete)
    - ``str_replace_editor`` CLI commands

    Detects but warns on:
    - Inline Python file-writing scripts
    - ``patch``/``git apply`` commands

    All parsing is delegated to shared modules in ``edit_parsing_helpers/``.
    """

    # AIDEV-NOTE: ALL-match (not first-match): every statement matching a block contributes
    # edits, appended in this order — cat heredoc > tee heredoc (file creation first, so later
    # modifications can target created files) > sed > str_replace_editor > rm > cp > patch.
    # ``patch`` looks up the heredoc body memoized by ``MiniSweMessagesTrajectoryParser``.
    EDIT_STATEMENTS: ClassVar[tuple[type[MiniSweEditStatement], ...]] = (
        CatHeredocStatement,
        TeeHeredocStatement,
        SedStatement,
        StrReplaceEditorStatement,
        RMStatement,
        CpStatement,
        PatchStatement,
    )

    def __init__(self, explanation_prefix: str = "") -> None:
        self.explanation_prefix = explanation_prefix
        self._str_replace_extractor = None
        self.patch_file_bodies: dict[str, str] = {}
        self.patch_user_response: str = ""

    @property
    def str_replace_extractor(self):
        """Lazy-import to avoid circular dependency."""
        if self._str_replace_extractor is None:
            from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.str_replace_editor_extractor import (
                StrReplaceEditorExtractor,
            )
            self._str_replace_extractor = StrReplaceEditorExtractor(
                explanation_prefix=self.explanation_prefix,
            )
        return self._str_replace_extractor

    def extract_edits(self, action_text: str, **kwargs) -> list[Edit]:
        """Parse ``action_text`` (one assistant message) for editing commands.

        Ordering assumptions:
        1. Bash blocks are processed in document order (top to bottom).
        2. Within each block, edits are grouped by type, not by position
           (see ``_extract_from_block`` AIDEV-NOTE for details).
        3. ``str_replace_editor`` commands outside bash blocks are appended
           last, after all block-level edits.

        AIDEV-NOTE: Do NOT early-return when no bash blocks are found —
        assistant messages can contain ``str_replace_editor`` invocations
        directly outside any fenced block, and those must still be captured.
        """
        blocks = extract_bash_blocks(action_text)
        all_edits: list[Edit] = []
        for block in blocks:
            all_edits.extend(self._extract_from_block(block))

        # Check for str_replace_editor outside bash blocks (or when there
        # are no bash blocks at all — e.g. some str_replace_editor-only steps)
        text_outside_blocks = _BASH_BLOCK_RE.sub("", action_text)
        outside = StrReplaceEditorStatement.from_action(
            text_outside_blocks, self._statement_context()
        )
        if outside is not None:
            all_edits.extend(outside.to_edits(self.explanation_prefix))

        return all_edits

    def extract_edits_from_step(self, step: dict) -> list[Edit]:
        """Extract edits using the full step dict for context.

        Sets ``patch_file_bodies`` and ``patch_user_response`` from the
        step, calls ``extract_edits``, and cleans up afterwards.
        """
        self.patch_file_bodies = step.get("patch_file_bodies") or {}
        self.patch_user_response = step.get("user_response", "")
        try:
            return self.extract_edits(step.get("assistant_content", ""))
        finally:
            self.patch_file_bodies = {}
            self.patch_user_response = ""

    def _statement_context(self) -> dict:
        """Step-shaped context for statements: patch memo + shell output of this step."""
        return {
            "patch_file_bodies": self.patch_file_bodies,
            "user_response": self.patch_user_response,
        }

    def _extract_from_block(self, block: str) -> list[Edit]:
        """Try each parser against one bash block.

        AIDEV-NOTE: known limitation, chosen not to fix (2026-09-30) — ordering is
        lost: edits are grouped by command type, not by their position in the block
        (priority = ``EDIT_STATEMENTS`` order). If a block mixes types in a different
        order, the actual execution order is not preserved. This has always been the
        behaviour (pre-dates the statement refactor) and the parsing goldens depend on
        it. Fixing requires each statement to report character-level match positions,
        then sorting across types. Single bash blocks rarely mix command types.
        """
        edits: list[Edit] = []
        ctx = self._statement_context()
        for cls in self.EDIT_STATEMENTS:
            stmt = cls.from_action(block, ctx)
            if stmt is not None:
                edits.extend(stmt.to_edits(self.explanation_prefix))

        # Warn on patterns we detect but cannot extract
        if not edits:
            if _detect_python_file_edit(block):
                logger.warning(
                    "Detected Python file-writing script but cannot extract "
                    "Edit: %s",
                    block[:200],
                )
            if _is_patch_command(block):
                logger.warning(
                    "Detected patch/git-apply command but cannot extract "
                    "Edit: %s",
                    block[:200],
                )

        return edits
