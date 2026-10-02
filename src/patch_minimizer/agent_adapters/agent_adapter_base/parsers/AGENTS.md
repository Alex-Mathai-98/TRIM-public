# Parser Base — ABC, Registry, and Core Types

> Shared foundation for all trajectory-to-patch-list adapters. No format-specific code lives here.

## File Overview

| File | Purpose |
|---|---|
| `trajectory_types.py` | `Revision` and `CandidateTrajectory` dataclasses — the universal shape between parsers and the minimizer. `Revision` has `revision_index`, `start_step`, `end_step`, `edits: list[Edit]`, and `feedback_commands: list[dict[str, Any]]` (each entry `{"step_index": int, "command": str}` — the test commands run after a revision's edits). `CandidateTrajectory` holds `revisions: list[Revision]` and `success_step: int`. |
| `trajectory_parser.py` | `TrajectoryParser` ABC — the contract every concrete adapter parser implements, plus the shared `parse_trajectory` template method. |
| `parser_registry.py` | `ParserRegistry` (ordered list of parser classes), the `parser_registry` singleton instance, `parse_trajectory_payload` (dispatch), and `UnknownTrajectoryFormat` exception. |
| `__init__.py` | Re-exports `ParserRegistry`, `TrajectoryParser`, `UnknownTrajectoryFormat`, `parse_trajectory_payload`, `parser_registry`. |

## `TrajectoryParser` ABC

| Member | Kind | Default | Purpose |
|---|---|---|---|
| `payload_keys()` | classmethod, **abstract** | — | Top-level JSON keys this parser reads. |
| `can_parse(data)` | classmethod | accept if any `payload_keys()` value is present & truthy | Discriminates whether this parser owns a payload. Subclasses override for version/schema checks. |
| `parse(data, *, source_label)` | **abstract** | — | Parse the full top-level dict → `list[CandidateTrajectory]`. |
| `step_requests_feedback(step)` | hook | `False` | Is `step` a kernel-feedback boundary (splits revisions). |
| `is_edit_step(step)` | hook | `False` | Structural check: does `step` carry an edit shape. |
| `is_successful_edit_step(step)` | hook | `False` | Did the edit in `step` succeed (usually via observation text). |
| `parse_edit_step(step, idx, extractor)` | hook | `[]` | Return the successful `Edit`s in one step. |
| `is_undo_step(step)` | hook | `None` | Normalized filename if `step` is an undo, else `None`. |
| `is_successful_undo_step(step)` | hook | `True` | Did an undo succeed. |
| `perform_undo_step(step, bucket, revisions, *, source_label, step_idx)` | hook | `False` | Remove affected edit(s) for an undo; return whether it acted. |
| `is_reset_all_step(step)` | hook | `False` | Does `step` reset the repo (e.g. `git reset --hard`). |
| `is_stash_save_step(step)` / `is_stash_restore_step(step)` | hook | `False` | Detect stash save (temp save + clear) / restore (e.g. `git stash pop`). |
| `_perform_stash_save` / `_perform_stash_restore` | internal | — | Push/pop `(bucket, revisions, _last_diff)` on `self._stash_stack`. |
| `parse_trajectory(trajectory, extractor, *, source_label)` | template | — | Shared walk loop (see below). Returns 0 or 1 `CandidateTrajectory`. |

### `parse_trajectory` loop order

For each `step` in `trajectory` (in order):
1. `step_requests_feedback` → `flush()` current bucket into a `Revision`; record the step's `action` as a `feedback_commands` entry on the **preceding** revision (captures consecutive test runs).
2. `perform_undo_step` → if it acts, continue.
3. `is_stash_save_step` → `_perform_stash_save` (carries each cleared revision's `feedback_commands` into `self._carried_feedback`, pushes state, clears).
4. `is_stash_restore_step` → `_perform_stash_restore` (pops state; no-op if stack empty).
5. `is_reset_all_step` → carry feedback forward, then clear bucket + revisions.
6. otherwise `parse_edit_step` → extend the bucket; track `bucket_start`/`bucket_end`/`last_step`.

After the loop: final `flush()`, then `_carried_feedback` is de-duped (by command string) and attached to the last revision; empty-after-undo revisions are dropped and `revision_index` re-numbered. Returns `[]` if no revisions survive, else one `CandidateTrajectory(revisions, success_step=last_step)`.

> Note: the stash stack, `_carried_feedback`, and `_last_diff` are established inside `parse_trajectory`; subclasses that want stash carry-forward rely on this loop rather than reimplementing it.

## Registry Pattern

```
parser_registry = ParserRegistry()          # module-level singleton

# adapters, on import:
parser_registry.register(YourParser)         # dedup; also usable as class decorator

# dispatch:
parse_trajectory_payload(data, source_label)
  → parser_registry.match(data)              # first cls whose can_parse(data) is True, else None
  → cls().parse(data, source_label=...)      # -> list[CandidateTrajectory]
  → raises UnknownTrajectoryFormat when match() returns None
```

`ParserRegistry` is an ordered collection (**first registered = first asked**). It also supports `clear()` (tests), `__iter__`, and `__len__`. The registry itself has no hardcoded knowledge of any format.

## Adding a New Adapter

1. Subclass `TrajectoryParser`; implement `payload_keys()` and `parse()` (override the per-step hooks and reuse `parse_trajectory` if your format is step-based).
2. Optionally subclass `ActionEditExtractor` (in `../edit_parsing_helpers/`) if the agent uses a novel edit format.
3. Call `parser_registry.register(YourParser)` at the bottom of your `parsers.py`.
4. Ensure your adapter package is imported (import side-effect) before `load_trajectory_file` is called, and set `agent_adapter_base.utils.REPO_TREE_PREFIX` in your package `__init__`.
