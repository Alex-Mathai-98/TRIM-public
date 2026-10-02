"""Parse ``rm`` shell commands into ``WHOLE_FILE_DELETE`` edits."""
from __future__ import annotations

from patch_minimizer.core.edit import Edit, EditType
from patch_minimizer.agent_adapters.agent_adapter_base.utils import (
    normalize_linux_repo_path,
)

# AIDEV-NOTE: bashlex splits compound commands (``rm file && git diff``)
# into individual command nodes so we only parse the ``rm`` portion.
try:
    import bashlex
    _HAS_BASHLEX = True
except ImportError:
    _HAS_BASHLEX = False


def _find_rm_commands(bash_block: str) -> list[str]:
    if _HAS_BASHLEX and "<<" not in bash_block:
        try:
            parts = bashlex.parse(bash_block)
        except Exception:
            return [bash_block]
        cmds: list[str] = []

        def _walk(node):
            if node.kind == "command":
                words = [p.word for p in node.parts if p.kind == "word"]
                if words and words[0] == "rm":
                    cmds.append(bash_block[node.pos[0]:node.pos[1]])
            for attr in ("parts", "list"):
                for child in getattr(node, attr, []) or []:
                    _walk(child)

        for p in parts:
            _walk(p)
        return cmds
    return [bash_block]


def is_rm_command(text: str) -> bool:
    """Return ``True`` if ``text`` is an ``rm`` command."""
    return text.strip().startswith("rm ")


def extract_rm_targets(action: str) -> list[str]:
    """Return the normalized repo paths deleted by every ``rm`` in ``action``, in order."""
    targets: list[str] = []
    for cmd in _find_rm_commands(action):
        tokens = cmd.strip().split()
        for token in tokens[1:]:
            if token.startswith("-"):
                continue
            path = normalize_linux_repo_path(token)
            # AIDEV-NOTE: ``/tmp/...`` normalizes to ``""`` — not a kernel-tree path.
            if not path:
                continue
            targets.append(path)
    return targets


def rm_edits_for_targets(targets: list[str], explanation_prefix: str) -> list[Edit]:
    """Build one ``WHOLE_FILE_DELETE`` edit per target (numbered ``rm #1``, ``rm #2`` …)."""
    return [
        Edit(
            filename=path,
            before="",
            after="",
            edit_type=EditType.WHOLE_FILE_DELETE,
            explanation=f"{explanation_prefix} rm #{i}",
        )
        for i, path in enumerate(targets, 1)
    ]


def parse_rm_command(action: str, explanation_prefix: str) -> list[Edit]:
    """Parse ``rm /linux/file1 /linux/file2`` into WHOLE_FILE_DELETE edits."""
    return rm_edits_for_targets(extract_rm_targets(action), explanation_prefix)
