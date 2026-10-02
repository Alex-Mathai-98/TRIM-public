"""Cached HuggingFace SWE-bench dataset access.

AIDEV-NOTE: Replaces four variants that hit three different datasets with three different
caching strategies. See z-cpl-44 step 1.
"""
from __future__ import annotations

import threading
from typing import Iterable

DATASET = "princeton-nlp/SWE-bench_Verified"

# AIDEV-NOTE: keyed BY DATASET. run_swebench_postprocessing.py used a single global because
# it hardcoded one dataset; parameterising without keying the cache would hand the second
# caller the first dataset's rows.
_ROWS: dict[str, dict[str, dict]] = {}
_LOCK = threading.Lock()


def hf_rows(dataset: str = DATASET) -> dict[str, dict]:
    """Cached ``{instance_id: row}``. Body lifted from run_swebench_postprocessing.py:76-83.

    AIDEV-NOTE: ``load_dataset()`` runs while holding the lock, as it did there —
    deliberate, so N worker threads arriving together perform one load, not N.
    ``split="test"`` is the only split Verified has; a caller restoring the full dataset
    (see base_commits_for's error) would need dev/train too.
    """
    with _LOCK:
        if dataset not in _ROWS:
            from datasets import load_dataset
            ds = load_dataset(dataset, split="test")
            _ROWS[dataset] = {r["instance_id"]: r for r in ds}
    return _ROWS[dataset]


def base_commits_for(
    instance_ids: Iterable[str], dataset: str = DATASET
) -> dict[str, str]:
    """Resolve every id up front, or raise naming the ones that failed.

    AIDEV-NOTE: Resolving the whole batch in one call is the point. The code this replaces
    looked one id up per instance, re-streaming the dataset each time, from inside the
    per-repo lock. Call this once before the worker pool starts.
    """
    ids = list(instance_ids)
    rows = hf_rows(dataset)
    missing = sorted(i for i in ids if i not in rows)
    if missing:
        raise LookupError(
            f"{len(missing)} instance(s) have no row in {dataset}: "
            f"{missing[:10]}{' …' if len(missing) > 10 else ''}\n"
            "All four call sites were unified onto SWE-bench_Verified (z-cpl-44 step 1). "
            "run_swebench_minimization.py previously used the full princeton-nlp/SWE-bench "
            "(~2,300 rows, test+dev+train) and WOULD have resolved these ids. If they are "
            "legitimate, that unification was wrong for this call site: restore the full "
            "dataset here rather than dropping the instances."
        )
    return {i: rows[i]["base_commit"] for i in ids}
