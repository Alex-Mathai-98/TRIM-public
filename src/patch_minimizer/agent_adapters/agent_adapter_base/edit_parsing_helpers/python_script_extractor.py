"""Convert canonical inline-Python edit scripts into ``Edit`` objects.

mini-swe-agent agents frequently apply multi-line code changes by writing
a Python script to ``/tmp/<name>.py`` via ``cat > /tmp/foo.py <<'EOF'``,
then running ``python /tmp/foo.py``. The dominant idiom (covering 5 of
the 8 python-script residuals in the post-aic-fix snapshot) is:

    with open('PATH', 'r') as f:
        content = f.read()

    old_X = '''<original code block>'''
    new_X = '''<replacement code block>'''
    content = content.replace(old_X, new_X)

    with open('PATH', 'w') as f:
        f.write(content)

This module recognises that idiom and emits one ``EditType.REPLACE`` per
``content.replace(old_X, new_X)`` call, where ``old_X`` and ``new_X``
each refer to a triple-quoted block defined above. Other Python edit
patterns (``re.sub``, ``readlines`` + manual loop) are deliberately not
extracted here — they require AST-level analysis or are agent-specific.

Output conventions (matching ``patch_file_extractor.py``):

* ``EditType.REPLACE`` with ``starting_line=None`` — substring match
  against the live file at apply time. ``is_dd_hunk=False``.
* ``best_effort = True`` — the agent's script may have failed silently
  (``content.replace`` is a no-op when ``old_X`` isn't present), so we
  cannot assert the edit applies. Mirrors sed-extracted edits.
* One edit per ``replace`` call, in document order — preserves the
  agent's chained-replace ordering when a later script edits the output
  of an earlier one (``apply_fix.py`` → ``apply_fix2.py``).
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from patch_minimizer.core.edit import Edit, EditType, MatchMode
from patch_minimizer.agent_adapters.agent_adapter_base.utils import (
    normalize_linux_repo_path,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bash-side: detect ``python /tmp/X.py`` runs and extract their targets
# ---------------------------------------------------------------------------

_PY_RUN_RE = re.compile(
    r"\bpython[23]?\b\s+(?P<path>\S+\.py)\b",
)

# ``python3 << 'EOF' ... EOF`` — inline script heredoc, no /tmp file involved.
# The script body runs directly. Same idiom recognition as the file-based
# form, so we plug it into the same parser.
_PY_INLINE_HEREDOC_RE = re.compile(
    r"\bpython[23]?\b\s*<<\s*['\"]?(?P<delim>\w+)['\"]?\s*\n"
    r"(?P<body>.*?)\n(?P=delim)\b",
    re.DOTALL,
)


def detect_python_run_cmd(bash_block: str) -> bool:
    """Return ``True`` if ``bash_block`` invokes ``python /path/X.py`` or
    runs an inline ``python << EOF`` heredoc."""
    return bool(_PY_RUN_RE.search(bash_block)) or bool(
        _PY_INLINE_HEREDOC_RE.search(bash_block)
    )


def extract_python_run_targets(bash_block: str) -> list[str]:
    """Return the script paths run by every ``python <path>.py`` in
    ``bash_block``. Bare ``python <path>.py`` only — inline ``python
    <<EOF`` heredocs are handled by ``extract_inline_python_bodies``."""
    return [m.group("path") for m in _PY_RUN_RE.finditer(bash_block)]


def extract_inline_python_bodies(bash_block: str) -> list[str]:
    """Return script bodies for every inline ``python << EOF`` invocation."""
    return [m.group("body") for m in _PY_INLINE_HEREDOC_RE.finditer(bash_block)]


def iter_heredoc_python_targets(
    heredoc_matches: Iterable[tuple[str, str]],
) -> dict[str, str]:
    """Filter ``(raw_path, body)`` heredoc captures down to ``.py`` script
    candidates. Returned dict is keyed by the raw path so the command-side
    lookup works without re-normalisation. Mirrors
    ``iter_heredoc_patch_targets`` for symmetry."""
    out: dict[str, str] = {}
    for raw_path, body in heredoc_matches:
        if not body or not body.strip():
            continue
        if raw_path.endswith(".py"):
            out[raw_path] = body
    return out


# ---------------------------------------------------------------------------
# Script-body parser: ``open(...) → old_X / new_X → content.replace → write``
# ---------------------------------------------------------------------------

# ``with open('PATH'[, 'r']) as f:\n    content = f.read()``
_OPEN_READ_RE = re.compile(
    r"with\s+open\s*\(\s*['\"](?P<path>[^'\"]+)['\"]"
    r"(?:\s*,\s*['\"]r[bU]?['\"])?\s*\)\s*as\s+(?P<fh>\w+)\s*:\s*"
    r"\n\s*(?P<var>\w+)\s*=\s*(?P=fh)\.read\(\)"
)

# ``with open('PATH', 'w') as f:\n    f.write(...)``
_OPEN_WRITE_RE = re.compile(
    r"with\s+open\s*\(\s*['\"](?P<path>[^'\"]+)['\"]\s*,\s*['\"]w[bU]?\+?['\"]"
    r"\s*\)\s*as\s+(?P<fh>\w+)\s*:\s*"
    r"\n\s*(?P=fh)\.write\("
)

# ``<varname> = '''...'''`` or ``<varname> = """..."""`` (assignment at line start).
# AIDEV-NOTE: we accept any string-prefix (``r``/``b``/``u`` etc.) but pass
# the body through verbatim — the agents we see don't use raw-string escapes
# in practice for the ``old_X`` / ``new_X`` blocks.
_TRIPLEQUOTE_BLOCK_RE = re.compile(
    r"^(?P<var>\w+)\s*=\s*(?P<prefix>[rRbBuU]*?)(?P<quote>'''|\"\"\")"
    r"(?P<body>.*?)(?P=quote)",
    re.MULTILINE | re.DOTALL,
)

# ``.replace(old_X, new_X)`` — restricted to two-arg, both variable refs.
# Method-call form ``content = content.replace(...)``, ``content = content.replace(...).replace(...)``
# — each ``.replace(...)`` call match is processed independently.
_REPLACE_CALL_RE = re.compile(
    r"\.replace\s*\(\s*(?P<old>\w+)\s*,\s*(?P<new>\w+)\s*\)"
)


def parse_python_replace_pairs(script_body: str) -> list[tuple[str, str, str]]:
    """Return ``[(filename, before, after), …]`` extracted from one Python
    script body. Empty list when the script does not match the canonical
    open / triple-quoted-blocks / replace / write idiom — those scripts
    are left for explicit handling.

    ``filename`` is normalised to repo-root relative (no ``/linux/`` prefix).
    Scratch paths (``/tmp/...``) are rejected because they don't correspond
    to kernel files we patch.
    """
    open_m = _OPEN_READ_RE.search(script_body)
    if not open_m:
        return []
    raw_path = open_m.group("path")
    filename = normalize_linux_repo_path(raw_path)
    if not filename or filename.startswith("/tmp/"):
        return []
    if not _OPEN_WRITE_RE.search(script_body):
        # No write-back — the script reads but never persists. Skip.
        return []

    blocks: dict[str, str] = {}
    for m in _TRIPLEQUOTE_BLOCK_RE.finditer(script_body):
        blocks[m.group("var")] = m.group("body")

    pairs: list[tuple[str, str, str]] = []
    for m in _REPLACE_CALL_RE.finditer(script_body):
        old_var, new_var = m.group("old"), m.group("new")
        if old_var in blocks and new_var in blocks:
            pairs.append((filename, blocks[old_var], blocks[new_var]))
    return pairs


# ---------------------------------------------------------------------------
# High-level entry — parallel to ``extract_edits_from_patch_cmd``
# ---------------------------------------------------------------------------

def extract_edits_from_python_run(
    bash_block: str,
    python_file_bodies: dict[str, str] | None,
    explanation_prefix: str = "",
) -> list[Edit]:
    """For every ``python /tmp/<name>.py`` invocation OR inline
    ``python << EOF`` heredoc in ``bash_block``, parse the script body via
    ``parse_python_replace_pairs`` and emit one edit per replace pair.

    File-based runs look up bodies in ``python_file_bodies`` (memo keyed by
    the heredoc target path). Inline heredoc bodies are parsed directly
    from ``bash_block`` because they don't go through the /tmp memo.

    Edits are emitted in invocation order so a chained-replace flow
    (``apply_fix.py`` runs first, ``apply_fix2.py`` second) lands in the
    right sequence.
    """
    out: list[Edit] = []

    def _emit_pairs(body: str, source_label: str) -> None:
        pairs = parse_python_replace_pairs(body)
        for n, (filename, before, after) in enumerate(pairs, start=1):
            if before == after:
                continue
            out.append(Edit(
                filename=filename,
                before=before,
                after=after,
                explanation=f"{explanation_prefix} {source_label} replace #{n}",
                best_effort=True,
                edit_type=EditType.REPLACE,
                match_mode=MatchMode.EXACT,
            ))

    if python_file_bodies:
        for target in extract_python_run_targets(bash_block):
            body = _lookup_python_body(python_file_bodies, target)
            if body is not None:
                _emit_pairs(body, "python /tmp/*.py")

    for body in extract_inline_python_bodies(bash_block):
        _emit_pairs(body, "python <<EOF")

    return out


def _lookup_python_body(memo: dict[str, str], target: str) -> str | None:
    """Look up ``target`` in ``memo`` tolerating common path variants
    (absolute vs relative, leading ``./``)."""
    if target in memo:
        return memo[target]
    candidates = [
        target,
        target.lstrip("./"),
        "/" + target.lstrip("/"),
    ]
    for c in candidates:
        if c in memo:
            return memo[c]
    base = target.rsplit("/", 1)[-1]
    for k, v in memo.items():
        if k.rsplit("/", 1)[-1] == base:
            return v
    return None
