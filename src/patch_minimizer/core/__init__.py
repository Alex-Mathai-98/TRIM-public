from patch_minimizer.core.edit import Edit, EditType, MatchMode
from patch_minimizer.core.node import Node
from patch_minimizer.core.rw_lock import RWLock, GitRWLock
from patch_minimizer.core.coupling_types import CouplingMap
from patch_minimizer.core.edit_applier import EditApplier

# AIDEV-NOTE: core/ is L0 — it must not import from any benchmark. BugData used to be
# re-exported here, which made every importer of any core submodule pull in
# benchmark_utils.kernel. Import it directly from
# patch_minimizer.benchmark_utils.kernel.bug_data instead.
