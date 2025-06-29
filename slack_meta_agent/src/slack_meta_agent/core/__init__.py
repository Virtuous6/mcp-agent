"""
Core components of the Slack Meta-Agent system
"""

from .meta_agent import SlackMetaAgent
from .agent_spec import AgentSpec
from .conversation import ConversationTurn, ConversationState

# Backward compatibility alias
MetaAgent = SlackMetaAgent

__all__ = [
    "SlackMetaAgent",
    "MetaAgent",
    "AgentSpec",
    "ConversationTurn",
    "ConversationState",
]
