"""SWE-bench test execution environment.

Runs feedback commands inside a Docker container built from a pre-built
SWE-bench instance image (``sweb.eval.<instance_id>:latest``).
"""
from __future__ import annotations

import io
import logging
import re
import shlex
import tarfile
from dataclasses import dataclass, field
from typing import Any, List

import docker

from patch_minimizer.core.edit import Edit

from patch_minimizer.strategies.environments.base import BaseEnvironment, BaseFeedback
from patch_minimizer.strategies.environments.feedback_types import (
    PatchFeedback,
    RuntimeFeedback,
)
from patch_minimizer.frontends.swebench.utils.diff_utils import patch_file_paths

logger = logging.getLogger(__name__)


# AIDEV-NOTE: Patch SWE-bench build constants at import time so image builds
# work on any machine. Fixes: (1) pip --no-use-pep517 removed in newer pip,
# (2) some repos deleted branches from GitHub (e.g., sympy 1.7), (3) sphinx
# images don't bundle ``roman`` PyPI package — needed because
# ``sphinx/writers/latex.py`` does ``from roman import toRoman`` at module
# load time. Without roman, ``sphinx.builders.latex`` fails to import and
# every Sphinx-instantiating test fails at collection (P2P collapses to 0/N).
# The pip_packages addition is for future image rebuilds; the test_cmd
# prepend kicks in immediately for already-built images via run_instance.
def _patch_swebench_build_constants() -> None:
    try:
        import re
        from swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS
        from swebench.harness.test_spec.test_spec import TestSpec
    except ImportError:
        return
    for ver, spec in MAP_REPO_VERSION_TO_SPECS.get("scikit-learn/scikit-learn", {}).items():
        install = spec.get("install", "")
        if "--no-use-pep517" in install:
            spec["install"] = install.replace("--no-use-pep517 ", "")

    # AIDEV-NOTE: Prepend ``pip install roman`` to sphinx test_cmd only
    # (NOT pip_packages) — modifying pip_packages would shift the env-image
    # hash and force rebuilds of all sphinx images. test_cmd runs inside
    # the eval script per-instance, so the install happens at run_instance
    # time on existing images, no rebuild required.
    for ver, spec in MAP_REPO_VERSION_TO_SPECS.get("sphinx-doc/sphinx", {}).items():
        test_cmd = spec.get("test_cmd", "")
        if test_cmd and "pip install roman" not in test_cmd:
            spec["test_cmd"] = f"pip install roman -q && {test_cmd}"

    _orig = TestSpec.install_repo_script.fget

    def _patched(self):
        script = _orig(self)
        return re.sub(r"--branch\s+\S+\s+--single-branch\s*", "", script)

    TestSpec.install_repo_script = property(_patched)


_patch_swebench_build_constants()


@dataclass
class SWEBenchFeedback(BaseFeedback):
    """Per-command results from SWE-bench test execution."""

    commands: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)


