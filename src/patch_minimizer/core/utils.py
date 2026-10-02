from patch_minimizer.core.rw_lock import RWLock
from os.path import join as pjoin
import glob
import os
import re
import subprocess
import traceback
import logging


# AIDEV-NOTE: Stale git lock files left by killed/crashed processes block the
# next git operation with a non-obvious error.
_GIT_STALE_LOCK_FILES = ("index.lock", "config.lock", "HEAD.lock")


def repair_missing_head(linux_repo: str, logger=None) -> bool:
    """If ``.git/HEAD`` is missing, reconstruct it pointing at
    ``refs/heads/master``. If the master ref itself is also missing,
    rebuild it from ``packed-refs``' ``refs/remotes/origin/master`` line.

    Returns ``True`` if HEAD is present (either was already, or was
    successfully repaired). Returns ``False`` if HEAD is still missing
    afterwards.
    """
    git_dir = pjoin(linux_repo, ".git")
    head = pjoin(git_dir, "HEAD")
    if os.path.exists(head):
        return True

    master_ref = pjoin(git_dir, "refs", "heads", "master")
    if not os.path.exists(master_ref):
        packed = pjoin(git_dir, "packed-refs")
        if os.path.exists(packed):
            try:
                with open(packed) as f:
                    for line in f:
                        line = line.rstrip()
                        if line.endswith("refs/remotes/origin/master"):
                            sha = line.split()[0]
                            os.makedirs(os.path.dirname(master_ref), exist_ok=True)
                            tmp = master_ref + ".tmp"
                            with open(tmp, "w") as wf:
                                wf.write(sha + "\n")
                            os.replace(tmp, master_ref)
                            break
            except OSError:
                pass

    if not os.path.exists(master_ref):
        if logger is not None:
            logger.error(
                f"repair_missing_head: cannot recover HEAD for {linux_repo} — "
                f"both .git/HEAD and refs/heads/master are missing and no "
                f"refs/remotes/origin/master line found in packed-refs"
            )
        return False

    tmp = head + ".tmp"
    try:
        with open(tmp, "w") as f:
            f.write("ref: refs/heads/master\n")
        os.replace(tmp, head)
    except OSError as e:
        if logger is not None:
            logger.error(f"repair_missing_head: write failed for {linux_repo}: {e}")
        return False

    if logger is not None:
        logger.warning(
            f"repair_missing_head: restored .git/HEAD → ref: refs/heads/master "
            f"for {linux_repo}"
        )
    return True


def sweep_stale_git_locks(linux_repo: str, logger=None) -> None:
    """Remove leftover ``.git/<name>.lock`` files (index/config/HEAD) that
    a killed git process may have stranded."""
    git_dir = pjoin(linux_repo, ".git")
    for lock_name in _GIT_STALE_LOCK_FILES:
        lock_file = pjoin(git_dir, lock_name)
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                if logger is not None:
                    logger.info(f"sweep_stale_git_locks: removed {lock_file}")
            except OSError as e:
                if logger is not None:
                    logger.warning(
                        f"sweep_stale_git_locks: could not remove {lock_file}: {e}"
                    )


def unescape_sed_replacement(s: str) -> str:
    """Unescape a sed replacement/pattern text into the literal string sed would emit.

    Shared by ``core/edit_applier.py`` and ``edit_parsing_helpers/sed_parsing.py``.
    """
    # AIDEV-NOTE: Protect literal backslashes (\\) first via sentinel, then
    # unescape \n, \t etc., then restore. Without this, \\n becomes
    # \<newline> instead of the intended literal \n (C escape).
    #
    # Drop GNU sed BRE escapes that aren't replacement back-references.
    # Agents commonly write `\(`, `\)`, `\|`, `\{`, `\}` in both pattern
    # AND replacement out of paranoia — real sed strips the backslash and
    # uses the literal char in the replacement output. Without these
    # entries the gen file ends up with a stray `\(` next to a paren that
    # never matches the baseline (e.g. 5e2a7fe08 — `\(fi->...` in gen vs
    # `(fi->...` in baseline).
    _SENTINEL = "\x00_BKSL_\x00"
    s = s.replace("\\\\", _SENTINEL)
    s = s.replace("\\n", "\n")
    s = s.replace("\\t", "\t")
    s = s.replace("\\&", "&")
    s = s.replace("\\[", "[")
    s = s.replace("\\]", "]")
    s = s.replace("\\.", ".")
    s = s.replace("\\*", "*")
    s = s.replace("\\+", "+")
    s = s.replace("\\(", "(")
    s = s.replace("\\)", ")")
    s = s.replace("\\|", "|")
    s = s.replace("\\{", "{")
    s = s.replace("\\}", "}")
    s = s.replace("\\?", "?")
    s = s.replace(_SENTINEL, "\\")
    return s


