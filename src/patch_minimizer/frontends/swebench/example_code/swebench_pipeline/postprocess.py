#!/usr/bin/env python3
"""Post-hoc SWE-bench oracle + gold-patch comparison over an existing
``run_swebench_minimization.py`` save-dir.

For each ``<save-dir>/<instance_id>/`` that already has a saved
``minimization_results.json`` and ``standalone__<instance_id>/minimized.patch``:

  1. Pre-min oracle: ``run_instance`` on the agent's ``info.submission`` from
     the matching ``.traj`` file.
  2. Post-min oracle: ``run_instance`` on the minimized patch.
  3. Gold-patch comparison: byte equality (mod ``index`` SHA), per-file
     overlap, hunk-count delta vs the HF dataset's ``patch`` field.

Writes results back into each ``minimization_results.json`` under
``oracle_pre`` / ``oracle_post`` / ``gold_comparison``, and updates
``<save-dir>/minimization_summary.json`` with ``oracle_transitions`` +
``gold_equivalence`` aggregate counts.

Usage:
    python src/patch_minimizer/frontends/swebench/example_code/swebench_pipeline/postprocess.py \\
        --save-dir results/minimization_333 \\
        --traj-dir results/swe-bench-swe-agent-trajs \\
        --force --num-parallel 8

Always pass --force: reports are cached in logs/run_evaluation/posthoc_{pre,post}_<id>/ for every
results folder, and without it an old report.json is reused instead of testing the current patch.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


# AIDEV-NOTE: bootstrap_env() must run before the heavy package imports below (hence the
# E402 noqa on each) — it sets KAGENT_PATH / BASE_PATH that those modules read at import.
from patch_minimizer.frontends.swebench.utils.env import bootstrap_env  # noqa: E402

bootstrap_env()

# AIDEV-NOTE: Importing swebench_environment triggers
# ``_patch_swebench_build_constants`` at module load — mutates
# ``MAP_REPO_VERSION_TO_SPECS`` (adds ``roman`` to sphinx pip_packages and
# prepends ``pip install roman`` to sphinx test_cmd) BEFORE
# ``make_test_spec`` is called below, so already-built sphinx images get
# the roman install via the patched test_cmd at run_instance time.
from patch_minimizer.frontends.swebench.utils.diff_utils import (  # noqa: E402
    hunk_count,
    patch_file_paths,
    plus_minus_counts,
    split_patch_into_files,
    strip_index_lines,
)
from patch_minimizer.frontends.swebench.utils.hf_dataset import hf_rows  # noqa: E402
from patch_minimizer.frontends.swebench.cli.common import (  # noqa: E402
    close_instance_logger,
    instance_logger,
    parse_instance_filter,
    setup_dispatcher_logging,
    write_json_atomic,
)
from patch_minimizer.frontends.swebench import (  # noqa: E402,F401
    environment as _swebench_env_patch,
)


# ---------------------------------------------------------------------------
# Dataset cache (one HF lookup per process)
# ---------------------------------------------------------------------------

# AIDEV-NOTE: the cache and its lock moved with the function into
# utils/hf_dataset.py, which keys the cache by dataset. Alias keeps call sites.
_hf_rows = hf_rows


# ---------------------------------------------------------------------------
# Local-image dependency-drift correction
# ---------------------------------------------------------------------------

# AIDEV-NOTE: Our LOCALLY-BUILT instance images resolved unpinned transitive
# deps at build time (2026) to versions NEWER than Princeton's canonical
# published images (e.g. docutils 0.22.4 vs 0.21.2 for sphinx 3.x; a broken
# source-only pandas C-ext for matplotlib). On these instances the agent's
# Princeton-verified submission FAILS locally purely from that drift — not a
# minimizer regression. For them we evaluate against Princeton's published
# image (``swebench/sweb.eval.x86_64.*``, via ``namespace="swebench"``) to get
# the faithful verdict. This is PER-INSTANCE gated: every other instance keeps
# using its local image, so the 289 already-passing cases are untouched (zero
# blast radius). Verified 2026-06-07: each of these flips to resolved on BOTH
# the agent submission (pre) and the minimized patch (post) in Princeton's
# image. Piecemeal dep-pinning in the local image does NOT work — the failures
# depend on the consistent build-time combination of deps, so only the whole
# canonical image reproduces Princeton's result.
_PRINCETON_IMAGE_INSTANCES = {
    "sphinx-doc__sphinx-7454",
    "sphinx-doc__sphinx-10323",
    "matplotlib__matplotlib-24970",
    "django__django-15103",
    "pylint-dev__pylint-6903",
}

# AIDEV-NOTE: Upstream-non-reproducible — the GOLD patch itself fails F2P in
# Princeton's canonical published image (verified 2026-06-07, with and without
# our roman test_cmd patch), so no model_patch can resolve them in the
# available image+harness:
#   sphinx-8595  (test_empty_all)        — autodoc blank-line drift
#   sphinx-9711  (test_needs_extensions) — extension behaviour drift
#   django-10097 (7 auth_tests.test_templates.AuthTemplateTests) — template drift
# NOT fixable from our side (would require editing the test); left in bucket D
# and documented. Deliberately NOT in _PRINCETON_IMAGE_INSTANCES because the
# canonical image does not correct them.
_UPSTREAM_NONREPRODUCIBLE = {
    "sphinx-doc__sphinx-8595",
    "sphinx-doc__sphinx-9711",
    "django__django-10097",
}


def _ensure_remote_image(client, image_key: str, logger: logging.Logger) -> None:
    """Pull a Princeton published image if it isn't present locally."""
    try:
        client.images.get(image_key)
        return
    except Exception:
        pass
    repo, _, tag = image_key.rpartition(":")
    logger.info("pulling Princeton image %s", image_key)
    client.images.pull(repo, tag=tag or "latest")


