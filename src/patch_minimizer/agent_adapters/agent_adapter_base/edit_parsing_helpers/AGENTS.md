# Edit Parsing Helpers

> Shared edit extractors used across multiple trajectory adapters. Each module turns a specific agent action/command format into `Edit` objects, and (mostly) exposes a paired `is_*`/`detect_*` predicate for step/block classification.

## File Overview

| File | Purpose |
|---|---|
| `action_edit_extractor.py` | `ActionEditExtractor` ABC — one abstract method `extract_edits(action_text) -> list[Edit]`. Returns `[]` for non-edit actions; raising is reserved for malformed input. Concrete subclasses live in adapter packages (and `str_replace_editor_extractor.py` here). |
| `str_replace_editor_extractor.py` | `StrReplaceEditorExtractor` + detection fns (`is_str_replace_editor_command`, `is_str_replace_editor_str_replace`, `is_str_replace_editor_insert`, `is_str_replace_editor_create`). Tokenizes with `shlex.split(posix=True)`, finds every `str_replace_editor` invocation in original order (`_find_all_invocations`), parses each via `argparse.parse_known_args`. `str_replace` → `REPLACE`/`EXACT`; `insert` → `INSERT_AFTER_LINE` with `starting_line=insert_line+1` and `is_str_replace_cmd=True`; `create` → `WHOLE_FILE_ACTIONS`. Used by both classic SWE and mini-swe (same CLI). OpenHands has its own extractor. |
| `sed_parsing.py` | `sed_edits()` + `is_sed_command()`. Parses `sed -i` into `Edit`s (see the dedicated sed section below). |
| `git_reset_file_detection.py` | `is_git_reset_file_command()` + `extract_git_reset_file_targets()`. Detects `git checkout` / `git restore` / `git reset --hard` per-file reverts (incl. `git -C <dir>` prefix) and extracts normalized target paths. `git restore --staged` (without `--worktree`) is treated as an unstage-only no-op and ignored. Shared by mini-swe and OpenHands. |
| `cat_heredoc_parsing.py` | `heredoc_edits()` + `is_cat_heredoc()` + `iter_cat_heredocs()`. Extracts `WHOLE_FILE_ACTIONS` edits from **both** orderings: `cat << EOF > file` and `cat > file << EOF` (redirect-first, used by the `patch -p1 < /tmp/fix.patch` flow). |
| `tee_heredoc_parsing.py` | `tee_heredoc_edits()` + `is_tee_heredoc()` — `WHOLE_FILE_ACTIONS` edits from `tee <file> <<EOF` patterns. |
| `cp_command_parsing.py` | `parse_cp_command()` + `is_cp_command()`, split into `extract_cp_pairs()` (parse `(source, dest)`) + `cp_edits_for_pairs()` (build edits) for `CpStatementBase` — `COPY_FILE` edits (`before`=source, `after`=""). Uses bashlex AST walking (`_find_cp_commands`) to isolate `cp` from compound commands; skips bashlex when `<<` is present. External non-tree source paths (e.g. `/tmp/`, `/home/`) are preserved raw for adapter-level remap. |
| `rm_command_parsing.py` | `parse_rm_command()` + `is_rm_command()`, split into `extract_rm_targets()` + `rm_edits_for_targets()` for `RMStatementBase` — `WHOLE_FILE_DELETE` edits. Uses bashlex AST walking (`_find_rm_commands`); `/tmp/...` targets normalize to `""` and are skipped. |
| `patch_command_detection.py` | `is_patch_command()` (back-compat alias `detect_patch_cmd`) — detection only, no extraction. Regex carefully excludes the `<<` heredoc form so `cat > /tmp/fix.patch << EOF` is not misread as a patch command. |
| `patch_file_extractor.py` | Unified-diff → `Edit` conversion for `patch`/`git apply`. See the patch-file section below. |
| `python_file_write_detection.py` | `detect_python_file_edit()` — **detection only** (no extraction) for inline Python that writes files (`python -c '... open(...'` or `python << EOF` heredoc). Parsing arbitrary Python is infeasible; the parser logs a coverage-gap warning. |
| `python_script_extractor.py` | Extracts `Edit`s from the canonical `open()/triple-quoted old_X,new_X/content.replace/write` Python idiom. See the python-script section below. |
| `tmp_file_extractor.py` | `head\|cat\|tail\|cp/mv` splice → `CHANGE_RANGE` edits. See the splice section below. |
| `__init__.py` | Re-exports only 4 symbols: `is_sed_command`, `sed_edits`, `is_git_reset_file_command`, `extract_git_reset_file_targets`. (All other helpers are imported by their full module path.) |

