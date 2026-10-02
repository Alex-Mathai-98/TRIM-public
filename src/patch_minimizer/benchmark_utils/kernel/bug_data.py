"""
Bug data abstraction classes for Kernel Agent.

This module provides BugData and CrashData classes that abstract away
direct JSON dictionary access patterns, making schema changes easier to manage.

AIDEV-NOTE: This abstraction supports both legacy kebab-case field names
(e.g., "kernel-source-git") and new camelCase field names (e.g., "kernelSourceGit")
to maintain backward compatibility during migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Optional
import json


@dataclass
class CrashData:
    """
    Crash information from kernel bugs.

    Represents a single crash instance with kernel configuration,
    reproducer, and compiler information.
    """

    kernel_source_git: str
    kernel_source_commit: str
    architecture: str
    kernel_config_data: Optional[str] = None
    kernel_config: Optional[str] = None
    compiler_description: Optional[str] = None
    c_reproducer: Optional[str] = None
    syz_reproducer_data: Optional[str] = None  # Content of the syz reproducer program

    @classmethod
    def from_dict(cls, data: dict) -> CrashData:
        """
        Load CrashData from dictionary with kebab-case field support.

        Args:
            data: Dictionary containing crash data (supports both kebab-case and camelCase)

        Returns:
            CrashData instance

        Raises:
            ValueError: If required fields are missing
        """
        # AIDEV-NOTE: Support both naming conventions with fallback
        kernel_source_git = data.get("kernel-source-git") or data.get("kernelSourceGit")
        kernel_source_commit = data.get("kernel-source-commit") or data.get("kernelSourceCommit")
        architecture = data.get("architecture")

        if not kernel_source_git:
            raise ValueError("kernel-source-git or kernelSourceGit field is missing")
        if not kernel_source_commit:
            raise ValueError("kernel-source-commit or kernelSourceCommit field is missing")
        if not architecture:
            raise ValueError("architecture field is missing")

        return cls(
            kernel_source_git=kernel_source_git,
            kernel_source_commit=kernel_source_commit,
            architecture=architecture,
            kernel_config_data=data.get("kernel-config-data") or data.get("kernelConfigData"),
            kernel_config=data.get("kernel-config") or data.get("kernelConfig"),
            compiler_description=data.get("compiler-description") or data.get("compilerDescription"),
            c_reproducer=data.get("c-reproducer") or data.get("cReproducer"),
            syz_reproducer_data=data.get("syz-reproducer-data") or data.get("syzReproducerData")
        )


@dataclass
class BugData:
    """
    Main bug data abstraction for kernel bugs.

    This class provides a unified interface for accessing bug data,
    abstracting away JSON field names and providing type-safe access.

    Attributes:
        id: Unique bug identifier
        crashes: List of crash instances
        title: Bug title/description
        patch: Fix patch content
        parent_of_fix_commit: Parent commit SHA of the fix
        fix_commit: Fix commit SHA hash
        kcache: Kernel cache URL or identifier
        total_cost: Total LLM API cost for this bug
        total_calls: Total number of LLM API calls
        total_input_tokens: Total input tokens used
        total_output_tokens: Total output tokens generated
        total_thinking_output_tokens: Total thinking tokens (for models with extended thinking)
        total_non_thinking_output_tokens: Total non-thinking output tokens
        calls: List of individual LLM call records
        per_node_costs: Cost breakdown by tree node
        cost_breakdown: Detailed cost breakdown by token type
    """

    # Core metadata
    id: str
    crashes: List[CrashData]

    # Optional fields
    title: Optional[str] = None
    patch: Optional[str] = None
    parent_of_fix_commit: Optional[str] = None
    fix_commit: Optional[str] = None
    kcache: Optional[str] = None

    # Crash report fields (for nightly pipeline)
    raw_crash_report: Optional[str] = None
    clean_crash_report: Optional[List] = None  # List[List[Dict]] in SyzbotData
    patch_modified_files: Optional[List[str]] = None
    patch_modified_functions: Optional[List[List[str]]] = None
    display_title: Optional[str] = None

    # Cost tracking fields (for LLM usage analysis)
    total_cost: Optional[float] = None
    total_calls: Optional[int] = None
    total_input_tokens: Optional[int] = None
    total_output_tokens: Optional[int] = None
    total_thinking_output_tokens: Optional[int] = None
    total_non_thinking_output_tokens: Optional[int] = None
    calls: Optional[List[dict]] = None
    per_node_costs: Optional[Dict] = None
    cost_breakdown: Optional[Dict] = None

    # === Getter Methods with Fallbacks ===

    def get_kernel_source_git(self) -> str:
        """
        Get kernel git URL from first crash.

        Returns:
            Kernel git repository URL

        Raises:
            IndexError: If no crashes are available
        """
        return self.crashes[0].kernel_source_git

    def get_base_commit(self, parent_commit_flag: bool = False) -> str:
        """
        Get base commit - parent of fix or original crash commit.

        Args:
            parent_commit_flag: If True, return parent_of_fix_commit;
                               otherwise return original crash commit

        Returns:
            Commit SHA hash

        Raises:
            ValueError: If requested commit type is not available
            IndexError: If no crashes are available
        """
        if parent_commit_flag:
            if not self.parent_of_fix_commit:
                raise ValueError(
                    f"Bug {self.id}: parent_of_fix_commit field is missing. "
                    "Cannot use parent commit mode."
                )
            return self.parent_of_fix_commit
        return self.crashes[0].kernel_source_commit

    def get_fix_commit(self) -> str:
        """
        Get fix commit SHA.

        Returns:
            Fix commit SHA hash

        Raises:
            ValueError: If fix_commit field is not available
        """
        if not self.fix_commit:
            raise ValueError(
                f"Bug {self.id}: fix_commit field is missing. "
                "This bug may not have fix commit data available. "
                "Use parent_of_fix_commit or original crash commit instead."
            )
        return self.fix_commit

    def get_architecture(self) -> str:
        """
        Get architecture from first crash.

        Returns:
            CPU architecture (e.g., "amd64", "arm64")

        Raises:
            IndexError: If no crashes are available
        """
        return self.crashes[0].architecture

    def get_kernel_config_data(self) -> Optional[str]:
        """
        Get kernel config data from first crash.

        Returns:
            Kernel configuration content, or None if not available

        Raises:
            IndexError: If no crashes are available
        """
        return self.crashes[0].kernel_config_data

    def get_compiler(self) -> str:
        """
        Get compiler type (gcc/clang) from first crash.

        Returns:
            Compiler name: "gcc" or "clang"

        Raises:
            ValueError: If compiler description is missing or unknown
            IndexError: If no crashes are available
        """
        compiler_desc = self.crashes[0].compiler_description
        if not compiler_desc:
            raise ValueError(
                f"Bug {self.id}: compiler_description field is missing or empty"
            )

        # AIDEV-NOTE: Simple string matching for compiler detection
        compiler_lower = compiler_desc.lower()
        if "gcc" in compiler_lower:
            return "gcc"
        elif "clang" in compiler_lower:
            return "clang"
        else:
            raise ValueError(
                f"Bug {self.id}: Unknown compiler in description: {compiler_desc}"
            )

    # === Factory Methods ===

    @classmethod
    def from_json_file(cls, filepath: str) -> BugData:
        """
        Load BugData from JSON file with kebab-case field support.

        Args:
            filepath: Path to JSON file containing bug data

        Returns:
            BugData instance

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If required fields are missing
            json.JSONDecodeError: If file is not valid JSON
        """
        with open(filepath, 'r') as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> BugData:
        """
        Load BugData from dictionary with field name normalization.

        Supports both legacy kebab-case field names and new camelCase field names.

        Args:
            data: Dictionary containing bug data

        Returns:
            BugData instance

        Raises:
            ValueError: If required fields are missing
        """
        # Parse bug ID - support both "id" and "bugId"
        bug_id = data.get("id") or data.get("bugId")
        if not bug_id:
            raise ValueError("Bug ID field ('id' or 'bugId') is missing")

        # Parse crashes - required field
        crashes_data = data.get("crashes")
        if not crashes_data:
            raise ValueError("crashes field is missing or empty")

        crashes = [CrashData.from_dict(c) for c in crashes_data]

        # Parse optional fields with fallbacks
        # AIDEV-NOTE: Support both snake_case and camelCase for parent commit field
        parent_commit = data.get("parent_of_fix_commit") or data.get("parentOfFixCommit")

        # Parse fix_commit from fix-commits list
        # AIDEV-NOTE: Support both kebab-case "fix-commits" and camelCase "fixCommits"
        fix_commit = None
        fix_commits_list = data.get("fix-commits") or data.get("fixCommits")
        if fix_commits_list and isinstance(fix_commits_list, list) and len(fix_commits_list) > 0:
            first_fix = fix_commits_list[0]  # Get first fix commit dict
            if isinstance(first_fix, dict):
                # Support both "hash" (kebab-case) and "hashValue" (camelCase)
                fix_commit = first_fix.get("hash") or first_fix.get("hashValue")

        return cls(
            id=bug_id,
            crashes=crashes,
            title=data.get("title"),
            patch=data.get("patch"),
            parent_of_fix_commit=parent_commit,
            fix_commit=fix_commit,
            kcache=data.get("kcache"),
            # Crash report fields
            raw_crash_report=data.get("rawCrashReport") or data.get("raw_crash_report"),
            clean_crash_report=data.get("cleanCrashReport") or data.get("clean_crash_report"),
            patch_modified_files=data.get("patchModifiedFiles") or data.get("patch_modified_files"),
            patch_modified_functions=data.get("patchModifiedFunctions") or data.get("patch_modified_functions"),
            display_title=data.get("displayTitle") or data.get("display_title"),
            # Cost tracking fields
            total_cost=data.get("total_cost"),
            total_calls=data.get("total_calls"),
            total_input_tokens=data.get("total_input_tokens"),
            total_output_tokens=data.get("total_output_tokens"),
            total_thinking_output_tokens=data.get("total_thinking_output_tokens"),
            total_non_thinking_output_tokens=data.get("total_non_thinking_output_tokens"),
            calls=data.get("calls"),
            per_node_costs=data.get("per_node_costs"),
            cost_breakdown=data.get("cost_breakdown")
        )

    # AIDEV-NOTE: from_syzbot_data commented out — requires Kernel_Agent nightly pipeline
    # (SyzbotData from deployment_related_code). Not used by minimization.
    # @classmethod
    # def from_syzbot_data(cls, syzbot_data) -> BugData:
    #     """
    #     Convert SyzbotData Pydantic model to BugData.
    #
    #     This method enables seamless integration with the nightly pipeline
    #     that uses SyzbotData Pydantic models from JSON files.
    #
    #     Args:
    #         syzbot_data: SyzbotData Pydantic model instance
    #
    #     Returns:
    #         BugData instance
    #
    #     Raises:
    #         ValueError: If required fields are missing in syzbot_data
    #     """
    #     # AIDEV-NOTE: Import here to avoid circular dependency
    #     # SyzbotData is in deployment_related_code, which might import data_classes
    #     from Kernel_Agent.deployment_related_code.syzbot_models import SyzbotData
    #
    #     if not isinstance(syzbot_data, SyzbotData):
    #         raise TypeError(
    #             f"Expected SyzbotData instance, got {type(syzbot_data).__name__}"
    #         )
    #
    #     # Convert SyzbotCrash objects to CrashData
    #     crashes = []
    #     for crash in syzbot_data.crashes:
    #         crashes.append(CrashData(
    #             kernel_source_git=crash.kernelSourceGit,
    #             kernel_source_commit=crash.kernelSourceCommit,
    #             architecture=crash.architecture,
    #             kernel_config_data=crash.kernelConfig,  # Use kernelConfig for both fields
    #             kernel_config=crash.kernelConfig,
    #             compiler_description=crash.compilerDescription,
    #             c_reproducer=crash.cReproducer,
    #             syz_reproducer_data=crash.syzReproducer
    #         ))
    #
    #     # Extract fix commit SHA from fixCommits list
    #     # AIDEV-NOTE: Takes first fix commit if available, extracts hashValue field
    #     fix_commit = None
    #     if syzbot_data.fixCommits and len(syzbot_data.fixCommits) > 0:
    #         primary_fix_commit = syzbot_data.fixCommits[0]  # First SyzbotGitCommit object
    #         if primary_fix_commit.hashValue:  # Access .hashValue attribute
    #             fix_commit = primary_fix_commit.hashValue
    #
    #     return cls(
    #         id=syzbot_data.bugId,
    #         crashes=crashes,
    #         title=syzbot_data.title,
    #         patch=syzbot_data.patch,
    #         parent_of_fix_commit=syzbot_data.parentOfFixCommit,
    #         fix_commit=fix_commit,
    #         kcache=None,  # AIDEV-NOTE: Not in SyzbotData schema, populated separately
    #         # Crash report fields
    #         raw_crash_report=syzbot_data.rawCrashReport,
    #         clean_crash_report=syzbot_data.cleanCrashReport,
    #         patch_modified_files=syzbot_data.patchModifiedFiles,
    #         patch_modified_functions=syzbot_data.patchModifiedFunctions,
    #         display_title=syzbot_data.displayTitle
    #     )

    def to_dict(self) -> dict:
        """
        Convert BugData to dictionary representation.

        Useful for serialization and debugging.

        Returns:
            Dictionary with all bug data fields
        """
        return {
            "id": self.id,
            "crashes": [
                {
                    "kernel-source-git": c.kernel_source_git,
                    "kernel-source-commit": c.kernel_source_commit,
                    "architecture": c.architecture,
                    "kernel-config-data": c.kernel_config_data,
                    "kernel-config": c.kernel_config,
                    "compiler-description": c.compiler_description,
                    "c-reproducer": c.c_reproducer,
                    "syz-reproducer-data": c.syz_reproducer_data
                }
                for c in self.crashes
            ],
            "title": self.title,
            "patch": self.patch,
            "parent_of_fix_commit": self.parent_of_fix_commit,
            "fix_commit": self.fix_commit,
            "kcache": self.kcache,
            "total_cost": self.total_cost,
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_thinking_output_tokens": self.total_thinking_output_tokens,
            "total_non_thinking_output_tokens": self.total_non_thinking_output_tokens,
            "calls": self.calls,
            "per_node_costs": self.per_node_costs,
            "cost_breakdown": self.cost_breakdown
        }
