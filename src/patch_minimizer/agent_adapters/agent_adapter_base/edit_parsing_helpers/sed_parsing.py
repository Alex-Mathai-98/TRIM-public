"""Shared ``sed -i`` → ``Edit`` parsing functions.

Extracted from ``mini_swe_agent_adapter/bash_edit_extractor.py`` so that
multiple agent adapters (mini-swe, classic SWE-agent) can reuse sed parsing.

Public entry point: ``sed_edits(bash_block, explanation_prefix)``.
"""
from __future__ import annotations

import logging
import re
import shlex

from patch_minimizer.core.edit import Edit, EditType
# AIDEV-NOTE: lives in core so core/edit_applier.py needn't import from agent_adapters (import cycle).
from patch_minimizer.core.utils import unescape_sed_replacement as _unescape_sed_replacement
from patch_minimizer.agent_adapters.agent_adapter_base.utils import (
    is_linux_tree_path,
    normalize_linux_repo_path,
)

logger = logging.getLogger(__name__)


_SED_INPLACE_RE = re.compile(r"\bsed\s+(?:.*\s)?-i")


def is_sed_command(text: str) -> bool:
    """Return ``True`` if ``text`` contains a ``sed -i`` invocation."""
    return bool(_SED_INPLACE_RE.search(text))

# ---------------------------------------------------------------------------
# bashlex with graceful fallback
# ---------------------------------------------------------------------------
try:
    import bashlex
    _HAS_BASHLEX = True
except ImportError:
    _HAS_BASHLEX = False


# ---------------------------------------------------------------------------
# bashlex-based command tokenizer
# ---------------------------------------------------------------------------

_DQ_NL_ESCAPE_RE = re.compile(r'"[^"]*\\[nt][^"]*"')


def _tokenize_command(cmd: str) -> list[str] | None:
    """Tokenize a single simple command into its words.

    Returns ``None`` when the command is too complex for word-level tokenization.

    AIDEV-NOTE: bashlex hangs on heredoc syntax (``<<``), so we skip it
    for any input containing heredoc operators.

    AIDEV-NOTE: bashlex also drops ``\\n`` / ``\\t`` backslashes from inside
    double-quoted strings (bash POSIX rules say preserve them since
    ``n`` / ``t`` aren't special escape chars). That turns sed
    ``"s/old/new\\n\\tcont/"`` into ``s/old/newntcont/`` and the inserted
    newline/tab is lost. Skip bashlex when we see that shape and let
    shlex handle the unquoting — shlex follows the POSIX rules correctly.
    """
    # AIDEV-NOTE: ``<<`` inside a sed pattern (e.g. C bitshift ``nr_pages << PAGE_SHIFT``)
    # is not a heredoc — only skip bashlex when ``<<`` appears in a non-sed command.
    has_heredoc = "<<" in cmd and not is_sed_command(cmd)
    has_dq_nl_escape = bool(_DQ_NL_ESCAPE_RE.search(cmd))
    # AIDEV-NOTE: two shell idioms for embedding a single-quote inside a
    # single-quoted string — bashlex preserves them literally, shlex unquotes.
    has_sq_escape = "'\"'\"'" in cmd or "'\\''" in cmd
    if _HAS_BASHLEX and not has_heredoc and not has_dq_nl_escape and not has_sq_escape:
        try:
            parts = bashlex.parse(cmd)
            words: list[str] = []
            for node in parts:
                if node.kind == "command":
                    for child in node.parts:
                        if child.kind == "word":
                            words.append(child.word)
            if words:
                return words
        except Exception:
            pass
    if not has_heredoc:
        try:
            return shlex.split(cmd)
        except ValueError:
            pass
    return None


def _sed_fallback_tokenize(block: str) -> list[str]:
    """Best-effort tokenization for sed commands that bashlex/shlex reject.

    Handles multi-line ``sed -i '...' file`` where the single-quoted
    argument spans multiple lines.
    """
    block = block.strip()
    tokens: list[str] = []
    i = 0
    while i < len(block):
        if block[i] in " \t\n\r":
            i += 1
            continue
        if block[i] == "'":
            j = block.index("'", i + 1) if "'" in block[i + 1:] else len(block)
            tokens.append(block[i + 1: j])
            i = j + 1
            continue
        if block[i] == '"':
            j = i + 1
            while j < len(block):
                if block[j] == '"' and block[j - 1] != "\\":
                    break
                j += 1
            tokens.append(block[i + 1: j])
            i = j + 1
            continue
        j = i
        while j < len(block) and block[j] not in " \t\n'\"":
            j += 1
        if j == i:
            i += 1
            continue
        tokens.append(block[i:j])
        i = j
    return tokens


# ---------------------------------------------------------------------------
# sed expression parser
# ---------------------------------------------------------------------------


