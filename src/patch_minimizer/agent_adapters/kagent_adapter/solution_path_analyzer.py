from patch_minimizer.benchmark_utils.kernel.llm_response_history import LLMResponseHistory
from collections import deque

class SolutionPathAnalyzer:
    """
    Analysis & Path Finding component for LLM response histories.

    This class provides static methods to analyze LLM response histories,
    identify successful debugging paths, and extract corresponding edit sequences.
    """

    @staticmethod
    def find_successful_nodes(llm_response: LLMResponseHistory):
        """
        Find the nodes of the tree that have resolved the bug.
        The 'bug_resolved' attribute will be True for such nodes.
        Meant to be invoked only as a post-facto analysis.
        """
        use_lock = False
        rw_lock = None
        success_nodes = []
        success_jobs = []
        success_patches = []
        all_nodes = llm_response.collect_all_nodes(use_lock=use_lock, rw_lock=rw_lock)
        for (node, _) in all_nodes:
            bug_resolved = llm_response.get_node_attr(node, "bug_resolved", use_lock=use_lock, rw_lock=rw_lock)
            job_id = llm_response.get_node_attr(node, "job_id", use_lock=use_lock, rw_lock=rw_lock)
            final_diff = llm_response.get_node_attr(node, "final_git_diff", use_lock=use_lock, rw_lock=rw_lock)
            passed_validation = llm_response.get_node_attr(node, "passed_validation", use_lock=use_lock, rw_lock=rw_lock)
            if (bug_resolved is not None) and (bug_resolved) \
                and ((passed_validation is not None) and passed_validation):
                success_nodes.append(node)
                success_jobs.append(job_id)
                success_patches.append(final_diff)
        return success_nodes, success_jobs, success_patches

    @staticmethod
    def find_successful_paths(node_name: str, llm_response: LLMResponseHistory):
        """ Given a successful node, find the path from root to the node. """
        queue = deque()
        queue.append(("0", []))
        while len(queue):
            node = queue[0][0]
            parents = queue[0][1]
            if node == node_name:
                return parents + [node]
            queue.popleft()
            child_list = llm_response.get_children_of_node(node, use_lock=False, rw_lock=None)
            modified_child_list = [(ele, parents + [node]) for ele in child_list]
            queue.extend(modified_child_list)
        assert(False)

    @staticmethod
    def find_all_succesful_paths(llm_response: LLMResponseHistory):
        """ Find all successful paths in the response history tree. """
        successful_paths = []
        success_nodes, _, _ = SolutionPathAnalyzer.find_successful_nodes(llm_response)
        for node in success_nodes:
            successful_paths.append(SolutionPathAnalyzer.find_successful_paths(node, llm_response)[1:])
        return successful_paths

    @staticmethod
    def find_successful_edit_paths(llm_response: LLMResponseHistory):
        """
        Extract the actual code edits corresponding to successful paths.

        Returns:
            succ_edit_paths: List of edit sequences (List[List[List[Edit]]])
            successful_paths: Corresponding node name paths
        """
        succ_edit_paths = []
        successful_paths = SolutionPathAnalyzer.find_all_succesful_paths(llm_response)
        for solution_trajectory in successful_paths:
            solution_edits = []
            for node_name in solution_trajectory:
                solution_edits.append(
                    (llm_response.get_node_attr(node_name, "parent_git_diff", use_lock=False, rw_lock=None), llm_response.get_node_attr(node_name, "valid_edits", use_lock=False, rw_lock=None))
                )
            succ_edit_paths.append(solution_edits)
        return succ_edit_paths, successful_paths
