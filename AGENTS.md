# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

### Some project **independent** instructions to CLAUDE - to make it a better coding partner

> **purpose** – This file is the onboarding manual for every AI assistant (Claude, Cursor, GPT, etc.) and every human who edits this repository.
> It encodes our coding standards, guard-rails, and workflow tricks so the *human 30 %* (architecture, tests, domain judgment) stays in human hands.[^1]

---

## 0. Always run prelude.sh
Always run the .claude/prelude.sh file - it helps in setting up the correct environment.

## 1. Non-negotiable golden rules

| #:  | AI *may* do                                                                                                                                                                       | AI *must NOT* do                                                                                                                                     |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| G-1 | Whenever unsure about something that's related to the project, ask the developer for clarification before making changes.                                                         | ❌ Write changes or use tools when you are not sure about something project-specific, or if you don't have context for a particular feature/decision. |
| G-2 | Generate code **only inside** relevant source directories (e.g., `src/*.py`) or explicitly pointed files.                                                                         | ❌ Modify `tests/`, `SPEC.md`, or other test/spec files (humans own tests & specs).                                                                   |
| G-3 | Add/update **`AIDEV-NOTE:` anchor comments** near non-trivial edited code.                                                                                                        | ❌ Delete or mangle existing `AIDEV-` comments.                                                                                                       |
| G-4 | Follow lint/style configs (`pyproject.toml`, `.ruff.toml`, `.pre-commit-config.yaml`). Use the project's configured linter, if available, instead of manually re-formatting code. | ❌ Re-format code to any other style.                                                                                                                 |
| G-5 | For changes >300 LOC or >3 files, **ask for confirmation**.                                                                                                                       | ❌ Refactor large modules without human guidance.                                                                                                     |
| G-6 | Stay within the current task context. Inform the dev if it'd be better to start afresh.                                                                                           | ❌ Continue work from a prior prompt after "new task" – start a fresh session.                                                                        |

---

## 2. Coding standards

* **Python**: 3.12+ recommended. Use `async/await` where appropriate (e.g., in web frameworks such as FastAPI).
* **Formatting**: `ruff` enforces 96-char lines, double quotes, sorted imports. Standard `ruff` linter rules.
* **Typing**: Strict (Pydantic v2 models preferred); `from __future__ import annotations`.
* **Naming**: `snake_case` (functions/variables), `PascalCase` (classes), `SCREAMING_SNAKE` (constants).
* **Error Handling**: Typed exceptions; context managers for resources.
* **Documentation**: Google-style docstrings for public functions/classes.
* **Testing**: Separate test files matching source file patterns.

**Error handling patterns**:

* Use typed, hierarchical exceptions defined in `exceptions.py`.
* Catch specific exceptions, not general `Exception`.
* Use context managers for resources (database connections, file handles).
* For async code, use `try/finally` to ensure cleanup.

Example:

```python
from project.exceptions import ValidationError

async def process_data(data: dict) -> Result:
    try:
        # Process data
        return result
    except KeyError as e:
        raise ValidationError(f"Missing required field: {e}") from e
```

---

## 3. Anchor comments

Add specially formatted comments throughout the codebase, where appropriate, for yourself as inline knowledge that can be easily `grep`ped for.

### Guidelines:

* Use `AIDEV-NOTE:`, `AIDEV-TODO:`, or `AIDEV-QUESTION:` (all-caps prefix) for comments aimed at AI and developers.
* Keep them concise (≤ 120 chars).
* **Important:** Before scanning files, always first try to **locate existing anchors** `AIDEV-*` in relevant subdirectories.
* **Update relevant anchors** when modifying associated code.
* **Do not remove `AIDEV-NOTE`s** without explicit human instruction.
* Make sure to add relevant anchor comments whenever a file or piece of code is:

  * too long, or
  * too complex, or
  * very important, or
  * confusing, or
  * could have a bug unrelated to the task you are currently working on.

Example:

```python
# AIDEV-NOTE: perf-hot-path; avoid extra allocations (see project ADRs/design docs)
async def render_feed(...):
    ...
```

---

## 4. Commit discipline

