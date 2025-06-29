"""
Slack Meta-Agent System

A comprehensive Slack bot system built on the MCP (Model Context Protocol)
agent framework that can dynamically orchestrate specialized agents to solve complex problems.
"""

__version__ = "1.0.0"
__author__ = "Your Name"

from .core.meta_agent import SlackMetaAgent, MetaAgent

__all__ = ["SlackMetaAgent", "MetaAgent"]
