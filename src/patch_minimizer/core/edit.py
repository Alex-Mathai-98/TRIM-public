"""
Edit dataclass for representing code modifications.

Moved from kernel_write_strategies/edit.py to tools/generate_patch/ as part of
the tool-based architecture refactoring.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pprint import pformat

from diff_match_patch import diff_match_patch


class ExtractStatus(str, Enum):
    """Status codes for patch extraction results.

    Copied from Kernel_Agent.tools.generate_patch.extract_status — needed for
    unpickling LLMResponseHistory .pkl files that reference this enum.
    """
    APPLICABLE_PATCH = "APPLICABLE_PATCH"
    MATCHED_BUT_EMPTY_ORIGIN = "MATCHED_BUT_EMPTY_ORIGIN"
    MATCHED_BUT_EMPTY_DIFF = "MATCHED_BUT_EMPTY_DIFF"
    RAW_PATCH_BUT_UNMATCHED = "RAW_PATCH_BUT_UNMATCHED"
    RAW_PATCH_BUT_UNPARSED = "RAW_PATCH_BUT_UNPARSED"
    NO_PATCH = "NO_PATCH"
    IS_VALID_JSON = "IS_VALID_JSON"
    NOT_VALID_JSON = "NOT_VALID_JSON"
    NOT_DIFFERENT_FROM_PARENT = "NOT_DIFFERENT_FROM_PARENT"


class MatchMode(str, Enum):
    FUZZY = "fuzzy"
    SEMI_FUZZY = "semi_fuzzy"
    EXACT = "exact"


class EditType(str, Enum):

    # ── Replace Operations ──
    # AIDEV-NOTE: substring find-and-replace. MatchMode controls algorithm:
    # FUZZY (Kernel Agent), SEMI_FUZZY (OpenHands), EXACT (SWE-Agent/str_replace).
    REPLACE = "replace"
    # patch-file hunk — exact match with fuzz-1 fallback (trim first/last context line)
    REPLACE_PATCH_HUNK = "replace_patch_hunk"
    # sed '5s/old/new/' — first occurrence on a single line
    REPLACE_ONCE_AT_LINE = "replace_once_at_line"
    # sed '5s/old/new/g' — all occurrences on a single line
    REPLACE_MULTI_AT_LINE = "replace_multi_at_line"
    # sed 's/old/new/' — first occurrence per line, all lines
    REPLACE_ONCE_PER_LINE = "replace_once_per_line"
    # sed 's/old/new/g' — all occurrences per line, all lines
    REPLACE_MULTI_PER_LINE = "replace_multi_per_line"
    # sed '5,10s/old/new/g' — all occurrences per line, within a line range
    REPLACE_MULTI_PER_LINE_IN_RANGE = "replace_multi_per_line_in_range"
    # sed '5,10s/old/new/' — first occurrence per line, within a line range
    REPLACE_ONCE_PER_LINE_IN_RANGE = "replace_once_per_line_in_range"
    # sed '/pattern/s/old/new/' — first occurrence on pattern-matched lines
    REPLACE_ONCE_PER_LINE_PATTERN = "replace_once_per_line_pattern"
    # sed N{N;s/old/new/} — join line N with next, run substitution on combined text
    REPLACE_JOINED_NEXT = "replace_joined_next"


    # ── Delete Operations ──
    # sed Nd — single-line delete. With pattern_address: sed N{/pattern/d} — conditional delete.
    DELETE_LINE = "delete_line"
    # sed N,Md — multi-line range delete
    DELETE_RANGE = "delete_range"
    # sed /pattern/d — delete all lines matching pattern
    DELETE_PATTERN = "delete_pattern"
    # sed /pattern/,+Nd — pattern-addressed range delete (resolved at apply time)
    DELETE_PATTERN_RANGE = "delete_pattern_range"
    # sed /A/{n;/B/d} — find line matching A (in `pattern_address`), advance to
    # next line, delete it iff it matches B (in `before`). Resolved at apply time.
    DELETE_PATTERN_NEXT = "delete_pattern_next"


    # ── Insert Operations ──
    # sed Ni\text — insert before line N
    INSERT_BEFORE_LINE = "insert_before_line"
    # str_replace_editor insert --insert_line N / sed Na\text — insert after line N
    INSERT_AFTER_LINE = "insert_after_line"
    # sed /regex/a\TEXT — insert after all lines matching regex
    INSERT_AFTER_PATTERN = "insert_after_pattern"
    # sed /regex/i\TEXT — insert before all lines matching regex
    INSERT_BEFORE_PATTERN = "insert_before_pattern"


    # ── Change Operations ──
    # sed Nc\text — replace line N entirely with new text
    CHANGE_LINE = "change_line"
    # sed N,Mc\text — replace lines N through M with new text (once)
    CHANGE_RANGE = "change_range"
    # sed N,Ms/.*/text/ — replace each line in range with text independently
    CHANGE_RANGE_PER_LINE = "change_range_per_line"
    # sed /pattern1/,/pattern2/c\text — pattern-addressed range replace
    CHANGE_PATTERN_RANGE = "change_pattern_range"


    # ── File-Level Operations ──
    # cp source dest — copy a file
    COPY_FILE = "copy_file"
    # rm /linux/file — delete a file from the tree
    WHOLE_FILE_DELETE = "whole_file_delete"
    # str_replace_editor create --file_text, cat/tee heredoc overwrite.
    # Parser doesn't know if file exists — applier dispatches to REWRITE or CREATE.
    WHOLE_FILE_ACTIONS = "whole_file_actions"
    # Sub-type: overwrite existing file (resolved at apply time)
    WHOLE_FILE_REWRITE = "whole_file_rewrite"
    # Sub-type: create new file (resolved at apply time)
    WHOLE_FILE_CREATE = "whole_file_create"


def get_common_lines(text_A: str, text_B: str) -> int:
    """Count common leading lines between two texts."""
    text_A_lines = text_A.split("\n")
    text_B_lines = text_B.split("\n")
    min_length = min(len(text_A_lines), len(text_B_lines))
    common = 0
    for idx in range(min_length):
        if text_A_lines[idx] == text_B_lines[idx]:
            common += 1
    return common


def extract_prefix(text_A: str, text_B: str, common_count: int) -> tuple[str, str, str]:
    """Extract common prefix lines and return (prefix, remaining_A, remaining_B)."""
    window = common_count - 5
    prefix = "\n".join(text_A.split("\n")[:window]) + "\n"
    text_A = text_A.replace(prefix, "")
    text_B = text_B.replace(prefix, "")
    return prefix, text_A, text_B


@dataclass
class Edit:
    """Represents a single code edit with before/after content."""
    filename: str
    before: str
    after: str
    explanation: str
    # AIDEV-NOTE: Ephemeral UID stamped per-path for coupling remapping during minimization.
    # Not serialized — only used to track edits through node/file/edit passes.
    global_edit_idx: int | None = None
    # AIDEV-NOTE: 1-based line hint. When set, EditApplier jumps directly to
    # this line instead of scanning the whole file. Coordinate system depends
    # on is_dd_hunk — see below.
    starting_line: int | None = None
    # AIDEV-NOTE: True ONLY for DD baseline hunks (diff-header origin), where
    # starting_line is in ORIGINAL-file coords and repo_patch_manager must
    # adjust it by the cumulative per-file shift from prior edits. Default
    # False covers everything else — agent adapters (SWE-agent insert,
    # mini-swe-agent sed, OpenHands), Kernel_Agent's own LLM patches,
    # tests/examples. For False edits, starting_line (if set) is in LIVE-file
    # coords and must NOT receive an offset adjustment.
    is_dd_hunk: bool = False
    # AIDEV-NOTE: When True, apply_single_patch and check_single_patch_application
    # skip this edit silently if it fails to apply, instead of asserting/aborting.
    # Used for edits parsed from bash commands (e.g. sed -i) where we cannot
    # determine from the trajectory whether the edit actually succeeded.
    best_effort: bool = False
    # AIDEV-NOTE: Semantic operation type — dispatches to the correct applier method.
    edit_type: EditType = EditType.REPLACE
    # AIDEV-NOTE: Matching algorithm for REPLACE edits. FUZZY (Kernel Agent),
    # SEMI_FUZZY (OpenHands — exact then strip retry), EXACT (SWE-Agent/str_replace).
    match_mode: MatchMode = MatchMode.FUZZY
    # AIDEV-NOTE: 1-based inclusive end line for DELETE_RANGE (sed N,Md).
    ending_line: int | None = None
    # AIDEV-NOTE: 0-based trajectory step index this edit was extracted from.
    step_index: int | None = None
    # AIDEV-NOTE: Expected cumulative git diff after this edit's step was applied
    # in the docker environment. From trajectory state.diff. Only set on the LAST
    # edit of a step (a single step can produce multiple edits). Used for debugging.
    expected_diff: str | None = None
    # AIDEV-NOTE: Per-edit diff from OpenHands extras.diff — shows what this single
    # edit changed, computed against the file after all previous edits were applied.
    # Distinct from expected_diff (cumulative state.diff from SWE-agent).
    edit_diff: str | None = None
    # AIDEV-NOTE: sed /pattern/ address for REPLACE_ONCE_PER_LINE_PATTERN — restricts
    # substitution to lines matching this substring.
    pattern_address: str | None = None
    pattern_address_start: str | None = None
    pattern_address_end: str | None = None
    n_count: int | None = None
    # AIDEV-NOTE: For DELETE_PATTERN_NEXT — True when sed uses uppercase N
    # ({N;/B/d} deletes both anchor+next), False for lowercase n (next only).
    delete_anchor: bool = False
    # AIDEV-NOTE: True for str_replace_editor commands. At EOF the applier uses a
    # leading separator ('\n' + edit.after) instead of trailing (edit.after + '\n').
    is_str_replace_cmd: bool = False

    def __str__(self):
        return f"{self.filename}\nBefore:\n{pformat(self.before)}\nAfter:\n{pformat(self.after)}\n"

    def __repr__(self):
        return str(self)

    def generate_structured_format(self, edit_type: str = "1") -> str:
        """Generates the string form of the edit."""

        filename_part = f"""<file>\n{self.filename.strip()}\n</file>"""
        original_part = f"""<original>\n{self.before.strip()}\n</original>"""
        patched_part = f"""<patched>\n{self.after.strip()}\n</patched>"""

        if edit_type == "1":
            # include for each edit : [reason, file, original, replaced]
            if self.explanation is not None:
                reason_part = "<reason>\n" + self.explanation.strip() + "\n</reason>"
                final_format = "\n".join([reason_part, filename_part, original_part, patched_part])
            else:
                final_format = "\n".join([filename_part, original_part, patched_part])

        elif edit_type == "2":
            # include for each edit : [file, original, replaced]
            final_format = "\n".join([filename_part, original_part, patched_part])

        elif edit_type == "3":
            # search replace format
            final_format = "\n".join([filename_part, self.generate_search_replace_diff()])

        return final_format

    @classmethod
    def get_dummy_format_1(cls) -> str:
        """Returns the dummy format including a high-level solution and a List of [reason,file,original,replaced] format."""

        return """<solution> A natural language explanation of the solution. </solution>

