"""SWE-agent minimization, split by where trajectories come from.

- ``agent_patches`` — a scanned directory (``--agent-patches-dir`` / ``--agent-patches``)
- ``karena_db``     — SQLite lookups, used only by ``agent_patches``
- ``utils``         — env bootstrap (must run before the heavy imports)

AIDEV-NOTE: Split out of run_swe_minimization.py, which keeps its CLI and dispatches to
``agent_patches.load_passes()`` then ``agent_patches.run()``. That runner now
minimizes each pass through ``traj_cli`` (via ``minimize_one_traj``) instead of calling
``minimize_edits`` itself, and sequential/parallel share one worker.

AIDEV-NOTE: ``single_traj`` (``--traj-json``/``--bug-id``) was removed — it wrote every run
to the constant ``save_dir/traj_file__c0/``, so results collided across bugs and the metrics
pipeline could not find them. Use ``cli/traj_cli.py`` for one trajectory.
"""
