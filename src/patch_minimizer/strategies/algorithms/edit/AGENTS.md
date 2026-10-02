# Edit-Level Minimization

> **Purpose:** Fine-grained patch minimization at the file and individual-edit level. `EditMinimizer` runs the full hierarchy (node -> file -> edit -> 1-minimal); it first invokes node-level minimization, then consumes the surviving `Node`s here.

---

## File Overview

| File | Purpose |
|---|---|
| `edit_minimizer.py` | `EditMinimizer` -- thin orchestrator inheriting `SolutionMinimizationBase`. `find_independent_edits()` runs the full node->file->edit pipeline: sets granularity `"node"`, wraps raw `(parent_diff, List[Edit])` tuples into `Node`s, records `edit_list_original`, calls `minimize_nodes`, then `minimize_edits_from_nodes`, then generates/saves the minimized diff |
| `minimize_edits_from_nodes.py` | `minimize_edits_from_nodes()` -- core edit-level pipeline: flatten surviving `Node`s to a flat `List[Edit]` (Node->Edit boundary), file-level removal, coupling setup, edit-level greedy removal, then 1-minimal refinement. Returns `(edit_minimized: List[List[Edit]], edit_count_metadata: dict)` |
| `minimize_at_file_level.py` | `minimize_at_file_level()` -- groups edits by `filename`, applies `unrestricted_greedy_removal` on file groups (each file = one unit), flattens back to `List[Edit]` sorted by `global_edit_idx`. Skips (returns input) if <=1 file |
| `get_coupling_config.py` | `get_edit_level_rule_engine_and_feedback_agg()` -- if `coupling_map.groups` is non-empty, builds a `CouplingNeuralEnvironment` and returns a new `FeedbackReceiver` (reusing the caller's `runtime_env`/`git_diff_env`) plus a `CouplingAwareRuleEngine`; otherwise returns the passed feedback aggregator and rule engine unchanged |
| `__init__.py` | Re-exports `minimize_at_file_level`, `get_edit_level_rule_engine_and_feedback_agg`, `minimize_edits_from_nodes`, `EditMinimizer` |

## Pipeline (within `minimize_edits_from_nodes`)

```
surviving List[Node]
   │  flatten (Node -> Edit boundary)
   ▼
file level:  minimize_at_file_level        (drop whole files; granularity="file")
   │  flatten to [[edit], [edit], ...]
   ▼
coupling setup: get_edit_level_rule_engine_and_feedback_agg  (no-op if no coupling)
   │
   ▼
edit level:  unrestricted_greedy_removal   (single reverse pass; granularity="edit")
   │
   ▼
refinement:  one_minimal_guarantee         (repeat until 1-minimal, if refinement=True and >1 edit)
```

## Key Patterns / Design Decisions

- **Three-phase edit minimization**: file-level (drop entire files) -> edit-level greedy removal -> 1-minimal refinement. File and edit levels both call `unrestricted_greedy_removal`.
- **Coupling integration**: between file-level and edit-level phases. `get_edit_level_rule_engine_and_feedback_agg` swaps in coupling-aware components only when `coupling_map.groups` is non-empty; otherwise the default runtime aggregator and rule engine are used for the edit-level pass.
- **Node->Edit boundary**: `minimize_edits_from_nodes` flattens `Node` wrappers into a flat `List[Edit]`; from that point all structures are edit-based (`List[List[Edit]]` after re-wrapping).
- **Edit ordering**: after file-level minimization, results are sorted by `global_edit_idx` to preserve original patch order.
- **Metadata tracking**: `edit_count_metadata` dict carries `edits_after_node_min`, `edits_after_file_min`, and `edit_refinement_passes`; `find_independent_edits` merges it into `feedback_stats`. Progress lists (`edit_list_original`, `edit_list_after_node_min`, `edit_list_after_file_min`, `edit_list_after_greedy`, `edit_list_after_refinement`) are written directly to `feedback_stats` for `minimization_summary.json`. When only 1 edit remains, the edit-level and refinement passes are skipped but the `edit_list_after_greedy`/`edit_list_after_refinement` keys are still stamped for consistency.
