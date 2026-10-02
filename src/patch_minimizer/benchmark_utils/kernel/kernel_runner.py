"""Kernel job runner for patch_minimizer — faithful copy of Kernel_Agent code.

AIDEV-NOTE: This module is a direct copy of code from three Kernel_Agent files:
  - Kernel_Agent.kgym_utils.kernel_runner (KVMManager, KReproducer)
  - Kernel_Agent.kgym_utils.kernel_builder (KBuilder)
  - Kernel_Agent.kgym_utils.util_funcs (run_job, wait_for_job, get_job_results, helpers)
  - Kernel_Agent.kgym_utils.job_analyzer (JobAnalyzer.get_important_job_info)

The ONLY changes from the Kernel_Agent originals are:
  1. Import path: KBDr_Runner.kcomposer.KBDr.kcomposer -> KBDr.kcomposer
  2. Import path: Kernel_Agent.data_classes -> patch_minimizer.benchmark_utils.kernel.*
  3. Import path: Kernel_Agent.kgym_utils.* -> local functions in this module
  4. The module-level perform_job_run_check() call is removed (no immediate_stop.json in minimizer)

Any other deviation from the Kernel_Agent code is a bug.
"""

from __future__ import annotations

import KBDr.kcomposer as kcomp
from KBDr.kcomposer.models.kbuilder import kbuilder_argument_from_bug, kbuilder_from_kcache_argument, kbuilder_from_scratch_argument

from patch_minimizer.benchmark_utils.kernel.bug_data import BugData
from patch_minimizer.benchmark_utils.kernel.kvm_parameters import KBuilderParameters, KVMManagerParameters, CompleteKVMArguments
from patch_minimizer.benchmark_utils.kernel.kgym_types import (
    SpecialConditions,
    JobStatus,
    ResponseExtracted,
    LLMHistoryNodeErrors,
    PreBuilderResults,
    RunnerResults,
    BuilderResults,
)
from patch_minimizer.benchmark_utils.kernel.stub_responses import StubResponseFactory

from argparse import ArgumentTypeError
from dataclasses import dataclass
import json
import logging
import os
import pickle
import requests
import time
import traceback
import urllib.parse
from os.path import join as pjoin
from typing import List, Tuple

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
    after_log,
    RetryCallState,
)
from unidiff import PatchSet


# AIDEV-NOTE: Logger for network operations and retry logic
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# AIDEV-NOTE: Custom handler to show only filename (not full path) in logs
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter('[%(asctime)s][%(filename)s][%(levelname)s] - %(message)s'))
logger.addHandler(_handler)
logger.propagate = False  # Prevent duplicate logs from Hydra's root logger


###################### Utility Functions ######################
# AIDEV-NOTE: Copied from Kernel_Agent.kgym_utils.util_funcs

def get_kgym_api_url() -> str:
    """
    Get the kGym API URL from environment variables.
    Checks both KBDR_RUNNER_API_BASE_URL (Kernel_Agent) and KGYM_API_ENDPOINT (SWE-Agent).
    """
    # AIDEV-NOTE: Support both variable names for compatibility with Kernel_Agent and SWE-Agent
    url = os.getenv("KBDR_RUNNER_API_BASE_URL") or os.getenv("KGYM_API_ENDPOINT")
    if not url:
        raise ValueError(
            "kGym API URL not configured. Set either KBDR_RUNNER_API_BASE_URL or KGYM_API_ENDPOINT"
        )
    return url


def build_url(base_url, path, args_dict):
    # Returns a list in the structure of urlparse.ParseResult
    url_parts = list(urllib.parse.urlparse(base_url))
    url_parts[2] = path
    url_parts[4] = urllib.parse.urlencode(args_dict)
    return urllib.parse.urlunparse(url_parts)


def get_linker(compiler) :
    if compiler == "gcc" :
        return "ld"
    elif compiler == "clang" :
        return "ld.lld"


def get_arch(bug_data: BugData) -> str:
    """Get architecture from bug data."""
    # AIDEV-NOTE: Use BugData method instead of direct dict access
    return bug_data.get_architecture()


def get_kernel_git_url(bug_data: BugData) -> str:
    """Get kernel git URL from bug data."""
    # AIDEV-NOTE: Use BugData method to get git URL
    git_url = bug_data.get_kernel_source_git()

    # Clean up URL to get base repository URL
    if 'https://github.com/' in git_url:
        git_url = git_url.split('/commits/')[0]
    elif 'https://git.kernel.org/pub/scm/linux/kernel/git/' in git_url:
        git_url = git_url.split('/log/?id=')[0]
    else:
        raise ValueError(f'Git URL not supported: {git_url}')
    return git_url


def read_compiler_version(bug_data: BugData) -> str:
    """Read compiler type from bug data."""
    # AIDEV-NOTE: Use BugData method instead of manual field access
    return bug_data.get_compiler()


def get_patch(bug_data: BugData) -> str:
    """Get patch content from bug data."""
    # AIDEV-NOTE: Use BugData attribute access
    return bug_data.patch or ""


def get_base_bug_id(bug_id: str) -> str:
    """Extract base bug ID by stripping the tree suffix (_N)."""
    return bug_id.split("_")[0] if "_" in bug_id else bug_id


def get_golden_subset_kcache(golden_subset_data: dict, bug_id: str) -> str | None:
    """Get kcache URL from golden_subset_data, handling tree suffix in bug_id."""
    base_bug_id = get_base_bug_id(bug_id)
    if base_bug_id in golden_subset_data:
        return golden_subset_data[base_bug_id].get("kcache")
    return None


