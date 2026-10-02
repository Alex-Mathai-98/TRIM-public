#!/usr/bin/env python3
"""Genuine clean-agent-patch reconstruction + line/hunk minimization numbers.

For each of the 327 resolved->resolved instances we build the agent's patch as
if it had been produced on a CLEAN base_commit (no SWE-bench env churn), the
SAME way ``minimized.patch`` is generated, then compare the two.

Method (per instance), using the local clone at .swe_bench_verify_workspace/<repo>:
  1. ``git checkout -f <base_commit>`` + ``git clean -fdq``  -> clean base tree.
  2. From the agent submission, keep only the diff blocks the agent AUTHORED:
       - files present in the agent edit-action list (original_patch_list), OR
       - NEW files (agent scratch, incl. bash-created ones the edit list misses), OR
       - whole-file DELETIONS, when the trajectory shows the agent ran a bash
         ``rm`` (these are `rm -f *.py` scratch-cleanup collateral -- they delete
         real top-level files like setup.py; bash ops never enter the edit-list).
     Drop only TRUE env churn: SWE-bench's `sed` modifications to setup.py /
     tox.ini / pyproject.toml (files the agent never touched). NOTE these ARE
     present in the eval container's `git diff <base>` for sed-repos.
  3. ``git apply`` that subset to the clean tree.  This is also the VALIDATION:
     if it applies cleanly, the agent-edited files' pre-agent state == clean
     base, i.e. no env lines hide inside them (proves the file-level cleanliness
     claim at the content level).  Failures are flagged, not hidden.
  4. ``git diff`` -> the reconstructed CLEAN agent patch (a real artifact).
  5. Compare line/hunk counts against ``minimized.patch``.

Edits reduction is the minimizer's own canonical 840->642; we re-emit it here.

Outputs an aggregate table and writes each reconstructed patch to
``<save-dir>/<id>/standalone__<id>/original_clean.patch`` for inspection.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

def resolved(o):
    return bool(o and o.get("resolved") and not o.get("error"))


def load_base_commits():
    """Map instance_id -> base_commit from the public SWE-bench-Verified dataset."""
    from datasets import load_dataset
    ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
    return {r["instance_id"]: r["base_commit"] for r in ds}


def authored_files(res):
    s = set()
    for node in res.get("original_patch_list") or []:
        if node and len(node) > 1 and node[1]:
            for e in node[1]:
                if e.get("filename"):
                    s.add(e["filename"])
    return s


def agent_did_rm(traj):
    """True iff the agent issued a bash ``rm`` during the episode.

    Whole-file deletions appear in the submission diff but never in the
    edit-action list, because the agent removes files via a bash ``rm -f *.py``
    (scratch cleanup) rather than a str_replace/create tool call. Confirming a
    real ``rm`` in the trajectory lets us classify such deletions as
    agent-authored *evidentially* rather than by assumption.
    """
    for step in traj.get("trajectory") or []:
        if isinstance(step, dict):
            for k in ("action", "command"):
                v = step.get(k)
                if isinstance(v, str) and re.search(r"\brm\b", v):
                    return True
    return False


def iter_blocks(patch):
    """Yield (path, is_new, is_del, raw_text, hunks, lines) per file block."""
    if not patch:
        return
    for part in re.split(r"(?m)^(?=diff --git )", patch):
        if not part.strip():
            continue
        is_new = bool(re.search(r"(?m)^new file mode", part)) or \
            bool(re.search(r"(?m)^--- /dev/null", part))
        # AIDEV-NOTE: whole-file deletions come from the agent's bash `rm -f *.py`
        # scratch cleanup (collateral) -- NOT env churn; the edit-action list
        # never records bash ops, so we surface is_del here to reclaim them.
        is_del = bool(re.search(r"(?m)^deleted file mode", part)) or \
            bool(re.search(r"(?m)^\+\+\+ /dev/null", part))
        m = re.search(r"(?m)^\+\+\+ b/(.+)$", part) or \
            re.search(r"(?m)^diff --git a/\S+ b/(\S+)", part)
        path = m.group(1).strip() if m else None
        hunks = len(re.findall(r"(?m)^@@ ", part))
        lines = sum(1 for ln in part.splitlines()
                    if (ln.startswith("+") or ln.startswith("-"))
                    and not ln.startswith("+++") and not ln.startswith("---"))
        yield path, is_new, is_del, part, hunks, lines


def count(patch):
    h = l = 0
    for _, _, _, _, hh, ll in iter_blocks(patch):
        h += hh
        l += ll
    return h, l


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=check)


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--save-dir", type=Path, required=True,
                    help="results directory")
    # AIDEV-NOTE: --traj-dir / --workspace-root replace hard-coded paths from the old
    # monorepo (tests/bash_files/... and .swe_bench_verify_workspace), which don't exist here.
    # The workspace clones are git reset/clean'ed: don't run this while a minimization batch
    # is using the same clones.
    ap.add_argument("--traj-dir", type=Path, default=Path("results/swe-bench-swe-agent-trajs"),
                    help="Directory of <instance_id>.traj files "
                         "(default: results/swe-bench-swe-agent-trajs).")
    ap.add_argument("--workspace-root", type=Path, default=Path("results/swe-bench-workspace"),
                    help="Root of the <owner>__<repo> clones (default: results/swe-bench-workspace).")
    ap.add_argument("--limit", type=int, default=0, help="only first N (sample)")
    ap.add_argument("--ids", nargs="*", help="only these instance_ids")
    ap.add_argument("--write", action="store_true",
                    help="write original_clean.patch artifacts")
    args = ap.parse_args(argv)
    canon = args.save_dir

    base = load_base_commits()
    summ = json.load(open(canon / "minimization_summary.json"))
    ids = sorted(e["bug_id"] for e in summ["successful_bugs"]
                 if resolved(e.get("oracle_pre")) and resolved(e.get("oracle_post")))
    if args.ids:
        ids = [i for i in ids if i in args.ids]
    if args.limit:
        ids = ids[:args.limit]

    edits = [0, 0]
    clean = {"h": 0, "l": 0}
    mini = {"h": 0, "l": 0}
    applied_ok = 0
    failures = []
    del_lines_folded = 0          # agent rm-collateral lines reclaimed as authored
    del_no_rm = []                # deletions with NO bash rm in traj (flag, don't assume)
    # group by repo so we minimize cross-repo checkout thrash
    ids.sort(key=lambda i: (i.rsplit("-", 1)[0], i))
    # AIDEV-NOTE: the git calls below use check=False, so a missing clone would silently
    # count every instance as a failure. Fail up front instead.
    missing = sorted({i.rsplit("-", 1)[0] for i in ids}
                     - {d.name for d in args.workspace_root.iterdir() if (d / ".git").exists()}
                     if args.workspace_root.is_dir() else {i.rsplit("-", 1)[0] for i in ids})
    if missing:
        raise SystemExit(f"Missing repo clones under {args.workspace_root}: {missing}")

    for n, iid in enumerate(ids, 1):
        repo = args.workspace_root / iid.rsplit("-", 1)[0]
        res = json.load(open(canon / iid / "minimization_results.json"))
        edits[0] += res.get("original_edit_count") or 0
        edits[1] += res.get("minimized_edit_count") or 0

        traj = json.load(open(args.traj_dir / f"{iid}.traj"))
        sub = traj["info"]["submission"] or ""
        au = authored_files(res)
        had_rm = agent_did_rm(traj)
        # keep agent-authored blocks:
        #   - files in the edit-action list, OR
        #   - new files (scratch, incl. bash heredoc the edit list misses), OR
        #   - whole-file deletions when the agent ran a bash `rm` (collateral of
        #     `rm -f *.py` scratch cleanup -- agent-authored, just not via a tool).
        kept = []
        for p, is_new, is_del, txt, _, ll in iter_blocks(sub):
            if (p in au) or is_new:
                kept.append(txt)
            elif is_del and had_rm:
                kept.append(txt)
                del_lines_folded += ll
            elif is_del:
                del_no_rm.append((iid, p))   # deletion w/o rm evidence: flag it
        filtered = "".join(kept)
        if filtered and not filtered.endswith("\n"):
            filtered += "\n"

        bc = base.get(iid)
        git(repo, "reset", "-q", "--hard", bc, check=False)
        git(repo, "clean", "-fdq", check=False)

        chk = subprocess.run(["git", "-C", str(repo), "apply", "--check", "-"],
                             input=filtered, capture_output=True, text=True)
        if chk.returncode == 0:
            subprocess.run(["git", "-C", str(repo), "apply", "-"],
                           input=filtered, text=True, capture_output=True)
            # stage so NEW files (agent scratch) are included in the diff
            git(repo, "add", "-A", check=False)
            recon = git(repo, "-c", "core.fileMode=false",
                        "diff", "--cached").stdout
            applied_ok += 1
        else:
            recon = filtered   # fall back to the filtered submission blocks
            failures.append((iid, chk.stderr.strip().splitlines()[:2]))

        mp = canon / iid / f"standalone__{iid}" / "minimized.patch"
        minipatch = mp.read_text() if mp.is_file() else ""

        ch, cl = count(recon)
        mh, ml = count(minipatch)
        clean["h"] += ch; clean["l"] += cl
        mini["h"] += mh; mini["l"] += ml

        if args.write:
            out = canon / iid / f"standalone__{iid}" / "original_clean.patch"
            out.write_text(recon)

        git(repo, "reset", "-q", "--hard", bc, check=False)
        git(repo, "clean", "-fdq", check=False)
        if n % 25 == 0:
            print(f"  ... {n}/{len(ids)}")

    def pct(o, m):
        return 0.0 if o == 0 else 100.0 * (o - m) / o

    n = len(ids)
    print(f"\nN = {n}   applied-cleanly = {applied_ok}/{n}   "
          f"failures = {len(failures)}")
    print(f"agent rm-collateral deletions reclaimed as authored: "
          f"{del_lines_folded} lines")
    print("\n=== CLEAN AGENT PATCH (reconstructed) vs MINIMIZED ===")
    print(f"  edits  {edits[0]:6d} -> {edits[1]:<6d}  {pct(*edits):5.1f}%")
    print(f"  hunks  {clean['h']:6d} -> {mini['h']:<6d}  {pct(clean['h'], mini['h']):5.1f}%")
    print(f"  lines  {clean['l']:6d} -> {mini['l']:<6d}  {pct(clean['l'], mini['l']):5.1f}%")
    if del_no_rm:
        # deletions with no bash rm evidence -- NOT folded in; surfaced for review
        print(f"\n[deletions WITHOUT a bash rm in traj (left out, review): "
              f"{len(del_no_rm)}]")
        for iid, p in del_no_rm[:25]:
            print(f"  {iid}: {p}")
    if failures:
        print(f"\n[apply failures: {len(failures)}]")
        for iid, err in failures[:25]:
            print(f"  {iid}: {err}")


if __name__ == "__main__":
    main()
