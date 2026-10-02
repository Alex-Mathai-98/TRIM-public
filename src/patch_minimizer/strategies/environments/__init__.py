"""Environment abstraction for patch validation feedback.

AIDEV-NOTE: Benchmark-agnostic environment contract and generic implementations:
- BaseEnvironment / BaseFeedback: the contract every environment implements
- PatchFeedback / RuntimeFeedback: shared feedback vocabulary (all benchmarks emit these)
- DryRunEnvironment: No-op environment for testing without job submission
- NeuralEnvironment: LLM-based judgment on patch quality (dummy impl, override for real)
- FeedbackReceiver: Orchestrates multiple environments and aggregates feedback

Benchmark-specific environments are NOT here — see patch_minimizer.frontends.kernel /
patch_minimizer.frontends.swebench.
"""
from __future__ import annotations

from .base import BaseEnvironment, BaseFeedback
from .feedback_types import PatchFeedback, RuntimeFeedback
from .dry_run_environment import DryRunEnvironment
from .neural_environment import NeuralEnvironment, NeuralFeedback, NeuralSignal
from .git_diff_environment import GitDiffEnvironment, DiffSizeFeedback, DiffSizeSignal, PatchStats
from .feedback_receiver import FeedbackReceiver, EnvironmentFeedback

# AIDEV-NOTE: This package exports ONLY what lives in it. Benchmark-specific environments
# (KernelEnvironment, SWEBenchEnvironment) live in patch_minimizer.frontends.* — import them
# from there. Re-exporting them here would make the contract depend on its own implementers
# and reintroduce a circular import, since loading any submodule of this package runs this
# __init__ first. See claude_plans/frontend-split-clean-design.md.

__all__ = [
    # Base classes
    "BaseEnvironment",
    "BaseFeedback",
    # Runtime environments
    "DryRunEnvironment",
    "RuntimeFeedback",
    "PatchFeedback",
    # Neural environment
    "NeuralEnvironment",
    "NeuralFeedback",
    "NeuralSignal",
    # Git diff environment
    "GitDiffEnvironment",
    "DiffSizeFeedback",
    "DiffSizeSignal",
    "PatchStats",
    # Feedback aggregation
    "FeedbackReceiver",
    "EnvironmentFeedback",
]
