# Mini-SWE-Agent Adapter

> **Purpose:** Parse mini-swe-agent `messages[]` trajectories — extracting edits from bash commands (`sed -i`, `cat <<EOF > file`, etc.) — for the minimization pipeline.
>
> **Scope:** Trajectory parsing + edit extraction. Bulk loading and evaluation harness live in `agent_adapter_base/evaluation/`.

---

## 1. Directory layout

```
mini_swe_agent_adapter/
├── __init__.py                # Side-effect import (registers parser) + re-exports
├── parsers.py                 # MiniSweMessagesTrajectoryParser (TrajectoryParser subclass)
├── bash_edit_extractor.py     # BashEditExtractor (ActionEditExtractor subclass) — delegates to edit_parsing_helpers
├── parser_gap_detector.py     # detect_parser_gaps() — scans trajectory for known unparseable patterns
├── statements/                # Concrete Statement/RevertStatement classes (see statements/AGENTS.md)
└── AGENTS.md                  # This file
```

## 2. Key classes

| Class | File | Purpose |
|-------|------|---------|
| `MiniSweMessagesTrajectoryParser` | `parsers.py` | Parses `messages[]` JSON; converts to steps, overrides 5 template hooks from `TrajectoryParser` ABC plus `is_reset_all_step`, `is_successful_undo_step`, and `perform_undo_step` for multi-file `git checkout`/`git restore`/`git reset --hard` revert semantics. `__init__` sets `REPO_TREE_PREFIX = "/linux/"`. Implements external-file stashing: edits targeting non-repo-tree paths (e.g. `/tmp/`, `/home/`) are deferred in `_external_file_edits` and remapped to their repo-tree destination when a subsequent `COPY_FILE` cp-back step is encountered. |
| `BashEditExtractor` | `bash_edit_extractor.py` | Orchestrates edit extraction by delegating to shared modules in `edit_parsing_helpers/` (`sed_edits`, `heredoc_edits`, `tee_heredoc_edits`, `StrReplaceEditorExtractor`). Uses `is_*` detection functions for uniform command identification. Also detects (with warnings) Python file-writes and patch commands. `extract_edits_from_step(step)` threads `patch_file_bodies` and `patch_user_response` from the step dict, calls `extract_edits`, and cleans up. |
| `detect_parser_gaps` | `parser_gap_detector.py` | Scans trajectory messages for known parser gap patterns (python inline writes, tmpfile python scripts, head/tail splices, sed hold-space/r/grouped commands). Only reports gaps where the command succeeded. Tracks temp file creation across messages to link multi-step patterns. |

## 3. How it works

### Message → Step conversion

Mini-swe trajectories are alternating assistant/user message pairs:
- **Assistant message**: contains `THOUGHT:` reasoning + markdown `\`\`\`bash` code blocks
- **User message**: contains `<returncode>N</returncode>` + `<output>...</output>` XML tags

`_messages_to_steps()` converts these pairs into step dicts (`assistant_content`, `user_response`, `msg_index`, `bash_blocks`, `returncode`, `patch_file_bodies`). It also maintains a **cumulative heredoc memo**: `cat`/`tee` heredocs writing `/tmp/*.patch` / `*.diff` bodies are accumulated across steps (`iter_heredoc_patch_targets`) and snapshotted into each step's `patch_file_bodies`, so a later `patch -p1 < /tmp/fix.patch` can look up the diff body and extract real edits (works even when the heredoc and the `patch` command share one bash block).

### Template hooks

`MiniSweMessagesTrajectoryParser` overrides these from `TrajectoryParser`:

