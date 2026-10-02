from __future__ import annotations
import os
import pickle
from os.path import join as pjoin

from patch_minimizer.benchmark_utils.kernel.bug_data import BugData
from patch_minimizer.benchmark_utils.kernel.llm_response_history import LLMResponseHistory


class CustomUnpickler(pickle.Unpickler):
    """Custom unpickler to remap Kernel_Agent module paths to patch_minimizer equivalents.

    AIDEV-NOTE: .pkl files from Kernel_Agent runs contain pickled objects that
    reference Kernel_Agent module paths. This unpickler remaps them so the .pkl
    files load without Kernel_Agent installed.
    """

    # AIDEV-NOTE: Two-stage remapping:
    # 1. Legacy renames (old Kernel_Agent paths → current Kernel_Agent paths)
    # 2. Kernel_Agent → patch_minimizer (so .pkl loads without Kernel_Agent)
    _LEGACY_RENAMES = {
        'util_funcs': 'Kernel_Agent.kgym_utils.util_funcs',
        'Kernel_Agent.llm_response_history': 'Kernel_Agent.environment.llm_response_history',
        'Kernel_Agent.kernel_write_strategies.edit': 'Kernel_Agent.tools.generate_patch.edit',
        'Kernel_Agent.kernel_write_strategies.kernel_write_driver': 'Kernel_Agent.tools.generate_patch.write_driver',
    }

    _KERNEL_AGENT_TO_LOCAL = {
        'Kernel_Agent.environment.llm_response_history': 'patch_minimizer.benchmark_utils.kernel.llm_response_history',
        'Kernel_Agent.kgym_utils.util_funcs': 'patch_minimizer.benchmark_utils.kernel.util_funcs',
        'Kernel_Agent.kgym_utils.types': 'patch_minimizer.benchmark_utils.kernel.kgym_types',
        'Kernel_Agent.tools.generate_patch.edit': 'patch_minimizer.core.edit',
        'Kernel_Agent.tools.generate_patch.extract_status': 'patch_minimizer.core.edit',
        'Kernel_Agent.data_classes.bug_data': 'patch_minimizer.benchmark_utils.kernel.bug_data',
        'Kernel_Agent.data_classes.kvm_parameters': 'patch_minimizer.benchmark_utils.kernel.kvm_parameters',
        'Kernel_Agent.benchmark_utils.kernel.conversation_types': 'patch_minimizer.benchmark_utils.kernel.conversation_types',
        'Kernel_Agent.environment.conversation_types': 'patch_minimizer.benchmark_utils.kernel.conversation_types',
    }

    def find_class(self, module, name):
        # Stage 1: legacy renames
        if module in self._LEGACY_RENAMES:
            module = self._LEGACY_RENAMES[module]

        # Stage 2: Kernel_Agent → patch_minimizer
        if module in self._KERNEL_AGENT_TO_LOCAL:
            module = self._KERNEL_AGENT_TO_LOCAL[module]

        return super().find_class(module, name)


def instantiate_llm_response_history(save_dir, base_commit, bug_id, model_id):
    """ Instantiate LLM Response history. """
    save_path = pjoin(save_dir, LLMResponseHistory.get_unique_id(model_id, bug_id) + ".pkl")
    if os.path.exists(save_path):
        with open(save_path, "rb") as f:
            response_history = CustomUnpickler(f).load()
        try:
            assert(response_history.save_dir == save_dir), "save_dir has changed, was {} ==> {}".format(response_history.save_dir, save_dir)
        except Exception:
            response_history.save_dir = save_dir
        assert(response_history.base_commit == base_commit), "base_commit has changed"
        assert(response_history.bug_id == bug_id), "bug_id has changed"
        assert(response_history.model_id == model_id), "model_id has changed"
    else:
        assert(False)
    return response_history


def get_base_commit(benchmark_folder, bug_id, parent_commit_flag=False):
    """Get base commit from kernel bug JSON file."""
    bug_path = os.path.join(benchmark_folder, bug_id + ".json")
    bug_data = BugData.from_json_file(bug_path)
    return bug_data.get_base_commit(parent_commit_flag=parent_commit_flag)
