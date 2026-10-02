"""Reusable interface for the SCBench (SlopCodeBench) alternate code-slop metric.

AIDEV-NOTE: This module runs under the Kernel_Agent interpreter (Python 3.11).
It never imports ``slop_code`` directly — that package needs Python 3.12 and a
heavy dependency tree, so we treat SCBench as an EXTERNAL tool: ``SCBenchMetricRunner``
shells out to ``slop_measure_driver.py`` using the metric-home venv python and
parses its JSON. Build the metric home once with ``setup_env.sh`` (see README).

Pipeline (the ``evaluate_patch`` contract the task asked for):
    1. clean checkout of the touched files (SWEBenchRepoProvider, from Docker)
    2. apply the supplied patch
    3. run the SCBench metric
    4. return the numeric metric values
    5. leave nothing behind (all work happens in a discarded tempdir)

``evaluate_pair`` runs step 1-5 for the agent patch and the minimized patch and
returns ``before``, ``after`` and their ``reduction`` (before - after).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Metric home discovery
# ---------------------------------------------------------------------------
DEFAULT_METRIC_HOME = Path.home() / ".cache" / "scbench_metric"
_DRIVER = Path(__file__).with_name("slop_measure_driver.py")

# Composite / component keys carried through before/after/reduction.
METRIC_KEYS = (
    "verbosity_flagged_lines",
    "verbosity_pct",
    "erosion_high_cc_pct",
    "clone_lines",
    "ast_grep_violations",
    "cc_sum",
)


class SCBenchError(RuntimeError):
    """Raised when the SCBench metric environment or a measurement fails."""


class SCBenchMetricRunner:
    """Runs the SCBench slop metric on a directory via the metric-home venv."""

    def __init__(self, metric_home: str | os.PathLike | None = None) -> None:
        self.home = Path(
            metric_home
            or os.environ.get("SCBENCH_METRIC_HOME", DEFAULT_METRIC_HOME)
        )
        self.python = self.home / "venv" / "bin" / "python"
        self.rules = self.home / "slop_rules.yaml"
        if not self.python.exists():
            raise SCBenchError(
                f"SCBench metric env not found at {self.home}. "
                f"Run setup_env.sh first (missing {self.python})."
            )
        # Subprocess env: put ast-grep binary + uv-shim + venv on PATH, and
        # point ast-grep at the bundled 214-rule slop ruleset.
        self._env = dict(os.environ)
        self._env["PATH"] = os.pathsep.join(
            [str(self.home / "bin"), str(self.home / "venv" / "bin"),
             self._env.get("PATH", "")]
        )
        if self.rules.exists():
            self._env["AST_GREP_RULES_PATH"] = str(self.rules)

    def measure(self, directory: Path, entry_file: str, timeout: int = 300) -> dict:
        """Measure SCBench slop metrics for ``directory``; returns the flat dict.

        The metric only understands ``.py`` — non-Python files contribute
        nothing (see README limitations).
        """
        proc = subprocess.run(
            [str(self.python), str(_DRIVER), str(directory), entry_file],
            capture_output=True, text=True, env=self._env, timeout=timeout,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            raise SCBenchError(
                f"measure failed (rc={proc.returncode}) for {directory}\n"
                f"stderr: {proc.stderr[-800:]}"
            )
        return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# Patch utilities
# ---------------------------------------------------------------------------
_PLUS_RE = re.compile(r"^\+\+\+ b/(.+)$", re.M)
_MINUS_RE = re.compile(r"^--- a/(.+)$", re.M)


def changed_files(patch_text: str, only_python: bool = True) -> set[str]:
    """Return the set of file paths touched by a unified diff (``a/``/``b/``)."""
    files: set[str] = set()
    for rx in (_PLUS_RE, _MINUS_RE):
        for m in rx.finditer(patch_text):
            p = m.group(1).strip()
            if p and p != "/dev/null":
                files.add(p)
    if only_python:
        files = {f for f in files if f.endswith(".py")}
    return files


def new_files(patch_text: str, only_python: bool = True) -> set[str]:
    """Return files CREATED by a patch (``new file mode`` / ``--- /dev/null``).

    Mirrors ``compute_compaction.py``'s ``is_new`` detection so the SCBench
    ``filtered`` view matches the paper's filtered patch-size view exactly.
    """
    out: set[str] = set()
    for part in re.split(r"(?m)^(?=diff --git )", patch_text):
        if not part.strip():
            continue
        is_new = ("new file mode" in part) or bool(
            re.search(r"(?m)^--- /dev/null", part))
        if not is_new:
            continue
        m = _PLUS_RE.search(part)
        if m:
            p = m.group(1).strip()
            if p and p != "/dev/null" and (not only_python or p.endswith(".py")):
                out.add(p)
    return out


def filter_patch_to_python(patch_text: str) -> str:
    """Keep only the per-file diff blocks whose target is a ``.py`` file.

    Patches may also touch ``.txt``/``.rst`` docs or ``git submodule`` pointers
    (e.g. ``astropy_helpers``) that ``patch``/``git apply`` cannot handle. We only
    ever measure ``.py`` files, so dropping non-``.py`` blocks yields identical
    ``.py`` content while sidestepping those apply failures.
    """
    blocks: list[str] = []
    cur: list[str] = []
    for ln in patch_text.splitlines(keepends=True):
        if ln.startswith("diff --git "):
            if cur:
                blocks.append("".join(cur))
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        blocks.append("".join(cur))

    keep = []
    for b in blocks:
        m = _PLUS_RE.search(b) or _MINUS_RE.search(b)
        if m and m.group(1).strip().endswith(".py"):
            keep.append(b)
    return "".join(keep)


def apply_patch(work_dir: Path, patch_path: Path) -> tuple[bool, str]:
    """Apply a unified diff under ``work_dir`` (``patch``; ``git apply`` fallback)."""
    # AIDEV-NOTE: resolve to absolute — the subprocess runs with cwd=work_dir,
    # so a relative patch path would not be found. ``-l`` ignores whitespace and
    # ``--fuzz=3`` tolerates minor context drift.
    patch_abs = str(Path(patch_path).resolve())
    r = subprocess.run(
        ["patch", "-p1", "-l", "--fuzz=3", "-i", patch_abs],
        cwd=work_dir, capture_output=True, text=True,
    )
    if r.returncode == 0:
        return True, r.stdout
    r2 = subprocess.run(
        ["git", "apply", "--unsafe-paths", "--whitespace=nowarn", "-p1", patch_abs],
        cwd=work_dir, capture_output=True, text=True,
    )
    return r2.returncode == 0, (r.stdout + r.stderr + r2.stderr)


# ---------------------------------------------------------------------------
# Repo providers (clean-checkout source per domain)
# ---------------------------------------------------------------------------
class SWEBenchRepoProvider:
    """Supplies clean base-commit source for a SWE-bench instance.

    Uses the locally-built ``sweb.eval.x86_64.<instance_id>:latest`` image whose
    ``/testbed`` is checked out at the instance's base commit. Only the requested
    files are copied out, so nothing persists between evaluations.
    """

    def __init__(self, instance_id: str, timeout: int = 120) -> None:
        self.instance_id = instance_id
        self.image = f"sweb.eval.x86_64.{instance_id}:latest"
        self.timeout = timeout

    def extract_base_files(self, files: set[str], dest: Path) -> list[str]:
        """Copy ``/testbed/<f>`` for each file into ``dest`` (preserving layout)."""
        cid = subprocess.run(
            ["docker", "create", self.image],
            capture_output=True, text=True, timeout=self.timeout,
        ).stdout.strip()
        if not cid:
            raise SCBenchError(f"could not create container for {self.image}")
        extracted: list[str] = []
        try:
            for f in sorted(files):
                out = dest / f
                out.parent.mkdir(parents=True, exist_ok=True)
                r = subprocess.run(
                    ["docker", "cp", f"{cid}:/testbed/{f}", str(out)],
                    capture_output=True, text=True, timeout=self.timeout,
                )
                if r.returncode == 0:
                    extracted.append(f)
                # missing file => created by the patch; leave absent
        finally:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
        return extracted


# ---------------------------------------------------------------------------
# evaluate_patch / evaluate_pair
# ---------------------------------------------------------------------------
def _aggregate(per_file: dict, keep) -> dict:
    """Aggregate per-file metrics over the files for which ``keep(path)`` is True."""
    loc = flagged = clone = sg = 0
    mass = high = 0.0
    for path, m in per_file.items():
        if not keep(path):
            continue
        loc += m["loc"]
        flagged += m["verbosity_flagged_lines"]
        clone += m["clone_lines"]
        sg += m["ast_grep_violations"]
        mass += m["cc_mass"]
        high += m["cc_mass_high"]
    return {
        "loc": loc,
        "verbosity_flagged_lines": flagged,
        "verbosity_pct": (flagged / loc) if loc else 0.0,
        "clone_lines": clone,
        "ast_grep_violations": sg,
        "erosion_high_cc_pct": (high / mass) if mass else 0.0,
        "cc_sum": 0,  # cc_sum is snapshot-only; kept for key parity
    }


def evaluate_patch(
    runner: SCBenchMetricRunner,
    provider: SWEBenchRepoProvider,
    patch_path: Path,
    union_files: set[str],
    entry_file: str,
) -> dict:
    """Clean checkout -> apply patch -> measure -> reset. Returns per-file metrics.

    Returns ``{"per_file": {path: {...}}}`` (aggregation into unfiltered/filtered
    views happens in ``evaluate_pair``, which knows both patches). The tempdir is
    discarded, leaving the repo state clean.
    """
    with tempfile.TemporaryDirectory(prefix="scbench_") as td:
        work = Path(td)
        provider.extract_base_files(union_files, work)
        # Apply only the .py hunks (measurement ignores non-.py anyway).
        filtered = filter_patch_to_python(Path(patch_path).read_text())
        fp = work / "_scbench.patch"
        fp.write_text(filtered)
        ok, log = apply_patch(work, fp)
        if not ok:
            return {"error": "apply_failed", "log": log[-500:]}
        # entry file must exist for graph tracing; fall back to any present .py
        entry = entry_file
        if not (work / entry).exists():
            present = sorted(p.relative_to(work).as_posix()
                             for p in work.rglob("*.py"))
            if not present:
                return {"error": "no_python_files"}
            entry = present[0]
        raw = runner.measure(work, entry)
        return {"per_file": raw.get("per_file", {})}


def _measure_state(runner: SCBenchMetricRunner, work: Path) -> dict:
    """Measure a prepared dir; pick any present .py as the entry. {} if empty."""
    present = sorted(p.relative_to(work).as_posix()
                     for p in work.rglob("*.py") if p.name != "_scbench.patch")
    if not present:
        return {}
    raw = runner.measure(work, present[0])
    return raw.get("per_file", {})


def evaluate_triple(
    runner: SCBenchMetricRunner,
    provider: SWEBenchRepoProvider,
    original_patch: Path,
    minimized_patch: Path,
) -> dict:
    """Measure code-slop at THREE states to isolate the slop the patch introduced.

    original  = base commit, no patch
    agent     = base + agent patch
    minimized = base + minimized patch

    Then per file: introduced = agent - original, removed = agent - minimized.
    Subtracting ``original`` cancels the large pre-existing whole-file slop that
    otherwise dominates a single-state measurement, so the numbers reflect the
    patch rather than the file. Returns per-file metrics for all three states.
    """
    orig_text = original_patch.read_text()
    min_text = minimized_patch.read_text()
    union_all = changed_files(orig_text, only_python=False) | changed_files(
        min_text, only_python=False)
    union_py = {f for f in union_all if f.endswith(".py")}
    if not union_py:
        return {"error": "no_python_files_touched"}

    with tempfile.TemporaryDirectory(prefix="scbench_base_") as basetd:
        base = Path(basetd)
        # Pre-existing files only; agent-created (scratch) files are absent at
        # base -> original slop 0 for them (correct).
        provider.extract_base_files(union_py, base)
        states = {"original": _measure_state(runner, base)}

        for label, ptext in (("agent", orig_text), ("minimized", min_text)):
            with tempfile.TemporaryDirectory(prefix=f"scbench_{label}_") as wtd:
                work = Path(wtd)
                if any(base.iterdir()):
                    shutil.copytree(base, work, dirs_exist_ok=True)
                fp = work / "_scbench.patch"
                fp.write_text(filter_patch_to_python(ptext))
                ok, log = apply_patch(work, fp)
                fp.unlink(missing_ok=True)
                if not ok:
                    return {"error": "apply_failed", "state": label,
                            "log": log[-400:]}
                states[label] = _measure_state(runner, work)

    return {"changed_files": sorted(union_py), "per_file": states}


def _reduction(before: dict, after: dict) -> dict:
    return {k: round(before[k] - after[k], 6)
            for k in METRIC_KEYS if k in before and k in after}


def evaluate_pair(
    runner: SCBenchMetricRunner,
    provider: SWEBenchRepoProvider,
    original_patch: Path,
    minimized_patch: Path,
) -> dict:
    """Full before/after/reduction for an agent patch vs. its minimized patch."""
    orig_text, min_text = original_patch.read_text(), minimized_patch.read_text()
    # AIDEV-NOTE: extract EVERY file the patches touch (any extension) so the
    # patch applies cleanly — some patches also edit .txt/.rst/etc. The metric
    # still only measures .py (non-.py files are skipped by SCBench), and only
    # .py files can serve as the entry point.
    union_all = changed_files(orig_text, only_python=False) | changed_files(
        min_text, only_python=False)
    union_py = {f for f in union_all if f.endswith(".py")}
    if not union_py:
        return {"error": "no_python_files_touched"}
    entry = sorted(union_py)[0]

    # Only .py files are extracted/applied/measured (see filter_patch_to_python).
    before = evaluate_patch(runner, provider, original_patch, union_py, entry)
    after = evaluate_patch(runner, provider, minimized_patch, union_py, entry)
    if "error" in before or "error" in after:
        return {"error": "eval_failed", "before": before, "after": after}

    # Paper-consistent scopes (compute_compaction.py):
    #   unfiltered = whole agent submission vs whole minimized (all touched files)
    #   filtered   = drop NEW (+A) files the minimizer removed; keep modified
    #                files + new files that survived into the minimized patch.
    orig_new = new_files(orig_text)                       # +A files in agent patch
    surviving = changed_files(min_text)                   # files kept by minimizer

    def keep_filtered(path: str) -> bool:
        return (path not in orig_new) or (path in surviving)

    bpf, apf = before["per_file"], after["per_file"]
    before_uf = _aggregate(bpf, lambda p: True)
    before_fl = _aggregate(bpf, keep_filtered)
    after_uf = _aggregate(apf, lambda p: True)
    after_fl = _aggregate(apf, keep_filtered)

    return {
        "changed_files": sorted(union_py),
        "before": {"unfiltered": before_uf, "filtered": before_fl},
        "after": {"unfiltered": after_uf, "filtered": after_fl},
        "reduction": {
            "unfiltered": _reduction(before_uf, after_uf),
            "filtered": _reduction(before_fl, after_fl),
        },
        "per_file": {"before": bpf, "after": apf},
    }