def get_golden_subset_image(golden_subset_data: dict, bug_id: str, default: str = "buildroot.raw") -> str:
    """Get userspace image name from golden_subset_data, handling tree suffix in bug_id."""
    base_bug_id = get_base_bug_id(bug_id)
    if base_bug_id in golden_subset_data:
        return golden_subset_data[base_bug_id].get("image", default)
    return default


def clean_model_patch(model_patch, src_folder) :
    """ Removes the prefix 'collection_of_linux_repos/linux-XYZ/' from the git diff
        and replaces it with an empty string. """

    start_index = src_folder.find("collection_of_linux_repos/")
    fixed_prefix = src_folder[start_index:]+"/"
    model_patch_2 = model_patch.replace(fixed_prefix,"")

    if model_patch == model_patch_2 :
        # the src_folder has changed
        import regex as re
        pattern = re.compile(r'collection_of_linux_repos\/linux-\d+\/')
        matches = pattern.findall(model_patch)
        if len(matches) :
            fixed_prefix = matches[0]
            model_patch_2 = model_patch.replace(fixed_prefix,'')

    return model_patch_2


def get_base_commit(benchmark_folder, bug_id, parent_commit_flag=False):
    """Get base commit from kernel bug JSON file."""
    # AIDEV-NOTE: Use BugData abstraction instead of manual JSON parsing
    bug_path = os.path.join(benchmark_folder, bug_id + ".json")
    bug_data = BugData.from_json_file(bug_path)
    return bug_data.get_base_commit(parent_commit_flag=parent_commit_flag)


def abort_job(job_id) :
    return


def run_job(workers, args, labels, session:(requests.Session|None)=None) :

    # AIDEV-NOTE: Check for stub mode - bypass actual job submission for testing
    # This is the ONLY place where we increment the iteration counter
    if StubResponseFactory.should_use_stub(increment=True):
        stub_job_id = StubResponseFactory.create_stub_job_id()
        print(f"[STUB MODE] Created stub job ID: {stub_job_id}")
        return stub_job_id

    # AIDEV-NOTE: Check for debug replay mode - return real job ID without creating new job
    if StubResponseFactory.is_debug_replay_mode():
        debug_job_id = StubResponseFactory.get_debug_job_id()
        print(f"[DEBUG REPLAY] Using existing job ID: {debug_job_id}")
        return debug_job_id

    # AIDEV-NOTE: Extract context for logging from labels
    context_info = labels.get('bug-reproduction-for', 'unknown-bug')

    # AIDEV-NOTE: Use tenacity decorator for robust retry logic with exponential backoff
    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=20, max=160),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=False  # Return None instead of raising after all retries exhausted
    )
    def _create_job_with_retry():
        """Inner function to create job - will be retried on network errors by tenacity."""
        if session is None:
            logger.debug(f"[run_job] Creating new KBDrSession for {context_info}")
            kdbr_session = kcomp.KBDrSession(get_kgym_api_url(), timeout=10)
            job_id = kdbr_session.create_job(workers, args, labels)
            logger.info(f"[run_job] Job created successfully: {job_id} for {context_info}")
            return job_id
        else:
            # code copied from kcomp.KBDrSession.create_job function
            from urllib.parse import urljoin
            api_url = get_kgym_api_url()
            logger.debug(f"[run_job] Using existing session to create job at {api_url} for {context_info}")
            resp = session.post(urljoin(api_url, 'jobs/new_raw_job'), json={
                'job_workers': workers,
                'worker_arguments': args,
                'kv': labels
            }, timeout=10)
            if resp.status_code == 200:
                logger.info(f"[run_job] Job created successfully: {resp.text} for {context_info}")
                return resp.text
            else:
                error_msg = f"Failed to create the job for {context_info}: status={resp.status_code}, response={resp.text}"
                logger.error(f"[run_job] {error_msg}")
                raise ValueError(error_msg)

    # Execute with tenacity retry logic
    try:
        job_id = _create_job_with_retry()
    except Exception as e:
        # AIDEV-NOTE: Tenacity raises RetryError even with reraise=False when retries are exhausted
        from tenacity import RetryError
        if isinstance(e, RetryError):
            logger.error(
                f"[run_job] All retry attempts exhausted for {context_info}. "
                f"Last error: {e.last_attempt.exception()}"
            )
        else:
            logger.error(f"[run_job] Unexpected exception after retries for {context_info}: {type(e).__name__}: {str(e)}")
        job_id = None

    if job_id is not None:
        print("Job ID : {}".format(job_id))
    else:
        logger.error(f"[run_job] Failed to create job after all retries for {context_info}")

    return job_id


def get_job_results(logger, job_id, sleep_flag=True, job_type="simple_reproduction", session=None) :
    """ Return job results after waiting for 30 seconds. """

    from time import sleep

    if sleep_flag :
        sleep(30)

    MAX_TRY = 10
    for idx in range(MAX_TRY) :
        try :
            job_results = JobAnalyzer.get_important_job_info(job_id, get_struct=True, job_type=job_type, session=session)
            return job_results
        except Exception as e :
            logger.error("Failed at getting results for Job {}".format(job_id))
            sleep(10)

    logger.error(f"KBDR cannot be queried for Job {job_id} even after 10 attempts. Raising Assertion Error.")
    raise AssertionError("KBDR is likely down.")