def _split_sed_expression(expression: str) -> list[str]:
    """Split a sed expression on top-level ``;`` separators.

    Implements a small structural scanner so embedded ``;`` characters that
    are *part of* an address or command body are not treated as separators.
    Specifically aware of:

    * Regex addresses ``/.../`` (and the second range half ``,/.../``) —
      consumed opaquely so ``;`` inside the regex is preserved.
    * Substitution / transliteration ``s<delim>...<delim>...<delim>[flags]``
      and ``y<delim>...<delim>...<delim>`` — delimiter triple consumed
      opaquely so cases like ``s/A/B/g;1d`` split correctly into
      ``['s/A/B/g', '1d']``.
    * Brace blocks ``{ ... }`` — inner ``;`` belongs to the group, not the
      outer expression. Brace depth is tracked so nested groups work.

    AIDEV-NOTE: callers further validate each fragment; whole-expression
    fallback handles anything we still cannot split. Older naive ``;``
    splitting mangled ``/A;B/d``, ``/A/{n;/B/d}``, and ``s/A/B/g;1d``.
    """
    if ";" not in expression:
        return [expression]

    parts: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(expression)
    brace_depth = 0

    def _consume_slash_address(start: int) -> int:
        """Consume ``/.../`` starting at ``start`` (which points at the
        opening ``/``). Returns index just past the closing ``/``."""
        j = start + 1
        while j < n:
            ch = expression[j]
            if ch == "\\" and j + 1 < n:
                j += 2
                continue
            if ch == "/":
                return j + 1
            j += 1
        return j

    def _consume_subst(start: int) -> int:
        """Consume ``s<delim>...<delim>...<delim>[flags]`` (or ``y...``)
        starting at the command character. Returns index just past the
        flags. Flags are alphanumeric chars that follow."""
        cmd_idx = start
        delim_idx = cmd_idx + 1
        if delim_idx >= n:
            return n
        delim = expression[delim_idx]
        j = delim_idx + 1
        delims_seen = 0  # need 2 more (the closing of OLD and of NEW)
        while j < n and delims_seen < 2:
            ch = expression[j]
            if ch == "\\" and j + 1 < n:
                j += 2
                continue
            if ch == delim:
                delims_seen += 1
            j += 1
        # consume trailing flag chars (alphanumeric)
        while j < n and expression[j].isalnum():
            j += 1
        return j

    while i < n:
        c = expression[i]

        # Pass through escape sequences as a single unit.
        if c == "\\" and i + 1 < n:
            buf.append(c)
            buf.append(expression[i + 1])
            i += 2
            continue

        # Inside a brace block, ``;`` is an inner separator — keep it.
        if brace_depth > 0:
            buf.append(c)
            if c == "{":
                brace_depth += 1
            elif c == "}":
                brace_depth -= 1
            i += 1
            continue

        # Top-level ``;`` separates expressions.
        if c == ";":
            # AIDEV-NOTE: lstrip-only — trailing whitespace (especially ``\n``)
            # is meaningful for ``a\``/``i\``/``c\`` text where ``\<NL>`` at
            # the end of the expression body means "append text terminated
            # by a newline" (i.e. produces a trailing blank line in the
            # inserted block). Stripping it dropped the trailing blank line
            # for hundreds of agent ``sed Na\TEXT\n`` invocations.
            piece = "".join(buf).lstrip()
            if piece:
                parts.append(piece)
            buf = []
            i += 1
            continue

        # Regex address ``/.../`` — opaque consume.
        if c == "/":
            end = _consume_slash_address(i)
            buf.append(expression[i:end])
            i = end
            continue

        # ``s<delim>...<delim>...<delim>[flags]`` / ``y...`` — opaque
        # consume so the inner delimiters don't masquerade as addresses
        # and the flag-region ``;`` (e.g. ``s/A/B/g;1d``) stays a real
        # top-level separator.
        if c in ("s", "y") and i + 1 < n and not expression[i + 1].isalnum():
            end = _consume_subst(i)
            buf.append(expression[i:end])
            i = end
            continue

        # ``{`` opens a brace block — track depth so inner ``;`` is kept.
        if c == "{":
            brace_depth = 1
            buf.append(c)
            i += 1
            continue

        buf.append(c)
        i += 1

    tail = "".join(buf).lstrip()  # preserve trailing \n for a\/i\/c\ text
    if tail:
        parts.append(tail)
    return parts or [expression]


# AIDEV-NOTE: Recognized brace-block forms. Today only ``/A/{n;/B/d}`` is
# supported (delete the line after PATTERN_A iff that line matches PATTERN_B).
# Other group forms (``{N;d}``, ``{p;d}``, etc.) fall through to the existing
# single-expression parser, which will return ``None`` and skip the edit.
_SED_NEXT_DELETE_RE = re.compile(
    r"^\{\s*(?P<ncmd>[nN])\s*;\s*/(?P<target>.+?)/\s*d\s*\}\s*$"
)
_SED_CONDITIONAL_DELETE_RE = re.compile(
    r"^\{\s*/(?P<pattern>.+?)/\s*d\s*\}\s*$"
)
_SED_NEXT_SUBST_RE = re.compile(
    r"^\{\s*N\s*;\s*s(?P<delim>.)(?P<old>.*?)(?P=delim)"
    r"(?P<new>.*?)(?P=delim)(?P<flags>[g]?)\s*\}\s*$"
)
# AIDEV-NOTE: ``N,M{ Ns/.*/A/; Ms/.*/B/ }`` — per-line whole-line replacement
# inside a brace block. Emits a single CHANGE_RANGE edit.
_SED_BRACE_PERLINE_SUBST_RE = re.compile(
    r"^\{\s*(?P<body>(?:\d+s/\.\*/.*?/\s*;\s*)*\d+s/\.\*/.*?/)\s*\}\s*$",
    re.DOTALL,
)


def _parse_brace_n_aic(rest: str, addr: str | None) -> dict | None:
    """Parse ``{N[;N]*; c\\text}`` — one or more N + a/i/c command."""
    inner = rest.strip().removeprefix("{").removesuffix("}").strip()
    if not inner:
        return None
    n_count = 0
    pos = 0
    while pos < len(inner):
        while pos < len(inner) and inner[pos] in " \t\n;":
            pos += 1
        if pos < len(inner) and inner[pos] == "N":
            n_count += 1
            pos += 1
        else:
            break
    if n_count == 0:
        return None
    remaining = inner[pos:].lstrip(" \t\n;")
    if not remaining or remaining[0] not in ("a", "i", "c"):
        return None
    parsed = _parse_sed_aic(remaining, addr)
    if parsed:
        parsed["n_count"] = n_count
    return parsed


