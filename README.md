# ✂️ TRIM: Trajectory-guided Redundancy Identification and Minimization

<a href="https://arxiv.org/abs/2607.18161"><img src="https://img.shields.io/badge/arxiv-2607.18161-red?style=for-the-badge&logo=arxiv&logoColor=white&labelColor=black" alt="arxiv 2607.18161"></a>

```bibtex
@misc{mathai2026trimreducingaigeneratedcodeslop,
      title={TRIM: Reducing AI-Generated CodeSlop via Agent Trajectory Minimization}, 
      author={Alex Mathai and Shobini Iyer and Aleksandr Nogikh and Petros Maniatis and Franjo Ivancic and Junfeng Yang and Baishakhi Ray},
      year={2026},
      eprint={2607.18161},
      archivePrefix={arXiv},
      primaryClass={cs.SE},
      url={https://arxiv.org/abs/2607.18161}, 
}
```

A standalone framework for minimizing patches produced by AI coding agents. Given an agent's multi-step trajectory of code edits, TRIM identifies the minimal subset of edits that still produces a correct fix — stripping away cosmetic changes, dead code, debug scaffolding, and redundant modifications.

## 💡 Motivation

AI coding agents (SWE-Agent, OpenHands, etc.) often produce patches with far more edits than necessary. A 10-edit patch may contain only 2 edits that actually fix the bug — the rest are noise. This hurts interpretability, increases review burden, and inflates diffs.

TRIM solves this by applying a greedy, feedback-driven minimization algorithm: it tries removing edits one at a time and keeps only those whose removal causes the patch to fail (either structurally or at runtime).

## 🏗️ Architecture

```
patch_minimizer/
├── core/                        # Generic data types: Edit, Node, RWLock, EditApplier
├── benchmark_utils/
│   └── kernel/                  # Kernel-specific: BugData, kGym types, LLM history
├── agent_adapters/              # Trajectory parsers for different agent formats
│   ├── swe_agent_adapter/       #   SWE-Agent / SWE-bench trajectory format
│   ├── openhands_adapter/       #   OpenHands agent trajectory format
│   ├── mini_swe_agent_adapter/  #   Mini-SWE-Agent bash-edit format
│   ├── karena_integration/      #   Karena database integration + metadata
│   └── agent_adapter_base/      #   Shared parsing utilities + registry
│       ├── parsers/             #     Format-specific parser registry
│       ├── edit_parsing_helpers/#     Edit extraction + normalization helpers
│       ├── statements/          #     Shared base classes for agent commands (rm, sed -i, cp, ...)
│       └── evaluation/          #     Loads agent passes from Karena agent-patches exports
├── strategies/
│   ├── algorithms/              # Minimization algorithms
│   │   ├── edit/                #   Edit-level minimizer (finest granularity)
│   │   └── node/                #   Sequence-level minimizer (trajectory-step granularity)
│   ├── environments/            # Generic feedback backends (benchmark ones live in frontends/)
│   │   ├── dry_run_environment  #   No-op (structural checks only)
│   │   ├── git_diff_environment #   Diff-size tracking
│   │   ├── neural_environment   #   LLM judgment as a second signal next to runtime feedback
│   │   └── feedback_receiver    #   Combines runtime, git-diff and (optional) neural feedback
│   ├── rule_engines/            # Edit selection strategies (coupling-aware, etc.)
│   ├── repo_patch_manager.py    # Git repo operations (apply/check/clean patches)
│   └── minimize_core.py         # Benchmark-agnostic entry point: minimize_edit_list()
└── frontends/                   # Benchmark-specific code + CLI entry points
    ├── kernel/                  #   KernelEnvironment (kGym build+test) + cli/ (traj, minimize_edits, kagent)
    └── swebench/                #   SWEBenchEnvironment (Docker tests) + cli/ + utils/ + example_code/
```

Dependencies flow one way: CLIs → `frontends/` → `strategies/` → `core/`. Nothing in `core/` or
`strategies/` imports from `frontends/`.

## 📦 Installation

```bash
pip install -e .

# Optional: SWE-bench support (Docker-based test execution)
pip install -e ".[swebench]"
```

Requires Python 3.10+ (3.12 recommended).

## 🚀 Usage

### ⭐ Quickstart: minimize one trajectory with the `traj` CLI (recommended)

The easiest way to use TRIM: point the `traj` command at **one** agent trajectory
and it handles everything else. It parses the trajectory, builds the test environment, runs
minimization, and saves the minimized patch.

