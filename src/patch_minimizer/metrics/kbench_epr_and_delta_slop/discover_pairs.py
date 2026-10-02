"""Discover original/minimized patch pairs from results folders.

Supports three layouts:
  nightly  — tree_N/gemini-3-pro-preview__<hash>_0/<node>/final.patch + minimized.patch
  swe      — <bug_id>/swe_candidate_0/standalone__<hash>_{0,}/minimized.patch (edit_full/node_full)
             or standalone__<hash>_{0,}/minimized.patch (hybrid, flat)
  agentic  — run_N/<bug_id>/original/patch.diff + <strategy>/patch.diff

Outputs a JSON file mapping:
    { bug_id: { traj_N: { original: path, minimized: path [, fallback: reason] } } }

Usage:
    # Nightly (CrashFixer) — single folder has both final.patch and minimized.patch
    python discover_pairs.py --layout nightly \
        --base-folder results/parent_commit/nightly_deployment/nightly/srw_with_lessons_per_tool/gemini-3-pro/None/4x3/hyp_0.8_patch_0.8_seed_10_run_9000_edit_minimize_guarantee_cache \
        --out pairs_crashfixer.json

    # SWE — openhands (3 runs)
    python discover_pairs.py --layout swe \
        --min-folders results/openhands_runs/openhands_run1_edit_full \
                      results/openhands_runs/openhands_run2_edit_full \
                      results/openhands_runs/openhands_run3_edit_full \
        --orig-folder results/openhands-c-unlimited_gemini-3-pro-preview_5 \
        --out pairs_openhands.json

    # Agentic — mini-swe with min-diff strategy
    python discover_pairs.py --layout agentic \
        --results-folder results/mini-swe-agent-c-5.6_claude-opus-4.5_8 \
        --strategy minimized --runs 1 \
        --out pairs_agentic.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from unidiff import PatchSet

BUG_PREFIX = "gemini-3-pro-preview__"
STANDALONE_RE = re.compile(r"^standalone__(.+?)(_0)?$")
_LINUX_DIR_RE = re.compile(r"collection_of_linux_repos/linux-[A-Za-z0-9_-]+/")


def _find_last_node(bug_dir: Path) -> Path | None:
    last = None
    for d in bug_dir.iterdir():
        if d.is_dir() and d.name.isdigit():
            if last is None or int(d.name) > int(last.name):
                last = d
    return last


def _read_nonempty(p: Path) -> bool:
    return p.is_file() and bool(p.read_text(errors="replace").strip())


def _files_match(orig_path: Path, mini_path: Path) -> bool:
    """Check that the minimized patch's .c/.h files are a subset of the original's."""
    def _m3_files(ps: PatchSet) -> set[str]:
        return {
            pf.path.removeprefix("a/").removeprefix("b/")
            for pf in ps
            if (pf.path.removeprefix("a/").removeprefix("b/").endswith((".c", ".h"))
                and not pf.is_added_file)
        }

    try:
        orig_ps = PatchSet.from_string(
            _LINUX_DIR_RE.sub("", orig_path.read_text(errors="replace"))
        )
        mini_ps = PatchSet.from_string(
            _LINUX_DIR_RE.sub("", mini_path.read_text(errors="replace"))
        )
    except Exception:
        return False

    return _m3_files(mini_ps).issubset(_m3_files(orig_ps))


def _resolve_minimized(orig: Path, mini: Path) -> dict[str, str]:
    """Build entry dict, falling back to original on file mismatch."""
    entry = {"original": str(orig.resolve())}
    if _read_nonempty(mini) and _files_match(orig, mini):
        entry["minimized"] = str(mini.resolve())
    elif _read_nonempty(mini):
        # AIDEV-NOTE: file-mismatch → fall back to original (no reduction)
        entry["minimized"] = str(orig.resolve())
        entry["fallback"] = "file_mismatch"
    else:
        entry["minimized"] = str(orig.resolve())
    return entry


# ---------- Nightly layout ----------

def discover_nightly(base_folder: Path, patch_name: str) -> dict[str, dict[str, dict[str, str]]]:
    """Return { bug_id: { traj_N: { original, minimized } } } for nightly layout."""
    result: dict[str, dict[str, dict[str, str]]] = {}

    for tree_dir in sorted(base_folder.iterdir()):
        if not tree_dir.is_dir() or not tree_dir.name.startswith("tree_"):
            continue
        tree_num = tree_dir.name.removeprefix("tree_")

        for bug_dir in sorted(tree_dir.iterdir()):
            if not bug_dir.is_dir() or not bug_dir.name.startswith(BUG_PREFIX):
                continue

            bug_id = bug_dir.name.removeprefix(BUG_PREFIX)
            last_node = _find_last_node(bug_dir)
            if last_node is None:
                continue

            # AIDEV-NOTE: skip bugs where passed_validation is not "True"; hybrid folders lack this file
            pv = last_node / "passed_validation.txt"
            if pv.is_file():
                if pv.read_text().strip() != "True":
                    continue
            elif "hybrid" not in base_folder.name:
                continue

            orig = last_node / "final.patch"
            mini = last_node / patch_name

            if not _read_nonempty(orig):
                continue

            result.setdefault(bug_id, {})[f"traj_{tree_num}"] = _resolve_minimized(orig, mini)

    return result


# ---------- SWE layout ----------

def _extract_bug_id_from_standalone(dirname: str) -> str | None:
    m = STANDALONE_RE.match(dirname)
    return m.group(1) if m else None


def _find_minimized_swe(min_folder: Path) -> list[tuple[str, Path]]:
    """Find minimized patches in a SWE minimization folder.

    Handles both nested (edit_full/node_full) and flat (hybrid) layouts.
    """
    results = []

    for entry in sorted(min_folder.iterdir()):
        if not entry.is_dir():
            continue

        if entry.name.startswith("standalone__"):
            # AIDEV-NOTE: flat layout (hybrid) — standalone dirs at top level
            bug_id = _extract_bug_id_from_standalone(entry.name)
            if bug_id:
                mp = entry / "minimized.patch"
                results.append((bug_id, mp))
        else:
            # AIDEV-NOTE: nested layout (edit_full/node_full) — bug_id/swe_candidate_0/standalone__*/
            candidate = entry / "swe_candidate_0"
            if not candidate.is_dir():
                continue
            for standalone in candidate.iterdir():
                if not standalone.is_dir() or not standalone.name.startswith("standalone__"):
                    continue
                bug_id = _extract_bug_id_from_standalone(standalone.name)
                if bug_id:
                    mp = standalone / "minimized.patch"
                    results.append((bug_id, mp))

    return results


def discover_swe(
    min_folders: list[Path],
    orig_folder: Path,
) -> dict[str, dict[str, dict[str, str]]]:
    """Return { bug_id: { traj_N: { original, minimized } } } for SWE layout.

    Each min_folder corresponds to one trajectory (run).
    Originals come from orig_folder/run_N/<bug_id>/original/patch.diff.
    """
    result: dict[str, dict[str, dict[str, str]]] = {}

    for traj_idx, min_folder in enumerate(min_folders, start=1):
        if not min_folder.is_dir():
            print(f"WARN: missing {min_folder}")
            continue

        orig_run_dir = orig_folder / f"run_{traj_idx}"
        entries = _find_minimized_swe(min_folder)

        for bug_id, mini_path in entries:
            orig_path = orig_run_dir / bug_id / "original" / "patch.diff"
            if not _read_nonempty(orig_path):
                continue

            result.setdefault(bug_id, {})[f"traj_{traj_idx}"] = _resolve_minimized(orig_path, mini_path)

    return result


# AIDEV-NOTE: non-bug dirs at run level that must be skipped
_AGENTIC_SKIP_PREFIXES = ("_", "judge_results")


# ---------- Agentic layout ----------

def discover_agentic(
    results_folder: Path,
    strategy: str,
    runs: list[int],
) -> dict[str, dict[str, dict[str, str]]]:
    """Return { bug_id: { traj_N: { original, minimized [, fallback] } } } for agentic layout."""
    result: dict[str, dict[str, dict[str, str]]] = {}

    for run_idx in runs:
        run_dir = results_folder / f"run_{run_idx}"
        if not run_dir.is_dir():
            print(f"WARN: missing {run_dir}")
            continue

        for bug_dir in sorted(run_dir.iterdir()):
            if not bug_dir.is_dir():
                continue
            if bug_dir.name.startswith(_AGENTIC_SKIP_PREFIXES):
                continue

            bug_id = bug_dir.name
            orig = bug_dir / "original" / "patch.diff"
            mini = bug_dir / strategy / "patch.diff"
            kgym = bug_dir / strategy / "kgym_eval.json"

            if not _read_nonempty(orig):
                continue

            entry: dict[str, str] = {"original": str(orig.resolve())}

            kgym_status = _read_kgym_status(kgym)
            if kgym_status is not None and kgym_status != "notReproduced":
                entry["minimized"] = str(orig.resolve())
                entry["fallback"] = f"kgym_eval:{kgym_status}"
            elif not _read_nonempty(mini):
                entry["minimized"] = str(orig.resolve())
                entry["fallback"] = "missing_patch"
            elif not _files_match(orig, mini):
                entry["minimized"] = str(orig.resolve())
                entry["fallback"] = "file_mismatch"
            else:
                entry["minimized"] = str(mini.resolve())

            result.setdefault(bug_id, {})[f"traj_{run_idx}"] = entry

    return result


def _read_kgym_status(kgym_path: Path) -> str | None:
    if not kgym_path.is_file():
        return None
    data = json.loads(kgym_path.read_text())
    return data.get("kGymEvaluation")


# ---------- CLI ----------

def main() -> None:
    parser = argparse.ArgumentParser(description="Discover patch pairs from results folders")
    parser.add_argument("--layout", choices=["nightly", "swe", "agentic"], required=True)
    parser.add_argument("--out", type=Path, required=True)

    nightly_group = parser.add_argument_group("nightly layout")
    nightly_group.add_argument("--base-folder", type=Path)
    nightly_group.add_argument("--patch-name", default="minimized.patch")

    swe_group = parser.add_argument_group("SWE layout")
    swe_group.add_argument("--min-folders", type=Path, nargs="+")
    swe_group.add_argument("--orig-folder", type=Path)

    agentic_group = parser.add_argument_group("agentic layout")
    agentic_group.add_argument("--results-folder", type=Path)
    agentic_group.add_argument(
        "--strategy",
        choices=["minimized", "minimized_diff_and_traj", "minimized_traj_only"],
    )
    agentic_group.add_argument("--runs", type=int, nargs="+", default=[1])

    args = parser.parse_args()

    if args.layout == "nightly":
        if not args.base_folder:
            parser.error("--base-folder is required for nightly layout")
        pairs = discover_nightly(args.base_folder, args.patch_name)
    elif args.layout == "swe":
        if not args.min_folders or not args.orig_folder:
            parser.error("--min-folders and --orig-folder are required for swe layout")
        pairs = discover_swe(args.min_folders, args.orig_folder)
    else:
        if not args.results_folder or not args.strategy:
            parser.error("--results-folder and --strategy are required for agentic layout")
        pairs = discover_agentic(args.results_folder, args.strategy, args.runs)

    bugs_with_trajs = sum(1 for v in pairs.values() if v)
    total_trajs = sum(len(v) for v in pairs.values())
    print(f"Bugs: {bugs_with_trajs}, Total trajectories: {total_trajs}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(pairs, indent=2, sort_keys=True))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
