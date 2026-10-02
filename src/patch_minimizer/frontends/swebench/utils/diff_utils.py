"""Unified-diff parsing shared across the SWE-bench frontend.

AIDEV-NOTE: Before this module the same parsing existed six times — three block-splitters
and three path-extractors. See z-cpl-44 step 1 for the full inventory.
"""
from __future__ import annotations


def split_patch_into_files(patch_text: str) -> list[str]:
    """Split a unified diff into per-file blocks starting at ``diff --git``.

    Moved verbatim from run_swebench_postprocessing.py:157.

    AIDEV-NOTE: Line-scan, not ``re.split(r"(?m)^(?=diff --git )")``. They differ on input
    with a preamble before the first ``diff --git``: this keeps it attached to the first
    block, the regex makes it a separate part. Keep the line-scan form.
    """
    lines = patch_text.splitlines(keepends=True)
    blocks: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("diff --git ") and current:
            blocks.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("".join(current))
    return [b for b in blocks if b.strip()]


def patch_file_paths(patch: str) -> list[str]:
    """Files a unified diff modifies, in order of appearance, de-duplicated.

    Replaces ``_patch_files`` (postprocessing), ``_submission_paths`` (minimization) and
    ``_patch_modified_paths`` (environment). The three bodies were identical apart from the
    container returned.

    AIDEV-NOTE: Returns an **ordered list**, not a set. Callers that want a set wrap it —
    ``set(patch_file_paths(p))``. Do not change this to return a set:
    ``_submission_paths``'s result feeds ``filter_to_submission_paths``, where order is
    observable. (``split("\\t")[0]`` and ``split("\\t", 1)[0]`` are equivalent, so the three
    originals really were the same algorithm.)
    """
    out: list[str] = []
    for ln in patch.splitlines():
        if not ln.startswith("+++ "):
            continue
        p = ln[4:].split("\t", 1)[0].strip()
        if p == "/dev/null":
            continue
        if p.startswith("b/"):
            p = p[2:]
        if p not in out:
            out.append(p)
    return out


def strip_index_lines(patch: str) -> str:
    """Drop ``index <sha>..<sha>`` lines so diffs with different blob hashes compare byte-wise."""
    return "\n".join(ln for ln in patch.splitlines() if not ln.startswith("index "))


def hunk_count(patch: str) -> int:
    """Number of ``@@`` hunk headers."""
    return sum(1 for ln in patch.splitlines() if ln.startswith("@@ "))


def plus_minus_counts(patch: str) -> tuple[int, int]:
    """(added, removed) line counts, counting only inside hunks.

    AIDEV-NOTE: The ``in_hunk`` guard is load-bearing and is why this is NOT merged with
    ``reconstruct_clean_agent_patch.py``'s per-block counter, which omits it and returns a
    single combined total. Merging them could shift the compaction table, so both stay.
    """
    plus = minus = 0
    in_hunk = False
    for ln in patch.splitlines():
        if ln.startswith("@@"):
            in_hunk = True
        elif in_hunk:
            if ln.startswith("+") and not ln.startswith("+++"):
                plus += 1
            elif ln.startswith("-") and not ln.startswith("---"):
                minus += 1
    return plus, minus