def _parse_sed_expression(expression: str) -> dict | None:
    """Parse a sed expression string into its components.

    Returns a dict with keys depending on the subcommand:
    - ``'type'``: one of ``'s'``, ``'a'``, ``'i'``, ``'d'``, ``'c'``,
      ``'next_delete'`` (brace-block ``/A/{n;/B/d}``)
    - ``'address'``: optional address prefix (line number or pattern)
    - ``'old'``/``'new'``: for substitutions
    - ``'text'``: for append/insert/change
    - ``'flags'``: for substitution flags (``g``, etc.)
    - ``'target'``: for ``next_delete`` — the inner ``/B/`` pattern
    """
    # AIDEV-NOTE: lstrip-only — preserve trailing ``\n`` for a\/i\/c\ text
    # whose body ends with ``\<NL>`` to produce a trailing blank line.
    expr = expression.lstrip()
    if not expr:
        return None

    addr, rest = _split_address(expr)
    rest = rest.lstrip()

    if rest and rest[0] == "s":
        return _parse_sed_substitution(rest, addr)
    if rest and rest[0] in ("a", "i", "c"):
        return _parse_sed_aic(rest, addr)
    if rest and rest[0] == "d":
        return {"type": "d", "address": addr}
    if rest and rest[0] == "{" and addr:
        # AIDEV-NOTE: {N+;c\text} must be parsed before normalization (c\ text has real newlines)
        nc_parsed = _parse_brace_n_aic(rest, addr)
        if nc_parsed:
            return nc_parsed
        # AIDEV-NOTE: normalize multi-line brace blocks to single-line
        if "\n" in rest:
            rest = re.sub(r"\s*\n\s*", ";", rest).replace("{;", "{").replace(";}", "}")
        m = _SED_NEXT_DELETE_RE.match(rest)
        if m and addr.startswith("/") and addr.endswith("/"):
            return {
                "type": "next_delete",
                "address": addr,
                "target": m.group("target"),
                "delete_anchor": m.group("ncmd") == "N",
            }
        m = _SED_NEXT_SUBST_RE.match(rest)
        if m and re.fullmatch(r"\d+(?:,\d+)?", addr):
            return {
                "type": "next_subst",
                "address": addr,
                "old": m.group("old"),
                "new": m.group("new"),
                "flags": m.group("flags"),
            }
        m = _SED_CONDITIONAL_DELETE_RE.match(rest)
        if m and addr.isdigit():
            return {
                "type": "conditional_delete",
                "address": addr,
                "pattern": m.group("pattern"),
            }
        m = _SED_BRACE_PERLINE_SUBST_RE.match(rest)
        if m and "," in addr:
            return {
                "type": "brace_change",
                "address": addr,
                "body": m.group("body"),
            }
        # /start/,/end/{ s/old/new/ } — unwrap braces, delegate to substitution parser
        inner = rest.strip().removeprefix("{").removesuffix("}").strip()
        if inner and inner[0] == "s":
            parsed = _parse_sed_substitution(inner, addr)
            if parsed:
                return parsed
        # /start/,/end/{ /pattern/d } — unwrap braces, delegate to delete parser
        if inner and inner[0] == "/":
            inner_parsed = _parse_sed_expression(inner)
            if inner_parsed and inner_parsed["type"] == "d":
                inner_parsed["range_address"] = addr
                return inner_parsed
    return None


def _split_address(expr: str) -> tuple[str | None, str]:
    """Split a sed expression into (address, command+args)."""
    if not expr:
        return None, expr

    m = re.match(r"^(\d+(?:,\d+)?)\s*", expr)
    if m:
        return m.group(1), expr[m.end():]

    if expr.startswith("/"):
        i = 1
        while i < len(expr):
            if expr[i] == "/" and (i == 0 or expr[i - 1] != "\\"):
                rest = expr[i + 1:]
                if rest.startswith(",/"):
                    j = rest.index("/", 2) + 1 if "/" in rest[2:] else len(rest)
                    return expr[: i + 1 + j], rest[j:]
                # /regex/,+N — pattern + relative offset
                m = re.match(r",\+(\d+)", rest)
                if m:
                    return expr[: i + 1 + m.end()], rest[m.end():]
                return expr[: i + 1], rest
            i += 1

    return None, expr


def _parse_sed_substitution(rest: str, addr: str | None) -> dict | None:
    if len(rest) < 2 or rest[0] != "s":
        return None
    delim = rest[1]
    parts = _split_sed_delimited(rest[2:], delim)
    if parts is None or len(parts) < 2:
        return None
    old_pat, new_pat = parts[0], parts[1]
    flags = parts[2] if len(parts) > 2 else ""
    return {
        "type": "s",
        "address": addr,
        "old": old_pat,
        "new": new_pat,
        "flags": flags,
    }


def _split_sed_delimited(s: str, delim: str) -> list[str] | None:
    """Split a sed expression at unescaped ``delim`` characters."""
    parts: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            if s[i + 1] == delim:
                current.append(delim)
            else:
                current.append(s[i: i + 2])
            i += 2
            continue
        if s[i] == delim:
            parts.append("".join(current))
            current = []
            if len(parts) >= 3:
                break
            i += 1
            continue
        current.append(s[i])
        i += 1
    if current or len(parts) < 2:
        parts.append("".join(current))
    return parts if len(parts) >= 2 else None