- `step_requests_feedback(step)` — detects `run_kernel` / `/KBDr/run_kernel` in bash blocks
- `is_edit_step(step)` — true if any bash block satisfies `is_edit` of any class in `BashEditExtractor.EDIT_STATEMENTS` (cat/tee heredoc, sed, str_replace_editor, rm, cp, patch — `PatchStatement.is_edit` consults the step's `patch_file_bodies` heredoc memo); also matches `str_replace_editor` in `assistant_content` outside any bash block
- `is_successful_edit_step(step)` — checks `<returncode>0</returncode>` in user response; also accepts `patching file` output for partial-apply patch steps
- `parse_edit_step(step, idx, extractor)` — delegates to `BashEditExtractor.extract_edits_from_step()`
- `is_reset_all_step(step)` — detects `git reset --hard` (no file args) that wipes all edits; checks `"HEAD is now at"` in response rather than rc (compound commands can have rc!=0 from later commands even though the reset succeeded)
- `is_successful_undo_step(step)` — validates undo step succeeded; checks positive `"Updated N path"` signal before rc (handles compound commands where later commands fail), rejects `"Updated 0 paths"` (staged-but-not-reverted checkout)
- `perform_undo_step(step, bucket, revisions, ...)` — multi-file git checkout/restore undo via `UNDO_STATEMENTS` (`GitResetFileStatement`, applied per bash block). Success rule `reset_file_succeeded` is shared with `is_successful_undo_step`. Two departures from the base single-file `is_undo_step` / last-edit-pop contract: (1) handles multiple files per step via `extract_git_reset_file_targets`, (2) drops **all** prior edits on reverted files (full revert, not single-undo pop). Returns `False` so the trajectory walk falls through to `parse_edit_step` — a single mini-swe turn can contain a reset and a fresh edit in different bash blocks.

### can_parse discrimination

Accepts when either:
1. `trajectory_format` field contains `"mini-swe"` (preferred)
2. `messages` key is present without `trajectory` or `events` (fallback)

## 4. Bash edit extraction

`BashEditExtractor` handles these patterns by iterating `EDIT_STATEMENTS` over each block (**all** matching statements contribute, in list order cat > tee > sed > str_replace_editor > rm > cp > patch; the table below is by frequency):

| Pattern | Frequency | EditType | Notes |
|---------|-----------|----------|-------|
| `sed -i 's/old/new/' file` | 100% | `REPLACE` | Substitution (with optional line address, flags). Parsing via shared `sed_parsing.py` module. |
| `sed -i 'Na\text' file` | Common | `INSERT_AFTER` | Append after line N (multi-line via `\` continuation) |
| `sed -i 'Ni\text' file` | Common | `INSERT_BEFORE` | Insert before line N |
| `sed -i 'Nd' file` | Common | `DELETE` / `RANGE_DELETE` | Delete line(s). Range addresses (`N,Md`) produce `RANGE_DELETE` with `ending_line`. |
| `sed -i 'Nc\text' file` | Common | `CHANGE` | Change/replace line(s) |
| `sed -i 'N,Mc\text' file` | Common | `RANGE_CHANGE` | Change/replace line range (sets `ending_line`) |
| `cat <<'EOF' > file` | 54% | `WHOLE_FILE_ACTIONS` | Heredoc full-file overwrite |
| `tee file <<EOF` | ~4% | `WHOLE_FILE_ACTIONS` | Heredoc via tee |
| `str_replace_editor` | ~4% | `REPLACE` / `INSERT_AFTER` | Classic SWE-Agent CLI (delegates to `StrReplaceEditorExtractor`) |
| `rm file` | rare | `WHOLE_FILE_DELETE` | `parse_rm_command` (mirrors SWE-agent adapter) |
| `cp src dst` | rare | `COPY_FILE` | `parse_cp_command`; drives external-file → repo-tree remap in `parse_edit_step` |
| `patch -pN < file` / `git apply` | ~18% | (from diff) | `extract_edits_from_patch_cmd` on the memoized heredoc body; rejected hunks dropped |

| `head N FILE > /tmp/X; cat /tmp/Y >> /tmp/X; tail +M FILE >> /tmp/X; cp /tmp/X FILE` | rare | `RANGE_CHANGE` | Splice pattern (variants A/B/C) — replace lines `(N+1)..(M-1)` of FILE with middle scratch body. Implementation: `tmp_file_extractor.py`. Trajectory-level post-pass `_dedupe_splice_edits_per_file` keeps only the most-recent splice per filename across revisions because LIVE-coord re-application of repeated splices on the same range mis-targets after the first one mutates the file. |

`patch` / `git apply`: extracted when the diff body was written by an earlier (or same-block) `cat`/`tee` heredoc into `patch_file_bodies` — `extract_edits_from_patch_cmd` parses the unified diff and drops rejected hunks. Falls back to a warning only when no body is memoized (e.g. `patch -p1 < /proc/self/fd/0`).

Detected but not extracted (warnings logged):
- Inline Python file-writing scripts (~22%)
- `patch`/`git apply` with no recoverable diff body

Known sed gaps (not extracted):
- Hold-space line swap `{h;d}` / `{G}` — 3 trajectories affected
- Read file command `sed Nr file` — 4 trajectories affected (bottleneck; 15 others produce correct diff via other edits)

**Sed parsing shared module:** Sed command parsing has been extracted to `agent_adapter_base/edit_parsing_helpers/sed_parsing.py` — a shared module used by both mini-swe and potentially other adapters. All sed builders now set explicit `EditType` values on constructed `Edit` objects. `_extract_sed_commands` uses bashlex AST walking to handle compound statements (`cp && sed && diff`), falling back to manual line-based splitting. `_sed_change_edit` handles range addresses (`N,Mc`) producing `RANGE_CHANGE` with `ending_line`. Dead code `_SED_CMD_RE` has been removed.

**Coord system:** all extracted edits set `is_dd_hunk=False` (default) — `starting_line` on sed `a/i/c/d` is LIVE-file coord at the agent step and must not receive DD-style offset adjustment in `repo_patch_manager`.

### bashlex integration

`bashlex` (GPLv3, pip install) provides robust AST-based tokenization for command parsing. Falls back to `shlex.split()` (stdlib) when bashlex is unavailable.

**AIDEV-NOTE:** bashlex hangs on heredoc syntax (`<<`), so it is skipped for any input containing heredoc operators — but `<<` inside a sed pattern (e.g. C bitshift) is not a heredoc, so the guard uses `is_sed_command()` to avoid false-positive skips. Compound blocks (heredoc + sed) are split into individual commands before tokenization.

## 5. Shared with other adapters

- `StrReplaceEditorExtractor` is imported from `agent_adapter_base/edit_parsing_helpers/` for the rare `str_replace_editor` cases
- `AgentPass`, `AgentPassesStore`, `AgentPatchesLoadStats` live in `agent_adapter_base/evaluation/`

## 6. Registration

Importing this package triggers side-effect registration:
```python
import patch_minimizer.agent_adapters.mini_swe_agent_adapter  # noqa: F401
```
This registers `MiniSweMessagesTrajectoryParser` with `parser_registry` via `parsers.py` (`payload_keys() == ("messages",)`). Unlike most adapters it overrides `can_parse` (see below) instead of relying on the default key-presence check.

## 7. Coverage stats (100-trajectory test)

- **100% parse success** (0 errors, 0 timeouts)
- **100% edit extraction** (all 100 trajectories produced edits)
- **19% multi-revision** (split at `run_kernel` boundaries)
- Edit type distribution: heredoc 25%, substitution 24%, delete 16%, append 13%, insert 13%, change 8%
