"""
Agent Logging System - Comprehensive logging for all agent activities.

This module provides structured logging for agent tasks, actions, and results.
Each agent logs what they did, when, and with what results.
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field


@dataclass
class AgentAction:
    """Represents a single action taken by an agent."""

    action_id: str
    agent_name: str
    action_type: str  # "task_start", "tool_use", "llm_call", "task_complete", "error"
    description: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    duration_ms: Optional[float] = None
    success: bool = True
    error: Optional[str] = None


@dataclass
class AgentSession:
    """Represents a complete agent session with multiple actions."""

    session_id: str
    agent_name: str
    task_description: str
    start_time: str = field(default_factory=lambda: datetime.now().isoformat())
    end_time: Optional[str] = None
    actions: List[AgentAction] = field(default_factory=list)
    final_result: Optional[Any] = None
    success: bool = True
    total_duration_ms: Optional[float] = None


class AgentLogger:
    """Enhanced logging system for agent activities."""

    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.logger = logging.getLogger(f"Agent.{agent_name}")
        self.current_session: Optional[AgentSession] = None
        self.action_counter = 0

    def start_session(self, session_id: str, task_description: str) -> AgentSession:
        """Start a new agent session."""
        self.current_session = AgentSession(
            session_id=session_id,
            agent_name=self.agent_name,
            task_description=task_description,
        )

        self.logger.info(f"🚀 Session started: {session_id} - {task_description}")
        return self.current_session

    def log_action(
        self,
        action_type: str,
        description: str,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        metadata: Dict[str, Any] = None,
        duration_ms: float = None,
        success: bool = True,
        error: str = None,
    ) -> AgentAction:
        """Log a single agent action."""

        self.action_counter += 1
        action_id = f"{self.agent_name}_{self.action_counter}"

        action = AgentAction(
            action_id=action_id,
            agent_name=self.agent_name,
            action_type=action_type,
            description=description,
            inputs=inputs or {},
            outputs=outputs or {},
            metadata=metadata or {},
            duration_ms=duration_ms,
            success=success,
            error=error,
        )

        if self.current_session:
            self.current_session.actions.append(action)

        # Log to standard logger
        status = "✅" if success else "❌"
        duration_str = f" ({duration_ms:.1f}ms)" if duration_ms else ""
        self.logger.info(f"{status} {action_type}: {description}{duration_str}")

        if error:
            self.logger.error(f"❌ Error in {action_type}: {error}")

        return action

    def log_llm_call(
        self,
        model: str,
        prompt_length: int,
        response_length: int,
        duration_ms: float,
        success: bool = True,
        error: str = None,
    ) -> AgentAction:
        """Log an LLM call with specific metrics."""

        return self.log_action(
            action_type="llm_call",
            description=f"LLM call to {model}",
            inputs={"model": model, "prompt_length": prompt_length},
            outputs={"response_length": response_length},
            metadata={
                "tokens_estimated": prompt_length + response_length,
                "model_type": model,
            },
            duration_ms=duration_ms,
            success=success,
            error=error,
        )

    def log_tool_use(
        self,
        tool_name: str,
        tool_inputs: Dict[str, Any],
        tool_outputs: Dict[str, Any],
        duration_ms: float,
        success: bool = True,
        error: str = None,
    ) -> AgentAction:
        """Log a tool usage."""

        return self.log_action(
            action_type="tool_use",
            description=f"Used tool: {tool_name}",
            inputs=tool_inputs,
            outputs=tool_outputs,
            duration_ms=duration_ms,
            success=success,
            error=error,
        )

    def log_task_start(
        self, task_description: str, task_inputs: Dict[str, Any]
    ) -> AgentAction:
        """Log the start of a task."""

        return self.log_action(
            action_type="task_start",
            description=task_description,
            inputs=task_inputs,
            metadata={"task_phase": "start"},
        )

    def log_task_complete(
        self,
        task_description: str,
        result: Any,
        duration_ms: float,
        success: bool = True,
    ) -> AgentAction:
        """Log task completion."""

        return self.log_action(
            action_type="task_complete",
            description=f"Completed: {task_description}",
            outputs={"result": result},
            metadata={"task_phase": "complete"},
            duration_ms=duration_ms,
            success=success,
        )

    def end_session(
        self, final_result: Any = None, success: bool = True
    ) -> AgentSession:
        """End the current session."""

        if not self.current_session:
            self.logger.warning("No active session to end")
            return None

        self.current_session.end_time = datetime.now().isoformat()
        self.current_session.final_result = final_result
        self.current_session.success = success

        # Calculate total duration
        if self.current_session.start_time:
            start_dt = datetime.fromisoformat(self.current_session.start_time)
            end_dt = datetime.fromisoformat(self.current_session.end_time)
            self.current_session.total_duration_ms = (
                end_dt - start_dt
            ).total_seconds() * 1000

        status = "✅" if success else "❌"
        duration_str = (
            f" ({self.current_session.total_duration_ms:.1f}ms)"
            if self.current_session.total_duration_ms
            else ""
        )

        self.logger.info(
            f"{status} Session completed: {self.current_session.session_id} - "
            f"{len(self.current_session.actions)} actions{duration_str}"
        )

        session = self.current_session
        self.current_session = None
        return session

    def get_session_summary(self) -> Dict[str, Any]:
        """Get a summary of the current session."""

        if not self.current_session:
            return {"error": "No active session"}

        action_types = {}
        total_duration = 0
        errors = []

        for action in self.current_session.actions:
            action_types[action.action_type] = (
                action_types.get(action.action_type, 0) + 1
            )
            if action.duration_ms:
                total_duration += action.duration_ms
            if action.error:
                errors.append(action.error)

        return {
            "session_id": self.current_session.session_id,
            "agent_name": self.current_session.agent_name,
            "task_description": self.current_session.task_description,
            "actions_count": len(self.current_session.actions),
            "action_types": action_types,
            "total_duration_ms": total_duration,
            "errors_count": len(errors),
            "errors": errors,
            "success": self.current_session.success,
        }


class AgentLoggingMixin:
    """Mixin to add comprehensive logging to any agent."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.agent_logger = AgentLogger(self.__class__.__name__)

    def start_task_session(self, task_id: str, description: str) -> AgentSession:
        """Start a new task session with logging."""
        return self.agent_logger.start_session(task_id, description)

    def log_action(self, action_type: str, description: str, **kwargs) -> AgentAction:
        """Log an agent action."""
        return self.agent_logger.log_action(action_type, description, **kwargs)

    def log_llm_call(
        self,
        model: str,
        prompt_length: int,
        response_length: int,
        duration_ms: float,
        **kwargs,
    ) -> AgentAction:
        """Log an LLM call."""
        return self.agent_logger.log_llm_call(
            model, prompt_length, response_length, duration_ms, **kwargs
        )

    def log_tool_use(
        self,
        tool_name: str,
        tool_inputs: Dict[str, Any],
        tool_outputs: Dict[str, Any],
        duration_ms: float,
        **kwargs,
    ) -> AgentAction:
        """Log tool usage."""
        return self.agent_logger.log_tool_use(
            tool_name, tool_inputs, tool_outputs, duration_ms, **kwargs
        )

    def complete_task_session(
        self, result: Any = None, success: bool = True
    ) -> AgentSession:
        """Complete the current task session."""
        return self.agent_logger.end_session(result, success)

    def get_task_summary(self) -> Dict[str, Any]:
        """Get current task summary."""
        return self.agent_logger.get_session_summary()