```
# modification 1
<reason>
...
</reason>
<file>
...
</file>
<original>
...
</original>
<patched>
...
</patched>

# modification 2
<reason>
...
</reason>
<file>
...
</file>
<original>
...
</original>
<patched>
...
</patched>

# modification 3
...
```"""

    @classmethod
    def get_dummy_format_2(cls) -> str:
        """Returns the dummy format that is List of [file,original,replaced] format."""

        return """```
# modification 1
<file>
...
</file>
<original>
...
</original>
<patched>
...
</patched>

# modification 2
<file>
...
</file>
<original>
...
</original>
<patched>
...
</patched>

# modification 3
...
```"""

    @classmethod
    def get_dummy_format_3(cls) -> str:
        """Returns the dummy format that is List of [file,original,replaced] format."""

        return """<solution> A natural language explanation of the solution. </solution>

```
# modification 1
<file>
...
</file>
<original>
...
</original>
<patched>
...
</patched>

# modification 2
<file>
...
</file>
<original>
...
</original>
<patched>
...
</patched>

# modification 3
...
```"""

    def generate_search_replace_diff(self) -> str:
        """Generate a search-replace diff format for the edit."""

        def get_line_level_diff(text1: str, text2: str) -> list:
            dmp = diff_match_patch()
            lines_to_unicode = dmp.diff_linesToChars(text1, text2)
            lineText1 = lines_to_unicode[0]
            lineText2 = lines_to_unicode[1]
            lineArray = lines_to_unicode[2]
            diffs = dmp.diff_main(lineText1, lineText2, False)
            dmp.diff_charsToLines(diffs, lineArray)
            dmp.diff_cleanupSemantic(diffs)
            return diffs

        def extract_space_tab_prefix(s: str) -> str:
            prefix = ''
            for char in s:
                if char in (' ', '\t'):
                    prefix += char
                else:
                    break
            return prefix

        def lines_less_than_x(text: str, x_ctr: int) -> bool:
            num_lines = len(text.split("\n"))
            return num_lines < x_ctr

        text1_ = "\n" + self.before
        text2_ = "\n" + self.after
        list_of_edits = get_line_level_diff(text1_, text2_)

        ans_list = []

        last_value = ""
        if len(list_of_edits) > 1 and list_of_edits[-1][0] == 0:
            last_value = list_of_edits[-1][1]

        num_edits = len(list_of_edits)
        count = 5
        index = 0
        while index < num_edits:

            edit = list_of_edits[index]
            edit_type = edit[0]
            edit_text = edit[1]

            if index == 0:
                # special case
                pass

            else:
                # case 1
                if edit_type == 0:
                    pass

                # case 2
                elif edit_type == 1:
                    prev_edit_type = list_of_edits[index - 1][0]
                    prev_edit_text = list_of_edits[index - 1][1]

                    if prev_edit_type == 0:
                        before = prev_edit_text
                        after = prev_edit_text + edit_text

                        if len(ans_list) == 0:
                            ans_list.append([before, after])
                        else:
                            if lines_less_than_x(before, count) and lines_less_than_x(after, count):
                                # modify previous edit
                                ans_list[-1][0] += before
                                ans_list[-1][1] += after
                            else:
                                ans_list.append([before, after])

                    elif prev_edit_type == -1:
                        prev_ans = ans_list[-1]
                        # update the previous after
                        prev_ans[1] += edit_text

                # case 3
                elif edit_type == -1:

                    prev_edit_type = list_of_edits[index - 1][0]
                    prev_edit_text = list_of_edits[index - 1][1]

                    if prev_edit_type == 0:
                        before = prev_edit_text + edit_text
                        after = prev_edit_text

                        if len(ans_list) == 0:
                            ans_list.append([before, after])
                        else:
                            if lines_less_than_x(before, count) and lines_less_than_x(after, count):
                                # modify previous edit
                                ans_list[-1][0] += before
                                ans_list[-1][1] += after
                            else:
                                ans_list.append([before, after])

                    elif prev_edit_type == +1:
                        prev_ans = ans_list[-1]
                        prev_ans[0] += edit_text

            index += 1

        final_text = ""
        for index, ans in enumerate(ans_list):
            if index == 0:
                ans_list[0][0] = ans_list[0][0][1:]
                ans_list[0][1] = ans_list[0][1][1:]

            common_count = get_common_lines(ans[0], ans[1])
            if common_count > 10:
                common_prefix_lines, text_A, text_B = extract_prefix(ans[0], ans[1], common_count)
                prefix = extract_space_tab_prefix(text_A)
                final_text += common_prefix_lines + "\n" + prefix + ">>>>>>>> SEARCH\n" + text_A + prefix + "<<<<<<<< REPLACE\n" + text_B + prefix + "=================\n"
            else:
                prefix = extract_space_tab_prefix(ans[0])
                final_text += prefix + ">>>>>>>> SEARCH\n" + ans[0] + prefix + "<<<<<<<< REPLACE\n" + ans[1] + prefix + "=================\n"

        first_value = ""
        if final_text == "":
            if len(list_of_edits) and list_of_edits[0][0] == 0:
                first_value = list_of_edits[0][1]

        final_ans = first_value + final_text + last_value
        final_ans = "<patch>\n" + final_ans + "\n</patch>"
        return final_ans
