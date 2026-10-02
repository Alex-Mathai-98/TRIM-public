"""Analyze SWE-bench trajectory steps to measure test-invocation detection coverage.

Classifies every trajectory step into: edit (diff changed), test (our detector),
known non-test (editor/nav/git/etc), or UNCLASSIFIED (potential gaps).

Usage:
    python src/Kernel_Agent/tools/solution_minimization/agent_adapters/swe_agent_adapter/other-code-for-information/swebench-feedback-coverage.py [--trajs-dir DIR]

Sample output (333 trajectories, 19,437 steps):

  ┌───────────────────────────────────────┬───────┬──────────────────┐
  │               Category                │ Count │      Status      │
  ├───────────────────────────────────────┼───────┼──────────────────┤
  │ Other classified (editor/nav/git/etc) │ 7,651 │ Fine — not tests │
  ├───────────────────────────────────────┼───────┼──────────────────┤
  │ Edit steps (diff changed)             │ 5,209 │ Fine — edits     │
  ├───────────────────────────────────────┼───────┼──────────────────┤
  │ Test steps (our detector catches)     │ 6,550 │ Covered          │
  ├───────────────────────────────────────┼───────┼──────────────────┤
  │ UNCLASSIFIED                          │    27 │ All non-tests    │
  └───────────────────────────────────────┴───────┴──────────────────┘

  The 27 unclassified are all non-test commands (mkdir, head, awk, python3 --version,
  psql --help, pwd, etc.) — no remaining gaps in test detection.

Recognized feedback execution actions (4 patterns):

```
┌─────────────────────────────────┬────────────────────────────────────────────────────────┬──────────────────┐
│ Pattern                         │ Regex                                                  │ Count in dataset │
├─────────────────────────────────┼────────────────────────────────────────────────────────┼──────────────────┤
│ pytest / python -m pytest       │ python\d*\s+(?:-\S+\s+)*-m\s+pytest\b or bare pytest\b │              894 │
├─────────────────────────────────┼────────────────────────────────────────────────────────┼──────────────────┤
│ python <script>.py              │ python\d*\s+(?!-c\b)\S+\.py\b                          │            5,089 │
├─────────────────────────────────┼────────────────────────────────────────────────────────┼──────────────────┤
│ python -c "..."                 │ python\d*\s+-c\b                                       │              401 │
├─────────────────────────────────┼────────────────────────────────────────────────────────┼──────────────────┤
│ python -m <module> (non-pytest) │ python\d*\s+(?:-\S+\s+)*-m\s+(?!pytest\b)\S+           │              166 │
├─────────────────────────────────┼────────────────────────────────────────────────────────┼──────────────────┤
│ Total                           │                                                        │            6,550 │
└─────────────────────────────────┴────────────────────────────────────────────────────────┴──────────────────┘
```
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

# --- Test-invocation detector (synced with swebench_parsers.py) ---
_PYTEST_RE = re.compile(
    r"(?:^|&&\s*)(?:cd\s+\S+\s*&&\s*)?(?:python\d*\s+(?:-\S+\s+)*-m\s+)?pytest\b",
    re.MULTILINE,
)
_PYTHON_SCRIPT_RE = re.compile(
    r"(?:^|&&\s*)(?:cd\s+\S+\s*&&\s*)?python\d*\s+(?!-c\b)\S+\.py\b", re.MULTILINE
)
_PYTHON_C_RE = re.compile(
    r"(?:^|&&\s*)(?:cd\s+\S+\s*&&\s*)?python\d*\s+-c\b", re.MULTILINE
)
_PYTHON_M_RE = re.compile(
    r"(?:^|&&\s*)(?:cd\s+\S+\s*&&\s*)?python\d*\s+(?:-\S+\s+)*-m\s+(?!pytest\b)\S+",
    re.MULTILINE,
)
def is_test_invocation(action: str) -> bool:
    if not action:
        return False
    return bool(
        _PYTEST_RE.search(action)
        or _PYTHON_SCRIPT_RE.search(action)
        or _PYTHON_C_RE.search(action)
        or _PYTHON_M_RE.search(action)
    )


# --- Known non-test categories ---
def classify_non_test(action: str) -> str | None:
    a = action.strip()
    if a == "submit":
        return "submit"
    if a.startswith("str_replace_editor"):
        return "editor"
    if a.startswith(("find ", "find_file", "grep ", "search_dir")):
        return "navigation"
    if a.startswith(("ls ", "ls\n")) or a == "ls":
        return "navigation"
    if a.startswith(("cat ", "head ", "tail ")):
        return "navigation"
    if a.startswith("cd ") and "&&" not in a:
        return "navigation"
    if a.startswith("wc "):
        return "navigation"
    if re.match(r"^git\s", a):
        return "git"
    if re.match(r"^(pip |pip3 |conda )", a):
        return "setup"
    if re.match(r"^(echo |printf )", a):
        return "echo"
    if re.match(r"^(mkdir |touch |chmod |ln )", a):
        return "filesystem"
    if re.match(r"^(sed |awk )", a):
        return "edit_cmd"
    if re.match(r"^(rm |cp |mv )", a):
        return "edit_cmd"
    if re.match(r"^(diff |patch )", a):
        return "diff_cmd"
    if re.match(r"^(export |source |\. )", a):
        return "env"
    # cd && <something> compound commands
    if re.match(r"^cd\s+\S+\s*&&", a):
        rest = re.sub(r"^cd\s+\S+\s*&&\s*", "", a).strip()
        if rest.startswith("git "):
            return "git"
        if rest.startswith(("pip ", "pip3 ")):
            return "setup"
        if rest.startswith(("find ", "grep ", "ls", "cat ")):
            return "navigation"
        if rest.startswith("echo "):
            return "echo"
        if rest.startswith(("sed ", "rm ", "cp ", "mv ", "patch ")):
            return "edit_cmd"
        if rest.startswith("diff "):
            return "diff_cmd"
    return None


def main() -> None:
    trajs_dir = sys.argv[1] if len(sys.argv) > 1 else "results/swe-bench-swe-agent-trajs"
    if not os.path.isdir(trajs_dir):
        print(f"Directory not found: {trajs_dir}", file=sys.stderr)
        sys.exit(1)

    files = sorted(f for f in os.listdir(trajs_dir) if f.endswith(".traj"))
    print(f"Trajectories: {len(files)}")

    total = 0
    edit_steps = 0
    test_detected = 0
    classified_other: Counter[str] = Counter()
    unclassified: Counter[str] = Counter()
    unclassified_samples: dict[str, str] = {}

    for fname in files:
        with open(os.path.join(trajs_dir, fname)) as f:
            data = json.load(f)
        traj = data.get("trajectory", [])
        last_diff = ""

        for step in traj:
            total += 1
            action = str(step.get("action", "")).strip()
            diff = step.get("state", {}).get("diff", "")
            diff_changed = diff != last_diff
            last_diff = diff

            if diff_changed:
                edit_steps += 1
                continue

            if is_test_invocation(action):
                test_detected += 1
                continue

            cat = classify_non_test(action)
            if cat:
                classified_other[cat] += 1
            else:
                key = re.sub(r"\s+", " ", action[:80]).strip() or "<empty>"
                unclassified[key] += 1
                if key not in unclassified_samples:
                    unclassified_samples[key] = action[:200]

    print(f"Total steps: {total}")
    print(f"Edit steps (diff changed): {edit_steps}")
    print(f"Test steps (our detector): {test_detected}")
    print(f"Other classified (non-test, non-edit): {sum(classified_other.values())}")
    print(f"UNCLASSIFIED: {sum(unclassified.values())}")
    print()

    print("--- Classified non-test categories ---")
    for cat, count in classified_other.most_common():
        print(f"  {cat}: {count}")

    print()
    print("--- UNCLASSIFIED (potential gaps) ---")
    for key, count in sorted(unclassified.items(), key=lambda x: -x[1]):
        print(f"  [{count:4d}] {key}")
        if count <= 5:
            print(f"         sample: {unclassified_samples[key][:200]}")


if __name__ == "__main__":
    main()
