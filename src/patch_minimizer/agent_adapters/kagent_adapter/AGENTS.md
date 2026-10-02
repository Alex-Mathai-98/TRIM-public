# CrashFixer (Kernel Agent) Adapter

> **Purpose:** Convert saved CrashFixer `.pkl` files (LLMResponseHistory decision trees)
> into the canonical `patch_list` format used by the minimization pipeline.

Unlike the JSON-based adapters that go through `TrajectoryParser` + `parser_registry`,
this adapter handles binary `.pkl` files containing a networkx DiGraph — a fundamentally
different input format. It follows the *spirit* of the adapter pattern (agent format →
`patch_list`) without subclassing the JSON-specific `TrajectoryParser` ABC.

---

## File Overview

| File | Purpose |
|---|---|
| `adapter.py` | Three public functions: `load_all_trajectories()` (workhorse), `load_trajectory()` (single, indexed), `count_trajectories()`. Handles: `.pkl` loading → `SolutionPathAnalyzer` → `global_edit_idx` stamping → `patch_list`. |
| `solution_path_analyzer.py` | `SolutionPathAnalyzer` — stateless helpers over an `LLMResponseHistory` tree. Walks the decision tree to find successful paths and extract `(parent_git_diff, valid_edits)` sequences. Moved here from `frontends/kernel/utils/` because it is trajectory parsing, not a frontend concern. |
| `__init__.py` | Re-exports the three public functions from `adapter.py`. |

## Public API

```python
from patch_minimizer.agent_adapters.kagent_adapter import (
    load_all_trajectories,  # → (succ_edit_paths, trajectory_names)
    load_trajectory,        # → (patch_list, trajectory_name) for index i
    count_trajectories,     # → int
)
```

All three take `(folder_path, bug_id, model_id, base_commit)`. The caller is responsible for
obtaining `base_commit` via `get_base_commit()` from `benchmark_utils.kernel.util_funcs`.

## Dependencies

- `benchmark_utils.kernel.util_funcs.instantiate_llm_response_history` — `.pkl` loading
- `benchmark_utils.kernel.llm_response_history.LLMResponseHistory` — the tree type
- `core.edit.Edit` — for type annotations

No imports from `strategies/` or `frontends/` — this adapter sits below both in the layer model.
