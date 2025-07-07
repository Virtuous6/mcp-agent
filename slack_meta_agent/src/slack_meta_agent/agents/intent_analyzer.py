"""
Intent Analyzer Agent - LLM-powered intent classification system.

This component uses an LLM to intelligently analyze user messages and determine:
1. What the user wants to accomplish (intent classification)
2. Initial agent suggestions
3. Confidence levels and reasoning
4. Execution strategy recommendations

The orchestrator uses this analysis to build detailed execution plans.
"""

import re
import json
from typing import Dict, List, Optional, Any

from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

from ..core.types import (
    Intent,
    IncomingMessage,
    ExecutionStrategy,
    ConfidenceLevel,
    AgentComponent,
)


class IntentAnalyzerAgent(AgentComponent):
    """
    LLM-powered intent analyzer that intelligently classifies user requests.

    Uses a language model to understand user intent rather than rigid pattern matching.
    This provides much more flexible and accurate intent classification.
    """

    def __init__(
        self,
        registry=None,
        tool_discovery=None,
        pool_manager=None,
        config: Dict[str, Any] = None,
    ):
        super().__init__("IntentAnalyzer")

        # Store injected dependencies
        self.registry = registry
        self.tool_discovery = tool_discovery
        self.pool_manager = pool_manager

        # Ensure config is a dictionary
        if config is None:
            self.config = self._default_config()
        elif hasattr(config, "__getitem__"):
            self.config = config
        else:
            try:
                self.config = dict(config) if config else self._default_config()
            except:
                self.config = self._default_config()

        # LLM for intent classification
        self.intent_llm = None

        # Workflow checker (optional - for database-driven workflows)
        self._workflow_checker = None

    async def initialize(self):
        """Initialize the intent analyzer with LLM."""
        try:
            # Initialize LLM for intent classification
            self.intent_llm = OpenAIAugmentedLLM(
                model="gpt-4o-mini",  # Fast and efficient for classification
                temperature=0.1,  # Low temperature for consistent classification
                max_tokens=1000,  # Sufficient for intent analysis
            )

            self.logger.info(
                "🧠 Intent analyzer initialized with LLM-based classification"
            )

        except Exception as e:
            self.logger.error(f"Failed to initialize intent analyzer: {e}")
            raise

    def set_workflow_checker(self, workflow_checker):
        """Inject workflow checker dependency (optional)."""
        self._workflow_checker = workflow_checker

    async def analyze(self, message: IncomingMessage) -> Intent:
        """
        Analyze user intent using LLM-based classification.

        This is much more intelligent than pattern matching and can understand
        context, nuance, and complex requests.
        """
        text = message.text
        context = message.context

        self.logger.info(f"🧠 Analyzing intent with LLM for: {text[:50]}...")

        try:
            # Get available agents and tools for context
            available_agents = await self._get_available_agents()
            available_tools = self._get_available_tools()

            # Build intent classification prompt
            intent_prompt = self._build_intent_classification_prompt(
                text, context, available_agents, available_tools
            )

            # Get intent classification from LLM
            intent_response = await self.intent_llm.generate_str(intent_prompt)

            # Parse the LLM response
            intent_data = self._parse_intent_response(intent_response)

            # Create Intent object
            intent = Intent(
                name=intent_data["intent_name"],
                required_agents=intent_data["required_agents"],
                execution_strategy=ExecutionStrategy[intent_data["execution_strategy"]],
                confidence=ConfidenceLevel[intent_data["confidence"]],
                reasoning=intent_data["reasoning"],
                priority=intent_data.get("priority", "medium"),
                estimated_tasks=intent_data.get("estimated_tasks", 1),
                payload=intent_data.get("payload", {}),
            )

            self.logger.info(
                f"✅ LLM classified intent: {intent.name} "
                f"(confidence: {intent.confidence.value}, "
                f"strategy: {intent.execution_strategy.value})"
            )

            return intent

        except Exception as e:
            self.logger.error(f"LLM intent analysis failed: {e}")
            # Fallback to simple classification
            return self._fallback_intent_classification(text, context)

    def _build_intent_classification_prompt(
        self,
        text: str,
        context,
        available_agents: List[str],
        available_tools: List[str],
    ) -> str:
        """Build a comprehensive prompt for LLM intent classification."""

        return f"""You are an expert intent classifier for a multi-agent AI system. Analyze the user's message and classify their intent.

USER MESSAGE: "{text}"

CONTEXT:
- User ID: {context.user_id}
- Channel: {context.channel_id} 
- Platform: {context.platform}
- Timestamp: {context.timestamp}

AVAILABLE AGENTS:
{json.dumps(available_agents, indent=2)}

AVAILABLE TOOLS:
{json.dumps(available_tools[:20], indent=2)}  # Limit for prompt size

INTENT CLASSIFICATION GUIDELINES:

1. COMMON INTENT CATEGORIES:
   - greeting: Simple greetings, casual conversation
   - question: Asking for information, explanations, definitions
   - task_request: Asking to perform a specific task or action
   - data_analysis: Requesting analysis of data, metrics, reports
   - workflow_automation: Setting up processes, automations, integrations
   - feedback: Providing feedback, suggestions, bug reports
   - capability_inquiry: Asking what the system can do
   - technical_support: Help with technical issues, troubleshooting
   - creative_request: Writing, content creation, brainstorming
   - research: Information gathering, investigation, fact-finding

2. EXECUTION STRATEGIES:
   - SINGLE_AGENT: One agent can handle this completely
   - ORCHESTRATED: Multiple agents need to collaborate
   - WORKFLOW: Database-driven multi-step process
   - DYNAMIC_DISCOVERY: Need to discover new tools/capabilities

3. CONFIDENCE LEVELS:
   - HIGH: Very clear intent, obvious classification
   - MEDIUM: Reasonably clear but some ambiguity
   - LOW: Unclear or ambiguous request

4. AGENT SELECTION:
   Choose the most appropriate agents based on the request:
   - data_researcher: General research, API calls, data gathering
   - knowledge_agent: Factual questions, definitions, explanations
   - capability_inspector: System capabilities, tool discovery
   - feedback_collector: User feedback, suggestions, issues
   - financial_analyst: Business metrics, financial analysis
   - code_developer: Programming, development, technical tasks
   - airtable_manager: Database operations, record management
   - automation_specialist: Workflow automation, integrations

RESPONSE FORMAT (JSON):
{{
    "intent_name": "specific_intent_name",
    "intent_category": "category_from_list_above", 
    "required_agents": ["agent1", "agent2"],
    "execution_strategy": "SINGLE_AGENT|ORCHESTRATED|WORKFLOW|DYNAMIC_DISCOVERY",
    "confidence": "HIGH|MEDIUM|LOW",
    "reasoning": "Clear explanation of why this classification was chosen",
    "priority": "high|medium|low",
    "estimated_tasks": 1,
    "payload": {{
        "key_entities": ["extracted", "entities"],
        "intent_details": "additional context"
    }}
}}

Analyze the user message and provide a JSON response with intelligent intent classification."""

    async def _get_available_agents(self) -> List[str]:
        """Get list of available agents."""
        try:
            if self.registry:
                agent_specs = await self.registry.get_agent_specs()
                return list(agent_specs.keys())
            else:
                # Default agents if registry not available
                return [
                    "data_researcher",
                    "knowledge_agent",
                    "capability_inspector",
                    "feedback_collector",
                    "financial_analyst",
                    "code_developer",
                    "airtable_manager",
                    "automation_specialist",
                ]
        except Exception as e:
            self.logger.warning(f"Could not get available agents: {e}")
            return ["data_researcher"]  # Safe fallback

    def _get_available_tools(self) -> List[str]:
        """Get list of available tools."""
        try:
            if self.tool_discovery:
                catalog = self.tool_discovery.get_cached_catalog()
                if hasattr(catalog, "tools"):
                    return [
                        tool.name for tool in catalog.tools[:50]
                    ]  # Limit for prompt
                elif isinstance(catalog, dict) and "tools" in catalog:
                    return [
                        tool.get("name", "unknown") for tool in catalog["tools"][:50]
                    ]

            # Default tools if discovery not available
            return [
                "web_search",
                "file_operations",
                "data_analysis",
                "api_calls",
                "database_queries",
                "email_sending",
            ]
        except Exception as e:
            self.logger.warning(f"Could not get available tools: {e}")
            return ["basic_tools"]

    def _parse_intent_response(self, response: str) -> Dict[str, Any]:
        """Parse the LLM intent classification response."""
        try:
            # Extract JSON from response
            json_start = response.find("{")
            json_end = response.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_str = response[json_start:json_end]
                intent_data = json.loads(json_str)

                # Validate required fields
                required_fields = [
                    "intent_name",
                    "required_agents",
                    "execution_strategy",
                    "confidence",
                    "reasoning",
                ]
                for field in required_fields:
                    if field not in intent_data:
                        raise ValueError(f"Missing required field: {field}")

                # Ensure required_agents is a list
                if isinstance(intent_data["required_agents"], str):
                    intent_data["required_agents"] = [intent_data["required_agents"]]

                return intent_data
            else:
                raise ValueError("No valid JSON found in response")

        except Exception as e:
            self.logger.error(f"Failed to parse intent response: {e}")
            self.logger.debug(f"Raw response: {response}")

            # Return fallback intent
            return {
                "intent_name": "parsing_error",
                "required_agents": ["data_researcher"],
                "execution_strategy": "SINGLE_AGENT",
                "confidence": "LOW",
                "reasoning": f"Failed to parse LLM response: {str(e)}",
                "priority": "medium",
                "estimated_tasks": 1,
                "payload": {"error": str(e)},
            }

    def _fallback_intent_classification(self, text: str, context) -> Intent:
        """Fallback intent classification when LLM fails."""
        text_lower = text.lower()

        # Simple keyword-based fallback
        if any(word in text_lower for word in ["hello", "hi", "hey", "how are you"]):
            agent_type = "data_researcher"
            intent_name = "greeting"
            reasoning = "Simple greeting detected"
        elif any(
            word in text_lower for word in ["what", "how", "why", "explain", "define"]
        ):
            agent_type = "knowledge_agent"
            intent_name = "question"
            reasoning = "Question detected"
        elif any(
            word in text_lower for word in ["help", "capabilities", "what can you"]
        ):
            agent_type = "capability_inspector"
            intent_name = "capability_inquiry"
            reasoning = "Capability inquiry detected"
        else:
            agent_type = "data_researcher"
            intent_name = "general_request"
            reasoning = "General request - using default agent"

        return Intent(
            name=intent_name,
            required_agents=[agent_type],
            execution_strategy=ExecutionStrategy.SINGLE_AGENT,
            confidence=ConfidenceLevel.LOW,
            reasoning=f"Fallback classification: {reasoning}",
            priority="medium",
            estimated_tasks=1,
        )

    def _default_config(self) -> Dict[str, Any]:
        """Default configuration for the intent analyzer."""
        return {
            "llm_model": "gpt-4o-mini",
            "llm_temperature": 0.1,
            "llm_max_tokens": 1000,
            "enable_workflow_triggers": True,
            "cache_ttl_seconds": 300,
        }

    async def health_check(self) -> Dict[str, Any]:
        """Check component health."""
        return {
            "status": "healthy",
            "component": self.name,
            "llm_available": self.intent_llm is not None,
            "workflow_checker": self._workflow_checker is not None,
            "registry_available": self.registry is not None,
            "tool_discovery_available": self.tool_discovery is not None,
        }
