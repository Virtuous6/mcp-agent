"""Agent specification models with CrewAI-style configuration."""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime


@dataclass
class AgentSpec:
    """Legacy agent specification for backward compatibility."""

    name: str
    instruction: str
    server_names: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    is_dynamic: bool = False


@dataclass
class EnhancedAgentSpec:
    """Enhanced agent specification with CrewAI-style configuration."""

    # Core Identity
    id: str  # e.g., "intent_analyzer"
    name: str  # Human-friendly name
    role: str  # One-line role summary
    backstory: str  # Rich persona context
    goal: str  # Agent's primary objective

    # Behavior Configuration
    allow_delegation: bool = False  # Can spawn sub-agents
    verbose: bool = True  # Debug logging level
    cache: bool = True  # Enable response caching

    # LLM Configuration
    llm_provider: str = "openai"  # "openai", "anthropic", etc.
    llm_model: str = "gpt-4o-mini"  # Model identifier
    temperature: float = 0.3  # 0.0-1.0 sampling temp
    max_tokens: int = 2000  # Token limit

    # Execution Settings
    max_iterations: int = 10  # Max think→act loops
    tools: List[str] = field(default_factory=list)  # Available tool IDs
    memory_policy: Dict[str, Any] = field(
        default_factory=dict
    )  # Memory management config
    timeout_ms: int = 30000  # Execution timeout

    # Advanced Prompting
    system_prompt: str = ""  # Core system instructions
    few_shot_examples: List[Dict[str, str]] = field(
        default_factory=list
    )  # Example interactions
    constraints: List[str] = field(default_factory=list)  # Behavioral constraints
    output_format: str = "markdown"  # Expected output structure

    # Metadata
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    version: str = "1.0.0"

    # Legacy compatibility
    capabilities: List[str] = field(default_factory=list)
    is_dynamic: bool = False

    def to_legacy_spec(self) -> AgentSpec:
        """Convert to legacy AgentSpec for backward compatibility."""
        return AgentSpec(
            name=self.id,
            instruction=self.build_instruction(),
            server_names=self.tools,
            capabilities=self.capabilities,
            is_dynamic=self.is_dynamic,
        )

    def build_instruction(self) -> str:
        """Build rich instruction from spec components."""
        instruction_parts = []

        if self.system_prompt:
            instruction_parts.append(self.system_prompt)
        else:
            # Build default instruction from components
            instruction_parts.append(f"Role: {self.role}")
            instruction_parts.append(f"Backstory: {self.backstory}")
            instruction_parts.append(f"Goal: {self.goal}")

        if self.constraints:
            instruction_parts.append("\nConstraints:")
            for constraint in self.constraints:
                instruction_parts.append(f"- {constraint}")

        if self.few_shot_examples:
            instruction_parts.append("\nExamples:")
            for example in self.few_shot_examples:
                instruction_parts.append(f"Query: {example.get('query', '')}")
                instruction_parts.append(f"Response: {example.get('response', '')}")
                instruction_parts.append("")

        if self.output_format and self.output_format != "markdown":
            instruction_parts.append(f"\nOutput Format: {self.output_format}")

        return "\n".join(instruction_parts)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database storage."""
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "backstory": self.backstory,
            "goal": self.goal,
            "allow_delegation": self.allow_delegation,
            "verbose": self.verbose,
            "cache": self.cache,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "max_iterations": self.max_iterations,
            "tools": self.tools,
            "memory_policy": self.memory_policy,
            "timeout_ms": self.timeout_ms,
            "system_prompt": self.system_prompt,
            "few_shot_examples": self.few_shot_examples,
            "constraints": self.constraints,
            "output_format": self.output_format,
            "capabilities": self.capabilities,
            "is_dynamic": self.is_dynamic,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EnhancedAgentSpec":
        """Create from dictionary (e.g., from database)."""
        # Remove timestamps if present
        data.pop("created_at", None)
        data.pop("updated_at", None)
        return cls(**data)
