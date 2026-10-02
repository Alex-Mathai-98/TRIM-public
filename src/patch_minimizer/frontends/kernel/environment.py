"""Kernel execution environment for patch validation.

AIDEV-NOTE: Extracted from SolutionMinimizationBase to decouple job submission/parsing from minimization algorithm.
AIDEV-NOTE: Moved to environments/ subpackage and refactored to inherit from BaseEnvironment.
"""
from __future__ import annotations

from typing import List

from patch_minimizer.benchmark_utils.kernel.kgym_types import JobStatus, SpecialConditions
from patch_minimizer.core.edit import Edit

from patch_minimizer.strategies.environments.base import BaseEnvironment
from patch_minimizer.strategies.environments.feedback_types import (
    PatchFeedback,
    RuntimeFeedback,
)


class KernelEnvironment(BaseEnvironment):
    """Submits a patch to the kernel build/run infrastructure and returns feedback."""

    def __init__(
        self,
        benchmark_folder,
        bug_id,
        golden_subset_data,
        code_dir,
        logger,
        syzkaller_rollback_tag=None,
    ):
        self.benchmark_folder = benchmark_folder
        self.bug_id = bug_id
        self.golden_subset_data = golden_subset_data
        self.code_dir = code_dir
        self.logger = logger
        self.syzkaller_rollback_tag = syzkaller_rollback_tag

    # AIDEV-NOTE: Infra failures that should trigger a retry (not real crash verdicts)
    _INFRA_FAILURES = {
        SpecialConditions.CRASH_LOST_CONNECTION,
        SpecialConditions.MESSAGE_SETUP_FAILURE,
        SpecialConditions.CRASH_NO_OUTPUT,  # AIDEV-NOTE: "no output from test machine" is transient infra
    }
    _MAX_INFRA_RETRIES = 3

    def _submit_and_wait(self, patch: str) -> tuple[str, object]:
        """Submit a kernel job and wait for results. Returns (job_id, job_results)."""
        # AIDEV-NOTE: Lazy imports — keeps the module loadable without kGym deps (dry-run / SWE-bench).
        # Uses local kernel_runner.py (faithful copy of Kernel_Agent code with KBDr.kcomposer imports).
        from patch_minimizer.benchmark_utils.kernel.kernel_runner import (
            KReproducer,
            wait_for_job,
            clean_model_patch,
            get_golden_subset_kcache,
            get_golden_subset_image,
        )

        complete_argument_dict = KReproducer.fill_kbuilder_kvm_manager_params(
            data_path=self.benchmark_folder,
            bug_id=self.bug_id,
            user_img=get_golden_subset_image(self.golden_subset_data, self.bug_id),
            get_parent_commit=True,
            get_fix_commit=False,
            reproducer_type="log",
            ninstance=5,
            kcache_url=get_golden_subset_kcache(self.golden_subset_data, self.bug_id),
            patch=clean_model_patch(patch, self.code_dir),
            syzkaller_rollback_tag=self.syzkaller_rollback_tag,
        )
        job_id = KReproducer.execute_bug_reproduction(complete_argument_dict)
        if job_id is None:
            raise RuntimeError(
                f"Job creation failed for bug {self.bug_id} - "
                "likely network issue, please retry later"
            )
        job_id = job_id.replace('"', "")
        self.logger.info(f"Bug ID : {self.bug_id}, Job ID : {job_id}")

        _, job_results = wait_for_job(self.logger, job_id, job_type="simple_reproduction")
        return job_id, job_results

    def get_feedback(
        self,
        patch: str,
        edits: List[Edit] = None,
        edits_being_dropped: List[Edit] = None
    ) -> RuntimeFeedback:
        """Submit patch, wait for kernel job, return feedback.

        AIDEV-NOTE: Retries up to _MAX_INFRA_RETRIES times on infra failures
        (lost connection, setup failure) before falling through to STILL_CRASHES.

        Args:
            patch: The git diff patch string (kgym_patch)
            edits: The list of Edit objects (unused for kernel env, kept for API compatibility)
            edits_being_dropped: The list of Edit objects being removed (unused, for API compatibility)

        Returns:
            RuntimeFeedback with status and job_id
        """
        for attempt in range(1 + self._MAX_INFRA_RETRIES):
            job_id, job_results = self._submit_and_wait(patch)

            status = job_results.get_status()
            if status == JobStatus.ABORTED:
                return RuntimeFeedback(PatchFeedback.BUILD_FAILED, job_id)

            special = job_results.get_special_status()
            self.logger.info(
                f"Bug {self.bug_id}, Job {job_id}: status={status.value}, special={special.value}"
            )
            if special == SpecialConditions.MESSAGE_NO_CRASH and status == JobStatus.FINISHED:
                return RuntimeFeedback(PatchFeedback.NO_CRASH, job_id)

            # AIDEV-NOTE: Infra failures are transient — retry instead of treating as crash
            if special in self._INFRA_FAILURES and attempt < self._MAX_INFRA_RETRIES:
                self.logger.warning(
                    f"Bug {self.bug_id}, Job {job_id}: infra failure ({special.value}), "
                    f"retrying ({attempt + 1}/{self._MAX_INFRA_RETRIES})"
                )
                continue

            return RuntimeFeedback(PatchFeedback.STILL_CRASHES, job_id)


