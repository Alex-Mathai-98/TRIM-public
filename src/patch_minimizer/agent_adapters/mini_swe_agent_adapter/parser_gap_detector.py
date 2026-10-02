"""Detect known parser gaps in mini-swe trajectories."""
from __future__ import annotations

import re

from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.python_file_write_detection import detect_python_file_edit
from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.tmp_file_extractor import detect_splice_cmd

_BASH_BLOCK_RE = re.compile(r"```(?:bash|sh)\s*\n(.*?)\n```", re.DOTALL)
_RETURNCODE_RE = re.compile(r"<returncode>\s*(-?\d+)\s*</returncode>")

_CAT_TMPFILE_RE = re.compile(r"cat\s+>\s*(/tmp/\S+\.py|/tmp/\S+|\S+\.new)\s")
_TMPFILE_PYTHON_RE = re.compile(r"python3?\s+(/tmp/\S+\.py)\b")
_SED_READ_RE = re.compile(r"sed\s+.*\d+r\s+(\S+)")
_SED_HOLD_RE = re.compile(r"\{[hHgGx];?[dDpP]?\}|\{[dDpP];?[hHgGx]\}")
_SED_GROUPED_RE = re.compile(r"sed\s+.*-i\s+.*\{[^}]*[/][^}]*[aic]\\")


def _step_succeeded(messages: list, assistant_idx: int) -> bool:
    """Same logic as MiniSweMessagesTrajectoryParser.is_successful_edit_step."""
    if assistant_idx + 1 >= len(messages):
        return False
    resp = str(messages[assistant_idx + 1].get("content") or "")
    m = _RETURNCODE_RE.search(resp)
    if m and int(m.group(1)) == 0:
        return True
    if "has been edited" in resp or "File created successfully" in resp:
        return True
    if "patching file" in resp:
        return True
    return False


def detect_parser_gaps(traj_data: dict) -> list[dict]:
    """Scan trajectory messages for known parser gap patterns.

    Only reports gaps where the command actually succeeded (rc=0).
    Tracks temp file creation across messages to link multi-step patterns
    (e.g. cat > /tmp/fix.py in msg N -> python3 /tmp/fix.py in msg M).
    """
    hits: list[dict] = []
    messages = traj_data.get("messages") or []
    created_tmp_files: dict[str, int] = {}

    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        content = str(msg.get("content") or "")
        blocks = _BASH_BLOCK_RE.findall(content)
        succeeded = _step_succeeded(messages, i)
        for block in blocks:
            cat_m = _CAT_TMPFILE_RE.search(block)
            if cat_m:
                created_tmp_files[cat_m.group(1)] = i

            if not succeeded:
                continue

            if detect_python_file_edit(block):
                hits.append({"gap_type": "python_inline_file_write",
                             "msg_index": i, "command": block[:120]})
                continue

            py_m = _TMPFILE_PYTHON_RE.search(block)
            if py_m:
                script_path = py_m.group(1)
                created_at = created_tmp_files.get(script_path)
                hits.append({"gap_type": "tmpfile_python_script",
                             "msg_index": i, "command": block[:120],
                             "script_path": script_path,
                             "created_at_msg": created_at})
                continue

            if detect_splice_cmd(block):
                hits.append({"gap_type": "head_tail_splice",
                             "msg_index": i, "command": block[:120]})
                continue

            if "sed" not in block:
                continue
            if _SED_GROUPED_RE.search(block):
                hits.append({"gap_type": "sed_curly_brace_grouped",
                             "msg_index": i, "command": block[:120]})
            elif _SED_HOLD_RE.search(block):
                hits.append({"gap_type": "sed_hold_space_swap",
                             "msg_index": i, "command": block[:120]})
            else:
                sed_r_m = _SED_READ_RE.search(block)
                if sed_r_m:
                    read_file = sed_r_m.group(1)
                    created_at = created_tmp_files.get(read_file)
                    hits.append({"gap_type": "sed_read_file",
                                 "msg_index": i, "command": block[:120],
                                 "read_file": read_file,
                                 "created_at_msg": created_at})
    return hits
