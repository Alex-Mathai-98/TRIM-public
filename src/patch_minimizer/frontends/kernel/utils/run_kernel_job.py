"""Sample script demonstrating how to run a kernel job from patch_minimizer.

This is the patch_minimizer equivalent of Kernel_Agent/sample_kernel_job.py,
adapted to use local types and self-contained parameter building (no kcomp
at import time). In stub mode, completes instantly without real kGym.

Prerequisites:
  Environment variables (or use --stub for testing):
    KBENCH_PATH  — path to populated Kernel_Benchmark folder
    KAGENT_STUB_MODE=true  — enable stub mode (bypasses real kGym)
    KAGENT_STUB_ITERATIONS=999999  — use stubs for all jobs

Usage:
    # Stub mode (no kGym needed, completes instantly):
    KAGENT_STUB_MODE=true KAGENT_STUB_ITERATIONS=999999 \\
        PYTHONPATH=src python src/patch_minimizer/frontends/kernel/utils/run_kernel_job.py \\
        --bug-id <BUG_HASH> --kbench-path /path/to/benchmark

    # With real kGym (needs KBDr_Runner + env vars):
    source dev.env && PYTHONPATH=src \\
        python src/patch_minimizer/frontends/kernel/utils/run_kernel_job.py --bug-id <BUG_HASH>

    # Dry run (print parameters only):
    PYTHONPATH=src python src/patch_minimizer/frontends/kernel/utils/run_kernel_job.py --dry-run

    # Stub mode shortcut:
    PYTHONPATH=src python src/patch_minimizer/frontends/kernel/utils/run_kernel_job.py --stub
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys


def _bootstrap_env():
    """Auto-detect project root and ensure src/ is on sys.path."""
    here = os.path.abspath(os.path.dirname(__file__))
    candidate = here
    for _ in range(5):
        candidate = os.path.dirname(candidate)
        if os.path.isdir(os.path.join(candidate, "src", "patch_minimizer")):
            src = os.path.join(candidate, "src")
            if src not in sys.path:
                sys.path.insert(0, src)
            return
    # AIDEV-NOTE: Fallback — add parent of runners/ to path
    sys.path.insert(0, os.path.dirname(os.path.dirname(here)))


_bootstrap_env()


def main():
    parser = argparse.ArgumentParser(
        description="Submit a sample kernel reproduction job from patch_minimizer"
    )
    parser.add_argument(
        "--bug-id",
        default="11acaa6d5c31d0b655997957f725da4a3cc05435",
        help="Bug ID (hash) from the benchmark dataset",
    )
    parser.add_argument(
        "--kbench-path",
        default=None,
        help="Path to Kernel_Benchmark folder (overrides KBENCH_PATH env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print job parameters without submitting",
    )
    parser.add_argument(
        "--stub",
        action="store_true",
        help="Enable stub mode (sets KAGENT_STUB_MODE=true, KAGENT_STUB_ITERATIONS=999999)",
    )
    parser.add_argument(
        "--patch-file",
        default=None,
        help="Path to a .patch file to apply before building",
    )
    parser.add_argument(
        "--reproducer-type",
        choices=["c", "log"],
        default="c",
        help="Reproducer type (default: c)",
    )
    parser.add_argument(
        "--syzkaller-tag",
        choices=["master", "kgym-latest"],
        default="kgym-latest",
        help="Syzkaller rollback tag (default: kgym-latest)",
    )
    parser.add_argument(
        "--kcache-url",
        default=None,
        help="Kcache URL for incremental build (optional)",
    )
    args = parser.parse_args()

    # -------------------------------------------------------------------------
    # 0. Set up stub mode if requested
    # -------------------------------------------------------------------------
    if args.stub:
        os.environ["KAGENT_STUB_MODE"] = "true"
        os.environ["KAGENT_STUB_ITERATIONS"] = "999999"
        print("[STUB MODE ENABLED] All jobs will use stub responses.\n")

    # -------------------------------------------------------------------------
    # 1. Validate environment
    # -------------------------------------------------------------------------
    kbench_path = args.kbench_path or os.getenv("KBENCH_PATH")
    if not kbench_path:
        print("ERROR: KBENCH_PATH not set. Use --kbench-path or set KBENCH_PATH env var.")
        sys.exit(1)

    from patch_minimizer.benchmark_utils.kernel.stub_responses import StubResponseFactory

    is_stub = StubResponseFactory.is_stub_mode()
    if not is_stub:
        api_url = os.getenv("KBDR_RUNNER_API_BASE_URL")
        if not api_url:
            print(
                "ERROR: KBDR_RUNNER_API_BASE_URL not set and stub mode not enabled.\n"
                "Use --stub for testing without kGym, or set the env var."
            )
            sys.exit(1)

    # -------------------------------------------------------------------------
    # 2. Load bug data
    # -------------------------------------------------------------------------
    from patch_minimizer.benchmark_utils.kernel.bug_data import BugData

    bug_data_path = os.path.join(kbench_path, f"{args.bug_id}.json")
    if not os.path.exists(bug_data_path):
        bug_data_path = os.path.join(kbench_path, args.bug_id, "original_data.json")
    if not os.path.exists(bug_data_path):
        print(f"ERROR: Bug data not found for '{args.bug_id}' in {kbench_path}")
        sys.exit(1)

    bug_data = BugData.from_json_file(bug_data_path)
    print(f"Loaded bug: {bug_data.title}")
    print(f"  ID:         {bug_data.id}")
    print(f"  Arch:       {bug_data.get_architecture()}")
    print(f"  Compiler:   {bug_data.get_compiler()}")

    # -------------------------------------------------------------------------
    # 3. Load optional patch
    # -------------------------------------------------------------------------
    patch = ""
    if args.patch_file:
        with open(args.patch_file) as f:
            patch = f.read()
        print(f"  Patch:      {args.patch_file} ({len(patch)} bytes)")
    else:
        print("  Patch:      (none — reproducing original crash)")

    # -------------------------------------------------------------------------
    # 4. Build job parameters using KReproducer (faithful copy of Kernel_Agent)
    # -------------------------------------------------------------------------
    from patch_minimizer.benchmark_utils.kernel.kernel_runner import KReproducer

    print(f"\n  Kcache:     {'provided' if args.kcache_url else 'not provided'}")
    print(f"  Stub mode:  {is_stub}")
    print()

    complete_argument_dict = KReproducer.fill_kbuilder_kvm_manager_params(
        data_path=kbench_path,
        bug_id=args.bug_id,
        user_img="buildroot.raw",
        get_parent_commit=True,
        reproducer_type=args.reproducer_type,
        ninstance=1,
        patch=patch,
        kcache_url=args.kcache_url,
        nproc=8,
        threaded=True,
        syzkaller_rollback_tag=args.syzkaller_tag,
    )

    # -------------------------------------------------------------------------
    # 5. Print job summary
    # -------------------------------------------------------------------------
    print("=" * 60)
    print("JOB PARAMETERS")
    print("=" * 60)
    print(f"  Bug ID:     {complete_argument_dict['bug_id']}")

    builder_params = complete_argument_dict.get("kvm_builder_parameters")
    if builder_params:
        has_kcache = builder_params.kcache_url is not None
        print(f"  Build mode: {'kcache' if has_kcache else 'from scratch'}")
        if has_kcache:
            print(f"  Kcache URL: {builder_params.kcache_url[:80]}...")
        if builder_params.kernel_commit_id:
            print(f"  Commit:     {builder_params.kernel_commit_id}")
        print(f"  Has patch:  {builder_params.has_patch()}")

    manager_params = complete_argument_dict.get("kvm_manager_parameters")
    if manager_params:
        manager_dict = manager_params.to_dict()
        repro = manager_dict.get("reproducer", {})
        print(f"  Reproducer: {repro.get('reproducer-type', 'unknown')}")
        print(f"  Machine:    {manager_dict.get('machine-type', 'unknown')}")

    print("=" * 60)

    if args.dry_run:
        print("\n[DRY RUN] Skipping job submission.")
        if builder_params:
            builder_dict = builder_params.to_dict()
            print(f"\n  Builder keys: {list(builder_dict.keys())}")
        if manager_params:
            manager_dict = manager_params.to_dict()
            print(f"  Manager keys: {list(manager_dict.keys())}")
        return

    # -------------------------------------------------------------------------
    # 6. Submit the job
    # -------------------------------------------------------------------------
    print("\nSubmitting job...")
    job_id = KReproducer.execute_bug_reproduction(complete_argument_dict)

    if job_id is None:
        print("ERROR: Job submission failed.")
        sys.exit(1)

    job_id = job_id.replace('"', "")
    print(f"Job submitted successfully!")
    print(f"  Job ID: {job_id}")

    # -------------------------------------------------------------------------
    # 7. Wait for job completion
    # -------------------------------------------------------------------------
    from patch_minimizer.benchmark_utils.kernel.kernel_runner import wait_for_job

    log = logging.getLogger("run_kernel_job")
    log.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s"))
    log.addHandler(handler)

    print("\nWaiting for job to complete...")
    stopped_wait, job_results = wait_for_job(log, job_id)

    # -------------------------------------------------------------------------
    # 8. Display results
    # -------------------------------------------------------------------------
    from patch_minimizer.benchmark_utils.kernel.kgym_types import (
        JobStatus,
        SpecialConditions,
    )

    print("\n" + "=" * 60)
    print("JOB RESULTS")
    print("=" * 60)

    if stopped_wait:
        print("  Status: TIMED OUT (waited > 50 minutes)")
        sys.exit(1)

    status = job_results.get_status()
    print(f"  Status:            {status.value}")

    if status == JobStatus.FINISHED:
        special = job_results.get_special_status()
        crash_desc = job_results.get_crash_description()

        if special == SpecialConditions.MESSAGE_NO_CRASH:
            print("  Crash reproduced:  NO")
            if patch:
                print("  Interpretation:    Patch likely FIXES the bug!")
            else:
                print("  Interpretation:    Bug did not reproduce")
        elif crash_desc:
            print("  Crash reproduced:  YES")
            print(f"  Crash description: {crash_desc[:200]}")
            if patch:
                print("  Interpretation:    Patch did NOT fix the bug")
            else:
                print("  Interpretation:    Original bug successfully reproduced")
        else:
            message = job_results.get_message()
            print(f"  Message: {message}")

    elif status == JobStatus.ABORTED:
        print("  Status: ABORTED")
        build_msg = job_results.get_build_message()
        if build_msg:
            print(f"  Build error:       {build_msg}")

    print("=" * 60)
    print(f"\nDone. Job ID: {job_id}")


if __name__ == "__main__":
    main()
