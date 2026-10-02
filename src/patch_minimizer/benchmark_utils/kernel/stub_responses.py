"""Stub response factory for testing kernel jobs without real kGym execution.

Copied from Kernel_Agent.kgym_utils.stub_responses, adapted to use local imports.

AIDEV-NOTE: The ONLY change from the Kernel_Agent original is the import path
for ResponseExtracted (local kgym_types instead of Kernel_Agent.kgym_utils.types).

Usage:
    export KAGENT_STUB_MODE=true
    export KAGENT_STUB_ITERATIONS=2
    # Run your tests - jobs will complete in seconds instead of 15+ minutes
"""

from __future__ import annotations

import os
import uuid
import threading
from typing import Optional

from patch_minimizer.benchmark_utils.kernel.kgym_types import ResponseExtracted


class StubResponseFactory:
    """Factory for creating realistic stub job responses for testing."""

    # AIDEV-NOTE: Fixture paths relative to KAGENT_PATH environment variable
    FIXTURE_BASE = "tests/fixtures/job_responses"

    # Default bug ID used in stub mode (must exist in benchmark folder)
    STUB_BUG_ID = "cd95cb722bfa1234ac4c78345c8953ee2e7170d0"

    # Class variables: Track iterations per bug_id to enable iteration-based stubbing
    # AIDEV-NOTE: Counter increments only in run_job() to track actual job submissions
    _iteration_counter = {}  # bug_id -> current_iteration_count
    _lock = threading.Lock()  # Thread-safe counter updates for parallel execution

    @staticmethod
    def is_stub_mode() -> bool:
        """Check if stub mode is enabled via environment variable."""
        return os.getenv("KAGENT_STUB_MODE", "false").lower() == "true"

    @staticmethod
    def get_debug_job_id() -> Optional[str]:
        """Get debug job ID from environment variable if set."""
        return os.getenv("KAGENT_DEBUG_JOB_ID")

    @staticmethod
    def is_debug_replay_mode() -> bool:
        """
        Check if debug replay mode is enabled (use real job ID instead of creating new).

        When KAGENT_DEBUG_JOB_ID is set, run_job() will return this ID instead of
        creating a new job. The rest of the flow (wait_for_job, JobDownloader) works
        normally with real job IDs, querying kGym API and downloading from GCS.
        """
        job_id = os.getenv("KAGENT_DEBUG_JOB_ID")
        return job_id is not None and job_id.strip() != ""

    @staticmethod
    def is_stub_job_id(job_id: str) -> bool:
        """Check if a job ID is a stub job ID."""
        return job_id.startswith("stub-job-")

    @staticmethod
    def create_stub_job_id() -> str:
        """Generate a unique stub job ID."""
        return f"stub-job-{uuid.uuid4()}"

    @classmethod
    def get_stub_bug_id(cls) -> str:
        """Get the default stub bug ID."""
        return cls.STUB_BUG_ID

    @classmethod
    def should_use_stub(cls, bug_id: str = None, increment: bool = False) -> bool:
        """
        Determine if stub mode should be used based on iteration count.

        The counter only increments when increment=True (i.e., in run_job()).
        All other locations just check the current state without incrementing.

        Stub mode is active when:
        1. KAGENT_STUB_MODE=true
        2. Current iteration < KAGENT_STUB_ITERATIONS
        """
        # Check if stub mode is enabled
        if os.getenv("KAGENT_STUB_MODE", "false").lower() != "true":
            return False

        # Get max stub iterations (default to 1 if not set)
        max_stub_iterations = int(os.getenv("KAGENT_STUB_ITERATIONS", "1"))

        # Track iterations globally or per-bug
        key = bug_id if bug_id else "global"

        with cls._lock:
            current_iteration = cls._iteration_counter.get(key, 0)

            if current_iteration < max_stub_iterations:
                # Still within stub iteration limit
                if increment:
                    # Only increment when creating a new job (in run_job)
                    cls._iteration_counter[key] = current_iteration + 1
                    print(f"[STUB MODE] Job {current_iteration + 1}/{max_stub_iterations} for {key}")
                return True
            else:
                # Exceeded stub iteration limit, use real jobs
                if increment:
                    print(f"[REAL MODE] Job {current_iteration + 1} for {key} - switching to real kernel jobs")
                return False

    @classmethod
    def reset_counter(cls, bug_id: str = None):
        """Reset iteration counter (useful for testing)"""
        key = bug_id if bug_id else "global"
        with cls._lock:
            if key in cls._iteration_counter:
                del cls._iteration_counter[key]

    @classmethod
    def get_iteration_count(cls, bug_id: str = None) -> int:
        """Get current iteration count for debugging"""
        key = bug_id if bug_id else "global"
        with cls._lock:
            return cls._iteration_counter.get(key, 0)

    @classmethod
    def create_crash_with_trace(cls, job_id: str) -> ResponseExtracted:
        """Create a ResponseExtracted object for a successful crash reproduction with ftrace."""
        return ResponseExtracted(
            status="finished",
            crash_description="KASAN: use-after-free in wacom_probe",
            message=None,
            final_syzkaller_checkout="abc123def456",
            argument_syzkaller_checkout="abc123def456",
            rollback_operation_performed=False,
            vm_image_url="gs://stub-bucket/vm-image.img",
            kernel_image_url="gs://stub-bucket/bzImage",
            build_message=None,
            compilation_error_url=None,
            patch_feedback=None,
            failed_features=None
        )

    @classmethod
    def create_build_failure(cls, job_id: str) -> ResponseExtracted:
        """Create a ResponseExtracted object for a build/compilation failure."""
        return ResponseExtracted(
            status="finished",
            crash_description=None,
            message=None,
            final_syzkaller_checkout=None,
            argument_syzkaller_checkout=None,
            rollback_operation_performed=None,
            vm_image_url=None,
            kernel_image_url=None,
            build_message="compilation failed: implicit declaration of function 'kfree'",
            compilation_error_url="gs://stub-bucket/compilation-error.txt",
            patch_feedback=["patch-failed"],
            failed_features=None
        )

    @classmethod
    def create_no_crash_success(cls, job_id: str) -> ResponseExtracted:
        """Create a ResponseExtracted object for a successful patch (no crash)."""
        return ResponseExtracted(
            status="finished",
            crash_description=None,
            message="no crash reproduced",
            final_syzkaller_checkout="abc123def456",
            argument_syzkaller_checkout="abc123def456",
            rollback_operation_performed=False,
            vm_image_url="gs://stub-bucket/vm-image.img",
            kernel_image_url="gs://stub-bucket/bzImage",
            build_message=None,
            compilation_error_url=None,
            patch_feedback=None,
            failed_features=None
        )

    @classmethod
    def get_fixture_dir(cls, job_id: str, scenario: str = "crash_with_trace") -> str:
        """Get the fixture directory path for a stub job."""
        kagent_path = os.getenv("KAGENT_PATH")
        if kagent_path is None:
            raise EnvironmentError("KAGENT_PATH environment variable not set")
        fixture_path = os.path.join(kagent_path, cls.FIXTURE_BASE, scenario)
        return fixture_path

    @classmethod
    def get_response_for_job(cls, job_id: str, scenario: str = "crash_with_trace") -> Optional[ResponseExtracted]:
        """Get the appropriate ResponseExtracted object for a stub job."""
        if not cls.is_stub_job_id(job_id):
            return None
        scenario_map = {
            "crash_with_trace": cls.create_crash_with_trace,
            "build_failure": cls.create_build_failure,
            "no_crash_success": cls.create_no_crash_success,
        }
        factory_method = scenario_map.get(scenario)
        if factory_method is None:
            raise ValueError(f"Unknown scenario: {scenario}. Valid options: {list(scenario_map.keys())}")
        return factory_method(job_id)


# AIDEV-NOTE: Helper functions for backward compatibility with existing code patterns
def is_stub_mode() -> bool:
    """Convenience function to check if stub mode is enabled."""
    return StubResponseFactory.is_stub_mode()


def is_stub_job_id(job_id: str) -> bool:
    """Convenience function to check if a job ID is a stub job."""
    return StubResponseFactory.is_stub_job_id(job_id)
