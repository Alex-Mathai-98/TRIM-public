"""
EditApplier: Applies Edit objects to source code files.

Contains methods migrated from kernel_write_strategies/kernel_write_utils.py:
- check_edit_application(): Validates edit can be applied without modifying file
- apply_edit_to_linux_code(): Applies edit to file with thread safety

AIDEV-NOTE: Part of z-cpl-74 migration. These methods require RWLock to be held.
"""
from __future__ import annotations

import os
import re
import shutil
from typing import Optional, Dict, List, Any

from copy import deepcopy

from patch_minimizer.core.edit import Edit, EditType, MatchMode
from patch_minimizer.core.rw_lock import RWLock
from dataclasses import dataclass

@dataclass
class ReplaceResult:
    orig_prog_lines: str
    match_type: str
    new_content: str
    start_line: int = 0   # 0-indexed line where old_str began
    end_line: int = 0     # 0-indexed line where old_str ended (inclusive)

_DEFAULT_RESULT = ReplaceResult(
    orig_prog_lines=None,
    new_content=None,
    start_line=None,
    end_line=None,
    match_type=None
)


from patch_minimizer.core.utils import unescape_sed_replacement as _unescape_sed_replacement


def _strip_anchors(needle: str) -> tuple[str, bool, bool] | None:
    """Strip ^/$ anchors from needle, return (core, anchor_start, anchor_end).

    Returns None if needle is empty or has no core and no anchors.
    """
    if not needle:
        return None
    anchor_start = needle.startswith("^")
    anchor_end = needle.endswith("$")
    core = needle
    if anchor_start:
        core = core[1:]
    if anchor_end:
        core = core[:-1]
    if not core and not (anchor_start or anchor_end):
        return None
    return core, anchor_start, anchor_end


def _bre_to_ere(pattern: str) -> str:
    """BRE→Python regex: convert BRE escapes and escape BRE-literal chars that are ERE-special.

    In BRE, ( ) + ? | are literal; only \\( \\) \\{ \\} are special.
    In Python regex (ERE), ( ) + ? | { } are special.

    Known gap: does NOT handle sed -E / sed -r (ERE mode) where bare { } ( )
    are already special. ~3 SWE-agent trajectories use sed -E.
    """
    # AIDEV-NOTE: sentinel approach — protect BRE specials before escaping BRE literals
    _GS, _GE = "\x00(G\x00", "\x00)G\x00"
    _BS, _BE = "\x00{B\x00", "\x00}B\x00"
    p = (pattern
         .replace("\\(", _GS).replace("\\)", _GE)
         .replace("\\{", _BS).replace("\\}", _BE))
    p = (p
         .replace("(", "\\(").replace(")", "\\)")
         .replace("+", "\\+").replace("?", "\\?").replace("|", "\\|"))
    return (p
            .replace(_GS, "(").replace(_GE, ")")
            .replace(_BS, "{").replace(_BE, "}"))


def _sed_repl_for_regex(text: str) -> str:
    """Escape a sed replacement string for use with ``re.sub``."""
    return text.replace("\\", "\\\\").replace("&", "\\g<0>")


def perform_exact_match(orig_prog_lines, edit) -> ReplaceResult:
    """Literal substring match: find ``edit.before`` in the file and replace with ``edit.after``."""
    old_str = edit.before
    new_str = edit.after

    file_content = ''.join(orig_prog_lines)
    haystack = file_content
    needle = old_str

    occurrences = haystack.count(needle)
    if occurrences == 0:
        return _DEFAULT_RESULT

    prefix = haystack.split(needle, 1)[0]
    start_line = prefix.count("\n")
    end_line = start_line + needle.count("\n") + 1

    new_content = haystack.replace(needle, new_str, 1)

    return ReplaceResult(
        orig_prog_lines=orig_prog_lines,
        start_line=start_line,
        end_line=end_line,
        new_content=new_content,
        match_type="exact"
    )


def perform_fuzzy_match(orig_prog_lines, edit) -> ReplaceResult:
    """Whitespace-tolerant line-by-line matching.

    Strips leading/trailing whitespace from both ``edit.before`` and the
    file lines before comparing, so indentation differences don't break
    matches. If ``edit.starting_line`` is set, jumps directly to that
    line; otherwise scans the whole file.
    """
    before_lines = edit.before.split("\n")
    cleaned_before_lines = [line.strip() for line in before_lines]
    while cleaned_before_lines and cleaned_before_lines[-1] == "":
        cleaned_before_lines.pop()
    cleaned_orig_lines = [line.strip() for line in orig_prog_lines]

    match_start = -1
    match_end = -1
    if edit.starting_line is not None:
        i = edit.starting_line - 1
        if i < 0:
            return _DEFAULT_RESULT
        if (i + len(cleaned_before_lines) <= len(cleaned_orig_lines) and
                cleaned_orig_lines[i:i + len(cleaned_before_lines)] == cleaned_before_lines):
            match_start = i
            match_end = i + len(cleaned_before_lines)
        else:
            return _DEFAULT_RESULT
    else:
        for i in range(len(cleaned_orig_lines) - len(cleaned_before_lines) + 1):
            if cleaned_orig_lines[i:i + len(cleaned_before_lines)] == cleaned_before_lines:
                match_start = i
                match_end = i + len(cleaned_before_lines)
                break
        if match_start == -1:
            return _DEFAULT_RESULT

    after_lines = edit.after.split("\n")
    prefix = "".join(orig_prog_lines[:match_start])
    suffix = "".join(orig_prog_lines[match_end:])
    new_prog = prefix + "\n".join(after_lines) + "\n" + suffix

    return ReplaceResult(
        orig_prog_lines=orig_prog_lines,
        start_line=match_start,
        end_line=match_end,
        new_content=new_prog,
        match_type="fuzzy"
    )