* **Granular commits**: One logical change per commit.
* **Tag AI-generated commits**: e.g., `feat: optimise feed query [AI]`.
* **Clear commit messages**: Explain the *why*; link to issues/ADRs if architectural.
* **Use `git worktree`** for parallel/long-running AI branches (e.g., `git worktree add ../wip-foo -b wip-foo`).
* **Review AI-generated code**: Never merge code you don't understand.

---

## 5. Directory-Specific AGENTS.md Files

* **Always check for `AGENTS.md` files in specific directories** before working on code within them. These files contain targeted context.
* If a directory's `AGENTS.md` is outdated or incorrect, **update it**.
* If you make significant changes to a directory's structure, patterns, or critical implementation details, **document these in its `AGENTS.md`**.
* If a directory lacks a `AGENTS.md` but contains complex logic or patterns worth documenting for AI/humans, **suggest creating one**.

---

## 6. Meta: Guidelines for updating AGENTS.md files

### Elements that would be helpful to add:

1. **Decision flowchart**: A simple decision tree for "when to use X vs Y" for key architectural choices would guide my recommendations.
2. **Reference links**: Links to key files or implementation examples that demonstrate best practices.
3. **Domain-specific terminology**: A small glossary of project-specific terms would help me understand domain language correctly.
4. **Versioning conventions**: How the project handles versioning, both for APIs and internal components.

### Format preferences:

1. **Consistent syntax highlighting**: Ensure all code blocks have proper language tags (`python`, `bash`, etc.).
2. **Hierarchical organization**: Consider using hierarchical numbering for subsections to make referencing easier.
3. **Tabular format for key facts**: The tables are very helpful - more structured data in tabular format would be valuable.
4. **Keywords or tags**: Adding semantic markers (like `#performance` or `#security`) to certain sections would help me quickly locate relevant guidance.

---

## 7. AI Assistant Workflow: Step-by-Step Methodology

When responding to user instructions, the AI assistant (Claude, Cursor, GPT, etc.) should follow this process to ensure clarity, correctness, and maintainability:

1. **Consult Relevant Guidance**: When the user gives an instruction, consult the relevant instructions from `AGENTS.md` files (both root and directory-specific) for the request.
2. **Clarify Ambiguities**: Based on what you could gather, see if there's any need for clarifications. If so, ask the user targeted questions before proceeding.
3. **Break Down & Plan**: Break down the task at hand and chalk out a rough plan for carrying it out, referencing project conventions and best practices.
4. **Trivial Tasks**: If the plan/request is trivial, go ahead and get started immediately.
5. **Non-Trivial Tasks**: Otherwise, present the plan to the user for review and iterate based on their feedback.
6. **Track Progress**: Use a to-do list (internally, or optionally in a `TODOS.md` file) to keep track of your progress on multi-step or complex tasks.
7. **If Stuck, Re-plan**: If you get stuck or blocked, return to step 3 to re-evaluate and adjust your plan.
8. **Update Documentation**: Once the user's request is fulfilled, update relevant anchor comments (`AIDEV-NOTE`, etc.) and `AGENTS.md` files (if used in the project).
9. **User Review**: After completing the task, ask the user to review what you've done, and repeat the process as needed.
10. **Session Boundaries**: If the user's request isn't directly related to the current context and can be safely started in a fresh session, suggest starting from scratch to avoid context confusion.

[^1]: This principle emphasizes human oversight for critical aspects like architecture, testing, and domain-specific decisions, ensuring AI assists rather than fully dictates development.

---

## Project Overview

**patch-minimizer** (internal name **TRIM**) is a standalone Python framework for
minimizing patches produced by AI coding agents. Given an agent's multi-step
trajectory of code edits, it identifies the minimal subset of edits that still
produces a correct fix — stripping cosmetic changes, dead code, debug scaffolding,
and redundant modifications.

The core algorithm is greedy and feedback-driven: it tries removing edits one at a
time and keeps only those whose removal breaks the patch — either structurally (fails
to apply) or at runtime (the bug reappears / the build breaks). Correctness feedback
comes from a pluggable **Environment** (real kernel build+test via kGym, Docker-based
SWE-bench test execution, or a structural dry run).