def wait_for_job(logger, job_id, job_type='simple_reproduction', session=None) :
    """ Wait for job to complete """
    from time import sleep

    # AIDEV-NOTE: Stub mode shortcut - stub jobs complete instantly, no KBDR polling needed
    if StubResponseFactory.is_stub_job_id(job_id):
        stub_response = StubResponseFactory.get_response_for_job(job_id)
        logger.info(f"[STUB MODE] Job {job_id} completed instantly with stub response")
        return False, stub_response

    stopped_wait = False
    start_time = time.time()
    job_results = ResponseExtracted(status="in_progress")
    while (job_results.wait_for_job()) :
        job_results = get_job_results(logger, job_id, job_type=job_type, session=session)
        if job_results is None :
            # kgym is busy - sleep for a longer time
            job_results = ResponseExtracted(status="in_progress") # reset to dummy object
            sleep(90)
            continue
        logger.info(f"Job={job_id}, Status={job_results.get_status()}")
        end_time = time.time()
        time_diff = end_time - start_time
        if time_diff > 50*60 :
            # If the job takes more than 50 minutes.
            # Stop checking the job and break
            stopped_wait = True
            break
    return stopped_wait, job_results


######################### JobAnalyzer #########################
# AIDEV-NOTE: Copied from Kernel_Agent.kgym_utils.job_analyzer

class JobAnalyzer() :

    @classmethod
    def get_kgym_job_json(cls, job_id, session:(requests.Session|None)=None) :
        """ Return the kgym json structure. """

        assert(os.getenv("KBDR_BUCKET_NAME") is not None)

        headers = {'accept': 'application/json'}
        job_id = job_id.replace("\"","")
        complete_url = build_url(get_kgym_api_url(), '/jobs/{}'.format(job_id), {})

        if session is None:
            with requests.Session() as session:
                resp = session.get(url=complete_url, headers=headers, timeout=10)
        else:
            resp = session.get(url=complete_url, headers=headers, timeout=10)

        resp_dict = resp.json()
        return resp_dict

    @classmethod
    def get_important_job_info(cls, job_id, job_type:str='simple_reproduction', get_struct:bool=False, session:(requests.Session|None)=None, get_arguments:bool=False) :
        """ Collects the status and the crash description. """

        # AIDEV-NOTE: Check if this is a stub job ID - return pre-configured ResponseExtracted object
        if StubResponseFactory.is_stub_job_id(job_id):
            stub_response = StubResponseFactory.get_response_for_job(job_id, scenario="crash_with_trace")
            print(f"[STUB MODE] Returning stub response for job {job_id}")
            return stub_response

        assert(os.getenv("KBDR_BUCKET_NAME") is not None)

        headers = {'accept': 'application/json'}
        job_id = job_id.replace("\"","")
        complete_url = build_url(get_kgym_api_url(), '/jobs/{}'.format(job_id), {})

        if session is None :
            with requests.Session() as session:
                resp = session.get(url=complete_url, headers=headers, timeout=10)
        else :
            resp = session.get(url=complete_url, headers=headers, timeout=10)

        try :
            resp_dict = resp.json()
        except Exception as e :
            return None

        # do we return only the arguments ?
        if get_arguments :
            return resp_dict.get("worker-arguments", None)

        # we want more than just the arguments
        if job_type == "compilation_check" :
            ans_dict = {}
            status = resp_dict["status"]
            ans_dict["status"] = status

            if status == "finished" :
                worker_results = resp_dict["worker-results"]
                if len(worker_results)==1 :
                    patch_feedback = worker_results[0]
                    ans_dict["patch_feedback"] = [ele["patch-status"] for ele in patch_feedback]
                else :
                    ans_dict["patch_feedback"] = None

            elif status == "aborted" or status == "in_progress" or status == "pending" :
                ans_dict["patch_feedback"] = None

        elif job_type == "kbuilder_only" :
            ans_dict = {}
            status = resp_dict["status"]
            ans_dict["status"] = status

            if status == "finished" :
                worker_results = resp_dict.get("worker-results", [])
                if len(worker_results) >= 1 :
                    worker_result = worker_results[0]
                    ans_dict["kcache-url"] = worker_result.get("kcache-url")
                    ans_dict["vm-image-url"] = worker_result.get("vm-image-url")
                    ans_dict["kernel_image_url"] = worker_result.get("kernel-image-url")
                else :
                    ans_dict["kcache-url"] = None
                    ans_dict["vm-image-url"] = None
                    ans_dict["kernel_image_url"] = None

            elif status == "aborted" or status == "in_progress" or status == "pending" :
                ans_dict["kcache-url"] = None
                ans_dict["vm-image-url"] = None
                ans_dict["kernel_image_url"] = None

        elif job_type == "simple_reproduction" :
            ans_dict = {}
            ans_dict["status"] = resp_dict["status"]
            status = resp_dict["status"]

            if status == "finished" :
                if len(resp_dict["worker-results"]) == 1 :
                    # shortcut reproduction with pre-built kernel image
                    if resp_dict["worker-results"][0].get("crash-description", -1) != -1 :
                        crash_description = resp_dict["worker-results"][0]["crash-description"]
                        ans_dict["crash_description"] = crash_description
                        ans_dict["message"] = None

                        if "unregister_netdevice" in crash_description :
                            message = SpecialConditions.MESSAGE_NO_CRASH
                            ans_dict["crash_description"] = None
                            ans_dict["message"] = message

                    else :
                        message = resp_dict["worker-results"][0]["message"]
                        ans_dict["crash_description"] = None
                        ans_dict["message"] = message

                    ans_dict["vm-image-url"] = resp_dict["worker-arguments"][0]["image"]["image-url"]
                    ans_dict["final-syzkaller-checkout"] = resp_dict["worker-results"][0]["final-syzkaller-checkout"]
                    ans_dict["argument-syzkaller-checkout"] = resp_dict["worker-arguments"][0]["reproducer"]["syzkaller-checkout"]
                    ans_dict["rollback-operation-performed"] = (ans_dict["final-syzkaller-checkout"] != ans_dict["argument-syzkaller-checkout"])
                    ans_dict["failed-features"] = resp_dict["worker-results"][0].get("failed-features")

                elif len(resp_dict["worker-results"]) == 2 :
                    # normal reproduction - build image and then reproduce
                    if resp_dict["worker-results"][1].get("crash-description", -1) != -1 :
                        crash_description = resp_dict["worker-results"][1]["crash-description"]
                        ans_dict["crash_description"] = crash_description
                        ans_dict["message"] = None

                        if "unregister_netdevice" in crash_description :
                            message = SpecialConditions.MESSAGE_NO_CRASH
                            ans_dict["crash_description"] = None
                            ans_dict["message"] = message

                    else :
                        message = resp_dict["worker-results"][1]["message"]
                        ans_dict["crash_description"] = None
                        ans_dict["message"] = message


                    ans_dict["kernel_image_url"] = resp_dict["worker-results"][0]["kernel-image-url"]
                    ans_dict["vm-image-url"] = resp_dict["worker-results"][0]["vm-image-url"]
                    ans_dict["final-syzkaller-checkout"] = resp_dict["worker-results"][1]["final-syzkaller-checkout"]
                    ans_dict["argument-syzkaller-checkout"] = resp_dict["worker-arguments"][1]["reproducer"]["syzkaller-checkout"]
                    ans_dict["rollback-operation-performed"] = (ans_dict["final-syzkaller-checkout"] != ans_dict["argument-syzkaller-checkout"])
                    ans_dict["kcache-url"] = resp_dict["worker-results"][0]["kcache-url"]
                    ans_dict["userspace-image-name"] = resp_dict["worker-arguments"][0]["userspace-image-name"]
                    ans_dict["failed-features"] = resp_dict["worker-results"][1].get("failed-features")

            elif status == "aborted" or status == "in_progress" or status == "pending" :
                if status == "aborted" :
                    worker_results = resp_dict["worker-results"]

                    if len(worker_results) == 1:
                        result = worker_results[0].get("result")
                        message = worker_results[0].get("message")
                        if result == "failure" and message is not None and \
                            message.lower() == SpecialConditions.BUILD_ERROR :
                            ans_dict["build_message"] = SpecialConditions.BUILD_ERROR
                            bucket_name = os.getenv("KBDR_BUCKET_NAME")
                            ans_dict["compilation_error_url"] = "https://storage.cloud.google.com/{}/jobs/{}/0_kbuilder/LinuxBuilder.err.log".format(bucket_name, job_id)

                ans_dict["crash_description"] = None
                ans_dict["message"] = None
                ans_dict["kernel_image_url"] = None

            else :
                print("Status : {}".format(status))

        elif job_type == "cross_reproduction" :
            ans_dict = {}
            ans_dict["status"] = resp_dict["status"]
            ans_dict["results"] = []
            status = resp_dict["status"]
            if status == "finished" :
                for index in range(1,len(resp_dict["worker-results"])) :
                    if resp_dict["worker-results"][index].get("crash-description", -1) != -1 :
                        crash_description = resp_dict["worker-results"][index]["crash-description"]
                        ans_dict["results"].append({"crash_description" : crash_description, "message" : None})
                    else :
                        message = resp_dict["worker-results"][index]["message"]
                        ans_dict["crash_description"] = None
                        ans_dict["message"] = message
                        ans_dict["results"].append({"crash_description" : None, "message" : message})

            elif status == "aborted" :
                ans_dict["crash_description"] = None
                ans_dict["message"] = None
            return ans_dict


        if get_struct :
            return ResponseExtracted(status=status,
                                    crash_description=ans_dict.get("crash_description"),
                                    message=ans_dict.get("message"),
                                    final_syzkaller_checkout=ans_dict.get("final-syzkaller-checkout"),
                                    argument_syzkaller_checkout=ans_dict.get("argument-syzkaller-checkout"),
                                    rollback_operation_performed=ans_dict.get("rollback-operation-performed"),
                                    vm_image_url=ans_dict.get("vm-image-url"),
                                    kernel_image_url=ans_dict.get("kernel_image_url"),
                                    build_message=ans_dict.get("build_message"),
                                    compilation_error_url=ans_dict.get("compilation_error_url"),
                                    patch_feedback=ans_dict.get("patch_feedback"),
                                                failed_features=ans_dict.get("failed-features"))
        else :
            return ans_dict
