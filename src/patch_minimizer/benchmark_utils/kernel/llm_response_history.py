from patch_minimizer.core.rw_lock import RWLock
from os.path import join as pjoin
from os.path import exists
from collections import deque
from copy import deepcopy
import networkx as nx
import pickle
import os
from patch_minimizer.benchmark_utils.kernel.kgym_types import SpecialConditions, ResponseExtracted, JobStatus, LLMHistoryNodeErrors
from patch_minimizer.benchmark_utils.kernel.conversation_types import ConversationHistory
import json
from typing import Optional, Dict, Any, List

class LLMResponseHistory() :
    """ A data structure to keep track of all the response trees by the LLM
    while trying to resolve the linux kernel crash problem. """

    def __init__(self, save_dir, base_commit, bug_id, model_id, rw_lock) -> None:
        ######## Important information to uniquely identify the interaction ########
        self.save_dir = save_dir
        self.bug_id = bug_id
        self.base_commit = base_commit
        self.model_id = model_id

        self.CRASH_no_output = "no output from test machine"
        self.CRASH_lost_connection = "lost connection to test machine"
        self.MESSAGE_setup_failure = "failed to set up instance"
        self.MESSAGE_no_crash = "no crash reproduced"
        self.saved_history = False

        self.max_index = 0
        self.decision_tree = nx.DiGraph()
        self.add_node("0", use_lock=True, rw_lock=rw_lock)
        self.curr_node_name = "0"
        self.add_node_attr("0", "depth", 0, use_lock=True, rw_lock=rw_lock)
        self.stop_exploration = False

        self.branch_factor = None
        self.max_depth = None
        self.tree_explored = False
        ############################################################################

    def __setstate__(self, state):
        """Handle unpickling with backward compatibility for removed attributes.

        This method is called when unpickling old .pkl files that may contain
        deprecated node attributes. It removes these attributes silently to
        ensure smooth migration.
        """
        self.__dict__.update(state)

        # AIDEV-NOTE: Migration code for deprecated node attributes.
        # When removing node attributes from get_node_attributes(), add them here
        # so old pickles load correctly without the removed attribute.
        deprecated_attrs = ["applied_lessons", "system_prompt", "user_prompt", "system_prompt_for_reasoning", "user_prompt_for_reasoning"]
        if hasattr(self, 'decision_tree'):
            for node_id in self.decision_tree.nodes:
                for attr in deprecated_attrs:
                    if attr in self.decision_tree.nodes[node_id]:
                        del self.decision_tree.nodes[node_id][attr]

    def set_branch_factor(self, branch_factor) :
        self.branch_factor = branch_factor
    
    def set_max_depth(self, max_depth) :
        self.max_depth = max_depth
    
    def get_branch_factor(self) :
        """ Get the branching factor for the LLM Response Tree. """
        if hasattr(self, 'branch_factor'):
            return self.branch_factor
        # backward compatibility
        return None
    
    def get_max_depth(self) :
        """ Get the maximum depth for the LLM Response Tree. """
        if hasattr(self, 'max_depth'):
            return self.max_depth
        # backward compatibility
        return None

    @classmethod
    def get_dummy_message(cls) :
        return "dummy response added"

    def is_dummy_node(self, node_name, use_lock=True, rw_lock:(RWLock|None)=None) :
        llm_response = self.get_node_attr(node_name, "llm_response", use_lock=use_lock, rw_lock=rw_lock)
        return llm_response == LLMResponseHistory.get_dummy_message()

    @classmethod
    def get_unique_id(cls, model_id, bug_id) :
        return model_id + "__" + bug_id

    @classmethod
    def get_node_attributes(cls) :
        """ List of all attributes. """
        node_list = ["parent_git_diff",
                    "llm_response",
                    "valid_edits",
                    "final_git_diff",
                    "job_id",
                    "job_status",
                    "crash_report",
                    "crash_title",
                    "bug_resolved",
                    "node_error",
                    "llm_attempts",
                    "job_attempts",
                    "kprebuilder_attempts",
                    "llm_interaction",
                    "depth",
                    "llm_reason",
                    "response_history",
                    "passed_validation",
                    "kernel_execution_prompt",
                    "execution_collection_log",
                    "conversation_history",
                    "node_review_insights"]
        return node_list

    #################################################
    # node related functions
    def add_node(self, node_id:(str|None)=None, use_lock=True, rw_lock:(RWLock|None)=None) :
        
        if use_lock :
            assert(rw_lock is not None)
            with rw_lock.w_locked() :
                # aquired the lock
                # all subsequent calls should not lock again to avoid deadlock
                use_lock = False
                
                if node_id is None :
                    # create the new node id
                    node_id = self.get_next_node_name(use_lock=use_lock, rw_lock=rw_lock)

                assert(not self.decision_tree.has_node(node_id))
                self.decision_tree.add_node(node_id)

                for attr_name in LLMResponseHistory.get_node_attributes() :
                    self.add_node_attr(node_id, attr_name, None, use_lock=use_lock, rw_lock=rw_lock)

                self.max_index += 1
                return node_id
        
        else:
            # caller already has the lock
            # all subsequent calls should not lock again to avoid deadlock
            use_lock = False
            
            if node_id is None :
                # create the new node id
                node_id = self.get_next_node_name(use_lock=use_lock, rw_lock=rw_lock)

            assert(not self.decision_tree.has_node(node_id))
            self.decision_tree.add_node(node_id)

            for attr_name in LLMResponseHistory.get_node_attributes() :
                self.add_node_attr(node_id, attr_name, None, use_lock=use_lock, rw_lock=rw_lock)

            self.max_index += 1
            return node_id

    def add_node_attr(self, node_id, attr_name, attr_value, use_lock=True, rw_lock:(RWLock|None)=None) :
        assert(attr_name in LLMResponseHistory.get_node_attributes()), "Unknown attribute name {}".format(attr_name)
        if use_lock :
            assert(rw_lock is not None)
            with rw_lock.w_locked() :
                # acquire the lock
                self.decision_tree.nodes[node_id][attr_name] = attr_value
        else :
            # lock is already held by the caller
            self.decision_tree.nodes[node_id][attr_name] = attr_value

    def get_node_attr(self, node_id, attr_name, use_lock=True, rw_lock:(RWLock|None)=None) :
        if use_lock :
            assert(rw_lock is not None)
            with rw_lock.r_locked() :
                # acquire the lock
                return self.decision_tree.nodes[node_id].get(attr_name)
        else :
            # lock is already held by the caller
            return self.decision_tree.nodes[node_id].get(attr_name)

    def get_next_node_name(self, use_lock=True, rw_lock:(RWLock|None)=None) :
        if use_lock :
            assert(rw_lock is not None)
            with rw_lock.r_locked() :
                # acquire the lock
                return str(self.max_index)
        else :
            # lock is already held by the caller
            return str(self.max_index)
    
    def get_llm_generation(self, node_name, use_lock=True, rw_lock:(RWLock|None)=None) :
        return self.get_node_attr(node_name, "llm_response", use_lock=use_lock, rw_lock=rw_lock)

    def get_parent_of_node(self, node_name, use_lock=True, rw_lock:(RWLock|None)=None) :
        if use_lock :
            # acquire the lock
            assert(rw_lock is not None)
            with rw_lock.r_locked() :
                parents = self.decision_tree.in_edges(node_name)
                if len(parents)==0:
                    return None
                else :
                    assert(len(parents)==1)
                    for par in parents :
                        parent_node = par[0]
                        break
                    return parent_node
        else:
            # lock is already held by the caller
            parents = self.decision_tree.in_edges(node_name)
            if len(parents)==0:
                return None
            else :
                assert(len(parents)==1)
                for par in parents :
                    parent_node = par[0]
                    break
                return parent_node

    def get_children_of_node(self, node_name, use_lock=True, rw_lock:(RWLock|None)=None) :
        if use_lock : 
            # acquire the lock
            assert(rw_lock is not None)
            with rw_lock.r_locked() :
                out_edges = self.decision_tree.out_edges(node_name)
                if len(out_edges) == 0 :
                    return []
                else :
                    child_list = []
                    for edge in out_edges :
                        child_list.append(edge[1])
                    return child_list
        else :
            # lock is already held by the caller
            out_edges = self.decision_tree.out_edges(node_name)
            if len(out_edges) == 0 :
                return []
            else :
                child_list = []
                for edge in out_edges :
                    child_list.append(edge[1])
                return child_list

    def get_parent_git_diff(self, node_name, use_lock=True, rw_lock:(RWLock|None)=None) :
        return self.get_node_attr(node_name, "parent_git_diff", use_lock=use_lock, rw_lock=rw_lock)

    def get_final_git_diff(self,node_name, use_lock=True, rw_lock:(RWLock|None)=None):
        return self.get_node_attr(node_name, "final_git_diff", use_lock=use_lock, rw_lock=rw_lock)

    def move_to_parent_node(self, node_name, use_lock=True, rw_lock:(RWLock|None)=None) :
        """ Modify the current node to the parent of 'node_name'. """
        if use_lock :
            assert(rw_lock is not None)
            with rw_lock.w_acquire() :
                # acquired the lock
                # all subsequent calls should not use the lock
                parent_name = self.get_parent_of_node(node_name=node_name, use_lock=False, rw_lock=rw_lock)
                self.update_curr_node(parent_name, use_lock=False, rw_lock=rw_lock)
                return parent_name
        else :
            # lock is already held by the caller
            # all subsequent calls should not use the lock
            parent_name = self.get_parent_of_node(node_name=node_name, use_lock=False, rw_lock=rw_lock)
            self.update_curr_node(parent_name, use_lock=False, rw_lock=rw_lock)
            return parent_name

    def move_to_child_node(self, child_node, node_name, use_lock=True, rw_lock:(RWLock|None)=None) :
        """ modify the current node to the child of the current node """
        if use_lock :
            assert(rw_lock is not None)
            with rw_lock.w_acquire() :
                # acquired the lock
                # all subsequent calls should not use the lock
                assert(self.decision_tree.has_edge(node_name,child_node))
                self.update_curr_node(child_node, use_lock=False, rw_lock=rw_lock)
                return child_node
        else :
            # lock is already held by the caller
            # all subsequent calls should not use the lock
            assert(self.decision_tree.has_edge(node_name,child_node))
            self.update_curr_node(child_node, use_lock=False, rw_lock=rw_lock)
            return child_node

    def add_llm_response(self, llm_response:str, parent_node, use_lock=True, rw_lock:(RWLock|None)=None) :
        """ Add a new node with the llm response. """
        if use_lock :
            assert(rw_lock is not None)
            with rw_lock.w_locked() :
                # acquired the lock
                # all subsequent calls should not use the lock
                use_lock = False
                new_node_id = self.add_node(use_lock=use_lock, rw_lock=rw_lock)
                self.add_node_attr(new_node_id, "llm_response", llm_response, use_lock=use_lock, rw_lock=rw_lock)
                self.add_node_attr(new_node_id, "parent_git_diff", 
                                    self.get_node_attr(parent_node,"final_git_diff",use_lock=use_lock,rw_lock=rw_lock),
                                    use_lock=use_lock, rw_lock=rw_lock)
                self.add_edge(parent_node, new_node_id, use_lock=use_lock, rw_lock=rw_lock)
                self.inherit_conversation_history(parent_node, new_node_id, use_lock=use_lock, rw_lock=rw_lock)

                # current node is the newest added node
                self.update_curr_node(new_node_id,use_lock=use_lock,rw_lock=rw_lock)
                return new_node_id

        else :
            # lock is already held by the caller
            # all subsequent calls should not use the lock
            use_lock = False
            new_node_id = self.add_node(use_lock=use_lock, rw_lock=rw_lock)
            self.add_node_attr(new_node_id, "llm_response", llm_response, use_lock=use_lock, rw_lock=rw_lock)
            self.add_node_attr(new_node_id, "parent_git_diff",
                            self.get_node_attr(parent_node,"final_git_diff",use_lock=use_lock, rw_lock=rw_lock),
                            use_lock=use_lock, rw_lock=rw_lock)
            self.add_edge(parent_node, new_node_id, use_lock=use_lock, rw_lock=rw_lock)
            self.inherit_conversation_history(parent_node, new_node_id, use_lock=use_lock, rw_lock=rw_lock)

            # current node is the newest added node
            self.update_curr_node(new_node_id,use_lock=use_lock,rw_lock=rw_lock)
            return new_node_id 
        
    def add_llm_node_error(self, node_name, error:LLMHistoryNodeErrors, use_lock=True, rw_lock:(RWLock|None)=None) :
        """Add special errors to LLM nodes """
        self.add_node_attr(node_id=node_name, attr_name="node_error", attr_value=error, use_lock=use_lock, rw_lock=rw_lock)

    def update_llm_attempts(self, node_name, new_attempts, use_lock=True, rw_lock:(RWLock|None)=None) :

        def base_function(node_name, new_attempts) :
            assert rw_lock is not None and rw_lock.is_w_locked(), "update_llm_attempts.base_function() requires write lock to be held"
            old_num_attempts = self.get_node_attr(node_name, "llm_attempts", use_lock=False)
            if old_num_attempts :
                new_attempts += old_num_attempts                
            self.add_node_attr(node_name, "llm_attempts", new_attempts, use_lock=False)

        if use_lock :
            assert(rw_lock is not None)
            with rw_lock.w_locked() :
                return base_function(node_name, new_attempts)
        else :
            return base_function(node_name, new_attempts)

    def update_reason_and_solutions(self, node_name, updated_element,  use_lock=True, rw_lock:(RWLock|None)=None) :
        """ Add the new key,value pairs to the last entry in "response_history" for the node "node_name" """

        def base_function(node_name, updated_element) :
            assert rw_lock is not None and rw_lock.is_w_locked(), "update_reason_and_solutions.base_function() requires write lock to be held"
            response_history = self.get_node_attr(node_name, "response_history", False)
            if (response_history is None) or (len(response_history)==0) :
                # there is an issue with the code. this function should not be called
                # if response_history is not populated.
                return
            else :
                last_entry = response_history[-1]
                for key, value in updated_element.items() :
                    if last_entry.get(key) is None :
                        last_entry[key] = value

        if use_lock :
            # use the lock
            assert(rw_lock is not None)
            with rw_lock.w_locked() :
                # hold the lock
                base_function(node_name, updated_element)
        else :
            # caller already holds the lock
            base_function(node_name, updated_element)

    def add_reason_and_solutions(self, node_name, element, use_lock=True, rw_lock:(RWLock|None)=None) :
        """ Add a new pair of (reasons, solutions) to the 'response history'. """

        def base_function(node_name, element) :
            assert rw_lock is not None and rw_lock.is_w_locked(), "add_reason_and_solutions.base_function() requires write lock to be held"
            response_history = self.get_node_attr(node_name, "response_history", False)
            if response_history is None :
                response_history = []
                self.add_node_attr(node_name,"response_history", response_history, False)
            response_history.append(element)

        if use_lock :
            # use the lock
            assert(rw_lock is not None)
            with rw_lock.w_locked() :
                # hold the lock
                base_function(node_name, element)
        else :
            # caller already holds the lock
            base_function(node_name, element)

    #################################################
    # edge related functions
    def add_edge(self, src, dst, use_lock=True, rw_lock:(RWLock|None)=None) :

        assert(self.decision_tree.has_node(src))
        assert(self.decision_tree.has_node(dst))
        assert(not self.decision_tree.has_edge(src,dst))

        if use_lock :
            assert(rw_lock is not None)
            with rw_lock.w_locked() :
                # acquired the lock
                # all subsequent calls should not use the lock
                use_lock = False
                self.decision_tree.add_edge(src,dst)
                depth = self.get_node_attr(src, "depth", use_lock=use_lock, rw_lock=rw_lock)
                self.add_node_attr(dst, "depth", depth+1, use_lock=use_lock, rw_lock=rw_lock)
        else :
            # lock is already held by the caller
            # all subsequent calls should not use the lock
            use_lock=False
            self.decision_tree.add_edge(src,dst)
            depth = self.get_node_attr(src, "depth", use_lock=use_lock, rw_lock=rw_lock)
            self.add_node_attr(dst, "depth", depth+1, use_lock=use_lock, rw_lock=rw_lock)

    #################################################
    # functions specific to the "current latest" node

    def add_curr_node_attr(self, attr_name, attr_value, use_lock=True, rw_lock:(RWLock|None)=None) :
        curr_node_name = self.get_curr_node(use_lock=use_lock,rw_lock=rw_lock)
        self.add_node_attr(curr_node_name, attr_name, attr_value, use_lock=use_lock, rw_lock=rw_lock)

    def update_curr_node(self, node_name, use_lock=True, rw_lock:(RWLock|None)=None) :
        if use_lock :
            assert(rw_lock is not None)
            with rw_lock.w_locked() :
                # acquired the lock
                assert(self.decision_tree.has_node(node_name))
                self.curr_node_name = node_name
        else :
            # lock is already held by the caller
            assert(self.decision_tree.has_node(node_name))
            self.curr_node_name = node_name

    def get_curr_node(self, use_lock=True, rw_lock:(RWLock|None)=None) :
        """ Get the most recent node. """
        if use_lock :
            assert(rw_lock is not None)
            with rw_lock.r_locked() :
                # acquired the lock
                return self.curr_node_name
        else :
            # lock is already held by the caller
            return self.curr_node_name

    def get_current_llm_generation(self, use_lock=True, rw_lock:(RWLock|None)=None) :
        curr_node_name = self.get_curr_node(use_lock=use_lock, rw_lock=rw_lock)
        return self.get_node_attr(curr_node_name, "llm_response", use_lock=use_lock, rw_lock=rw_lock)

    def get_parent_of_curr_node(self, use_lock=True, rw_lock:(RWLock|None)=None) :
        curr_node_name = self.get_curr_node(use_lock=use_lock, rw_lock=rw_lock)
        return self.get_parent_of_node(curr_node_name, use_lock=use_lock, rw_lock=rw_lock)
    
    def get_current_parent_git_diff(self, use_lock=True, rw_lock:(RWLock|None)=None) :
        curr_node_name = self.get_curr_node(use_lock=use_lock, rw_lock=rw_lock)
        return self.get_parent_git_diff(curr_node_name, rw_lock=rw_lock)
    
    def get_current_final_git_diff(self, use_lock=True, rw_lock:(RWLock|None)=None):
        curr_node_name = self.get_curr_node(use_lock=use_lock, rw_lock=rw_lock)
        return self.get_final_git_diff(curr_node_name, rw_lock=rw_lock)

    ##################### other utility functions ########################

    def get_history_of_edits(self, node_name) :
        """ Go through the parents of 'node_name' and collects all the edits made to the repo until now. """
        all_edits = []
        parent_node = self.get_parent_of_node(node_name, use_lock=False, rw_lock=None)
        while parent_node is not None :
            parent_edit = self.get_node_attr(parent_node, "valid_edits", use_lock=False, rw_lock=None)
            if (parent_edit is not None) and len(parent_edit)>0:
                all_edits.append(parent_edit)
            parent_node = self.get_parent_of_node(parent_node, use_lock=False, rw_lock=None)

        if len(all_edits)==0 :
            # first node in the chain
            return None

        final_history = "===== Edit History Start =====\n"
        for edit_num, parent_edit_list in enumerate(all_edits) :
            save_str = ""
            for edit in parent_edit_list :
                if save_str == "" : 
                    save_str = f"\n--------\n{edit.generate_structured_format(edit_type='2')}"
                else :
                    save_str = f"{save_str}\n\n--------\n{edit.generate_structured_format(edit_type='2')}"
            final_history = f"{final_history}\n**Edit Number {edit_num+1}**\n{save_str}\n"
        final_history = f"{final_history}\n===== Edit History End ====="

        print(final_history)

        return final_history

    def print_tree(self, use_lock=True, rw_lock:(RWLock|None)=None) :
        """ Print the entire tree. """
        ans = []
        queue = deque()
        queue.append(("0",self.get_node_attr("0","depth",use_lock=use_lock,rw_lock=rw_lock)))
        
        while len(queue) :

            node = queue[0][0]
            curr_depth = queue[0][1]

            if node != "0" :
                ans.append(queue[0])

            queue.popleft()

            child_list = self.get_children_of_node(node, use_lock=use_lock, rw_lock=rw_lock)
            modified_child_list = [(ele,self.get_node_attr(ele,"depth",use_lock,rw_lock)) for ele in child_list]
            full_string = "({}, D={}) -->".format(node,curr_depth)
            for edge in modified_child_list :
                full_string += " ({}, D={})".format(edge[0], edge[1])
            print(full_string)

            queue.extend(modified_child_list)
        
        print("\n\n==============")

    def save_file_content(self, node_name, file_content, file_name, use_lock=True, rw_lock:(RWLock|None)=None, file_type:str="txt", metadata:dict=None) :
        """ Save file content. If metadata provided and file_type is json, wraps content with metadata. """

        def base_function(node_name, file_content, file_name, file_type, metadata) :
            assert rw_lock is not None and rw_lock.is_w_locked(), "save_file_content.base_function() requires write lock to be held"

            path_1 = pjoin(self.save_dir, LLMResponseHistory.get_unique_id(self.model_id, self.bug_id))
            path_2 = pjoin(path_1, node_name)
            path_3 = pjoin(path_2, file_name)

            if not os.path.exists(path_1) :
                os.mkdir(path_1)
            if not os.path.exists(path_2) :
                os.mkdir(path_2)

            if file_type == "txt" :
                with open(path_3,'w') as f:
                    f.write(file_content)
            elif file_type == "json" :
                # Wrap with metadata if provided
                output = {"metadata": metadata, "messages": file_content} if metadata else file_content
                with open(path_3,'w') as f:
                    json.dump(output,f,indent=4)

        if use_lock :
            assert(rw_lock is not None)
            with rw_lock.w_locked() :
                base_function(node_name, file_content, file_name, file_type, metadata)
        else :
            base_function(node_name, file_content, file_name, file_type, metadata)

    def load_file_content(self, node_name: str, file_name: str) -> dict | None:
        """Load JSON file content for a node."""
        file_path = pjoin(self.save_dir, LLMResponseHistory.get_unique_id(self.model_id, self.bug_id), node_name, file_name)
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                return json.load(f)
        return None

    def save_interactions(self, use_lock=True, rw_lock:(RWLock|None)=None):
        """ Save the interactions with the LLM for the kernel bug. """
        if use_lock :
            assert(rw_lock is not None)
            with rw_lock.w_locked() : 
                # acquire the lock
                with open(pjoin(self.save_dir,LLMResponseHistory.get_unique_id(self.model_id, self.bug_id)+".pkl"),'wb') as file:
                    # Use pickle to dump the object to the file
                    copy_obj = deepcopy(self)
                    copy_obj.saved_history = True
                    pickle.dump(copy_obj,file)
        else :
            # caller already has the lock
            with open(pjoin(self.save_dir,LLMResponseHistory.get_unique_id(self.model_id, self.bug_id)+".pkl"),'wb') as file:
                # Use pickle to dump the object to the file
                copy_obj = deepcopy(self)
                copy_obj.saved_history = True
                pickle.dump(copy_obj,file)
    
    # latest_node, dst_folder, crash_file_path, rw_lock=self.response_history_lock
    def add_job_compilation_error(self, node_name, crash_file_path, rw_lock:(RWLock|None)=None) :
        """ If a job is aborted due to a compilation error, save the compilation error in the tree. """ 
        with rw_lock.w_locked() :
            assert(exists(crash_file_path)), "{} : crash file does not exist.".format(crash_file_path)
            with open(crash_file_path,"r") as f :
                crash_file = f.read()
            # Update LLM interactions
            llm_interactions = self.get_node_attr(node_name,"llm_interaction",use_lock=False)
            last_kgym_attempt = llm_interactions[-1]
            last_llm_output = last_kgym_attempt[-1]
            last_llm_output["compilation_error"] = crash_file
            # Update the node attribute
            self.add_node_attr(node_name,"llm_interaction",llm_interactions,use_lock=False)
            return crash_file

    def add_job_results(self, node_name, dst_folder, job_id, job_results:ResponseExtracted, use_lock:bool=True , rw_lock:(RWLock|None)=None) :
        """ Adds the results of the job as node attributes. """ 
        def base_function(node_name, dst_folder, job_id, job_results, use_lock, rw_lock) :
            assert rw_lock is not None and rw_lock.is_w_locked(), "add_job_results.base_function() requires write lock to be held"
            # add job id
            self.add_node_attr(node_name, "job_id", job_id, use_lock=use_lock, rw_lock=rw_lock)

            status = job_results.get_status()
            special_status = job_results.get_special_status()
            crash_description = job_results.get_crash_description()

            if special_status == SpecialConditions.NORMAL_EXECUTION and status == JobStatus.FINISHED :
                collect_logs = True
            else :
                collect_logs = False

            self.add_node_attr(node_name, "job_status", status, use_lock=use_lock, rw_lock=rw_lock)

            # collect the logs
            file_content = None
            if collect_logs :
                all_files = os.listdir(dst_folder)
                for file in all_files :

                    # Old code                    
                    # if "report0" in file :
                    #     file_path = pjoin(dst_folder, file)
                    #     with open(file_path,"r") as f :
                    #         file_content = f.read()
                    #     break
                    
                    # added a special 'kgym_chosen' tag.
                    # the crash title in kgym corresponds to the 'kgym_chosen_report0'
                    if "kgym_chosen_report0" in file :
                        file_path = pjoin(dst_folder, file)
                        with open(file_path,"r") as f :
                            file_content = f.read()
                        break

                assert(file_content is not None)
                self.add_node_attr(node_name, "crash_report", file_content, use_lock=use_lock, rw_lock=rw_lock)
                self.add_node_attr(node_name, "crash_title", crash_description, use_lock=use_lock, rw_lock=rw_lock)
            else :
                if special_status == SpecialConditions.MESSAGE_NO_CRASH and status == JobStatus.FINISHED :
                    # bug has been solved
                    self.add_node_attr(node_name, "bug_resolved", True, use_lock=use_lock, rw_lock=rw_lock)
                    self.stop_exploration = True

        if use_lock :
            # acquire the lock
            assert(rw_lock is not None), "rw_lock must not be None, when use_lock is True"
            with rw_lock.w_locked() :
                base_function(node_name, dst_folder, job_id, job_results, use_lock=False, rw_lock=None)
        else :
            # the caller already owns the lock
            base_function(node_name, dst_folder, job_id, job_results, use_lock=False, rw_lock=None)

    def get_crash_report(self, node_name) :
        """ Get crash report """
        report = self.get_node_attr(node_name, "crash_report")
        title = self.get_node_attr(node_name, "crash_title")
        crash_report = "<issue>\n" + title + "\n\n" + report + "\n</issue>"
        return crash_report

    def collect_all_nodes(self, use_lock=True, rw_lock:(RWLock|None)=None, get_graph_edges_in_string:bool=False) :
        """ Do a breadth first traversal and then collect all the node names 
            and depths at each level in the tree. """
        ans = []
        graph_in_string_format = []
        queue = deque()
        queue.append(("0",-1))
        
        while len(queue) :

            node = queue[0][0]
            curr_depth = queue[0][1]

            # AIDEV-NOTE: Include node "0" in analysis to capture root node errors
            ans.append(queue[0])

            queue.popleft()

            child_list = self.get_children_of_node(node, use_lock=use_lock, rw_lock=rw_lock)
            modified_child_list = [(ele,curr_depth+1) for ele in child_list]
            queue.extend(modified_child_list)

            tgt_list = ""
            for child in child_list :
                if tgt_list == "" :
                    tgt_list = str(child)
                else :
                    tgt_list += "," + str(child)

            graph_in_string_format.append(f"{node} --> {tgt_list}")

        if get_graph_edges_in_string :
            return ans, graph_in_string_format
        else :
            return ans

    def collect_solved_nodes(self) : 
        """ Collect the nodes of the tree where 'bug_resolved' is True. """
        use_lock = False
        rw_lock = None
        ans = []
        all_nodes = self.collect_all_nodes(use_lock=use_lock, rw_lock=rw_lock)
        for (node_id, _) in all_nodes :
            bug_solved = self.get_node_attr(node_id, "bug_resolved", use_lock, rw_lock)
            if bug_solved is not None :
                if bug_solved : # HACK - REMOVE !!
                    ans.append(node_id)
                # # HACK - REMOVE !!
                # self.add_node_attr(node_id, "bug_resolved", True, use_lock, rw_lock)
                # self.add_node_attr(node_id, "passed_validation", None, use_lock, rw_lock) # null the passed_validation temporarily
        
        # # save the validation results
        # self.save_interactions(use_lock, rw_lock)
        return ans

    def collecting_remaining_nodes(self, branch_factor, rw_lock:(RWLock|None)=None) :
        """ Collect all the nodes that have num_children < branch factor. 
            "Remaining" nodes follow the below criteria.

            Pattern 1. 
                node_error==None; node_depth<max depth; num_children<branch_factor and bug was not solved

            Pattern 2.
                node_error==None; job_status!=JobStatus.FINISHED; node_depth==max depth
        """
        use_lock = False
        rw_lock = None
        ans = []
        all_nodes = self.collect_all_nodes(use_lock=use_lock, rw_lock=rw_lock)
        for (node_id, node_depth) in all_nodes :
            node_error = self.get_node_attr(node_id,"node_error", use_lock, rw_lock)
            num_children = len(self.get_children_of_node(node_id, use_lock, rw_lock))
            job_status = self.get_node_attr(node_id,"job_status",use_lock, rw_lock)
            bug_solved = self.get_node_attr(node_id, "bug_resolved", use_lock, rw_lock)
            # pattern 1
            if (node_error is None) and (node_depth<self.max_depth) \
                and (num_children < branch_factor) and (not bug_solved):
                message = "Node Error {}, Node Depth {}, Num Children {}, Bug Solved {}".format(node_error, node_depth, num_children, bug_solved)
                ans.append((node_id, message))
                continue
            # pattern 2
            if (node_error is None) and (job_status != JobStatus.FINISHED) and (node_depth==self.max_depth) :
                message = "Node Error {}, Node Depth {}, Job Status {}".format(node_error, node_depth, job_status)
                ans.append((node_id, message))
                continue
        return ans
    
    def walk_through_tree_path(self, tree_path) :
        """ For a given tree_path, walk down the tree_path printing 
            the git diffs and the git edits. """
        prev_node = None
        for next_node in tree_path :
            print("\n==========================")
            print("Node ID : {}".format(next_node))
            assert(self.decision_tree.has_node(next_node))
            if prev_node is not None :
                assert(self.decision_tree.has_edge(prev_node,next_node))
            edit_list = self.get_node_attr(next_node,"valid_edits",use_lock=False)
            ############# Print Edits #############
            for index, edit in enumerate(edit_list) :
                print("Edit No {}".format(index+1))
                print("Before Edit : {}".format(edit.before))
                print("After Edit : {}".format(edit.after))
            #######################################
            parent_git_diff = self.get_node_attr(next_node,"parent_git_diff", use_lock=False)
            final_git_diff = self.get_node_attr(next_node,"final_git_diff", use_lock=False)
            print(final_git_diff)
            print("==========================\n")
            if final_git_diff == parent_git_diff :
                assert(False), "Final git diff and Parent git diff are identical"
            prev_node = next_node

    def save_parent_and_final_diff(self, node, dst_folder, use_lock, rw_lock) :
        """ Save the parent and final diff of 'node' in 'dst_folder'. """
        # write parent patch
        parent_git_diff = self.get_node_attr(node,"parent_git_diff", use_lock, rw_lock)
        with open(pjoin(dst_folder,"parent.patch"), "w") as f :
            if parent_git_diff is None :
                f.write("")
            else :
                f.write(parent_git_diff)
        # write final patch
        final_git_diff = self.get_node_attr(node,"final_git_diff", use_lock, rw_lock)
        with open(pjoin(dst_folder,"final.patch"),"w") as f :
            if final_git_diff is None :
                f.write("")
            else :
                f.write(final_git_diff)

    def walk_through_entire_tree(self) :
        """ Walk through the entire tree and collect stats. """
        min_job_val = 1000000
        min_job_id = None

        max_job_val = -1
        max_job_id = None

        num_nodes = 0
        node_status_dict = {
            "aborted" : 0,
            "crashed" : 0,
            "solved" : 0
        }

        queue = deque()
        queue.append("0")
        while len(queue) :
            node = queue[0]
            queue.popleft()
            child_list = self.get_children_of_node(node, use_lock=False)            
            num_nodes += len(child_list)

            for child in child_list :
                job_id = self.get_node_attr(child, "job_id", use_lock=False)
                crash_report = self.get_node_attr(child, "crash_report", use_lock=False)
                job_status = self.get_node_attr(child, "job_status", use_lock=False)
                bug_solved = self.get_node_attr(child, "bug_resolved", use_lock=False)

                if crash_report is not None :
                    node_status_dict["crashed"] += 1
                elif bug_solved :
                    node_status_dict["solved"] += 1
                elif job_status != JobStatus.FINISHED :
                    node_status_dict["aborted"] += 1

                job_value = int(job_id, 16)

                if min_job_val != min(job_value, min_job_val) :
                    min_job_val = min(job_value, min_job_val)
                    min_job_id = job_id
                
                if max_job_val != max(job_value, max_job_val) :
                    max_job_val = max(job_value, max_job_val)
                    max_job_id = job_id

            queue.extend(child_list)

        num_jobs = max_job_val - min_job_val + 1
        print("Num Jobs : {}".format(num_jobs))
        print("First Job : {}, Last Job : {}".format(min_job_id, max_job_id))
        print(node_status_dict)

        return

    def add_interactions_with_LLM(self,
                                node_name,
                                llm_answer_info,
                                use_lock=True,
                                rw_lock:(RWLock|None)=None) :
        """ Add interactions with LLM. """
        if use_lock :
            # acquire the lock
            assert(rw_lock is not None)
            with rw_lock.w_locked() :
                self.add_node_attr(node_id=node_name, attr_name="llm_interaction", attr_value=llm_answer_info, use_lock=False, rw_lock=rw_lock)
        else :
            # caller already holds the lock
            self.add_node_attr(node_id=node_name, attr_name="llm_interaction", attr_value=llm_answer_info, use_lock=use_lock)

    #################################################
    # Conversation History Methods
    # AIDEV-NOTE: Per-node conversation history for tracking tool invocations
    # in Anthropic API format. Enables Claude to have full context when queried.

    def get_or_create_conversation_history(
        self,
        node_name: str,
        use_lock: bool = True,
        rw_lock: Optional[RWLock] = None
    ) -> ConversationHistory:
        """
        Get existing conversation history for a node or create a new one.

        Args:
            node_name: Name of the node
            use_lock: Whether to use locking
            rw_lock: Reader-writer lock instance

        Returns:
            ConversationHistory instance for the node
        """
        def base_function() -> ConversationHistory:
            assert rw_lock is not None and rw_lock.is_w_locked(), "get_or_create_conversation_history.base_function() requires write lock to be held"
            existing = self.get_node_attr(node_name, "conversation_history", use_lock=False)
            if existing is None:
                history = ConversationHistory()
                self.add_node_attr(node_name, "conversation_history", history, use_lock=False)
                return history
            return existing

        if use_lock:
            assert rw_lock is not None
            with rw_lock.w_locked():
                return base_function()
        else:
            return base_function()

    def inherit_conversation_history(
        self,
        parent_name: str,
        child_name: str,
        use_lock: bool = True,
        rw_lock: Optional[RWLock] = None
    ) -> None:
        """
        Copy parent's conversation history to child node.

        Called when creating child nodes so they inherit the full
        conversation context from root to their parent.

        Args:
            parent_name: Name of the parent node
            child_name: Name of the child node
            use_lock: Whether to use locking
            rw_lock: Reader-writer lock instance
        """
        def base_function() -> None:
            assert rw_lock is not None and rw_lock.is_w_locked(), "inherit_conversation_history.base_function() requires write lock to be held"
            parent_history = self.get_node_attr(parent_name, "conversation_history", use_lock=False)
            if parent_history is not None:
                # Deep copy parent's history to child
                child_history = parent_history.copy()
                self.add_node_attr(child_name, "conversation_history", child_history, use_lock=False)
            else:
                # Parent has no history, create empty one for child
                self.add_node_attr(child_name, "conversation_history", ConversationHistory(), use_lock=False)

        if use_lock:
            assert rw_lock is not None
            with rw_lock.w_locked():
                base_function()
        else:
            base_function()

    def add_tool_invocation_to_history(
        self,
        node_name: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_result: str,
        reasoning: Optional[str] = None,
        is_error: bool = False,
        use_lock: bool = True,
        rw_lock: Optional[RWLock] = None,
        tool_use_id: Optional[str] = None,
    ) -> None:
        """
        Add a tool invocation to a node's conversation history.

        Args:
            node_name: Name of the node
            tool_name: Name of the tool (e.g., "run_kernel", "generate_hypothesis")
            tool_input: Serialized input parameters
            tool_result: Serialized result string
            reasoning: Optional reasoning text to include
            is_error: Whether the result represents an error
            use_lock: Whether to use locking
            rw_lock: Reader-writer lock instance
            tool_use_id: Optional pre-generated ID (for linking to llm_input files)
        """
        def base_function() -> None:
            assert rw_lock is not None and rw_lock.is_w_locked(), "add_tool_invocation_to_history.base_function() requires write lock to be held"
            history = self.get_or_create_conversation_history(node_name, use_lock=False)
            prev_len = len(history.to_list())
            history.add_tool_invocation(
                tool_name=tool_name,
                tool_input=tool_input,
                tool_result=tool_result,
                reasoning=reasoning,
                is_error=is_error,
                node_name=node_name,
                tool_use_id=tool_use_id,
            )
            # Persist conversation to disk after each tool update
            self.save_file_content(
                node_name=node_name,
                file_content=history.to_list(),
                file_name="conversation_history.json",
                use_lock=False,  # Already inside lock
                file_type="json",
            )

            # Save to global conversation history
            bug_folder = pjoin(self.save_dir, LLMResponseHistory.get_unique_id(self.model_id, self.bug_id))
            global_conv_path = pjoin(bug_folder, "conversation_history_global.json")
            global_entries = []
            if os.path.exists(global_conv_path):
                with open(global_conv_path, 'r') as f:
                    global_entries = json.load(f)

            # Get all messages added since prev_len (handles multi-round sub-tools)
            new_messages = history.to_list()[prev_len:]
            for msg in new_messages:
                global_entry = {"node": node_name, **msg}
                global_entries.append(global_entry)

            with open(global_conv_path, 'w') as f:
                json.dump(global_entries, f, indent=4)

        if use_lock:
            assert rw_lock is not None
            with rw_lock.w_locked():
                base_function()
        else:
            base_function()

    def get_conversation_history(
        self,
        node_name: str,
        use_lock: bool = True,
        rw_lock: Optional[RWLock] = None
    ) -> List[Dict[str, Any]]:
        """
        Get conversation history for a node in Anthropic API format.

        Args:
            node_name: Name of the node
            use_lock: Whether to use locking
            rw_lock: Reader-writer lock instance

        Returns:
            List of message dicts in Anthropic API format
        """
        def base_function() -> List[Dict[str, Any]]:
            assert rw_lock is not None and rw_lock.is_w_locked(), "get_conversation_history.base_function() requires write lock to be held"
            history = self.get_node_attr(node_name, "conversation_history", use_lock=False)
            if history is None:
                return []
            return history.to_list()

        if use_lock:
            assert rw_lock is not None
            with rw_lock.r_locked():
                return base_function()
        else:
            return base_function()

    def get_global_conversation_history(
        self,
        use_lock: bool = True,
        rw_lock: Optional[RWLock] = None
    ) -> List[Dict[str, Any]]:
        """Get global conversation history with 'node' attribute on each message."""
        def base_function() -> List[Dict[str, Any]]:
            assert rw_lock is not None and rw_lock.is_w_locked(), "get_global_conversation_history.base_function() requires write lock to be held"
            bug_folder = pjoin(self.save_dir, LLMResponseHistory.get_unique_id(self.model_id, self.bug_id))
            global_conv_path = pjoin(bug_folder, "conversation_history_global.json")
            if os.path.exists(global_conv_path):
                with open(global_conv_path, 'r') as f:
                    return json.load(f)
            return []

        if use_lock:
            assert rw_lock is not None
            with rw_lock.r_locked():
                return base_function()
        else:
            return base_function()

