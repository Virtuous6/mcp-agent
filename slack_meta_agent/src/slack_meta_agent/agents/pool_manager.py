"""
Pool Manager Agent - Handles agent pooling and lifecycle management.

This component is responsible for:
1. Maintaining pools of pre-warmed agents
2. Health checking and recovery
3. Request isolation and conversation management
4. Performance optimization through connection reuse

Extracted from SlackMetaAgent to provide clean separation of concerns.
"""

import asyncio
from datetime import datetime
from typing import Dict, Optional, Any

from mcp_agent.agents.agent import Agent

from ..core.types import AgentSpec, AgentComponent


class PoolManagerAgent(AgentComponent):
    """
    Manages pools of pre-warmed agents for optimal performance.

    Provides connection pooling, health monitoring, and request isolation
    while maintaining high availability and performance.
    """

    def __init__(
        self,
        registry=None,
        mcp_app=None,
        pool_size: int = 10,
        health_check_interval: int = 300,  # 5 minutes
        recovery_delay: int = 30,
    ):  # 30 seconds
        super().__init__("PoolManager")

        self.registry = registry
        self.mcp_app = mcp_app
        self.pool_size = pool_size
        self.health_check_interval = health_check_interval
        self.recovery_delay = recovery_delay

        # Agent pools
        self.agent_pool: Dict[str, Agent] = {}
        self.agent_last_health_check: Dict[str, datetime] = {}
        self.agent_usage_stats: Dict[str, Dict[str, Any]] = {}

        # Request-level conversation isolation
        self.request_conversations: Dict[str, Dict[str, Any]] = {}

        # Pool state
        self.pool_initialized = False
        self.initialization_lock = asyncio.Lock()

        # Performance metrics
        self.pool_hits = 0
        self.pool_misses = 0
        self.health_check_failures = 0
        self.recoveries_performed = 0

    async def initialize(self) -> bool:
        """Initialize the agent pool with commonly used agents."""
        async with self.initialization_lock:
            if self.pool_initialized:
                return True

            try:
                self.logger.info("🔄 Initializing connection-pooled agents...")

                # Common agents to pre-warm
                common_agents = [
                    "data_researcher",  # Weather, news, real-time data
                    "knowledge_agent",  # Basic Q&A
                    "capability_inspector",  # System introspection
                    "airtable_manager",  # Airtable operations (if available)
                    "automation_specialist",  # Workflow automation (if available)
                ]

                initialized_count = 0
                for agent_type in common_agents:
                    try:
                        if await self._initialize_pooled_agent(agent_type):
                            initialized_count += 1
                    except Exception as e:
                        self.logger.warning(f"Could not initialize {agent_type}: {e}")

                self.pool_initialized = True
                self.logger.info(
                    f"🔄 Pool ready with {initialized_count}/{len(common_agents)} agents"
                )

                # Start background health checking
                asyncio.create_task(self._health_check_loop())

                return True

            except Exception as e:
                self.logger.error(f"Failed to initialize agent pool: {e}")
                return False

    async def get_agent(self, agent_type: str, request_id: str) -> Agent:
        """
        Get an agent from the pool or create a new one.

        Args:
            agent_type: Type of agent needed
            request_id: Unique request identifier for isolation

        Returns:
            Agent instance ready for use
        """
        # Ensure pool is initialized
        if not self.pool_initialized:
            await self.initialize()

        # Get agent spec from registry
        agent_spec = None
        if self.registry:
            try:
                agent_spec = await self.registry.get_agent_spec(agent_type)
            except Exception as e:
                self.logger.warning(
                    f"Could not get agent spec from registry for {agent_type}: {e}"
                )

        # Fallback to default specs if registry lookup fails
        if not agent_spec:
            agent_spec = self._get_default_agent_spec(agent_type)

        if not agent_spec:
            raise ValueError(f"Unknown agent type: {agent_type}")

        # Try to get from pool first
        if agent_type in self.agent_pool:
            agent = self.agent_pool[agent_type]

            # Check if agent is healthy
            if await self._is_agent_healthy(agent_type, agent):
                self.pool_hits += 1
                # Initialize isolated conversation context
                self._initialize_request_conversation(request_id, agent_type)
                self._update_usage_stats(agent_type, "pool_hit")

                self.logger.info(
                    f"♻️ Using pooled {agent_type} agent for request {request_id}"
                )
                return agent

        # Pool miss - create new agent
        self.pool_misses += 1
        self._update_usage_stats(agent_type, "pool_miss")

        self.logger.info(f"🆕 Creating new {agent_type} agent (not in pool)")

        if self.mcp_app:
            # Create agent with MCPApp context
            agent = Agent(
                name=f"temp_{agent_spec.name}_{request_id}",
                instruction=agent_spec.instruction,
                server_names=agent_spec.server_names,
                context=self.mcp_app.context,
            )
        else:
            # Fallback without MCPApp
            agent = Agent(
                name=f"temp_{agent_spec.name}_{request_id}",
                instruction=agent_spec.instruction,
                server_names=agent_spec.server_names,
            )

        # Initialize isolated conversation context
        self._initialize_request_conversation(request_id, agent_type)
        return agent

    async def return_agent(
        self, agent_type: str, agent: Agent, request_id: str, success: bool = True
    ) -> None:
        """
        Return an agent to the pool after use.

        Args:
            agent_type: Type of agent
            agent: Agent instance
            request_id: Request identifier
            success: Whether the request was successful
        """
        # Clean up request conversation
        self._cleanup_request_conversation(request_id)

        # Update usage statistics
        self._update_usage_stats(agent_type, "returned", success)

        # If this was a pooled agent, it stays in the pool
        if agent_type in self.agent_pool and self.agent_pool[agent_type] == agent:
            self.logger.debug(f"🔄 Returned pooled {agent_type} agent to pool")
        else:
            # This was a temporary agent - clean it up
            try:
                await agent.__aexit__(None, None, None)
                self.logger.debug(f"🧹 Cleaned up temporary {agent_type} agent")
            except Exception as e:
                self.logger.warning(f"Error cleaning up temporary agent: {e}")

    def get_request_conversation(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get conversation context for a specific request."""
        return self.request_conversations.get(request_id)

    async def cleanup(self) -> None:
        """Clean up all pooled agents and resources."""
        self.logger.info("🧹 Cleaning up agent pool...")

        cleanup_tasks = []
        for agent_type, agent in self.agent_pool.items():
            try:
                cleanup_tasks.append(agent.__aexit__(None, None, None))
            except Exception as e:
                self.logger.warning(f"Error scheduling cleanup for {agent_type}: {e}")

        # Wait for all cleanup tasks
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)

        self.agent_pool.clear()
        self.agent_last_health_check.clear()
        self.request_conversations.clear()

        self.logger.info("✅ Agent pool cleanup complete")

    async def get_metrics(self) -> Dict[str, Any]:
        """Get pool manager performance metrics."""
        total_requests = self.pool_hits + self.pool_misses
        hit_ratio = self.pool_hits / total_requests if total_requests > 0 else 0

        return {
            "pool_size": len(self.agent_pool),
            "pool_hits": self.pool_hits,
            "pool_misses": self.pool_misses,
            "hit_ratio": hit_ratio,
            "health_check_failures": self.health_check_failures,
            "recoveries_performed": self.recoveries_performed,
            "active_requests": len(self.request_conversations),
            "usage_stats": self.agent_usage_stats.copy(),
            "pool_initialized": self.pool_initialized,
        }

    # Private methods

    async def _initialize_pooled_agent(
        self, agent_type: str, agent_spec: Optional[AgentSpec] = None
    ) -> bool:
        """Initialize a single pooled agent."""
        if agent_type in self.agent_pool:
            return True  # Already initialized

        try:
            # Use provided spec or create a basic one
            if not agent_spec:
                # Create basic spec for common agents
                agent_spec = self._get_default_agent_spec(agent_type)
                if not agent_spec:
                    return False

            if self.mcp_app:
                # Create agent with MCPApp context
                agent = Agent(
                    name=f"pooled_{agent_spec.name}",
                    instruction=agent_spec.instruction,
                    server_names=agent_spec.server_names,
                    context=self.mcp_app.context,
                )
                # Initialize the agent with persistent connections
                await agent.__aenter__()
            else:
                # Fallback without MCPApp
                agent = Agent(
                    name=f"pooled_{agent_spec.name}",
                    instruction=agent_spec.instruction,
                    server_names=agent_spec.server_names,
                )
                # Initialize the agent with persistent connections
                await agent.__aenter__()

            self.agent_pool[agent_type] = agent
            self.agent_last_health_check[agent_type] = datetime.now()
            self._initialize_usage_stats(agent_type)

            return True

        except Exception as e:
            self.logger.warning(f"Failed to initialize pooled agent {agent_type}: {e}")
            return False

    async def _is_agent_healthy(self, agent_type: str, agent: Agent) -> bool:
        """Check if an agent is healthy and responsive."""
        last_check = self.agent_last_health_check.get(agent_type, datetime.min)
        now = datetime.now()

        # Only check if enough time has passed
        if (now - last_check).total_seconds() < self.health_check_interval:
            return True

        try:
            # Get agent spec to determine servers
            agent_spec = self._get_default_agent_spec(agent_type)
            if agent_spec and agent_spec.server_names:
                # Simple health check - try to list tools from first server
                server_name = agent_spec.server_names[0]
                await asyncio.wait_for(agent.list_tools(server_name), timeout=5.0)

            self.agent_last_health_check[agent_type] = now
            self.logger.debug(f"✅ Health check passed for pooled {agent_type}")
            return True

        except Exception as e:
            self.logger.warning(f"❌ Health check failed for {agent_type}: {e}")
            self.health_check_failures += 1
            return False

    async def _recover_pooled_agent(self, agent_type: str) -> bool:
        """Recover a failed pooled agent."""
        self.logger.info(f"🔄 Recovering pooled agent: {agent_type}")

        # Clean up the old agent
        if agent_type in self.agent_pool:
            try:
                await self.agent_pool[agent_type].__aexit__(None, None, None)
            except:  # noqa: E722
                pass  # Ignore cleanup errors
            del self.agent_pool[agent_type]

        # Wait before recovery attempt
        await asyncio.sleep(self.recovery_delay)

        # Recreate the agent
        success = await self._initialize_pooled_agent(agent_type)
        if success:
            self.recoveries_performed += 1
            self.logger.info(f"✅ Recovered pooled {agent_type} agent")
        else:
            self.logger.error(f"❌ Failed to recover pooled {agent_type} agent")

        return success

    async def _health_check_loop(self) -> None:
        """Background health checking loop."""
        while True:
            try:
                await asyncio.sleep(self.health_check_interval)

                if not self.pool_initialized:
                    continue

                # Check all pooled agents
                failed_agents = []
                for agent_type, agent in list(self.agent_pool.items()):
                    if not await self._is_agent_healthy(agent_type, agent):
                        failed_agents.append(agent_type)

                # Recover failed agents
                for agent_type in failed_agents:
                    asyncio.create_task(self._recover_pooled_agent(agent_type))

            except Exception as e:
                self.logger.error(f"Error in health check loop: {e}")

    def _initialize_request_conversation(
        self, request_id: str, agent_type: str
    ) -> None:
        """Initialize isolated conversation context for a request."""
        self.request_conversations[request_id] = {
            "agent_type": agent_type,
            "conversation_history": [],
            "context_memory": {},
            "start_time": datetime.now(),
        }
        self.logger.debug(
            f"🔒 Initialized isolated conversation for request {request_id}"
        )

    def _cleanup_request_conversation(self, request_id: str) -> None:
        """Clean up conversation context after request completes."""
        if request_id in self.request_conversations:
            del self.request_conversations[request_id]
            self.logger.debug(f"🧹 Cleaned up conversation for request {request_id}")

    def _initialize_usage_stats(self, agent_type: str) -> None:
        """Initialize usage statistics for an agent type."""
        self.agent_usage_stats[agent_type] = {
            "pool_hits": 0,
            "pool_misses": 0,
            "returns": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "last_used": None,
            "total_usage_time": 0.0,
        }

    def _update_usage_stats(
        self, agent_type: str, event: str, success: bool = True
    ) -> None:
        """Update usage statistics for an agent."""
        if agent_type not in self.agent_usage_stats:
            self._initialize_usage_stats(agent_type)

        stats = self.agent_usage_stats[agent_type]

        if event == "pool_hit":
            stats["pool_hits"] += 1
            stats["last_used"] = datetime.now()
        elif event == "pool_miss":
            stats["pool_misses"] += 1
            stats["last_used"] = datetime.now()
        elif event == "returned":
            stats["returns"] += 1
            if success:
                stats["successful_requests"] += 1
            else:
                stats["failed_requests"] += 1

    def _get_default_agent_spec(self, agent_type: str) -> Optional[AgentSpec]:
        """Get default agent specification for common agent types."""
        default_specs = {
            "data_researcher": AgentSpec(
                name="data_researcher",
                instruction="Research specialist with web search and data analysis tools.",
                server_names=["fetch", "brave_search", "supabase"],
                capabilities=["research", "data_analysis", "report_generation"],
            ),
            "knowledge_agent": AgentSpec(
                name="knowledge_agent",
                instruction="Knowledgeable assistant for factual questions using training data.",
                server_names=[],
                capabilities=["factual_knowledge", "definitions", "basic_qa"],
            ),
            "capability_inspector": AgentSpec(
                name="capability_inspector",
                instruction="System introspection specialist for analyzing bot capabilities.",
                server_names=[],
                capabilities=[
                    "system_introspection",
                    "capability_enumeration",
                    "tool_listing",
                ],
            ),
            "airtable_manager": AgentSpec(
                name="airtable_manager",
                instruction="Airtable database specialist for record management.",
                server_names=["Airtable", "fetch"],
                capabilities=[
                    "airtable_operations",
                    "database_management",
                    "record_management",
                ],
            ),
            "automation_specialist": AgentSpec(
                name="automation_specialist",
                instruction="Workflow automation expert with n8n and Airtable access.",
                server_names=["Airtable", "supabase", "fetch"],
                capabilities=[
                    "workflow_automation",
                    "data_synchronization",
                    "process_automation",
                ],
            ),
        }

        return default_specs.get(agent_type)

    async def health_check(self) -> Dict[str, Any]:
        """Check component health."""
        base_health = await super().health_check()
        metrics = await self.get_metrics()

        # Determine overall health status
        total_requests = metrics["pool_hits"] + metrics["pool_misses"]
        failure_rate = metrics["health_check_failures"] / max(total_requests, 1)

        status = "healthy"
        if failure_rate > 0.1:  # More than 10% failure rate
            status = "degraded"
        elif not metrics["pool_initialized"]:
            status = "initializing"

        return {
            **base_health,
            "status": status,
            **metrics,
            "failure_rate": failure_rate,
            "pool_health": {
                agent_type: {
                    "last_check": self.agent_last_health_check.get(agent_type),
                    "in_pool": agent_type in self.agent_pool,
                }
                for agent_type in self.agent_usage_stats.keys()
            },
        }