###############################################################


######################### KBuilder ###########################
# AIDEV-NOTE: Copied from Kernel_Agent.kgym_utils.kernel_builder

class KBuilder() :

    @classmethod
    def get_empty_kbuilder_params(cls) -> KBuilderParameters:
        return KBuilderParameters.get_empty()

    @classmethod
    def get_kernel_commit_id(cls,
                            bug_data: BugData,
                            get_parent_commit:bool=False,
                            get_fix_commit:bool=False) -> str:
        # AIDEV-NOTE: Use BugData methods instead of direct dict access
        if (not get_parent_commit) and (not get_fix_commit):
            kernel_commit_id = bug_data.crashes[0].kernel_source_commit
        elif get_parent_commit:
            kernel_commit_id = bug_data.get_base_commit(parent_commit_flag=True)
            print("\tTaking parent commit id as per user specifications")
        elif get_fix_commit:
            kernel_commit_id = bug_data.get_fix_commit()
            print("\tTaking fix commit id as per user specifications")
        return kernel_commit_id

    @classmethod
    def get_human_fix(cls, bug_folder, bug_id) -> str:
        bug_folder_path = os.path.join(bug_folder, bug_id)
        bug_data_path = os.path.join(bug_folder_path, "original_data.json")

        if not os.path.exists(bug_data_path):
            bug_folder_path = bug_folder
            bug_data_path = os.path.join(bug_folder_path, bug_id + ".json")
            assert(os.path.exists(bug_data_path))

        bug_data = BugData.from_json_file(bug_data_path)
        assert(bug_data.patch is not None)
        return bug_data.patch

    @classmethod
    def get_human_fix_modified_files(cls, bug_folder, bug_id) :
        human_fix = cls.get_human_fix(bug_folder, bug_id)
        patch_set = PatchSet.from_string(human_fix)
        file_paths = []
        for patch_file in patch_set :
            file_paths.append(patch_file.path)
        file_paths = list(set(file_paths))
        return file_paths

    @classmethod
    def turn_forceful_crash_on(cls, config) :
        config_lines = config.split("\n")
        additional_flag_dict = {
            "CONFIG_MAGIC_SYSRQ" : False,
            "CONFIG_MAGIC_SYSRQ_DEFAULT_ENABLE" : False
        }
        for idx,line in enumerate(config_lines) :
            for key,value in additional_flag_dict.items() :
                if value :
                    continue
                else :
                    if (f"{key}=" in line) or (f"{key} " in line) :
                        if key == "CONFIG_MAGIC_SYSRQ_DEFAULT_ENABLE" :
                            config_lines[idx] = f"{key}=0x1"
                        elif key == "CONFIG_MAGIC_SYSRQ" :
                            config_lines[idx] = f"{key}=y"
                        additional_flag_dict[key] = True
        for key,value in additional_flag_dict.items() :
            if not value :
                if key == "CONFIG_MAGIC_SYSRQ_DEFAULT_ENABLE" :
                    config_lines.append(f"{key}=0x1")
                elif key == "CONFIG_MAGIC_SYSRQ" :
                    config_lines.append(f"{key}=y")
        return "\n".join(config_lines)

    @classmethod
    def turn_kcov_off(cls, config) :
        config_lines = config.split("\n")
        for idx,line in enumerate(config_lines) :
            if "CONFIG_KCOV=" in line :
                old_val = config_lines[idx]
                config_lines[idx] = "CONFIG_KCOV=n"
                print(f"Changed {old_val} to {config_lines[idx]}")
        return "\n".join(config_lines)

    @classmethod
    def turn_ftrace_off(cls, config) :
        config_lines = config.split("\n")
        for idx,line in enumerate(config_lines) :
            if "CONFIG_FTRACE=" in line :
                old_val = config_lines[idx]
                config_lines[idx] = "CONFIG_FTRACE=n"
                print(f"Changed {old_val} to {config_lines[idx]}")
        return "\n".join(config_lines)

    @classmethod
    def turn_ftrace_on(cls, config) :
        config_lines = config.split("\n")
        flag1, flag2, flag3, flag4 = False, False, False, False
        for idx,line in enumerate(config_lines) :
            if "CONFIG_FUNCTION_TRACER" in line :
                flag1 = True
                config_lines[idx] = "CONFIG_FUNCTION_TRACER=y"
            elif "CONFIG_FUNCTION_GRAPH_TRACER" in line :
                flag2 = True
                config_lines[idx] = "CONFIG_FUNCTION_GRAPH_TRACER=y"
            elif "CONFIG_STACK_TRACER" in line :
                flag3 = True
                config_lines[idx] = "CONFIG_STACK_TRACER=y"
            elif "CONFIG_DYNAMIC_FTRACE" in line :
                flag4 = True
                config_lines[idx] = "CONFIG_DYNAMIC_FTRACE=y"
        if not flag1 :
            config_lines.append("CONFIG_FUNCTION_TRACER=y")
        if not flag2 :
            config_lines.append("CONFIG_FUNCTION_GRAPH_TRACER=y")
        if not flag3 :
            config_lines.append("CONFIG_STACK_TRACER=y")
        if not flag4 :
            config_lines.append("CONFIG_DYNAMIC_FTRACE=y")
        return "\n".join(config_lines)

    @classmethod
    def turn_kdump_on(cls, config) :
        config_lines = config.split("\n")
        additional_flag_dict = {
            "CONFIG_DEBUG_INFO" : False,
            "CONFIG_DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT" : False,
            "CONFIG_DEBUG_INFO_REDUCED" : False,
            "CONFIG_KEXEC" : False,
            "CONFIG_SYSFS" : False
        }
        for idx,line in enumerate(config_lines) :
            for key,value in additional_flag_dict.items() :
                if value :
                    continue
                else :
                    if (f"{key}=" in line) or (f"{key} " in line) :
                        if key == "CONFIG_DEBUG_INFO_REDUCED" :
                            config_lines[idx] = f"{key}=n"
                        else :
                            config_lines[idx] = f"{key}=y"
                        additional_flag_dict[key] = True
        for key,value in additional_flag_dict.items() :
            if not value :
                config_lines.append(f"{key}=y")
        return "\n".join(config_lines)

    @classmethod
    def fill_kbuilder_params_from_bug_folder(cls,
            bug_folder,
            bug_id,
            user_img="buildroot.raw",
            get_parent_commit:bool=False,
            get_fix_commit:bool=False,
            patch:str='',
            turn_ftrace_on:bool=False,
            kdump_flag:bool=False) :
        """ Gets the parameters for building a kernel job from the benchmark folder. """

        bug_folder_path = os.path.join(bug_folder, bug_id)
        bug_data_path = os.path.join(bug_folder_path, "original_data.json")

        if not os.path.exists(bug_data_path):
            bug_folder_path = bug_folder
            bug_data_path = os.path.join(bug_folder_path, bug_id + ".json")
            assert(os.path.exists(bug_data_path))

        bug_data = BugData.from_json_file(bug_data_path)

        kernel_commit_id = KBuilder.get_kernel_commit_id(bug_data, get_parent_commit=get_parent_commit, get_fix_commit=get_fix_commit)
        kernel_git_url = get_kernel_git_url(bug_data)

        kernel_config = bug_data.get_kernel_config_data()
        if not kernel_config:
            raise ValueError(f"Bug {bug_id}: kernel-config-data field is missing in crashes[0]")
        user_img = user_img

        if turn_ftrace_on:
            kernel_config = cls.turn_ftrace_on(kernel_config)

        if kdump_flag:
            kernel_config = cls.turn_kdump_on(kernel_config)
            if "kdump.raw" not in user_img:
                user_img = user_img.replace(".raw", "-kdump.raw")
        else:
            user_img = user_img.replace("-kdump.raw", ".raw")

        kernel_config = cls.turn_forceful_crash_on(kernel_config)

        arch = bug_data.get_architecture()
        compiler = read_compiler_version(bug_data)
        linker = get_linker(compiler)

        crash = {
            'kernel-config-data' : kernel_config,
            'kernel-source-git' : kernel_git_url,
            'kernel-source-commit' : kernel_commit_id,
            'architecture' : arch
        }

        kbuilder_dict = kbuilder_argument_from_bug(userspace_img_name = user_img,
                                                            crash = crash,
                                                            compiler = compiler,
                                                            linker = linker,
                                                            patch = patch)
        return KBuilderParameters.from_dict(kbuilder_dict)

    @classmethod
    def fill_kbuilder_params_from_kcache(cls, kcache_url, userspace_img, patch:str='') -> KBuilderParameters:
        kbuilder_dict = kbuilder_from_kcache_argument(kcache_url, userspace_img, patch)
        return KBuilderParameters.from_dict(kbuilder_dict)

    @classmethod
    def fill_kbuilder_params_from_scratch(cls, kernel_git_url, commit_id, kernel_config,
                                    userspace_img_name, arch, compiler, linker, patch) -> KBuilderParameters:
        kbuilder_dict = kbuilder_from_scratch_argument(kernel_git_url=kernel_git_url, commit_id=commit_id, kernel_config=kernel_config,
                                                        userspace_img_name=userspace_img_name, arch=arch, compiler=compiler,
                                                        linker=linker, patch=patch)
        return KBuilderParameters.from_dict(kbuilder_dict)
