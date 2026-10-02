"""Env bootstrap for the SWE-agent runner.

AIDEV-NOTE: ``bootstrap_env`` was ``_bootstrap_env_like_sample`` in
run_swe_minimization.py. It stays its own module because run_swe_minimization.py must call
it *before* its heavy imports — it sets sys.path and the KBENCH_PATH / AGENT_PATCHES_DIR /
KARENA_DB_PATH vars those imports expect.

AIDEV-NOTE: ``run_parallel_path`` used to live here. Both modes now go through
agent_patches.run(), which builds its pool with cli/common.build_linux_pool_paths().
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

def bootstrap_env() -> None:
    """Mirror ``sample_minimize_edits.py``: paths from repo layout; ``setdefault`` keeps exported env.

    AIDEV-NOTE: Infer Kernel_Agent repo root from ``__file__`` so runs work without ``dev.env``.
    """
    _here = Path(__file__).resolve()
    # .../Kernel_Agent/src/Kernel_Agent/tools/.../example_code/run_swe_minimization.py
    # AIDEV-NOTE: Walk up to find the project root (dir containing src/patch_minimizer).
    # Was a hardcoded parents[6], which silently resolved to "/" after this file moved.
    for _parent in _here.parents:
        if (_parent / "src" / "patch_minimizer").is_dir():
            default_kagent = str(_parent)
            break
    else:
        raise RuntimeError(f"Cannot locate project root (src/patch_minimizer) above {_here}")
    default_base = str(Path(default_kagent).parent)

    os.environ.setdefault("KAGENT_PATH", default_kagent)
    os.environ.setdefault("BASE_PATH", default_base)
    os.environ.setdefault("KBENCH_PATH", os.path.join(os.environ["BASE_PATH"], "random_test_processed"))
    os.environ.setdefault(
        "AGENT_PATCHES_DIR",
        os.path.join(os.environ["KAGENT_PATH"], "agent-patches"),
    )
    os.environ.setdefault(
        "KARENA_DB_PATH",
        os.path.join(os.environ["KAGENT_PATH"], "karena.db.backup_20260125_203200"),
    )
    os.environ.setdefault("KBDR_RUNNER_API_BASE_URL", "https://your-kgym-api-endpoint.example.com")
    os.environ.setdefault("KBDR_BUCKET_NAME", "your-gcs-bucket")
    os.environ.setdefault("PROJECT_ID", "your-gcp-project-id")
    # AIDEV-NOTE: Same as sample_minimize_edits: insert BASE_PATH for KBDr_Runner imports.
    sys.path.insert(0, os.environ["BASE_PATH"])
    _src = os.path.join(os.environ["KAGENT_PATH"], "src")
    if os.path.isdir(os.path.join(_src, "Kernel_Agent")):
        sys.path.insert(0, _src)
