"""Batch-run the SCBench alternate slop metric over the canonical SWE-bench set.

Dataset: ``z-validate-swebench-dataset/minimization_333`` (the same canonical
corpus used for the compaction numbers). Per instance we read:
    <id>/standalone__<id>/original_clean.patch   (agent patch, base-env churn removed)
    <id>/standalone__<id>/minimized.patch        (TRIM-minimized patch)

For each instance we compute SCBench slop (verbosity / erosion / components) on
the agent patch and the minimized patch, then the reduction (before - after),
in the paper's two scopes (compute_compaction.py): ``unfiltered`` (whole agent
submission vs whole minimized) and ``filtered`` (drop NEW files the minimizer
removed; keep modified files + surviving new files).

Instances whose two patches are byte-identical get reduction 0 analytically
(no Docker work). Usage:

    source ~/.cache/scbench_metric/ENV.sh   # or set SCBENCH_METRIC_HOME
    python run_swebench.py --out alternate_metric/scbench_results \\
        [--workers 4] [--limit N] [--only django__django-15128 ...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# AIDEV-NOTE: Kernel_Agent.tools.__init__ eagerly imports the whole kernel tool
# framework (KBDr_Runner etc.). This slop-metric tool is standalone, so import
# the sibling module directly and fall back to a path insert when the parent
# package can't initialize (e.g. KBDr_Runner absent).
try:
    from patch_minimizer.metrics.scbench_metric.scbench_runner import (
        SCBenchError,
        SCBenchMetricRunner,
        SWEBenchRepoProvider,
        changed_files,
        evaluate_triple,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from scbench_runner import (  # type: ignore[no-redef]
        SCBenchError,
        SCBenchMetricRunner,
        SWEBenchRepoProvider,
        changed_files,
        evaluate_triple,
    )


def _flagged(per_file: dict, path: str) -> int:
    return per_file.get(path, {}).get("verbosity_flagged_lines", 0)


def introduced_removed(states: dict) -> tuple[int, int]:
    """(introduced, removed) verbosity: agent-original, agent-minimized."""
    o, a, m = states["original"], states["agent"], states["minimized"]
    paths = set(o) | set(a) | set(m)
    introduced = sum(_flagged(a, p) - _flagged(o, p) for p in paths)
    removed = sum(_flagged(a, p) - _flagged(m, p) for p in paths)
    return introduced, removed

DEFAULT_DATASET = Path("z-validate-swebench-dataset/minimization_333")
_ZERO = {k: 0 for k in ("verbosity_flagged_lines", "verbosity_pct",
                        "erosion_high_cc_pct", "clone_lines",
                        "ast_grep_violations", "cc_sum")}


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def discover_instances(dataset: Path) -> list[dict]:
    """Return per-instance {id, orig, min, identical} records."""
    out = []
    for inst_dir in sorted(p for p in dataset.iterdir() if p.is_dir()):
        inst = inst_dir.name
        sd = inst_dir / f"standalone__{inst}"
        orig, mini = sd / "original_clean.patch", sd / "minimized.patch"
        if not (orig.exists() and mini.exists()):
            continue
        out.append({
            "id": inst, "orig": orig, "min": mini,
            "identical": _md5(orig) == _md5(mini),
        })
    return out


def process(rec: dict, runner: SCBenchMetricRunner) -> dict:
    inst = rec["id"]
    # Measure ALL instances (incl. identical) at three states so we can total
    # the slop the agent INTRODUCED across the whole corpus. Identical patches
    # naturally give removed=0 (agent == minimized).
    union = changed_files(rec["orig"].read_text()) | changed_files(
        rec["min"].read_text())
    if not union:
        return {"instance": inst, "status": "no_python_files"}
    try:
        provider = SWEBenchRepoProvider(inst)
        res = evaluate_triple(runner, provider, rec["orig"], rec["min"])
    except (SCBenchError, Exception) as e:  # noqa: BLE001
        return {"instance": inst, "status": "error", "error": str(e)[:400]}
    if "error" in res:
        return {"instance": inst, "status": "error", **res}
    intro, rem = introduced_removed(res["per_file"])
    return {"instance": inst, "status": "ok", "identical": rec["identical"],
            "introduced": intro, "removed": rem,
            "changed_files": res["changed_files"], "per_file": res["per_file"]}


def summarize(results: list[dict]) -> dict:
    ok = [r for r in results if r.get("status") == "ok"]
    summary = {
        "n_total": len(results),
        "n_ok": len(ok),
        "n_error": sum(1 for r in results if r.get("status") == "error"),
        "n_no_python": sum(1 for r in results if r.get("status") == "no_python_files"),
    }
    # Pure-deletion env-churn outliers (cf. compute_compaction.py); report with
    # and without them.
    outliers = {"matplotlib__matplotlib-24570", "matplotlib__matplotlib-23412"}
    for scope, rows in (("all", ok),
                        ("excl_outliers", [r for r in ok if r["instance"] not in outliers])):
        intro = sum(r["introduced"] for r in rows)
        rem = sum(r["removed"] for r in rows)
        summary[scope] = {
            "introduced_slop": intro,           # agent - original
            "removed_slop": rem,                 # agent - minimized
            "pct_of_introduced_removed": (100.0 * rem / intro) if intro else 0.0,
            "n_with_introduced_slop": sum(1 for r in rows if r["introduced"] > 0),
            "n_with_removal": sum(1 for r in rows if r["removed"] > 0),
        }
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", nargs="*", default=None,
                    help="restrict to these instance ids")
    ap.add_argument("--resume", action="store_true",
                    help="skip instances already present in --out")
    args = ap.parse_args()

    runner = SCBenchMetricRunner()  # validates metric home
    args.out.mkdir(parents=True, exist_ok=True)
    inst_dir = args.out / "instances"
    inst_dir.mkdir(exist_ok=True)

    records = discover_instances(args.dataset)
    if args.only:
        records = [r for r in records if r["id"] in set(args.only)]
    if args.resume:
        records = [r for r in records
                   if not (inst_dir / f"{r['id']}.json").exists()]
    if args.limit:
        records = records[:args.limit]

    print(f"[scbench] {len(records)} instances (3-state measurement), "
          f"workers={args.workers}", flush=True)

    results: list[dict] = []

    def _run(rec):
        res = process(rec, runner)
        (inst_dir / f"{rec['id']}.json").write_text(json.dumps(res, indent=2))
        return res

    def _line(i, res):
        intro = res.get("introduced", "-")
        rem = res.get("removed", "-")
        print(f"[{i}/{len(records)}] {res['instance']}: {res['status']} "
              f"(introduced={intro} removed={rem})", flush=True)

    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(_run, r): r for r in records}
            for i, fut in enumerate(as_completed(futs), 1):
                res = fut.result()
                results.append(res)
                if res["status"] in ("ok", "error"):
                    _line(i, res)
    else:
        for i, rec in enumerate(records, 1):
            res = _run(rec)
            results.append(res)
            _line(i, res)

    summary = summarize(results)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
