"""
Conversation management dataclasses for the Slack Meta-Agent system
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List


@dataclass
class ConversationTurn:
    """Represents a single conversation turn with quality metrics"""

    turn_number: int
    user_input: str
    agent_response: str
    intent_analysis: Dict
    timestamp: datetime
    quality_metrics: Dict = field(default_factory=dict)
    execution_time: float = 0.0


@dataclass
class ConversationState:
    """Enhanced conversation state management"""

    user_id: str
    channel_id: str
    turns: List[ConversationTurn] = field(default_factory=list)
    current_turn: int = 0
    context_memory: Dict = field(default_factory=dict)
    user_preferences: Dict = field(default_factory=dict)

    def add_turn(self, turn: ConversationTurn):
        """Add a new conversation turn"""
        self.turns.append(turn)
        self.current_turn = len(self.turns)

    def get_recent_context(self, max_turns: int = 3) -> str:
        """Get recent conversation context for better responses"""
        recent_turns = self.turns[-max_turns:] if self.turns else []
        context = []
        for turn in recent_turns:
            context.append(f"User: {turn.user_input}")
            context.append(f"Agent: {turn.agent_response[:200]}...")
        return "\n".join(context)
