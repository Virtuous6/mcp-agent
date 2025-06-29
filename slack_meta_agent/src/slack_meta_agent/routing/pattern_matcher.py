"""
Pattern matching for the Slack Meta-Agent system

This module handles dynamic pattern matching with learning capabilities.
"""

import json
import logging
import os
from typing import Dict, List, Optional, Any


class PatternMatcher:
    """Handles dynamic pattern matching with confidence scoring and learning"""

    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger("PatternMatcher")
        self.patterns = self._load_patterns()

    def match_patterns(self, message: str) -> Optional[Dict[str, Any]]:
        """Match message against learned patterns"""
        message_lower = message.lower()

        best_match = None
        best_confidence = 0.0
        confidence_threshold = self.config.get("pattern_confidence_threshold", 0.8)

        for pattern_name, pattern_info in self.patterns.items():
            # Calculate confidence based on keyword matches
            keyword_matches = sum(
                1 for keyword in pattern_info["keywords"] if keyword in message_lower
            )

            if keyword_matches > 0:
                # Confidence calculation: base confidence * match ratio * usage boost
                match_ratio = keyword_matches / len(pattern_info["keywords"])
                usage_boost = min(1.2, 1.0 + (pattern_info["usage_count"] / 100))

                confidence = pattern_info["confidence"] * match_ratio * usage_boost

                if confidence > best_confidence and confidence >= confidence_threshold:
                    best_confidence = confidence
                    best_match = {
                        "agent": pattern_info["agent"],
                        "pattern": pattern_name,
                        "confidence": confidence,
                        "matched_keywords": [
                            kw for kw in pattern_info["keywords"] if kw in message_lower
                        ],
                    }

        # Update usage statistics for learning
        if best_match:
            pattern_name = best_match["pattern"]
            self.patterns[pattern_name]["usage_count"] += 1
            self._save_patterns()

        return best_match

    def update_pattern_learning(self, message: str, agent_used: str, success: bool):
        """Update learning patterns based on interaction success"""
        if not success:
            return

        message_words = set(message.lower().split())

        # Create agent entry if it doesn't exist
        if agent_used not in self.patterns:
            self.patterns[agent_used] = {
                "keywords": [],
                "agent": agent_used,
                "confidence": 0.7,
                "usage_count": 0,
            }

        # Add successful keywords
        for word in message_words:
            if len(word) > 3:  # Ignore short words
                if word not in self.patterns[agent_used]["keywords"]:
                    self.patterns[agent_used]["keywords"].append(word)

        self._save_patterns()

    def _load_patterns(self) -> Dict[str, Dict]:
        """Load patterns from file or create defaults"""
        patterns_file = self.config.get(
            "learning_persistence_file", "slack_meta_agent/data/pattern_learning.json"
        )

        try:
            if os.path.exists(patterns_file):
                with open(patterns_file, "r") as f:
                    loaded_patterns = json.load(f)
                    self.logger.info(f"📚 Loaded {len(loaded_patterns)} patterns")
                    return loaded_patterns
        except Exception as e:
            self.logger.warning(f"Could not load patterns: {e}")

        # Default patterns
        return self._get_default_patterns()

    def _save_patterns(self):
        """Save patterns to file"""
        patterns_file = self.config.get(
            "learning_persistence_file", "slack_meta_agent/data/pattern_learning.json"
        )

        try:
            os.makedirs(os.path.dirname(patterns_file), exist_ok=True)
            with open(patterns_file, "w") as f:
                json.dump(self.patterns, f, indent=2)
        except Exception as e:
            self.logger.warning(f"Could not save patterns: {e}")

    def _get_default_patterns(self) -> Dict[str, Dict]:
        """Get default pattern configurations"""
        return {
            "weather": {
                "keywords": ["weather", "temperature", "forecast", "rain", "sunny"],
                "agent": "data_researcher",
                "confidence": 0.9,
                "usage_count": 0,
            },
            "knowledge": {
                "keywords": ["what is", "who is", "define", "explain", "capital"],
                "agent": "knowledge_agent",
                "confidence": 0.85,
                "usage_count": 0,
            },
            "capabilities": {
                "keywords": ["tools", "capabilities", "help", "commands"],
                "agent": "capability_inspector",
                "confidence": 0.9,
                "usage_count": 0,
            },
            "airtable": {
                "keywords": ["airtable", "records", "base id", "table records"],
                "agent": "airtable_manager",
                "confidence": 0.9,
                "usage_count": 0,
            },
        }
