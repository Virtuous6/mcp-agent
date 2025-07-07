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
from mcp_agent.agents.agent import Agent

from ..core.types import (
    Intent,
    IncomingMessage,
    ExecutionStrategy,
    ConfidenceLevel,
    AgentComponent,
)
from ..core.llm_factory import SmartLLMFactory, create_smart_llm_factory


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

        # LLM agent for intent classification
        self.intent_agent = None
        self.intent_llm = None

        # Workflow checker (optional - for database-driven workflows)
        self._workflow_checker = None

        # Dynamic discovery components
        self.mcp_discovery = None  # Will be set if discovery is available

    async def initialize(self):
        """Initialize the intent analyzer with Smart LLM Factory."""
        try:
            # Create an agent specifically for intent classification
            # This agent will have its own enhanced configuration from database
            self.intent_agent = Agent(
                name="intent_analyzer_llm",
                instruction="""You are an expert intent classifier for a multi-agent AI system. 
                Your role is to analyze user messages and accurately classify their intent to enable 
                optimal agent selection and execution strategy. You excel at understanding context, 
                nuance, and determining the complexity level of requests.""",
                server_names=[],  # Intent analysis doesn't need MCP servers
                context=getattr(self, "context", None),
            )

            # Add LLM configuration that can be overridden by database settings
            self.intent_agent.llm_config = {
                "model": "gpt-4o-mini",  # Fast and efficient for classification
                "temperature": 0.1,  # Low temperature for consistent classification
                "max_tokens": 1000,  # Sufficient for intent analysis
                "provider": "openai",
            }

            # Use smart LLM factory for database-driven configuration
            llm_factory = create_smart_llm_factory(self.intent_agent)
            self.intent_llm = await self.intent_agent.attach_llm(llm_factory)

            self.logger.info(
                "🧠 Intent analyzer initialized with Smart LLM Factory and database-driven configuration"
            )

        except Exception as e:
            self.logger.error(f"Failed to initialize intent analyzer: {e}")
            raise

    def set_workflow_checker(self, workflow_checker):
        """Inject workflow checker dependency (optional)."""
        self._workflow_checker = workflow_checker

    async def analyze(self, message: IncomingMessage) -> Intent:
        """
        Analyze user intent using LLM-based classification with dynamic MCP discovery.

        This is much more intelligent than pattern matching and can understand
        context, nuance, and complex requests. It also checks for dynamic MCP
        server discovery needs.
        """
        text = message.text
        context = message.context

        self.logger.info(f"🧠 Analyzing intent with LLM for: {text[:50]}...")

        try:
            # 🔍 FIRST: Check for dynamic MCP server discovery needs
            discovery_keywords = self._extract_discovery_keywords_from_message(text)
            if discovery_keywords:
                discovery_intent = await self._check_dynamic_discovery_needs(
                    text, discovery_keywords
                )
                if discovery_intent:
                    self.logger.info(
                        f"🔍 Dynamic MCP discovery detected: {discovery_intent.name} "
                        f"(keywords: {discovery_keywords})"
                    )
                    return discovery_intent

            # 🧠 SECOND: Standard LLM intent classification
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
   - weather_inquiry: Simple weather requests (should be SINGLE_AGENT)

2. EXECUTION STRATEGIES:
   - SINGLE_AGENT: One agent can handle this completely (PREFER THIS for simple requests)
   - ORCHESTRATED: Multiple agents need to collaborate (ONLY for complex multi-step tasks)
   - WORKFLOW: Database-driven multi-step process
   - DYNAMIC_DISCOVERY: Need to discover new tools/capabilities

3. CONFIDENCE LEVELS:
   - HIGH: Very clear intent, obvious classification
   - MEDIUM: Reasonably clear but some ambiguity
   - LOW: Unclear or ambiguous request

4. AGENT SELECTION:
   Choose the most appropriate agents based on the request:
   - data_researcher: General research, API calls, data gathering, weather queries
   - knowledge_agent: Factual questions, definitions, explanations
   - capability_inspector: System capabilities, tool discovery
   - feedback_collector: User feedback, suggestions, issues
   - financial_analyst: Business metrics, financial analysis
   - code_developer: Programming, development, technical tasks
   - airtable_manager: Database operations, record management
   - automation_specialist: Workflow automation, integrations

IMPORTANT GUIDELINES:
- For simple queries (weather, basic facts, single questions), use SINGLE_AGENT strategy
- For weather requests, use data_researcher agent with HIGH confidence
- Only use ORCHESTRATED for genuinely complex multi-step tasks
- Prefer simplicity over complexity

RESPONSE FORMAT (JSON):
{{
    "intent_name": "specific_intent_name",
    "intent_category": "category_from_list_above", 
    "required_agents": ["agent1"],
    "execution_strategy": "SINGLE_AGENT",
    "confidence": "HIGH|MEDIUM|LOW",
    "reasoning": "Clear explanation of why this classification was chosen",
    "priority": "high|medium|low",
    "estimated_tasks": 1,
    "payload": {{
        "key_entities": ["extracted", "entities"],
        "intent_details": "additional context"
    }}
}}

