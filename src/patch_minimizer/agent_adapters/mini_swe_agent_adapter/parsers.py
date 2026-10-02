"""mini-swe-agent ``messages[]`` trajectory parser. Self-registers on import.

mini-swe-agent uses raw bash commands (``sed -i``, ``cat <<EOF > file``,
``rm``, ``cp``, ``str_replace_editor``, etc.) rather than structured tool
calls. This parser converts alternating assistant/user message pairs into
"steps" and delegates to the base ``parse_trajectory`` template method for
revision splitting at ``run_kernel`` boundaries — matching the pattern used
by ``ClassicSWEAgentTrajectoryParser``.
"""
from __future__ import annotations

import logging
import re
from typing import ClassVar, List

from patch_minimizer.core.edit import Edit, EditType
from patch_minimizer.agent_adapters.agent_adapter_base import (
    CandidateTrajectory,
    TrajectoryParser,
    parser_registry,
)
from patch_minimizer.agent_adapters.agent_adapter_base.statements import RevertStatement
from patch_minimizer.agent_adapters.agent_adapter_base.utils import (
    is_linux_tree_path,
    normalize_linux_repo_path,
)
from patch_minimizer.agent_adapters.mini_swe_agent_adapter.bash_edit_extractor import (
    BashEditExtractor,
    extract_bash_blocks,
)
from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.cat_heredoc_parsing import (
    iter_cat_heredocs,
)
from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.tee_heredoc_parsing import (
    _TEE_HEREDOC_RE,
)
from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.patch_file_extractor import (
    iter_heredoc_patch_targets,
)

from patch_minimizer.agent_adapters.mini_swe_agent_adapter.statements import (
    GitResetAllStatement,
    GitResetFileStatement,
    StrReplaceEditorStatement,
)
from patch_minimizer.agent_adapters.mini_swe_agent_adapter.statements.git_reset_file_statement import (
    reset_file_succeeded,
)

logger = logging.getLogger(__name__)

# AIDEV-NOTE: Matches mini-swe format discriminator field.
_MINI_SWE_FORMAT_RE = re.compile(r"mini.?swe", re.IGNORECASE)

_RUN_KERNEL_RE = re.compile(r"\brun_kernel\b", re.IGNORECASE)


def is_run_kernel_block(bash_block: str) -> bool:
    """Return ``True`` if the block invokes ``run_kernel``."""
    lower = bash_block.lower()
    return "/kbdr/run_kernel" in lower or bool(_RUN_KERNEL_RE.search(bash_block))


def is_completion_block(bash_block: str) -> bool:
    """Return ``True`` if the block signals task completion."""
    return "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in bash_block

# XML returncode tag used in user response messages
_RETURNCODE_RE = re.compile(r"<returncode>\s*(\d+)\s*</returncode>")


# AIDEV-NOTE: Per-file reset detection (git checkout/restore <file>) lives in the
# shared module edit_parsing_helpers/git_reset_file_detection.py, reached via
# statements.GitResetFileStatement (see perform_undo_step).




def _collect_heredoc_targets(bash_blocks: list[str]) -> list[tuple[str, str]]:
    """Return ``(raw_path, body)`` for every ``cat`` / ``tee`` heredoc across
    ``bash_blocks``. Paths are returned unnormalized so
    ``iter_heredoc_patch_targets`` can keep ``/tmp/`` keys intact for
    later lookup by ``patch``/``git apply`` commands.
    """
    out: list[tuple[str, str]] = []
    for block in bash_blocks:
        # AIDEV-NOTE: iter_cat_heredocs covers both ``cat << EOF > file`` and
        # ``cat > file << EOF`` orderings; using the bare _HEREDOC_RE here
        # missed the redirect-first form that ``cat > /tmp/patch.diff <<EOF``
        # uses, leaving patch_cmd_missed cases stranded.
        for filepath, body in iter_cat_heredocs(block):
            out.append((filepath, body))
        for m in _TEE_HEREDOC_RE.finditer(block):
            out.append((m.group("filepath"), m.group("body")))
    return out


