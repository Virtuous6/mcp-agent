"""
Agent specification dataclass for the Slack Meta-Agent system
"""

from dataclasses import dataclass
from typing import List


@dataclass
class AgentSpec:
    """Specification for creating specialized agents"""

    name: str
    instruction: str
    server_names: List[str]
    capabilities: List[str]
