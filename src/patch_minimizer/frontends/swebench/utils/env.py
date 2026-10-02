"""Environment bootstrap for the SWE-bench scripts."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def bootstrap_env() -> None:
    """Set ``KAGENT_PATH`` / ``BASE_PATH`` from the repo layout; exported vars win.

    AIDEV-NOTE: Walk up to the project root (the dir containing ``src/patch_minimizer``)
    and **raise** if it is not found. The two copies this replaces fell back to
    ``_here.parents[3]``, which silently resolved to the wrong directory once the file
    moved. Kernel hit the same bug with ``parents[6]`` and fixed it the same way — see
    ``frontends/kernel/example_code/run_swe_agent/utils.py``.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "src" / "patch_minimizer").is_dir():
            root = str(parent)
            break
    else:
        raise RuntimeError(f"Cannot locate project root (src/patch_minimizer) above {here}")

    os.environ.setdefault("KAGENT_PATH", root)
    os.environ.setdefault("BASE_PATH", str(Path(root).parent))
    # AIDEV-NOTE: sys.path is NOT touched. Callers reach this module through the package,
    # so patch_minimizer is already importable by the time bootstrap_env() runs. Scripts
    # need PYTHONPATH=src (or an editable install) — see z-cpl-44 behaviour change 2.
    _src = os.path.join(root, "src")
    if _src not in sys.path and os.path.isdir(os.path.join(_src, "patch_minimizer")):
        sys.path.insert(0, _src)