###############################################################


######################### KVMManager #########################
# AIDEV-NOTE: Copied from Kernel_Agent.kgym_utils.kernel_runner

class KVMManager () :
    """Class that deals with kernel execution. """

    @classmethod
    def get_empty_kvmmanager_params(cls) -> KVMManagerParameters:
        return KVMManagerParameters.get_empty()

    @classmethod
    def modify_c_reproducer(cls, reproducer_text:str) :
        """Add a print statement so that syzkaller will recognise that the execution is alive. """
        if "executed programs:" in reproducer_text :
            return reproducer_text
        else :
            crepro = 'int __syz_main'.join(reproducer_text.split('int main'))
            crepro += '\n\n\n'
            crepro += '#include <unistd.h>\n#include <stdio.h>\n'
            crepro += 'int main() { int p = fork(); if (p != 0) { __syz_main(); } else { for (;;) { printf("executed programs: 0\\n"); fflush(stdout); sleep(5); } } }'
            crepro += '\n\n\n'
        return crepro

    @classmethod
    def get_bug_data(cls, data_folder_path, bug_id) :
        """ Read data for the reproducer dict """
        path_1 = os.path.join(data_folder_path, bug_id, "original_data.json")
        path_2 = os.path.join(data_folder_path, bug_id + ".json")
        if os.path.exists(path_1) :
            bug_data_path = path_1
        elif os.path.exists(path_2) :
            bug_data_path = path_2
        else :
            raise ArgumentTypeError("Bug ", bug_id, " does not exist.")
        bug_data = BugData.from_json_file(bug_data_path)
        return bug_data

    @classmethod
    def get_reproducer_dict(cls,
                        bug_data,
                        nproc: int=8,
                        restart_time: str='10m',
                        reproducer_type:str="c",
                        ninstance:int=1,
                        threaded:bool=True,
                        syzkaller_rollback_tag:(str|None)=None) :
        """ Kernel specific reproduction arguments """

        # AIDEV-NOTE: External API expects dict, so convert BugData to dict if needed
        bug_dict = bug_data.to_dict() if isinstance(bug_data, BugData) else bug_data

        # Kernel specific reproduction arguments
        reproducer_dict = kcomp.models.kvmmanager.reproducer_from_bug(bug=bug_dict,
                                                                    nproc=nproc,
                                                                    restart_time=restart_time,
                                                                    ninstance=ninstance,
                                                                    preference=reproducer_type)
        reproducer_dict["threaded"] = threaded

        # AIDEV-NOTE: syzkaller_rollback_tag is REQUIRED
        assert syzkaller_rollback_tag is not None, \
            "syzkaller_rollback_tag must not be None - must be provided from top-level config"
        assert syzkaller_rollback_tag in ("master", "kgym-latest"), \
            f"syzkaller_rollback_tag must be 'master' or 'kgym-latest', got: {syzkaller_rollback_tag}"
        reproducer_dict["syzkaller-rollback-tag"] = syzkaller_rollback_tag

        # modify the repoducer text if the reproducer is a c program
        if reproducer_dict["reproducer-type"] == "c" :
            reproducer_dict["reproducer-text"] = KVMManager.modify_c_reproducer(reproducer_dict["reproducer-text"])

        return reproducer_dict

    @classmethod
    def kvm_manager_params_using_kernel_image_url(cls,
                        data_folder_path,
                        bug_id,
                        kernel_image_url,
                        nproc: int=8,
                        restart_time: str='10m',
                        machine_type: str='gce:e2-standard-2',
                        reproducer_type:str="c",
                        ninstance:int=1,
                        ftrace_filter_list:List[str]=None,
                        kdump_flag:bool=False,
                        threaded:bool=True,
                        syzkaller_rollback_tag:(str|None)=None) :

        bug_data = KVMManager.get_bug_data(data_folder_path, bug_id)

        reproducer_dict = KVMManager.get_reproducer_dict(bug_data=bug_data,
                        nproc=nproc,
                        restart_time=restart_time,
                        reproducer_type=reproducer_type,
                        ninstance=ninstance,
                        threaded=threaded,
                        syzkaller_rollback_tag=syzkaller_rollback_tag)

        vmlinux_url = "/".join(kernel_image_url.split("/")[:-1]+["vmlinux"])

        kvm_manager_dict = kcomp.models.kvmmanager.kvmmanager_argument(
            reproducer=reproducer_dict,
            machine_type=machine_type,
            image_url=kernel_image_url,
            vmlinux_url=vmlinux_url,
            arch=get_arch(bug_data))

        if "ftrace" not in kvm_manager_dict.keys() :
            if ftrace_filter_list is not None and len(ftrace_filter_list)>0 :
                kvm_manager_dict["ftrace"] = {
                    "function-filter-list": ftrace_filter_list,
                    "buffer-size": 204800
                }

        if kdump_flag :
            kvm_manager_dict["kdump"] = {}
            if kvm_manager_dict.get("ftrace") is not None :
                kvm_manager_dict["ftrace"]["dump-on-oops"] = False

        # final sanity check
        if kvm_manager_dict.get("kdump") is not None :
            if kvm_manager_dict.get("ftrace") is not None :
                assert(kvm_manager_dict["ftrace"]["dump-on-oops"] == False)
        else :
            if kvm_manager_dict.get("ftrace") is not None :
                assert(
                    (kvm_manager_dict["ftrace"].get("dump-on-oops") is None)
                    or
                    (kvm_manager_dict["ftrace"]["dump-on-oops"] == True)
                )

        return KVMManagerParameters.from_dict(kvm_manager_dict)

    @classmethod
    def kvm_manager_params_using_kbuilder_output(cls,
                        data_folder_path,
                        bug_id,
                        nproc: int=8,
                        restart_time: str='10m',
                        machine_type: str='gce:e2-standard-2',
                        reproducer_type:str="c",
                        ninstance:int=1,
                        ftrace_filter_list:List[str]=None,
                        kdump_flag:bool=False,
                        threaded:bool=True,
                        syzkaller_rollback_tag:(str|None)=None) :

        bug_data = KVMManager.get_bug_data(data_folder_path, bug_id)

        reproducer_dict = KVMManager.get_reproducer_dict(bug_data=bug_data,
                                                    nproc=nproc,
                                                    restart_time=restart_time,
                                                    reproducer_type=reproducer_type,
                                                    ninstance=ninstance,
                                                    threaded=threaded,
                                                    syzkaller_rollback_tag=syzkaller_rollback_tag)

        kvm_manager_dict = kcomp.models.kvmmanager.kvmmanager_argument(
            reproducer=reproducer_dict,
            machine_type=machine_type,
            image_from_worker=0
        )

        if "ftrace" not in kvm_manager_dict.keys() :
            if ftrace_filter_list is not None and len(ftrace_filter_list)>0 :
                kvm_manager_dict["ftrace"] = {
                    "function-filter-list": ftrace_filter_list,
                    "buffer-size": 204800
                }

        if kdump_flag :
            kvm_manager_dict["kdump"] = {}
            if kvm_manager_dict.get("ftrace") is not None :
                kvm_manager_dict["ftrace"]["dump-on-oops"] = False

        # final sanity check
        if kvm_manager_dict.get("kdump") is not None :
            if kvm_manager_dict.get("ftrace") is not None :
                assert(kvm_manager_dict["ftrace"]["dump-on-oops"] == False)
        else :
            if kvm_manager_dict.get("ftrace") is not None :
                assert(
                    (kvm_manager_dict["ftrace"].get("dump-on-oops") is None)
                    or
                    (kvm_manager_dict["ftrace"]["dump-on-oops"] == True)
                )

        if (reproducer_type == "c") and (reproducer_dict['reproducer-type'] == 'log') :
            logger.warning("Reproducer type changed to 'syz-log' because 'c' is missing.")
        elif (reproducer_type == "log") and (reproducer_dict['reproducer-type'] == 'c') :
            logger.warning("Reproducer type changed to 'c' because 'syz-log' is missing.")

        return KVMManagerParameters.from_dict(kvm_manager_dict)
