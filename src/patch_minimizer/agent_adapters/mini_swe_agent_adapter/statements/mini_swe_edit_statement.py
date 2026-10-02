"""mini-swe success rule shared by every mini-swe edit statement."""
from __future__ import annotations

from typing import TYPE_CHECKING

from patch_minimizer.agent_adapters.agent_adapter_base.statements import Statement

if TYPE_CHECKING:
    from patch_minimizer.agent_adapters.agent_adapter_base import TrajectoryParser


def step_succeeded(step: dict) -> bool:
    """Step-level success for mini-swe edits.

    AIDEV-NOTE: mini-swe wraps shell output in ``<returncode>N</returncode>``. Non-zero rc
    means the bash command failed and its edits must not be captured (ghost edits). There
    is no per-step ``state.diff``, so rc is the sole structural signal; per-edit leniency
    (a sed regex that matches nothing) is handled via ``best_effort=True`` in the helpers.
    ``str_replace_editor`` success text also counts, and ``patch`` is partial-applying —
    rc=1 with ``patching file`` in the output means some hunks applied; the patch
    extractor drops the rejected ones, so the step counts as successful.
    """
    if step.get("returncode") == 0:
        return True
    resp = step.get("user_response", "")
    if "has been edited" in resp or "File created successfully" in resp:
        return True
    return "patching file" in resp


class MiniSweEditStatement(Statement):
    """An editing command inside one mini-swe bash block.

    ``action`` is the bash block; ``step`` is the mini-swe step dict (``user_response``,
    ``returncode``, ``patch_file_bodies`` …) or, from ``BashEditExtractor.extract_edits``,
    a minimal context dict with just the patch memo + response.
    """

    def is_successful(self, parser: TrajectoryParser) -> bool:
        # AIDEV-NOTE: statements built inside BashEditExtractor carry a 2-key context dict
        # (patch memo + response), not the real step; it has no ``returncode``, so
        # ``step_succeeded`` would wrongly return False. Real steps always have the key.
        assert "returncode" in self.step, (
            f"{type(self).__name__}.is_successful needs the real mini-swe step, got a "
            "context dict (keys: "
            f"{sorted(self.step)}). Check success on the step in the parser instead."
        )
        return step_succeeded(self.step)
