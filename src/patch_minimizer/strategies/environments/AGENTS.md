# Minimization Environments

> **Purpose:** Feedback environment abstractions for patch validation during solution minimization. An Environment answers "is this candidate patch (some edits removed) still correct?" and, optionally, "are the dropped edits coupled?". `FeedbackReceiver` aggregates the runtime, neural (coupling), and diff-size environments into a single `EnvironmentFeedback` that the rule engines consume.

---

## File Overview

| File | Purpose |
|---|---|
| `base.py` | `BaseEnvironment` ABC + `BaseFeedback` dataclass. Interface: `get_feedback(patch: str, edits: list[Edit], edits_being_dropped: list[Edit] = None) -> BaseFeedback`. |
| `kernel_environment.py` | `KernelEnvironment` — submits patches to kernel build/run infra via `KReproducer`, returns `RuntimeFeedback(status, job_id)`. `PatchFeedback` enum: `NO_CRASH` / `STILL_CRASHES` / `BUILD_FAILED`. Retries up to `_MAX_INFRA_RETRIES` (3) on transient infra failures before falling through to `STILL_CRASHES`. Also defines `DryRunEnvironment` (always returns `NO_CRASH`, job_id `"N/A"`; replaces `run_jobs=False`). |
| `neural_environment.py` | `NeuralEnvironment` — LLM-coupling feedback base (dummy impl returns `NeuralFeedback()` = `CAN_DROP`, no opinion). Defines `NeuralSignal` enum (`CAN_DROP`, `MUST_KEEP`, `DEFER`, `RESOLVE_GROUP`) and `NeuralFeedback` dataclass (`signal`, `confidence`, `reasoning`, `is_compulsory`, `deferred_partner_positions`). |
| `coupling_neural_environment.py` | `CouplingNeuralEnvironment(NeuralEnvironment)` — coupling state machine over `global_edit_idx` UIDs. Tracks `_visited` / `_compulsory` / `_deferred` sets; advanced by the greedy loop via `mark_visited()`. Adds `get_pre_feedback()` (compulsory pre-check) and returns `DEFER` / `MUST_KEEP` / `RESOLVE_GROUP` / `CAN_DROP` from `get_feedback()`. |
| `git_diff_environment.py` | `GitDiffEnvironment` — tracks baseline patch size, emits `DiffSizeSignal` (`SMALLER_DIFF` when `candidate_total <= baseline`, else `LARGER_DIFF`) in `DiffSizeFeedback`. Owns `PatchStats.from_diff` (single parse point). Stateful baseline via `reset_baseline(total)` / `confirm_drop()`. **Not** a `BaseEnvironment` subclass — observes stats only, never runs patches. |
| `swebench_environment.py` | `SWEBenchEnvironment(BaseEnvironment)` — stateless Docker-based SWE-bench test execution. Returns `RuntimeFeedback` (reuses `PatchFeedback`); stores per-command `SWEBenchFeedback` in `self.last_feedback`. |
| `feedback_receiver.py` | `FeedbackReceiver` — orchestrates runtime + optional neural + optional diff-size envs, aggregates into `EnvironmentFeedback`. Entry point for greedy-loop feedback. Also delegates `get_pre_feedback()`, `mark_visited()`, `reset_baseline()`, `confirm_drop()` to sub-environments. |
| `__init__.py` | Public API — re-exports all environment classes, enums, and feedback dataclasses. |

## Core interface

```python
class BaseEnvironment(ABC):
    @abstractmethod
    def get_feedback(
        self, patch: str, edits: list[Edit], edits_being_dropped: list[Edit] = None
    ) -> BaseFeedback: ...
```

`patch` is the candidate diff **with the dropped edits already removed**; `edits` are the edits still in it; `edits_being_dropped` are the edits under test (used only by neural envs for coupling judgment). `BaseFeedback` is an empty base dataclass — subclasses carry the real fields.

## Feedback signals by environment

| Environment | Feedback type | Signal field | Values |
|---|---|---|---|
| `KernelEnvironment` / `DryRunEnvironment` | `RuntimeFeedback` | `status` (aliased `.feedback`) | `PatchFeedback.NO_CRASH` / `STILL_CRASHES` / `BUILD_FAILED` |
| `SWEBenchEnvironment` | `RuntimeFeedback` | `status` | `NO_CRASH` (all commands exit 0) / `STILL_CRASHES` (any non-zero) / `BUILD_FAILED` (patch failed to apply) |
| `NeuralEnvironment` (base) | `NeuralFeedback` | `signal` | always `CAN_DROP` (dummy) |
| `CouplingNeuralEnvironment` | `NeuralFeedback` | `signal` | `CAN_DROP` / `DEFER` / `MUST_KEEP` / `RESOLVE_GROUP` |
| `GitDiffEnvironment` | `DiffSizeFeedback` | `signal` | `DiffSizeSignal.SMALLER_DIFF` (`<= baseline`) / `LARGER_DIFF` |

`FeedbackReceiver.get_feedback()` composes these into `EnvironmentFeedback(feedback, job_id, patch_stats, neural_feedback, diff_size_feedback, build_failure_stage)`.

