"""
Memory management for the Slack Meta-Agent system

This module handles conversation memory and caching.
"""

import logging
from typing import Dict, List, Any
from datetime import datetime


class MemoryManager:
    """Handles conversation memory and caching operations"""

    def __init__(self):
        self.logger = logging.getLogger("MemoryManager")
        self.conversation_cache: Dict[str, List[Dict]] = {}

    async def store_conversation_turn(self, user_id: str, turn_data: Dict[str, Any]):
        """Store a conversation turn in memory"""
        if user_id not in self.conversation_cache:
            self.conversation_cache[user_id] = []

        self.conversation_cache[user_id].append(
            {**turn_data, "timestamp": datetime.now().isoformat()}
        )

        # Keep only last 10 turns per user
        if len(self.conversation_cache[user_id]) > 10:
            self.conversation_cache[user_id] = self.conversation_cache[user_id][-10:]

    def get_recent_conversations(
        self, user_id: str, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Get recent conversation turns for a user"""
        return self.conversation_cache.get(user_id, [])[-limit:]

    def cleanup_old_conversations(self, max_age_hours: int = 24):
        """Clean up old conversation data"""
        # Placeholder for cleanup logic
        self.logger.info(f"Cleaning up conversations older than {max_age_hours} hours")
