"""
Configuration loading and management for the Slack Meta-Agent system
"""

import os
import logging
from typing import Dict, Any


class ConfigLoader:
    """Handles configuration loading and management"""

    def __init__(self):
        self.logger = logging.getLogger("ConfigLoader")
        self.config = self._load_config()

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value"""
        return self.config.get(key, default)

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from environment and defaults"""
        config = {
            # Cache settings
            "cache_ttl_seconds": int(
                os.getenv("CACHE_TTL_SECONDS", "1800")
            ),  # 30 minutes
            "pattern_confidence_threshold": float(
                os.getenv("PATTERN_CONFIDENCE_THRESHOLD", "0.8")
            ),
            # Agent pool settings
            "health_check_interval": int(
                os.getenv("HEALTH_CHECK_INTERVAL", "300")
            ),  # 5 minutes
            "memory_cleanup_interval": int(
                os.getenv("MEMORY_CLEANUP_INTERVAL", "3600")
            ),  # 1 hour
            # File paths
            "learning_persistence_file": os.getenv(
                "LEARNING_PERSISTENCE_FILE",
                "slack_meta_agent/data/pattern_learning.json",
            ),
        }

        self.logger.info(f"📊 Configuration loaded with {len(config)} settings")
        return config
