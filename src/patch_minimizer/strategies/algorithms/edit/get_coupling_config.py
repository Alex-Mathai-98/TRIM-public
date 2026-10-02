from __future__ import annotations

from patch_minimizer.strategies.environments import FeedbackReceiver
from patch_minimizer.strategies.environments.coupling_neural_environment import CouplingNeuralEnvironment
from patch_minimizer.strategies.rule_engines.coupling_aware_rule_engine import CouplingAwareRuleEngine


def get_edit_level_rule_engine_and_feedback_agg(feedback_aggregator, flattened, coupling_map, rule_engine, logger):
    """Return (feedback_aggregator, rule_engine) for edit-level minimization.

    If coupling_map has groups, wraps in coupling-aware feedback + rule engine.
    Otherwise returns defaults unchanged.
    """
    if coupling_map and coupling_map.groups:
        flat_edits = [e for slot in flattened for e in slot]
        neural_env = CouplingNeuralEnvironment(coupling_map, flat_edits)
        return (
            FeedbackReceiver(
                feedback_aggregator.runtime_env,
                neural_env=neural_env,
                git_diff_env=feedback_aggregator.git_diff_env,
            ),
            CouplingAwareRuleEngine(logger),
        )
    return feedback_aggregator, rule_engine
