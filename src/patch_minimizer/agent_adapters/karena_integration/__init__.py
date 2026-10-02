"""Karena SQLite + agent-patches metadata helpers.

Isolated from any specific agent adapter so non-SWE adapters can reuse these
for Karena-backed runs without importing ``swe_agent_adapter``.
"""
from patch_minimizer.agent_adapters.karena_integration.db import (
    agent_patch_ids_for_agent_family,
    karena_db_agent_patch_id_bounds,
    lookup_bug_id_for_agent_patch,
)
from patch_minimizer.agent_adapters.karena_integration.metadata import (
    infer_bug_id_from_agent_patches_path,
    read_agent_patch_metadata,
    resolve_agent_patches_root,
)

__all__ = [
    "agent_patch_ids_for_agent_family",
    "karena_db_agent_patch_id_bounds",
    "lookup_bug_id_for_agent_patch",
    "infer_bug_id_from_agent_patches_path",
    "read_agent_patch_metadata",
    "resolve_agent_patches_root",
]
