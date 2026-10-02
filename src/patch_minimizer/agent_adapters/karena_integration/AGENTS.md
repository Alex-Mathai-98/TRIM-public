# Karena Integration

> **Purpose:** Integration point (not the main product) for consuming an external **kArena / Karena** export when loading agent trajectories into the minimizer. Provides read-only SQLite queries and metadata/layout helpers to resolve a trajectory's `bugId` and `agentPatchId` — used only when a run is Karena-backed (`karena_db_path` set or an `agent-patches.zip` layout). Deliberately isolated from any specific agent adapter so any adapter can reuse it without importing `swe_agent_adapter`.

---

## File Overview

| File | Purpose |
|---|---|
| `db.py` | Direct SQLite queries against Karena's `agent_patches` / `agent_configurations` tables. Functions: `lookup_bug_id_for_agent_patch`, `karena_db_agent_patch_id_bounds`, `agent_patch_ids_for_agent_family`. |
| `metadata.py` | JSON metadata reading + directory layout helpers: `read_agent_patch_metadata`, `resolve_agent_patches_root`, `infer_bug_id_from_agent_patches_path`. |
| `__init__.py` | Re-exports all public functions from `db.py` and `metadata.py`. |

## `bugId` Lookup Fallback Chain

When loading trajectories, `bugId` resolution follows this priority:

1. **Karena DB** (when `karena_db_path` set): `lookup_bug_id_for_agent_patch(conn, agent_patch_id)` — tries `agentPatchId` column first, then `trajectoryKey` pattern match.
2. **JSON metadata**: `read_agent_patch_metadata(path)` reads `bugId` / `bug_id` from the trajectory JSON.
3. **Directory name**: `infer_bug_id_from_agent_patches_path(path, root)` checks for 40-char hex folder names matching Syzbot bug hashes.

## `agentPatchId` Resolution

`read_agent_patch_metadata()` tries multiple sources in order:

1. JSON field: `agentPatchId` or `agent_patch_id`
2. Numeric filename stem (e.g., `43840.json`)
3. Numeric parent directory name (e.g., `43840/traj.json`)
4. Sibling `agent_patch_id.txt` file

## Zip Layout Handling

Karena exports produce `agent-patches.zip` which may unzip with a nested structure:

```
agent-patches/
  agent-patches/          ← nested (double-wrapped)
    43840/
      traj.json
```

`resolve_agent_patches_root()` detects and resolves this by checking if `<path>/agent-patches/<numeric_dirs>` exists.

## Key Patterns

- **DB is optional.** All functions in `db.py` take a `sqlite3.Connection` — callers manage connection lifecycle. When no DB is available, the system falls back to JSON/path heuristics.
- **Read-only queries.** This module only reads from Karena SQLite — no writes, no schema changes.
- **Bug ID format.** Bug IDs are 40-char hex hashes (git commit style). Directory regex: `^[0-9a-f]{40}$`.
