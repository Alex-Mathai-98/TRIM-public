"""Convert ``head | cat | tail | cp/mv`` splice patterns into ``Edit`` objects.

mini-swe-agent agents occasionally replace a function body or block of lines
in a kernel source file by splicing around the lines being replaced rather
than via ``sed``. The dominant idiom is:

    cat > /tmp/middle.c << 'EOF'   # new function body, written earlier
    ...
    EOF
    head -n 278 fs/foo.c > /tmp/out
    cat /tmp/middle.c >> /tmp/out
    tail -n +357 fs/foo.c >> /tmp/out
    cp /tmp/out fs/foo.c

Three single-block variants are observed in mini-swe-claude trajectories:

* **A** — scratch accumulator + ``cp/mv`` back to FILE
  (e.g. 25a9bc64). ``head_dest == tail_dest`` (a ``/tmp/...`` path).
* **B** — ``cp FILE FILE.bak`` snapshot, then writes directly to FILE using
  ``head .bak > FILE``, inline ``cat >> FILE << EOF``, ``tail .bak >> FILE``
  (e.g. d47fcea92921). ``head_dest == tail_dest == FILE``.
* **C** — scratch parts (``/tmp/p1``, ``/tmp/p2``) + ``cat A B C > FILE``
  (e.g. dba9b954bfaa). ``head_dest != tail_dest``; final ``cat`` redirect
  concatenates them with a middle scratch in between.

All three reduce to the same effect: replace lines ``(head_N+1)..(tail_M-1)``
of FILE with the body of the middle scratch (or inline heredoc). One
``EditType.CHANGE_RANGE`` edit is emitted per detected splice — the applier
already replaces a 1-based line range with a literal block (see
``perform_in_memory_edit_CHANGE_RANGE``).

Output conventions (matching ``patch_file_extractor.py`` /
``python_script_extractor.py``):

* ``EditType.CHANGE_RANGE`` with explicit ``starting_line``/``ending_line``
  (LIVE-file coords; ``is_dd_hunk=False``).
* ``best_effort=True`` — the agent's bash may have failed silently or the
  file's line count may have shifted from a prior step; we cannot assert
  the edit applies. Mirrors sed-extracted edits.
* ``before=""`` — RANGE_CHANGE replaces by line range, not by content.
* ``explanation`` carries the literal token ``"splice"`` so the
  trajectory-level post-pass can dedupe repeated splices on the same file.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from patch_minimizer.core.edit import Edit, EditType
from patch_minimizer.agent_adapters.agent_adapter_base.utils import (
    normalize_linux_repo_path,
)

logger = logging.getLogger(__name__)

# AIDEV-NOTE: ``splice`` token in explanation is load-bearing — see
# MiniSweMessagesTrajectoryParser.parse() post-pass which dedupes splice
# edits per filename across revisions when the agent re-runs the same
# splice multiple times (variant C / dba9b954bfaa).
SPLICE_EXPLANATION_TAG = "splice"


# ---------------------------------------------------------------------------
# Bash command regexes
# ---------------------------------------------------------------------------

# ``head -n N PATH > DEST`` or ``head -nN`` or ``head -N`` (POSIX-allowed).
_HEAD_RE = re.compile(
    r"\bhead\s+(?:-n?\s*)(?P<n>\d+)\s+(?P<src>\S+)\s*(?P<redir>>>?)\s*(?P<dest>\S+)"
)
# AIDEV-NOTE: piped variant: ``cat FILE | head -n N > DEST``
_HEAD_PIPED_RE = re.compile(
    r"\bcat\s+(?P<src>\S+)\s*\|\s*head\s+(?:-n?\s*)(?P<n>\d+)\s*(?P<redir>>>?)\s*(?P<dest>\S+)"
)

# ``tail -n +M PATH > DEST`` — only the ``+M`` (1-based start-from-line) form
# is interesting for splices; ``tail -n M`` (last M lines) is used for
# inspection, not splicing.
_TAIL_RE = re.compile(
    r"\btail\s+-n\s*\+(?P<m>\d+)\s+(?P<src>\S+)\s*(?P<redir>>>?)\s*(?P<dest>\S+)"
)

# ``cp SRC DEST`` and ``mv SRC DEST`` (single source, single dest only).
_CP_RE = re.compile(r"\bcp\s+(?P<src>\S+)\s+(?P<dest>\S+)")
_MV_RE = re.compile(r"\bmv\s+(?P<src>\S+)\s+(?P<dest>\S+)")

# ``cat A B C ... > DEST`` or ``cat A >> DEST``. The paths group is
# non-greedy and stops at the redirect operator.
_CAT_REDIRECT_RE = re.compile(
    r"\bcat\s+(?P<paths>(?:\S+\s+)+?)(?P<redir>>>?)\s*(?P<dest>\S+)"
)

# ``cat >> DEST << 'EOF' ... EOF`` — append-heredoc to DEST. Variant B uses
# this to write the middle body directly into FILE.
_CAT_APPEND_HEREDOC_RE = re.compile(
    r"\bcat\s*>>\s*(?P<dest>\S+)\s*<<\s*['\"]?(?P<delim>\w+)['\"]?\s*\n"
    r"(?P<body>.*?)\n(?P=delim)\b",
    re.DOTALL,
)

# ``cat >> SCRATCH << 'EOF' ... EOF`` — append-heredoc to a scratch path
# (used inside variant A when the agent inlines the middle body rather than
# referencing a previously-written /tmp/X).
_CAT_APPEND_SCRATCH_HEREDOC_RE = re.compile(
    r"\bcat\s*>>\s*(?P<dest>/tmp/\S+)\s*<<\s*['\"]?(?P<delim>\w+)['\"]?\s*\n"
    r"(?P<body>.*?)\n(?P=delim)\b",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Heredoc-target filter for the cumulative_tmp_files memo
# ---------------------------------------------------------------------------

def iter_heredoc_tmp_targets(
    heredoc_matches: Iterable[tuple[str, str]],
) -> dict[str, str]:
    """Filter ``(raw_path, body)`` heredoc captures down to ``/tmp/`` scratch
    bodies. Returned dict is keyed by the raw heredoc path so the splice
    extractor's lookup works without re-normalisation. Mirrors
    ``iter_heredoc_python_targets`` / ``iter_heredoc_patch_targets`` for
    symmetry.

    Distinct scoping: python and patch helpers filter by *extension*
    (``.py`` / ``.patch`` / ``.diff``) because their downstream parsers
    require a specific file format. Splices use scratch with arbitrary
    extensions (``.c``, ``.txt``, ``.h``), so we filter by *path location*
    (``/tmp/...``) instead. This avoids double-tracking heredoc rewrites of
    real source files (those already become ``WHOLE_FILE_ACTIONS`` edits
    via ``cat_heredoc_parsing.heredoc_edits``).
    """
    out: dict[str, str] = {}
    for raw_path, body in heredoc_matches:
        if not body or not body.strip():
            continue
        if not raw_path.startswith("/tmp/"):
            continue
        out[raw_path] = body
    return out


# ---------------------------------------------------------------------------
# Splice detection / extraction
# ---------------------------------------------------------------------------

def detect_splice_cmd(bash_block: str) -> bool:
    """Cheap predicate — used by ``is_edit_step`` to gate full extraction.

    True iff the block contains both a ``head -n N <path>`` and at least
    one writeback operation (``cp``/``mv``/``cat ... > FILE``/``cat >>
    FILE << EOF``). The full extractor then verifies the splice shape and
    rejects false positives.
    """
    if not _HEAD_RE.search(bash_block) and not _HEAD_PIPED_RE.search(bash_block):
        return False
    if _CP_RE.search(bash_block):
        return True
    if _MV_RE.search(bash_block):
        return True
    if _CAT_REDIRECT_RE.search(bash_block):
        return True
    if _CAT_APPEND_HEREDOC_RE.search(bash_block):
        return True
    return False


def extract_edits_from_splice(
    bash_block: str,
    tmp_file_bodies: dict[str, str] | None,
    explanation_prefix: str = "",
) -> list[Edit]:
    """Parse splice patterns in ``bash_block`` and emit ``RANGE_CHANGE``
    edits. Returns ``[]`` when no splice shape is detected — the caller
    should fall through to other extractors (this one is additive).
    """
    tmp_bodies = dict(tmp_file_bodies or {})

    # ``cp FILE FILE.bak`` snapshots: alias FILE.bak → FILE so head/tail
    # reads from the .bak resolve to the same real file as reads from
    # FILE itself (variant B and one form of variant C).
    alias_map = _build_bak_aliases(bash_block)

    # Index head/tail commands by the underlying real source file.
    # AIDEV-NOTE: merge direct and piped head matches — both have same named groups.
    import itertools
    head_matches = itertools.chain(_HEAD_RE.finditer(bash_block), _HEAD_PIPED_RE.finditer(bash_block))
    heads = _index_head_tail(head_matches, alias_map, "n")
    tails = _index_head_tail(_TAIL_RE.finditer(bash_block), alias_map, "m")

    edits: list[Edit] = []
    for fn, head_entries in heads.items():
        if fn not in tails:
            continue
        head_N, head_dest = head_entries[0]
        tail_M, tail_dest = tails[fn][0]
        if head_N + 1 >= tail_M:
            # Empty range or inverted; nothing to splice.
            continue

        resolved = _resolve_middle_body(
            bash_block,
            fn,
            head_dest,
            tail_dest,
            tmp_bodies,
            alias_map,
        )
        if resolved is None:
            continue
        body, final_fn = resolved
        if final_fn != fn:
            continue
        if not body:
            continue

        edits.append(
            Edit(
                filename=fn,
                before="",
                after=body,
                explanation=(
                    f"{explanation_prefix} {SPLICE_EXPLANATION_TAG} "
                    f"replace lines {head_N + 1}-{tail_M - 1}"
                ),
                best_effort=True,
                edit_type=EditType.CHANGE_RANGE,
                starting_line=head_N + 1,
                ending_line=tail_M - 1,
            )
        )

    return edits


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_bak_aliases(bash_block: str) -> dict[str, str]:
    """Return ``{bak_path: real_path}`` for every ``cp FILE FILE.bak``
    seen in the block. We deliberately ignore ``cp`` to non-.bak paths
    so we don't conflate scratch building with snapshotting.
    """
    aliases: dict[str, str] = {}
    for m in _CP_RE.finditer(bash_block):
        src, dst = m.group("src"), m.group("dest")
        if dst.endswith(".bak") and not src.endswith(".bak") and not src.startswith("/tmp/"):
            aliases[dst] = src
    return aliases


def _index_head_tail(
    matches: Iterable[re.Match[str]],
    alias_map: dict[str, str],
    n_group: str,
) -> dict[str, list[tuple[int, str]]]:
    """Group head/tail matches by the underlying real source file.

    Returns ``{normalised_real_path: [(N_or_M, redirect_dest), ...]}``.
    ``.bak`` aliases are resolved via ``alias_map`` so a ``head -n 645
    FILE.bak > X`` is keyed by FILE.
    """
    out: dict[str, list[tuple[int, str]]] = {}
    for m in matches:
        src = m.group("src")
        if src in alias_map:
            src = alias_map[src]
        fn = _norm_real_file(src)
        if not fn:
            continue
        try:
            n_val = int(m.group(n_group))
        except ValueError:
            continue
        out.setdefault(fn, []).append((n_val, m.group("dest")))
    return out


def _norm_real_file(path: str) -> str | None:
    """Return repo-root-relative path iff ``path`` is a real source file
    (not ``/tmp/...``, not a ``.bak`` snapshot). ``normalize_linux_repo_path``
    strips ``/linux/`` and similar prefixes the agent uses inside the
    docker.
    """
    if not path:
        return None
    if path.startswith("/tmp/"):
        return None
    if path.endswith(".bak"):
        return None
    fn = normalize_linux_repo_path(path)
    if not fn or fn.startswith("/tmp/"):
        return None
    return fn


def _resolve_middle_body(
    bash_block: str,
    fn: str,
    head_dest: str,
    tail_dest: str,
    tmp_bodies: dict[str, str],
    alias_map: dict[str, str],
) -> tuple[str, str] | None:
    """Return ``(middle_body, final_dest_fn)`` for the splice, or ``None``.

    Variant dispatch is a flat decision based on where ``head_dest`` and
    ``tail_dest`` point:

    * **Variant B** (head_dest == FILE): writes directly to FILE; middle
      is the inline ``cat >> FILE << EOF`` body.
    * **Variant A** (head_dest == tail_dest, both ``/tmp/...``): scratch
      accumulator; middle is whichever ``cat /tmp/Y >> scratch`` body is
      memoised, plus a final ``cp/mv scratch FILE``.
    * **Variant C** (head_dest != tail_dest, both ``/tmp/...``): final
      ``cat A B C > FILE`` concatenation; middle is the path between
      head_dest and tail_dest in the cat list.
    """
    head_dest_fn = normalize_linux_repo_path(head_dest)

    # Variant B — head goes straight to a real file.
    if head_dest_fn == fn:
        for m in _CAT_APPEND_HEREDOC_RE.finditer(bash_block):
            dest_fn = normalize_linux_repo_path(m.group("dest"))
            if dest_fn == fn and m.group("body"):
                return m.group("body"), fn
        return None

    # Variant A — single scratch accumulator.
    if head_dest == tail_dest and head_dest.startswith("/tmp/"):
        scratch = head_dest
        body = _find_appended_body(bash_block, scratch, tmp_bodies)
        if body is None:
            return None
        final_fn = _find_final_dest(bash_block, scratch)
        if final_fn != fn:
            return None
        return body, fn

    # Variant C — multi-cat concatenation directly into FILE.
    for m in _CAT_REDIRECT_RE.finditer(bash_block):
        if m.group("redir") != ">":
            # Append (>>) form — handled as variant B above; here we only
            # care about full overwrites.
            continue
        dest_fn = normalize_linux_repo_path(m.group("dest"))
        if dest_fn != fn:
            continue
        paths = m.group("paths").split()
        if head_dest in paths and tail_dest in paths:
            i_h, i_t = paths.index(head_dest), paths.index(tail_dest)
            lo, hi = (i_h, i_t) if i_h < i_t else (i_t, i_h)
            for mp in paths[lo + 1:hi]:
                body = _lookup_body(tmp_bodies, mp)
                if body:
                    return body, fn

    return None


def _find_appended_body(
    bash_block: str,
    scratch: str,
    tmp_bodies: dict[str, str],
) -> str | None:
    """For variant A: find the middle body appended into ``scratch``.

    Looks for either ``cat /tmp/Y >> scratch`` (memo lookup) or an inline
    ``cat >> scratch << EOF ... EOF`` heredoc. Returns the first non-empty
    body found; ``None`` when nothing matches.
    """
    scratch_re = re.escape(scratch)
    for m in re.finditer(
        rf"\bcat\s+(?P<paths>[^|<>\n]+?)\s*>>\s*{scratch_re}",
        bash_block,
    ):
        for p in m.group("paths").split():
            body = _lookup_body(tmp_bodies, p)
            if body:
                return body
    for m in re.finditer(
        rf"\bcat\s*>>\s*{scratch_re}\s*<<\s*['\"]?(?P<delim>\w+)['\"]?\s*\n"
        rf"(?P<body>.*?)\n(?P=delim)\b",
        bash_block,
        re.DOTALL,
    ):
        if m.group("body"):
            return m.group("body")
    return None


def _find_final_dest(bash_block: str, scratch: str) -> str | None:
    """Return the real-file destination of the first ``cp scratch FILE``
    or ``mv scratch FILE`` in the block; ``None`` when neither found or
    the destination is not a real source file.
    """
    for m in _CP_RE.finditer(bash_block):
        if m.group("src") == scratch:
            fn = _norm_real_file(m.group("dest"))
            if fn:
                return fn
    for m in _MV_RE.finditer(bash_block):
        if m.group("src") == scratch:
            fn = _norm_real_file(m.group("dest"))
            if fn:
                return fn
    return None


def _lookup_body(memo: dict[str, str], target: str) -> str | None:
    """Resolve ``target`` against ``memo`` tolerating absolute/relative
    variants and basename-only matches (mirrors ``_lookup_python_body``).
    """
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


def is_splice_edit(edit: Edit) -> bool:
    """Return True iff ``edit`` was emitted by ``extract_edits_from_splice``.

    Identifies splice-origin edits via the ``"splice"`` token in
    ``explanation`` (set by ``extract_edits_from_splice``). Used by the
    trajectory-level post-pass to dedupe repeated splices on the same file.
    """
    if edit.edit_type != EditType.CHANGE_RANGE:
        return False
    return SPLICE_EXPLANATION_TAG in (edit.explanation or "")
