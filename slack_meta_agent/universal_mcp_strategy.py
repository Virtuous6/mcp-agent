#!/usr/bin/env python3
"""
Universal MCP Server Strategy - Backward Compatibility Wrapper

This file maintains backward compatibility for the original main.py
by importing the refactored universal MCP strategy functions from their new location.

The actual implementation has been moved to src/slack_meta_agent/utils/mcp_strategy.py
"""

# Import the functions from the refactored location
from src.slack_meta_agent.utils.mcp_strategy import (
    explore_any_mcp_server,
    print_exploration_results,
)

# Re-export for backward compatibility
__all__ = [
    "explore_any_mcp_server",
    "print_exploration_results",
]
