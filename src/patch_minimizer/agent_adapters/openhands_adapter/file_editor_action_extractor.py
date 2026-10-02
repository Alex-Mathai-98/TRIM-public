"""OpenHands ``FileEditorAction`` → ``Edit`` extractor.

OpenHands uses a structured ``FileEditorAction`` dict with
``command='str_replace'`` plus a matching ``FileEditorObservation`` whose
text must contain ``"has been edited"`` for the edit to count as
successful. That's unrelated to the shell ``str_replace_editor`` CLI used
by classic SWE-Agent and mini-swe, so OpenHands gets its own extractor.

The OpenHands kernel-feedback boundary detector lives on
``KarenaOpenHandsTrajectoryParser.step_requests_feedback`` in ``parsers.py``.
"""
from __future__ import annotations

from patch_minimizer.core.edit import Edit, EditType, MatchMode
from patch_minimizer.agent_adapters.agent_adapter_base import (
    ActionEditExtractor,
    normalize_linux_repo_path,
)


class FileEditorActionExtractor(ActionEditExtractor):
    """Extracts one ``Edit`` from a verified successful OpenHands ``FileEditorAction``.

    Stateless: the parser's ``is_edit_step`` + ``is_successful_edit_step``
    hooks do the structural + success filtering (the latter reads the
    observation map held on the parser), so this class only needs the
    action dict and a step index.
    """

    def extract_from_event(
        self, ev: dict, step_idx: int
    ) -> list[Edit]:
        args = ev.get("args") or {}
        cmd = args.get("command")

        if cmd == "str_replace":
            old_s = args.get("old_str")
            new_s = args.get("new_str")
            if old_s is None:
                return []
            if new_s is None:
                new_s = ""
            return [Edit(
                filename=normalize_linux_repo_path(str(args.get("path") or "")),
                before=str(old_s),
                after=str(new_s),
                edit_type=EditType.REPLACE,
                match_mode=MatchMode.SEMI_FUZZY,
                explanation=f"openhands edit step {step_idx}",
                step_index=step_idx,
            )]

        if cmd == "insert":
            new_s = args.get("new_str")
            insert_line = args.get("insert_line")
            if new_s is None or insert_line is None:
                return []
            # AIDEV-NOTE: insert_line+1 aligns "insert AFTER line N" with EditApplier's 1-based indexing
            return [Edit(
                filename=normalize_linux_repo_path(str(args.get("path") or "")),
                before="",
                after=str(new_s),
                starting_line=int(insert_line) + 1,
                edit_type=EditType.INSERT_AFTER_LINE,
                explanation=f"openhands insert step {step_idx}",
                step_index=step_idx,
            )]

        if cmd == "create":
            file_text = args.get("file_text")
            if file_text is None:
                return []
            return [Edit(
                filename=normalize_linux_repo_path(str(args.get("path") or "")),
                before="",
                after=str(file_text),
                edit_type=EditType.WHOLE_FILE_ACTIONS,
                explanation=f"openhands create step {step_idx}",
                step_index=step_idx,
            )]

        return []

    def extract_observation_from_file_editor(
        self, ev: dict
    ) -> tuple[str, str, str | None] | None:
        """Return ``(cause_id, observation_text, diff_or_none)`` if ``ev``
        is an edit observation linked to an action via ``cause``, else ``None``.
        """
        if ev.get("observation") != "edit":
            return None
        cause = ev.get("cause")
        if cause is None:
            return None
        content = str(ev.get("content") or "")
        diff = (ev.get("extras") or {}).get("diff") or None
        return str(cause), content, diff

    def extract_edits(self, ev: dict, step_idx: int = 0) -> list[Edit]:
        """Public entry point: delegate to ``extract_from_event``."""
        return self.extract_from_event(ev, step_idx)
