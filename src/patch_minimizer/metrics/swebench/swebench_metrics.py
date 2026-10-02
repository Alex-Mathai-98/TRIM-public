#!/usr/bin/env python3
"""SWE-Bench-Verified minimization metrics — reproduces the paper's Table IV (RQ4).

Run from the repository root once the pipeline has produced the results dir
(default ``z-validate-swebench-dataset/minimization_333``):

    python src/patch_minimizer/metrics/swebench/swebench_metrics.py            # all tables
    python src/patch_minimizer/metrics/swebench/swebench_metrics.py --table compaction

End-to-end reproduction order (see the README "SWE-bench Minimization" section):
  1. frontends/swebench/run_swebench_minimization.py     minimize the 333 SWE-agent trajectories
  2. frontends/swebench/run_swebench_postprocessing.py    hidden-oracle eval + gold comparison
  3. frontends/swebench/consolidate_swebench_recoveries.py  merge manual-assert recoveries -> 327 set
  4. frontends/swebench/reconstruct_clean_agent_patch.py   write original_clean.patch (feeds SCBench)
  5. THIS script                                Table IVa / IVb / IVc + the "18"
  6. metrics/scbench_metric/run_swebench.py             Table IVd (SlopCodeBench verbosity)

Sections (--table):
  compaction  Table IVa    edits 23.6%, lines(total) 63.5% (upper bound),
                           lines(modified files) 20.0%  [hunks ~17.8% via the
                           filtered view]
  categories  Table IVb/c  source 20.4%, tests 18.6%, per-file-type + structural
  gold        RQ4          18 minimized patches byte-identical to the developer fix
  all         (default)    all of the above

Instance set: the 333 SWE-agent (Claude-Sonnet-4) trajectories of submission
``20250522_sweagent_claude-4-sonnet-20250514``. Metrics are reported over the 327
resolved->resolved instances (oracle preserved before and after minimization).

Inputs (relative to CWD = repo root):
  z-validate-swebench-dataset/minimization_333/<id>/minimization_results.json
  z-validate-swebench-dataset/minimization_333/<id>/standalone__<id>/minimized.patch
  z-validate-swebench-dataset/minimization_333/minimization_summary.json
  tests/.../trajectory_test_cases_swe_agent_swe_bench_verified_claude-4-sonnet/<id>.traj

Exact-cell note: 23.6% (edits) and 20.4%/18.6% (source/tests) reproduce exactly;
the paper's 63.5% (total lines) is the unfiltered upper bound (~64.2% here) and
17.8% (hunks) is the filtered view (~18.1% here); small deltas are outlier/filter
handling documented inline below.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

# AIDEV-NOTE: set by main() from --save-dir before any report function runs
CANON: Path
# AIDEV-NOTE: set by main() from --traj-dir (was a hard-coded path from the old monorepo).
TRAJ: Path

# Pure-deletion env-churn outliers: the agent diff swept big build-file deletions
# (setup.py/setupext.py) into the submission; the minimizer correctly stripped
# them. They dominate the pooled line metric, so we report with/without them.
OUTLIERS = {"matplotlib__matplotlib-24570", "matplotlib__matplotlib-23412"}
CATS = ["scratch_new", "src", "tests", "docs", "build"]


def resolved(o):
    return bool(o and o.get("resolved") and not o.get("error"))


def resolved_ids():
    """The resolved->resolved bug ids (oracle preserved pre & post)."""
    summ = json.load(open(CANON / "minimization_summary.json"))
    return [e["bug_id"] for e in summ["successful_bugs"]
            if resolved(e.get("oracle_pre")) and resolved(e.get("oracle_post"))]


def split_files(patch):
    """Split a unified diff into per-file blocks: {path, is_new, hunks, lines}."""
    out = []
    if not patch:
        return out
    for part in re.split(r"(?m)^(?=diff --git )", patch):
        if not part.strip():
            continue
        is_new = bool(re.search(r"(?m)^new file mode", part)) or \
            bool(re.search(r"(?m)^--- /dev/null", part))
        m = re.search(r"(?m)^\+\+\+ b/(.+)$", part) or \
            re.search(r"(?m)^diff --git a/\S+ b/(\S+)", part)
        path = m.group(1).strip() if m else "?"
        hunks = len(re.findall(r"(?m)^@@ ", part))
        lines = sum(1 for ln in part.splitlines()
                    if (ln.startswith("+") or ln.startswith("-"))
                    and not ln.startswith("+++") and not ln.startswith("---"))
        out.append({"path": path, "is_new": is_new,
                    "hunks": hunks, "lines": lines})
    return out


def _agent_submission(iid):
    traj = json.load(open(TRAJ / f"{iid}.traj"))
    return traj.get("info", {}).get("submission") or ""


def _minimized_patch(iid, res=None):
    mp = CANON / iid / f"standalone__{iid}" / "minimized.patch"
    if mp.is_file():
        return mp.read_text()
    return (res or {}).get("minimized_patch") or ""


# --------------------------------------------------------------------------- #
# Table IVa — compaction (edits / hunks / lines, filtered + unfiltered)
# --------------------------------------------------------------------------- #
def report_compaction():
    ids = sorted(resolved_ids())
    tot = {"edits": [0, 0], "uf_hunks": [0, 0], "uf_lines": [0, 0],
           "fl_hunks": [0, 0], "fl_lines": [0, 0]}
    tot_x = {k: [0, 0] for k in tot}
    red = {"edits": 0, "uf_hunks": 0, "uf_lines": 0, "fl_hunks": 0, "fl_lines": 0}
    fl_line_pct = []
    missing = []

    for iid in ids:
        res = json.load(open(CANON / iid / "minimization_results.json"))
        oe = res.get("original_edit_count") or 0
        me = res.get("minimized_edit_count") or 0
        orig = _agent_submission(iid)
        mini = _minimized_patch(iid, res)
        if not orig or not mini:
            missing.append(iid)
            continue

        ob = split_files(orig)
        mb = split_files(mini)
        surviving = {b["path"] for b in mb}
        uf_oh = sum(b["hunks"] for b in ob)
        uf_ol = sum(b["lines"] for b in ob)
        fl_oh = sum(b["hunks"] for b in ob
                    if (not b["is_new"]) or (b["path"] in surviving))
        fl_ol = sum(b["lines"] for b in ob
                    if (not b["is_new"]) or (b["path"] in surviving))
        mh = sum(b["hunks"] for b in mb)
        ml = sum(b["lines"] for b in mb)

        tot["edits"][0] += oe; tot["edits"][1] += me
        tot["uf_hunks"][0] += uf_oh; tot["uf_hunks"][1] += mh
        tot["uf_lines"][0] += uf_ol; tot["uf_lines"][1] += ml
        tot["fl_hunks"][0] += fl_oh; tot["fl_hunks"][1] += mh
        tot["fl_lines"][0] += fl_ol; tot["fl_lines"][1] += ml
        if iid not in OUTLIERS:
            tot_x["edits"][0] += oe; tot_x["edits"][1] += me
            tot_x["uf_hunks"][0] += uf_oh; tot_x["uf_hunks"][1] += mh
            tot_x["uf_lines"][0] += uf_ol; tot_x["uf_lines"][1] += ml
            tot_x["fl_hunks"][0] += fl_oh; tot_x["fl_hunks"][1] += mh
            tot_x["fl_lines"][0] += fl_ol; tot_x["fl_lines"][1] += ml
        if fl_ol > 0:
            fl_line_pct.append(100.0 * (fl_ol - ml) / fl_ol)

        red["edits"] += me < oe
        red["uf_hunks"] += mh < uf_oh
        red["uf_lines"] += ml < uf_ol
        red["fl_hunks"] += mh < fl_oh
        red["fl_lines"] += ml < fl_ol

    n = len(ids) - len(missing)
    nx = n - len(OUTLIERS & set(ids))

    def pct(a, b):
        return 0.0 if a == 0 else 100.0 * (a - b) / a

    def row(name, t, reduced, denom):
        o, m = t
        return (f"  {name:13s} {o:6d} -> {m:<6d}  {pct(o, m):5.1f}%   "
                f"reduced {reduced}/{denom}")

    print("=" * 70)
    # AIDEV-NOTE: headers print the real count; they used to hard-code the paper's 327.
    print(f"TABLE IVa — compaction ({n} resolved->resolved instances)")
    print("=" * 70)
    print(f"N = {n} instances ({len(missing)} missing: {missing})")
    print(f"Lines = TOTAL changed lines (+ and -). Outliers excluded on the "
          f"right-hand view: {sorted(OUTLIERS)}\n")
    print("                  orig ->  min    %comp   #reduced")
    print("--- EDITS (minimizer reduction; scratch-filtered, one value) ---")
    print(row("edits", tot["edits"], red["edits"], n))
    print("\n--- UNFILTERED (upper bound: whole submission vs whole min) ---")
    print(row("hunks", tot["uf_hunks"], red["uf_hunks"], n))
    print(row("lines", tot["uf_lines"], red["uf_lines"], n))
    print("\n--- FILTERED (drop removed-scratch +A new files) ---")
    print(row("hunks", tot["fl_hunks"], red["fl_hunks"], n))
    print(row("lines", tot["fl_lines"], red["fl_lines"], n))
    print(f"\n--- SAME, EXCLUDING {len(OUTLIERS)} pure-deletion outliers "
          f"(N={nx}) ---")
    print(row("uf lines", tot_x["uf_lines"], "-", nx))
    print(row("fl lines", tot_x["fl_lines"], "-", nx))
    print("\n--- FILTERED line compaction, per-instance distribution ---")
    print(f"  mean   {statistics.mean(fl_line_pct):.1f}%")
    print(f"  median {statistics.median(fl_line_pct):.1f}%")
    sb = tot["uf_lines"][0] - tot["fl_lines"][0]
    sh = tot["uf_hunks"][0] - tot["fl_hunks"][0]
    print(f"\n  (scratch +A removed by filter: {sb} lines, {sh} hunks)")


# --------------------------------------------------------------------------- #
# Table IVb / IVc — file categories (source 20.4%, tests 18.6%, structural)
# --------------------------------------------------------------------------- #
def categorize(path, is_new):
    if is_new:
        return "scratch_new"
    p = path.lower()
    base = p.rsplit("/", 1)[-1]
    if base in ("setup.py", "setupext.py", "setup.cfg", "pyproject.toml",
                "tox.ini", "manifest.in", "makefile", "conftest.py",
                "noxfile.py", "environment.yml") or base.startswith("requirements") \
            or p.endswith((".cfg", ".ini", ".toml", ".yml", ".yaml")):
        return "build"
    if re.search(r"(^|/)tests?/", p) or re.search(r"(^|/)testing/", p) \
            or base.startswith("test_") or base.endswith("_test.py") \
            or "/_pytest/" in p:
        return "tests"
    if re.search(r"(^|/)docs?/", p) or p.endswith((".rst", ".md", ".txt")) \
            or "changelog" in base or "whatsnew" in p or base.startswith("changes"):
        return "docs"
    return "src"


def report_categories():
    ids = resolved_ids()
    touched = defaultdict(int)
    survived = defaultdict(int)
    dropped = defaultdict(int)
    o_lines = defaultdict(int); m_lines = defaultdict(int)
    o_hunks = defaultdict(int); m_hunks = defaultdict(int)
    inst_with_cat = defaultdict(set)

    for iid in ids:
        orig = _agent_submission(iid)
        mini = _minimized_patch(iid)
        ob = split_files(orig)
        mb = split_files(mini)
        msurv = {b["path"]: b for b in mb}
        for b in ob:
            c = categorize(b["path"], b["is_new"])
            touched[c] += 1
            inst_with_cat[c].add(iid)
            o_lines[c] += b["lines"]; o_hunks[c] += b["hunks"]
            if b["path"] in msurv:
                survived[c] += 1
                mb_ = msurv[b["path"]]
                m_lines[c] += mb_["lines"]; m_hunks[c] += mb_["hunks"]
            else:
                dropped[c] += 1

    def pc(o, m):
        return 0.0 if o == 0 else 100.0 * (o - m) / o

    print("=" * 108)
    print("TABLE IVb / IVc — file categories")
    print("=" * 108)
    print(f"N = {len(ids)} instances\n")
    print("Category      files  survive  dropped   | orig_lines->min   %comp  "
          "| orig_hunks->min  %comp  | #insts")
    print("-" * 108)
    for c in CATS:
        print(f"{c:12s}  {touched[c]:5d}  {survived[c]:7d}  {dropped[c]:7d}   "
              f"| {o_lines[c]:7d}->{m_lines[c]:<6d} {pc(o_lines[c], m_lines[c]):5.1f}%  "
              f"| {o_hunks[c]:6d}->{m_hunks[c]:<5d} {pc(o_hunks[c], m_hunks[c]):5.1f}%  "
              f"| {len(inst_with_cat[c])}")
    print("-" * 108)
    tt = sum(touched.values()); ts = sum(survived.values())
    td = sum(dropped.values())
    tol = sum(o_lines.values()); tml = sum(m_lines.values())
    toh = sum(o_hunks.values()); tmh = sum(m_hunks.values())
    print(f"{'TOTAL':12s}  {tt:5d}  {ts:7d}  {td:7d}   "
          f"| {tol:7d}->{tml:<6d} {pc(tol, tml):5.1f}%  "
          f"| {toh:6d}->{tmh:<5d} {pc(toh, tmh):5.1f}%  |")
    # Table IVa "Lines (Mod. files)" = source + tests modified-file lines only.
    mf_o = o_lines["src"] + o_lines["tests"]
    mf_m = m_lines["src"] + m_lines["tests"]
    print(f"\nTable IVa 'Lines (Mod. files)' = source+tests: "
          f"{mf_o}->{mf_m}  {pc(mf_o, mf_m):.1f}%")
    print("\nWhat minimization DROPS entirely, by category "
          "(share of all dropped file-blocks):")
    for c in CATS:
        if td and dropped[c]:
            print(f"  {c:12s} {dropped[c]:4d}  ({100*dropped[c]/td:.0f}% of drops)")


# --------------------------------------------------------------------------- #
# RQ4 — agreement with the developer (gold) patch: the "18"
# --------------------------------------------------------------------------- #
def _tiers_from_gc(gc):
    """(byte, edit, file) booleans from a stored gold_comparison dict."""
    if not gc:
        return False, False, False
    byte = bool(gc.get("byte_equivalent_mod_index"))
    same_files = bool(gc.get("same_file_set")) \
        and not gc.get("extra_files_in_minimized") \
        and not gc.get("missing_files_vs_gold")
    edit = bool(byte or (
        same_files
        and gc.get("minimized_added_lines") == gc.get("gold_added_lines")
        and gc.get("minimized_deleted_lines") == gc.get("gold_deleted_lines")
        and gc.get("minimized_hunks") == gc.get("gold_hunks")))
    return byte, edit, same_files


def _norm_bytes(patch):
    if not patch:
        return ""
    return "\n".join(ln.rstrip() for ln in patch.splitlines()
                     if not ln.startswith("index ")).strip("\n")


def _summarize(patch):
    files, added, deleted, hunks = set(), 0, 0, 0
    if not patch:
        return files, added, deleted, hunks
    for part in re.split(r"(?m)^(?=diff --git )", patch):
        if not part.strip():
            continue
        m = re.search(r"(?m)^\+\+\+ b/(.+)$", part) or \
            re.search(r"(?m)^diff --git a/\S+ b/(\S+)", part)
        if not m:
            continue
        files.add(m.group(1).strip())
        hunks += len(re.findall(r"(?m)^@@ ", part))
        for ln in part.splitlines():
            if ln.startswith("+++") or ln.startswith("---"):
                continue
            if ln.startswith("+"):
                added += 1
            elif ln.startswith("-"):
                deleted += 1
    return files, added, deleted, hunks


def _tiers_vs_gold(patch, gold):
    if not patch or not gold:
        return False, False, False
    byte = _norm_bytes(patch) == _norm_bytes(gold)
    pf, pa, pd, ph = _summarize(patch)
    gf, ga, gd, gh = _summarize(gold)
    same_files = (pf == gf) and bool(gf)
    edit = bool(byte or (same_files and pa == ga and pd == gd and ph == gh))
    return byte, edit, same_files


def _load_gold_optional():
    """Gold *solution* patches keyed by instance_id, or None if unavailable.

    Uses the public HuggingFace dataset (never test_patch / FAIL_TO_PASS); the
    gold is only ever used for reporting, never fed back into minimization.
    """
    try:
        from datasets import load_dataset
        ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
        return {r["instance_id"]: r["patch"] for r in ds}
    except Exception as e:  # datasets missing / offline
        print(f"  (pre/post movement skipped: gold dataset unavailable — {e})")
        return None


def report_gold():
    summ = json.load(open(CANON / "minimization_summary.json"))
    resolved_set = {e["bug_id"] for e in summ["successful_bugs"]
                    if resolved(e.get("oracle_pre")) and resolved(e.get("oracle_post"))}

    rows = []
    for d in sorted(CANON.glob("*/minimization_results.json")):
        r = json.load(open(d))
        gc = r.get("gold_comparison")
        if gc is None:
            continue
        byte, edit, fileq = _tiers_from_gc(gc)
        rows.append((r["bug_id"], byte, edit, fileq,
                     r.get("gold_equivalence"), r["bug_id"] in resolved_set))

    def report(label, sub):
        n = len(sub)
        byte = sum(1 for x in sub if x[1])
        edit = sum(1 for x in sub if x[2])
        fileq = sum(1 for x in sub if x[3])
        eqcat = Counter(x[4] for x in sub)
        print(f"=== {label}  (N={n}) ===")
        print(f"  byte-identical to human : {byte:4d}  ({100*byte/n:.1f}%)")
        print(f"  edit-identical to human : {edit:4d}  ({100*edit/n:.1f}%)")
        print(f"  same-files as human     : {fileq:4d}  ({100*fileq/n:.1f}%)")
        print("  gold_equivalence breakdown:")
        for k, v in eqcat.most_common():
            print(f"      {str(k):24s} {v:4d}")
        print()

    print("=" * 70)
    print("RQ4 — agreement with the developer (gold) patch")
    print("=" * 70)
    report("resolved->resolved", [x for x in rows if x[5]])
    report("all enriched", rows)
    print("Byte-identical instances (resolved set):")
    for x in rows:
        if x[1] and x[5]:
            print(f"  {x[0]}")

    # Optional PRE- vs POST-minimization movement toward the gold fix.
    gold = _load_gold_optional()
    if not gold:
        return
    ids = sorted(resolved_set)
    pre = {"byte": 0, "edit": 0, "file": 0}
    post = {"byte": 0, "edit": 0, "file": 0}
    n = 0
    for iid in ids:
        g = gold.get(iid)
        if g is None:
            continue
        orig = _agent_submission(iid)
        mini = _minimized_patch(iid)
        if not orig or not mini:
            continue
        n += 1
        ob, oe, of = _tiers_vs_gold(orig, g)
        mb, me, mf = _tiers_vs_gold(mini, g)
        pre["byte"] += ob; pre["edit"] += oe; pre["file"] += of
        post["byte"] += mb; post["edit"] += me; post["file"] += mf

    def line(label, d):
        return (f"  {label:42s} "
                f"{d['byte']:3d} ({100*d['byte']/n:.1f}%)   "
                f"{d['edit']:3d} ({100*d['edit']/n:.1f}%)   "
                f"{d['file']:3d} ({100*d['file']/n:.1f}%)")

    print(f"\nPRE vs POST movement toward gold  (N={n})")
    print(f"  {'':42s} {'byte-identical':>14s}   "
          f"{'same-edit':>14s}   {'same-files':>14s}")
    print(line("PRE-minimization (agent raw submission)", pre))
    print(line("POST-minimization (minimized patch)    ", post))


SECTIONS = {
    "compaction": report_compaction,
    "categories": report_categories,
    "gold": report_gold,
}


def main():
    global CANON, TRAJ
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--save-dir", type=Path, required=True,
                    help="results directory")
    ap.add_argument("--traj-dir", type=Path, default=Path("results/swe-bench-swe-agent-trajs"),
                    help="Directory of <instance_id>.traj files "
                         "(default: results/swe-bench-swe-agent-trajs).")
    ap.add_argument("--table", choices=[*SECTIONS, "all"], default="all",
                    help="Which section to print (default: all).")
    args = ap.parse_args()
    CANON = args.save_dir
    TRAJ = args.traj_dir
    order = list(SECTIONS) if args.table == "all" else [args.table]
    for i, key in enumerate(order):
        if i:
            print("\n")
        SECTIONS[key]()


if __name__ == "__main__":
    main()