def _messages_to_steps(messages: list[dict]) -> list[dict]:
    """Convert raw ``messages[]`` to step dicts for ``parse_trajectory``.

    Each step is one assistant turn paired with the following user
    response (if any). The step dict has:
    - ``assistant_content``: the assistant message text
    - ``user_response``: the user (tool output) message text
    - ``msg_index``: index of the assistant message in the original list
    - ``bash_blocks``: pre-extracted bash code blocks from assistant text
    - ``returncode``: parsed return code from user response, or ``None``
    - ``patch_file_bodies``: cumulative ``{raw_path: body}`` memo for
      heredocs targeting ``/tmp/*.patch`` / ``*.diff`` so a later
      ``patch -p1 < /tmp/fix.patch`` step can retrieve the diff and
      extract real edits (see ``patch_file_extractor``).
    """
    steps: list[dict] = []
    # AIDEV-NOTE: single shared memo that grows monotonically across steps.
    # Every step snapshots the memo *as of that step* — this keeps the
    # parser correct even if a ``patch`` command references a heredoc
    # written in the same message (single-block ``cat <<EOF > /tmp/p;
    # patch -p1 < /tmp/p``).
    cumulative_patch_files: dict[str, str] = {}
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "assistant":
            assistant_content = str(msg.get("content") or "")
            user_response = ""
            if (
                i + 1 < len(messages)
                and messages[i + 1].get("role") == "user"
            ):
                user_response = str(messages[i + 1].get("content") or "")
                i += 2
            else:
                i += 1

            bash_blocks = extract_bash_blocks(assistant_content)
            returncode = None
            rc_match = _RETURNCODE_RE.search(user_response)
            if rc_match:
                returncode = int(rc_match.group(1))

            heredoc_targets = _collect_heredoc_targets(bash_blocks)
            new_patch_bodies = iter_heredoc_patch_targets(heredoc_targets)
            if new_patch_bodies:
                cumulative_patch_files.update(new_patch_bodies)

            steps.append(
                {
                    "assistant_content": assistant_content,
                    "user_response": user_response,
                    "msg_index": len(steps),
                    "bash_blocks": bash_blocks,
                    "returncode": returncode,
                    "patch_file_bodies": dict(cumulative_patch_files),
                }
            )
        else:
            i += 1
    return steps


