"""
Type definitions for conversation history tracking in Anthropic API format.

This module contains dataclasses for tracking tool invocations in a format
compatible with the Anthropic API message structure, enabling Claude to be
queried with full context as tree exploration deepens.

AIDEV-NOTE: Per-node conversation history enables Claude to understand
the full debugging context when generating hypotheses or patches.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum
import uuid


class ToolNames(str, Enum):
    """Standardized tool names for conversation history."""
    GENERATE_HYPOTHESIS = "generate_hypothesis"
    SELECT_HYPOTHESIS = "select_hypothesis"
    GENERATE_PATCH = "generate_patch"
    SELECT_PATCH = "select_patch"
    VALIDATE_PATCH = "validate_patch"
    VALIDATE_PATCH_5X = "validate_patch_5x"
    RUN_KERNEL = "run_kernel"
    HYPOTHESIS_SELF_REFLECTION = "hypothesis_self_reflection"
    PATCH_SELF_REFLECTION = "patch_self_reflection"
    NODE_REVIEW = "node_review"
    APPLY_LESSONS = "apply_lessons"
    HELLO_WORLD = "hello_world"


@dataclass
class TextBlock:
    """Text content block in Anthropic API format."""
    text: str
    type: str = "text"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "text": self.text}


@dataclass
class ToolUseBlock:
    """Tool use block in Anthropic API format."""
    id: str
    name: str
    input: Dict[str, Any]
    node_name: Optional[str] = None  # Tree node identifier
    type: str = "tool_use"

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "type": self.type,
            "id": self.id,
            "name": self.name,
            "input": self.input,
        }
        if self.node_name is not None:
            result["node_name"] = self.node_name
        return result


@dataclass
class ToolResultBlock:
    """Tool result block in Anthropic API format."""
    tool_use_id: str
    content: str
    is_error: bool = False
    type: str = "tool_result"

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "type": self.type,
            "tool_use_id": self.tool_use_id,
            "content": self.content,
        }
        if self.is_error:
            result["is_error"] = True
        return result


@dataclass
class ConversationMessage:
    """A single message in the conversation history."""
    role: str  # "assistant" or "user"
    content: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "content": self.content}


@dataclass
class ConversationHistory:
    """
    Conversation history tracking tool invocations in Anthropic API format.

    Maintains a list of messages alternating between assistant (tool_use)
    and user (tool_result) roles.

    AIDEV-NOTE: This class is designed to be pickle-compatible by storing
    messages as dicts rather than dataclass instances.
    """
    messages: List[Dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def generate_tool_id() -> str:
        """Generate a tool_id upfront for use in file naming and conversation history."""
        return f"tool_{uuid.uuid4().hex[:12]}"

    def add_assistant_tool_call(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        reasoning: Optional[str] = None,
        node_name: Optional[str] = None,
        tool_use_id: Optional[str] = None,
    ) -> str:
        """
        Add an assistant message with a tool call.

        Args:
            tool_name: Name of the tool being called
            tool_input: Input parameters for the tool
            reasoning: Optional reasoning text to include before tool call
            node_name: Optional tree node identifier
            tool_use_id: Optional pre-generated tool ID (for file naming consistency)

        Returns:
            tool_use_id: Generated ID for matching with tool_result
        """
        if tool_use_id is None:
            tool_use_id = f"tool_{uuid.uuid4().hex[:12]}"

        content = []
        if reasoning:
            content.append(TextBlock(text=reasoning).to_dict())

        content.append(ToolUseBlock(
            id=tool_use_id,
            name=tool_name,
            input=tool_input,
            node_name=node_name,
        ).to_dict())

        self.messages.append(ConversationMessage(
            role="assistant",
            content=content,
        ).to_dict())

        return tool_use_id

    def add_tool_result(
        self,
        tool_use_id: str,
        result: str,
        is_error: bool = False,
    ) -> None:
        """
        Add a user message with a tool result.

        Args:
            tool_use_id: ID from the corresponding tool_use block
            result: String result from the tool execution
            is_error: Whether the result represents an error
        """
        self.messages.append(ConversationMessage(
            role="user",
            content=[ToolResultBlock(
                tool_use_id=tool_use_id,
                content=result,
                is_error=is_error,
            ).to_dict()],
        ).to_dict())

    def add_tool_invocation(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_result: str,
        reasoning: Optional[str] = None,
        is_error: bool = False,
        node_name: Optional[str] = None,
        tool_use_id: Optional[str] = None,
    ) -> None:
        """
        Convenience method to add both tool call and result in one operation.

        Args:
            tool_name: Name of the tool
            tool_input: Input parameters
            tool_result: Result string
            reasoning: Optional reasoning text
            is_error: Whether the result represents an error
            node_name: Optional tree node identifier
            tool_use_id: Optional pre-generated ID (for linking to llm_input files)
        """
        tool_use_id = self.add_assistant_tool_call(tool_name, tool_input, reasoning, node_name, tool_use_id)
        self.add_tool_result(tool_use_id, tool_result, is_error)

    def to_list(self) -> List[Dict[str, Any]]:
        """
        Get messages in Anthropic API format.

        Returns:
            List of message dicts ready for API submission
        """
        return self.messages.copy()

    def copy(self) -> ConversationHistory:
        """Create a deep copy of this conversation history."""
        import copy
        return ConversationHistory(messages=copy.deepcopy(self.messages))

    def __len__(self) -> int:
        return len(self.messages)

    def __bool__(self) -> bool:
        return len(self.messages) > 0


# AIDEV-NOTE: ConversationHistory stores messages as dicts for pickle compatibility.
# Use to_list() to get Anthropic API format for LLM queries.