class SWEBenchEnvironment(BaseEnvironment):
    """Stateless SWE-bench environment — spins up a fresh container per ``get_feedback()`` call.

    AIDEV-NOTE: Container is created and destroyed within each ``get_feedback()`` call.
    No instance-level container state, no cleanup methods needed.
    """

    def __init__(
        self,
        instance_id: str,
        feedback_commands: list[dict[str, Any]],
        logger=None,
        timeout: int = 300,
        test_files: dict[str, str] | None = None,
    ):
        self.instance_id = instance_id
        self.feedback_commands = feedback_commands
        self.logger = logger
        self.timeout = timeout
        self.test_files = test_files or {}
        # AIDEV-NOTE: swebench v4.1.0 includes arch in image name
        self.image_name = f"sweb.eval.x86_64.{instance_id}:latest"
        self._client = docker.from_env()
        self.last_feedback: SWEBenchFeedback | None = None

    def _install_runtime_deps(self, container) -> None:
        """Install pytest plus instance-specific runtime deps.

        AIDEV-NOTE: ``roman`` is needed for sphinx images because
        ``sphinx/writers/latex.py`` does ``from roman import toRoman`` at
        module load and the SWE-bench sphinx images don't bundle it. Without
        this, ``sphinx.builders.latex`` fails to import → every Sphinx-
        instantiating pytest collects 0/N tests → P2P drops to 0. Confirmed
        on sphinx-7454 and sphinx-10323; covers the sphinx instances that
        otherwise regress to 0 passing tests.
        """
        cmd = "source activate testbed 2>/dev/null; pip install pytest -q"
        if self.instance_id.startswith("sphinx-doc__sphinx"):
            cmd += " && pip install roman -q"
        self._exec(container, cmd, timeout=90)

    def _exec(
        self, container, cmd: str, timeout: int | None = None,
    ) -> tuple[int, str]:
        t = timeout or self.timeout
        wrapped = f"timeout {t}s bash -c {_shell_quote(cmd)}"
        exit_code, output = container.exec_run(
            ["bash", "-c", wrapped],
            workdir="/testbed",
        )
        decoded = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output
        return exit_code, decoded

    def _copy_patch_to_container(self, container, patch: str) -> None:
        patch_bytes = patch.encode("utf-8")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name="candidate.patch")
            info.size = len(patch_bytes)
            tar.addfile(info, io.BytesIO(patch_bytes))
        buf.seek(0)
        container.put_archive("/tmp", buf)

    def _copy_test_files_to_container(
        self, container, skip_paths: set[str] | None = None,
    ) -> None:
        """Write test files into ``/testbed`` via ``put_archive``.

        AIDEV-NOTE: ``skip_paths`` MUST contain every path the patch modifies.
        ``extract_test_files_from_trajectory`` can over-collect a *source* file
        the agent both ran in a test command and edited; for a ``str_replace``
        edit its ``edit.after`` is a hunk *fragment*, not the whole file.
        Writing that over the correctly-patched tree corrupts the repo (e.g.
        sklearn ``least_angle.py`` -> IndentationError -> every command fails).
        A patched path is already correct on disk, so it is never re-injected.
        """
        if not self.test_files:
            return
        skip_paths = skip_paths or set()
        buf = io.BytesIO()
        wrote_any = False
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for path, content in self.test_files.items():
                if path in skip_paths:
                    if self.logger:
                        self.logger.info(
                            "[SWEBench] not injecting %s — modified by patch",
                            path,
                        )
                    continue
                data = content.encode("utf-8")
                info = tarfile.TarInfo(name=path)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
                wrote_any = True
        if not wrote_any:
            return
        buf.seek(0)
        container.put_archive("/testbed", buf)

    def _apply_patch(self, container, patch: str) -> bool:
        patch = _strip_from_patch(patch)
        self._copy_patch_to_container(container, patch)
        code, output = self._exec(
            container,
            "git checkout -- . && git clean -fd && git apply --verbose /tmp/candidate.patch",
        )
        if code != 0 and self.logger:
            self.logger.debug("[SWEBench] patch apply failed: %s", output[:500])
        return code == 0

    def get_feedback(
        self,
        patch: str,
        edits: List[Edit],
        edits_being_dropped: List[Edit] = None,
    ) -> RuntimeFeedback:
        job_id = f"swebench_{self.instance_id}"
        container = self._client.containers.run(
            self.image_name,
            "sleep infinity",
            detach=True,
        )
        try:
            if patch and not self._apply_patch(container, patch):
                self.last_feedback = SWEBenchFeedback(
                    commands=self.feedback_commands, results=[],
                )
                return RuntimeFeedback(PatchFeedback.BUILD_FAILED, job_id)

            self._install_runtime_deps(container)

            # AIDEV-NOTE: Test files written AFTER patch, but paths the patch
            # itself modifies are skipped so a str_replace fragment can never
            # overwrite the correctly-patched tree (see _copy_test_files_to_container).
            self._copy_test_files_to_container(
                container, _patch_modified_paths(patch),
            )

            results: list[dict[str, Any]] = []
            all_passed = True
            for cmd_entry in self.feedback_commands:
                command = cmd_entry["command"]
                full_cmd = f"source activate testbed 2>/dev/null; export PYTHONIOENCODING=utf-8; {command}"
                exit_code, output = self._exec(container, full_cmd)
                results.append({
                    "command": command,
                    "exit_code": exit_code,
                    "output": output,
                })
                if self.logger:
                    self.logger.info(
                        "[SWEBench] cmd=%r exit=%d", command, exit_code,
                    )
                if exit_code != 0:
                    all_passed = False
                    break

            self.last_feedback = SWEBenchFeedback(
                commands=self.feedback_commands, results=results,
            )
            status = PatchFeedback.NO_CRASH if all_passed else PatchFeedback.STILL_CRASHES
            return RuntimeFeedback(status, job_id)
        finally:
            container.remove(force=True)


    def _missing_paths_in_container(
        self, container, paths: set[str],
    ) -> set[str]:
        """Return the subset of ``paths`` that don't exist under ``/testbed``.

        AIDEV-NOTE: Single ``_exec`` over all paths — emits the missing ones
        on stdout, one per line. Relative paths resolve against
        ``workdir=/testbed`` (same as feedback-command exec); absolute paths
        are checked as-is. Used by the path-existence pre-filter in
        ``validate_feedback_commands``.
        """
        if not paths:
            return set()
        script = " ".join(
            f"[ ! -e {_shell_quote(p)} ] && echo {_shell_quote(p)};"
            for p in paths
        )
        _, output = self._exec(container, script, timeout=30)
        return {line.strip() for line in output.splitlines() if line.strip()}

    def validate_feedback_commands(self, full_patch: str) -> list[dict]:
        """Run each feedback command with the full patch; drop commands that fail.

        Mutates ``self.feedback_commands`` in place. Returns the dropped commands.
        """
        full_patch = _strip_from_patch(full_patch)
        container = self._client.containers.run(
            self.image_name, "sleep infinity", detach=True,
        )
        try:
            if full_patch:
                if not self._apply_patch(container, full_patch):
                    raise RuntimeError(
                        f"Full patch failed to apply for {self.instance_id}"
                    )
            self._install_runtime_deps(container)
            self._copy_test_files_to_container(
                container, _patch_modified_paths(full_patch),
            )

            # AIDEV-NOTE: path-existence pre-filter. Runs BEFORE exec so a
            # ghost command (typically a carried-over feedback command whose
            # scratch ``.py`` target was created mid-trajectory and then
            # ``git reset --hard``-ed away before submission) cannot pollute
            # this shared pre-flight container with side effects on its way
            # to a non-zero exit. Conservative: drop only commands whose
            # *every* explicit ``.py`` path argument is missing — discovery-
            # style commands (no path arg) fall through to the exec-based
            # check below.
            per_cmd_paths: list[list[str]] = [
                _referenced_py_paths(c["command"]) for c in self.feedback_commands
            ]
            all_referenced: set[str] = {p for ps in per_cmd_paths for p in ps}
            missing = self._missing_paths_in_container(container, all_referenced)

            passed: list[dict] = []
            dropped: list[dict] = []
            for cmd_entry, paths in zip(self.feedback_commands, per_cmd_paths):
                command = cmd_entry["command"]
                if paths and all(p in missing for p in paths):
                    dropped.append(cmd_entry)
                    if self.logger:
                        self.logger.info(
                            "[SWEBench] pre-flight drop (path-missing): %r (paths=%s)",
                            command, sorted(p for p in paths if p in missing),
                        )
                    continue
                full_cmd = f"source activate testbed 2>/dev/null; export PYTHONIOENCODING=utf-8; {command}"
                exit_code, _ = self._exec(container, full_cmd)
                if exit_code == 0:
                    passed.append(cmd_entry)
                else:
                    dropped.append(cmd_entry)
                    if self.logger:
                        self.logger.info(
                            "[SWEBench] pre-flight drop: %r (exit=%d)",
                            command, exit_code,
                        )
        finally:
            container.remove(force=True)

        self.feedback_commands = passed
        return dropped


