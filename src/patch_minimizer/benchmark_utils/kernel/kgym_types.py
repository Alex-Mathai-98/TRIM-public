"""Shared type definitions for kgym utilities.

This module contains enums and dataclasses used across multiple modules
to avoid circular import dependencies.
"""

from enum import Enum
from dataclasses import dataclass
from typing import List


class SpecialConditions(str, Enum):
    CRASH_NO_OUTPUT = "no output from test machine"
    CRASH_LOST_CONNECTION = "lost connection to test machine"
    MESSAGE_SETUP_FAILURE = "failed to set up instance"
    MESSAGE_NO_CRASH = "no crash reproduced"
    NORMAL_EXECUTION = ""
    BUILD_ERROR = "unable to build the kernel"


class JobStatus(str, Enum):
    # Status of Job
    FINISHED = "finished"
    ABORTED = "aborted"
    IN_PROGRESS = "in_progress"
    PENDING = "pending"
    WAITING = "waiting"


class LLMHistoryNodeErrors(str, Enum):
    WAITED_FOR_50_MINUTES = "waited for more than 50 minutes"
    KGYM_ATTEMPTS_EXCEEDED = "exceeded the maximum Kgym attempts"
    LLM_ATTEMPTS_EXCEEDED = "exceeded the maximum LLM attempts"
    EXECUTION_ATTEMPTS_EXCEEDED = "exceeded the maximum execution attempts"
    CRASH_ATTEMPTS_EXCEEDED = "exceeded the maximum attempts to crash the kernel"
    BUDGET_EXCEEDED = "exceeded the budget limit"
    UNEXPECTED_EXCEPTION = "unexpected exception"


@dataclass
class PreBuilderResults:
    patch_feedback: List[str]


@dataclass
class RunnerResults:
    crash_description: str
    message: str
    final_syzkaller_checkout: str
    argument_syzkaller_checkout: str
    rollback_operation_performed: bool
    failed_features: (List[str]) = None


@dataclass
class BuilderResults:
    # when the build is successful
    vm_image_url: str
    kernel_image_url: str
    # when the build is not successful
    build_message: str
    compilation_error_url: str


# response extracting code
class ResponseExtracted():
    """ Class that holds the extracted response. """
    def __init__(self,
                status: str,
                crash_description: (str | None) = None,
                message: (str | None) = None,
                final_syzkaller_checkout: (str | None) = None,
                argument_syzkaller_checkout: (str | None) = None,
                rollback_operation_performed: (bool | None) = None,
                vm_image_url: (str | None) = None,
                kernel_image_url: (str | None) = None,
                build_message: (str | None) = None,
                compilation_error_url: (str | None) = None,
                patch_feedback: (List[str] | None) = None,
                failed_features: (List[str]) = None) -> None:

        self.status = self.populate_status(status)

        self.builder_results = self.populate_builder_results(vm_image_url,
                                                            kernel_image_url,
                                                            build_message,
                                                            compilation_error_url)

        self.runner_results = self.populate_runner_results(crash_description,
                                                        message,
                                                        final_syzkaller_checkout,
                                                        argument_syzkaller_checkout,
                                                        rollback_operation_performed,
                                                        failed_features=failed_features)

        self.prebuilder_results = self.populate_prebuilder_results(patch_feedback)

        self.special_status = self.populate_special_status()

    def patch_applied_flag(self):
        """ Checks if the patch was applied. """
        return self.builder_results is not None

    def populate_special_status(self):
        if self.runner_results is None:
            return None
        else:
            if self.runner_results.crash_description == SpecialConditions.CRASH_LOST_CONNECTION:
                return SpecialConditions.CRASH_LOST_CONNECTION
            elif self.runner_results.crash_description == SpecialConditions.CRASH_NO_OUTPUT:
                return SpecialConditions.CRASH_NO_OUTPUT
            elif self.runner_results.message == SpecialConditions.MESSAGE_SETUP_FAILURE:
                return SpecialConditions.MESSAGE_SETUP_FAILURE
            elif self.runner_results.message == SpecialConditions.MESSAGE_NO_CRASH:
                return SpecialConditions.MESSAGE_NO_CRASH
            else:
                return SpecialConditions.NORMAL_EXECUTION

    def get_special_status(self):
        return self.special_status

    def get_status(self):
        return self.status

    ################# Builder related Getters and Setters #################
    def get_vm_image_url(self):
        """ Returns a vm image url (if any). """
        if self.builder_results:
            return self.builder_results.vm_image_url
        return None

    def get_build_message(self):
        """ Returns a build message (if any). """
        if self.builder_results and self.builder_results.build_message:
            return self.builder_results.build_message
        return None

    def populate_builder_results(self,
                                vm_image_url: (str | None) = None,
                                kernel_image_url: (str | None) = None,
                                build_message: (str | None) = None,
                                compilation_error_url: (str | None) = None):
        if (vm_image_url is not None) and (kernel_image_url is not None):
            return BuilderResults(vm_image_url, kernel_image_url, None, None)
        elif build_message is not None:
            return BuilderResults(None, None, build_message, compilation_error_url)
        else:
            return None

    ##########################################################

    ################# Runner related Getters and Setters #################
    def get_failed_features(self):
        if self.runner_results:
            return self.runner_results.failed_features
        return None

    def get_message(self):
        if self.runner_results:
            return self.runner_results.message
        return None

    def get_crash_description(self):
        if self.runner_results:
            return self.runner_results.crash_description
        return None

    def populate_runner_results(self,
                            crash_description: (str | None) = None,
                            message: (str | None) = None,
                            final_syzkaller_checkout: (str | None) = None,
                            argument_syzkaller_checkout: (str | None) = None,
                            rollback_operation_performed: (bool | None) = None,
                            failed_features: (List[str]) = None):

        if crash_description is None and message is None:
            return None

        try:
            assert((crash_description is None) or (message is None))
            assert(final_syzkaller_checkout is not None)
            assert(argument_syzkaller_checkout is not None)
            assert(rollback_operation_performed is not None)

            if crash_description is not None:
                crash_description = crash_description.lower()
            elif message is not None:
                message = message.lower()

            return RunnerResults(crash_description,
                                message,
                                final_syzkaller_checkout,
                                argument_syzkaller_checkout,
                                rollback_operation_performed,
                                failed_features)

        except Exception as e:
            return None

    ##########################################################

    def populate_status(self, status):
        status = status.lower()
        if status == "aborted":
            return JobStatus.ABORTED
        elif status == "in_progress":
            return JobStatus.IN_PROGRESS
        elif status == "pending":
            return JobStatus.PENDING
        elif status == "finished":
            return JobStatus.FINISHED
        elif status == "waiting":
            return JobStatus.WAITING
        else:
            raise ValueError("Unknown Status : ", status)

    ############## Pre-Builder related functionality ##############
    def get_patch_feedback(self):
        if self.prebuilder_results:
            return self.prebuilder_results.patch_feedback
        return None

    def populate_prebuilder_results(self, patch_feedback: (List[str] | None) = None):
        if patch_feedback is not None:
            return PreBuilderResults(patch_feedback=patch_feedback)
        else:
            return None
    ################################################################

    def wait_for_job(self):
        if self.status == JobStatus.IN_PROGRESS \
            or self.status == JobStatus.PENDING \
            or self.status == JobStatus.WAITING:
            return True
        return False