if __name__ == '__main__' :
    
    from Kernel_Agent.models.model_zoo_id import ModelID
    import pickle
    model_id = ModelID.GEMINI_15_PRO
    save_dir = pjoin(os.getenv("KAGENT_PATH"),"llm-response-dir")

    all_files = os.listdir(save_dir)
    all_pickle_files = [ele for ele in all_files if (".pkl" in ele)]
    for pickle_file in all_pickle_files :
        save_path = pjoin(save_dir,pickle_file)
        # an LLM Response history already exists, load this history
        with open(save_path,"rb") as f :
            response_history:LLMResponseHistory = pickle.load(f)
        response_history.print_tree(use_lock=False,rw_lock=None)

    assert(False)

    bug_id = "a8e52aea23a08661ca01bb5346bb78d35df76b50"
    save_path = pjoin(save_dir,LLMResponseHistory.get_unique_id(model_id, bug_id)+".pkl")
    if os.path.exists(save_path) :
        # an LLM Response history already exists, load this history
        with open(save_path,"rb") as f :
            response_history:LLMResponseHistory = pickle.load(f)

    # response_history.walk_through_entire_tree()
    error_dict, dead_nodes = response_history.find_dead_ends(max_depth=3)
    # response_history.walk_through_tree_path(["1","2"])
    for key,val in error_dict.items() :
        print("Error Key {}, Value {}".format(key, val))
    print("Dead Nodes : {}".format(dead_nodes))
    print("Total Nodes : {}".format(response_history.max_index))