###############################################################

######################### KReproducer #########################
# AIDEV-NOTE: Copied from Kernel_Agent.kgym_utils.kernel_runner

class KReproducer() :
    """ Class that marries kernel building and kernel execution for end-to-end kernel reproduction jobs.  """

    @classmethod
    def get_empty_reproducer_params(cls, bug_id: str = "") -> CompleteKVMArguments:
        return CompleteKVMArguments(
            bug_id=bug_id,
            kvm_builder_parameters=KBuilder.get_empty_kbuilder_params(),
            kvm_manager_parameters=KVMManager.get_empty_kvmmanager_params()
        )

    @classmethod
    def fill_kbuilder_kvm_manager_params(cls,
                                data_path,
                                bug_id,
                                user_img="buildroot.raw",
                                get_parent_commit:bool=False,
                                get_fix_commit:bool=False,
                                reproducer_type:str="c",
                                ninstance:int=1,
                                kernel_url:(str|None)=None,
                                patch:str='',
                                kcache_url:(str|None)=None,
                                turn_ftrace_on:bool=False,
                                ftrace_filter_list:List[str]=None,
                                kdump_flag:bool=False,
                                nproc:int=8,
                                threaded:bool=True,
                                syzkaller_rollback_tag:(str|None)=None) :
        """ Fill all kernel building and kernel running parameters. """

        complete_argument_dict = {}
        complete_argument_dict["bug_id"] = bug_id

        if (kernel_url is not None) :
            complete_argument_dict["kvm_manager_parameters"] = KVMManager.kvm_manager_params_using_kernel_image_url(data_folder_path=data_path,
                                                                                                                bug_id=bug_id,
                                                                                                                kernel_image_url=kernel_url,
                                                                                                                nproc=nproc,restart_time='10m',
                                                                                                                machine_type='gce:e2-standard-2',
                                                                                                                reproducer_type=reproducer_type,
                                                                                                                ninstance=ninstance,
                                                                                                                ftrace_filter_list=ftrace_filter_list,
                                                                                                                kdump_flag=kdump_flag,
                                                                                                                threaded=threaded,
                                                                                                                syzkaller_rollback_tag=syzkaller_rollback_tag)
        else :
            if kcache_url is not None :
                complete_argument_dict["kvm_builder_parameters"] = KBuilder.fill_kbuilder_params_from_kcache(kcache_url=kcache_url,
                                                                                                             userspace_img=user_img,
                                                                                                             patch=patch)
            else :
                complete_argument_dict["kvm_builder_parameters"] = KBuilder.fill_kbuilder_params_from_bug_folder(bug_folder=data_path,
                                                                                                        bug_id=bug_id,
                                                                                                        user_img=user_img,
                                                                                                        get_parent_commit=get_parent_commit,
                                                                                                        get_fix_commit=get_fix_commit,
                                                                                                        patch=patch,
                                                                                                        turn_ftrace_on=turn_ftrace_on,
                                                                                                        kdump_flag=kdump_flag)

                builder_params = complete_argument_dict["kvm_builder_parameters"]
                builder_params.update_kernel_config(KBuilder.turn_ftrace_off(builder_params.kernel_config))
                builder_params.update_kernel_config(KBuilder.turn_kcov_off(builder_params.kernel_config))

            complete_argument_dict["kvm_manager_parameters"] = KVMManager.kvm_manager_params_using_kbuilder_output(data_folder_path=data_path,
                                                                                                            bug_id=bug_id,
                                                                                                            nproc=nproc,
                                                                                                            restart_time='10m',
                                                                                                            machine_type='gce:e2-standard-2',
                                                                                                            reproducer_type=reproducer_type,
                                                                                                            ninstance=ninstance,
                                                                                                            ftrace_filter_list=ftrace_filter_list,
                                                                                                            kdump_flag=kdump_flag,
                                                                                                            threaded=threaded,
                                                                                                            syzkaller_rollback_tag=syzkaller_rollback_tag)
        return complete_argument_dict

    @classmethod
    def execute_bug_reproduction(cls, complete_argument_dict, session:(requests.Session|None)=None):
        # AIDEV-NOTE: Using data class attribute access instead of dict iteration
        manager_params = complete_argument_dict["kvm_manager_parameters"]
        manager_dict = manager_params.to_dict()
        for key, value in manager_dict.items():
            assert(value is not None)

        if manager_params.image is not None:
            if manager_params.image.image_url is not None:
                workers = ['kvmmanager']
                arguments = [manager_dict]
        else:
            builder_params = complete_argument_dict["kvm_builder_parameters"]
            builder_dict = builder_params.to_dict()
            for key, value in builder_dict.items():
                assert(value is not None)

            workers = ['kbuilder', 'kvmmanager']
            arguments = [
                builder_dict,
                manager_dict
            ]

        labels = {
            'composed-by': 'kcomposer.bug_reproduction',
            f'contains-bug-kernel-{complete_argument_dict["bug_id"]}-at': '0',
            'bug-reproduction-for': complete_argument_dict["bug_id"]
        }

        if complete_argument_dict.get("kvm_builder_parameters"):
            builder_params = complete_argument_dict["kvm_builder_parameters"]
            if builder_params.kernel_commit_id:
                labels[f'contains-kernel-commit-{builder_params.kernel_commit_id}-at'] = "0"
            elif builder_params.kcache_url:
                labels[f'contains-kcache-url-{builder_params.kcache_url}-at'] = "0"
        else:
            manager_params = complete_argument_dict["kvm_manager_parameters"]
            if manager_params.image is not None:
                labels[f'contains-image-url-{manager_params.image.image_url}-at'] = "0"

        try :
            job_id = run_job(workers, arguments, labels, session=session)

            if job_id is None:
                bug_id = complete_argument_dict.get("bug_id", "unknown")
                logger.error(
                    f"[execute_bug_reproduction] Job creation failed for bug {bug_id} after retries. "
                    f"Network may be unreachable or kGym API unavailable."
                )

            return job_id
        except Exception as e :
            bug_id = complete_argument_dict.get("bug_id", "unknown")
            logger.error(
                f"[execute_bug_reproduction] Unexpected error for bug {bug_id}: {type(e).__name__}: {str(e)}"
            )
            traceback.print_exc()
            return None
################################################################
