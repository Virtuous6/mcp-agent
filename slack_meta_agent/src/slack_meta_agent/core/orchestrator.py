"""
Orchestrator - The central brain that coordinates all micro-agents.

This component is responsible for:
1. Receiving normalized messages from adapters
2. Coordinating intent analysis, agent discovery, tool discovery, etc.
3. Managing execution strategies and routing
4. Collecting results and returning them to adapters

This is the "brain" that pulls all other components together.
"""

from datetime import datetime
from typing import Dict, Optional, Any

from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
from mcp_agent.workflows.orchestrator.orchestrator import (
    Orchestrator as MCPOrchestrator,
)

from .types import (
    IncomingMessage,
    ExecutionResult,
    Intent,
    ExecutionStrategy,
    ConfidenceLevel,
    AgentComponent,
)
from ..agents.intent_analyzer import IntentAnalyzerAgent
from ..agents.registry import AgentRegistryAgent
from ..agents.tool_discovery import ToolDiscoveryAgent
from ..agents.pool_manager import PoolManagerAgent
from ..agents.workflow_manager import WorkflowManagerAgent


class Orchestrator(AgentComponent):
    """
    The central orchestrator that coordinates all micro-agents.

    This is the main "brain" that receives messages from adapters,
    coordinates the various specialized agents, and returns results.
    """

    def __init__(
        self,
        intent_analyzer: IntentAnalyzerAgent,
        registry: AgentRegistryAgent,
        tool_discovery: ToolDiscoveryAgent,
        pool_manager: PoolManagerAgent,
        workflow_manager: WorkflowManagerAgent,
        mcp_app=None,
    ):
        super().__init__("Orchestrator")

        # Core components
        self.intent_analyzer = intent_analyzer
        self.registry = registry
        self.tool_discovery = tool_discovery
        self.pool_manager = pool_manager
        self.workflow_manager = workflow_manager
        self.mcp_app = mcp_app

        # State tracking
        self.active_requests: Dict[str, Dict] = {}
        self.execution_metrics: Dict[str, Any] = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "average_execution_time": 0.0,
        }

    async def handle(self, incoming: IncomingMessage) -> ExecutionResult:
        """
        Main entry point - handle an incoming message and return a result.

        This is the core coordination method that orchestrates all other components.
        """
        start_time = datetime.now()
        request_id = f"{incoming.context.user_id}_{int(start_time.timestamp())}"

        try:
            self.logger.info(
                f"🎯 Orchestrating request {request_id}: {incoming.text[:50]}..."
            )

            # Track the request
            self.active_requests[request_id] = {
                "start_time": start_time,
                "message": incoming.text,
                "context": incoming.context,
                "status": "analyzing",
            }
            self.execution_metrics["total_requests"] += 1

            # Step 1: Analyze intent using multiple strategies
            intent = await self._analyze_intent(incoming)
            self.active_requests[request_id]["intent"] = intent
            self.active_requests[request_id]["status"] = "routing"

            # Step 2: Execute based on intent strategy
            if intent.execution_strategy == ExecutionStrategy.WORKFLOW:
                result = await self._execute_workflow_strategy(
                    intent, incoming, request_id
                )
            elif intent.execution_strategy == ExecutionStrategy.DYNAMIC_DISCOVERY:
                result = await self._execute_dynamic_discovery_strategy(
                    intent, incoming, request_id
                )
            elif intent.execution_strategy == ExecutionStrategy.SINGLE_AGENT:
                result = await self._execute_single_agent_strategy(
                    intent, incoming, request_id
                )
            elif intent.execution_strategy == ExecutionStrategy.ORCHESTRATED:
                result = await self._execute_orchestrated_strategy(
                    intent, incoming, request_id
                )
            elif intent.execution_strategy == ExecutionStrategy.HUMAN_INPUT:
                result = await self._execute_human_input_strategy(
                    intent, incoming, request_id
                )
            else:
                result = await self._execute_fallback_strategy(
                    intent, incoming, request_id
                )

            # Step 3: Calculate metrics and cleanup
            execution_time = (datetime.now() - start_time).total_seconds()

            # Update metrics
            self.execution_metrics["successful_requests"] += 1
            self._update_average_execution_time(execution_time)

            # Create final result
            final_result = ExecutionResult(
                response=result,
                execution_time=execution_time,
                intent_confidence=intent.confidence,
                complexity=intent.complexity,
                agent_count=len(intent.required_agents),
                metadata={
                    "request_id": request_id,
                    "execution_strategy": intent.execution_strategy.value,
                    "intent_name": intent.intent_name,
                    "reasoning": intent.reasoning,
                },
            )

            self.logger.info(
                f"✅ Request {request_id} completed in {execution_time:.2f}s "
                f"(strategy: {intent.execution_strategy.value})"
            )

            return final_result

        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            self.execution_metrics["failed_requests"] += 1

            self.logger.error(
                f"❌ Request {request_id} failed after {execution_time:.2f}s: {e}"
            )

            return ExecutionResult(
                response=f"I encountered an error processing your request: {str(e)}",
                execution_time=execution_time,
                intent_confidence=ConfidenceLevel.LOW,
                complexity="error",
                agent_count=0,
                metadata={
                    "request_id": request_id,
                    "error": str(e),
                    "execution_strategy": "error_fallback",
                },
            )

        finally:
            # Cleanup request tracking
            if request_id in self.active_requests:
                del self.active_requests[request_id]

    async def _analyze_intent(self, incoming: IncomingMessage) -> Intent:
        """Analyze user intent using the Intent Analyzer."""
        try:
            # Convert context to dict for intent analyzer
            context = {
                "user_id": incoming.context.user_id,
                "channel_id": incoming.context.channel_id,
                "platform": incoming.context.platform,
                "timestamp": incoming.context.timestamp,
            }

            return await self.intent_analyzer.analyze(incoming.text, context)

        except Exception as e:
            self.logger.error(f"Intent analysis failed: {e}")

            # Fallback intent
            return Intent(
                required_agents=["data_researcher"],
                execution_strategy=ExecutionStrategy.SINGLE_AGENT,
                confidence=ConfidenceLevel.LOW,
                complexity="fallback",
                task_description="Fallback task due to intent analysis failure",
                reasoning=f"Intent analysis failed: {str(e)}",
                intent_name="fallback",
                metadata={"error": str(e)},
            )

    async def _execute_workflow_strategy(
        self, intent: Intent, incoming: IncomingMessage, request_id: str
    ) -> str:
        """Execute database-driven workflow strategy."""
        try:
            self.logger.info(f"🔄 Executing workflow strategy for {request_id}")

            workflow = intent.metadata.get("workflow")
            if not workflow:
                return "❌ Workflow strategy selected but no workflow provided"

            context = {
                "user_id": incoming.context.user_id,
                "channel_id": incoming.context.channel_id,
                "request_id": request_id,
            }

            result = await self.workflow_manager.execute_workflow(
                workflow, incoming.text, context
            )

            return result

        except Exception as e:
            self.logger.error(f"Workflow execution failed: {e}")
            return f"❌ Workflow execution failed: {str(e)}"

    async def _execute_dynamic_discovery_strategy(
        self, intent: Intent, incoming: IncomingMessage, request_id: str
    ) -> str:
        """Execute dynamic MCP server discovery strategy."""
        try:
            self.logger.info(
                f"🔍 Executing dynamic discovery strategy for {request_id}"
            )

            keywords = intent.metadata.get("discovery_keywords", [])
            if not keywords:
                return "❌ Dynamic discovery strategy selected but no keywords provided"

            # Use tool discovery to find matching servers
            servers = await self.tool_discovery.discover_servers_by_keywords(keywords)

            if not servers:
                return f"⚠️ No servers found for keywords: {', '.join(keywords)}"

            # Create dynamic agent with discovered server
            dynamic_agent = await self._create_dynamic_agent(servers[0], request_id)

            if not dynamic_agent:
                return (
                    f"❌ Failed to create agent for server: {servers[0]['server_name']}"
                )

            try:
                async with dynamic_agent:
                    llm = await dynamic_agent.attach_llm(OpenAIAugmentedLLM)

                    prompt = f"""
                    Original request: {incoming.text}
                    
                    You have access to the '{servers[0]["server_name"]}' server.
                    Use the available tools to fulfill the user's request.
                    """

                    result = await llm.generate_str(prompt)
                    return result

            finally:
                # Cleanup dynamic server registration if needed
                await self._cleanup_dynamic_server(servers[0])

        except Exception as e:
            self.logger.error(f"Dynamic discovery execution failed: {e}")
            return f"❌ Dynamic discovery failed: {str(e)}"

    async def _execute_single_agent_strategy(
        self, intent: Intent, incoming: IncomingMessage, request_id: str
    ) -> str:
        """Execute single agent strategy."""
        try:
            self.logger.info(f"🎯 Executing single agent strategy for {request_id}")

            if not intent.required_agents:
                return "❌ Single agent strategy selected but no agent specified"

            agent_type = intent.required_agents[0]

            # Get agent from pool manager
            agent = await self.pool_manager.get_agent(agent_type, request_id)

            if not agent:
                return f"❌ Could not get agent of type: {agent_type}"

            # Special handling for capability inspector
            if agent_type == "capability_inspector":
                return await self._handle_capability_inspection(incoming.text)

            async with agent:
                llm = await agent.attach_llm(OpenAIAugmentedLLM)
                result = await llm.generate_str(incoming.text)
                return result

        except Exception as e:
            self.logger.error(f"Single agent execution failed: {e}")
            return f"❌ Single agent execution failed: {str(e)}"

    async def _execute_orchestrated_strategy(
        self, intent: Intent, incoming: IncomingMessage, request_id: str
    ) -> str:
        """Execute orchestrated multi-agent strategy."""
        try:
            self.logger.info(f"🎭 Executing orchestrated strategy for {request_id}")

            if not intent.required_agents:
                return "❌ Orchestrated strategy selected but no agents specified"

            # Get all required agents
            agents = []
            for agent_type in intent.required_agents:
                agent = await self.pool_manager.get_agent(agent_type, request_id)
                if agent:
                    agents.append(agent)

            if not agents:
                return "❌ Could not get any agents for orchestrated execution"

            # Use MCP orchestrator for complex multi-agent workflows
            orchestrator = MCPOrchestrator(
                llm_factory=OpenAIAugmentedLLM,
                available_agents=agents,
                plan_type="full",
            )

            task_description = f"""
            Original request: {incoming.text}
            
            Task analysis: {intent.task_description}
            Required capabilities: {", ".join([agent.name for agent in agents])}
            
            Execute this request using the available specialized agents and provide actionable results.
            """

            self.logger.info(
                f"🎯 Orchestrating {len(agents)} agents for complex workflow"
            )
            result = await orchestrator.generate_str(message=task_description)
            return result

        except Exception as e:
            self.logger.error(f"Orchestrated execution failed: {e}")
            return await self._execute_sequential_fallback(intent, incoming, request_id)

    async def _execute_human_input_strategy(
        self, intent: Intent, incoming: IncomingMessage, request_id: str
    ) -> str:
        """Execute human input strategy."""
        try:
            self.logger.info(f"🤖 Executing human input strategy for {request_id}")

            # This would typically be handled by specialized workflow agents
            # For now, route to a standard agent that can handle human input
            agent_type = (
                intent.required_agents[0]
                if intent.required_agents
                else "data_researcher"
            )

            agent = await self.pool_manager.get_agent(agent_type, request_id)
            if not agent:
                return f"❌ Could not get agent for human input handling: {agent_type}"

            async with agent:
                llm = await agent.attach_llm(OpenAIAugmentedLLM)
                result = await llm.generate_str(incoming.text)
                return result

        except Exception as e:
            self.logger.error(f"Human input execution failed: {e}")
            return f"❌ Human input execution failed: {str(e)}"

    async def _execute_fallback_strategy(
        self, intent: Intent, incoming: IncomingMessage, request_id: str
    ) -> str:
        """Execute fallback strategy when others fail."""
        self.logger.warning(f"⚠️ Using fallback strategy for {request_id}")

        # Try to use data_researcher as a general-purpose fallback
        try:
            agent = await self.pool_manager.get_agent("data_researcher", request_id)
            if agent:
                async with agent:
                    llm = await agent.attach_llm(OpenAIAugmentedLLM)
                    result = await llm.generate_str(incoming.text)
                    return result
        except Exception as e:
            self.logger.error(f"Fallback execution failed: {e}")

        return "I'm having trouble processing your request right now. Please try again later."

    async def _execute_sequential_fallback(
        self, intent: Intent, incoming: IncomingMessage, request_id: str
    ) -> str:
        """Sequential fallback if orchestration fails."""
        results = []

        for agent_type in intent.required_agents:
            try:
                agent = await self.pool_manager.get_agent(agent_type, request_id)
                if agent:
                    async with agent:
                        llm = await agent.attach_llm(OpenAIAugmentedLLM)
                        result = await llm.generate_str(
                            f"Handle this request with your specialized capabilities: {incoming.text}"
                        )
                        results.append(f"{agent_type}: {result}")
            except Exception as e:
                results.append(f"{agent_type}: Error - {str(e)}")

        return "\n\n".join(results) if results else "❌ All agents failed to execute"

    async def _handle_capability_inspection(self, message: str) -> str:
        """Handle capability inspection requests."""
        try:
            # Get current tool catalog
            catalog = await self.tool_discovery.get_cached_catalog()

            # Extract tool name if user is searching for specific tool
            tool_name = self._extract_tool_name(message)

            if tool_name:
                # Search for specific tool
                return await self._format_tool_search_results(tool_name, catalog)
            else:
                # General capability overview
                return await self._format_capability_overview(catalog)

        except Exception as e:
            self.logger.error(f"Capability inspection failed: {e}")
            return f"❌ Could not inspect system capabilities: {str(e)}"

    async def _create_dynamic_agent(
        self, server_config: Dict, request_id: str
    ) -> Optional[Agent]:
        """Create a dynamic agent with discovered server."""
        try:
            # This would create a temporary agent with the dynamically discovered server
            # Implementation would depend on the MCP app's server registry
            self.logger.info(
                f"Creating dynamic agent for server: {server_config['server_name']}"
            )

            # For now, return None - this needs integration with MCP app
            return None

        except Exception as e:
            self.logger.error(f"Failed to create dynamic agent: {e}")
            return None

    async def _cleanup_dynamic_server(self, server_config: Dict):
        """Clean up dynamically registered server."""
        try:
            # Clean up any temporary server registrations
            self.logger.debug(
                f"Cleaning up dynamic server: {server_config['server_name']}"
            )
        except Exception as e:
            self.logger.warning(f"Dynamic server cleanup failed: {e}")

    def _extract_tool_name(self, message: str) -> Optional[str]:
        """Extract tool name from message if user is asking about specific tool."""
        import re

        # Simple pattern matching for tool names
        patterns = [
            r"use the ([a-zA-Z0-9_-]+) tool",
            r"find the ([a-zA-Z0-9_-]+) tool",
            r"what.*([a-zA-Z0-9_-]+) tool",
        ]

        for pattern in patterns:
            match = re.search(pattern, message.lower())
            if match:
                tool_name = match.group(1)
                if len(tool_name) > 2:  # Filter out tiny matches
                    return tool_name

        return None

    async def _format_tool_search_results(self, tool_name: str, catalog) -> str:
        """Format search results for a specific tool."""
        # This would search the catalog for the specific tool
        return (
            f"🔍 **Tool Search Results for '{tool_name}'**\n\nSearching tool catalog..."
        )

    async def _format_capability_overview(self, catalog) -> str:
        """Format a general capability overview."""
        specs = await self.registry.get_specs()

        overview = "🤖 **Meta-Agent System Capabilities**\n\n"
        overview += f"**Available Agents:** {len(specs)}\n"
        overview += f"**Total Tools:** {catalog.get('total_tools', 'Unknown') if catalog else 'Unknown'}\n\n"

        overview += "**Specialized Agents:**\n"
        for agent_type, spec in specs.items():
            overview += f"• **{agent_type.replace('_', ' ').title()}**: {spec.instruction.split('.')[0]}\n"

        return overview

    def _update_average_execution_time(self, execution_time: float):
        """Update rolling average execution time."""
        current_avg = self.execution_metrics["average_execution_time"]
        total_requests = self.execution_metrics["successful_requests"]

        # Simple rolling average
        new_avg = (
            (current_avg * (total_requests - 1)) + execution_time
        ) / total_requests
        self.execution_metrics["average_execution_time"] = new_avg

    async def health_check(self) -> Dict[str, Any]:
        """Check orchestrator and all component health."""
        base_health = await super().health_check()

        # Check all components
        component_health = {}
        for name, component in [
            ("intent_analyzer", self.intent_analyzer),
            ("registry", self.registry),
            ("tool_discovery", self.tool_discovery),
            ("pool_manager", self.pool_manager),
            ("workflow_manager", self.workflow_manager),
        ]:
            if component:
                try:
                    component_health[name] = await component.health_check()
                except Exception as e:
                    component_health[name] = {"status": "error", "error": str(e)}
            else:
                component_health[name] = {"status": "missing"}

        return {
            **base_health,
            "active_requests": len(self.active_requests),
            "execution_metrics": self.execution_metrics,
            "mcp_app_available": self.mcp_app is not None,
            "components": component_health,
        }
