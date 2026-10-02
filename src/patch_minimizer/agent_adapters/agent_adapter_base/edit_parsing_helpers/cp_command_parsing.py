"""Parse ``cp`` shell commands into ``COPY_FILE`` edits."""
from __future__ import annotations

from patch_minimizer.core.edit import Edit, EditType
from patch_minimizer.agent_adapters.agent_adapter_base.utils import (
    is_linux_tree_path,
    normalize_linux_repo_path,
)

# AIDEV-NOTE: bashlex splits compound commands (``cp file dest && make``)
# into individual command nodes so we only parse the ``cp`` portion.
try:
    import bashlex
    _HAS_BASHLEX = True
except ImportError:
    _HAS_BASHLEX = False


def _find_cp_commands(bash_block: str) -> list[str]:
    if _HAS_BASHLEX and "<<" not in bash_block:
        try:
            parts = bashlex.parse(bash_block)
        except Exception:
            return [bash_block]
        cmds: list[str] = []

        def _walk(node):
            if node.kind == "command":
                words = [p.word for p in node.parts if p.kind == "word"]
                if words and words[0] == "cp":
                    cmds.append(bash_block[node.pos[0]:node.pos[1]])
            for attr in ("parts", "list"):
                for child in getattr(node, attr, []) or []:
                    _walk(child)

        for p in parts:
            _walk(p)
        return cmds
    return [bash_block]


def is_cp_command(text: str) -> bool:
    """Return ``True`` if ``text`` is a ``cp`` command."""
    return text.strip().startswith("cp ")


def extract_cp_pairs(action: str) -> list[tuple[str, str]]:
    """Return ``(source, dest)`` for every ``cp`` in ``action``, in order.

    ``dest`` is a normalized repo path; ``source`` is normalized too, except external
    absolute sources (``/tmp/``, ``/home/`` …) which are kept raw for adapter-level remap.
    """
    pairs: list[tuple[str, str]] = []
    for cmd in _find_cp_commands(action):
        tokens = cmd.strip().split()
        args = [t for t in tokens[1:] if not t.startswith("-")]
        if len(args) < 2:
            continue
        raw_source = args[-2]
        source = normalize_linux_repo_path(raw_source)
        dest = normalize_linux_repo_path(args[-1])
        if not dest:
            continue
        # AIDEV-NOTE: external source (e.g. /tmp/, /home/) → keep raw path for remap in parser
        if not is_linux_tree_path(raw_source) and raw_source.startswith("/"):
            source = raw_source
        elif not source:
            continue
        pairs.append((source, dest))
    return pairs


def cp_edits_for_pairs(pairs: list[tuple[str, str]], explanation_prefix: str) -> list[Edit]:
    """Build one ``COPY_FILE`` edit per ``(source, dest)`` pair."""
    return [
        Edit(
            filename=dest,
            before=source,
            after="",
            edit_type=EditType.COPY_FILE,
            explanation=f"{explanation_prefix} cp",
        )
        for source, dest in pairs
    ]


def parse_cp_command(action: str, explanation_prefix: str) -> list[Edit]:
    return cp_edits_for_pairs(extract_cp_pairs(action), explanation_prefix)