class MiniSweMessagesTrajectoryParser(TrajectoryParser):
    """Parses mini-swe-agent ``messages[]`` trajectories with bash command extraction.

    Converts messages into steps, splits revisions at ``run_kernel``
    boundaries, and extracts edits from ``sed -i``, ``cat <<EOF``,
    ``str_replace_editor``, etc. via ``BashEditExtractor``.

    AIDEV-NOTE: Discriminates via ``trajectory_format`` containing
    ``"mini-swe"`` (preferred) or presence of ``messages`` key without
    ``trajectory`` or ``events`` (fallback).
    """

    def __init__(self):
        from patch_minimizer.agent_adapters.agent_adapter_base import utils as _u
        _u.REPO_TREE_PREFIX = "/linux/"

    @classmethod
    def payload_keys(cls) -> tuple[str, ...]:
        return ("messages",)

    @classmethod
    def can_parse(cls, data: dict) -> bool:
        """Accept when ``trajectory_format`` matches mini-swe, or when
        ``messages`` is present without competing keys."""
        fmt = data.get("trajectory_format", "")
        if isinstance(fmt, str) and _MINI_SWE_FORMAT_RE.search(fmt):
            return True
        if not data.get("messages"):
            return False
        # Reject if this looks like classic SWE or OpenHands
        if data.get("trajectory") or data.get("events"):
            return False
        return True

    def step_requests_feedback(self, step: dict) -> bool:
        """Detect ``run_kernel`` invocation in any bash block of this step."""
        for block in step.get("bash_blocks", []):
            if is_run_kernel_block(block):
                return True
        return False

    def is_successful_edit_step(self, step: dict) -> bool:
        """Return ``True`` if the command executed successfully.

        AIDEV-NOTE: mini-swe wraps shell output in ``<returncode>N</returncode>``.
        Non-zero rc means the bash command failed (bad regex, missing file,
        etc.) and its edits must not be captured — otherwise we pollute the
        edit list with ghost edits. This mirrors SWE-agent's two-gate design:
        observation-text gate + state.diff gate. Mini-swe has no per-step
        ``state.diff``, so ``returncode`` is the sole structural success
        signal at the *step* level. Per-edit leniency (sed's regex may not
        match a line) is handled at the *edit* level via ``best_effort=True``
        inside ``sed_parsing.py`` — we do NOT relax it here.

        ``str_replace_editor`` additionally recognises the textual success
        markers produced by its CLI.

        AIDEV-NOTE: ``patch`` is partial-applying — rc=1 means at least one
        hunk failed but earlier hunks may have applied. If the user_response
        contains ``patching file``, the patch tool ran and reported per-hunk
        outcomes; ``extract_edits_from_patch_cmd`` parses those and drops
        rejected hunks. Treat such steps as successful at the step level so
        the surviving hunks reach the candidate edit list.
        """
        rc = step.get("returncode")
        if rc == 0:
            return True
        resp = step.get("user_response", "")
        if "has been edited" in resp or "File created successfully" in resp:
            return True
        if "patching file" in resp:
            return True
        return False

    # AIDEV-NOTE: ALL-match over every bash block (a turn can revert several files in
    # different blocks). Each ``apply`` returns False so same-turn edits still get parsed.
    UNDO_STATEMENTS: ClassVar[tuple[type[RevertStatement], ...]] = (GitResetFileStatement,)

    def is_reset_all_step(self, step: dict) -> bool:
        """Detect ``git reset --hard`` (no file args) that wipes all edits.

        See ``GitResetAllStatement`` — success is ``"HEAD is now at"`` in the response,
        not rc (compound commands can fail later even though the reset succeeded).
        """
        for block in step.get("bash_blocks") or []:
            stmt = GitResetAllStatement.from_action(block, step)
            if stmt is not None:
                return stmt.is_successful(self)
        return False

    def is_edit_step(self, step: dict) -> bool:
        """Return ``True`` if any bash block contains an editing command."""
        for block in step.get("bash_blocks", []):
            if any(cls.is_edit(block, step) for cls in BashEditExtractor.EDIT_STATEMENTS):
                return True
        # Also check for str_replace_editor outside bash blocks
        content = step.get("assistant_content", "")
        if StrReplaceEditorStatement.is_edit(content, step):
            return True
        return False

    def is_successful_undo_step(self, step: dict) -> bool:
        """Return ``True`` if the git checkout/restore actually reverted files.

        AIDEV-NOTE: check positive signal ("Updated N path") before rc,
        because compound commands (``git checkout f && cp ...``) can have
        rc!=0 from a later command even though the checkout succeeded.
        """
        return reset_file_succeeded(step)

    def perform_undo_step(
        self,
        step: dict,
        bucket: list[Edit],
        revisions: list[Revision],
        *,
        source_label: str,
        step_idx: int,
    ) -> bool:
        """Drop prior edits on every file reverted by ``git checkout`` /
        ``git restore`` / ``git reset --hard <files>`` in this step.

        AIDEV-NOTE: Two intentional departures from the SWE-agent
        ``str_replace_editor undo_edit`` semantics this hook was
        originally designed for. The base contract (single-file via
        ``is_undo_step → str | None``, last-edit removal in
        ``perform_undo_step``) does not fit ``git checkout``:

        1. **Multi-file dispatch.** ``git checkout a.c b.c c.c`` reverts
           several files in a single step, while the base hook returns
           one filename. We bypass ``is_undo_step`` and re-extract the
           full list ourselves (``GitResetFileStatement.targets``, per block).
        2. **Full revert vs. last-edit pop.** SWE's override removes
           only the most recent edit on the file (one ``undo_edit`` =
           one operation reversed). ``git checkout <file>`` reverts the
           working-tree copy back to HEAD, so **every** prior edit on
           that file in the same revision must go — we filter, not pop.

        We also return ``False`` so the trajectory walk falls through to
        ``parse_edit_step`` instead of ``continue``-ing. A single mini-swe
        assistant turn can contain a reset for one file and a fresh edit
        for another (or for the same file) in different bash blocks; if
        we returned ``True`` here, those concurrent edits would be lost.
        ``parse_edit_step`` runs against an already-stripped bucket, so
        a re-edit of a reset file lands cleanly with no ghost prior.
        """
        for block in step.get("bash_blocks") or []:
            for cls in self.UNDO_STATEMENTS:
                stmt = cls.from_action(block, step)
                if stmt is not None:
                    stmt.apply(
                        bucket, revisions, parser=self,
                        source_label=source_label, step_idx=step_idx,
                    )
        return False

    def parse_edit_step(
        self, step: dict, idx: int, extractor: BashEditExtractor
    ) -> List[Edit]:
        """Extract edits from this step if it's a successful edit."""
        if not self.is_edit_step(step):
            return []
        if not self.is_successful_edit_step(step):
            return []
        edits = extractor.extract_edits_from_step(step)

        # AIDEV-NOTE: stash external-file edits; remap to linux path on cp-back
        linux_edits = []
        for e in edits:
            if not is_linux_tree_path(e.filename) and e.filename.startswith("/"):
                self._external_file_edits.setdefault(e.filename, []).append(e)
            elif e.edit_type == EditType.COPY_FILE and not is_linux_tree_path(e.before) and e.before.startswith("/"):
                remapped = self._external_file_edits.pop(e.before, [])
                for r in remapped:
                    r.filename = e.filename
                linux_edits.extend(remapped)
            else:
                linux_edits.append(e)

        for e in linux_edits:
            e.step_index = idx
        return linux_edits

    def parse(
        self, data: dict, *, source_label: str
    ) -> list[CandidateTrajectory]:
        messages = data.get("messages") or []
        steps = _messages_to_steps(messages)

        if not steps:
            logger.info("Mini-swe messages: no assistant turns in %s", source_label)
            return []

        self._external_file_edits: dict[str, list[Edit]] = {}
        extractor = BashEditExtractor(explanation_prefix="mini_swe")
        candidates = self.parse_trajectory(
            steps, extractor, source_label=source_label
        )
        return candidates


parser_registry.register(MiniSweMessagesTrajectoryParser)
