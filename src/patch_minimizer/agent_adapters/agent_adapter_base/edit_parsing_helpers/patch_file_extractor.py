"""Convert unified-diff text (``patch`` / ``git apply`` input) into ``Edit`` objects.

mini-swe-agent frequently writes a unified diff to ``/tmp/<name>.patch`` via
``cat > /tmp/fix.patch <<'EOF' ... EOF`` and then applies it with
``patch -p1 < /tmp/fix.patch`` or ``git apply /tmp/fix.patch``. The
``cat`` heredoc target lives under ``/tmp/`` so it is filtered out by
``normalize_linux_repo_path`` and produces no edit; meanwhile
``patch_command_detection.is_patch_command`` only warns. The net effect
on the pre-Phase-3 harness was ~86 ``empty_candidate_runs`` for the
Claude mini-swe tree.

This module closes the gap by turning the heredoc body — captured by
``_messages_to_steps`` and threaded into ``parse_edit_step`` — into real
``Edit``s that ``EditApplier`` can replay.

Output conventions (matching ``sed_parsing.py``):

* ``starting_line`` uses *live-file* coordinates (the hunk's ``-A,B``).
  ``is_dd_hunk`` stays ``False`` — ``patch -p1`` applies in place, so
  ``RepoPatchManager`` must *not* adjust ``starting_line`` with DD shifts.
* ``best_effort = True`` because the heredoc body is a raw diff — we
  cannot guarantee fuzz-0 applicability ahead of time (whitespace drift,
  index-hash-only diffs, etc.). Failures are swallowed silently by
  ``EditApplier``, matching the ``sed -i`` convention.
* One ``REPLACE`` (or ``WHOLE_FILE_CREATE``/``WHOLE_FILE_DELETE``) edit
  per hunk — keeps the edit list shape close to what ``sed`` / heredoc
  extractors produce and preserves per-hunk ``starting_line``.
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


# AIDEV-NOTE: ``patch`` / ``git apply`` command detection with file-target
# capture. ``is_patch_command`` is kept narrower (True/False only) for
# back-compat; this regex is more permissive because the extractor wants to
# *find* the patch-file argument (``-i path``, ``< path``, or positional).
_PATCH_APPLY_RE = re.compile(
    r"""
    (?:^|[\s|&;])              # command position (start, or after pipe/sep)
    (?:
        patch\s+[^\n|;&]*?         # ``patch`` with any flags (no shell-sep crossing)
        (?:
            <(?!<)\s*(?P<stdin>\S+)        # patch -p1 < /tmp/fix.patch (exclude ``<<``)
          | -i\s+(?P<ishort>\S+)            # patch -p1 -i /tmp/fix.patch
          | --input[= ]?(?P<ilong>\S+)      # patch --input=/tmp/fix.patch
        )
      | git\s+apply\s+[^\n|;&]*?(?P<gpos>\S+\.(?:patch|diff))\b  # git apply /tmp/fix.{patch,diff}
    )
    """,
    re.VERBOSE,
)


def extract_patch_file_targets(bash_block: str) -> list[str]:
    """Return every patch-file path referenced by ``patch``/``git apply``
    in ``bash_block``. Paths are returned *unnormalized* (as written) so
    they match keys in the heredoc memo.
    """
    targets: list[str] = []
    for m in _PATCH_APPLY_RE.finditer(bash_block):
        path = (
            m.group("stdin")
            or m.group("ishort")
            or m.group("ilong")
            or m.group("gpos")
        )
        if path:
            # Strip trailing shell separators the regex may capture.
            path = path.rstrip("|&;")
            targets.append(path)
    return targets


# ---------------------------------------------------------------------------
# Unified-diff parser
# ---------------------------------------------------------------------------
_GIT_HEADER_RE = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)\s*$")
_NEW_FILE_RE = re.compile(r"^new file mode\s+\d+\s*$")
_DELETED_FILE_RE = re.compile(r"^deleted file mode\s+\d+\s*$")
_FROM_FILE_RE = re.compile(r"^---\s+(?P<path>\S+)(?:\s.*)?$")
_TO_FILE_RE = re.compile(r"^\+\+\+\s+(?P<path>\S+)(?:\s.*)?$")
_HUNK_HEADER_RE = re.compile(
    r"^@@\s+-(?P<old_start>\d+)(?:,(?P<old_len>\d+))?\s+"
    r"\+(?P<new_start>\d+)(?:,(?P<new_len>\d+))?\s+@@.*$"
)


def _strip_patch_prefix(path: str) -> str:
    """Drop the customary ``a/`` or ``b/`` prefix written by ``diff --git``."""
    if path in {"/dev/null", "dev/null"}:
        return ""
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _finalize_hunk(
    filename: str,
    hunk_old_start: int,
    before_lines: list[str],
    after_lines: list[str],
    new_file: bool,
    deleted_file: bool,
    explanation_prefix: str,
) -> Edit | None:
    """Turn one collected hunk into a single ``Edit``."""
    if not filename:
        return None
    prefix = (explanation_prefix or "patch_file").strip()

    if new_file:
        body = "\n".join(after_lines)
        if after_lines and not body.endswith("\n"):
            body += "\n"
        return Edit(
            filename=filename,
            before="",
            after=body,
            explanation=f"{prefix} patch-file new file",
            edit_type=EditType.WHOLE_FILE_CREATE,
            best_effort=True,
        )
    if deleted_file:
        return Edit(
            filename=filename,
            before="",
            after="",
            explanation=f"{prefix} patch-file deleted file",
            edit_type=EditType.WHOLE_FILE_DELETE,
            best_effort=True,
        )

    if not before_lines and not after_lines:
        return None
    before = "\n".join(before_lines)
    after = "\n".join(after_lines)
    if before_lines and not before.endswith("\n"):
        before += "\n"
    if after_lines and not after.endswith("\n"):
        after += "\n"
    return Edit(
        filename=filename,
        before=before,
        after=after,
        explanation=f"{prefix} patch-file hunk @ {hunk_old_start}",
        edit_type=EditType.REPLACE_PATCH_HUNK,
        match_mode=MatchMode.EXACT,
        starting_line=hunk_old_start if hunk_old_start > 0 else None,
        is_dd_hunk=False,
        best_effort=True,
    )


def parse_unified_diff(
    diff_text: str, explanation_prefix: str = ""
) -> list[Edit]:
    """Convert ``diff_text`` (unified-diff syntax) into a list of ``Edit``.

    Each hunk becomes one ``REPLACE`` ``Edit``. ``new file mode`` / 
    ``deleted file mode`` headers produce ``WHOLE_FILE_CREATE`` /
    ``WHOLE_FILE_DELETE`` edits respectively.

    Non-diff lines at the top of ``diff_text`` (e.g. mail headers, git
    commit messages, shell prompts) are tolerated — we only start
    collecting when a ``diff --git`` or ``--- `` header appears.
    """
    edits: list[Edit] = []
    lines = diff_text.splitlines()

    filename: str = ""
    new_file = False
    deleted_file = False
    in_hunk = False
    hunk_old_start = 0
    before_lines: list[str] = []
    after_lines: list[str] = []

    def finish_file() -> None:
        """Emit WHOLE_FILE_CREATE/DELETE when the current file block ends."""
        nonlocal new_file, deleted_file, filename
        if not filename:
            new_file = False
            deleted_file = False
            return
        if deleted_file:
            edits.append(
                Edit(
                    filename=filename,
                    before="",
                    after="",
                    explanation=f"{(explanation_prefix or 'patch_file').strip()} "
                                f"patch-file deleted file",
                    edit_type=EditType.WHOLE_FILE_DELETE,
                    best_effort=True,
                )
            )
        elif new_file:
            # after_lines was filled by the (single) hunk of a new-file diff.
            body = "\n".join(after_lines)
            if after_lines and not body.endswith("\n"):
                body += "\n"
            edits.append(
                Edit(
                    filename=filename,
                    before="",
                    after=body,
                    explanation=f"{(explanation_prefix or 'patch_file').strip()} "
                                f"patch-file new file",
                    edit_type=EditType.WHOLE_FILE_CREATE,
                    best_effort=True,
                )
            )
        new_file = False
        deleted_file = False

    def flush_hunk() -> None:
        nonlocal before_lines, after_lines, in_hunk
        if not in_hunk:
            return
        # WHOLE_FILE_CREATE / DELETE are emitted once per file in
        # ``finish_file``; per-hunk REPLACE edits are not appropriate for
        # those cases.
        if not (new_file or deleted_file):
            edit = _finalize_hunk(
                filename, hunk_old_start, before_lines, after_lines,
                new_file=False, deleted_file=False,
                explanation_prefix=explanation_prefix,
            )
            if edit is not None:
                edits.append(edit)
        before_lines = []
        after_lines = []
        in_hunk = False

    i = 0
    while i < len(lines):
        line = lines[i]

        gh = _GIT_HEADER_RE.match(line)
        if gh is not None:
            flush_hunk()
            finish_file()
            filename = normalize_linux_repo_path(_strip_patch_prefix(gh.group("b")))
            i += 1
            continue

        if _NEW_FILE_RE.match(line):
            new_file = True
            i += 1
            continue
        if _DELETED_FILE_RE.match(line):
            deleted_file = True
            i += 1
            continue

        if _FROM_FILE_RE.match(line):
            i += 1
            continue

        tf = _TO_FILE_RE.match(line)
        if tf is not None:
            flush_hunk()
            raw = tf.group("path")
            cleaned = _strip_patch_prefix(raw)
            if raw in {"/dev/null", "dev/null"}:
                deleted_file = True
            elif cleaned:
                filename = normalize_linux_repo_path(cleaned) or filename
            i += 1
            continue

        hh = _HUNK_HEADER_RE.match(line)
        if hh is not None:
            flush_hunk()
            in_hunk = True
            hunk_old_start = int(hh.group("old_start"))
            before_lines = []
            after_lines = []
            i += 1
            continue

        if in_hunk:
            if not line:
                before_lines.append("")
                after_lines.append("")
                i += 1
                continue
            marker = line[0]
            body = line[1:] if len(line) > 1 else ""
            if marker == " ":
                before_lines.append(body)
                after_lines.append(body)
            elif marker == "-":
                before_lines.append(body)
            elif marker == "+":
                after_lines.append(body)
            elif marker == "\\":
                pass
            else:
                flush_hunk()
                continue
            i += 1
            continue

        i += 1

    flush_hunk()
    finish_file()

    return [e for e in edits if e is not None]


# ---------------------------------------------------------------------------
# High-level entry used by ``BashEditExtractor``
# ---------------------------------------------------------------------------


PATCH_EXPLANATION_TAG = "patch-file"


def is_patch_edit(edit) -> bool:
    """Return ``True`` if ``edit`` was extracted from a unified-diff patch
    file. Used by ``parsers.py``'s trajectory-level dedupe post-pass to
    keep only the most-recent patch edit per filename across revisions —
    agents iterate patch v1, v2, v3 on the same file across run_kernel
    boundaries; applying all three cumulatively double-applies hunks.
    """
    return PATCH_EXPLANATION_TAG in (edit.explanation or "")


_PATCHING_FILE_RE = re.compile(r"^patching file (?P<path>\S+)\s*$", re.MULTILINE)
_HUNK_FAILED_RE = re.compile(
    r"^Hunk\s+#(?P<idx>\d+)\s+FAILED", re.MULTILINE
)
# AIDEV-NOTE: ``patch`` aborts mid-file with these fatal markers — when seen
# inside a ``patching file FOO`` section, NO hunks for FOO were applied. We
# must drop all edits for that file (not just specific hunk indices).
_FILE_FATAL_RE = re.compile(
    r"patch:\s+\*+\s+(?:malformed patch|"
    r"unexpected (?:end of|garbage)|"
    r"Only garbage|"
    r"hunk format)",
    re.IGNORECASE,
)


def parse_patch_outcomes(user_response: str) -> tuple[set[tuple[str, int]], set[str]]:
    """Parse ``patch`` tool output into ``(failed_hunks, fatal_files)``.

    - ``failed_hunks``: ``{(normalized_filename, hunk_index_1based), ...}`` —
      individual hunks the tool reported ``FAILED`` for. Successful hunks
      at the expected offset are silent.
    - ``fatal_files``: ``{normalized_filename, ...}`` — files where ``patch``
      printed a section-level fatal marker (e.g. ``malformed patch``).
      No hunks were applied to these files; their edits must be dropped
      entirely so the empty result reflects reality.
    """
    failures: set[tuple[str, int]] = set()
    fatal: set[str] = set()
    if not user_response:
        return failures, fatal
    file_matches = list(_PATCHING_FILE_RE.finditer(user_response))
    for i, fm in enumerate(file_matches):
        start = fm.end()
        end = file_matches[i + 1].start() if i + 1 < len(file_matches) else len(user_response)
        chunk = user_response[start:end]
        path = normalize_linux_repo_path(fm.group("path")) or fm.group("path")
        if _FILE_FATAL_RE.search(chunk):
            fatal.add(path)
            continue
        for hm in _HUNK_FAILED_RE.finditer(chunk):
            failures.add((path, int(hm.group("idx"))))
    return failures, fatal


def parse_patch_failures(user_response: str) -> set[tuple[str, int]]:
    """Back-compat wrapper around ``parse_patch_outcomes`` returning only
    the per-hunk failure set. New callers should use ``parse_patch_outcomes``.
    """
    failures, _ = parse_patch_outcomes(user_response)
    return failures


def extract_edits_from_patch_cmd(
    bash_block: str,
    patch_file_bodies: dict[str, str] | None,
    explanation_prefix: str = "",
    *,
    user_response: str = "",
) -> list[Edit]:
    """For every ``patch``/``git apply`` command in ``bash_block``, look up
    the patch-file body in ``patch_file_bodies`` and parse it into edits.
    Unknown targets (body not captured) are silently skipped — the caller
    is expected to have threaded all ``cat > /tmp/*.patch`` bodies into
    the memo already.

    When ``user_response`` is provided (the shell output of the patch
    invocation), per-file/per-hunk ``Hunk #N FAILED`` lines are honored —
    failed hunks are filtered out so partial-rc=1 patches still contribute
    their successful hunks. Hunks within a file are numbered in the order
    they appear in the patch body (1-based), matching the patch tool.
    """
    if not patch_file_bodies:
        return []
    failures, fatal_files = parse_patch_outcomes(user_response)
    out: list[Edit] = []
    for target in extract_patch_file_targets(bash_block):
        body = _lookup_patch_body(patch_file_bodies, target)
        if body is None:
            continue
        edits = parse_unified_diff(body, explanation_prefix)
        if fatal_files or failures:
            # Re-number per file (hunk index resets per filename). WHOLE_FILE_*
            # edits count as hunk 1 for their file because patch outputs one
            # ``patching file`` line per file regardless of operation. Files
            # in ``fatal_files`` had no hunks applied at all — drop every
            # edit for them.
            per_file_idx: dict[str, int] = {}
            kept: list[Edit] = []
            for e in edits:
                if e.filename in fatal_files:
                    continue
                per_file_idx[e.filename] = per_file_idx.get(e.filename, 0) + 1
                if (e.filename, per_file_idx[e.filename]) in failures:
                    continue
                kept.append(e)
            edits = kept
        out.extend(edits)
    return out


def _lookup_patch_body(
    memo: dict[str, str], target: str
) -> str | None:
    """Look up ``target`` in ``memo`` tolerating common path variants
    (absolute vs relative, leading ``./``, trailing quotes)."""
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
    # Last-resort basename match — rare but handles ``cd /tmp && patch -i fix.patch``
    base = target.rsplit("/", 1)[-1]
    for k, v in memo.items():
        if k.rsplit("/", 1)[-1] == base:
            return v
    return None


def is_valid_patch_command(block: str, patch_file_bodies: dict[str, str]) -> bool:
    """Return ``True`` if ``block`` contains a ``patch``/``git apply`` command
    whose patch body has been memoized in ``patch_file_bodies``."""
    targets = extract_patch_file_targets(block)
    return any(_lookup_patch_body(patch_file_bodies, t) is not None for t in targets)


def iter_heredoc_patch_targets(
    heredoc_matches: Iterable[tuple[str, str]],
) -> dict[str, str]:
    """Filter a list of ``(raw_path, body)`` heredoc captures down to
    patch-file candidates (path under ``/tmp/`` or ending in ``.patch`` /
    ``.diff``). Returned dict is keyed by the raw path so the command-side
    lookup works without re-normalization."""
    out: dict[str, str] = {}
    for raw_path, body in heredoc_matches:
        if not body or not body.strip():
            continue
        tmp_like = raw_path.startswith("/tmp/") or "/tmp/" in raw_path
        patchy = raw_path.endswith((".patch", ".diff"))
        if tmp_like or patchy:
            out[raw_path] = body
    return out
