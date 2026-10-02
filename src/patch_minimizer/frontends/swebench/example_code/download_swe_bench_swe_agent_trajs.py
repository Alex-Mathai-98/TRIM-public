"""Download SWE-agent SWE-bench Verified trajectories for resolved instances.

Reads ``results.json`` from the SWE-bench/experiments GitHub repo to identify
resolved instance_ids, then downloads each instance's ``.traj`` file from the
public S3 bucket referenced by the submission's ``metadata.yaml``
(``s3://swe-bench-submissions/verified/<submission>/trajs/``).

Used by M1.A (proxy-test-edit profiling) — see ``swe-bench-plan.md`` §13.

Usage:
    python download_swe_bench_swe_agent_trajs.py \\
        --submission 20250225_sweagent_claude-3-7-sonnet \\
        --output-dir <local dir> \\
        [--max-instances N] [--workers 16]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# AIDEV-NOTE: SWE-bench leaderboard submissions store results.json in
# this GitHub repo and trajectories in a public S3 bucket. The metadata.yaml
# in each submission folder references the S3 path under `assets.trajs`.
# [AI-M1.A]
GITHUB_RAW = "https://raw.githubusercontent.com/SWE-bench/experiments/main"
S3_BASE = "https://swe-bench-submissions.s3.amazonaws.com"


def fetch_resolved(submission: str) -> list[str]:
    """Fetch resolved instance_ids for a submission from results.json."""
    url = f"{GITHUB_RAW}/evaluation/verified/{submission}/results/results.json"
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.load(resp)
    return list(data.get("resolved", []))


def download_one(submission: str, instance_id: str, out_dir: Path) -> tuple[str, bool, str]:
    """Download one .traj file. Returns ``(instance_id, ok, msg)``.

    Different SWE-agent submissions use different S3 path layouts:
      - Older (e.g. 20240620_sweagent_claude3.5sonnet): trajs/<id>.traj
      - Newer (e.g. 20250522_sweagent_claude-4-sonnet-...): trajs/<id>/<id>.traj
    We try the flat layout first and fall back to nested.
    """
    out_path = out_dir / f"{instance_id}.traj"
    if out_path.exists() and out_path.stat().st_size > 0:
        return (instance_id, True, "skip (exists)")

    candidates = [
        f"{S3_BASE}/verified/{submission}/trajs/{instance_id}.traj",
        f"{S3_BASE}/verified/{submission}/trajs/{instance_id}/{instance_id}.traj",
    ]
    last_err = "no candidates"
    for url in candidates:
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                content = resp.read()
            out_path.write_bytes(content)
            return (instance_id, True, f"{len(content)} bytes")
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code != 404:
                return (instance_id, False, last_err)
        except Exception as e:  # network / timeout / etc.
            return (instance_id, False, f"{type(e).__name__}: {e}")
    return (instance_id, False, last_err)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--submission", required=True,
        help="Submission folder under evaluation/verified/ "
             "(e.g. 20250225_sweagent_claude-3-7-sonnet)",
    )
    ap.add_argument(
        "--output-dir", required=True,
        help="Local directory to save .traj files (created if missing)",
    )
    ap.add_argument(
        "--max-instances", type=int, default=None,
        help="Cap on trajectories to download (default: all resolved)",
    )
    ap.add_argument(
        "--workers", type=int, default=16,
        help="Concurrent downloads (default: 16)",
    )
    args = ap.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/3] Fetching resolved list for {args.submission}...", file=sys.stderr)
    resolved = fetch_resolved(args.submission)
    print(f"      {len(resolved)} resolved instances", file=sys.stderr)

    if args.max_instances is not None:
        resolved = resolved[: args.max_instances]
        print(f"      Capped to {len(resolved)}", file=sys.stderr)

    print(
        f"[2/3] Downloading {len(resolved)} trajectories to {out_dir} "
        f"(workers={args.workers})...",
        file=sys.stderr,
    )
    ok_count = 0
    fail_count = 0
    skip_count = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {
            ex.submit(download_one, args.submission, iid, out_dir): iid
            for iid in resolved
        }
        for i, fut in enumerate(as_completed(futs), 1):
            iid, ok, msg = fut.result()
            if ok:
                if "skip" in msg:
                    skip_count += 1
                else:
                    ok_count += 1
            else:
                fail_count += 1
                print(f"  [FAIL] {iid}: {msg}", file=sys.stderr)
            if i % 25 == 0 or i == len(resolved):
                print(f"  ... {i}/{len(resolved)}", file=sys.stderr)

    print(
        f"[3/3] done. ok={ok_count} skip={skip_count} fail={fail_count}",
        file=sys.stderr,
    )
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
