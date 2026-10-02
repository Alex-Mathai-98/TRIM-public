"""SWE-bench variant of the classic SWE-agent parser.

Overrides ``step_requests_feedback`` to detect SWE-bench test commands
(pytest, python scripts, python -c, python -m module) as revision
boundaries instead of ``run_kernel``.
"""
from __future__ import annotations

import re

from patch_minimizer.agent_adapters.swe_agent_adapter.parsers import (
    ClassicSWEAgentTrajectoryParser,
)

# AIDEV-NOTE: Each regex allows an optional ``cd <dir> &&`` prefix and
# ``&&``-chained compound commands. See z-swebench-feedback-coverage.py
# for the dataset analysis that derived these patterns.

_PYTEST_RE = re.compile(
    r"(?:^|&&\s*)(?:cd\s+\S+\s*&&\s*)?(?:python\d*\s+(?:-\S+\s+)*-m\s+)?pytest\b",
    re.MULTILINE,
)
_PYTHON_SCRIPT_RE = re.compile(
    r"(?:^|&&\s*)(?:cd\s+\S+\s*&&\s*)?python\d*\s+(?!-c\b)\S+\.py\b",
    re.MULTILINE,
)
_PYTHON_C_RE = re.compile(
    r"(?:^|&&\s*)(?:cd\s+\S+\s*&&\s*)?python\d*\s+-c\b",
    re.MULTILINE,
)
_PYTHON_M_RE = re.compile(
    r"(?:^|&&\s*)(?:cd\s+\S+\s*&&\s*)?python\d*\s+(?:-\S+\s+)*-m\s+(?!pytest\b)\S+",
    re.MULTILINE,
)
def _is_test_invocation(action: str) -> bool:
    if not action:
        return False
    return bool(
        _PYTEST_RE.search(action)
        or _PYTHON_SCRIPT_RE.search(action)
        or _PYTHON_C_RE.search(action)
        or _PYTHON_M_RE.search(action)
    )


# AIDEV-NOTE: Agents sometimes try pytest before installing it; the failed
# invocation should not count as a feedback boundary or feedback command.
def _is_failed_pytest(step: dict) -> bool:
    obs = str(step.get("observation", ""))
    return "No module named pytest" in obs


class SWEBenchSWEAgentTrajectoryParser(ClassicSWEAgentTrajectoryParser):
    """SWE-bench variant: test commands as revision boundaries instead of run_kernel."""

    def __init__(self):
        from patch_minimizer.agent_adapters.agent_adapter_base import utils as _u
        _u.REPO_TREE_PREFIX = "/testbed/"

    def step_requests_feedback(self, step: dict) -> bool:
        action = str(step.get("action") or "")
        if not _is_test_invocation(action):
            return False
        if _PYTEST_RE.search(action) and _is_failed_pytest(step):
            return False
        return True
