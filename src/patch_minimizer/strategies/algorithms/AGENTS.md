# Minimization Algorithms

> **Purpose:** Core algorithms for hierarchical patch minimization (node -> file -> edit). Holds the shared base class, the algorithm-agnostic greedy loop, the 1-minimal fixed-point wrapper, and the candidate tester that all granularity levels share.

---

## File Overview

| File | Purpose |
|---|---|
| `solution_minimization_base.py` | `SolutionMinimizationBase` -- base class owning repo, rule engine, patch tester. Provides `_generate_minimized_diff()` and `_save_minimized_patch()`. Parent of NodeMinimizer and EditMinimizer |
| `greedy_removal.py` | `unrestricted_greedy_removal()` -- single reverse-pass greedy removal. Handles KEEP/DROP/DEFER/RESOLVE_GROUP decisions from rule engine. Core workhorse used at all granularity levels |
| `one_minimal_guarantee.py` | `one_minimal_guarantee()` -- fixed-point wrapper around greedy removal. Repeats until convergence to guarantee 1-minimality (no single element removable). Empirically ~1 pass |
| `patch_candidate_tester.py` | `PatchCandidateTester` -- owns the lock->apply->diff->feedback loop. Shared by all algorithms. Tracks feedback_stats for reporting |
| `__init__.py` | Re-exports PatchCandidateTester, unrestricted_greedy_removal, one_minimal_guarantee, NodeMinimizer, EditMinimizer |

## Subdirectories

| Directory | Purpose |
|---|---|
| `node/` | Node-level minimization (coarsest granularity). Runs first; surviving `Node`s feed edit-level |
| `edit/` | Edit-level minimization (file -> edit -> 1-minimal) with coupling support. Consumes the surviving nodes |

## Hierarchical Pipeline

```
NodeMinimizer.find_independent_nodes  →  minimize_nodes   (node level: suffix → subsequence → 1-minimal)
                                                │  surviving List[Node]
                                                ▼
EditMinimizer.find_independent_edits  →  minimize_nodes then minimize_edits_from_nodes
                                             (file level → edit level → 1-minimal)
```

`EditMinimizer` internally runs the full node → file → edit chain; `NodeMinimizer` stops after the node level. Both are dispatched by `sol_minimize.py` via `minimize_at` ("node" or "edit").

## Key Patterns / Design Decisions

- **Hierarchical minimization**: node level (coarse) -> file level (intermediate) -> edit level (fine). File-level and edit-level both reuse `unrestricted_greedy_removal`; node level uses its own suffix/subsequence phases plus the same greedy loop during refinement.
- **Granularity tracking**: `PatchCandidateTester._granularity` is set by each phase ("node", "file", "edit") for feedback_stats keys.
- **Algorithm-agnostic greedy loop**: `unrestricted_greedy_removal` delegates all KEEP/DROP logic to a pluggable rule engine.
- **`SolutionMinimizationBase`** delegates repo ops to `RepoPatchManager` and feedback state to `PatchCandidateTester`.
- **Result caching**: `PatchCandidateTester` optionally caches `try_candidate` results keyed by `frozenset` of `global_edit_idx`. Same edit combination = same kernel outcome, avoiding redundant tests. Controlled by `use_result_cache` (default `True`), threaded from Hydra config through the entire call chain. Cache hits are tracked separately via `_cache_hit` suffix keys (e.g. `greedy_removal_edit_no_crash_cache_hit`) and do NOT increment regular feedback counters — `total_feedbacks` reflects only actual kernel tests.