There is one `traj` command per benchmark: SWE-bench ([below](#swe-bench-trajs)) and Live-kBench, the Linux kernel
benchmark ([further below](#live-kbench-trajs)).

<a id="swe-bench-trajs"></a>

#### 1️⃣ Minimizing SWE-Bench Trajs for different agent scaffolds

---

SWE-bench bugs are Python repository bugs, so candidates are tested by running the tests inside
the instance's SWE-bench Docker image (`sweb.eval.*`). The command is `frontends.swebench.cli traj`.

Before a full run, set up Docker, the `sweb.eval.*` images and the repository clones: see [SWE-bench Setup](#5-swe-bench-setup).

##### Agent Scaffold

| Scaffold | Supported | Trajectory file |
|---|---|---|
| **SWE-agent** | ✅ | `<instance_id>.traj` |
| OpenHands, mini-SWE-agent, others | ❌ not yet | – |

For SWE-bench we currently support **SWE-agent** trajectories only. Other scaffolds are not
supported yet. `--agent` is **required**, the same as in the Live-kBench command, and must be
`swe-agent`; any other value stops with an error.

**SWE-agent:**

```bash
python -m patch_minimizer.frontends.swebench.cli traj \
    --agent swe-agent \
    --traj results/swe-bench-swe-agent-trajs/sphinx-doc__sphinx-8459.traj \
    --bug-id sphinx-doc__sphinx-8459 \
    --save-dir /tmp/min_sphinx-8459 \
    --minimize-at edit
```

It ends with a summary like:

```
=== DONE sphinx-doc__sphinx-8459 in 36.8s — reduction=3_to_1 (orig=3, min=1) ===
```

and writes:

| File | Contents |
|---|---|
| `<save-dir>/standalone__<instance_id>/minimized.patch` | The minimized git diff |
| `<save-dir>/minimization_results.json` | Edit counts, reduction, feedback stats |

| Option | Required | What it does |
|---|---|---|
| `--agent` | ✅ | Agent scaffold. Must be `swe-agent`; anything else is an error |
| `--traj` | ✅ | The SWE-agent `.traj` file |
| `--bug-id` | ✅ | SWE-bench instance id, e.g. `django__django-16527` |
| `--save-dir` | | Output folder (default `/tmp/min_<instance_id>`) |
| `--minimize-at` | | `edit` (finest, default) or `node` (whole trajectory steps) |
| `--repo-dir` | | Repo clone (default `results/swe-bench-workspace/<owner>__<repo>`) |
| `--manual-asserts-dir` | | Directory of `<instance_id>/` hand-written reproduction tests, added to the agent's own tests |
| `--only-parse` | | Parse the trajectory and print what would be minimized; **no Docker or repo needed**, takes seconds |

**Tip: check parsing first.** `--only-parse` shows the edits, test files and test commands
TRIM extracted from the trajectory, without running anything:

```bash
python -m patch_minimizer.frontends.swebench.cli traj \
    --agent swe-agent \
    --traj results/swe-bench-swe-agent-trajs/sphinx-doc__sphinx-8459.traj \
    --bug-id sphinx-doc__sphinx-8459 --only-parse
```

**Many trajectories at once?** Use the batch driver. It runs this same `traj` minimization for
every `<instance_id>.traj` in a directory, in parallel, and is resume-safe:

```bash
python src/patch_minimizer/frontends/swebench/example_code/run_swebench_minimization.py \
    --traj-dir results/swe-bench-swe-agent-trajs \
    --save-dir results/minimization_333 \
    --num-parallel 24 --clones-per-repo 2 --minimize-at edit
```

| Option | Required | What it does |
|---|---|---|
| `--traj-dir` | ✅ | Directory of `<instance_id>.traj` files |
| `--save-dir` | ✅ | Output folder: one subfolder per instance, plus `minimization_summary.json` |
| `--num-parallel` | | Worker count (default 1). Set it to about 12 × `--clones-per-repo` |
| `--clones-per-repo` | | Clones per repository, so that many instances of one repo run at once (default 1) |
| `--minimize-at` | | `edit` (default) or `node` |
| `--instance-filter` | | Comma-separated instance IDs to run (default: all) |
| `--retry-failed` | | Also retry instances that failed in an earlier run (default: skip them) |
| `--workspace-root` | | Where the repo clones live (default `results/swe-bench-workspace`) |
| `--manual-asserts-dir` | | Directory of `<instance_id>/` hand-written reproduction tests, added to the agent's own tests |
| `--then-postprocess` | | Afterwards, run the hidden SWE-bench oracle on the original and minimized patches |
| `--then-reconstruct` | | Afterwards, write each agent patch rebuilt on a clean base commit (`original_clean.patch`) |

Rerunning the same command skips instances that already finished.

<a id="live-kbench-trajs"></a>

#### 2️⃣ Minimizing Live-kBench Trajs for different agent scaffolds

---

Live-kBench bugs are Linux kernel bugs, so candidates are built and tested with kGym instead of
Docker. The command is `frontends.kernel.cli traj`, and `--agent` picks the scaffold.

##### Agent Scaffold

| Scaffold | `--agent` | Trajectory file |
|---|---|---|
| **SWE-agent** | `swe-agent` | `<bug_id>/run_N.json` |
| **OpenHands** | `openhands` | `run_N/<bug_id>/original/traj.json` |
| **mini-SWE-agent** | `mini-swe-agent` | `run_N/<bug_id>/original/traj.json` |

Only `--agent` and `--traj` change between scaffolds. The examples use the sample
trajectories in `tests/parsing_benchmark_tests/` for bug `ddb1f9850ea49c02c07efe60c5487483ba04bb66`.

**SWE-agent:**

```bash
python -m patch_minimizer.frontends.kernel.cli traj \
    --agent swe-agent \
    --traj tests/parsing_benchmark_tests/live_kbench_swe_agent_gemini_3_pro/trajs/ddb1f9850ea49c02c07efe60c5487483ba04bb66/run_1.json \
    --bug-id ddb1f9850ea49c02c07efe60c5487483ba04bb66 \
    --repo-dir /path/to/linux-repo --save-dir /tmp/min_kernel_swe_agent
```

**OpenHands:**

```bash
python -m patch_minimizer.frontends.kernel.cli traj \
    --agent openhands \
    --traj tests/parsing_benchmark_tests/live_kbench_openhands_gemini_3_pro/trajs/run_1/ddb1f9850ea49c02c07efe60c5487483ba04bb66/original/traj.json \
    --bug-id ddb1f9850ea49c02c07efe60c5487483ba04bb66 \
    --repo-dir /path/to/linux-repo --save-dir /tmp/min_kernel_openhands
```

**mini-SWE-agent:**

```bash
python -m patch_minimizer.frontends.kernel.cli traj \
    --agent mini-swe-agent \
    --traj tests/parsing_benchmark_tests/live_kbench_mini_swe_claude_opus_4_5/trajs/run_1/ddb1f9850ea49c02c07efe60c5487483ba04bb66/original/traj.json \
    --bug-id ddb1f9850ea49c02c07efe60c5487483ba04bb66 \
    --repo-dir /path/to/linux-repo --save-dir /tmp/min_kernel_mini_swe
```

| Option | Required | What it does |
|---|---|---|
| `--agent` | ✅ | Scaffold: `swe-agent`, `openhands` or `mini-swe-agent` |
| `--traj` | ✅ | The trajectory file (see the table above) |
| `--bug-id` | ✅ | Live-kBench bug id (the kernel bug hash) |
| `--repo-dir` | ✅ (unless `--only-parse`) | A Linux kernel clone to apply candidate patches in |
| `--run-jobs` | | Submit real kGym build/test jobs. **Without it, a structural dry run** (only checks that patches apply) |
| `--syzkaller-rollback-tag` | | Syzkaller version kGym uses to run the crash reproducer (default `master`; only matters with `--run-jobs`) |
| `--benchmark-folder` | | Per-bug JSONs + `golden_subset.json` (default `$KBENCH_PATH`) |
| `--save-dir` | | Output folder for `minimization_results.json` |
| `--minimize-at` | | `edit` (finest, default) or `node` (whole trajectory steps) |
| `--only-parse` | | Parse the trajectory only; no Linux clone, bug data or jobs needed |

**Tip: check parsing first.** Add `--only-parse` and drop `--repo-dir` / `--save-dir` to see
what TRIM extracted from any of the three trajectories above, in a few seconds:

```
Parsed:
  nodes:          2
  edits:          2
  files changed:  ['net/mac80211/iface.c', 'net/wireless/core.c']
```

Exit code 0 means the trajectory parsed and has edits; 1 means it has none.

### 🧩 Core Concepts

### 1. Edit

An `Edit` represents a single code modification — a search-and-replace operation on a file:

```python
from patch_minimizer.core.edit import Edit

edit = Edit(
    filename="fs/nilfs2/the_nilfs.c",
    before="old code to find",
    after="new replacement code",
    explanation="Why this change is needed",
    global_edit_idx=0,
)
```

### 2. Patch List

A patch list is the primary input format — a list of `(parent_diff, List[Edit])` tuples, where each tuple represents one step in the agent's trajectory:

```python
patch_list = [
    (None, [edit_0, edit_1]),              # Step 0: no prior diff context
    (git_diff_string, [edit_2, edit_3]),   # Step 1: Optional, applied on top of step 0's diff
]
```

### 3. Environments

Environments provide feedback on whether a candidate patch (with some edits removed) is still correct:

| Environment | Use Case |
|---|---|
| `KernelEnvironment` | Submits real kernel build+test jobs via kGym |
| `SWEBenchEnvironment` | Runs tests in Docker containers for SWE-bench instances |
| `DryRunEnvironment` | Always returns "pass" — tests structural applicability only |

### 4. Python API: `minimize_edit_list()`

For use from Python. Bring your own patch list. The example below is SWE-bench instance
`django__django-11099`: two edits are the real fix (the username regex must use `\A`/`\Z`
so a trailing newline is rejected), and the third is a debug print the minimizer should drop.

```python
import logging
from patch_minimizer.core.edit import Edit
from patch_minimizer.core.rw_lock import RWLock
from patch_minimizer.strategies.minimize_core import minimize_edit_list
from patch_minimizer.strategies.algorithms.edit.edit_minimizer import EditMinimizer
from patch_minimizer.strategies.environments import FeedbackReceiver, GitDiffEnvironment
from patch_minimizer.frontends.swebench.environment import SWEBenchEnvironment
from patch_minimizer.frontends.swebench.utils.hf_dataset import base_commits_for

instance_id = "django__django-11099"
logger = logging.getLogger(instance_id)

# 1. The edits: one search/replace each, with a unique global_edit_idx
edits = [
    Edit(filename="django/contrib/auth/validators.py",
         before="class ASCIIUsernameValidator(validators.RegexValidator):\n    regex = r'^[\\w.@+-]+$'",
         after="class ASCIIUsernameValidator(validators.RegexValidator):\n    regex = r'\\A[\\w.@+-]+\\Z'",
         explanation="Reject trailing newline in ASCII usernames",
         global_edit_idx=0),
    Edit(filename="django/contrib/auth/validators.py",
         before="class UnicodeUsernameValidator(validators.RegexValidator):\n    regex = r'^[\\w.@+-]+$'",
         after="class UnicodeUsernameValidator(validators.RegexValidator):\n    regex = r'\\A[\\w.@+-]+\\Z'",
         explanation="Reject trailing newline in Unicode usernames",
         global_edit_idx=1),
    Edit(filename="django/contrib/auth/validators.py",
         before="from django.utils.translation import gettext_lazy as _",
         after="from django.utils.translation import gettext_lazy as _\nprint('DEBUG: validators loaded')",
         explanation="Debug print left behind by the agent",
         global_edit_idx=2),
]

# 2. The patch list: one (parent_diff, edits) tuple per trajectory step
patch_list = [(None, edits)]

# 3. The feedback environment: runs these commands in the instance's sweb.eval.* image
env = SWEBenchEnvironment(
    instance_id=instance_id,
    feedback_commands=[
        # existing tests must still pass
        {"command": "cd /testbed && ./tests/runtests.py auth_tests.test_validators"},
        # the bug: a trailing newline must be rejected by both validators
        {"command": "cd /testbed && python -c \"import re; "
                    "from django.contrib.auth.validators import ASCIIUsernameValidator as A, "
                    "UnicodeUsernameValidator as U; "
                    "assert not re.search(A.regex, 'joe\\n') and not re.search(U.regex, 'joe\\n')\""},
    ],
    logger=logger,
)

# 4. The minimizer: applies candidate patches in a local clone at the base commit
base_commit = base_commits_for([instance_id])[instance_id]
minimizer = EditMinimizer(
    "results/swe-bench-workspace/django__django", RWLock(), base_commit, logger,
    kernel_base_url=None, swebench_mode=True,
)

result = minimize_edit_list(
    bug_id=instance_id,
    patch_list=patch_list,
    minimizer=minimizer,
    feedback_aggregator=FeedbackReceiver(env, git_diff_env=GitDiffEnvironment()),
    minimize_at="edit",    # "edit" (finest) or "node" (trajectory-step level)
)

print(f"Reduction: {result['original_edit_count']} -> {result['minimized_edit_count']} edits")
print(f"Minimized patch:\n{result['minimized_patch']}")
```

For a whole trajectory file, `minimize_traj()` in `frontends/swebench/cli/traj_cli.py` does
all of the above for you (it is what the `traj` CLI calls).

### 5. SWE-bench Setup

Everything a full SWE-bench run needs (the `--only-parse` check needs none of it).

#### Prerequisites

1. Docker daemon running
2. SWE-bench instance images (`sweb.eval.x86_64.<instance_id>:latest`)
3. A local clone of each target repository
4. SWE-agent `.traj` files
5. The `[swebench]` extra (see [Installation](#-installation))

#### Downloading the trajectories

Download the SWE-agent trajectories used in the paper (submission
`20250522_sweagent_claude-4-sonnet-20250514`, 333 resolved instances, ≈3 GB) from SWE-bench's
public storage:

```bash
python src/patch_minimizer/frontends/swebench/example_code/download_swe_bench_swe_agent_trajs.py \
    --submission 20250522_sweagent_claude-4-sonnet-20250514 \
    --output-dir results/swe-bench-swe-agent-trajs
```

#### Setting up the workspace

Clone the target repos into a workspace directory. The runner expects `results/swe-bench-workspace/<owner>__<repo>/`:

```bash
mkdir -p results/swe-bench-workspace
git clone https://github.com/astropy/astropy.git results/swe-bench-workspace/astropy__astropy
git clone https://github.com/django/django.git results/swe-bench-workspace/django__django
git clone https://github.com/matplotlib/matplotlib.git results/swe-bench-workspace/matplotlib__matplotlib
git clone https://github.com/mwaskom/seaborn.git results/swe-bench-workspace/mwaskom__seaborn
git clone https://github.com/pallets/flask.git results/swe-bench-workspace/pallets__flask
git clone https://github.com/psf/requests.git results/swe-bench-workspace/psf__requests
git clone https://github.com/pydata/xarray.git results/swe-bench-workspace/pydata__xarray
git clone https://github.com/pylint-dev/pylint.git results/swe-bench-workspace/pylint-dev__pylint
git clone https://github.com/pytest-dev/pytest.git results/swe-bench-workspace/pytest-dev__pytest
git clone https://github.com/scikit-learn/scikit-learn.git results/swe-bench-workspace/scikit-learn__scikit-learn
git clone https://github.com/sphinx-doc/sphinx.git results/swe-bench-workspace/sphinx-doc__sphinx
git clone https://github.com/sympy/sympy.git results/swe-bench-workspace/sympy__sympy
```

#### SWE-bench Docker images

The minimizer does not build images itself: each instance's `sweb.eval.x86_64.<instance_id>:latest`
image must exist locally before a run. Build them with SWE-bench's `prepare_images`
(drop `--instance_ids` to build every instance in the dataset):

```bash
python -m swebench.harness.prepare_images \
    --dataset_name princeton-nlp/SWE-bench_Verified --split test \
    --instance_ids django__django-11099 sphinx-doc__sphinx-8459 \
    --tag latest --env_image_tag latest --max_workers 4
```

Or verify existing images:
```bash
docker images --format '{{.Repository}}:{{.Tag}}' | grep sweb.eval | head -5
```

## ⚙️ How It Works

The minimization algorithm operates in phases:

1. **Edit Sequence level minimization** — removes entire trajectory steps that are unnecessary (e.g., exploratory edits the agent later superseded).

2. **File-level minimization** — within surviving edit sequences, identifies files whose edits can be dropped entirely.

3. **Edit-level greedy removal** — iterates over individual edits and tries removing each one. An edit is kept only if its removal causes:
   - The patch to fail to apply cleanly, OR
   - The runtime test to fail (bug reappears / build breaks)

4. **Refinement passes** — re-checks previously kept edits, since removing later edits may have made earlier ones droppable.

At each step, a **feedback environment** (kernel build+test, Docker container, or dry run) determines whether the reduced patch is still correct. A **coupling-aware rule engine** optionally tracks dependencies between edits to avoid redundant test runs.

## 📤 Output Format

`minimize_edit_list()` returns a dictionary (so does the kernel wrapper `minimize_edits()`):

```python
{
    "bug_id": "django__django-11099",
    "short_path": [...],              # Surviving edits
    "minimized_patch": "diff ...",    # Final git diff
    "feedback_stats": {...},          # Job counts by algorithm/outcome
    "original_edit_count": 10,
    "minimized_edit_count": 3,
    "reduction": "10_to_3",
}
```

## 📧 Contact

Please open issues or email [Alex](mailto:alexmathai@cs.columbia.edu) for TRIM questions.

## 🔁 Reproducing the SWE-bench Results

To reproduce the paper's SWE-bench results (Table IV) end to end, follow
[`reproducing.md`](reproducing.md). It walks through minimizing all 333 SWE-agent
trajectories, running the hidden SWE-bench oracle, rebuilding the clean agent patches, and printing the
paper tables.