# ---------------------------------------------------------------------------
# Patch-reversal fix helpers
# ---------------------------------------------------------------------------

def _patch_file_name(block: str) -> str:
    """Extract the destination file path from a diff block header for logging."""
    for line in block.splitlines():
        if line.startswith("+++ b/"):
            return line[6:].split("\t", 1)[0]
        if line.startswith("diff --git "):
            parts = line.split(" b/", 1)
            if len(parts) > 1:
                return parts[1].strip()
    return "(unknown)"


# AIDEV-NOTE: moved to utils/diff_utils.py (z-cpl-44 step 1); alias keeps call sites.
_split_patch_into_files = split_patch_into_files


def _put_text_in_container(container, path: str, text: str) -> None:
    """Write text as a file at path inside a running Docker container via put_archive."""
    import io
    import tarfile
    data = text.encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=os.path.basename(path))
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    container.put_archive(os.path.dirname(path) or "/", buf.read())


def _strip_already_applied_hunks(
    patch_text: str, image_name: str, client, logger: logging.Logger
) -> str:
    """Return patch_text with per-file blocks that are already at post-state removed.

    For each per-file block:
    - git apply --check succeeds → forward-applicable → keep.
    - git apply --check fails AND git apply --check --reverse succeeds → image
      already has the post-state (pre-baked env-setup commit overlap).  Strip.
      Without stripping, patch --batch would see the post-state and auto-reverse
      the entire model_patch including the agent's actual source fix.
    - Neither direction applies cleanly → uncertain, keep and let run_instance
      surface the real error.

    Falls back to the original patch unchanged on any container-lifecycle failure.
    """
    blocks = _split_patch_into_files(patch_text)
    if not blocks:
        return patch_text

    container = None
    try:
        container = client.containers.run(
            image_name, command="sleep infinity", detach=True, remove=False,
        )
        kept: list[str] = []
        stripped_count = 0
        for block in blocks:
            _put_text_in_container(container, "/tmp/__sm_check.diff", block)
            fwd_exit, _ = container.exec_run(
                ["git", "-C", "/testbed", "apply", "--check", "/tmp/__sm_check.diff"]
            )
            if fwd_exit == 0:
                kept.append(block)
                continue
            rev_exit, _ = container.exec_run(
                ["git", "-C", "/testbed", "apply", "--check", "--reverse",
                 "/tmp/__sm_check.diff"]
            )
            if rev_exit == 0:
                logger.info("strip_already_applied: removing pre-baked block: %s",
                            _patch_file_name(block))
                stripped_count += 1
            else:
                # Neither direction applies cleanly — uncertain, keep as-is.
                kept.append(block)
        if stripped_count:
            logger.info("strip_already_applied: stripped %d/%d file-blocks",
                        stripped_count, len(blocks))
        return "".join(kept)
    except Exception as exc:
        logger.warning(
            "strip_already_applied: container check failed (%s); using original patch.", exc
        )
        return patch_text
    finally:
        if container is not None:
            try:
                container.stop(timeout=5)
                container.remove(force=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Oracle (run_instance wrapper)
# ---------------------------------------------------------------------------

def _run_oracle(instance_id: str, patch: str, label: str, run_id: str,
                timeout: int, instance_logger: logging.Logger,
                strip_applied: bool = False, force: bool = False) -> dict:
    out: dict = {
        "resolved": False, "f2p_pass": 0, "f2p_fail": 0,
        "p2p_pass": 0, "p2p_fail": 0,
        "patch_applied": False, "error": None,
    }
    if not patch or not patch.strip():
        out["error"] = "empty_patch"
        return out
    try:
        import shutil

        import docker
        from swebench.harness.run_evaluation import run_instance
        from swebench.harness.test_spec.test_spec import make_test_spec
        from swebench.harness.constants import RUN_EVALUATION_LOG_DIR, LOG_REPORT

        rows = _hf_rows()
        if instance_id not in rows:
            out["error"] = "not_in_dataset"
            return out
        client = docker.from_env()
        # AIDEV-NOTE: For local-image-drift instances, evaluate against
        # Princeton's canonical published image instead of our drifted local
        # build. Per-instance gated — every other instance uses its
        # local image, so the already-passing set is untouched.
        if instance_id in _PRINCETON_IMAGE_INSTANCES:
            test_spec = make_test_spec(rows[instance_id], namespace="swebench")
            _ensure_remote_image(client, test_spec.instance_image_key, instance_logger)
        else:
            test_spec = make_test_spec(rows[instance_id])

        # AIDEV-NOTE: run_instance early-returns a cached report.json if one
        # already exists for this (run_id, label, instance_id).  --force must
        # therefore clear the per-run log dir, or run_instance silently reuses
        # the stale result and never re-evaluates the (possibly stripped) patch.
        run_log_dir = (
            RUN_EVALUATION_LOG_DIR / run_id / label.replace("/", "__") / instance_id
        )
        if force and run_log_dir.exists():
            instance_logger.info("force: clearing stale log dir %s", run_log_dir)
            shutil.rmtree(run_log_dir, ignore_errors=True)
        # AIDEV-NOTE: pre-strip already-applied hunks to prevent patch --batch
        # auto-reversing the model_patch on instances where the image pre-baked
        # overlapping env-setup commits.  Only applied to the
        # pre-min oracle (agent submission); minimized patches skip this.
        effective_patch = (
            _strip_already_applied_hunks(
                patch,
                getattr(test_spec, "instance_image_key",
                        f"sweb.eval.x86_64.{instance_id}:latest"),
                client,
                instance_logger,
            )
            if strip_applied else patch
        )
        pred = {
            "instance_id": instance_id,
            "model_patch": effective_patch,
            "model_name_or_path": label,
        }
        run_instance(
            test_spec=test_spec, pred=pred,
            rm_image=False, force_rebuild=False, client=client,
            run_id=run_id, timeout=timeout,
        )
        report_path = (
            RUN_EVALUATION_LOG_DIR / run_id
            / label.replace("/", "__") / instance_id / LOG_REPORT
        )
        if not report_path.exists():
            out["error"] = "no_report"
            return out
        report = json.loads(report_path.read_text())[instance_id]
        out["resolved"] = bool(report.get("resolved", False))
        out["patch_applied"] = bool(report.get("patch_successfully_applied", False))
        ts = report.get("tests_status", {})
        out["f2p_pass"] = len(ts.get("FAIL_TO_PASS", {}).get("success", []))
        out["f2p_fail"] = len(ts.get("FAIL_TO_PASS", {}).get("failure", []))
        out["p2p_pass"] = len(ts.get("PASS_TO_PASS", {}).get("success", []))
        out["p2p_fail"] = len(ts.get("PASS_TO_PASS", {}).get("failure", []))
    except Exception as exc:
        instance_logger.exception("Oracle failed for %s (label=%s)", instance_id, label)
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _oracle_line(label: str, o: dict) -> str:
    if o.get("error"):
        return f"{label}: ERROR ({o['error']})"
    return (
        f"{label}: resolved={o['resolved']} "
        f"F2P={o['f2p_pass']}/{o['f2p_pass'] + o['f2p_fail']} "
        f"P2P={o['p2p_pass']}/{o['p2p_pass'] + o['p2p_fail']}"
    )


# ---------------------------------------------------------------------------
# Patch comparison vs gold
# ---------------------------------------------------------------------------

# AIDEV-NOTE: moved to utils/diff_utils.py (z-cpl-44 step 1); alias keeps call sites.
_strip_index_lines = strip_index_lines


# AIDEV-NOTE: moved to utils/diff_utils.py (z-cpl-44 step 1); alias keeps call sites.
_patch_files = patch_file_paths


# AIDEV-NOTE: moved to utils/diff_utils.py (z-cpl-44 step 1); alias keeps call sites.
_hunk_count = hunk_count


# AIDEV-NOTE: moved to utils/diff_utils.py (z-cpl-44 step 1); alias keeps call sites.
_plus_minus_counts = plus_minus_counts


def _compare_to_gold(minimized: str, gold: str) -> dict:
    """Compute structural similarity between minimized and gold patches."""
    min_files = _patch_files(minimized)
    gold_files = _patch_files(gold)
    common = [f for f in min_files if f in gold_files]
    extra = [f for f in min_files if f not in gold_files]
    missing = [f for f in gold_files if f not in min_files]
    min_p, min_m = _plus_minus_counts(minimized)
    gold_p, gold_m = _plus_minus_counts(gold)
    return {
        "byte_equivalent_mod_index": _strip_index_lines(minimized.strip())
                                     == _strip_index_lines(gold.strip()),
        "same_file_set": set(min_files) == set(gold_files),
        "files_subset_of_gold": set(min_files) <= set(gold_files),
        "common_files": common,
        "extra_files_in_minimized": extra,
        "missing_files_vs_gold": missing,
        "minimized_files": min_files,
        "gold_files": gold_files,
        "minimized_hunks": _hunk_count(minimized),
        "gold_hunks": _hunk_count(gold),
        "minimized_added_lines": min_p,
        "minimized_deleted_lines": min_m,
        "gold_added_lines": gold_p,
        "gold_deleted_lines": gold_m,
    }


def _gold_equivalence_label(cmp: dict) -> str:
    """Coarse bucket for aggregation."""
    if cmp["byte_equivalent_mod_index"]:
        return "byte_equivalent"
    if cmp["same_file_set"]:
        return "same_files_diff_content"
    if cmp["files_subset_of_gold"]:
        return "subset_of_gold"
    if cmp["extra_files_in_minimized"] and cmp["common_files"]:
        return "overlap_with_extras"
    if not cmp["common_files"]:
        return "no_overlap"
    return "partial_overlap"


# ---------------------------------------------------------------------------
# Trajectory + minimized-patch IO
# ---------------------------------------------------------------------------

def _read_agent_submission(traj_path: Path) -> str:
    with open(traj_path) as f:
        data = json.load(f)
    patch = (data.get("info", {}) or {}).get("submission", "") or ""
    if patch and not patch.endswith("\n"):
        patch += "\n"
    return patch


def _read_minimized_patch(instance_dir: Path, instance_id: str) -> str:
    p = instance_dir / f"standalone__{instance_id}" / "minimized.patch"
    if not p.is_file():
        raise FileNotFoundError(p)
    return p.read_text()


def _read_results_json(instance_dir: Path) -> dict:
    p = instance_dir / "minimization_results.json"
    if not p.is_file():
        raise FileNotFoundError(p)
    return json.loads(p.read_text())


def _write_results_json(instance_dir: Path, data: dict) -> None:
    write_json_atomic(instance_dir / "minimization_results.json", data)


# ---------------------------------------------------------------------------
# Per-instance worker
# ---------------------------------------------------------------------------

def _process(instance_id: str, save_root: Path, traj_dir: Path,
             oracle_timeout: int, force: bool) -> dict:
    """Returns a dict with at least: instance_id, status, oracle_pre,
    oracle_post, gold_comparison, gold_equivalence."""
    instance_dir = save_root / instance_id
    log_path = instance_dir / "oracle_posthoc.log"
    instance_logger = logging.getLogger(f"oracle.{instance_id}")
    instance_logger.handlers.clear()
    fh = logging.FileHandler(log_path)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    instance_logger.addHandler(fh)
    instance_logger.setLevel(logging.INFO)
    instance_logger.propagate = False

    res: dict = {"instance_id": instance_id, "status": "ok"}
    try:
        existing = _read_results_json(instance_dir)
        if not force and "oracle_post" in existing and "gold_comparison" in existing:
            instance_logger.info("Already has oracle + gold blocks; skipping.")
            res["status"] = "skipped"
            res["oracle_pre"] = existing.get("oracle_pre")
            res["oracle_post"] = existing.get("oracle_post")
            res["gold_comparison"] = existing.get("gold_comparison")
            res["gold_equivalence"] = existing.get("gold_equivalence")
            return res

        traj = traj_dir / f"{instance_id}.traj"
        if not traj.is_file():
            res["status"] = "missing_traj"
            instance_logger.error("Trajectory not found: %s", traj)
            return res
        agent_submission = _read_agent_submission(traj)
        try:
            minimized = _read_minimized_patch(instance_dir, instance_id)
        except FileNotFoundError as exc:
            res["status"] = "missing_minimized"
            instance_logger.error("Minimized patch not found: %s", exc)
            return res

        # Oracle pre
        started = time.time()
        instance_logger.info("Pre-min oracle (agent submission)...")
        oracle_pre = _run_oracle(
            instance_id, agent_submission, label="agent_submission",
            run_id=f"posthoc_pre_{instance_id}",
            timeout=oracle_timeout, instance_logger=instance_logger,
            strip_applied=True,  # pre-strip patch-reversal hunks
            force=force,
        )
        instance_logger.info(_oracle_line("Pre", oracle_pre))

        # Oracle post
        instance_logger.info("Post-min oracle (minimized patch)...")
        oracle_post = _run_oracle(
            instance_id, minimized, label="minimized",
            run_id=f"posthoc_post_{instance_id}",
            timeout=oracle_timeout, instance_logger=instance_logger,
            force=force,
        )
        instance_logger.info(_oracle_line("Post", oracle_post))

        # Gold comparison
        rows = _hf_rows()
        gold = rows.get(instance_id, {}).get("patch", "") or ""
        gold_cmp = _compare_to_gold(minimized, gold) if gold else {
            "byte_equivalent_mod_index": False,
            "same_file_set": False,
            "files_subset_of_gold": False,
            "common_files": [], "extra_files_in_minimized": [], "missing_files_vs_gold": [],
            "minimized_files": _patch_files(minimized), "gold_files": [],
            "minimized_hunks": _hunk_count(minimized), "gold_hunks": 0,
            "minimized_added_lines": 0, "minimized_deleted_lines": 0,
            "gold_added_lines": 0, "gold_deleted_lines": 0,
            "error": "no_gold_patch",
        }
        gold_label = _gold_equivalence_label(gold_cmp) if gold else "no_gold_patch"
        instance_logger.info("Gold comparison: %s", gold_label)

        existing["oracle_pre"] = oracle_pre
        existing["oracle_post"] = oracle_post
        existing["gold_comparison"] = gold_cmp
        existing["gold_equivalence"] = gold_label
        _write_results_json(instance_dir, existing)
        instance_logger.info("Wrote oracle + gold blocks back to %s",
                             instance_dir / "minimization_results.json")

        res.update({
            "elapsed_sec": round(time.time() - started, 1),
            "oracle_pre": oracle_pre,
            "oracle_post": oracle_post,
            "gold_comparison": gold_cmp,
            "gold_equivalence": gold_label,
        })
    except Exception as exc:
        instance_logger.exception("Unexpected failure")
        res["status"] = "error"
        res["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        for h in instance_logger.handlers:
            h.flush()
            h.close()
        instance_logger.handlers.clear()
    return res


# ---------------------------------------------------------------------------
# Summary aggregation
# ---------------------------------------------------------------------------

def _classify_transition(pre: dict | None, post: dict | None) -> str:
    if pre is None or post is None:
        return "no_oracle"
    if pre.get("error") or post.get("error"):
        return "pre_error_or_post_error"
    pre_r = bool(pre.get("resolved"))
    post_r = bool(post.get("resolved"))
    return f"pre_{'resolved' if pre_r else 'unresolved'}_post_{'resolved' if post_r else 'unresolved'}"


def _update_summary(save_root: Path, per_instance_results: list[dict]) -> None:
    """Patch oracle + gold fields into ``minimization_summary.json`` and
    compute aggregate ``oracle_transitions`` + ``gold_equivalence`` blocks."""
    summary_path = save_root / "minimization_summary.json"
    if not summary_path.is_file():
        logging.warning("No minimization_summary.json at %s; skipping aggregate update.",
                        summary_path)
        return
    data = json.loads(summary_path.read_text())
    by_id = {r["instance_id"]: r for r in per_instance_results}

    # Pass 1 — merge this batch's results into the matching entries.
    for entry in data.get("successful_bugs", []):
        bid = entry.get("bug_id")
        if bid not in by_id:
            continue
        r = by_id[bid]
        if r.get("oracle_pre") is not None:
            entry["oracle_pre"] = r["oracle_pre"]
        if r.get("oracle_post") is not None:
            entry["oracle_post"] = r["oracle_post"]
        if r.get("gold_comparison") is not None:
            entry["gold_comparison"] = r["gold_comparison"]
            entry["gold_equivalence"] = r["gold_equivalence"]

    # AIDEV-NOTE: aggregate over ALL enriched entries, not just this batch —
    # otherwise a partial re-run (e.g. --instance-filter) clobbers the
    # full-dataset oracle_transitions / gold_equivalence with a subset count.
    transitions = {
        "pre_resolved_post_resolved": 0,
        "pre_resolved_post_unresolved": 0,
        "pre_unresolved_post_resolved": 0,
        "pre_unresolved_post_unresolved": 0,
        "pre_error_or_post_error": 0,
        "no_oracle": 0,
    }
    equiv: dict[str, int] = {}
    for entry in data.get("successful_bugs", []):
        pre = entry.get("oracle_pre")
        post = entry.get("oracle_post")
        if pre is None and post is None:
            continue
        transitions[_classify_transition(pre, post)] += 1
        ge = entry.get("gold_equivalence")
        if ge is not None:
            equiv[ge] = equiv.get(ge, 0) + 1

    data["oracle_transitions"] = transitions
    data["gold_equivalence"] = equiv
    tmp = str(summary_path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, summary_path)
    logging.info("Wrote aggregate transitions=%s gold_equivalence=%s", transitions, equiv)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save-dir", required=True, metavar="DIR",
                    help="The run_swebench_minimization.py --save-dir output to enrich.")
    ap.add_argument("--traj-dir", required=True, metavar="DIR",
                    help="Directory of <instance_id>.traj files (for the agent's info.submission).")
    ap.add_argument("--num-parallel", type=int, default=8)
    ap.add_argument("--oracle-timeout", type=int, default=600)
    ap.add_argument("--instance-filter", default=None,
                    help="Comma-separated subset of instance IDs (default: all).")
    ap.add_argument("--force", action="store_true",
                    help="Re-run even for instances already enriched (default: skip).")
    args = ap.parse_args(argv)

    save_root = Path(args.save_dir).resolve()
    traj_dir = Path(args.traj_dir).resolve()
    if not save_root.is_dir():
        print(f"Error: save-dir not found: {save_root}", file=sys.stderr)
        return 1
    if not traj_dir.is_dir():
        print(f"Error: traj-dir not found: {traj_dir}", file=sys.stderr)
        return 1

    setup_dispatcher_logging(save_root, prefix="oracle_posthoc")
    logging.info("save_dir=%s traj_dir=%s num_parallel=%d", save_root, traj_dir, args.num_parallel)

    # Discover instances with a minimized patch on disk.
    candidates: list[str] = []
    for d in sorted(save_root.iterdir()):
        if not d.is_dir():
            continue
        if (d / f"standalone__{d.name}" / "minimized.patch").is_file():
            candidates.append(d.name)
    wanted = parse_instance_filter(args.instance_filter)
    if wanted:
        candidates = [c for c in candidates if c in wanted]
    logging.info("Found %d instances with minimized.patch on disk", len(candidates))
    if not candidates:
        logging.info("Nothing to do.")
        return 0

    # Warm the HF cache once before fanning out.
    _hf_rows()

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.num_parallel, thread_name_prefix="oracle-posthoc") as ex:
        futures = {
            ex.submit(_process, iid, save_root, traj_dir, args.oracle_timeout, args.force): iid
            for iid in candidates
        }
        completed = 0
        for fut in as_completed(futures):
            iid = futures[fut]
            try:
                r = fut.result()
            except Exception as exc:
                logging.error("Worker for %s raised: %s", iid, exc)
                r = {"instance_id": iid, "status": "error", "error": str(exc)}
            results.append(r)
            completed += 1
            if completed % 5 == 0 or completed == len(futures):
                logging.info("Progress: %d/%d", completed, len(futures))

    _update_summary(save_root, results)

    # Final summary line.
    transitions = {
        "pre_resolved_post_resolved": 0,
        "pre_resolved_post_unresolved": 0,
        "pre_unresolved_post_resolved": 0,
        "pre_unresolved_post_unresolved": 0,
        "pre_error_or_post_error": 0,
        "no_oracle": 0,
    }
    equiv: dict[str, int] = {}
    for r in results:
        transitions[_classify_transition(r.get("oracle_pre"), r.get("oracle_post"))] += 1
        if r.get("gold_equivalence"):
            equiv[r["gold_equivalence"]] = equiv.get(r["gold_equivalence"], 0) + 1
    logging.info(
        "Oracle transitions: TP=%d FP=%d fixed=%d persistent_fail=%d errors=%d no_oracle=%d",
        transitions["pre_resolved_post_resolved"],
        transitions["pre_resolved_post_unresolved"],
        transitions["pre_unresolved_post_resolved"],
        transitions["pre_unresolved_post_unresolved"],
        transitions["pre_error_or_post_error"],
        transitions["no_oracle"],
    )
    logging.info("Gold equivalence: %s", equiv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
