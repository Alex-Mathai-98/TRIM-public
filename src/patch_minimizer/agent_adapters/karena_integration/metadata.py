"""Karena agent-patches JSON metadata + zip layout helpers."""
from __future__ import annotations

import json
import re
from pathlib import Path

# AIDEV-NOTE: bug-id folder names are 40-char git hashes (no _0); benchmark JSONs may use _0 suffix.
_BUG_ID_DIR_RE = re.compile(r"^[0-9a-f]{40}$")


def read_agent_patch_metadata(path: Path) -> tuple[int | None, str | None]:
    """Read ``agentPatchId`` and ``bugId`` from a trajectory JSON without full parsing.

    Tries common keys used by Karena exports; falls back to numeric filename stem or
    parent directory name (``<agentPatchId>/traj.json`` layout from agent-patches.zip).
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    raw_id = data.get("agentPatchId", data.get("agent_patch_id"))
    aid: int | None
    if raw_id is None:
        aid = None
    else:
        try:
            aid = int(raw_id)
        except (TypeError, ValueError):
            aid = None
    bug = data.get("bugId") or data.get("bug_id")
    bug_id = bug if isinstance(bug, str) else None
    if aid is None:
        stem = path.stem
        if stem.isdigit():
            aid = int(stem)
    # AIDEV-NOTE: Karena zip uses ``.../43840/traj.json`` — id is the folder name, not ``traj``.
    if aid is None and path.parent.name.isdigit():
        aid = int(path.parent.name)
    # AIDEV-NOTE: Some layouts use ``original/traj.json`` with a sibling ``agent_patch_id.txt``.
    if aid is None:
        id_file = path.parent / "agent_patch_id.txt"
        if id_file.is_file():
            txt = id_file.read_text().strip()
            if txt.isdigit():
                aid = int(txt)
    return aid, bug_id


def resolve_agent_patches_root(path: Path) -> Path:
    """If unzip produced ``agent-patches/agent-patches/<id>/``, return the inner root."""
    root = path.resolve()
    nested = root / "agent-patches"
    if nested.is_dir() and any(
        p.is_dir() and p.name.isdigit() for p in nested.iterdir()
    ):
        return nested
    return root


def infer_bug_id_from_agent_patches_path(path: Path, root: Path) -> str | None:
    """Infer bug hash from directory layout under extracted ``agent-patches`` root."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    if len(rel.parts) >= 2 and _BUG_ID_DIR_RE.match(rel.parts[0]):
        return rel.parts[0]
    for ancestor in path.parents:
        if ancestor == root:
            break
        if _BUG_ID_DIR_RE.match(ancestor.name):
            return ancestor.name
    return None
