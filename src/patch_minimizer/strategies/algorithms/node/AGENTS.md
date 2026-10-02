# Node-Level Minimization

> **Purpose:** Coarsest granularity of hierarchical minimization — operates on `Node` objects (groups of semantically-related edits). Runs before file-level and edit-level passes.

---

## File Overview

| File | Purpose |
|---|---|
| `node_minimizer.py` | `NodeMinimizer` — thin orchestrator inheriting `SolutionMinimizationBase`. Entry point: `find_independent_nodes()`. Wraps raw `(parent_diff, List[Edit])` tuples into `Node` objects and delegates to `minimize_nodes`. |
| `minimize_nodes.py` | `minimize_nodes()` — core three-phase pipeline: Phase 1 (suffix) → Phase 2 (subsequence) → Refinement (1-minimal). Records surviving node indices in `feedback_stats` after each phase. |
| `find_minimum_substring.py` | `find_minimum_substring()` — Phase 1: backward elimination that grows a contiguous suffix from the end, keeping the best (smallest `total_lines`) suffix that still fixes the bug. Each candidate is judged by `rule_engine.decide(...)`; a `DROP` records the suffix as the new best, and `decision.stop_search` breaks the loop. Returns the best suffix, or the full list if none improved. |
| `find_minimum_subsequence.py` | `find_minimum_subsequence()` — Phase 2: greedy removal of intermediate nodes (keeps first & last). Walks interior indices from `len-2` down to `1`, deep-copies the range with one node nulled, tests, and drops on a `DROP` decision. |
| `__init__.py` | Re-exports: `Node`, `find_minimum_substring`, `find_minimum_subsequence`, `minimize_nodes`, `NodeMinimizer`. |

## Three-Phase Pipeline

```
Phase 1: find_minimum_substring     → shortest suffix that fixes the bug
Phase 2: find_minimum_subsequence   → drop unnecessary interior nodes (optional, enabled by phase_2=True)
Refinement: one_minimal_guarantee   → repeat greedy removal until no single node is removable (1-minimal)
```

- **Base case.** If there is exactly 1 node, all phases are skipped and every `node_list_after_*` key is stamped with the single surviving index (`node_refinement_passes=0`).
- **Phase 1** always runs (for >1 node). Builds the suffix incrementally from the end; a baseline is established first via `feedback_aggregator.reset_baseline(total_lines)`, and `total_lines` acts as the tiebreaker for which suffix is "best".
- **Phase 2** only runs if `phase_2=True` and there are >2 surviving nodes.
- **Refinement** only runs if `refinement=True` (default) and >1 node survives. Uses `one_minimal_guarantee()` from the parent `algorithms/` package, which returns `(surviving, num_passes)`; `num_passes` is recorded as `node_refinement_passes`.

## Relationship to Edit-Level Minimization

Node-level runs first. Its output (surviving `Node` objects) feeds into edit-level minimization (`edit/`), which operates on individual `Edit` objects within each surviving node. The hierarchy is:

```
Nodes (coarse) → Files (intermediate, within edit/) → Edits (fine, within edit/)
```

## Key Patterns

- **Uniform signature.** `find_independent_nodes()` mirrors `EditMinimizer.find_independent_edits()` so `ToolSolnMinimize` can dispatch either without special-casing.
- **`feedback_stats` tracking.** `minimize_nodes` records `nodes_original_count`, `node_list_original`, and surviving-index lists after each phase (`node_list_after_phase1`, `node_list_after_phase2`, `node_list_after_refinement`) plus `node_refinement_passes`, for post-run analysis. These live on `patch_tester.feedback_stats` (delegated from `SolutionMinimizationBase.feedback_stats`).
- **Node wrapping.** Raw `patch_list` tuples are wrapped into `Node(edits=edits, global_node_idx=i)` — the index is stable through all phases and used for tracking.