## sed (`sed_parsing.py`)

`sed_edits(bash_block, explanation_prefix)` splits a block into individual `sed -i` commands (bashlex with a manual line-based fallback), then per command splits the expression on top-level `;` (structurally aware of `/…/` addresses, `s…`/`y…` delimiter triples, and `{…}` brace depth), expands `/range/{cmd1;cmd2}` into separate edits, and parses each fragment. If any fragment fails to parse or is a freeform `a`/`i`/`c` command, it falls back to parsing the whole expression as one. `old_text` is kept **raw/escaped** (applier decides literal vs regex); only `new_text`/`a\i\c` text is unescaped (`_unescape_sed_replacement` for `s`; single-pass `_aic_text_unescape` for `a`/`i`/`c`). All sed edits set `best_effort=True`.

EditType mapping (verified against code):

| sed form | EditType |
|---|---|
| `s/old/new/` (unaddressed) | `REPLACE_ONCE_PER_LINE` (drops if `old` contains real `\n`) |
| `s/old/new/g` (unaddressed) | `REPLACE_MULTI_PER_LINE` |
| `Ns/old/new/` | `REPLACE_ONCE_AT_LINE` (`/g` → `REPLACE_MULTI_AT_LINE`) |
| `Ns/.*/new/` (whole-line) | `CHANGE_LINE`; `Ns/.*//` → `DELETE_LINE` |
| `N,Ms/old/new/` | `REPLACE_ONCE_PER_LINE_IN_RANGE` (`/g` → `REPLACE_MULTI_PER_LINE_IN_RANGE`) |
| `N,Ms/.*/new/` (whole-line) | `CHANGE_RANGE_PER_LINE`; `N,Ms/.*//` → `CHANGE_RANGE_PER_LINE` (empties lines) |
| `/pat/s/old/new/` | `REPLACE_ONCE_PER_LINE_PATTERN` (`pattern_address`) |
| `/p1/,/p2/s/old/new/` | `REPLACE_ONCE_PER_LINE_IN_RANGE` (`pattern_address_start`/`_end`) |
| `Na\text` | `INSERT_AFTER_LINE` (`starting_line=N+1`); `/pat/a\text` → `INSERT_AFTER_PATTERN` |
| `Ni\text` | `INSERT_BEFORE_LINE` (`starting_line=N`); `/pat/i\text` → `INSERT_BEFORE_PATTERN` |
| `Nc\text` | `CHANGE_LINE`; `N,Mc\text` → `CHANGE_RANGE`; `/p1/,/p2/c\text` → `CHANGE_PATTERN_RANGE` |
| `Nd` | `DELETE_LINE`; `N,Md` → `DELETE_RANGE` |
| `/pat/d` | `DELETE_PATTERN`; `/p1/,/p2/d` and `/pat/,+Nd` → `DELETE_PATTERN_RANGE` |
| `N{N;s/old/new/}` | `REPLACE_JOINED_NEXT` (range `N,M{…}` → one edit per pair, bottom-to-top) |
| `/A/{n;/B/d}` | `DELETE_PATTERN_NEXT` (`{N;…}` sets `delete_anchor=True`) |
| `N{/pat/d}` | `DELETE_LINE` (conditional) |
| `{N+;c\text}` | `CHANGE_RANGE` (line addr) / `CHANGE_PATTERN_RANGE` (pattern addr) with `n_count` |
| `N,M{Ns/.*/A/;Ms/.*/B/}` | `CHANGE_RANGE` (brace per-line whole-line replacement) |

