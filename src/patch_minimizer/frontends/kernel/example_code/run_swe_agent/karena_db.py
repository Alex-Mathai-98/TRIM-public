"""Karena SQLite lookups for the agent-patches path.

AIDEV-NOTE: Moved verbatim from run_swe_minimization.py (lines 107-161). Only the
agent-patches runner uses these; the single-trajectory path never touches the DB.
"""
from __future__ import annotations

import sqlite3


def infer_swe_agent_config_id(conn: sqlite3.Connection) -> int:
    """Infer Karena agentConfigId for swe-agent-c-unlimited.

    AIDEV-NOTE: Prefer exact name; LIKE ``%swe-agent-c-unlimited%`` matches ``mini-swe-agent-…``
    first by id and breaks the swe-agent / evaluations join.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT agentConfigId
        FROM agent_configurations
        WHERE agentConfigName = 'swe-agent-c-unlimited'
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if row is None:
        cur.execute(
            """
            SELECT agentConfigId
            FROM agent_configurations
            WHERE agentConfigName LIKE '%swe-agent-c-unlimited%'
              AND agentConfigName NOT LIKE 'mini-%'
            ORDER BY agentConfigId
            LIMIT 1
            """
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError("Could not infer swe-agent-c-unlimited agentConfigId from Karena DB")
    return int(row[0])


def agent_patch_has_kgym_evaluation(
    conn: sqlite3.Connection,
    *,
    agent_config_id: int,
    agent_patch_id: int,
    kGymEvaluation: str,
) -> bool:
    """True if this ``agentPatchId`` row exists for ``agentConfigId`` with the given evaluation."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1
        FROM agent_patches ap
        JOIN patch_crash_resolution_evaluations pe ON pe.agentPatchId = ap.agentPatchId
        WHERE ap.agentPatchId = ?
          AND ap.agentConfigId = ?
          AND pe.kGymEvaluation = ?
        LIMIT 1
        """,
        (agent_patch_id, agent_config_id, kGymEvaluation),
    )
    return cur.fetchone() is not None