## `EnvironmentFeedback` fields

| Field | Source | Notes |
|---|---|---|
| `feedback` | runtime env `.status` | `PatchFeedback` value the rule engines branch on first |
| `job_id` | runtime env | kernel job id, `swebench_<instance_id>`, or `"N/A"` (dry run) |
| `patch_stats` | `GitDiffEnvironment` (fallback: direct `PatchStats.from_diff`) | added/removed/total lines |
| `neural_feedback` | neural env (or `None`) | coupling signal + `is_compulsory` + `deferred_partner_positions` |
| `diff_size_feedback` | diff env (or `None`) | `SMALLER_DIFF` / `LARGER_DIFF` + baseline_total |
| `build_failure_stage` | `getattr(runtime, "build_failure_stage", None)` | e.g. compile_gate vs reproduction; usually `None` |

## When to use which environment

| Scenario | Runtime env | Neural env | Diff env |
|---|---|---|---|
| Standard kernel minimization | `KernelEnvironment` | none | `GitDiffEnvironment` |
| Coupling-aware kernel minimization | `KernelEnvironment` | `CouplingNeuralEnvironment` | `GitDiffEnvironment` |
| SWE-bench (Python) minimization | `SWEBenchEnvironment` | none | `GitDiffEnvironment` |
| Offline / no-job smoke test | `DryRunEnvironment` | optional | optional |

## Key patterns / design decisions

- **Aggregation over inheritance.** `FeedbackReceiver` composes three orthogonal environments rather than one env doing everything. Neural and diff envs are optional; passing only a runtime env is valid.
- **Coupling state machine (`CouplingNeuralEnvironment`).** State (`_visited` / `_compulsory` / `_deferred`, all keyed by `global_edit_idx`) is advanced by the greedy loop via `mark_visited(edit, runtime_can_drop)`, **not** by the rule engine. `_deferred ⊂ _visited` by construction. On a runtime failure of one member, all coupled partners are marked `_compulsory` (compulsory cascade).
- **Dead-UID filtering.** `_uid_to_pos` (built from the surviving edit list) contains only surviving edits; UIDs dropped by earlier node/file minimization are absent, which naturally filters them out and prevents infinite `DEFER` on partners that no longer exist.
- **`get_pre_feedback()` short-circuit.** `FeedbackReceiver.get_pre_feedback(edits_being_dropped)` is called by the greedy loop *before* runtime; if a dropped edit's coupled partner already failed, it returns `MUST_KEEP` / `is_compulsory=True` so the rule engine's `pre_decide` can skip a runtime job.
- **Single parse point for diff size.** `GitDiffEnvironment` is the sole caller of `PatchStats.from_diff`; `FeedbackReceiver` reads `patch_stats` from `DiffSizeFeedback` when a diff env is wired in. `confirm_drop()` is no-arg — it promotes the last candidate's total to the new baseline.
- **Kernel infra retries.** `KernelEnvironment` retries transient infra failures (`CRASH_LOST_CONNECTION`, `MESSAGE_SETUP_FAILURE`, `CRASH_NO_OUTPUT`) up to 3 times; `NO_CRASH` requires both `MESSAGE_NO_CRASH` special status *and* `JobStatus.FINISHED`; `JobStatus.ABORTED` → `BUILD_FAILED`.

## SWEBenchEnvironment specifics

- **Stateless container-per-call.** Each `get_feedback()` runs a fresh container from `sweb.eval.x86_64.<instance_id>:latest` (arch prefix `x86_64` per swebench v4.1.0 image naming) via `sleep infinity`, then `container.remove(force=True)` in a `finally`.
- **Flow per call:** `_apply_patch` (`git checkout -- . && git clean -fd && git apply --verbose /tmp/candidate.patch`; failure → `BUILD_FAILED`) → `_install_runtime_deps` (activate `testbed` conda env + `pip install pytest`; adds `roman` for sphinx images) → `_copy_test_files_to_container` (writes oracle `test_files`, **skipping paths the patch itself modified** so a `str_replace` fragment can't clobber the patched tree) → run each `feedback_commands[*]["command"]` (short-circuits on first non-zero exit) → `NO_CRASH` if all pass else `STILL_CRASHES`.
- **`_strip_from_patch`.** Drops `diff --git` chunks for `pyproject.toml` / `tox.ini` / `setup.py` (SWE-bench images pre-apply config changes, so agent diffs for these have stale context and fail `git apply`).
- **`validate_feedback_commands(full_patch)`.** Pre-flight: applies the full patch once, then drops feedback commands that fail — via a path-existence pre-filter (`_referenced_py_paths` + `_missing_paths_in_container`, drops commands whose every explicit `.py` path arg is missing) and an exec-based check (non-zero exit). Mutates `self.feedback_commands` in place; returns the dropped commands.
- **Import-time build-constant patches (`_patch_swebench_build_constants`).** Strips `--no-use-pep517`, removes `--branch/--single-branch` from clone scripts (deleted upstream branches), and prepends `pip install roman` to sphinx `test_cmd` (not `pip_packages`, to avoid env-image hash churn).