> **Note:** kGym here is only a *kernel build/test backend*. This project is **not**
> the old "kArena/kGym" evaluation product — if you find docs referencing Syzbot
> crawling, an LLM judge, a FastAPI backend, or SQLite eval tables as the product,
> they are stale.

See [`README.md`](README.md) for full, worked usage examples and the paper (Table IV /
RQ4) reproduction pipeline.

### Packaging

- Package root: `src/patch_minimizer/` (PEP 517, `setuptools`, `pyproject.toml`).
- Python **>= 3.10** (3.12 recommended; the SlopCodeBench metric wants its own 3.12 env).
- Runtime deps: `diff-match-patch`, `unidiff`, `networkx`, `requests`, `aiohttp`, `bashlex` (required: rm/cp/sed parsing silently degrades without it — see `edit_parsing_helpers/AGENTS.md`).
- Optional extras: `.[swebench]` (`datasets`, `docker`, `swebench`, `huggingface_hub`),
  `.[neural]` (`litellm`).

```bash
pip install -e .            # core
pip install -e ".[swebench]" # Docker-based SWE-bench test execution
pip install -e ".[neural]"   # LLM-based coupling analysis
```

## Core Concepts

| Concept | Definition |
|---|---|
| **`Edit`** | The atomic unit — a search/replace on one file (`core/edit.py`). Has `filename`, `before`, `after`, `explanation`, `global_edit_idx`. |
| **`Node`** | A group of semantically-related edits, i.e. one trajectory step (`core/node.py`). |
| **patch list** | The canonical input: `list[tuple[parent_diff, list[Edit]]]` — one tuple per trajectory step. |
| **Environment** | Feedback backend that judges whether a candidate patch (edits removed) is still correct. |
| **Rule engine** | Turns environment feedback into KEEP / DROP / DEFER / RESOLVE_GROUP decisions. |
| **Coupling** | Optional dependency tracking between edits, to avoid redundant test runs. |
| **Statement** | One agent command kind (`rm`, `sed -i`, `undo_edit` …): detection + success rule + effect (`agent_adapter_base/statements/`). |

## System Architecture

Every significant directory has its own `AGENTS.md` — consult it before editing code
there. This map shows the layout and where to look:

```
src/patch_minimizer/
├── core/                      # Generic types: Edit, Node, EditApplier, RWLock, CouplingMap
├── benchmark_utils/
│   └── kernel/                # Kernel-specific: BugData, kGym types, LLMResponseHistory, conversation types
├── agent_adapters/            # Trajectory file → patch_list, per agent format
│   ├── agent_adapter_base/    #   TrajectoryParser ABC + ParserRegistry + shared edit-extraction helpers
│   │   ├── parsers/           #     Parser ABC, registry, trajectory types
│   │   ├── edit_parsing_helpers/ #  sed / heredoc / str_replace / patch-command extractors
│   │   ├── statements/        #     Statement / RevertStatement ABCs + shared *StatementBase parsing
│   │   └── evaluation/        #     AgentPass / AgentPassesStore (bulk load from Karena exports)
│   ├── swe_agent_adapter/     #   SWE-Agent + SWE-bench trajectory formats (EXACT matching)
│   │   └── statements/        #     per-command classes (each adapter has one); dispatch = ordered lists
│   ├── mini_swe_agent_adapter/#   mini-SWE-agent bash-edit format
│   ├── openhands_adapter/     #   OpenHands file_editor format (SEMI_FUZZY matching)
│   └── karena_integration/    #   Optional kArena DB / metadata integration point
├── strategies/                # L1 — benchmark-agnostic minimization
│   ├── minimize_core.py       #   minimize_edit_list() — THE edit-level core; no benchmark knowledge
│   ├── minimize_patches.py    #   minimize_edits() — kernel/dry-run wrapper + batch driver + CLI
│   ├── feedback_stats.py      #   _summarize_feedback_stats (shared)
│   ├── repo_patch_manager.py  #   Git apply/check/clean; per-file line-offset tracking
│   ├── algorithms/            #   Hierarchical greedy removal
│   │   ├── node/              #     Node-level (coarsest): suffix → subsequence → 1-minimal
│   │   └── edit/             #     File-level then edit-level greedy removal + coupling
│   ├── environments/          #   Contract + generic envs: base, feedback_types, DryRun, GitDiff, Neural
│   └── rule_engines/          #   KernelOnlyRuleEngine (used by ALL benchmarks), CouplingAwareRuleEngine
├── frontends/                 # L2 — benchmark-specific implementations
│   ├── kernel/                #   KernelEnvironment, ToolSolnMinimize, minimize_patches, utils/, example_code/
│   └── swebench/              #   SWEBenchEnvironment, submission filter, cli/, utils/, example_code/
└── metrics/                   # Reporting — pure stdlib, read-only over results
    ├── kbench_epr_and_delta_slop/  # Kernel: EPR + Δ_Slop (modified lines)
    ├── swebench/              #   Table IVa/b/c + gold "18"
    └── scbench_metric/        #   Table IVd — SlopCodeBench verbosity
```