class Utils() :

    def __init__(self, logger:logging.Logger) -> None:
        self.logger = logger

    def parse_kernel_url(self, raw_url: str | None) -> str | None:
        """Parse different git URL formats to extract the base repository URL."""
        if not raw_url:
            return None

        if "/log/" in raw_url:
            return raw_url.split("/log/")[0]
        elif "/commits/" in raw_url:
            return raw_url.split("/commits/")[0] + ".git"
        else:
            self.logger.warning(f"Unrecognized git URL format: {raw_url}, skipping remote setup")
            return None

    def run_command(self, cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        """Run a command in the shell."""
        try :
            if "git" in cmd :
                assert(
                    (("--git-dir" in cmd) and ("--work-tree" in cmd)) or \
                    (("remote" in cmd) and ("-v" in cmd)) or \
                    (("remote" in cmd) and ("add" in cmd)) or \
                    ("fetch" in cmd) or \
                    (("remote" in cmd) and ("set-url" in cmd)) or \
                    (("-C" in cmd) and ("apply" in cmd))
                )

        except Exception as e :
            traceback.print_exc()
            raise e

        try:
            if (kwargs.get("stdout") is None) or (kwargs.get("stderr") is None) :
                if len(kwargs)>0 :
                    print("Command {}".format(cmd))
                    print("Keyword Arguments : {}".format(kwargs))
                    assert(False)

            if len(kwargs) :
                kwargs.pop("stdout")
                kwargs.pop("stderr")

            cp = subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)
            if cp.stderr != "" and ("checkout" not in cmd) :
                self.logger.info(f"Running command: {cmd}")
                self.logger.info("Current CWD {}".format(os.getcwd()))
                self.logger.info("StdErr : {}".format(cp.stderr))

            if ("apply" in cmd) :
                self.logger.info(f"Success when running command: {cmd}")

            return cp

        except subprocess.CalledProcessError as e:
            self.logger.info(f"Error running command: {cmd}")
            self.logger.info(e.stderr)
            raise e

    def run_multiple_commands(self, command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) :
        process = subprocess.Popen(command, shell=True, stdout=stdout, stderr=stderr)
        stdout_data, stderr_data = process.communicate()
        if stderr_data is not None :
            stderr_str = stderr_data.decode('utf-8')
            if stderr_str != "" :
                print("Function 'run_multiple_commands' throws error {}".format(stderr_str))
                raise AssertionError("Error raised when running subprocess Popen")

    def repo_clean_changes(self,
                           use_lock=True,
                           code_dir_lock:(RWLock|None)=None,
                           git_dir:str=None,
                           work_tree:str=None) -> None:
        """Reset repo to HEAD. Clean active changes and untracked files on top of HEAD."""
        assert((git_dir is not None) and (work_tree is not None))
        if use_lock :
            assert(code_dir_lock is not None)
            with code_dir_lock.w_locked() :
                reset_cmd = ["git", "--git-dir", git_dir, "--work-tree", work_tree, "reset", "--hard"]
                clean_cmd = ["git", "--git-dir", git_dir, "--work-tree", work_tree, "clean", "-fd"]
                self.run_command(reset_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.run_command(clean_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else :
            assert(code_dir_lock is not None and code_dir_lock.is_w_locked()), \
                "When use_lock=False, caller must hold the write lock"
            reset_cmd = ["git", "--git-dir", git_dir, "--work-tree", work_tree, "reset", "--hard"]
            clean_cmd = ["git", "--git-dir", git_dir, "--work-tree", work_tree, "clean", "-fd"]
            self.run_command(reset_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.run_command(clean_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def set_correct_branch(self,
                        use_lock=True,
                        code_dir_lock:(RWLock|None)=None,
                        git_dir:str=None,
                        work_tree:str=None,
                        kernel_base_url:str=None) :
        """Adding new/different branches to the mainline."""
        self.logger.info("\tCalling the `set_correct_branch` function.")

        if kernel_base_url is None :
            self.logger.info("\tEarly exit in `set_correct_branch` function as kernel_base_url is None")
            return

        parsed_base_url = self.parse_kernel_url(kernel_base_url)

        if parsed_base_url is None:
            self.logger.info("\tEarly exit in `set_correct_branch` function as parsed URL is None")
            return

        def base_function(git_dir, work_tree, parsed_base_url) :
            assert code_dir_lock is not None and code_dir_lock.is_w_locked(), "set_correct_branch.base_function() requires write lock to be held"
            output = self.run_command(f"git --git-dir {git_dir} --work-tree {work_tree} remote -v".split(" "))

            output_lines = output.stdout.split("\n")
            for line in output_lines :
                line = line.strip()
                if line == "" :
                    continue
                self.logger.info(f"\tLine {line}")
                splits = line.split(" ")
                name,url = splits[0].split("\t")
                if name=="origin" and url==parsed_base_url :
                    self.logger.info(f"\tInside `set_correct_branch` function, origin already set to {parsed_base_url}")
                    return

            if parsed_base_url not in output.stdout :
                self.logger.info(f"\tInside `set_correct_branch` function, Adding new remote branch {parsed_base_url}")
                tree_name = parsed_base_url.split("/")[-1].replace(".git","")
                self.run_command(f"git --git-dir {git_dir} --work-tree {work_tree} remote add {tree_name} {parsed_base_url}".split(" "))
                self.run_command(f"git --git-dir {git_dir} --work-tree {work_tree} fetch {tree_name}".split(" "))
                self.run_command(f"git --git-dir {git_dir} --work-tree {work_tree} fetch --tags {tree_name}".split(" "))

            self.run_command(f"git --git-dir {git_dir} --work-tree {work_tree} remote set-url origin {parsed_base_url}".split(" "))
            self.logger.info(f"\tInside `set_correct_branch` function, set origin to remote branch {parsed_base_url}")

        assert((git_dir is not None) and (work_tree is not None))
        if use_lock :
            assert(code_dir_lock is not None)
            with code_dir_lock.w_locked() :
                base_function(git_dir, work_tree, parsed_base_url)
        else :
            assert(code_dir_lock is not None and code_dir_lock.is_w_locked()), \
                "When use_lock=False, caller must hold the write lock"
            base_function(git_dir, work_tree, parsed_base_url)

    def repo_reset_and_clean_checkout(self,
                                    commit_hash: str,
                                    use_lock=True,
                                    code_dir_lock:(RWLock|None)=None,
                                    git_dir:str=None,
                                    work_tree:str=None,
                                    kernel_base_url:str=None) -> None:
        """Reset repo to the original commit state.
        Cleans uncommited changes, untracked files, and submodule changes.
        Handles orphaned commits by fetching them as remote refs when standard checkout fails.
        """
        parsed_base_url = self.parse_kernel_url(kernel_base_url) if kernel_base_url else None

        def base_function(commit_hash, git_dir, work_tree, parsed_base_url) :
            assert code_dir_lock is not None and code_dir_lock.is_w_locked(), "repo_reset_and_clean_checkout.base_function() requires write lock to be held"

            sweep_stale_git_locks(work_tree, logger=self.logger)
            repair_missing_head(work_tree, logger=self.logger)

            if kernel_base_url:
                self.set_correct_branch(use_lock=False,
                                       code_dir_lock=code_dir_lock,
                                       git_dir=git_dir,
                                       work_tree=work_tree,
                                       kernel_base_url=kernel_base_url)

            if os.path.exists(".coverage"):
                os.remove(".coverage")
            if os.path.exists("tests/.coveragerc"):
                os.remove("tests/.coveragerc")
            other_cov_files = glob.glob(".coverage.TSS.*", recursive=True)
            for f in other_cov_files:
                os.remove(f)

            reset_cmd = ["git", "--git-dir", git_dir, "--work-tree", work_tree, "reset", "--hard", commit_hash]
            clean_cmd = ["git", "--git-dir", git_dir, "--work-tree", work_tree, "clean", "-fd"]
            checkout_cmd = ["git", "--git-dir", git_dir, "--work-tree", work_tree, "checkout", commit_hash]

            try:
                self.run_command(reset_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.run_command(clean_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.run_command(checkout_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                self.logger.error(f"Could not checkout to commit {commit_hash}, attempting to fetch as orphaned commit")

                if parsed_base_url:
                    fetch_cmd = ["git", "--git-dir", git_dir, "--work-tree", work_tree,
                                "fetch", "-f", "origin", f"{commit_hash}:refs/remotes/origin/orphaned-commit"]
                    self.run_command(fetch_cmd)
                    self.run_command(checkout_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self.logger.info(f"Successfully checked out orphaned commit {commit_hash}")
                else:
                    self.logger.error(f"Cannot fetch orphaned commit {commit_hash}: no kernel_base_url provided")
                    raise e

            submodule_unbind_cmd = "cd '{}'; git submodule deinit -f .".format(work_tree)
            self.run_multiple_commands(submodule_unbind_cmd, stderr=subprocess.DEVNULL)
            submodule_init_cmd = "cd '{}'; git submodule update --init".format(work_tree)
            self.run_multiple_commands(submodule_init_cmd, stderr=subprocess.DEVNULL)

        assert((git_dir is not None) and (work_tree is not None))
        if use_lock :
            assert(code_dir_lock is not None)
            with code_dir_lock.w_locked() :
               base_function(commit_hash, git_dir, work_tree, parsed_base_url)
        else :
            assert(code_dir_lock is not None and code_dir_lock.is_w_locked()), \
                "When use_lock=False, caller must hold the write lock"
            base_function(commit_hash, git_dir, work_tree, parsed_base_url)

    def apply_git_diff(self,
                      git_diff: str,
                      repo_path: str,
                      code_dir_lock: RWLock,
                      git_dir: str = None,
                      work_tree: str = None) -> subprocess.CompletedProcess:
        """Apply a git diff to a repository."""
        if code_dir_lock is None:
            raise ValueError("code_dir_lock is required for apply_git_diff()")

        assert code_dir_lock.is_w_locked(), "apply_git_diff() requires caller to hold write lock"

        if git_dir is None:
            git_dir = pjoin(repo_path, ".git")
        if work_tree is None:
            work_tree = repo_path

        if "collection_of_linux_repos" in work_tree:
            current_dir = work_tree.split("collection_of_linux_repos")[1]
            if current_dir[-1] == "/":
                current_dir = current_dir[:-1]

            new_pattern = "collection_of_linux_repos" + current_dir
            modified_git_diff = re.sub(r'collection_of_linux_repos/.*?linux-\d+', new_pattern, git_diff)

            if new_pattern not in modified_git_diff:
                modified_git_diff = modified_git_diff.replace(" a/", f" a/{new_pattern}/")
                modified_git_diff = modified_git_diff.replace(" b/", f" b/{new_pattern}/")
        else:
            modified_git_diff = git_diff

        patch_file_path = pjoin(repo_path, "temp_apply.patch")
        with open(patch_file_path, "w") as f:
            f.write(modified_git_diff)

        try:
            git_apply_cmd = ["git",
                           "--git-dir", git_dir,
                           "--work-tree", work_tree,
                           "apply", patch_file_path]
            result = self.run_command(git_apply_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return result

        except Exception as e:

            try :
                modified_git_diff = re.sub(r'collection_of_linux_repos/.*?linux-\d+/', "", git_diff)

                with open(patch_file_path, "w") as f:
                    f.write(modified_git_diff)

                git_apply_cmd = ["git", "-C", work_tree, "apply", patch_file_path]
                result = self.run_command(git_apply_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return result

            except Exception as e:
                self.logger.error(f"Failed to apply git diff: {e}")

        finally:
            if os.path.exists(patch_file_path):
                os.remove(patch_file_path)

    @classmethod
    def find_file(cls, directory, filename) -> str | None:
        """Find a file in a directory. filename can be short name or relative path."""

        def find_file_exact_relative(directory, filename) -> str | None:
            if os.path.isfile(os.path.join(directory, filename)):
                return os.path.join(directory, filename)
            return None

        def find_file_shortname(directory, filename) -> str | None:
            for root, dirs, files in os.walk(directory):
                for file in files:
                    if file == filename:
                        return os.path.join(root, file)
            return None

        found = find_file_exact_relative(directory, filename)
        if found is not None:
            return found

        found = find_file_shortname(directory, filename)
        if found is not None:
            return found

        return None
