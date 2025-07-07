"""
Core types and interfaces for the modular micro-agent framework.

This module defines the fundamental data structures and contracts that all
components use to communicate with each other.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any
from enum import Enum


class ExecutionStrategy(Enum):
    """Available execution strategies for handling user requests."""

    SINGLE_AGENT = "single_agent"
    WORKFLOW = "workflow"
    DYNAMIC_DISCOVERY = "dynamic_discovery"
    ORCHESTRATED = "orchestrated"
    HUMAN_INPUT = "human_input"


class ConfidenceLevel(Enum):
    """Confidence levels for intent classification."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Priority(Enum):
    """Priority levels for task execution."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class WorkflowStatus(Enum):
    """Workflow execution status."""

    STARTED = "started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DiscoveryMethod(Enum):
    """MCP server discovery methods."""

    DATABASE = "database"
    CONFIGURED = "configured"
    GENERATED = "generated"


@dataclass
class MessageContext:
    """Context information for an incoming message."""

    channel_id: str
    user_id: str
    thread_ts: Optional[str] = None
    timestamp: Optional[str] = None
    message_ts: Optional[str] = None
    turn_number: int = 1
    recent_context: str = ""
    platform: str = "slack"  # Future: support other platforms


@dataclass
class IncomingMessage:
    """Normalized representation of an incoming message from any platform."""

    text: str
    context: MessageContext
    raw_event: Optional[Dict[str, Any]] = None  # Platform-specific raw data


@dataclass
class Intent:
    """Classified intent with execution strategy and metadata."""

    name: str
    required_agents: List[str]
    execution_strategy: ExecutionStrategy
    confidence: ConfidenceLevel
    reasoning: str

    # Strategy-specific payload
    payload: Dict[str, Any] = field(default_factory=dict)

    # Optional workflow information
    workflow: Optional[Dict[str, Any]] = None

    # Pattern matching metadata
    matched_keywords: List[str] = field(default_factory=list)
    pattern_confidence: float = 0.0

    # Discovery metadata
    discovery_keywords: List[str] = field(default_factory=list)
    qualified_services: bool = False

    # Execution hints
    priority: Priority = Priority.MEDIUM
    estimated_tasks: int = 1
    requires_tools: List[str] = field(default_factory=list)


@dataclass
class ExecutionResult:
    """Result of executing an intent."""

    success: bool
    response: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Performance metrics
    execution_time: float = 0.0
    agent_count: int = 0

    # Quality metrics
    intent_confidence: ConfidenceLevel = ConfidenceLevel.LOW
    complexity: str = "unknown"

    # Error information
    error: Optional[str] = None
    error_type: Optional[str] = None


@dataclass
class AgentSpec:
    """Specification for a specialized agent."""

    name: str
    instruction: str
    server_names: List[str]
    capabilities: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Dynamic configuration
    is_dynamic: bool = False
    database_id: Optional[str] = None


@dataclass
class ToolCatalog:
    """Catalog of discovered tools and capabilities."""

    agents: Dict[str, Dict[str, Any]]
    servers: Dict[str, List[Dict[str, Any]]]
    total_tools: int
    cache_timestamp: datetime
    discovery_time: float = 0.0


@dataclass
class PatternMatch:
    """Result of pattern matching analysis."""

    pattern_name: str
    agent: str
    confidence: float
    matched_keywords: List[str]
    match_strategy: str
    all_scores: Dict[str, float] = field(default_factory=dict)
    threshold_used: float = 0.0


@dataclass
class WorkflowTrigger:
    """Workflow trigger match information."""

    workflow: Dict[str, Any]
    confidence: float
    matched_patterns: List[str]
    trigger_keywords: List[str]


@dataclass
class WorkflowExecution:
    """Workflow execution result information."""

    workflow_id: str
    workflow_name: str
    execution_status: WorkflowStatus
    result: Optional[str] = None
    execution_time: float = 0.0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ServerDiscovery:
    """MCP server discovery result."""

    servers: List[Dict[str, Any]]
    keywords: List[str]
    discovery_method: DiscoveryMethod
    confidence: float = 0.0


# Abstract base classes for components


class ComponentBase:
    """Base class for all micro-agent components."""

    def __init__(self, name: str):
        self.name = name
        self.logger = self._setup_logger()

    def _setup_logger(self):
        import logging

        return logging.getLogger(
            f"{self.__class__.__module__}.{self.__class__.__name__}"
        )


class AgentComponent(ComponentBase):
    """Base class for agent components that can be initialized and cleaned up."""

    async def initialize(self) -> bool:
        """Initialize the component. Returns True if successful."""
        return True

    async def cleanup(self) -> None:
        """Clean up resources."""
        pass

    async def health_check(self) -> Dict[str, Any]:
        """Check component health."""
        return {"status": "healthy", "component": self.name}


# Type aliases for common patterns
AgentRegistry = Dict[str, AgentSpec]
PatternRegistry = Dict[str, Dict[str, Any]]
ConversationMemory = Dict[str, List[Dict[str, Any]]]
