"""SWE-bench instance-id helpers."""
from __future__ import annotations


def instance_to_repo_prefix(instance_id: str) -> str:
    """``django__django-12345`` -> ``django__django`` (the clone directory name).

    AIDEV-NOTE: Lives in utils/, not cli/common.py, so cli/traj_cli.py can use it without
    importing cli/common.py — that would create a common <-> traj_cli import cycle. See
    z-cpl-44 step 2.
    """
    return instance_id.rsplit("-", 1)[0]