def _unescape_sed_pattern(s: str) -> str:
    """Unescape BRE metacharacters for literal substring matching."""
    _SENTINEL = "\x00_BKSL_\x00"
    s = s.replace("\\\\", _SENTINEL)
    s = s.replace("\\/", "/")
    s = s.replace("\\[", "[").replace("\\]", "]")
    s = s.replace("\\.", ".").replace("\\*", "*").replace("\\+", "+")
    s = s.replace("\\^", "^").replace("\\$", "$")
    s = s.replace("\\(", "(").replace("\\)", ")")
    s = s.replace("\\&", "&")
    s = s.replace(_SENTINEL, "\\")
    return s


def _is_pattern_in_line(pattern: str, line: str) -> bool:
    """Match a sed pattern address against a line.

    Tries literal match first (unescaped via ``in``), falls back to BRE
    regex via ``_bre_to_ere`` for anchored/regex patterns.
    """
    unescaped = _unescape_sed_pattern(pattern)
    stripped = line.rstrip("\n")
    if unescaped in stripped:
        return True
    try:
        return bool(re.search(_bre_to_ere(pattern), line))
    except re.error:
        return False


class EditApplier:
    """
    Applies Edit objects to source code files.

    All methods require the caller to hold the appropriate lock for thread safety.

    AIDEV-NOTE: Migrated from KernelWriteUtils in kernel_write_strategies/kernel_write_utils.py
    """

    # ── Replace Operations ──

    def perform_in_memory_edit_REPLACE(self, orig_prog_lines, edit):
        # AIDEV-NOTE: sub-dispatches on edit.match_mode
        if edit.match_mode == MatchMode.FUZZY:
            before = edit.before
            if (not before or not before.strip()) and edit.starting_line is None:
                return self.perform_in_memory_edit_WHOLE_FILE_REWRITE(orig_prog_lines, edit)
            return perform_fuzzy_match(orig_prog_lines, edit)
        elif edit.match_mode == MatchMode.SEMI_FUZZY:
            result = perform_exact_match(orig_prog_lines, edit)
            if result == _DEFAULT_RESULT:
                from copy import copy
                stripped = copy(edit)
                stripped.before = edit.before.strip()
                stripped.after = edit.after.strip()
                result = perform_exact_match(orig_prog_lines, stripped)
            return result
        else:
            return perform_exact_match(orig_prog_lines, edit)

    def perform_in_memory_edit_REPLACE_PATCH_HUNK(self, orig_prog_lines, edit):
        result = perform_exact_match(orig_prog_lines, edit)
        if result != _DEFAULT_RESULT:
            return result
        # Fuzz-1: trim first/last context line from both before and after.
        # split("\n") on "a\nb\n" gives ["a","b",""] — trailing "" is the
        # newline artifact, so "last real line" is index -2.
        from copy import copy
        b_lines = edit.before.split("\n")
        a_lines = edit.after.split("\n")
        if len(b_lines) < 3 or len(a_lines) < 3:
            return _DEFAULT_RESULT
        has_trailing_b = b_lines and b_lines[-1] == ""
        has_trailing_a = a_lines and a_lines[-1] == ""
        for trim in ("first", "last", "both"):
            bl, al = list(b_lines), list(a_lines)
            if trim in ("first", "both"):
                bl = bl[1:]
                al = al[1:]
            if trim in ("last", "both"):
                cut_b = -2 if has_trailing_b else -1
                cut_a = -2 if has_trailing_a else -1
                bl = bl[:cut_b] + bl[cut_b + 1:]
                al = al[:cut_a] + al[cut_a + 1:]
            fuzzed = copy(edit)
            fuzzed.before = "\n".join(bl)
            fuzzed.after = "\n".join(al)
            result = perform_exact_match(orig_prog_lines, fuzzed)
            if result != _DEFAULT_RESULT:
                return result
        return _DEFAULT_RESULT

    def perform_in_memory_edit_REPLACE_ONCE_AT_LINE(self, orig_prog_lines, edit):
        # AIDEV-NOTE: sed Ns/old/new/ semantics — substring replace on line N only.
        if edit.starting_line is None:
            return perform_exact_match(orig_prog_lines, edit)
        line_idx = edit.starting_line - 1
        if line_idx < 0 or line_idx >= len(orig_prog_lines):
            return _DEFAULT_RESULT
        target_line = orig_prog_lines[line_idx]
        # AIDEV-NOTE: edit.before is raw (escaped) from parser. Unescape for
        # literal matching; ^-anchored patterns use re.sub (BRE regex).
        before = _unescape_sed_replacement(edit.before)
        if edit.before == "$":
            # AIDEV-NOTE: sed 's/$/.../'' — append after to end of line
            stripped = target_line.rstrip("\n")
            new_line = stripped + edit.after + "\n"
        elif edit.before.startswith("^"):
            pattern = _bre_to_ere(edit.before)
            replacement = _sed_repl_for_regex(edit.after)
            new_line = re.sub(pattern, replacement, target_line, count=1)
            if new_line == target_line:
                return _DEFAULT_RESULT
        else:
            if before not in target_line:
                return _DEFAULT_RESULT
            new_line = target_line.replace(before, edit.after, 1)
        prefix = "".join(orig_prog_lines[:line_idx])
        suffix = "".join(orig_prog_lines[line_idx + 1:])
        new_prog = prefix + new_line + suffix
        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=line_idx,
            end_line=line_idx + 1,
            new_content=new_prog,
            match_type="replace_once_at_line",
        )

    def perform_in_memory_edit_REPLACE_MULTI_AT_LINE(self, orig_prog_lines, edit):
        # AIDEV-NOTE: sed Ns/old/new/g — replace ALL occurrences on line N.
        if edit.starting_line is None:
            return self.perform_in_memory_edit_REPLACE_MULTI_PER_LINE(orig_prog_lines, edit)
        line_idx = edit.starting_line - 1
        if line_idx < 0 or line_idx >= len(orig_prog_lines):
            return _DEFAULT_RESULT
        target_line = orig_prog_lines[line_idx]
        before = _unescape_sed_replacement(edit.before)
        if before not in target_line:
            return _DEFAULT_RESULT
        new_line = target_line.replace(before, edit.after)
        prefix = "".join(orig_prog_lines[:line_idx])
        suffix = "".join(orig_prog_lines[line_idx + 1:])
        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=line_idx,
            end_line=line_idx + 1,
            new_content=prefix + new_line + suffix,
            match_type="replace_multi_at_line",
        )

    def perform_in_memory_edit_REPLACE_ONCE_PER_LINE(self, orig_prog_lines, edit):
        # AIDEV-NOTE: sed s/old/new/ — first occurrence per line, all lines.
        new_content, first = self._apply_first_per_line(
            orig_prog_lines, edit.before, edit.after,
        )
        if new_content is None:
            return _DEFAULT_RESULT
        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=first,
            end_line=first + 1,
            new_content=new_content,
            match_type="replace_once_per_line",
        )

    def perform_in_memory_edit_REPLACE_MULTI_PER_LINE(self, orig_prog_lines, edit):
        # AIDEV-NOTE: sed s/old/new/g — replace ALL occurrences per line.
        # Try literal first, fall back to BRE regex (count=0 for /g).
        before = _unescape_sed_replacement(edit.before)
        new_lines = [l.replace(before, edit.after) for l in orig_prog_lines]
        new_content = "".join(new_lines)
        if new_content == "".join(orig_prog_lines):
            pattern = _bre_to_ere(edit.before)
            replacement = _sed_repl_for_regex(edit.after)
            new_content, first_changed = self._apply_regex_per_line(
                orig_prog_lines, pattern, replacement, 0, None, count=0)
            if new_content is None:
                return _DEFAULT_RESULT
        else:
            first_changed = next(i for i, (a, b) in enumerate(zip(orig_prog_lines, new_lines)) if a != b)
        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=first_changed,
            end_line=first_changed + 1,
            new_content=new_content,
            match_type="replace_multi_per_line",
        )

    def perform_in_memory_edit_REPLACE_ONCE_PER_LINE_IN_RANGE(self, orig_prog_lines, edit):
        # AIDEV-NOTE: sed N,Ms/old/new/ — first occurrence per line, within a range.
        # Also handles /start/,/end/{ s/old/new/ } via pattern_address_start/end.
        if edit.pattern_address_start and edit.pattern_address_end:
            lo = hi = None
            for i, line in enumerate(orig_prog_lines):
                if lo is None and _is_pattern_in_line(edit.pattern_address_start, line):
                    lo = i
                elif lo is not None and _is_pattern_in_line(edit.pattern_address_end, line):
                    hi = i + 1
                    break
            if lo is None or hi is None:
                return _DEFAULT_RESULT
        else:
            lo = (edit.starting_line - 1) if edit.starting_line else 0
            hi = edit.ending_line if edit.ending_line else None
        new_content, first = self._apply_first_per_line(
            orig_prog_lines, edit.before, edit.after,
            start_idx=lo, end_idx=hi,
        )
        if new_content is None:
            return _DEFAULT_RESULT
        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=first,
            end_line=first + 1,
            new_content=new_content,
            match_type="replace_once_per_line_in_range",
        )

    def perform_in_memory_edit_REPLACE_MULTI_PER_LINE_IN_RANGE(self, orig_prog_lines, edit):
        # sed N,Ms/old/new/g — replace ALL occurrences per line, within range.
        if edit.pattern_address_start and edit.pattern_address_end:
            lo = hi = None
            for i, line in enumerate(orig_prog_lines):
                if lo is None and _is_pattern_in_line(edit.pattern_address_start, line):
                    lo = i
                elif lo is not None and _is_pattern_in_line(edit.pattern_address_end, line):
                    hi = i + 1
                    break
            if lo is None or hi is None:
                return _DEFAULT_RESULT
        else:
            lo = (edit.starting_line - 1) if edit.starting_line else 0
            hi = edit.ending_line if edit.ending_line else len(orig_prog_lines)

        before = _unescape_sed_replacement(edit.before)
        new_lines = list(orig_prog_lines)
        first_changed = None
        for i in range(lo, min(hi, len(new_lines))):
            replaced = new_lines[i].replace(before, edit.after)
            if replaced != new_lines[i]:
                new_lines[i] = replaced
                if first_changed is None:
                    first_changed = i

        if first_changed is None:
            pattern = _bre_to_ere(edit.before)
            replacement = _sed_repl_for_regex(edit.after)
            new_content, first_changed = self._apply_regex_per_line(
                orig_prog_lines, pattern, replacement, lo, hi, count=0)
            if new_content is None:
                return _DEFAULT_RESULT
        else:
            new_content = "".join(new_lines)

        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=first_changed,
            end_line=first_changed + 1,
            new_content=new_content,
            match_type="replace_multi_per_line_in_range",
        )

    def perform_in_memory_edit_REPLACE_ONCE_PER_LINE_PATTERN(self, orig_prog_lines, edit):
        # AIDEV-NOTE: sed /pattern/s/old/new/ — replace on ALL lines matching pattern.
        pattern = edit.pattern_address
        before = _unescape_sed_replacement(edit.before)
        modified = False
        new_lines = list(orig_prog_lines)
        for line_idx, line in enumerate(new_lines):
            if _is_pattern_in_line(pattern, line) and before in line:
                new_lines[line_idx] = line.replace(before, edit.after, 1)
                modified = True
        if not modified:
            return _DEFAULT_RESULT
        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=0,
            end_line=len(orig_prog_lines),
            new_content="".join(new_lines),
            match_type="replace_once_per_line_pattern",
        )

    def perform_in_memory_edit_REPLACE_JOINED_NEXT(self, orig_prog_lines, edit):
        """Handle ``sed -i 'N{N;s/old/new/}' file`` — join line N with next,
        run substitution on the combined text, replace both lines with result.

        Sed's N strips trailing newlines before joining with a single \\n
        separator. We replicate that: strip, join with \\n, substitute,
        then re-add \\n to each output line.
        """
        start_idx = edit.starting_line - 1
        end_idx = edit.ending_line
        if not (0 <= start_idx < start_idx + 1 < end_idx <= len(orig_prog_lines)):
            return _DEFAULT_RESULT
        raw_lines = [l.rstrip("\n") for l in orig_prog_lines[start_idx:end_idx]]
        joined = "\n".join(raw_lines)
        before = _unescape_sed_replacement(edit.before)
        if before in joined:
            new_text = joined.replace(before, edit.after, 1)
        else:
            try:
                pattern = _bre_to_ere(edit.before)
                replacement = _sed_repl_for_regex(edit.after)
                new_text = re.sub(pattern, replacement, joined, count=1)
            except re.error:
                return _DEFAULT_RESULT
        if new_text == joined:
            return _DEFAULT_RESULT
        result_lines = [l + "\n" for l in new_text.split("\n")]
        prefix = "".join(orig_prog_lines[:start_idx])
        suffix = "".join(orig_prog_lines[end_idx:])
        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=start_idx,
            end_line=end_idx,
            new_content=prefix + "".join(result_lines) + suffix,
            match_type="replace_joined_next",
        )

    # ── Delete Operations ──

    def perform_in_memory_edit_DELETE_LINE(self, orig_prog_lines, edit):
        idx = edit.starting_line - 1
        if not (0 <= idx < len(orig_prog_lines)):
            return _DEFAULT_RESULT
        if edit.pattern_address:
            line = orig_prog_lines[idx].rstrip("\n")
            py_pat = edit.pattern_address.replace("[[:space:]]", r"\s")
            if py_pat not in line and not re.search(py_pat, line):
                return _DEFAULT_RESULT
        prefix = "".join(orig_prog_lines[:idx])
        suffix = "".join(orig_prog_lines[idx + 1:])
        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=idx,
            end_line=idx + 1,
            new_content=prefix + suffix,
            match_type="delete_line_positional",
        )

    def perform_in_memory_edit_DELETE_RANGE(self, orig_prog_lines, edit):
        # AIDEV-NOTE: sed N,Md — positional delete, not content-based.
        start_idx = edit.starting_line - 1
        end_idx = edit.ending_line
        if not (0 <= start_idx < end_idx <= len(orig_prog_lines)):
            return _DEFAULT_RESULT
        assert edit.after == "", "edit.after should be empty string for EditType.DELETE_RANGE"
        prefix = "".join(orig_prog_lines[:start_idx])
        suffix = "".join(orig_prog_lines[end_idx:])
        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=start_idx,
            end_line=end_idx,
            new_content=prefix + suffix,
            match_type="delete_range_positional",
        )

    def perform_in_memory_edit_DELETE_PATTERN(self, orig_prog_lines, edit):
        pattern = edit.pattern_address
        if edit.pattern_address_start and edit.pattern_address_end:
            lo = hi = None
            for i, line in enumerate(orig_prog_lines):
                if lo is None and _is_pattern_in_line(edit.pattern_address_start, line):
                    lo = i
                elif lo is not None and _is_pattern_in_line(edit.pattern_address_end, line):
                    hi = i + 1
                    break
            if lo is None or hi is None:
                return _DEFAULT_RESULT
            new_lines = list(orig_prog_lines)
            to_remove = [i for i in range(lo, hi) if _is_pattern_in_line(pattern, new_lines[i])]
            if not to_remove:
                return _DEFAULT_RESULT
            for i in reversed(to_remove):
                del new_lines[i]
            first_changed = to_remove[0]
        else:
            new_lines = [l for l in orig_prog_lines if pattern not in l]
            if len(new_lines) == len(orig_prog_lines):
                return _DEFAULT_RESULT
            first_changed = next(i for i, l in enumerate(orig_prog_lines) if pattern in l)
        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=first_changed,
            end_line=first_changed + 1,
            new_content="".join(new_lines),
            match_type="delete_pattern",
        )

    def perform_in_memory_edit_DELETE_PATTERN_RANGE(self, orig_prog_lines, edit):
        """Handle ``sed -i '/pattern/,+Nd'`` or ``sed -i '/pat1/,/pat2/d'``."""
        pattern = edit.before
        # Two-pattern range: /pat1/,/pat2/d — sed checks end pattern from line AFTER start
        if edit.pattern_address_end:
            lo = hi = None
            for i, line in enumerate(orig_prog_lines):
                if lo is None and _is_pattern_in_line(pattern, line):
                    lo = i
                elif lo is not None and _is_pattern_in_line(edit.pattern_address_end, line):
                    hi = i + 1
                    break
            if lo is None:
                return _DEFAULT_RESULT
            if hi is None:
                hi = len(orig_prog_lines)
            new_lines = orig_prog_lines[:lo] + orig_prog_lines[hi:]
            return ReplaceResult(
                orig_prog_lines=orig_prog_lines,
                start_line=lo,
                end_line=hi,
                new_content="".join(new_lines),
                match_type="delete_pattern_range_two_pat",
            )
        # Existing: /pattern/,+Nd
        offset = edit.starting_line
        match_idx = None
        for i, line in enumerate(orig_prog_lines):
            if _is_pattern_in_line(pattern, line):
                match_idx = i
                break
        if match_idx is None:
            return _DEFAULT_RESULT
        end_idx = match_idx + offset + 1
        if end_idx > len(orig_prog_lines):
            return _DEFAULT_RESULT
        edit_copy = deepcopy(edit)
        edit_copy.before = "".join(orig_prog_lines[match_idx:end_idx])
        edit_copy.after = ""
        answer = perform_exact_match(orig_prog_lines, edit_copy)
        if answer.new_content is None:
            return _DEFAULT_RESULT
        return answer

    def perform_in_memory_edit_DELETE_PATTERN_NEXT(self, orig_prog_lines, edit):
        """Handle ``sed -i '/A/{n;/B/d}'`` or ``'/A/{N;/B/d}'``.

        Lowercase ``n``: delete only the next line.
        Uppercase ``N`` (``edit.delete_anchor=True``): delete both anchor and next.
        """
        anchor = edit.pattern_address
        target = edit.before
        if not anchor or not target:
            return _DEFAULT_RESULT
        new_lines = []
        first_changed = None
        skip_next = False
        for i, line in enumerate(orig_prog_lines):
            if skip_next:
                skip_next = False
                continue
            if anchor in line and i + 1 < len(orig_prog_lines) and target in orig_prog_lines[i + 1]:
                if not edit.delete_anchor:
                    new_lines.append(line)
                skip_next = True
                if first_changed is None:
                    first_changed = i
            else:
                new_lines.append(line)
        if first_changed is None:
            return _DEFAULT_RESULT
        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=first_changed,
            end_line=first_changed + 1,
            new_content="".join(new_lines),
            match_type="delete_pattern_next",
        )

    # ── Insert Operations ──

    def perform_in_memory_edit_INSERT_BEFORE_LINE(self, orig_prog_lines, edit):
        """Insert ``edit.after`` BEFORE line N (``edit.starting_line``)."""
        idx = edit.starting_line - 1
        if not (0 <= idx < len(orig_prog_lines)):
            return _DEFAULT_RESULT
        edit_copy = deepcopy(edit)
        context_end = min(len(orig_prog_lines), idx + 10)
        context_block = "".join(orig_prog_lines[idx:context_end])
        edit_copy.before = context_block
        sep = '' if edit.after.endswith('\n') else '\n'
        edit_copy.after = edit.after + sep + context_block
        return perform_exact_match(orig_prog_lines, edit_copy)

    def perform_in_memory_edit_INSERT_AFTER_LINE(self, orig_prog_lines, edit):
        """Insert ``edit.after`` at position ``starting_line`` (1-based).

        ``_sed_append_edit`` sets ``starting_line = N + 1`` for ``sed Na\\``,
        so the text lands between original lines N and N+1.
        """
        idx = edit.starting_line - 1
        if idx < 0:
            return _DEFAULT_RESULT
        # Clamp: if past end of file, append at end.
        idx = min(idx, len(orig_prog_lines))
        prefix = "".join(orig_prog_lines[:idx])
        suffix = "".join(orig_prog_lines[idx:])
        # AIDEV-NOTE: str_replace_editor inserts at EOF need leading separator, not trailing.
        append_eof = (edit.starting_line - 1) > len(orig_prog_lines)

        if edit.is_str_replace_cmd and append_eof:
            new_content = prefix + '\n' + edit.after
        else:
            new_content = prefix + edit.after + '\n' + suffix
        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=idx,
            end_line=idx,
            new_content=new_content,
            match_type="insert_after_line_positional",
        )

    def perform_in_memory_edit_INSERT_BEFORE_PATTERN(self, orig_prog_lines, edit):
        """Handle ``sed -i '/regex/i\\TEXT'`` — insert TEXT before ALL lines
        containing ``edit.pattern_address``."""
        pattern = edit.pattern_address
        if not pattern:
            return _DEFAULT_RESULT
        new_lines = []
        modified = False
        for line in orig_prog_lines:
            if _is_pattern_in_line(pattern, line):
                new_lines.append(edit.after + "\n")
                modified = True
            new_lines.append(line)
        if not modified:
            return _DEFAULT_RESULT
        first_changed = next(i for i, l in enumerate(orig_prog_lines) if _is_pattern_in_line(pattern, l))
        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=first_changed,
            end_line=first_changed + 1,
            new_content="".join(new_lines),
            match_type="insert_before_pattern",
        )

    def perform_in_memory_edit_INSERT_AFTER_PATTERN(self, orig_prog_lines, edit):
        """Handle ``sed -i '/regex/a\\TEXT'`` — insert TEXT after ALL lines
        containing ``edit.pattern_address``."""
        pattern = edit.pattern_address
        if not pattern:
            return _DEFAULT_RESULT
        new_lines = []
        modified = False
        for line in orig_prog_lines:
            new_lines.append(line)
            if _is_pattern_in_line(pattern, line):
                new_lines.append(edit.after + "\n")
                modified = True
        if not modified:
            return _DEFAULT_RESULT
        first_changed = next(i for i, l in enumerate(orig_prog_lines) if _is_pattern_in_line(pattern, l))
        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=first_changed,
            end_line=first_changed + 1,
            new_content="".join(new_lines),
            match_type="insert_after_pattern",
        )

    # ── Change Operations ──

    def perform_in_memory_edit_CHANGE_LINE(self, orig_prog_lines, edit):
        idx = edit.starting_line - 1
        if not (0 <= idx < len(orig_prog_lines)):
            return _DEFAULT_RESULT
        edit_copy = deepcopy(edit)
        edit_copy.before = orig_prog_lines[idx]
        # AIDEV-NOTE: empty after is valid — sed 'Nc\' replaces line with a blank line.
        if not edit_copy.after.endswith("\n"):
            edit_copy.after += "\n"
        answer = perform_exact_match(orig_prog_lines, edit_copy)
        assert answer.new_content is not None, "Exact Match failed for EditType.CHANGE_LINE"
        return answer

    def perform_in_memory_edit_CHANGE_RANGE(self, orig_prog_lines, edit):
        # AIDEV-NOTE: sed N,Mc\TEXT — replace entire range with text ONCE.
        start_idx = edit.starting_line - 1
        end_idx = edit.ending_line
        if not (0 <= start_idx < end_idx <= len(orig_prog_lines)):
            return _DEFAULT_RESULT
        new_text = edit.after
        # AIDEV-NOTE: empty after is valid — sed 'N,Mc\' replaces range with a blank line.
        new_text += "\n"
        prefix = "".join(orig_prog_lines[:start_idx])
        suffix = "".join(orig_prog_lines[end_idx:])
        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=start_idx,
            end_line=end_idx,
            new_content=prefix + new_text + suffix,
            match_type="change_range_positional",
        )

    def perform_in_memory_edit_CHANGE_RANGE_PER_LINE(self, orig_prog_lines, edit):
        # AIDEV-NOTE: sed N,Ms/.*/TEXT/ — replace each line in range independently.
        start_idx = edit.starting_line - 1
        end_idx = edit.ending_line
        if not (0 <= start_idx < end_idx <= len(orig_prog_lines)):
            return _DEFAULT_RESULT
        new_text = edit.after
        # AIDEV-NOTE: empty after is valid — sed 'N,Ms/.*//'' replaces each line with blank.
        new_text += "\n"
        replaced_block = new_text * (end_idx - start_idx)
        prefix = "".join(orig_prog_lines[:start_idx])
        suffix = "".join(orig_prog_lines[end_idx:])
        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=start_idx,
            end_line=end_idx,
            new_content=prefix + replaced_block + suffix,
            match_type="change_range_per_line",
        )

    def perform_in_memory_edit_CHANGE_PATTERN_RANGE(self, orig_prog_lines, edit):
        """Handle ``sed -i '/pat1/,/pat2/c\\text'`` — find all ranges, replace with text."""
        start_pat = edit.pattern_address_start
        end_pat = edit.pattern_address_end
        n_count = edit.n_count
        new_text = edit.after
        if not new_text.endswith("\n"):
            new_text += "\n"
        new_lines = list(orig_prog_lines)
        first_changed = None
        i = 0
        while i < len(new_lines):
            if _is_pattern_in_line(start_pat, new_lines[i]):
                start_idx = i
                end_idx = None
                if n_count:
                    end_idx = min(start_idx + n_count + 1, len(new_lines))
                elif end_pat:
                    for j in range(start_idx, len(new_lines)):
                        if _is_pattern_in_line(end_pat, new_lines[j]):
                            end_idx = j + 1
                            break
                if end_idx is None:
                    break
                new_lines[start_idx:end_idx] = [new_text]
                if first_changed is None:
                    first_changed = start_idx
                i = start_idx + 1
            else:
                i += 1
        if first_changed is None:
            return _DEFAULT_RESULT
        return ReplaceResult(
            orig_prog_lines=orig_prog_lines,
            start_line=first_changed,
            end_line=first_changed + 1,
            new_content="".join(new_lines),
            match_type="change_pattern_range",
        )

    # ── File-Level Operations ──

    def perform_in_memory_edit_WHOLE_FILE_ACTIONS(self, orig_prog_lines, edit, file_path: str = None):
        """Dispatch to REWRITE or CREATE based on whether the file exists."""
        if file_path is not None and not os.path.exists(file_path):
            return self.perform_in_memory_edit_WHOLE_FILE_CREATE([], edit)
        if not orig_prog_lines and file_path is not None:
            with open(file_path) as f:
                orig_prog_lines = f.readlines()
        return self.perform_in_memory_edit_WHOLE_FILE_REWRITE(orig_prog_lines, edit)

    def perform_in_memory_edit_WHOLE_FILE_REWRITE(self, orig_prog_lines, edit):
        return ReplaceResult(orig_prog_lines=orig_prog_lines, new_content=edit.after, start_line=0, end_line=len(orig_prog_lines), match_type="exact")

    def perform_in_memory_edit_WHOLE_FILE_CREATE(self, orig_prog_lines, edit):
        return ReplaceResult(orig_prog_lines=[], new_content=edit.after, start_line=0, end_line=0, match_type="exact")

    def perform_in_memory_edit_WHOLE_FILE_DELETE(self, orig_prog_lines):
        """Delete a file — return empty content with original lines for diffing."""
        return ReplaceResult(orig_prog_lines=orig_prog_lines, new_content="", start_line=0, end_line=len(orig_prog_lines), match_type="exact")

    # ── Private helpers for per-line sed replacement ──

    def _apply_regex_per_line(self, orig_prog_lines, pattern, replacement,
                              start_idx, end_idx, count=1):
        """Apply ``re.sub(pattern, replacement, line, count)`` per line in range."""
        end_idx = end_idx if end_idx is not None else len(orig_prog_lines)
        new_lines = list(orig_prog_lines)
        first_match = None
        try:
            for i in range(start_idx, min(end_idx, len(new_lines))):
                new_lines[i] = re.sub(pattern, replacement, new_lines[i], count=count)
                if first_match is None and new_lines[i] != orig_prog_lines[i]:
                    first_match = i
        except re.error:
            return None, None
        if first_match is None:
            return None, None
        return "".join(new_lines), first_match

    def _apply_first_per_line(
        self, orig_prog_lines, needle, new_str,
        start_idx: int = 0, end_idx: int | None = None,
    ):
        """Sed s/needle/new_str/ per line in range.

        ``needle`` is raw (escaped) from the parser. Tries literal match
        first; falls back to BRE regex if the unescaped text doesn't
        exist in any line.

        Returns ``(new_content, first_match_idx)`` or ``(None, None)``.
        """
        result = _strip_anchors(needle)
        if result is None:
            return None, None
        core, anchor_start, anchor_end = result

        unescaped_core = _unescape_sed_replacement(core)
        eff_end = end_idx if end_idx is not None else len(orig_prog_lines)
        literal_exists = any(
            unescaped_core in (line[:-1] if line.endswith("\n") else line)
            for i, line in enumerate(orig_prog_lines)
            if start_idx <= i < eff_end
        )
        if not literal_exists and not (anchor_start or anchor_end):
            pattern = _bre_to_ere(needle)
            replacement = _sed_repl_for_regex(new_str)
            return self._apply_regex_per_line(
                orig_prog_lines, pattern, replacement, start_idx, end_idx)

        core = unescaped_core

        end_idx = end_idx if end_idx is not None else len(orig_prog_lines)
        new_lines: list[str] = []
        first_match_line: int | None = None
        matched = False
        for i, line in enumerate(orig_prog_lines):
            if not (start_idx <= i < end_idx):
                new_lines.append(line)
                continue
            had_nl = line.endswith("\n")
            body = line[:-1] if had_nl else line
            replaced: str | None = None
            if anchor_start and anchor_end:
                if not core:
                    if body == "":
                        replaced = new_str
                elif body == core:
                    replaced = new_str
            elif anchor_end:
                if not core:
                    replaced = body + new_str
                elif body.endswith(core):
                    replaced = body[: -len(core)] + new_str
            elif anchor_start:
                if not core:
                    replaced = new_str + body
                elif body.startswith(core):
                    replaced = new_str + body[len(core):]
            else:
                if core in body:
                    replaced = body.replace(core, new_str, 1)
            if replaced is None:
                new_lines.append(line)
                continue
            if had_nl:
                replaced += "\n"
            new_lines.append(replaced)
            matched = True
            if first_match_line is None:
                first_match_line = i
        if not matched:
            return None, None
        return "".join(new_lines), first_match_line

    # ── Dispatcher ──

    def check_edit_application(
        self,
        edit: Edit,
        file_path: str,
        code_dir_lock: RWLock,
        file_content: Optional[str] = None
    ) -> ReplaceResult:
        """Check if an edit can be applied to code, without modifying the file.

        AIDEV-NOTE: Caller MUST hold the write lock before calling this function.
        """
        assert code_dir_lock.is_w_locked(), "check_edit_application() requires caller to hold write lock"

        # ── File-Level Operations (dispatch before file read — file may not exist) ──
        if edit.edit_type == EditType.WHOLE_FILE_ACTIONS:
            return self.perform_in_memory_edit_WHOLE_FILE_ACTIONS(
                orig_prog_lines=[], edit=edit, file_path=file_path)

        if edit.edit_type == EditType.COPY_FILE:
            return ReplaceResult(orig_prog_lines=[], new_content="", start_line=0, end_line=0, match_type="exact")

        # Read file content
        if file_content is None:
            with open(file_path) as f:
                orig_prog_lines = f.readlines()
        else:
            orig_prog_lines = file_content.splitlines(keepends=True)

        if edit.edit_type == EditType.WHOLE_FILE_DELETE:
            return self.perform_in_memory_edit_WHOLE_FILE_DELETE(orig_prog_lines=orig_prog_lines)

        # ── Replace Operations ──
        elif edit.edit_type == EditType.REPLACE:
            return self.perform_in_memory_edit_REPLACE(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.REPLACE_PATCH_HUNK:
            return self.perform_in_memory_edit_REPLACE_PATCH_HUNK(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.REPLACE_ONCE_AT_LINE:
            return self.perform_in_memory_edit_REPLACE_ONCE_AT_LINE(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.REPLACE_MULTI_AT_LINE:
            return self.perform_in_memory_edit_REPLACE_MULTI_AT_LINE(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.REPLACE_ONCE_PER_LINE:
            return self.perform_in_memory_edit_REPLACE_ONCE_PER_LINE(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.REPLACE_MULTI_PER_LINE:
            return self.perform_in_memory_edit_REPLACE_MULTI_PER_LINE(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.REPLACE_ONCE_PER_LINE_IN_RANGE:
            return self.perform_in_memory_edit_REPLACE_ONCE_PER_LINE_IN_RANGE(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.REPLACE_MULTI_PER_LINE_IN_RANGE:
            return self.perform_in_memory_edit_REPLACE_MULTI_PER_LINE_IN_RANGE(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.REPLACE_ONCE_PER_LINE_PATTERN:
            return self.perform_in_memory_edit_REPLACE_ONCE_PER_LINE_PATTERN(orig_prog_lines=orig_prog_lines, edit=edit)

        # ── Delete Operations ──
        elif edit.edit_type == EditType.DELETE_LINE:
            return self.perform_in_memory_edit_DELETE_LINE(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.DELETE_RANGE:
            return self.perform_in_memory_edit_DELETE_RANGE(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.DELETE_PATTERN:
            return self.perform_in_memory_edit_DELETE_PATTERN(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.DELETE_PATTERN_RANGE:
            return self.perform_in_memory_edit_DELETE_PATTERN_RANGE(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.DELETE_PATTERN_NEXT:
            return self.perform_in_memory_edit_DELETE_PATTERN_NEXT(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.REPLACE_JOINED_NEXT:
            return self.perform_in_memory_edit_REPLACE_JOINED_NEXT(orig_prog_lines=orig_prog_lines, edit=edit)

        # ── Insert Operations ──
        elif edit.edit_type == EditType.INSERT_BEFORE_LINE:
            return self.perform_in_memory_edit_INSERT_BEFORE_LINE(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.INSERT_AFTER_LINE:
            return self.perform_in_memory_edit_INSERT_AFTER_LINE(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.INSERT_BEFORE_PATTERN:
            return self.perform_in_memory_edit_INSERT_BEFORE_PATTERN(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.INSERT_AFTER_PATTERN:
            return self.perform_in_memory_edit_INSERT_AFTER_PATTERN(orig_prog_lines=orig_prog_lines, edit=edit)

        # ── Change Operations ──
        elif edit.edit_type == EditType.CHANGE_LINE:
            return self.perform_in_memory_edit_CHANGE_LINE(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.CHANGE_RANGE:
            return self.perform_in_memory_edit_CHANGE_RANGE(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.CHANGE_RANGE_PER_LINE:
            return self.perform_in_memory_edit_CHANGE_RANGE_PER_LINE(orig_prog_lines=orig_prog_lines, edit=edit)

        elif edit.edit_type == EditType.CHANGE_PATTERN_RANGE:
            return self.perform_in_memory_edit_CHANGE_PATTERN_RANGE(orig_prog_lines=orig_prog_lines, edit=edit)

        else:
            raise NotImplementedError(f"Unsupported EditType: {edit.edit_type}")

    def apply_edit_to_linux_code(
        self,
        edit: Edit,
        file_path: str,
        code_dir_lock: RWLock
    ) -> Optional[Dict[str, str]]:
        """
        Apply one Edit to a file.

        This function reads the file, tries to match the before string
        (after stripping spaces to improve matching), and replaces the
        matched region with the after string.

        AIDEV-NOTE: Caller MUST hold the write lock before calling this function.

        Args:
            edit: The Edit object to apply
            file_path: Path to the file to modify
            code_dir_lock: RWLock that must be held by caller

        Returns:
            Dict with 'file_path' and 'file_content' if successful; None otherwise
        """
        # AIDEV-NOTE: Assert lock is held - caller must hold the lock
        assert code_dir_lock.is_w_locked(), "apply_edit_to_linux_code() requires caller to hold write lock"

        result = self.check_edit_application(edit, file_path, code_dir_lock)
        match_start = result.start_line
        match_end = result.end_line
        new_content = result.new_content

        if (match_start is None) or (match_end is None):
            return None

        if edit.edit_type == EditType.COPY_FILE:
            code_dir = file_path[: file_path.rfind(edit.filename)]
            source_path = os.path.join(code_dir, edit.before)
            if os.path.exists(source_path):
                # AIDEV-NOTE: Agent may target a path whose parent dir
                # doesn't exist (e.g. SWE-bench `create /testbed/newpkg/x.py`).
                # mkdir -p before write; harmless for kernel paths whose
                # parent always exists. [AI-M1.B]
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                shutil.copy2(source_path, file_path)
            content = open(file_path).read() if os.path.exists(file_path) else ""
            return {"file_path": file_path, "file_content": content}

        if edit.edit_type == EditType.WHOLE_FILE_DELETE:
            if os.path.exists(file_path):
                os.remove(file_path)
            return {"file_path": file_path, "file_content": ""}

        # AIDEV-NOTE: see COPY_FILE branch above. [AI-M1.B]
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(new_content)
        return {"file_path": file_path, "file_content": new_content}


# Module-level singleton for convenience
_default_applier = EditApplier()


def check_edit_application(
    edit: Edit,
    file_path: str,
    code_dir_lock: RWLock,
    file_content: Optional[str] = None
) -> tuple[Optional[List[str]], Optional[int], Optional[int], Optional[str]]:
    """
    Module-level function for checking edit application.

    See EditApplier.check_edit_application() for details.
    """
    return _default_applier.check_edit_application(
        edit, file_path, code_dir_lock, file_content
    )


def apply_edit_to_linux_code(
    edit: Edit,
    file_path: str,
    code_dir_lock: RWLock
) -> Optional[Dict[str, str]]:
    """
    Module-level function for applying edits to code.

    See EditApplier.apply_edit_to_linux_code() for details.
    """
    return _default_applier.apply_edit_to_linux_code(edit, file_path, code_dir_lock)