`/start/,/end/{s/old/new/}` and `/start/,/end/{/pat/d}` unwrap the braces and delegate to the substitution / delete parsers. Multiple `DELETE_LINE` edits from one command are sorted descending by line so earlier deletes don't shift later ones. bashlex is skipped for heredoc `<<` (unless the block is a sed command — `<<` may be a C bitshift), for `\n`/`\t` inside double quotes, and for the `'"'"'` / `'\''` single-quote-escape idioms.

## patch-file (`patch_file_extractor.py`)

`extract_edits_from_patch_cmd(bash_block, patch_file_bodies, ...)` finds `patch`/`git apply` targets (`extract_patch_file_targets`), looks up the memoized heredoc body (`_lookup_patch_body`, tolerant of `./`, leading `/`, basename), and parses it via `parse_unified_diff`. Per-hunk edits → `REPLACE_PATCH_HUNK` with `MatchMode.EXACT`, `starting_line` = hunk's old-start, `is_dd_hunk=False`, `best_effort=True`; `new file mode` → `WHOLE_FILE_CREATE`; `deleted file mode` / `+++ /dev/null` → `WHOLE_FILE_DELETE`. When `user_response` (shell output) is supplied, `parse_patch_outcomes` honors per-hunk `Hunk #N FAILED` (dropped) and section-level fatal markers (`malformed patch`, etc. — all edits for that file dropped). Also exposes `is_valid_patch_command(block, bodies)`, `iter_heredoc_patch_targets` (filters heredoc captures to `/tmp/` or `.patch`/`.diff` paths), `is_patch_edit(edit)` (identifies patch-origin edits via the `"patch-file"` tag for the trajectory-level dedupe post-pass), and back-compat `parse_patch_failures`.

## python-script (`python_script_extractor.py`)

`extract_edits_from_python_run(bash_block, python_file_bodies, ...)` handles `python /tmp/X.py` (memo lookup) and inline `python << EOF` heredocs. `parse_python_replace_pairs` recognizes only the canonical idiom — `open(PATH,'r')→read`, triple-quoted `old_X`/`new_X` blocks, `content.replace(old_X,new_X)`, `open(PATH,'w')→write` — and emits one `EditType.REPLACE` (`EXACT`, `best_effort=True`) per replace call in document order (`before==after` pairs skipped). `/tmp/` targets are rejected. `re.sub` / readlines-loop idioms are deliberately not extracted. Also exposes `detect_python_run_cmd`, `extract_python_run_targets`, `extract_inline_python_bodies`, `iter_heredoc_python_targets` (`.py` filter).

## splice (`tmp_file_extractor.py`)

`extract_edits_from_splice(bash_block, tmp_file_bodies, ...)` detects three single-block `head | cat | tail | cp/mv` splice variants and emits one `CHANGE_RANGE` edit per splice (lines `head_N+1 .. tail_M-1` of FILE replaced by the middle scratch body; `before=""`, explicit `starting_line`/`ending_line`, `best_effort=True`). Also supports the piped head form `cat FILE | head -n N > DEST`. Variants: **A** scratch accumulator + `cp/mv` (`head_dest == tail_dest`, `/tmp/`), **B** `cp FILE FILE.bak` snapshot then writes directly to FILE (`head_dest == tail_dest == FILE`), **C** multi-`cat` concat into FILE (`head_dest != tail_dest`). `detect_splice_cmd` is the cheap gate. `iter_heredoc_tmp_targets` filters heredoc captures by **path location** (`/tmp/...`) rather than extension (splice middles use arbitrary extensions). `is_splice_edit(edit)` → `True` only when `edit_type == CHANGE_RANGE` **and** the `"splice"` token is in `explanation` — used by `MiniSweMessagesTrajectoryParser.parse()` to dedupe repeated splices on the same file across revisions.

## Design Notes

- Linux-tree path args are normalized via `normalize_linux_repo_path()` from `agent_adapter_base/utils.py`; external paths (via `is_linux_tree_path()`) are preserved raw for adapter-level stashing/remap.
- `StrReplaceEditorExtractor._find_all_invocations` iterates all subcommands in original order (not grouped by type).
- `sed_parsing` uses a sentinel-based approach in `_unescape_sed_replacement` for literal-backslash handling and drops GNU-BRE escapes (`\(`, `\|`, `\{`, …) from replacements.
- Consumers: adapters no longer call these helpers directly for dispatch — each command kind is wrapped by a `Statement` (`agent_adapter_base/statements/`, per-adapter subclasses in `<adapter>/statements/`). Historically: **SWE adapter** used `is_str_replace_editor_*`, `is_sed_command`, `is_rm_command`, `is_cp_command` for step classification; **mini-swe adapter** uses `is_sed_command`, `is_cat_heredoc`, `is_tee_heredoc`, `is_str_replace_editor_command` for block classification. Both delegate to the extractors for edit parsing.
