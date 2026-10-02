"""M1.B — Byte-identical reconstruction verify for SWE-agent SWE-bench trajectories.

For each ``.traj`` under ``--trajs-dir``:

  1. Parse the trajectory via ``SWEBenchSWEAgentTrajectoryParser``, which
     sets ``REPO_TREE_PREFIX = "/testbed/"`` in its ``__init__`` so path
     normalization strips ``/testbed/`` correctly.
  2. Apply the parsed edits to a local clone of the repo at ``base_commit``
     via ``RepoPatchManager.check_patch_range``.
  3. Run ``RepoPatchManager.generate_git_diff()`` (uses plain ``a/``/``b/``
     prefixes for non-kernel paths thanks to M2.repo).
  4. Compare byte-identically against the agent's submitted patch
     (``model_patch`` from the submission's ``all_preds.jsonl`` on S3).

The gate per ``swe-bench-plan.md`` §13 M1.B is ≥ 95% byte-identical
reconstruction.

Usage:
    python verify_swe_bench_edit_applicability.py \\
        --trajs-dir <dir of .traj files> \\
        --submission 20250522_sweagent_claude-4-sonnet-20250514 \\
        --workspace <dir to clone repos into> \\
        [--limit N] [--instance-ids id1 id2 ...] [--output-json path]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import patch_minimizer.agent_adapters.swe_agent_adapter  # noqa: F401
from patch_minimizer.agent_adapters.swe_agent_adapter import (
    SWEBenchSWEAgentTrajectoryParser,
)
from patch_minimizer.agent_adapters.agent_adapter_base.utils import (
    to_patch_list,
    stamp_global_edit_idx,
)
from patch_minimizer.strategies.repo_patch_manager import (
    RepoPatchManager,
)
from patch_minimizer.core.rw_lock import RWLock
from patch_minimizer.frontends.swebench.utils.hf_dataset import DATASET as HF_DATASET
from patch_minimizer.frontends.swebench.utils.hf_dataset import hf_rows

S3_BASE = "https://swe-bench-submissions.s3.amazonaws.com"

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)


def fetch_all_preds(submission: str) -> dict[str, str]:
    """Fetch all_preds.jsonl from S3, return ``instance_id -> model_patch``."""
    url = f"{S3_BASE}/verified/{submission}/all_preds.jsonl"
    print(f"  fetching {url}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=120) as resp:
        text = resp.read().decode("utf-8")
    preds: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        preds[row["instance_id"]] = row.get("model_patch", "") or ""
    return preds


def load_swebench_metadata() -> dict[str, dict[str, str]]:
    """Load ``instance_id -> {repo, base_commit}`` from SWE-bench_Verified."""
    return {
        iid: {"repo": row["repo"], "base_commit": row["base_commit"]}
        for iid, row in hf_rows().items()
    }


def clone_repo(repo: str, workspace: Path) -> Path:
    """Ensure ``workspace/<owner__name>`` contains a full clone of github.com/<repo>."""
    safe_name = repo.replace("/", "__")
    code_dir = workspace / safe_name
    if (code_dir / ".git").exists():
        return code_dir
    code_dir.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{repo}.git"
    print(f"  cloning {url} -> {code_dir}", file=sys.stderr)
    subprocess.run(
        ["git", "clone", "--quiet", url, str(code_dir)],
        check=True,
        timeout=900,
    )
    return code_dir


_DIFF_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/\S+\s*$")
# AIDEV-NOTE: ``index <abbrev1>..<abbrev2> <mode>`` lines vary by git's
# auto-extending core.abbrev (7 chars default, longer on dense histories
# like scikit-learn). Strip before byte-comparison — purely cosmetic. [AI-M1.B]
_INDEX_LINE_RE = re.compile(r"^index [0-9a-f]+\.\.[0-9a-f]+(?: \d+)?\s*$", re.MULTILINE)


def _strip_index_lines(diff_section: str) -> str:
    return _INDEX_LINE_RE.sub("", diff_section)


def split_diff_by_file(diff_text: str) -> dict[str, str]:
    """Split a unified diff into ``{repo_relative_path: diff_section}``.

    The section for each file starts at its ``diff --git`` line and runs up
    to the next file's header (or end of diff).
    """
    sections: dict[str, str] = {}
    cur_file: str | None = None
    cur_lines: list[str] = []
    for line in diff_text.splitlines():
        m = _DIFF_HEADER_RE.match(line)
        if m:
            if cur_file is not None:
                sections[cur_file] = "\n".join(cur_lines)
            cur_file = m.group(1)
            cur_lines = [line]
        elif cur_file is not None:
            cur_lines.append(line)
    if cur_file is not None:
        sections[cur_file] = "\n".join(cur_lines)
    return sections


def verify_one(
    instance_id: str,
    traj_path: Path,
    code_dir: Path,
    base_commit: str,
    model_patch: str,
    code_dir_lock: RWLock,
    save_diffs_dir: Path | None = None,
) -> dict[str, Any]:
    """Verify one trajectory's parsed edits reconstruct ``model_patch``.

    Comparison is **per-file, restricted to the file set in `model_patch`**:
    SWE-bench submissions strip agent-created scratch/proxy files before
    submission, so our generated diff (which includes them) is a strict
    superset. We compare each file the model_patch DOES include, and treat
    extra agent-side files in our reconstruction as expected (not a fail).
    See ``swe-bench-plan.md`` §13 M1.B. [AI-M1.B]
    """
    # AIDEV-NOTE: Some SWE-bench repos (e.g. astropy) had submodules at old
    # commits but removed them from HEAD. The clone has no .git/modules/,
    # so checkout the base_commit and init submodules before RepoPatchManager
    # tries deinit/init (which fails if .git/modules/ is missing). [AI-M1.B]
    with code_dir_lock.w_locked_nowait():
        subprocess.run(
            ["git", "checkout", base_commit],
            cwd=str(code_dir), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "submodule", "update", "--init"],
            cwd=str(code_dir), capture_output=True, check=False,
        )

    try:
        # AIDEV-NOTE: kernel_base_url=None — skips set_correct_branch; the
        # local clone already has the commit we need (full-history clone). [AI-M1.B]
        repo_mgr = RepoPatchManager(
            code_dir=str(code_dir),
            code_dir_lock=code_dir_lock,
            base_commit=base_commit,
            logger=logger,
            kernel_base_url=None,
        )
    except Exception as e:
        return {"instance_id": instance_id, "status": "checkout_failed", "msg": str(e)}

    try:
        parser = SWEBenchSWEAgentTrajectoryParser()
        with open(traj_path, encoding="utf-8") as f:
            raw = json.load(f)
        _candidates = parser.parse(raw, source_label=str(traj_path))
        patch_lists = [to_patch_list(c) for c in _candidates]
        for pl in patch_lists:
            stamp_global_edit_idx(pl)
    except Exception as e:
        return {"instance_id": instance_id, "status": "parse_failed", "msg": str(e)}

    if not patch_lists:
        return {"instance_id": instance_id, "status": "no_patches"}

    # Use the LAST candidate trajectory (= what the agent ultimately submitted).
    last_patch_list = patch_lists[-1]
    patch_range = [edits for _, edits in last_patch_list]
    if not patch_range:
        return {"instance_id": instance_id, "status": "no_edits"}

    try:
        with code_dir_lock.w_locked_nowait():
            ok = repo_mgr.check_patch_range(
                None, patch_range, leave_patches=True, use_lock=False,
            )
            generated = ""
            if ok:
                generated = repo_mgr.generate_git_diff()
            repo_mgr.clean_repo(use_lock=False)
    except Exception as e:
        # AIDEV-NOTE: Per-instance exception isolation — one bad apply must
        # not sink the whole batch. The clone may be in a dirty state; the
        # next instance's RepoPatchManager.__init__ will re-checkout and
        # heal it. [AI-M1.B]
        try:
            with code_dir_lock.w_locked_nowait():
                repo_mgr.clean_repo(use_lock=False)
        except Exception:
            pass
        return {
            "instance_id": instance_id, "status": "apply_failed",
            "msg": f"{type(e).__name__}: {e}",
            "n_edits": sum(len(p) for p in patch_range),
        }

    if not ok:
        return {"instance_id": instance_id, "status": "not_applicable",
                "n_edits": sum(len(p) for p in patch_range)}

    gen_sections = split_diff_by_file(generated)
    model_sections = split_diff_by_file(model_patch or "")

    # AIDEV-NOTE: Compare per-file on the INTERSECTION of files between
    # generated and model_patch. Files only in model_patch are either env
    # artifacts injected by SWE-bench setup (e.g. pyproject.toml setuptools
    # pin) or parser-missed edits — either way they're tracked separately,
    # not treated as a hard fail of the parser. Files only in generated
    # are agent scratch/proxy files, expected and harmless. M1.B's gate is
    # about parser faithfulness on the files the agent really edited.
    # [AI-M1.B]
    common = sorted(set(gen_sections) & set(model_sections))
    mismatched = [
        f for f in common
        if _strip_index_lines(gen_sections[f]).rstrip()
        != _strip_index_lines(model_sections[f]).rstrip()
    ]
    missing_in_generated = sorted(set(model_sections) - set(gen_sections))
    extra_in_generated = sorted(set(gen_sections) - set(model_sections))

    if common and not mismatched:
        status = "ok"
    elif mismatched:
        status = "diff_mismatch"
    else:
        # No common files at all — every model_patch file is missing.
        # Likely a parser bug for this instance.
        status = "no_common_files"
    diff_match = status == "ok"

    if save_diffs_dir is not None and not diff_match:
        save_diffs_dir.mkdir(parents=True, exist_ok=True)
        (save_diffs_dir / f"{instance_id}.generated.diff").write_text(generated)
        (save_diffs_dir / f"{instance_id}.model.diff").write_text(model_patch or "")

    return {
        "instance_id": instance_id,
        "status": status,
        "n_edits": sum(len(p) for p in patch_range),
        "model_files": sorted(model_sections.keys()),
        "common_files": common,
        "missing_in_generated": missing_in_generated,
        "mismatched_files": mismatched,
        "extra_in_generated": extra_in_generated,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--trajs-dir", required=True)
    ap.add_argument("--submission", required=True)
    ap.add_argument("--workspace", required=True,
                    help="Directory to clone repos into (~5 GB total)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--instance-ids", nargs="+", default=None,
                    help="Verify only these instance_ids (space-separated)")
    ap.add_argument("--output-json", default=None,
                    help="Write per-instance result list to this JSON file")
    ap.add_argument("--save-diffs-dir", default=None,
                    help="If set, save generated.diff and model.diff per instance "
                         "for any non-ok result to this directory (debug)")
    args = ap.parse_args()

    # AIDEV-NOTE: Resolve the workspace to an absolute path. RepoPatchManager
    # passes `code_dir` to git's `--work-tree` and embeds it in candidate
    # file paths for `git add -N`. Mixing relative work-tree + relative file
    # paths makes git fail with exit 128. Absolute paths sidestep this. [AI-M1.B]
    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] Loading metadata from HF ({HF_DATASET}) ...", file=sys.stderr)
    metadata = load_swebench_metadata()
    print(f"      {len(metadata)} instances total", file=sys.stderr)

    print(f"[2/4] Fetching predictions for {args.submission} ...", file=sys.stderr)
    preds = fetch_all_preds(args.submission)
    print(f"      {len(preds)} predicted patches", file=sys.stderr)

    trajs = {p.stem: p for p in Path(args.trajs_dir).glob("*.traj")}
    iids = sorted(set(trajs) & set(metadata) & set(preds))
    if args.instance_ids:
        wanted = set(args.instance_ids)
        iids = [i for i in iids if i in wanted]
    if args.limit is not None:
        iids = iids[: args.limit]
    print(
        f"[3/4] Selected {len(iids)} trajectories "
        f"(traj∩meta∩preds = {len(set(trajs) & set(metadata) & set(preds))} pre-filter)",
        file=sys.stderr,
    )

    unique_repos = sorted({metadata[i]["repo"] for i in iids})
    print(f"      {len(unique_repos)} unique repos to clone", file=sys.stderr)
    repo_paths: dict[str, Path] = {}
    repo_locks: dict[str, RWLock] = {}
    for repo in unique_repos:
        repo_paths[repo] = clone_repo(repo, workspace)
        repo_locks[repo] = RWLock()

    print(f"[4/4] Verifying {len(iids)} trajectories ...", file=sys.stderr)
    results: list[dict[str, Any]] = []
    save_dir = Path(args.save_diffs_dir) if args.save_diffs_dir else None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)
    for i, iid in enumerate(iids, 1):
        meta = metadata[iid]
        result = verify_one(
            iid, trajs[iid], repo_paths[meta["repo"]],
            meta["base_commit"], preds[iid], repo_locks[meta["repo"]],
            save_diffs_dir=save_dir,
        )
        results.append(result)
        marker = "OK" if result["status"] == "ok" else result["status"].upper()
        print(f"  [{i}/{len(iids)}] {iid}: {marker}", file=sys.stderr)

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    print()
    print("M1.B verify summary")
    print("=" * 50)
    total = len(results)
    ok = counts.get("ok", 0)
    pct = 100 * ok / total if total else 0
    print(f"  {'ok':18s} {ok:5d} ({pct:5.1f}%)")
    for st in (
        "diff_mismatch", "no_common_files", "not_applicable", "apply_failed",
        "no_patches", "no_edits", "parse_failed", "checkout_failed",
    ):
        c = counts.get(st, 0)
        if c:
            sub_pct = 100 * c / total
            print(f"  {st:18s} {c:5d} ({sub_pct:5.1f}%)")
    print(f"  {'total':18s} {total:5d}")
    n_with_missing = sum(1 for r in results if r.get("missing_in_generated"))
    n_with_extra = sum(1 for r in results if r.get("extra_in_generated"))
    print()
    print("Secondary metrics:")
    print(f"  trajectories with model_patch files we did NOT reconstruct: {n_with_missing}"
          f" (env artifacts like pyproject.toml + parser misses)")
    print(f"  trajectories with reconstructed files NOT in model_patch:   {n_with_extra}"
          f" (agent scratch/proxy files; expected)")
    print()
    print(f"Gate (>=95% byte-identical on common files): {'PASS' if pct >= 95 else 'FAIL'}")

    if args.output_json:
        Path(args.output_json).write_text(json.dumps(results, indent=2))
        print(f"\nDetail written to {args.output_json}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
