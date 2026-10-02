# Evaluation — Agent Pass Loading

> **Purpose:** Bridge between Karena agent-patches exports and the minimization pipeline. Parses trajectory JSONs into `AgentPass` objects and provides bulk loading from `agent-patches/` directory trees.

---

## File Overview

| File | Purpose |
|---|---|
| `agent_pass.py` | `AgentPass` dataclass — one parsed run: `pass_id`, `bug_id`, `candidates`, `patch_lists`, and optionals `run_json_path`, `base_commit`, `kernel_base_url`, `agent_patch_id` (Karena `agent_patches.agentPatchId`). `__post_init__` asserts `len(candidates) == len(patch_lists)`. Helpers `num_candidates()` and `total_edits()`. Defines `PatchList = List[Tuple[None, List[Edit]]]`. |
| `agent_passes_store.py` | `AgentPassesStore` — in-memory registry, plus `AgentPatchesLoadStats` and the `_traj_json_supported` heuristic. |
| `__init__.py` | Re-exports: `AgentPass`, `AgentPassesStore`, `AgentPatchesLoadStats`, `PatchList`. |

## `AgentPassesStore`

State: `_passes: list[AgentPass]` (insertion order) and `_by_pass_id: dict[str, AgentPass]`.

| Method | Purpose |
|---|---|
| `add_pass_from_run_json(run_json_path, bug_id, pass_id=None, agent_patch_id=None)` | Parse one `traj.json` via `load_trajectory_file`, wrap into an `AgentPass`, add. Returns the pass, or `None` if not a file / no candidates with edits. Default `pass_id` = `agent_patch_<id>` when `agent_patch_id` given else `path.stem`. Duplicate `pass_id` → warns and returns the existing pass (no re-add). |
| `load_from_agent_patches_directory(directory, *, karena_db_path=None, db_agent_family=None)` | Bulk scan; returns `(added_passes, AgentPatchesLoadStats)`. |
| `get_all_passes()` | List copy of `_passes`. |
| `get_pass(pass_id)` | Lookup or `None`. |
| `get_patch_lists_for_pass(pass_id)` | The pass's `patch_lists` copy, or `None`. |
| `__len__` | Number of passes. |

### Bulk directory scan

Resolves the root via `resolve_agent_patches_root`, then prefers `rglob("traj.json")` (falls back to non-dot `*.json`). Layout:

```
agent-patches/
  ├── 43840/            ← agentPatchId
  │   └── traj.json
  ├── 43841/
  │   └── traj.json
  ...
```

Per file: skip if `_traj_json_supported` fails (first 64KB must contain `"events"`, `"trajectory"`, or `"messages"`); resolve `agentPatchId` via `read_agent_patch_metadata` (JSON field, numeric `<id>.json`, or `<agentPatchId>/traj.json`); optionally filter by `db_agent_family`; resolve `bugId` (DB or fallback); then `add_pass_from_run_json`. Every skip category is counted.

`AgentPatchesLoadStats` fields: `traj_files_scanned`, `skipped_unsupported_format`, `skipped_no_agent_patch_id`, `skipped_not_agent_family`, `skipped_not_in_db`, `skipped_no_bugid`, `skipped_no_edits`, `loaded_passes`.

## Karena DB Integration

When `karena_db_path` is provided (and exists):
- **DB is source of truth** for `bugId` — resolved only via `lookup_bug_id_for_agent_patch`; JSON-embedded `bugId` is ignored.
- Trajectories whose `agentPatchId` is absent from the DB are **skipped** (e.g. zip newer than DB snapshot); logged with the DB's max `agentPatchId`.
- Optional `db_agent_family` pre-filters to an agent type (e.g. `"swe-agent"`) via `agent_patch_ids_for_agent_family`.

Without a DB path, `bugId` falls back to the JSON `bugId` field, else `infer_bug_id_from_agent_patches_path` (a 40-char parent directory name). All DB helpers come from `agent_adapters.karena_integration`.

## `PatchList` Type

```python
PatchList = List[Tuple[None, List[Edit]]]
```

The `None` slot is the `parent_diff` position (always `None` here — parent diff is resolved separately during minimization setup).

## Key Invariants

- **`AgentPass` invariant:** `len(candidates) == len(patch_lists)` (asserted in `__post_init__`).
- **Dedup by `pass_id`:** a second add with an existing `pass_id` is refused with a warning.
- **Edits-required:** a pass with no candidates carrying edits is not added (returns `None`, counted as `skipped_no_edits`).
