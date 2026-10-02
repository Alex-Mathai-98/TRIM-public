# SWE-bench Frontend Utils

> **Purpose:** Domain helpers shared by `cli/`, `environment.py` and `example_code/`.
> Distinct from `cli/common.py`, which is CLI orchestration infra (logging, summaries,
> repo pooling) used only by `cli/` and the `example_code/` runners.

## Layering

Lowest layer inside `frontends/swebench/`. Imports nothing else from this package, so
anything here is safe to use from `cli/`, `environment.py` and `example_code/` alike.

| File | Contents |
|---|---|
| `env.py` | `bootstrap_env()` — sets `KAGENT_PATH`/`BASE_PATH` from the repo layout. **Raises** if the project root is not found; the two copies it replaced fell back to a hardcoded `parents[3]`, which resolved to the wrong directory after a move. |
| `diff_utils.py` | `split_patch_into_files`, `patch_file_paths`, `strip_index_lines`, `hunk_count`, `plus_minus_counts`. |
| `instance_ids.py` | `instance_to_repo_prefix` — `django__django-12345` → `django__django`. |
| `hf_dataset.py` | `hf_rows(dataset)` (cached, thread-safe, keyed by dataset) and `base_commits_for(ids)` (batch resolve, raises naming every missing id). |

## Invariants

- **`patch_file_paths` returns an ordered list, never a set.** It replaced three helpers,
  two of which returned sets; callers that need one wrap with `set(...)`. Order is
  observable at `filter_to_submission_paths`.
- **`hf_dataset.DATASET` is `SWE-bench_Verified`.** All four former call sites now share it;
  one previously used the full `princeton-nlp/SWE-bench`. `base_commits_for` raises a
  `LookupError` that explains how to undo the switch if an id fails to resolve.
- **`hf_rows` holds the lock across `load_dataset`** so concurrent workers perform one load,
  not N. Do not narrow the lock to the dict write.
- **`plus_minus_counts` counts only inside hunks.** `reconstruct_clean_agent_patch.py` has a
  per-block counter that omits that guard and returns a combined total; the two are
  deliberately NOT merged, because merging could shift the reported compaction table.