def _aic_text_unescape(text: str) -> str:
    """Single-pass GNU-sed escape processor for ``a\\``/``i\\``/``c\\`` text.

    Rules (matching GNU sed's compile_text for append/insert/change text):
    - ``\\\\`` → literal ``\\``
    - ``\\n``  → real newline (line separator in single-line bash form)
    - ``\\t``  → real tab
    - ``\\<X>`` for any other ``X`` (incl. real-tab, real-newline) → just
      ``X`` — the backslash is silently dropped. This is the rule that
      handles the dominant residual signature: agents writing
      ``a\\<NL>\\<TAB>code…`` where the leading ``\\`` on each
      continuation line is sed's mark-as-text escape and must be stripped.

    Run AFTER stripping the command's ``\\<newline>`` separator (existing
    ``_parse_sed_aic`` logic). Replaces the previous split-on-``\\n`` plus
    later ``_unescape_sed_replacement`` two-pass design, which mishandled
    ``\\<TAB>`` (left a literal ``\\`` in the output) and ``\\\\n``
    (incorrectly split inside C string literals).
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            if i + 1 >= n:
                # Trailing lone ``\\`` — sed's line-continuation marker with
                # nothing following (the real newline gets stripped earlier
                # by ``_split_sed_expression``). Drop it.
                i += 1
                continue
            nxt = text[i + 1]
            if nxt == "\\":
                out.append("\\")
                i += 2
            elif nxt == "n":
                out.append("\n")
                i += 2
            elif nxt == "t":
                out.append("\t")
                i += 2
            else:
                out.append(nxt)
                i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _parse_sed_aic(rest: str, addr: str | None) -> dict | None:
    """Parse ``a\\text``, ``i\\text``, ``c\\text`` commands.

    Returns the text field already fully unescaped — callers (the per-command
    edit builders) must NOT re-run ``_unescape_sed_replacement`` on it.
    """
    if not rest:
        return None
    cmd_type = rest[0]
    text_part = rest[1:]
    text_part = text_part.lstrip(" ")
    if text_part.startswith("\\"):
        text_part = text_part[1:]
    text_part = text_part.lstrip("\n")
    # AIDEV-NOTE: strip trailing bare newline (shell line break before closing ')
    # but keep \<NL> (continuation marker that produces a real newline in sed).
    if text_part.endswith("\n") and not text_part.endswith("\\\n"):
        text_part = text_part[:-1]
    text = _aic_text_unescape(text_part)
    return {"type": cmd_type, "address": addr, "text": text}


def _extract_sed_expression_and_file(tokens: list[str]) -> tuple[str, str] | None:
    """From tokenized ``sed -i <expr> <file>``, extract (expression, filepath)."""
    i = 0
    if i < len(tokens) and tokens[i] == "sed":
        i += 1
    while i < len(tokens) and tokens[i].startswith("-"):
        flag = tokens[i]
        if flag == "-i" or flag.startswith("-i"):
            if flag == "-i":
                if i + 1 < len(tokens) and (
                    tokens[i + 1] == "''" or tokens[i + 1].startswith(".")
                ):
                    i += 2
                else:
                    i += 1
            else:
                i += 1
        else:
            i += 1
    if i >= len(tokens):
        return None
    expression = tokens[i]
    i += 1
    if i >= len(tokens):
        return None
    filepath = tokens[i]
    return expression, filepath


def _find_sed_commands_bashlex(bash_block: str) -> list[str] | None:
    """Use bashlex to extract ``sed`` commands from compound statements.

    Returns ``None`` when bashlex is unavailable or the parse fails,
    signalling the caller to fall back to the manual splitter.
    """
    if not _HAS_BASHLEX:
        return None
    # AIDEV-NOTE: bashlex hangs on heredoc syntax — but ``<<`` inside a sed
    # pattern (e.g. C bitshift) is not a heredoc, so skip the guard when the
    # block contains a sed command.
    if "<<" in bash_block and not is_sed_command(bash_block):
        return None
    try:
        parts = bashlex.parse(bash_block)
    except Exception:
        return None

    def _walk(node):
        results = []
        if node.kind == "command":
            words = [p.word for p in node.parts if p.kind == "word"]
            if words and words[0] == "sed":
                results.append(bash_block[node.pos[0]:node.pos[1]])
        for attr in ("parts", "list"):
            for child in getattr(node, attr, []) or []:
                results.extend(_walk(child))
        return results

    commands = []
    for p in parts:
        commands.extend(_walk(p))
    return commands


def _extract_sed_commands_manual(bash_block: str) -> list[str]:
    """Line-based fallback: split a bash block into ``sed -i`` command strings."""
    commands: list[str] = []
    lines = bash_block.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        if re.match(r"^sed\s+", stripped) and "-i" in stripped:
            cmd_lines = [stripped]
            open_quotes = stripped.count("'") % 2
            while open_quotes and i + 1 < len(lines):
                i += 1
                cmd_lines.append(lines[i])
                open_quotes = (open_quotes + lines[i].count("'")) % 2
            commands.append("\n".join(cmd_lines))
        i += 1
    return commands


def _extract_sed_commands(bash_block: str) -> list[str]:
    """Split a bash block into individual ``sed -i`` command strings.

    Tries bashlex first (handles compound ``&&`` / ``;`` / ``||`` statements),
    falls back to manual line-based splitting.
    """
    result = _find_sed_commands_bashlex(bash_block)
    if result is not None:
        return result
    return _extract_sed_commands_manual(bash_block)





# ---------------------------------------------------------------------------
# Per-subcommand Edit builders
# ---------------------------------------------------------------------------

def _sed_substitution_edit(parsed: dict, filename: str, explanation_prefix: str, n: int) -> Edit | None:
    # AIDEV-NOTE: keep old_text raw (escaped) — applier uses escapes to distinguish
    # regex metacharacters from literal chars (e.g. `\.` vs `.`).
    old_text = parsed["old"]
    new_text = _unescape_sed_replacement(parsed["new"])
    if _unescape_sed_replacement(old_text) == new_text:
        return None
    addr = parsed.get("address")
    # /start/,/end/s/old/new/ — pattern-delimited range substitution (check before single-pattern)
    pat_range_m = re.match(r"^/(.+)/,/(.+)/$", addr or "")
    if pat_range_m:
        start_pat = pat_range_m.group(1)
        end_pat = pat_range_m.group(2)
        return Edit(
            filename=filename,
            before=old_text,
            after=new_text,
            pattern_address_start=start_pat,
            pattern_address_end=end_pat,
            explanation=f"{explanation_prefix} sed /pat/,/pat/ s #{n}",
            best_effort=True,
            edit_type=EditType.REPLACE_ONCE_PER_LINE_IN_RANGE,
        )
    # /pattern/s/old/new/ — restrict to lines matching pattern
    if addr and addr.startswith("/") and addr.endswith("/"):
        pattern = addr[1:-1]
        return Edit(
            filename=filename,
            before=old_text,
            after=new_text,
            pattern_address=pattern,
            explanation=f"{explanation_prefix} sed /pattern/ s #{n}",
            best_effort=True,
            edit_type=EditType.REPLACE_ONCE_PER_LINE_PATTERN,
        )
    # AIDEV-NOTE: ``N,Ms/.*/REPL/`` replaces lines N..M (inclusive) with one
    # block — agents use this heavily. Mapping to RANGE_CHANGE avoids bogus
    # global REPLACE with ``before='.*'`` (literal match disaster). When the
    # replacement is empty (``N,Ms/.*//``), route to CHANGE_RANGE_PER_LINE
    # (not DELETE_RANGE) because real sed empties lines, not removes them.
    is_global = "g" in parsed.get("flags", "")
    range_m = re.fullmatch(r"(\d+),(\d+)", addr or "")
    # AIDEV-NOTE: ``N,Ms/.*/x`` with ``g`` is still one match/line for ``.*`` —
    # map to RANGE_CHANGE (do not downgrade to LINE_REPLACE_GLOBAL).
    if range_m:
        lo, hi = int(range_m.group(1)), int(range_m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        whole_line_old = old_text in (".*", "^.*$", ".")
        if whole_line_old:
            if new_text == "":
                return Edit(
                    filename=filename,
                    before="",
                    after="",
                    starting_line=lo,
                    ending_line=hi,
                    explanation=f"{explanation_prefix} sed N,M s/.*// # {n} (range empty)",
                    best_effort=True,
                    edit_type=EditType.CHANGE_RANGE_PER_LINE,
                )
            return Edit(
                filename=filename,
                before="",
                after=new_text,
                starting_line=lo,
                ending_line=hi,
                explanation=f"{explanation_prefix} sed N,M s # {n} (range per-line)",
                best_effort=True,
                edit_type=EditType.CHANGE_RANGE_PER_LINE,
            )
        # AIDEV-NOTE: Non-whole-line range sub (e.g. 391,394s/^/\t/) —
        # preserve range so applier can restrict to targeted lines.
        edit_type_val = EditType.REPLACE_MULTI_PER_LINE_IN_RANGE if is_global else EditType.REPLACE_ONCE_PER_LINE_IN_RANGE
        return Edit(
            filename=filename,
            before=old_text,
            after=new_text,
            starting_line=lo,
            ending_line=hi,
            explanation=f"{explanation_prefix} sed N,M s #{n} (range sub)",
            best_effort=True,
            edit_type=edit_type_val,
        )
    # AIDEV-NOTE: ``Ns/.*/REPL/`` (single line) — LINE_REPLACE used substring
    # ``before='.*'`` which never matches; use CHANGE (full line replace) like ``Nc``.
    # When ``REPL`` is empty (``Ns/.*//``), route to DELETE for the same reason
    # the range case routes to RANGE_DELETE.
    if addr and re.fullmatch(r"\d+", addr):
        line_one = int(addr)
        whole_line_old = old_text in (".*", "^.*$", ".")
        if whole_line_old:
            if new_text == "":
                return Edit(
                    filename=filename,
                    before="",
                    after="",
                    starting_line=line_one,
                    explanation=f"{explanation_prefix} sed N s/.*// # {n} (whole line delete)",
                    best_effort=True,
                    edit_type=EditType.DELETE_LINE,
                )
            return Edit(
                filename=filename,
                before="",
                after=new_text,
                starting_line=line_one,
                explanation=f"{explanation_prefix} sed N s # {n} (whole line)",
                best_effort=True,
                edit_type=EditType.CHANGE_LINE,
            )
    line_num = None
    ending_line = None
    if addr and addr.isdigit():
        line_num = int(addr)
    # AIDEV-NOTE: sed g flag replaces ALL occurrences, not just the first.
    # For unaddressed forms (no line number), sed iterates lines and replaces
    # first-per-line by default — that's SED_REPLACE, which matches
    # real-sed semantics. Multi-line ``old`` patterns (containing real ``\n``)
    # are dropped entirely: sed's default pattern space is one line, so
    # ``s/A\nB/C/`` never matches in real sed (rc=0 but no change). We used
    # to emit EditType.REPLACE here, which DID match across lines — that
    # produced ghost insertions when the agent's true edit lived in a
    # later sed command (3200ebfc — duplicate ``guard(thermal_zone)(tz);``
    # from a failed multi-line s/// followed by a successful Na\\).
    if line_num is not None:
        edit_type = EditType.REPLACE_MULTI_AT_LINE if is_global else EditType.REPLACE_ONCE_AT_LINE
    else:
        if is_global:
            edit_type = EditType.REPLACE_MULTI_PER_LINE
        elif "\n" in _unescape_sed_replacement(old_text):
            return None
        else:
            edit_type = EditType.REPLACE_ONCE_PER_LINE
    return Edit(
        filename=filename,
        before=old_text,
        after=new_text,
        starting_line=line_num,
        explanation=f"{explanation_prefix} sed s #{n}",
        best_effort=True,
        edit_type=edit_type,
    )


def _sed_append_edit(parsed: dict, filename: str, explanation_prefix: str, n: int) -> Edit | None:
    """Handle ``sed -i 'Na\\text' file`` or ``sed -i '/regex/a\\text' file``.

    Line-addressed (``Na\\text``) → ``INSERT_AFTER`` with ``starting_line=N+1``
    (matches ``str_replace_editor --insert_line`` convention; new text lands
    at that line).

    Pattern-addressed (``/regex/a\\text``) → ``PATTERN_INSERT_AFTER``: the
    anchor regex moves to ``pattern_address`` and the applier resolves the
    line at apply time. No ``starting_line`` set.

    NOTE: ``parsed["text"]`` is already fully unescaped by ``_parse_sed_aic``
    (single-pass GNU-sed compile_text rules) — do NOT re-unescape here.
    """
    # AIDEV-NOTE: _parse_sed_aic already handles \t and \n unescaping for a/i/c
    # text. Don't call _unescape_sed_replacement here — it double-converts \n
    # in C format strings (e.g. printk("...%s\n", ...)).
    # AIDEV-NOTE: empty text is valid — ``sed '5a\'`` inserts a blank line.
    text = parsed.get("text")
    if text is None:
        return None
    addr = parsed.get("address")
    if addr and addr.isdigit():
        return Edit(
            filename=filename,
            before="",
            after=text,
            starting_line=int(addr) + 1,
            explanation=f"{explanation_prefix} sed a #{n}",
            best_effort=True,
            edit_type=EditType.INSERT_AFTER_LINE,
        )
    # AIDEV-NOTE: Pattern-addressed ``a`` (e.g. ``/re/a\\text``) — resolve at apply time
    if addr and addr.startswith("/") and addr.endswith("/") and len(addr) > 2:
        pattern = addr[1:-1]
        return Edit(
            filename=filename,
            before="",
            after=text,
            pattern_address=pattern,
            explanation=f"{explanation_prefix} sed /pattern/ a #{n}",
            best_effort=True,
            edit_type=EditType.INSERT_AFTER_PATTERN,
        )
    return None


def _sed_insert_edit(parsed: dict, filename: str, explanation_prefix: str, n: int) -> Edit | None:
    """Handle ``sed -i 'Ni\\text' file`` or ``sed -i '/regex/i\\text' file``.

    Line-addressed (``Ni\\text``) → ``INSERT_BEFORE`` with ``starting_line=N``
    (new text appears at line N, pushing the old line N to N+1).

    Pattern-addressed (``/regex/i\\text``) → ``PATTERN_INSERT_BEFORE``: the
    anchor regex moves to ``pattern_address`` and the applier resolves the
    line at apply time. No ``starting_line`` set.

    NOTE: ``parsed["text"]`` is already fully unescaped by ``_parse_sed_aic``.
    """
    # AIDEV-NOTE: empty text is valid — ``sed '5i\'`` inserts a blank line.
    text = parsed.get("text")
    if text is None:
        return None
    addr = parsed.get("address")
    if addr and addr.isdigit():
        return Edit(
            filename=filename,
            before="",
            after=text,
            starting_line=int(addr),
            explanation=f"{explanation_prefix} sed i #{n}",
            best_effort=True,
            edit_type=EditType.INSERT_BEFORE_LINE,
        )
    if addr and addr.startswith("/") and addr.endswith("/") and len(addr) > 2:
        pattern = addr[1:-1]
        return Edit(
            filename=filename,
            before="",
            after=text,
            pattern_address=pattern,
            explanation=f"{explanation_prefix} sed /pattern/ i #{n}",
            best_effort=True,
            edit_type=EditType.INSERT_BEFORE_PATTERN,
        )
    return None


def _sed_change_edit(parsed: dict, filename: str, explanation_prefix: str, n: int) -> Edit | None:
    """Handle ``sed -i 'Nc\\text' file`` or ``sed -i 'N,Mc\\text' file``."""
    # AIDEV-NOTE: empty text is valid — ``sed '5c\'`` replaces line with a blank line.
    text = parsed.get("text")
    if text is None:
        return None
    addr = parsed.get("address")
    if not addr:
        return None
    # AIDEV-NOTE: {N+;c\text} — n_count > 0 means N commands preceded c\
    n_count = parsed.get("n_count", 0)
    if n_count > 0 and addr.isdigit():
        return Edit(
            filename=filename,
            before="",
            after=text,
            starting_line=int(addr),
            ending_line=int(addr) + n_count,
            explanation=f"{explanation_prefix} sed {{N;c}} #{n}",
            best_effort=True,
            edit_type=EditType.CHANGE_RANGE,
        )
    if n_count > 0 and addr.startswith("/") and addr.endswith("/"):
        return Edit(
            filename=filename,
            before="",
            after=text,
            pattern_address_start=addr[1:-1],
            n_count=n_count,
            explanation=f"{explanation_prefix} sed {{N;c}} #{n}",
            best_effort=True,
            edit_type=EditType.CHANGE_PATTERN_RANGE,
        )
    if addr.isdigit():
        return Edit(
            filename=filename,
            before="",
            after=text,
            starting_line=int(addr),
            explanation=f"{explanation_prefix} sed c #{n}",
            best_effort=True,
            edit_type=EditType.CHANGE_LINE,
        )
    # AIDEV-NOTE: Range address like "1193,1194" → RANGE_CHANGE.
    # Re-split literal \n in text: replacing multiple lines requires real
    # newlines as line separators (e.g. sed '1193,1194c\code\ncode').
    # _parse_sed_aic's else branch keeps \n literal to avoid breaking C
    # escapes in a/i commands — but RANGE_CHANGE structurally needs them.
    parts = addr.split(",")
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        range_text = "\n".join(text.split("\\n")) if "\n" not in text else text
        return Edit(
            filename=filename,
            before="",
            after=range_text,
            starting_line=int(parts[0]),
            ending_line=int(parts[1]),
            explanation=f"{explanation_prefix} sed c #{n}",
            best_effort=True,
            edit_type=EditType.CHANGE_RANGE,
        )
    # /pattern1/,/pattern2/c\text → PATTERN_RANGE_CHANGE
    m = re.match(r"^/(.+)/,/(.+)/$", addr)
    if m:
        start_pat = m.group(1)
        end_pat = m.group(2)
        range_text = "\n".join(text.split("\\n")) if "\n" not in text else text
        return Edit(
            filename=filename,
            before="",
            after=range_text,
            pattern_address_start=start_pat,
            pattern_address_end=end_pat,
            explanation=f"{explanation_prefix} sed /pat/,/pat/c #{n}",
            best_effort=True,
            edit_type=EditType.CHANGE_PATTERN_RANGE,
        )
    return None


def _sed_delete_edit(parsed: dict, filename: str, explanation_prefix: str, n: int) -> Edit | None:
    """Handle ``sed -i 'Nd' file`` or ``sed -i 'N,Md' file`` or ``sed -i '/pattern/,+Nd' file``."""
    addr = parsed.get("address")
    if not addr:
        return None
    if addr.isdigit():
        return Edit(
            filename=filename,
            before="",
            after="",
            starting_line=int(addr),
            explanation=f"{explanation_prefix} sed d #{n}",
            best_effort=True,
            edit_type=EditType.DELETE_LINE,
        )
    # AIDEV-NOTE: Range address like "1191,1201" → RANGE_DELETE
    parts = addr.split(",")
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        return Edit(
            filename=filename,
            before="",
            after="",
            starting_line=int(parts[0]),
            ending_line=int(parts[1]),
            explanation=f"{explanation_prefix} sed d #{n}",
            best_effort=True,
            edit_type=EditType.DELETE_RANGE,
        )
    # AIDEV-NOTE: /pattern/,+N → PATTERN_RANGE_DELETE (resolved at apply time)
    m = re.match(r"^/(.+)/,\+(\d+)$", addr)
    if m:
        pattern, offset = m.group(1), int(m.group(2))
        return Edit(
            filename=filename,
            before=pattern,
            after="",
            starting_line=offset,
            explanation=f"{explanation_prefix} sed /pattern/,+{offset}d #{n}",
            best_effort=True,
            edit_type=EditType.DELETE_PATTERN_RANGE,
        )
    # /pat1/,/pat2/d — two-pattern range delete
    pat_range_m = re.match(r"^/(.+)/,/(.+)/$", addr or "")
    if pat_range_m:
        return Edit(
            filename=filename,
            before=pat_range_m.group(1),
            after="",
            pattern_address_end=pat_range_m.group(2),
            explanation=f"{explanation_prefix} sed /pat1/,/pat2/ d #{n}",
            best_effort=True,
            edit_type=EditType.DELETE_PATTERN_RANGE,
        )
    # /pattern/d — single-line pattern delete
    if addr and addr.startswith("/") and addr.endswith("/") and len(addr) > 2:
        pattern = addr[1:-1]
        edit = Edit(
            filename=filename,
            before="",
            after="",
            pattern_address=pattern,
            explanation=f"{explanation_prefix} sed /pattern/ d #{n}",
            best_effort=True,
            edit_type=EditType.DELETE_PATTERN,
        )
        range_addr = parsed.get("range_address")
        pat_range_m = re.match(r"^/(.+)/,/(.+)/$", range_addr or "")
        if pat_range_m:
            edit.pattern_address_start = pat_range_m.group(1)
            edit.pattern_address_end = pat_range_m.group(2)
        return edit
    return None


def _sed_next_delete_edit(
    parsed: dict, filename: str, explanation_prefix: str, n: int
) -> Edit | None:
    """Handle ``sed -i '/A/{n;/B/d}' file`` — delete line after PATTERN_A iff
    it matches PATTERN_B. Resolved at apply time via PATTERN_NEXT_DELETE.

    ``pattern_address`` carries the anchor pattern A; ``before`` carries the
    target pattern B. Both are stripped of the surrounding ``/.../``.
    """
    addr = parsed.get("address")
    target = parsed.get("target")
    if not addr or not target:
        return None
    if not (addr.startswith("/") and addr.endswith("/")):
        return None
    anchor = addr[1:-1]
    if not anchor or not target:
        return None
    del_anchor = parsed.get("delete_anchor", False)
    ncmd = "N" if del_anchor else "n"
    return Edit(
        filename=filename,
        before=target,
        after="",
        pattern_address=anchor,
        delete_anchor=del_anchor,
        explanation=f"{explanation_prefix} sed /A/{{{ncmd};/B/d}} #{n}",
        best_effort=True,
        edit_type=EditType.DELETE_PATTERN_NEXT,
    )


def _sed_next_subst_edit(
    parsed: dict, filename: str, explanation_prefix: str, n: int
) -> list[Edit]:
    """Handle ``sed -i 'N{N;s/old/new/[g]}' file`` or ``sed -i 'N,M{N;s/old/new/}' file``
    — join line with next, run substitution on the combined two-line text.
    Range addresses emit one edit per pair, bottom-to-top."""
    addr = parsed.get("address")
    if not addr:
        return []
    old_text = parsed.get("old", "")
    new_text = _unescape_sed_replacement(parsed.get("new", ""))
    parts = addr.split(",")
    start = int(parts[0])
    end = int(parts[1]) if len(parts) > 1 else start
    edits = []
    for line in range(start, end + 1, 2):
        edits.append(Edit(
            filename=filename,
            before=old_text,
            after=new_text,
            starting_line=line,
            ending_line=line + 1,
            explanation=f"{explanation_prefix} sed {{N;s}} #{n}",
            best_effort=True,
            edit_type=EditType.REPLACE_JOINED_NEXT,
        ))
    edits.reverse()
    return edits


def _sed_conditional_delete_edit(
    parsed: dict, filename: str, explanation_prefix: str, n: int
) -> Edit | None:
    """Handle ``sed -i 'N{/pattern/d}' file`` — delete line N if it matches pattern."""
    addr = parsed.get("address")
    pattern = parsed.get("pattern")
    if not addr or not addr.isdigit() or not pattern:
        return None
    return Edit(
        filename=filename,
        before="",
        after="",
        starting_line=int(addr),
        pattern_address=pattern,
        explanation=f"{explanation_prefix} sed N{{/pat/d}} #{n}",
        best_effort=True,
        edit_type=EditType.DELETE_LINE,
    )


def _sed_brace_change_edit(
    parsed: dict, filename: str, explanation_prefix: str, n: int
) -> Edit | None:
    """Handle ``sed -i 'N,M{ Ns/.*/A/; Ms/.*/B/ }' file`` — per-line
    whole-line replacement inside a brace block → single CHANGE_RANGE."""
    addr = parsed.get("address", "")
    parts = addr.split(",")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    start, end = int(parts[0]), int(parts[1])
    replacements = []
    for m in re.finditer(r"\d+s/\.\*/(.*?)/", parsed.get("body", "")):
        replacements.append(_unescape_sed_replacement(m.group(1)))
    if not replacements:
        return None
    return Edit(
        filename=filename,
        before="",
        after="\n".join(replacements),
        starting_line=start,
        ending_line=end,
        explanation=f"{explanation_prefix} sed {{N,M}} change #{n}",
        best_effort=True,
        edit_type=EditType.CHANGE_RANGE,
    )


def _expand_brace_multi_commands(sub_exprs: list[str]) -> list[str]:
    """Expand ``/range/{cmd1;cmd2}`` into ``[/range/{cmd1}, /range/{cmd2}]``."""
    expanded = []
    for expr in sub_exprs:
        stripped = expr.strip()
        addr, rest = _split_address(stripped)
        rest_s = rest.strip() if rest else ""
        if addr and rest_s.startswith("{") and rest_s.endswith("}") and ";" in rest_s:
            inner = rest_s[1:-1].strip()
            parts = _split_brace_inner(inner)
            if len(parts) > 1:
                for sub in parts:
                    expanded.append(f"{addr}{{{sub}}}")
            else:
                expanded.append(expr)
        else:
            expanded.append(expr)
    return expanded


def _split_brace_inner(inner: str) -> list[str]:
    """Split brace-block inner on ``;`` respecting ``/pattern/`` boundaries."""
    parts: list[str] = []
    current: list[str] = []
    i = 0
    in_regex = False
    while i < len(inner):
        ch = inner[i]
        if ch == "/" and not in_regex:
            in_regex = True
            current.append(ch)
        elif ch == "/" and in_regex:
            in_regex = False
            current.append(ch)
        elif ch == "\\" and i + 1 < len(inner):
            current.append(inner[i:i + 2])
            i += 2
            continue
        elif ch == ";" and not in_regex:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
        else:
            current.append(ch)
        i += 1
    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts


def _sed_single_edit(sed_cmd: str, explanation_prefix: str) -> list[Edit]:
    """Parse a single ``sed -i`` command into ``Edit`` objects."""
    tokens = _tokenize_command(sed_cmd)
    if tokens is None:
        tokens = _sed_fallback_tokenize(sed_cmd)

    result = _extract_sed_expression_and_file(tokens)
    if result is None:
        return []
    expression, raw_path = result
    filename = normalize_linux_repo_path(raw_path)
    # AIDEV-NOTE: external absolute paths (e.g. /tmp/, /home/) — keep raw for stashing
    if not is_linux_tree_path(raw_path) and raw_path.startswith("/"):
        filename = raw_path
    elif not filename:
        return []

    sub_exprs = _split_sed_expression(expression)
    sub_exprs = _expand_brace_multi_commands(sub_exprs)
    all_parsed = [_parse_sed_expression(sub) for sub in sub_exprs]

    # AIDEV-NOTE: fall back if any fragment fails OR is a/i/c (freeform text can contain ;)
    needs_fallback = (
        any(p is None for p in all_parsed)
        or any(p["type"] in ("a", "i", "c") for p in all_parsed if p)
    )
    if needs_fallback:
        single = _parse_sed_expression(expression)
        if single is None:
            logger.debug("Could not parse sed expression: %s", expression[:200])
            return []
        all_parsed = [single]

    edits: list[Edit] = []
    for n_idx, parsed in enumerate(all_parsed, start=1):
        edit = None
        if parsed["type"] == "s":
            edit = _sed_substitution_edit(parsed, filename, explanation_prefix, n_idx)
        elif parsed["type"] == "a":
            edit = _sed_append_edit(parsed, filename, explanation_prefix, n_idx)
        elif parsed["type"] == "i":
            edit = _sed_insert_edit(parsed, filename, explanation_prefix, n_idx)
        elif parsed["type"] == "c":
            edit = _sed_change_edit(parsed, filename, explanation_prefix, n_idx)
        elif parsed["type"] == "d":
            edit = _sed_delete_edit(parsed, filename, explanation_prefix, n_idx)
        elif parsed["type"] == "next_delete":
            edit = _sed_next_delete_edit(parsed, filename, explanation_prefix, n_idx)
        elif parsed["type"] == "next_subst":
            edits.extend(_sed_next_subst_edit(parsed, filename, explanation_prefix, n_idx))
            continue
        elif parsed["type"] == "conditional_delete":
            edit = _sed_conditional_delete_edit(parsed, filename, explanation_prefix, n_idx)
        elif parsed["type"] == "brace_change":
            edit = _sed_brace_change_edit(parsed, filename, explanation_prefix, n_idx)
        if edit:
            edits.append(edit)
    # AIDEV-NOTE: sed applies all commands against original line numbering, but our
    # system applies edits sequentially. Sort deletes descending so each doesn't
    # shift line numbers of subsequent ones.
    if len(edits) > 1 and all(e.edit_type == EditType.DELETE_LINE for e in edits):
        edits.sort(key=lambda e: e.starting_line or 0, reverse=True)
    return edits


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def sed_edits(bash_block: str, explanation_prefix: str) -> list[Edit]:
    """Extract ``Edit`` objects from ``sed -i`` commands in a bash block."""
    if "sed" not in bash_block or "-i" not in bash_block:
        return []

    sed_commands = _extract_sed_commands(bash_block)
    if not sed_commands:
        sed_commands = [bash_block]

    edits: list[Edit] = []
    for sed_cmd in sed_commands:
        edits.extend(_sed_single_edit(sed_cmd, explanation_prefix))
    return edits
