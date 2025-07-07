"""
Agent specification dataclass for the Slack Meta-Agent system
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any


@dataclass
class AgentSpec:
    """Specification for creating specialized agents"""

    name: str
    instruction: str
    server_names: List[str]
    capabilities: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)
