"""Karena SQLite queries against ``agent_patches`` / ``agent_configurations``."""
from __future__ import annotations

import sqlite3


def lookup_bug_id_for_agent_patch(
    conn: sqlite3.Connection, agent_patch_id: int
) -> str | None:
    """Resolve ``bugId`` from Karena SQLite for this ``agentPatchId``.

    Used as the **only** ``bugId`` source when loading with ``karena_db_path`` (DB is reference).
    Tries ``agentPatchId`` first, then ``trajectoryKey`` (``agent-patches/<id>/traj.json``).
    """
    cur = conn.execute(
        "SELECT bugId FROM agent_patches WHERE agentPatchId = ? LIMIT 1",
        (agent_patch_id,),
    )
    row = cur.fetchone()
    if row:
        return str(row[0])
    cur = conn.execute(
        """
        SELECT bugId FROM agent_patches
        WHERE trajectoryKey = ? OR trajectoryKey = ?
        LIMIT 1
        """,
        (
            f"agent-patches/{agent_patch_id}/traj.json",
            f"{agent_patch_id}/traj.json",
        ),
    )
    row = cur.fetchone()
    return str(row[0]) if row else None


def karena_db_agent_patch_id_bounds(
    conn: sqlite3.Connection,
) -> tuple[int | None, int | None]:
    """Return ``(min, max)`` ``agentPatchId`` in ``agent_patches`` (for diagnostics)."""
    row = conn.execute(
        "SELECT MIN(agentPatchId), MAX(agentPatchId) FROM agent_patches"
    ).fetchone()
    if not row or row[0] is None:
        return None, None
    return int(row[0]), int(row[1])


def agent_patch_ids_for_agent_family(
    conn: sqlite3.Connection, *, agent_family: str
) -> set[int]:
    """Return ``agentPatchId`` set for rows whose ``agent_configurations.agent`` matches.

    Args:
        conn: Open Karena SQLite connection.
        agent_family: Value from ``agent_configurations.agent`` (e.g. ``swe-agent``).
    """
    rows = conn.execute(
        """
        SELECT ap.agentPatchId
        FROM agent_patches ap
        JOIN agent_configurations ac ON ac.agentConfigId = ap.agentConfigId
        WHERE ac.agent = ?
        """,
        (agent_family,),
    ).fetchall()
    return {int(r[0]) for r in rows if r and r[0] is not None}
