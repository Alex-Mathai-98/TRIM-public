import os
from os.path import exists
from os.path import join as pjoin
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from patch_minimizer.core.utils import Utils, repair_missing_head, sweep_stale_git_locks
import logging

logger = logging.Logger(__name__)


def clean_single_repo(utils, linux_repo, max_retries=3, delay=2):
    """Clean a single repo with retry logic.

    AIDEV-NOTE: Added retry logic to handle transient git failures (lock files, interrupted ops).
    Returns True on success, False if failed after all retries.

    Self-healing: ``sweep_stale_git_locks`` removes leftover ``*.lock`` files
    (now including ``HEAD.lock``), and ``repair_missing_head`` restores
    ``.git/HEAD`` from ``refs/heads/master`` / ``packed-refs`` when it has
    gone missing — recurring symptom on the parallel mini-swe verify
    harness, see ``utils.py::repair_missing_head`` AIDEV-NOTE.
    """
    git_dir = pjoin(linux_repo, ".git")
    git_reset_cmd = ["git", "--git-dir", git_dir, "--work-tree", linux_repo, "reset", "--hard"]
    git_clean_cmd = ["git", "--git-dir", git_dir, "--work-tree", linux_repo, "clean", "-fd"]

    for attempt in range(max_retries):
        try:
            sweep_stale_git_locks(linux_repo, logger=logger)
            if not repair_missing_head(linux_repo, logger=logger):
                # Cannot self-heal — let the git command below produce a
                # meaningful error if the repo really is unusable.
                pass

            utils.run_command(git_reset_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            utils.run_command(git_clean_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True  # Success
        except subprocess.CalledProcessError as e:
            if attempt < max_retries - 1:
                print(f"Retry {attempt + 1}/{max_retries} for {linux_repo}: {e}")
                time.sleep(delay)
            else:
                print(f"WARNING: Failed to clean {linux_repo} after {max_retries} attempts: {e}")
                return False  # Failed after all retries
    return False


def clean_all_linux_repos(linux_repo_list, parallel=False):
    """For each linux repo:
        1. Remove any previous locks from the repo
        2. Clean the repo with retry logic
    """

    utils = Utils(logger)
    failed_repos = []

    if parallel and len(linux_repo_list) > 1:
        print(f"Cleaning {len(linux_repo_list)} repos in parallel...")
        with ThreadPoolExecutor(max_workers=len(linux_repo_list)) as executor:
            futures = {
                executor.submit(clean_single_repo, utils, repo): repo
                for repo in linux_repo_list
            }
            for future in as_completed(futures):
                repo = futures[future]
                if not future.result():
                    failed_repos.append(repo)
    else:
        for linux_repo in linux_repo_list:
            if not clean_single_repo(utils, linux_repo):
                failed_repos.append(linux_repo)

    # AIDEV-NOTE: Fail only if too many repos failed (>50%), otherwise continue with available repos
    if failed_repos:
        failure_rate = len(failed_repos) / len(linux_repo_list)
        if failure_rate > 0.5:
            raise RuntimeError(f"Too many repos failed to clean ({len(failed_repos)}/{len(linux_repo_list)}): {failed_repos}")
        else:
            print(f"WARNING: {len(failed_repos)} repo(s) failed to clean but continuing: {failed_repos}")


if __name__ == '__main__' :
    
    base_path = os.environ.get("BASE_PATH")
    linux_folder_base_path = pjoin(base_path,"collection_of_linux_repos")
    num_linux_repos = 70

    linux_paths = []
    for idx in range(1,num_linux_repos+1) :
        repo_path = pjoin(linux_folder_base_path,"linux-{}".format(idx))
        assert(exists(repo_path))
        linux_paths.append(repo_path)

    clean_all_linux_repos(linux_paths)

    