_STRIP_FROM_PATCH = {"pyproject.toml", "tox.ini", "setup.py"}


def _strip_from_patch(patch: str) -> str:
    """Remove diffs for files in the exclusion list.

    AIDEV-NOTE: Temporary solution. SWE-bench Docker images often have config
    changes pre-applied (e.g., pinned setuptools), so the agent's diff for
    these files has stale context lines and fails ``git apply``.

    A more complete approach:
    1. Split into per-file diffs (``diff --git`` boundaries)
    2. Try ``git apply --check`` on each individually inside the container
    3. Keep only the ones that apply cleanly
    4. Combine into final patch
    """
    if not patch:
        return patch
    chunks = patch.split("diff --git ")
    kept = [chunks[0]]
    for chunk in chunks[1:]:
        first_line = chunk.split("\n", 1)[0]
        if any(f"/{name}" in first_line or first_line.endswith(name) for name in _STRIP_FROM_PATCH):
            continue
        kept.append(chunk)
    return "diff --git ".join(kept)


def _patch_modified_paths(patch: str) -> set[str]:
    """Return the repo-relative paths a unified diff modifies.

    AIDEV-NOTE: Reads each hunk's post-image (``+++ b/...``) path. Used to keep
    injected test files from clobbering a path the patch already wrote.

    AIDEV-NOTE: Kept as a named wrapper rather than inlining ``set(patch_file_paths(...))``
    at the two call sites — the note above is the reason these paths are collected, and
    inlining would lose it. The ``set()`` belongs here, not at the callers.
    """
    return set(patch_file_paths(patch))


def _shell_quote(s: str) -> str:
    """Single-quote a string for shell, escaping embedded single quotes."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


_PY_PATH_RE = re.compile(r"^/?[\w./-]+\.py$")


def _referenced_py_paths(command: str) -> list[str]:
    """Return the ``.py`` paths a feedback command appears to reference.

    AIDEV-NOTE: Conservative tokenizer for the path-existence pre-filter. Only matches
    whole shell tokens that look like a file path ending in ``.py``
    (``tests/test_foo.py``, ``/tmp/repro.py``, ``foo.py``). Strips pytest
    test-id suffixes (``tests/x.py::TestCase::test_a``) before matching.
    Tokens starting with ``-`` (flags) are skipped. Discovery-style commands
    (``pytest`` / ``tox`` with no explicit path arg) return ``[]`` so the
    caller does NOT pre-filter them — exec handles those.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []
    paths: list[str] = []
    for tok in tokens:
        if not tok or tok.startswith("-"):
            continue
        path_part = tok.split("::", 1)[0]
        if _PY_PATH_RE.match(path_part):
            paths.append(path_part)
    return paths
