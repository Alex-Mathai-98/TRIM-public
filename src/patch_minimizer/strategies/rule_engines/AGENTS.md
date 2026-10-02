# Rule Engines

> **Purpose:** Pluggable decision logic for the greedy removal loop — determines whether each candidate edit/node should be KEPT or DROPPED based on runtime feedback and (optionally) neural coupling signals.

---

## File Overview

| File | Purpose |
|---|---|
| `base.py` | `BaseRuleEngine` ABC, `Decision` enum (`KEEP`, `DROP`, `DEFER`, `RESOLVE_GROUP`), `DecisionResult` dataclass. All rule engines inherit from this. |
| `kernel_only_rule_engine.py` | `KernelOnlyRuleEngine` — decides based on kernel crash feedback + git diff size signal. Used for standard (non-coupling) minimization. |
| `coupling_aware_rule_engine.py` | `CouplingAwareRuleEngine` — combines runtime feedback with neural coupling signals. Supports deferred decisions for coupled edit groups. Used when coupling analysis is enabled. |
| `__init__.py` | Re-exports: `BaseRuleEngine`, `Decision`, `DecisionResult`, `KernelOnlyRuleEngine`, `CouplingAwareRuleEngine`. |

## When to Use Which Engine

| Scenario | Engine | Why |
|---|---|---|
| Standard kernel minimization (no coupling) | `KernelOnlyRuleEngine` | Only needs crash feedback + diff size |
| Coupling-aware minimization | `CouplingAwareRuleEngine` | Must defer decisions until all coupled partners are visited |
| SWE-bench / dry-run minimization | `KernelOnlyRuleEngine` | No coupling analysis in non-kernel environments |

## Decision Protocol

Both engines implement `decide(feedback, bug_id, phase, granularity, index, has_solution)` → `DecisionResult`.

### KernelOnlyRuleEngine — Truth Table

| Kernel Feedback | Diff Signal | Phase | has_solution | → Decision | stop_search |
|---|---|---|---|---|---|
| `BUILD_FAILED` | any | any | any | **KEEP** | False |
| `STILL_CRASHES` | any | any | any | **KEEP** | False |
| `NO_CRASH` | `SMALLER_DIFF` | any | any | **DROP** | False |
| `NO_CRASH` | `LARGER_DIFF` | phase1 | True | **KEEP** | **True** |
| `NO_CRASH` | `LARGER_DIFF` | phase1 | False | **KEEP** | False |
| `NO_CRASH` | `LARGER_DIFF` | phase2 | any | **KEEP** | False |

### CouplingAwareRuleEngine — Two-Signal Composition

Runtime failure is checked **first** (`BUILD_FAILED`/`STILL_CRASHES` → KEEP), then the neural signal is consulted, and only on the `CAN_DROP`/`None` branch is the diff-size signal read. `phase` and `has_solution` are unused; `stop_search` is always `False`.

`pre_decide(pre_feedback)` runs before runtime: if `pre_feedback.is_compulsory` (coupled partner already failed), it returns `KEEP` and the runtime job is skipped; otherwise `None` (proceed).

| Runtime | Neural Signal | Diff Signal | → Decision |
|---|---|---|---|
| `BUILD_FAILED` | any | any | **KEEP** |
| `STILL_CRASHES` | any | any | **KEEP** |
| `NO_CRASH` | `RESOLVE_GROUP` | any | **RESOLVE_GROUP** (with `group_indices`) |
| `NO_CRASH` | `DEFER` | any | **DEFER** |
| `NO_CRASH` | `CAN_DROP` / `None` | `SMALLER_DIFF` (or no diff env) | **DROP** |
| `NO_CRASH` | `CAN_DROP` / `None` | `LARGER_DIFF` | **KEEP** (diff-size guard) |
| `NO_CRASH` | `MUST_KEEP` | any | **KEEP** (defensive; normally caught by `pre_decide`) |

- **DEFER**: edit is part of a coupled group where not all partners have been visited yet. The greedy loop marks it as visited but does not commit a decision.
- **RESOLVE_GROUP**: all coupled partners visited and all are runtime-droppable. `group_indices` = `deferred_partner_positions` — the caller drops the whole group together.
- **Diff-size guard**: on the uncoupled/`CAN_DROP` path, if the candidate grew (`LARGER_DIFF`) the edit is KEPT even though runtime says droppable.

## Key Patterns

- **Stateless engines.** Neither engine holds state — all state (visited/compulsory/deferred sets) lives in the environments (`CouplingNeuralEnvironment`) and the greedy loop caller. `CouplingAwareRuleEngine` only *reads* `NeuralFeedback` fields (`signal`, `is_compulsory`, `deferred_partner_positions`).
- **`pre_decide(pre_feedback)` hook.** Optional method on `BaseRuleEngine` (default returns `None` = proceed to runtime). `CouplingAwareRuleEngine` returns `KEEP` when `pre_feedback.is_compulsory`, skipping the runtime job for edits whose coupled partner already failed. `KernelOnlyRuleEngine` does not override it.
- **`stop_search` flag.** Set only by `KernelOnlyRuleEngine`, and only in `phase1` when `has_solution` is True and the diff grew (`LARGER_DIFF`) — the caller breaks out of the backward search loop. `CouplingAwareRuleEngine` never sets it.
- **Signal ordering.** Both engines branch on runtime failure first (KEEP), so a `BUILD_FAILED`/`STILL_CRASHES` result overrides any neural/diff signal.
