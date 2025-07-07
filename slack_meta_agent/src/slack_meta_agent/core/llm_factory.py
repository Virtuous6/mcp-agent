"""
LLM Factory Utilities - Database-driven LLM configuration.

This module provides smart LLM factory functions that use agent specifications
from the database to configure LLM instances with the correct model, temperature,
max_tokens, and other settings.
"""

from typing import Dict, Any, Optional, Callable
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.agents.agent import Agent


class SmartLLMFactory:
    """Factory class for creating LLM instances with database-driven configuration."""

    @staticmethod
    def create_from_agent_config(
        agent: Agent, fallback_config: Optional[Dict[str, Any]] = None
    ) -> OpenAIAugmentedLLM:
        """
        Create an LLM instance using agent's database configuration.

        Args:
            agent: The agent instance (may have llm_config from database)
            fallback_config: Optional fallback configuration if agent has no config

        Returns:
            OpenAIAugmentedLLM instance configured with appropriate settings
        """
        # Check if agent has database-driven config
        if hasattr(agent, "llm_config") and agent.llm_config:
            config = agent.llm_config

            return OpenAIAugmentedLLM(
                agent=agent,
                default_model=config.get("model", "gpt-4o-mini"),
                default_request_params=RequestParams(
                    model=config.get("model", "gpt-4o-mini"),
                    temperature=config.get("temperature", 0.3),
                    maxTokens=config.get("max_tokens", 2000),
                ),
            )

        # Use fallback config if provided
        elif fallback_config:
            return OpenAIAugmentedLLM(
                agent=agent,
                default_model=fallback_config.get("model", "gpt-4o-mini"),
                default_request_params=RequestParams(
                    model=fallback_config.get("model", "gpt-4o-mini"),
                    temperature=fallback_config.get("temperature", 0.3),
                    maxTokens=fallback_config.get("max_tokens", 2000),
                ),
            )

        # Final fallback to defaults
        else:
            return OpenAIAugmentedLLM(agent=agent)

    @staticmethod
    def create_for_task_type(
        agent: Agent, task_type: str, agent_type: str = None
    ) -> OpenAIAugmentedLLM:
        """
        Create an LLM instance optimized for specific task types.

        This method first checks for agent's database config, then applies
        task-specific optimizations on top of it.

        Args:
            agent: The agent instance
            task_type: Type of task (e.g., "simple", "complex", "research", "analysis")
            agent_type: Optional agent type for additional optimization

        Returns:
            OpenAIAugmentedLLM instance optimized for the task
        """
        # Start with agent's database config if available
        base_config = {}
        if hasattr(agent, "llm_config") and agent.llm_config:
            base_config = agent.llm_config.copy()

        # Apply task-specific optimizations
        if task_type == "simple" or agent_type in [
            "feedback_collector",
            "capability_inspector",
        ]:
            # Use faster, cheaper model for simple tasks
            optimized_config = {
                "model": base_config.get("model", "gpt-4o-mini"),
                "temperature": base_config.get("temperature", 0.3),
                "max_tokens": min(
                    base_config.get("max_tokens", 2000), 1500
                ),  # Cap tokens for simple tasks
            }

        elif task_type == "complex" or agent_type in [
            "financial_analyst",
            "code_developer",
        ]:
            # Use more powerful model for complex analysis
            optimized_config = {
                "model": base_config.get("model", "gpt-4o"),
                "temperature": max(
                    base_config.get("temperature", 0.1), 0.1
                ),  # Lower temp for precision
                "max_tokens": max(
                    base_config.get("max_tokens", 4000), 4000
                ),  # Higher tokens for complex tasks
            }

        elif task_type == "research" or agent_type in [
            "data_researcher",
            "knowledge_agent",
        ]:
            # Balanced model for research tasks
            optimized_config = {
                "model": base_config.get("model", "gpt-4o"),
                "temperature": base_config.get("temperature", 0.2),
                "max_tokens": base_config.get("max_tokens", 3000),
            }

        else:
            # Use agent's config or reasonable defaults
            optimized_config = {
                "model": base_config.get("model", "gpt-4o-mini"),
                "temperature": base_config.get("temperature", 0.3),
                "max_tokens": base_config.get("max_tokens", 2000),
            }

        return OpenAIAugmentedLLM(
            agent=agent,
            default_model=optimized_config["model"],
            default_request_params=RequestParams(
                model=optimized_config["model"],
                temperature=optimized_config["temperature"],
                maxTokens=optimized_config["max_tokens"],
            ),
        )

    @staticmethod
    def create_for_intent(
        agent: Agent, intent_name: str, confidence: str = "medium"
    ) -> OpenAIAugmentedLLM:
        """
        Create an LLM instance optimized for specific intent types.

        Args:
            agent: The agent instance
            intent_name: Name of the intent (e.g., "weather_inquiry", "greeting", "complex_analysis")
            confidence: Confidence level of the intent classification

        Returns:
            OpenAIAugmentedLLM instance optimized for the intent
        """
        # Start with agent's database config
        base_config = {}
        if hasattr(agent, "llm_config") and agent.llm_config:
            base_config = agent.llm_config.copy()

        # Apply intent-specific optimizations
        if intent_name in ["weather_inquiry", "greeting", "simple_lookup"]:
            # Fast path for simple intents
            optimized_config = {
                "model": "gpt-4o-mini",  # Always use fast model for simple intents
                "temperature": 0.3,
                "max_tokens": 1500,
            }

        elif confidence == "high" and intent_name in [
            "complex_analysis",
            "financial_analysis",
        ]:
            # High-confidence complex tasks get premium models
            optimized_config = {
                "model": base_config.get("model", "gpt-4o"),
                "temperature": base_config.get("temperature", 0.1),
                "max_tokens": base_config.get("max_tokens", 4000),
            }

        else:
            # Use agent's database config or defaults
            optimized_config = {
                "model": base_config.get("model", "gpt-4o-mini"),
                "temperature": base_config.get("temperature", 0.3),
                "max_tokens": base_config.get("max_tokens", 2000),
            }

        return OpenAIAugmentedLLM(
            agent=agent,
            default_model=optimized_config["model"],
            default_request_params=RequestParams(
                model=optimized_config["model"],
                temperature=optimized_config["temperature"],
                maxTokens=optimized_config["max_tokens"],
            ),
        )