**Layering rule:** CLIs → `frontends/` → `strategies/` → `core/`. Arrows point one way only; no
`__init__.py` in `core/` or `strategies/` may import from `frontends/`.

### Entry points

| Use case | Entry point |
|---|---|
| Bring-your-own patch list (benchmark-agnostic) | `strategies.minimize_core.minimize_edit_list(...)` |
| From a saved agent trajectory | `agent_adapters.agent_adapter_base.utils.load_trajectory_file(...)` → `minimize_edit_list(...)` |
| SWE-bench, one trajectory | `python -m patch_minimizer.frontends.swebench.cli traj` |
| SWE-bench, a directory | `frontends/swebench/example_code/run_swebench_minimization.py` |
| Kernel minimization | `frontends.kernel.cli.minimize_edits.minimize_edits(...)` / `frontends.kernel.cli.kagent.minimize_edits_for_kagent.ToolSolnMinimize` |

## How Minimization Works

Minimization is **hierarchical** — coarse to fine — so cheap coarse passes remove
whole steps before expensive fine-grained edit testing:

1. **Node level** (`algorithms/node/`) — remove entire trajectory steps that are
   unnecessary (suffix search → interior subsequence removal → 1-minimal refinement).
2. **File level** (`algorithms/edit/minimize_at_file_level.py`) — within surviving
   nodes, drop all edits to a file if the file isn't needed.
3. **Edit level** (`algorithms/edit/`) — greedy removal of individual edits.
4. **Refinement** — `one_minimal_guarantee()` repeats greedy removal to a fixed point,
   so no single remaining edit is removable.

Each candidate is judged by a **feedback Environment**; a **rule engine** maps the
feedback to a KEEP/DROP decision. A **coupling-aware** path can defer decisions until
all coupled partners are tested. Results can be cached by `frozenset(global_edit_idx)`
to skip re-testing identical edit combinations.

## Common Commands

```bash
# Single SWE-bench instance (the traj CLI; the batch driver below has no --traj)
PYTHONPATH=src python -m patch_minimizer.frontends.swebench.cli traj \
    --agent swe-agent \
    --traj <path.traj> --bug-id <id> --save-dir <out> --minimize-at edit

# Parallel over a directory of .traj files (resume-safe)
PYTHONPATH=src python src/patch_minimizer/frontends/swebench/example_code/run_swebench_minimization.py \
    --traj-dir <dir> --save-dir <out> --num-parallel 8 --minimize-at edit
```

`--minimize-at` is `edit` (finest) or `node` (trajectory-step granularity).
The full RQ4 / Table IV reproduction (download → minimize → postprocess → metrics) is
documented step-by-step in [`README.md`](README.md).

## Testing

- Parsing regression suite: `bash tests/run_parsing_benchmarks.sh` (4 suites: SWE-bench
  SWE-agent + Live-kBench SWE-agent / OpenHands / mini-SWE-agent; `parser_baseline.py verify`).
- There is no end-to-end `test_swebench_minimization.py`; it was never in this repo.
- Per project rule **G-2**, humans own tests — do not edit test files without being asked.

## Output Format

`minimize_edits()` returns a dict: `minimized_patch` (git diff), `short_path`
(surviving edit descriptions), `feedback_stats` (job counts by algorithm/outcome),
`original_edit_count`, `minimized_edit_count`, and `reduction`.
