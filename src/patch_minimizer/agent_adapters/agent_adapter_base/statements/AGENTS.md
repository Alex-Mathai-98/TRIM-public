# Statements (shared)

> **Purpose:** The `Statement` abstraction — one class per command kind an agent can run
> (`rm`, `cp`, `sed -i`, `str_replace_editor`, `undo_edit`, `git checkout <f>` …). A statement
> bundles *detection*, *success rule* and *effect* so parsers dispatch over an ordered list
> instead of hand-written `if/elif` chains.
>
> **Scope:** ABCs + adapter-agnostic parsing bases. Adapter-specific subclasses (which add the
> success rule) live in `<adapter>/statements/`. #architecture

---

## 1. File overview

| File | Contents |
|---|---|
| `statement.py` | `Statement` (produces edits) and `RevertStatement` (undoes edits) ABCs, sharing `_StatementCommon`. |
| `rm_statement.py` | `RMStatementBase` — parses `targets` once via `extract_rm_targets`. |
| `cp_statement.py` | `CpStatementBase` — parses `(source, dest)` `pairs` once via `extract_cp_pairs`. |
| `sed_statement.py` | `SedStatementBase` — keeps raw command; `to_edits` → `sed_edits`. |
| `str_replace_editor_statement.py` | `StrReplaceEditorStatementBase` — keeps raw command; `to_edits` → `StrReplaceEditorExtractor`. |
| `undo_edit_statement.py` | `UndoEditStatementBase` — `apply` pops the last edit on `target`. |
| `git_reset_file_statement.py` | `GitResetFileStatementBase` (parses `targets`) + `drop_edits_on_files` helper. |

## 2. Contract

| Member | Kind | Meaning |
|---|---|---|
| `matches(action, step)` | classmethod | Dispatch predicate — cheap, no parsing. |
| `is_edit(action, step)` | classmethod (`Statement`) | Edit-step gate; defaults to `matches`. Override when the gate differs (SWE `str_replace_editor`, mini-swe `patch`). |
| `from_action(action, step, *, step_idx=None)` | classmethod | `None` if no match; otherwise constructs and **parses once** (`_parse`). `step_idx` is stored as `self.step_idx`; only statements whose labels embed it (OpenHands file editor) need it. |
| `is_successful(parser)` | method | Adapter-specific success rule; may read parser state (`_last_diff`, `_run_error_ids`, `_obs_map`). |
| `to_edits(prefix)` | method (`Statement`) | Edits produced. Single argument everywhere; any other labelling input comes from construction. |
| `apply(bucket, revisions, *, parser, source_label, step_idx)` | method (`RevertStatement`) | In-place removal; returns the `perform_undo_step` result. Detection-only statements (reset-all, stash) don't implement it. |

`action` = the payload the adapter dispatches on: SWE `step["action"]`, OpenHands
`args.command` (shell string, or the file-editor sub-command name), mini-swe one bash block.

## 3. Parse-once rule (decision Q3)

- **Simple commands store parsed fields**: `RMStatementBase.targets`, `CpStatementBase.pairs`,
  `GitResetFileStatementBase.targets`, `UndoEditStatementBase.target`.
- **Complex commands keep the raw text** and call the existing helper in `to_edits` (sed,
  str_replace_editor, heredocs, patch) — the helpers in `edit_parsing_helpers/` stay the
  single source of truth for their grammars.
- Parsing happens inside the parser's hooks, so `normalize_linux_repo_path` sees the
  `REPO_TREE_PREFIX` the parser set. Never build statements at import time.

## 4. Adding a statement

1. Put adapter-agnostic parsing in a `*StatementBase` here (or reuse a helper).
2. Subclass in `<adapter>/statements/`, add `is_successful` (and `is_edit` if needed).
3. Insert it into that adapter's ordered `EDIT_STATEMENTS` list at the precedence the old
   code had — order is behaviour (first-match for SWE/OpenHands, all-match for mini-swe).
4. Run the parsing benchmarks (`tests/run_parsing_benchmarks.sh`); changes here are shared,
   so run **all** suites.
