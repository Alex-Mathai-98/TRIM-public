"""kAgent / CrashFixer path — minimization from a saved ``.pkl`` results tree.

Separate from the trajectory agents (swe-agent, openhands, mini-swe), which all share
``cli/traj_cli.py``. This path has its own pipeline: ``kagent_adapter`` loads the ``.pkl``
tree, and ``ToolSolnMinimize`` runs its own per-trajectory dispatch.

- ``minimize_edits_for_kagent`` — ``ToolSolnMinimize``, ``minimize_bug_solution()``,
  ``run_minimization_on_results()`` (batch driver: repo pool, threading, resume), ``main()``
- ``kagent_cli``                — thin wrapper, the ``kagent`` subcommand of ``cli/main.py``

AIDEV-NOTE: Known gap (see frontends/kernel/AGENTS.md) — ``ToolSolnMinimize.minimize_one_path()``
bypasses ``strategies/minimize_core.minimize_edit_list()``, so this is a second code path for
the same logic. Unaffected by the traj_cli unification, which covered the trajectory agents only.
"""