Analyze the user message and provide a JSON response with intelligent intent classification. PREFER SINGLE_AGENT strategy for simple requests."""

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

    # ======= DYNAMIC MCP DISCOVERY METHODS =======

    def _extract_discovery_keywords_from_message(self, message: str) -> List[str]:
        """Extract keywords from message that might indicate need for dynamic MCP server discovery"""
        message_lower = message.lower()
        keywords = []

        # 🎯 PRIORITY: Extract organization-specific qualifiers (like "ARC supabase")
        # Look for patterns like "our [ORG] [service]" or "[ORG] [service]"
        org_service_patterns = [
            r"\b(?:our\s+)?([A-Z]{2,10})\s+(supabase|database|db)\b",  # "our ARC supabase", "ARC supabase"
            r"\b(?:our\s+)?([A-Z]{2,10})\s+(airtable|air table)\b",  # "our ACME airtable"
            r"\b(?:our\s+)?([A-Z]{2,10})\s+(n8n|automation)\b",  # "our CORP n8n"
            r"\b(?:our\s+)?([A-Z]{2,10})\s+(api|webhook|integration)\b",  # "our ORG api"
            r"\b(?:our\s+)?([A-Z]{2,10})\s+(server|service)\b",  # "our ARC server"
            r"\b(arc|advertising)\s+(supabase|database)\b",  # "arc supabase", "advertising database"
            r"\b(arc_supabase|arc_database|ghl_dynamic)\b",  # "arc_supabase", "ghl_dynamic" (compound forms)
        ]

        for pattern in org_service_patterns:
            matches = re.findall(pattern, message, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple) and len(match) == 2:
                    org_name = match[0].lower()  # Organization name (e.g., "arc")
                    service_type = match[1].lower()  # Service type (e.g., "supabase")

                    # Add both individual keywords and compound qualifier
                    keywords.extend([org_name, service_type])
                    keywords.append(
                        f"{org_name}_{service_type}"
                    )  # e.g., "arc_supabase"

                    self.logger.info(
                        f"🎯 Extracted qualified service: {org_name} {service_type}"
                    )
                elif isinstance(match, str):
                    # Handle compound form matches like "arc_supabase"
                    compound_service = match.lower()
                    keywords.append(compound_service)

                    # Also extract parts if it contains underscore
                    if "_" in compound_service:
                        parts = compound_service.split("_")
                        keywords.extend(parts)

                    self.logger.info(
                        f"🎯 Extracted compound service: {compound_service}"
                    )

        # 🔍 SECONDARY: Look for general MCP/service discovery keywords
        discovery_patterns = [
            r"\b(find|search|discover|list)\s+(?:mcp\s+)?(servers?|services?|tools?)\b",
            r"\b(show|get|access)\s+(?:me\s+)?(?:mcp\s+)?(servers?|services?|tools?)\b",
            r"\b(what|which)\s+(?:mcp\s+)?(servers?|services?|tools?)\b",
            r"\b(mcp|server|service)\s+(discovery|exploration|search)\b",
        ]

        for pattern in discovery_patterns:
            if re.search(pattern, message_lower):
                keywords.extend(["mcp", "server", "discovery"])

        # Remove duplicates and empty strings
        keywords = list(set([k for k in keywords if k]))

        if keywords:
            self.logger.info(f"🔍 Extracted discovery keywords: {keywords}")

        return keywords

    async def _check_dynamic_discovery_needs(
        self, message: str, keywords: List[str]
    ) -> Optional[Intent]:
        """Check if the message requires dynamic MCP server discovery"""
        if not keywords:
            return None

        # Check if we have potential database matches for these keywords
        needs_discovery = await self._should_trigger_dynamic_discovery(
            keywords, message
        )

        if needs_discovery:
            return Intent(
                name="dynamic_mcp_discovery",
                required_agents=["data_researcher"],  # Use reliable agent that exists
                execution_strategy=ExecutionStrategy.DYNAMIC_DISCOVERY,
                confidence=ConfidenceLevel.HIGH,
                reasoning=f"Detected organization-specific or MCP server patterns requiring database discovery",
                priority="high",
                estimated_tasks=1,
                payload={
                    "discovery_keywords": keywords,
                    "original_message": message,
                    "qualified_services": any("_" in k for k in keywords),
                },
            )

        return None

    async def _should_trigger_dynamic_discovery(
        self, keywords: List[str], message: str
    ) -> bool:
        """Determine if we should trigger dynamic MCP server discovery"""

        # High-priority triggers
        high_priority_keywords = ["arc_supabase", "arc", "advertising"]
        if any(keyword in keywords for keyword in high_priority_keywords):
            self.logger.info(f"🎯 High-priority discovery trigger detected: {keywords}")
            return True

        # Check for compound organization keywords (like "arc_supabase")
        compound_keywords = [k for k in keywords if "_" in k]
        if compound_keywords:
            self.logger.info(
                f"🔍 Compound organization keywords detected: {compound_keywords}"
            )
            return True

        # Check for explicit MCP discovery requests
        discovery_indicators = [
            "find",
            "search",
            "discover",
            "list",
            "show",
            "get",
            "access",
        ]
        mcp_indicators = ["mcp", "server", "service", "tool"]

        message_lower = message.lower()
        has_discovery = any(word in message_lower for word in discovery_indicators)
        has_mcp = any(word in message_lower for word in mcp_indicators)

        if has_discovery and has_mcp:
            self.logger.info("🔍 Explicit MCP discovery request detected")
            return True

        return False

    async def health_check(self) -> Dict[str, Any]:
        """Check component health."""
        return {
            "status": "healthy",
            "component": self.name,
            "llm_available": self.intent_llm is not None,
            "workflow_checker": self._workflow_checker is not None,
            "registry_available": self.registry is not None,
            "tool_discovery_available": self.tool_discovery is not None,
            "dynamic_discovery_available": self.mcp_discovery is not None,
        }
