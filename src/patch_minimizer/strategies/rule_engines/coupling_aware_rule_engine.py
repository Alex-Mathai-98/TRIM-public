"""Coupling-aware rule engine for deferred-decision greedy removal.

AIDEV-NOTE: Stateless — reads NeuralFeedback from EnvironmentFeedback.
State mutation (mark_visited) happens in the greedy loop, not here.

===========================================================================
 HOW THIS RULE ENGINE WORKS — PLAIN ENGLISH
===========================================================================

Overview
--------
During greedy removal, we try dropping edits one-by-one (backward pass) and
ask: "does the patch still fix the bug without this edit?"  For *uncoupled*
edits the answer is simple (runtime says yes/no).  But when edits are
**semantically coupled** (e.g. a struct field addition and its usage), we
must decide them **as a group** — dropping one without the other is
meaningless.

This rule engine handles that by combining two independent signals:

  1. **Runtime feedback** — did the kernel build & still crash?
  2. **Neural (coupling) feedback** — is this edit part of a coupled group,
     and have all group members been visited yet?


Input Signals
-------------

### PatchFeedback (from kernel runtime environment)

  BUILD_FAILED   — The kernel did not compile after removing this edit.
                   The edit is structurally necessary.

  STILL_CRASHES  — The kernel compiled but the bug still reproduces.
                   The edit is functionally necessary for the fix.

  NO_CRASH       — The kernel compiled and the bug no longer reproduces
                   *without* this edit.  The edit is a candidate for removal.

### NeuralSignal (from CouplingNeuralEnvironment)

  CAN_DROP       — The edit is not part of any coupled group, or all coupled
                   partners have been visited already and none were deferred.
                   Decide purely based on runtime.

  DEFER          — The edit is coupled with partners that have not all been
                   visited yet.  Do not make a final decision now — wait.

  RESOLVE_GROUP  — All coupled partners have been visited and are
                   runtime-droppable.  `deferred_partner_positions` carries
                   the positions of those partners.  Test dropping the entire
                   group atomically.

  MUST_KEEP      — A coupled partner already failed runtime (BUILD_FAILED or
                   STILL_CRASHES), so this edit is compulsory.  This is
                   normally caught by pre_decide before runtime even runs.


Decision Logic — Truth Tables
-----------------------------

### Table 1: pre_decide (before runtime test)

  is_compulsory | Decision | skip_runtime | Rationale
  --------------|----------|--------------|-------------------------------------------
  True          | KEEP     | Yes          | Coupled partner already failed — no need to test
  False / None  | (none)   | No           | Proceed to runtime test

### Table 2: decide (after runtime test)

  NOTE: phase and has_solution are unused in this engine (marked X).

  KernelEnv Feedback | GitDiffEnv Feedback | NeuralSignal Feedback | Decision      | stop_search | Rationale
  -------------------|---------------------|-----------------------|---------------|-------------|-----------------------------------------------
  NO_CRASH           | X                   | MUST_KEEP             | KEEP          | False       | Defensive fallthrough — compulsory (is caught by pre_decide)
  BUILD_FAILED       | X                   | X                     | KEEP          | False       | Build broke — edit is necessary
  STILL_CRASHES      | X                   | X                     | KEEP          | False       | Bug still reproduces — edit is necessary
  NO_CRASH           | X                   | RESOLVE_GROUP         | RESOLVE_GROUP | False       | All coupled partners visited & droppable — test group atomically
  NO_CRASH           | X                   | DEFER                 | DEFER         | False       | Coupled partners not all visited yet — wait
  NO_CRASH           | SMALLER_DIFF        | CAN_DROP / None       | DROP          | False       | Uncoupled, fix held, patch got smaller — safe to drop
  NO_CRASH           | LARGER_DIFF         | CAN_DROP / None       | KEEP          | False       | Uncoupled, fix held, but patch grew — keep


Example: 2-member coupled group {A, B}  (backward iteration)
-------------------------------------------------------------

  Visit B: runtime=NO_CRASH, neural=DEFER(partners=[])
           → DEFER.  B stays in working list.

  Visit A: runtime=NO_CRASH, neural=RESOLVE_GROUP(partners=[pos_of_B])
           → RESOLVE_GROUP([pos_of_B]).
           Greedy loop tests removing A+B together.
           If test passes → both dropped atomically.


Example: 3-member coupled group {A, B, C}  (backward iteration)
----------------------------------------------------------------

  Visit C: runtime=NO_CRASH, neural=DEFER(partners=[])  → DEFER
  Visit B: runtime=NO_CRASH, neural=DEFER(partners=[])  → DEFER
  Visit A: runtime=NO_CRASH, neural=RESOLVE_GROUP(partners=[pos_of_B, pos_of_C])
           → RESOLVE_GROUP([pos_of_B, pos_of_C]).
           Greedy loop tests removing A+B+C together.


Example: coupled group {A, B} where B fails runtime
----------------------------------------------------

  Visit B: runtime=STILL_CRASHES → KEEP.
           mark_visited(B, False) → A becomes compulsory.

  Visit A: pre_decide sees A is compulsory → KEEP (no runtime test).

  Both preserved.


Example: 3-member group {A, B, C} — detailed trace with mid-pass failure
-------------------------------------------------------------------------

  Group {A, B, C}, visiting B when C is visited but A isn't:

  Visit C:  runtime=NO_CRASH → DEFER (A, B unvisited)
            mark_visited(C, True) → C enters _deferred

  Visit B:  runtime test RUNS → say NO_CRASH
            get_feedback: unvisited=[A] → non-empty → plain DEFER (no positions)
            rule engine: DEFER
            mark_visited(B, True) → B enters _deferred
            B stays in working list (no decision yet)

  Visit A:  runtime=NO_CRASH
            get_feedback: unvisited=[] → deferred_positions=[pos_B, pos_C]
            rule engine: RESOLVE_GROUP → test A+B+C together

  The important point: at step 2, the runtime test on B gives us information
  (via mark_visited), but the DROP decision is deferred. If B had failed
  runtime instead:

  Visit B:  runtime=STILL_CRASHES → KEEP immediately (step 1, before neural)
            mark_visited(B, False) → A and C become compulsory

  Visit A:  pre_decide → compulsory KEEP (no runtime test)

  C was already deferred (in working) → stays preserved.

  The compulsory cascade fires mid-pass because KEEP decisions are never
  deferred — only DROP decisions are.  That's the core rule:

    - KEEP = safe  → decide immediately, propagate compulsory to partners
    - DROP = risky → defer until the whole group can be tested atomically
===========================================================================
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from patch_minimizer.strategies.environments import PatchFeedback
from patch_minimizer.strategies.environments.git_diff_environment import DiffSizeSignal
from patch_minimizer.strategies.environments.neural_environment import NeuralSignal, NeuralFeedback

from .base import BaseRuleEngine, Decision, DecisionResult

if TYPE_CHECKING:
    from patch_minimizer.strategies.environments import EnvironmentFeedback


class CouplingAwareRuleEngine(BaseRuleEngine):
    """Decides KEEP/DROP/DEFER/RESOLVE_GROUP by reading NeuralFeedback from EnvironmentFeedback.

    AIDEV-NOTE: Stateless — no neural_env reference. All coupling info arrives via NeuralFeedback
    fields (is_compulsory, deferred_partner_positions). State mutation (mark_visited) happens
    in the greedy loop.
    """

    def __init__(self, logger):
        super().__init__(logger)

    def pre_decide(self, pre_feedback: NeuralFeedback | None = None) -> DecisionResult | None:
        """Skip runtime test for compulsory edits (coupled partner already failed).

        AIDEV-NOTE: pre_feedback is provided by the greedy loop via
        feedback_aggregator.get_pre_feedback(). If is_compulsory, return KEEP.
        """
        if pre_feedback is not None and pre_feedback.is_compulsory:
            self.logger.info("Pre-decide: compulsory KEEP (coupled partner already failed)")
            return DecisionResult(Decision.KEEP)
        return None

    def decide(self, feedback: "EnvironmentFeedback", bug_id: str, phase: str,
               granularity: str, index: int,
               has_solution: bool = False) -> DecisionResult:
        neural = feedback.neural_feedback
        runtime_status = feedback.feedback
        job_id = feedback.job_id
        g = granularity.capitalize()

        if runtime_status in (PatchFeedback.BUILD_FAILED, PatchFeedback.STILL_CRASHES):
            self.logger.info(f"[{bug_id}] {g}-level: Job {job_id} ==> {runtime_status.value} - keeping index {index}")
            return DecisionResult(Decision.KEEP)

        # --- Runtime says droppable (NO_CRASH) — consult coupling signal ---
        if neural and neural.signal == NeuralSignal.RESOLVE_GROUP:
            self.logger.info(f"[{bug_id}] {g}-level: Job {job_id} ==> Resolve group at index {index} (partners={neural.deferred_partner_positions})")
            return DecisionResult(Decision.RESOLVE_GROUP, group_indices=neural.deferred_partner_positions)

        if neural and neural.signal == NeuralSignal.DEFER:
            self.logger.info(f"[{bug_id}] {g}-level: Job {job_id} ==> Deferring index {index}")
            return DecisionResult(Decision.DEFER)

        if neural is None or neural.signal == NeuralSignal.CAN_DROP:
            # AIDEV-NOTE: Diff-size guard — if candidate grew larger, keep the edit
            diff_fb = feedback.diff_size_feedback
            if diff_fb is not None and diff_fb.signal == DiffSizeSignal.LARGER_DIFF:
                self.logger.info(f"[{bug_id}] {g}-level: Job {job_id} ==> Keeping index {index} (diff grew: {diff_fb.patch_stats.total_lines} > {diff_fb.baseline_total})")
                return DecisionResult(Decision.KEEP)
            self.logger.info(f"[{bug_id}] {g}-level: Job {job_id} ==> Dropped index {index} (no coupling opinion)")
            return DecisionResult(Decision.DROP)

        # NeuralSignal.MUST_KEEP from get_feedback means compulsory — shouldn't reach here
        # because pre_decide catches it, but handle gracefully.
        self.logger.info(f"[{bug_id}] {g}-level: Job {job_id} ==> Compulsory keep at index {index}")
        return DecisionResult(Decision.KEEP)
