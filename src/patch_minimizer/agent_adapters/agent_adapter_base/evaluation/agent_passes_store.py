"""In-memory store for agent trajectory passes — agent-agnostic bulk loader.

Walks ``agent-patches/`` directory trees, resolves metadata via Karena DB
or JSON/path heuristics, parses trajectories through the ``ParserRegistry``,
and holds results as ``AgentPass`` objects for batch minimization.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List

from patch_minimizer.agent_adapters.agent_adapter_base.evaluation.agent_pass import (
    AgentPass,
    PatchList,
)
from patch_minimizer.agent_adapters.agent_adapter_base.utils import (
    load_trajectory_file,
)
from patch_minimizer.agent_adapters.karena_integration import (
    agent_patch_ids_for_agent_family,
    infer_bug_id_from_agent_patches_path,
    karena_db_agent_patch_id_bounds,
    lookup_bug_id_for_agent_patch,
    read_agent_patch_metadata,
    resolve_agent_patches_root,
)

logger = logging.getLogger(__name__)


def _traj_json_supported(path: Path) -> bool:
    """Heuristic: we can attempt parse if ``events``, ``trajectory``, or ``messages`` present."""
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(65_536)
    except OSError:
        return False
    return any(
        k in head
        for k in ('"events"', '"trajectory"', '"messages"')
    )


@dataclass
class AgentPatchesLoadStats:
    """Counts from one ``load_from_agent_patches_directory`` scan (for verify / dashboards)."""

    traj_files_scanned: int = 0
    skipped_unsupported_format: int = 0
    skipped_no_agent_patch_id: int = 0
    skipped_not_agent_family: int = 0
    skipped_not_in_db: int = 0
    skipped_no_bugid: int = 0
    skipped_no_edits: int = 0
    loaded_passes: int = 0


class AgentPassesStore:
    """In-memory registry of agent passes with parsed trajectories.

    Build from run JSON paths, then use get_all_passes() / get_pass() to drive
    minimization (e.g. via minimize_edits in a wrapper script).
    """

    def __init__(self) -> None:
        self._passes: List[AgentPass] = []
        self._by_pass_id: dict[str, AgentPass] = {}

    def add_pass_from_run_json(
        self,
        run_json_path: str,
        bug_id: str,
        pass_id: str | None = None,
        agent_patch_id: int | None = None,
    ) -> AgentPass | None:
        """Parse a ``traj.json`` and add one pass to the store.

        Args:
            run_json_path: Path to trajectory JSON (e.g. ``.../agentPatchId/traj.json``).
            bug_id: Syzbot bug id (from JSON, path, or DB).
            pass_id: Optional id; default ``agent_patch_<id>`` or basename without ``.json``.
            agent_patch_id: Karena ``agent_patches.agentPatchId`` when known.

        Returns:
            The added AgentPass, or None if no candidates with edits.
        """
        path = Path(run_json_path)
        if not path.is_file():
            logger.warning("Not a file: %s", run_json_path)
            return None
        if agent_patch_id is not None:
            pid = pass_id or f"agent_patch_{agent_patch_id}"
        else:
            pid = pass_id or path.stem
        if pid in self._by_pass_id:
            logger.warning("pass_id %s already in store, skipping %s", pid, run_json_path)
            return self._by_pass_id[pid]

        candidates, patch_lists = load_trajectory_file(run_json_path)
        if not patch_lists or not any(pl for pl in patch_lists):
            logger.info("No candidates with edits in %s", run_json_path)
            return None

        pass_ = AgentPass(
            pass_id=pid,
            bug_id=bug_id,
            candidates=candidates,
            patch_lists=patch_lists,
            run_json_path=run_json_path,
            agent_patch_id=agent_patch_id,
        )
        self._passes.append(pass_)
        self._by_pass_id[pid] = pass_
        logger.info(
            "Added pass %s (bug_id=%s, agent_patch_id=%s): %d candidates, %d total edits",
            pid,
            bug_id,
            agent_patch_id,
            pass_.num_candidates(),
            pass_.total_edits(),
        )
        return pass_

    def load_from_agent_patches_directory(
        self,
        directory: str,
        *,
        karena_db_path: str | None = None,
        db_agent_family: str | None = None,
    ) -> tuple[List[AgentPass], AgentPatchesLoadStats]:
        """Load all trajectory JSONs from extracted ``agent-patches.zip`` (or similar).

        Returns:
            ``(added_passes, stats)`` — ``added_passes`` mirrors ``get_all_passes()`` order;
            ``stats`` summarizes skips (unsupported head, DB filter, no edits, etc.).

        Each file should identify a Karena ``agentPatchId`` (JSON field, numeric
        ``<id>.json``, or ``<agentPatchId>/traj.json``).

        When ``karena_db_path`` is set, the **SQLite DB is the source of truth**:
        ``bugId`` comes only from ``agent_patches`` (via ``lookup_bug_id_for_agent_patch``).
        Trajectories whose ``agentPatchId`` is absent from the DB (e.g. newer zip exports
        than the DB snapshot) are **skipped** — do not use ``bugId`` embedded in JSON alone.

        Without a DB path, ``bugId`` may fall back to JSON or a 40-char parent folder name.

        AIDEV-NOTE: Sort order of ``agentPatchId`` matches experiment run order per bug
        (smallest id = first run) only when using the same ``agentConfigId`` in Karena;
        this loader does not sort—``run_swe_minimization`` filters by DB row.
        """
        root = resolve_agent_patches_root(Path(directory))
        if not root.is_dir():
            logger.warning("Not a directory: %s", directory)
            return [], AgentPatchesLoadStats()
        conn: sqlite3.Connection | None = None
        allowed_agent_patch_ids: set[int] | None = None
        if karena_db_path and os.path.isfile(karena_db_path):
            conn = sqlite3.connect(karena_db_path)
            mn, mx = karena_db_agent_patch_id_bounds(conn)
            logger.info(
                "Karena DB %s: agentPatchId range %s–%s",
                karena_db_path,
                mn,
                mx,
            )
            if db_agent_family:
                # AIDEV-NOTE: Pre-filter by DB agent family (e.g. swe-agent) to avoid parsing
                # unrelated trajectory formats when running targeted experiments.
                allowed_agent_patch_ids = agent_patch_ids_for_agent_family(
                    conn, agent_family=db_agent_family
                )
                logger.info(
                    "DB agent-family filter %r: %d agentPatchIds",
                    db_agent_family,
                    len(allowed_agent_patch_ids),
                )
        added: List[AgentPass] = []
        skipped_no_bugid = 0
        skipped_not_in_db = 0
        skipped_not_agent_family = 0
        skipped_unsupported_format = 0
        skipped_no_agent_patch_id = 0
        skipped_no_edits = 0
        try:
            # AIDEV-NOTE: Prefer traj.json — avoids unrelated JSON; matches Karena export layout.
            traj_paths = sorted(root.rglob("traj.json"))
            if not traj_paths:
                traj_paths = sorted(p for p in root.rglob("*.json") if not p.name.startswith("."))
            for path in traj_paths:
                # AIDEV-NOTE: Zip mixes ``events``, ``trajectory`` (SWE), ``messages`` (mini-swe) — skip fast.
                if not _traj_json_supported(path):
                    skipped_unsupported_format += 1
                    continue
                aid, bug_from_json = read_agent_patch_metadata(path)
                if aid is None:
                    skipped_no_agent_patch_id += 1
                    logger.warning("Skip %s: no agentPatchId in JSON, filename, or parent dir", path)
                    continue
                if allowed_agent_patch_ids is not None and aid not in allowed_agent_patch_ids:
                    skipped_not_agent_family += 1
                    continue
                if conn is not None:
                    # AIDEV-NOTE: DB is reference; ignore trajs not in agent_patches (newer experiments).
                    bug_id = lookup_bug_id_for_agent_patch(conn, aid)
                    if not bug_id:
                        skipped_not_in_db += 1
                        logger.debug(
                            "Skip %s: agentPatchId=%s not in Karena DB (ignore zip-only traj)",
                            path,
                            aid,
                        )
                        continue
                else:
                    bug_id = bug_from_json or infer_bug_id_from_agent_patches_path(path, root)
                    if not bug_id:
                        skipped_no_bugid += 1
                        logger.debug(
                            "Skip %s: no bugId (agentPatchId=%s); set KARENA_DB_PATH",
                            path,
                            aid,
                        )
                        continue
                pass_ = self.add_pass_from_run_json(str(path), bug_id, agent_patch_id=aid)
                if pass_ is not None:
                    added.append(pass_)
                else:
                    skipped_no_edits += 1
            if skipped_not_in_db:
                _mn, mx = karena_db_agent_patch_id_bounds(conn)
                logger.warning(
                    "Skipped %d traj.json: agentPatchId not in Karena DB (newer experiments in zip; "
                    "ignored per DB-as-reference policy). DB agentPatchId max=%s.",
                    skipped_not_in_db,
                    mx,
                )
            if skipped_not_agent_family:
                logger.info(
                    "Skipped %d traj.json not in DB agent-family filter %r.",
                    skipped_not_agent_family,
                    db_agent_family,
                )
            if skipped_no_bugid:
                logger.warning(
                    "Skipped %d traj.json with no bugId (no DB path — set KARENA_DB_PATH).",
                    skipped_no_bugid,
                )
        finally:
            if conn is not None:
                conn.close()
        stats = AgentPatchesLoadStats(
            traj_files_scanned=len(traj_paths),
            skipped_unsupported_format=skipped_unsupported_format,
            skipped_no_agent_patch_id=skipped_no_agent_patch_id,
            skipped_not_agent_family=skipped_not_agent_family,
            skipped_not_in_db=skipped_not_in_db,
            skipped_no_bugid=skipped_no_bugid,
            skipped_no_edits=skipped_no_edits,
            loaded_passes=len(added),
        )
        return added, stats

    def get_all_passes(self) -> List[AgentPass]:
        return list(self._passes)

    def get_pass(self, pass_id: str) -> AgentPass | None:
        return self._by_pass_id.get(pass_id)

    def get_patch_lists_for_pass(self, pass_id: str) -> List[PatchList] | None:
        p = self._by_pass_id.get(pass_id)
        return list(p.patch_lists) if p else None

    def __len__(self) -> int:
        return len(self._passes)
