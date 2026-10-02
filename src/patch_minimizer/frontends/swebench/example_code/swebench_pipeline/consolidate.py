#!/usr/bin/env python3
"""Consolidate manual-assert recovery runs into the canonical minimization
results and re-aggregate minimization_summary.json (aggregated_results /
oracle_transitions / gold_equivalence). Recovery outputs are read from ``REMIN``
(the ``--save-dir`` of the ``--manual-asserts-dir`` re-minimization; default
/tmp/remin); only instances whose oracle_post is genuinely resolved are merged,
while flaky/unresolved ones are skipped and reported."""
import json
import os
import shutil
from collections import Counter
from pathlib import Path

import argparse

DEFAULT_CANON = Path("z-validate-swebench-dataset/minimization_333")
DEFAULT_REMIN = Path("/tmp/remin")




def cnt(v):
    return len(v) if isinstance(v, (list, tuple)) else (v or 0)


def post_clean(d):
    op = d.get("oracle_post") or {}
    return bool(op.get("resolved") and op.get("patch_applied")
               and cnt(op.get("f2p_fail")) == 0 and cnt(op.get("p2p_fail")) == 0)


def classify(pre, post):
    if pre is None and post is None:
        return "no_oracle"
    pre, post = pre or {}, post or {}
    if pre.get("error") or post.get("error"):
        return "pre_error_or_post_error"
    return "pre_%s_post_%s" % (
        "resolved" if pre.get("resolved") else "unresolved",
        "resolved" if post.get("resolved") else "unresolved")

def main(argv: list[str] | None = None) -> int:
    """Merge clean recoveries from ``--remin`` into ``--canon`` and re-aggregate.

    AIDEV-NOTE: This body used to execute at IMPORT time — the module had no main() and
    no __main__ guard, so `import consolidate` mutated a results directory. Wrapped in
    z-cpl-44 step 5. CANON/REMIN became flags with the old literals as defaults.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--canon", type=Path, default=DEFAULT_CANON,
                    help=f"Canonical results dir. Default: {DEFAULT_CANON}")
    ap.add_argument("--remin", type=Path, default=DEFAULT_REMIN,
                    help=f"Recovery run save-dir to merge from. Default: {DEFAULT_REMIN}")
    args = ap.parse_args(argv)
    canon, remin = args.canon, args.remin

    # --- 1. merge clean recoveries into canonical -------------------------------
    recovered, skipped = {}, []
    for resf in sorted(remin.glob("*/*/minimization_results.json")):
        d = json.load(open(resf))
        iid = d["bug_id"]
        if not post_clean(d):
            op = d.get("oracle_post") or {}
            skipped.append((iid, op.get("error") or "post_unresolved"))
            continue
        cdir = canon / iid
        if not cdir.is_dir():
            skipped.append((iid, "no canonical dir"))
            continue
        canon_res = json.load(open(cdir / "minimization_results.json"))
        # Merge changed fields into the canonical per-instance record.
        for k in ("minimized_patch", "reduction", "original_edit_count",
                  "minimized_edit_count", "feedback_stats", "original_patch_list",
                  "oracle_post", "gold_comparison", "gold_equivalence"):
            if k in d:
                canon_res[k] = d[k]
        # oracle_pre: prefer a resolved value (recovered re-run pre can flake to no_report).
        rec_pre = d.get("oracle_pre") or {}
        canon_pre = canon_res.get("oracle_pre") or {}
        if rec_pre.get("resolved"):
            canon_res["oracle_pre"] = rec_pre
        elif not canon_pre.get("resolved"):
            canon_res["oracle_pre"] = rec_pre  # neither resolved; take recovered
        # else keep canonical's resolved pre
        # write per-instance record
        tmp = str(cdir / "minimization_results.json") + ".tmp"
        json.dump(canon_res, open(tmp, "w"), indent=2)
        os.replace(tmp, cdir / "minimization_results.json")
        # copy the recovered minimized.patch
        src = resf.parent / f"standalone__{iid}" / "minimized.patch"
        dst = cdir / f"standalone__{iid}" / "minimized.patch"
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_file():
            shutil.copyfile(src, dst)
        recovered[iid] = canon_res

    # --- 2. update summary entries + recompute aggregates -----------------------
    sp = canon / "minimization_summary.json"
    data = json.load(open(sp))
    for e in data["successful_bugs"]:
        iid = e.get("bug_id")
        if iid not in recovered:
            continue
        r = recovered[iid]
        e["best_reduction"] = r.get("reduction")
        e["oracle_pre"] = r.get("oracle_pre")
        e["oracle_post"] = r.get("oracle_post")
        if r.get("gold_comparison") is not None:
            e["gold_comparison"] = r["gold_comparison"]
        if r.get("gold_equivalence") is not None:
            e["gold_equivalence"] = r["gold_equivalence"]
        if r.get("feedback_stats") is not None:
            e["feedback_stats"] = r["feedback_stats"]




    agg = Counter(e["best_reduction"] for e in data["successful_bugs"] if e.get("best_reduction"))
    trans = {k: 0 for k in ("pre_resolved_post_resolved", "pre_resolved_post_unresolved",
                            "pre_unresolved_post_resolved", "pre_unresolved_post_unresolved",
                            "pre_error_or_post_error", "no_oracle")}
    equiv = {}
    for e in data["successful_bugs"]:
        pre, post = e.get("oracle_pre"), e.get("oracle_post")
        if pre is None and post is None:
            continue
        trans[classify(pre, post)] += 1
        ge = e.get("gold_equivalence")
        if ge is not None:
            equiv[ge] = equiv.get(ge, 0) + 1

    data["aggregated_results"] = dict(agg)
    data["oracle_transitions"] = trans
    data["gold_equivalence"] = equiv
    tmp = str(sp) + ".tmp"
    json.dump(data, open(tmp, "w"), indent=2)
    os.replace(tmp, sp)

    print("CONSOLIDATED (%d):" % len(recovered), ", ".join(sorted(recovered)))
    print("\nSKIPPED (%d):" % len(skipped))
    for iid, why in sorted(skipped):
        print("  %-34s %s" % (iid, why))
    print("\nNEW oracle_transitions:", trans)
    print("aggregated_results sum:", sum(agg.values()))

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