# Convenience functions for backward compatibility
def create_smart_llm_factory(
    target_agent: Agent, fallback_config: Optional[Dict[str, Any]] = None
) -> Callable:
    """
    Create a smart LLM factory function that uses database configuration.

    This function returns a factory function that can be used with agent.attach_llm().
    """

    def llm_factory(agent=None):
        # Use the agent passed from attach_llm() - this is the correct agent instance
        return SmartLLMFactory.create_from_agent_config(agent, fallback_config)

    return llm_factory


def create_task_optimized_llm_factory(
    task_type: str, agent_type: str = None
) -> Callable:
    """
    Create a task-optimized LLM factory function.

    Args:
        task_type: Type of task for optimization
        agent_type: Optional agent type for additional optimization

    Returns:
        Factory function for use with agent.attach_llm()
    """

    def llm_factory(agent):
        return SmartLLMFactory.create_for_task_type(agent, task_type, agent_type)

    return llm_factory


def create_intent_optimized_llm_factory(
    intent_name: str, confidence: str = "medium"
) -> Callable:
    """
    Create an intent-optimized LLM factory function.

    Args:
        intent_name: Name of the intent for optimization
        confidence: Confidence level of intent classification

    Returns:
        Factory function for use with agent.attach_llm()
    """

    def llm_factory(agent):
        return SmartLLMFactory.create_for_intent(agent, intent_name, confidence)

    return llm_factory
