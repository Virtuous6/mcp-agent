# ULTRA CLEAN STARTUP: Configure logging FIRST before any imports
import logging
import os

# Set root logger to ERROR level IMMEDIATELY to silence framework noise
logging.getLogger().setLevel(logging.ERROR)

import asyncio
import json
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from mcp_agent.app import MCPApp
from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
from mcp_agent.workflows.orchestrator.orchestrator import Orchestrator
from mcp_agent.human_input.handler import console_input_callback
from mcp_agent.human_input.types import HumanInputRequest
from rich import print

# Import our custom Supabase logging
from supabase_logger import setup_supabase_logging

# Import database configuration system
from supabase_config_loader import get_settings_from_database, DatabaseConfig

# Slack integration imports
try:
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse
    from slack_sdk import WebClient

    SLACK_AVAILABLE = True
except ImportError:
    print("⚠️  Slack SDK not installed. Install with: pip install slack-sdk")
    SLACK_AVAILABLE = False

# Note: MCPApp will be created in main() with database configuration
app = None


@dataclass
class AgentSpec:
    """Specification for creating specialized agents"""

    name: str
    instruction: str
    server_names: List[str]
    capabilities: List[str]


@dataclass
class ConversationTurn:
    """Represents a single conversation turn with quality metrics"""

    turn_number: int
    user_input: str
    agent_response: str
    intent_analysis: Dict
    timestamp: datetime
    quality_metrics: Dict = field(default_factory=dict)
    execution_time: float = 0.0


@dataclass
class ConversationState:
    """Enhanced conversation state management"""

    user_id: str
    channel_id: str
    turns: List[ConversationTurn] = field(default_factory=list)
    current_turn: int = 0
    context_memory: Dict = field(default_factory=dict)
    user_preferences: Dict = field(default_factory=dict)

    def add_turn(self, turn: ConversationTurn):
        """Add a new conversation turn"""
        self.turns.append(turn)
        self.current_turn = len(self.turns)

    def get_recent_context(self, max_turns: int = 3) -> str:
        """Get recent conversation context for better responses"""
        recent_turns = self.turns[-max_turns:] if self.turns else []
        context = []
        for turn in recent_turns:
            context.append(f"User: {turn.user_input}")
            context.append(f"Agent: {turn.agent_response[:200]}...")
        return "\n".join(context)


class SlackMetaAgent:
    """The main meta-agent that orchestrates all other agents and handles Slack interactions"""

    def __init__(self, supabase_project_id: str = None, mcp_app=None):
        self.specialized_agents: Dict[str, Agent] = {}
        self.agent_registry: Dict[str, AgentSpec] = self._initialize_agent_registry()
        self.conversation_memory: Dict[
            str, List[Dict]
        ] = {}  # Legacy - kept for backward compatibility
        self.conversation_states: Dict[
            str, ConversationState
        ] = {}  # New structured state management
        self.active_workflows: Dict[str, Any] = {}
        self.slack_client: Optional[WebClient] = None
        self.socket_client: Optional[SocketModeClient] = None
        self.logger = logging.getLogger("SlackMetaAgent")
        self.event_loop = None
        self.supabase_project_id = supabase_project_id
        self.session_id = None
        self.supabase_log_handler = None
        self.mcp_app = (
            mcp_app  # Store MCPApp instance for proper server registry access
        )

        # Dynamic tool discovery cache
        self.discovered_tools: Optional[Dict[str, Dict]] = None
        self.tools_cache_timestamp: Optional[datetime] = None
        # Cache TTL now dynamic - managed in self.config

        # Human input handling for Slack
        self.pending_human_inputs: Dict[
            str, asyncio.Future
        ] = {}  # user_id -> Future[str]
        self.current_thread_ts: Optional[str] = (
            None  # Track current thread for human input
        )

        # Simplified connection pool with state isolation
        self.agent_pool: Dict[str, Agent] = {}
        self.agent_pool_initialized = False
        self.agent_last_health_check = {}

        # Request-level conversation isolation (key: request_id)
        self.request_conversations: Dict[str, Dict] = {}

        # Simplified configuration
        self.config = {
            "cache_ttl_seconds": 1800,  # 30 minutes
            "pattern_confidence_threshold": 0.8,
            "health_check_interval": 300,  # 5 minutes
            "memory_cleanup_interval": 3600,  # 1 hour
            "learning_persistence_file": "slack_meta_agent/pattern_learning.json",
        }

        # Load persistent learning patterns
        self.dynamic_patterns = self._load_learning_patterns()

        # Simplified usage tracking
        self.agent_usage_stats = {}
        self.last_cleanup_time = datetime.now()

    def _load_learning_patterns(self) -> Dict[str, Dict]:
        """Load persistent learning patterns from file or create defaults"""
        try:
            import json
            import os

            patterns_file = self.config["learning_persistence_file"]
            if os.path.exists(patterns_file):
                with open(patterns_file, "r") as f:
                    loaded_patterns = json.load(f)
                    self.logger.info(
                        f"📚 Loaded {len(loaded_patterns)} learning patterns from {patterns_file}"
                    )
                    return loaded_patterns
        except Exception as e:
            self.logger.warning(f"Could not load learning patterns: {e}")

        # Default patterns if file doesn't exist or fails to load
        default_patterns = {
            "weather": {
                "keywords": [
                    "weather",
                    "temperature",
                    "forecast",
                    "climate",
                    "temp",
                    "rain",
                    "sunny",
                    "cloudy",
                ],
                "agent": "data_researcher",
                "confidence": 0.9,
                "usage_count": 0,
            },
            "knowledge": {
                "keywords": [
                    "what is",
                    "who is",
                    "what does",
                    "define",
                    "explain",
                    "capital of",
                    "meaning of",
                ],
                "agent": "knowledge_agent",
                "confidence": 0.85,
                "usage_count": 0,
            },
            "capabilities": {
                "keywords": [
                    "what tools",
                    "what can you",
                    "capabilities",
                    "what do you have access",
                    "help",
                    "commands",
                ],
                "agent": "capability_inspector",
                "confidence": 0.9,
                "usage_count": 0,
            },
            "financial": {
                "keywords": [
                    "dashboard",
                    "revenue",
                    "financial",
                    "profit",
                    "metrics",
                    "analytics",
                    "sales",
                    "kpi",
                ],
                "agent": "financial_analyst",
                "confidence": 0.8,
                "usage_count": 0,
            },
            "development": {
                "keywords": [
                    "code",
                    "deploy",
                    "develop",
                    "build",
                    "app",
                    "website",
                    "programming",
                    "github",
                ],
                "agent": "code_developer",
                "confidence": 0.8,
                "usage_count": 0,
            },
            "automation": {
                "keywords": [
                    "n8n",
                    "workflow",
                    "automate",
                    "trigger",
                    "airtable",
                    "automation",
                    "zapier",
                ],
                "agent": "automation_specialist",
                "confidence": 0.85,
                "usage_count": 0,
            },
        }

        self.logger.info("📚 Using default learning patterns")
        return default_patterns

    def _save_learning_patterns(self):
        """Persist learning patterns to file"""
        try:
            import json
            import os

            patterns_file = self.config["learning_persistence_file"]
            os.makedirs(os.path.dirname(patterns_file), exist_ok=True)

            with open(patterns_file, "w") as f:
                json.dump(self.dynamic_patterns, f, indent=2)

            self.logger.debug(f"💾 Saved learning patterns to {patterns_file}")
        except Exception as e:
            self.logger.warning(f"Could not save learning patterns: {e}")

    async def _discover_available_tools(self) -> Dict[str, Dict]:
        """Dynamically discover all available tools from connected MCP servers"""
        self.logger.info("🔍 Discovering available tools from MCP servers...")
        import asyncio

        discovered_tools = {}

        # Use MCPApp if available, otherwise fall back to temporary agents
        if self.mcp_app:
            self.logger.info("✅ Using MCPApp context for tool discovery")

            for agent_type, spec in self.agent_registry.items():
                if not spec.server_names:
                    # Agents without servers (like knowledge_agent) - just use their capabilities
                    discovered_tools[agent_type] = {
                        "agent_description": spec.instruction[:200] + "...",
                        "servers": [],
                        "tools": [],
                        "capabilities": spec.capabilities,
                    }
                    continue

                agent_tools = {}
                for server_name in spec.server_names:
                    try:
                        # Create agent with MCPApp context for proper server registry access
                        temp_agent = Agent(
                            name=f"discovery_{server_name}",
                            instruction="Tool discovery agent",
                            server_names=[server_name],
                            context=self.mcp_app.context,  # Pass MCPApp context
                        )

                        async with temp_agent:
                            # Add timeout for individual server discovery
                            tools_result = await asyncio.wait_for(
                                temp_agent.list_tools(server_name), timeout=10.0
                            )
                            capabilities = await asyncio.wait_for(
                                temp_agent.get_capabilities(server_name), timeout=5.0
                            )

                            agent_tools[server_name] = {
                                "tools": [
                                    {
                                        "name": tool.name,
                                        "description": tool.description
                                        or "No description available",
                                        "parameters": getattr(tool, "inputSchema", {}),
                                    }
                                    for tool in tools_result.tools
                                ]
                                if tools_result
                                else [],
                                "capabilities": capabilities.model_dump()
                                if capabilities
                                else {},
                            }

                            # Only log if significant number of tools discovered
                            tool_count = len(agent_tools[server_name]["tools"])
                            if tool_count > 5:  # Only log if meaningful discovery
                                self.logger.info(
                                    f"✅ Discovered {tool_count} tools from {server_name}"
                                )

                    except asyncio.TimeoutError:
                        self.logger.warning(
                            f"⚠️  Tool discovery timed out for {server_name}"
                        )
                        agent_tools[server_name] = {
                            "tools": [],
                            "capabilities": {},
                            "error": f"Timeout connecting to {server_name}",
                        }
                    except Exception as e:
                        self.logger.warning(
                            f"⚠️  Could not discover tools for {server_name}: {e}"
                        )
                        agent_tools[server_name] = {
                            "tools": [],
                            "capabilities": {},
                            "error": str(e),
                        }

                discovered_tools[agent_type] = {
                    "agent_description": spec.instruction[:200] + "...",
                    "servers": spec.server_names,
                    "server_tools": agent_tools,
                    "capabilities": spec.capabilities,
                }
        else:
            # Fallback to old method if no MCPApp available
            self.logger.warning("⚠️  No MCPApp context - using fallback tool discovery")
            for agent_type, spec in self.agent_registry.items():
                discovered_tools[agent_type] = {
                    "agent_description": spec.instruction[:200] + "...",
                    "servers": spec.server_names,
                    "server_tools": {},
                    "capabilities": spec.capabilities,
                    "error": "No MCPApp context available",
                }

        # Log discovery summary only
        total_servers = sum(
            len(info.get("servers", [])) for info in discovered_tools.values()
        )
        self.logger.info(
            f"🎯 Discovery complete: {len(discovered_tools)} agents, {total_servers} servers"
        )
        return discovered_tools

    async def _initialize_agent_pool(self):
        """Initialize connection-pooled agents that will be reused with state isolation"""
        if self.agent_pool_initialized:
            return

        self.logger.info("🔄 Initializing connection-pooled agents...")

        # Initialize commonly used agents with persistent connections
        common_agents = [
            "data_researcher",  # Weather, news, real-time data
            "knowledge_agent",  # Basic Q&A
            "capability_inspector",  # System introspection
        ]

        for agent_type in common_agents:
            try:
                if agent_type not in self.agent_registry:
                    continue

                spec = self.agent_registry[agent_type]

                if self.mcp_app:
                    # Create agent with MCPApp context for proper server registry access
                    agent = Agent(
                        name=f"pooled_{spec.name}",
                        instruction=spec.instruction,
                        server_names=spec.server_names,
                        context=self.mcp_app.context,  # Pass MCPApp context
                    )
                    # Initialize the agent with persistent connections
                    await agent.__aenter__()
                else:
                    # Fallback to direct Agent creation
                    agent = Agent(
                        name=f"pooled_{spec.name}",
                        instruction=spec.instruction,
                        server_names=spec.server_names,
                    )
                    # Initialize the agent with persistent connections
                    await agent.__aenter__()

                self.agent_pool[agent_type] = agent

                # Reduced logging verbosity during startup
                pass  # self.logger.info(f"✅ Initialized pooled {agent_type} agent")

            except Exception as e:
                self.logger.warning(f"Could not initialize {agent_type}: {e}")

        self.agent_pool_initialized = True
        self.logger.info(f"🔄 Connection pool ready with {len(self.agent_pool)} agents")

    async def _health_check_agents(self):
        """Perform health checks on pooled agents and recover if needed"""
        now = datetime.now()

        for agent_type, agent in list(self.agent_pool.items()):
            last_check = self.agent_last_health_check.get(agent_type, datetime.min)

            if (now - last_check).total_seconds() < self.config[
                "health_check_interval"
            ]:
                continue

            try:
                # Simple health check - try to list tools from one server
                spec = self.agent_registry.get(agent_type)
                if spec and spec.server_names:
                    server_name = spec.server_names[0]
                    await agent.list_tools(server_name)

                self.agent_last_health_check[agent_type] = now
                self.logger.debug(f"✅ Health check passed for pooled {agent_type}")

            except Exception as e:
                self.logger.warning(f"❌ Health check failed for {agent_type}: {e}")
                await self._recover_pooled_agent(agent_type)

    async def _recover_pooled_agent(self, agent_type: str):
        """Recover a failed pooled agent"""
        self.logger.info(f"🔄 Recovering pooled agent: {agent_type}")

        # Clean up the old agent
        if agent_type in self.agent_pool:
            try:
                await self.agent_pool[agent_type].__aexit__(None, None, None)
            except:  # noqa: E722
                pass  # Ignore cleanup errors
            del self.agent_pool[agent_type]

        # Recreate the agent
        spec = self.agent_registry[agent_type]

        if self.mcp_app:
            # Create agent with MCPApp context for proper server registry access
            new_agent = Agent(
                name=f"pooled_{spec.name}_recovered",
                instruction=spec.instruction,
                server_names=spec.server_names,
                context=self.mcp_app.context,  # Pass MCPApp context
            )
            # Initialize the new agent
            await new_agent.__aenter__()
        else:
            # Fallback to direct Agent creation
            new_agent = Agent(
                name=f"pooled_{spec.name}_recovered",
                instruction=spec.instruction,
                server_names=spec.server_names,
            )
            # Initialize the new agent
            await new_agent.__aenter__()

        self.agent_pool[agent_type] = new_agent
        self.agent_last_health_check[agent_type] = datetime.now()
        self.logger.info(f"✅ Recovered pooled {agent_type} agent")

    def _get_cache_ttl(self) -> int:
        """Get cache TTL (simplified from complex dynamic management)"""
        return self.config["cache_ttl_seconds"]

    async def _cleanup_memory(self):
        """Periodic memory cleanup and optimization"""
        now = datetime.now()

        if (now - self.last_cleanup_time).total_seconds() < self.config[
            "memory_cleanup_interval"
        ]:
            return

        self.logger.info("🧹 Performing periodic memory cleanup...")

        # Clean up old conversation states (keep only last 24 hours)
        cutoff_time = now - timedelta(hours=24)
        old_conversations = []

        for conv_key, conv_state in self.conversation_states.items():
            if conv_state.turns and conv_state.turns[-1].timestamp < cutoff_time:
                old_conversations.append(conv_key)

        for conv_key in old_conversations:
            del self.conversation_states[conv_key]

        self.logger.info(
            f"🗑️  Cleaned up {len(old_conversations)} old conversation states"
        )

        # Reset usage stats counters
        for agent_type, stats in self.agent_usage_stats.items():
            stats["recent_requests"] = 0

        # Update cache TTL based on current usage
        await self._dynamic_cache_management()

        self.last_cleanup_time = now

    async def _get_cached_tools(self) -> Dict[str, Dict]:
        """Get cached tool discovery with TTL"""
        now = datetime.now()
        cache_ttl = self._get_cache_ttl()

        # Check if cache is valid
        if (
            self.discovered_tools is not None
            and self.tools_cache_timestamp is not None
            and (now - self.tools_cache_timestamp).total_seconds() < cache_ttl
        ):
            self.logger.debug(f"🔄 Using cached tool discovery (TTL: {cache_ttl}s)")
            return self.discovered_tools

        # Cache is stale or doesn't exist, refresh
        cache_age = (
            (now - self.tools_cache_timestamp).total_seconds()
            if self.tools_cache_timestamp
            else 0
        )
        self.logger.info(f"🔄 Refreshing tool discovery cache (age: {cache_age:.1f}s)")
        self.discovered_tools = await self._discover_available_tools()
        self.tools_cache_timestamp = now
        return self.discovered_tools

    def _format_agent_capabilities(self, tools: Dict[str, Dict]) -> str:
        """Format agent capabilities for the routing prompt"""
        formatted = []

        for agent_type, info in tools.items():
            formatted.append(f"\n**{agent_type.upper()}**:")
            formatted.append(f"  Purpose: {info['agent_description']}")

            if info.get("servers"):
                formatted.append(f"  MCP Servers: {', '.join(info['servers'])}")

                # Show key tools for each server
                server_tools = info.get("server_tools", {})
                for server_name, server_info in server_tools.items():
                    tools_list = server_info.get("tools", [])
                    if tools_list:
                        tool_names = [f"`{t['name']}`" for t in tools_list[:3]]
                        if len(tools_list) > 3:
                            tool_names.append(f"+ {len(tools_list) - 3} more")
                        formatted.append(
                            f"    - {server_name}: {', '.join(tool_names)}"
                        )
                    elif "error" in server_info:
                        formatted.append(f"    - {server_name}: (connection issue)")
            else:
                formatted.append(
                    f"  Built-in capabilities: {', '.join(info.get('capabilities', []))}"
                )

        return "\n".join(formatted)

    def _initialize_agent_registry(self) -> Dict[str, AgentSpec]:
        """Initialize the registry of available specialized agents"""
        return {
            "capability_inspector": AgentSpec(
                name="capability_inspector",
                instruction="""You are a system introspection specialist that can analyze and report on 
                the bot's capabilities, available MCP tools, and connected servers. When asked about 
                capabilities, you should:
                
                1. List all available MCP servers across all agent types
                2. Enumerate specific tools available on each server
                3. Explain what each tool/server can do
                4. Provide examples of how users can leverage these capabilities
                5. Organize the information in a clear, user-friendly format
                
                You have access to the complete system architecture and can introspect:
                - All specialized agents (financial_analyst, code_developer, etc.)
                - All MCP servers (supabase, brave_search, fetch, github, etc.)
                - All available tools and their functions
                
                Present this information in a comprehensive but accessible way.""",
                server_names=[],  # This agent will introspect other agents, not use direct MCP servers
                capabilities=[
                    "system_introspection",
                    "capability_enumeration",
                    "tool_listing",
                ],
            ),
            "knowledge_agent": AgentSpec(
                name="knowledge_agent",
                instruction="""You are a knowledgeable assistant that can answer factual questions 
                using your training data. You should provide direct, accurate answers to questions 
                about well-established facts, definitions, historical information, geography, 
                science, and general knowledge.
                
                Use your knowledge base to answer questions like:
                - What is the capital of [state/country]?
                - Who was [historical figure]?
                - What is [scientific concept]?
                - Basic definitions and explanations
                
                Only use your built-in knowledge - do not search the web for basic factual information.""",
                server_names=[],  # No external servers needed for basic knowledge
                capabilities=["factual_knowledge", "definitions", "basic_qa"],
            ),
            "financial_analyst": AgentSpec(
                name="financial_analyst",
                instruction="""You are a financial analysis expert with access to market data, 
                financial APIs, and analysis tools. You can analyze financial statements, 
                market trends, create financial models, and generate investment insights.
                
                For dashboard creation, you should:
                1. Gather financial data from Supabase
                2. Research industry benchmarks using Brave Search
                3. Create visualizations and analysis
                4. Provide actionable insights and recommendations""",
                server_names=["fetch", "brave_search", "supabase"],
                capabilities=[
                    "financial_analysis",
                    "market_research",
                    "data_visualization",
                ],
            ),
            "code_developer": AgentSpec(
                name="code_developer",
                instruction="""You are a software developer with access to filesystem and development tools. 
                You can write code, create applications, and provide technical solutions.
                
                For dashboard development, you should:
                1. Create responsive dashboard code (React/Next.js preferred)
                2. Provide deployment instructions
                3. Ensure mobile-friendly and accessible design
                4. Generate complete code solutions""",
                server_names=["filesystem", "fetch"],
                capabilities=[
                    "code_generation",
                    "testing",
                    "documentation",
                ],
            ),
            "data_researcher": AgentSpec(
                name="data_researcher",
                instruction="""You are a research specialist with access to web search, 
                data sources, and analysis tools. You MUST use your tools to gather real-time information.
                
                IMPORTANT: Always use your available tools (brave_search, fetch) to get actual data.
                - For weather: 1) Use brave_search to find weather sites, 2) Use fetch to get current conditions
                - For news/current events: 1) Use brave_search to find articles, 2) Use fetch to read content
                - For any real-time data: USE YOUR TOOLS to get specific current information
                
                Return the actual current data (temperature, conditions, etc.), not just links.""",
                server_names=["fetch", "brave_search", "supabase"],
                capabilities=["research", "data_analysis", "report_generation"],
            ),
            "project_manager": AgentSpec(
                name="project_manager",
                instruction="""You are a project management expert with access to databases 
                and coordination tools. You can create project plans, track progress, 
                and manage workflows using available data sources.""",
                server_names=["supabase", "fetch"],
                capabilities=[
                    "project_planning",
                    "progress_tracking",
                    "data_management",
                ],
            ),
            "communication_specialist": AgentSpec(
                name="communication_specialist",
                instruction="""You are a communication expert with access to content creation tools. 
                You can draft messages, create presentations, manage content, and facilitate communication 
                through available channels.""",
                server_names=["fetch", "supabase"],
                capabilities=["content_creation", "communication", "presentation"],
            ),
            "automation_specialist": AgentSpec(
                name="automation_specialist",
                instruction="""You are a workflow automation expert with access to n8n workflows and Airtable. 
                You can trigger automated workflows, sync data between systems, and manage complex automation processes.
                
                When handling automation requests, you should:
                1. Identify the appropriate n8n workflow to trigger
                2. Gather required data from the user or other systems
                3. Execute the workflow with proper parameters
                4. Monitor the automation and provide status updates
                5. Handle any errors or exceptions gracefully
                
                You specialize in:
                - Triggering n8n workflows via webhook URLs
                - Managing Airtable record operations
                - Data synchronization between platforms
                - Automated data processing workflows""",
                server_names=["n8n", "supabase", "fetch"],
                capabilities=[
                    "workflow_automation",
                    "data_synchronization",
                    "process_automation",
                    "airtable_integration",
                ],
            ),
            "airtable_manager": AgentSpec(
                name="airtable_manager",
                instruction="""You are an Airtable database specialist with expertise in managing 
                records, organizing data, and performing database operations through n8n workflows.
                
                You can:
                1. Create, read, update, and delete Airtable records
                2. Query and filter Airtable data
                3. Manage table relationships and data structure
                4. Perform bulk operations on records
                5. Generate reports from Airtable data
                
                Always ensure data integrity and follow best practices for database operations.
                Use n8n workflows to interact with Airtable for complex operations.""",
                server_names=["n8n", "fetch"],
                capabilities=[
                    "airtable_operations",
                    "database_management",
                    "record_management",
                    "data_querying",
                ],
            ),
        }

    async def initialize_slack(self, bot_token: str, app_token: str):
        """Initialize Slack clients and WebSocket connection"""
        if not SLACK_AVAILABLE:
            raise ImportError(
                "Slack SDK not available. Install with: pip install slack-sdk"
            )

        try:
            self.logger.info("🔌 Initializing Slack clients...")

            # Store the current event loop for async task scheduling
            self.event_loop = asyncio.get_event_loop()

            # Create Web client
            self.slack_client = WebClient(token=bot_token)
            self.logger.info("✅ Slack Web client created")

            # Create Socket Mode client
            self.socket_client = SocketModeClient(
                app_token=app_token, web_client=self.slack_client
            )
            self.logger.info("✅ Slack Socket Mode client created")

            # Register event handlers
            self.socket_client.socket_mode_request_listeners.append(
                self._handle_slack_events
            )
            self.logger.info("✅ Event handlers registered")

            self.logger.info("🔌 Slack WebSocket connection initialized")

        except Exception as e:
            self.logger.error(f"Failed to initialize Slack: {e}")
            raise

    def _handle_slack_events(self, client: SocketModeClient, req: SocketModeRequest):
        """Handle incoming Slack events (synchronous handler for Slack SDK)"""
        if req.type == "events_api":
            # Acknowledge the request immediately
            client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )

            # Process the event asynchronously using the stored event loop
            event = req.payload.get("event", {})
            if self.event_loop and not self.event_loop.is_closed():
                asyncio.run_coroutine_threadsafe(
                    self._process_slack_message(event), self.event_loop
                )

        elif req.type == "slash_commands":
            # Acknowledge slash commands immediately
            client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )

            # Process slash command asynchronously using the stored event loop
            command_data = req.payload
            if self.event_loop and not self.event_loop.is_closed():
                asyncio.run_coroutine_threadsafe(
                    self._process_slack_slash_command(command_data), self.event_loop
                )

        else:
            # Acknowledge other request types
            client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )

    async def _process_slack_message(self, event: Dict):
        """Process Slack message events with enhanced conversation management"""
        start_time = datetime.now()

        try:
            # Filter for relevant events only
            if event.get("type") not in ["app_mention", "message"]:
                return

            # Ignore bot messages and messages with subtypes (like edits, etc.)
            if event.get("subtype") or event.get("bot_id"):
                return

            self.logger.info(
                f"📩 Processing Slack message: {event.get('text', '')[:100]}..."
            )

            user_id = event.get("user")
            channel_id = event.get("channel")
            message_text = event.get("text", "")
            message_ts = event.get("ts")  # Capture the original message timestamp
            thread_ts = event.get("thread_ts")  # Check if this is a threaded reply

            if not user_id or not channel_id or not message_text:
                return

            # 🚨 PRIORITY: Check if this is a response to a pending human input request
            if user_id in self.pending_human_inputs and thread_ts:
                self.logger.info(
                    f"📝 Received human input response from user {user_id}"
                )
                try:
                    # Clean the message text (remove mentions, etc.)
                    clean_text = self._clean_user_input(message_text)

                    # Resolve the Future with the user's response
                    future = self.pending_human_inputs[user_id]
                    if not future.done():
                        future.set_result(clean_text)
                        self.logger.info(
                            f"✅ Human input resolved: {clean_text[:50]}..."
                        )

                    # Clean up the pending request
                    del self.pending_human_inputs[user_id]
                    return  # Don't process this as a new request

                except Exception as e:
                    self.logger.error(f"Error processing human input response: {e}")
                    # Continue processing as normal message if error

            # Check if this is an app mention or direct message to our bot
            if event.get("type") == "app_mention" or channel_id.startswith("D"):
                # Initialize or get conversation state
                conversation_key = f"{user_id}_{channel_id}"
                if conversation_key not in self.conversation_states:
                    self.conversation_states[conversation_key] = ConversationState(
                        user_id=user_id, channel_id=channel_id
                    )

                conversation_state = self.conversation_states[conversation_key]

                # 🎯 Track current context for human input callbacks
                self.current_user_id = user_id
                self.current_channel_id = channel_id
                self.current_thread_ts = (
                    message_ts  # Use message timestamp as thread starter
                )

                # Add eyes reaction immediately to show the bot received the message
                try:
                    if self.slack_client and message_ts:
                        self.slack_client.reactions_add(
                            channel=channel_id, timestamp=message_ts, name="eyes"
                        )
                        self.logger.info(
                            "👀 Added eyes reaction to show message received"
                        )
                except Exception as e:
                    self.logger.warning(f"Could not add reaction: {e}")

                # Store conversation memory (legacy support)
                await self._store_conversation_memory(user_id, message_text, channel_id)

                # Dynamic intent analysis with context
                context = {
                    "user_id": user_id,
                    "channel_id": channel_id,
                    "recent_context": conversation_state.get_recent_context(),
                    "turn_number": conversation_state.current_turn + 1,
                }

                intent_analysis = await self._analyze_user_intent_dynamic(
                    message_text, context
                )

                self.logger.info(
                    f"🎯 Intent Analysis: {intent_analysis['execution_strategy']} - {intent_analysis['intent_name']}"
                )

                # Get pooled agents with conversation isolation
                agents = []
                request_id = f"{user_id}_{int(datetime.now().timestamp())}"

                for agent_type in intent_analysis.get("required_agents", []):
                    try:
                        agent = await self.get_pooled_agent(agent_type, request_id)
                        agents.append(agent)
                        self.logger.info(f"✅ Using {agent_type} agent for request")
                    except Exception as e:
                        self.logger.warning(f"Could not get {agent_type} agent: {e}")

                # Perform periodic maintenance tasks
                await self._cleanup_memory()
                await self._health_check_agents()

                if not agents:
                    await self._send_slack_response(
                        channel_id,
                        "I need some specialized agents to help with this request, but none are available right now. Please try again later.",
                        thread_ts=message_ts,
                    )
                    return

                # Execute orchestrated workflow - pass original message for simple requests
                result = await self._execute_orchestrated_workflow(
                    intent_analysis, message_text, agents
                )

                # Calculate execution time and quality metrics
                execution_time = (datetime.now() - start_time).total_seconds()
                quality_metrics = {
                    "execution_time": execution_time,
                    "intent_confidence": intent_analysis.get("confidence", "unknown"),
                    "agent_count": len(agents),
                    "complexity": intent_analysis.get("complexity", "unknown"),
                }

                # Create conversation turn
                turn = ConversationTurn(
                    turn_number=conversation_state.current_turn + 1,
                    user_input=message_text,
                    agent_response=result,
                    intent_analysis=intent_analysis,
                    timestamp=start_time,
                    quality_metrics=quality_metrics,
                    execution_time=execution_time,
                )

                # Add turn to conversation state
                conversation_state.add_turn(turn)

                # Store interaction for learning (legacy support)
                await self._store_interaction_learning(
                    user_id, message_text, result, intent_analysis
                )

                # Update pattern learning based on successful interaction
                selected_agent = intent_analysis.get("required_agents", [None])[0]
                if (
                    selected_agent and execution_time < 30
                ):  # Consider it successful if under 30s
                    self._update_pattern_learning(
                        message_text, selected_agent, success=True
                    )

                # Clean up request conversation context
                self._cleanup_request_conversation(request_id)

                # Send formatted response to Slack (in a thread) with quality info
                await self._send_enhanced_slack_response(
                    channel_id,
                    result,
                    intent_analysis,
                    quality_metrics,
                    thread_ts=message_ts,
                )

        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            self.logger.error(
                f"Error processing Slack message (took {execution_time:.2f}s): {e}"
            )
            if "channel_id" in locals() and "message_ts" in locals():
                await self._send_slack_response(
                    channel_id,
                    f"Sorry, I encountered an error: {str(e)}",
                    thread_ts=message_ts,
                )

    async def _process_slack_slash_command(self, command_data: Dict):
        """Process Slack slash commands"""
        try:
            command = command_data.get("command", "")
            text = command_data.get("text", "")
            user_id = command_data.get("user_id", "")
            channel_id = command_data.get("channel_id", "")

            self.logger.info(f"⚡ Processing slash command: {command} {text}")

            # Handle different slash commands
            if command == "/meta-agent":
                # Process as regular message
                event = {
                    "type": "message",
                    "user": user_id,
                    "channel": channel_id,
                    "text": text,
                }
                await self._process_slack_message(event)

        except Exception as e:
            self.logger.error(f"Error processing slash command: {e}")

    async def get_pooled_agent(self, agent_type: str, request_id: str) -> Agent:
        """Get a pooled agent with conversation isolation for the request"""
        if agent_type not in self.agent_registry:
            raise ValueError(f"Unknown agent type: {agent_type}")

        # Try to get pooled agent first
        if agent_type in self.agent_pool:
            agent = self.agent_pool[agent_type]
            # Initialize isolated conversation context for this request
            self._initialize_request_conversation(request_id, agent_type)
            self.logger.info(
                f"♻️  Using pooled {agent_type} agent for request {request_id}"
            )
            return agent

        # If not in pool, create a new one (fallback)
        self.logger.info(f"🆕 Creating new {agent_type} agent (not in pool)")
        spec = self.agent_registry[agent_type]

        if self.mcp_app:
            # Create agent with MCPApp context for proper server registry access
            agent = Agent(
                name=f"temp_{spec.name}_{request_id}",
                instruction=spec.instruction,
                server_names=spec.server_names,
                context=self.mcp_app.context,  # Pass MCPApp context
            )
        else:
            # Fallback to direct Agent creation
            agent = Agent(
                name=f"temp_{spec.name}_{request_id}",
                instruction=spec.instruction,
                server_names=spec.server_names,
            )

        # Initialize isolated conversation context
        self._initialize_request_conversation(request_id, agent_type)
        return agent

    def _initialize_request_conversation(self, request_id: str, agent_type: str):
        """Initialize isolated conversation context for a request"""
        self.request_conversations[request_id] = {
            "agent_type": agent_type,
            "conversation_history": [],
            "context_memory": {},
            "start_time": datetime.now(),
        }
        self.logger.debug(
            f"🔒 Initialized isolated conversation for request {request_id}"
        )

    def _cleanup_request_conversation(self, request_id: str):
        """Clean up conversation context after request completes"""
        if request_id in self.request_conversations:
            del self.request_conversations[request_id]
            self.logger.debug(f"🧹 Cleaned up conversation for request {request_id}")

    def _dynamic_pattern_match(self, message: str) -> Optional[Dict[str, Any]]:
        """Dynamic pattern matching with confidence scoring and learning"""
        message_lower = message.lower()

        best_match = None
        best_confidence = 0.0

        for pattern_name, pattern_info in self.dynamic_patterns.items():
            # Calculate confidence based on keyword matches
            keyword_matches = sum(
                1 for keyword in pattern_info["keywords"] if keyword in message_lower
            )

            if keyword_matches > 0:
                # Confidence calculation: base confidence * match ratio * usage boost
                match_ratio = keyword_matches / len(pattern_info["keywords"])
                usage_boost = min(
                    1.2, 1.0 + (pattern_info["usage_count"] / 100)
                )  # Max 20% boost

                confidence = pattern_info["confidence"] * match_ratio * usage_boost

                if (
                    confidence > best_confidence
                    and confidence >= self.config["pattern_confidence_threshold"]
                ):
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
            self.dynamic_patterns[pattern_name]["usage_count"] += 1

            # Track agent usage for dynamic scaling
            agent_type = best_match["agent"]
            if agent_type not in self.agent_usage_stats:
                self.agent_usage_stats[agent_type] = {
                    "recent_requests": 0,
                    "total_requests": 0,
                }

            self.agent_usage_stats[agent_type]["recent_requests"] += 1
            self.agent_usage_stats[agent_type]["total_requests"] += 1

        return best_match

    def _update_pattern_learning(self, message: str, agent_used: str, success: bool):
        """Update learning patterns based on interaction success"""
        message_words = set(message.lower().split())

        # Initialize agent entry if it doesn't exist
        if agent_used not in self.pattern_learning:
            self.pattern_learning[agent_used] = {"successful_keywords": set()}

        # Track successful keywords
        if success:
            for word in message_words:
                if len(word) > 3:  # Ignore short words
                    self.pattern_learning[agent_used]["successful_keywords"].add(word)

                    # Also check if we should add compound keywords (simple heuristic)
                    # Look for action + object patterns
                    if word in ["create", "build", "generate", "make", "show", "get"]:
                        for potential_object in message_words:
                            if (
                                potential_object
                                in [
                                    "dashboard",
                                    "report",
                                    "analysis",
                                    "weather",
                                    "data",
                                    "file",
                                    "code",
                                ]
                                and potential_object != word
                            ):
                                compound_keyword = f"{word}_{potential_object}"
                                self.pattern_learning[agent_used][
                                    "successful_keywords"
                                ].add(compound_keyword)
                                self.logger.debug(
                                    f"📚 Added compound keyword: {compound_keyword} -> {agent_used}"
                                )
                                break  # Only add one new keyword per successful interaction

        # Persist learning after updates
        self._save_learning_patterns()

    def _extract_discovery_keywords_from_message(self, message: str) -> List[str]:
        """Extract keywords from message that might indicate need for dynamic MCP server discovery"""
        import re

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
        ]

        for pattern in org_service_patterns:
            matches = re.findall(pattern, message, re.IGNORECASE)
            for match in matches:
                org_name = match[0].upper()  # Organization name (e.g., "ARC")
                service_type = match[1].lower()  # Service type (e.g., "supabase")

                # Add both individual keywords and compound qualifier
                keywords.extend([org_name.lower(), service_type])
                keywords.append(
                    f"{org_name.lower()}_{service_type}"
                )  # e.g., "arc_supabase"

                self.logger.info(
                    f"🎯 Extracted qualified service: {org_name} {service_type}"
                )

        # Extract specific service/tool names that might be in database
        # Look for patterns like "airtable", "n8n", "webhook", etc.
        service_patterns = [
            r"\b(airtable|air table)\b",
            r"\b(n8n|n-8-n)\b",
            r"\b(webhook|web hook)\b",
            r"\b(automation|automate)\b",
            r"\b(workflow|work flow)\b",
            r"\b(integration|integrate)\b",
            r"\b(zapier)\b",
            r"\b(sync|synchronize)\b",
            r"\b(trigger)\b",
            r"\b(gmail|email)\b",
            r"\b(calendar)\b",
            r"\b(notion)\b",
            r"\b(slack)\b",
            r"\b(discord)\b",
            r"\b(teams)\b",
            r"\b(api)\b",
            r"\b(database|db|supabase)\b",  # Include supabase here too
            r"\b(crm)\b",
            r"\b(analytics)\b",
        ]

        for pattern in service_patterns:
            matches = re.findall(pattern, message_lower)
            keywords.extend(matches)

        # Extract organization names (capitalized words that might be org identifiers)
        # Look for 2-10 character all-caps words that could be organization codes
        org_patterns = [
            r"\b([A-Z]{2,10})\b",  # All caps words like "ARC", "ACME", "CORP"
        ]

        for pattern in org_patterns:
            matches = re.findall(pattern, message)
            for match in matches:
                # Only add if it's not a common English word
                if match.lower() not in [
                    "THE",
                    "AND",
                    "FOR",
                    "ARE",
                    "BUT",
                    "NOT",
                    "YOU",
                    "ALL",
                    "CAN",
                    "HAD",
                    "HER",
                    "WAS",
                    "ONE",
                    "OUR",
                    "OUT",
                    "DAY",
                    "GET",
                    "USE",
                    "MAN",
                    "NEW",
                    "NOW",
                    "OLD",
                    "SEE",
                    "HIM",
                    "TWO",
                    "HOW",
                    "ITS",
                    "DID",
                    "YES",
                    "WHO",
                    "OIL",
                    "SIT",
                    "SET",
                ]:
                    keywords.append(match.lower())

        # Also extract quoted service names or capitalized words that might be service names
        quoted_matches = re.findall(r'"([^"]+)"', message)
        keywords.extend([match.lower() for match in quoted_matches])

        # Remove duplicates while preserving order
        unique_keywords = []
        for keyword in keywords:
            if keyword not in unique_keywords:
                unique_keywords.append(keyword)

        return unique_keywords

    async def _check_if_needs_dynamic_discovery(
        self, message: str, keywords: List[str]
    ) -> bool:
        """Check if the message requires dynamic MCP server discovery"""
        if not keywords:
            return False

        # Check if any keywords suggest services that might be in database but not in current registry
        message_lower = message.lower()

        # Patterns that suggest need for specialized tools/services
        discovery_indicators = [
            "mcp server",
            "mcp tool",
            "new service",
            "connect to",
            "integrate with",
            "automation tool",
            "workflow tool",
            "api service",
            "third party",
            "external service",
        ]

        # If message contains discovery indicators and service keywords, likely needs discovery
        has_indicators = any(
            indicator in message_lower for indicator in discovery_indicators
        )
        has_service_keywords = len(keywords) > 0

        # Also check if keywords are NOT in current agent capabilities
        current_capabilities = set()
        for agent_spec in self.agent_registry.values():
            current_capabilities.update(agent_spec.server_names)

        unknown_services = [k for k in keywords if k not in current_capabilities]

        return has_indicators or (has_service_keywords and len(unknown_services) > 0)

    async def _execute_dynamic_discovery_workflow(
        self, intent_analysis: Dict, message: str, agents: List[Agent]
    ) -> str:
        """Execute the dynamic MCP server discovery workflow"""
        try:
            keywords = intent_analysis.get("discovery_keywords", [])
            self.logger.info(f"🔍 Starting dynamic discovery for keywords: {keywords}")

            # Step 1: Discover servers from database
            discovered_servers = await self._dynamic_mcp_server_discovery(keywords)

            if not discovered_servers:
                return f"⚠️ No specialized MCP servers found for '{', '.join(keywords)}'. Using standard agents."

            # Step 2: Create dynamic agent with discovered servers
            dynamic_agent = await self._create_dynamic_agent_with_servers(
                discovered_servers, "discovery_agent"
            )

            if not dynamic_agent:
                return f"❌ Failed to create dynamic agent with discovered servers."

            # Step 3: Execute request with dynamic agent
            try:
                async with dynamic_agent:
                    llm = await dynamic_agent.attach_llm(OpenAIAugmentedLLM)

                    enhanced_prompt = f"""
                    Original request: {message}
                    
                    You have access to specialized MCP servers that were dynamically discovered:
                    {[f"- {s['server_name']}: {s['description']}" for s in discovered_servers]}
                    
                    Use these specialized tools to fulfill the user's request. Focus on providing specific, 
                    actionable results with relevant URLs, IDs, or identifiers where applicable.
                    """

                    result = await llm.generate_str(enhanced_prompt)
                    return result

            finally:
                # Clean up dynamic agent
                try:
                    await dynamic_agent.__aexit__(None, None, None)
                except:  # noqa: E722
                    pass

        except Exception as e:
            self.logger.error(f"Dynamic discovery workflow error: {e}")
            # Fallback to standard execution
            if agents:
                return await self._execute_sequential_fallback(message, agents)
            else:
                return f"❌ Dynamic discovery failed: {str(e)}"

    def _parse_mcp_query_result(self, query_result: str) -> List[Dict]:
        """Parse the result from MCP server database query into server configurations"""
        import re
        import json

        servers = []

        try:
            # Try to extract JSON from the result if it contains structured data
            json_matches = re.findall(r"\{[^{}]*\}", query_result)

            for json_str in json_matches:
                try:
                    server_data = json.loads(json_str)
                    if isinstance(server_data, dict) and "server_name" in server_data:
                        servers.append(server_data)
                except json.JSONDecodeError:
                    continue

            # If no JSON found, try to parse from structured text
            if not servers:
                # Look for patterns like "server_name: value"
                lines = query_result.split("\n")
                current_server = {}

                for line in lines:
                    line = line.strip()
                    if not line:
                        if current_server and "server_name" in current_server:
                            servers.append(current_server)
                            current_server = {}
                        continue

                    # Parse key: value pairs
                    if ":" in line:
                        key, value = line.split(":", 1)
                        key = key.strip().lower()
                        value = value.strip().strip("\"'")

                        if key in [
                            "server_name",
                            "display_name",
                            "description",
                            "transport",
                            "url",
                            "command",
                        ]:
                            current_server[key] = value
                        elif key == "args" and value:
                            # Parse args if they're in a list format
                            try:
                                current_server["args"] = json.loads(value)
                            except:
                                current_server["args"] = [value] if value else []

                # Add final server if exists
                if current_server and "server_name" in current_server:
                    servers.append(current_server)

        except Exception as e:
            self.logger.warning(f"Error parsing MCP query result: {e}")

        # Ensure each server has required fields with defaults
        for server in servers:
            server.setdefault(
                "description",
                f"Specialized MCP server: {server.get('server_name', 'unknown')}",
            )
            server.setdefault("transport", "stdio")

        return servers

    async def _analyze_user_intent_dynamic(
        self, message: str, context: Dict = None
    ) -> Dict:
        """Dynamic intent analysis using actual tool discovery - replaces hard-coded patterns"""
        try:
            # 🔍 FIRST: Check if we need dynamic MCP server discovery (highest priority)
            message_keywords = self._extract_discovery_keywords_from_message(message)
            needs_dynamic_discovery = await self._check_if_needs_dynamic_discovery(
                message, message_keywords
            )

            if needs_dynamic_discovery:
                self.logger.info(
                    f"🎯 PRIORITY: Dynamic MCP discovery detected for: {message[:50]}... keywords: {message_keywords}"
                )

                # Select appropriate agent type for the discovered tools
                # Default to data_researcher for database queries, but could be more intelligent
                agent_type = "data_researcher"  # This agent can work with any MCP tools

                return {
                    "required_agents": [agent_type],
                    "complexity": "dynamic",
                    "estimated_tasks": 1,
                    "execution_strategy": "dynamic_discovery",
                    "priority": "high",
                    "task_description": f"Dynamic MCP discovery for qualified services",
                    "reasoning": f"Detected NAME TOOL patterns requiring database server discovery",
                    "intent_name": f"dynamic_discovery_{agent_type}",
                    "confidence": "high",
                    "requires_tools": [],
                    "discovery_keywords": message_keywords,
                    "qualified_services": True,
                }

            # 🚀 SECOND: Dynamic pattern matching (bypass LLM for common requests)
            pattern_match = self._dynamic_pattern_match(message)
            if pattern_match:
                agent_type = pattern_match["agent"]
                confidence = pattern_match["confidence"]
                matched_keywords = pattern_match["matched_keywords"]

                self.logger.info(
                    f"⚡ Dynamic pattern match: {message[:50]}... -> {agent_type} "
                    f"(confidence: {confidence:.2f}, keywords: {matched_keywords[:3]})"
                )

                return {
                    "required_agents": [agent_type],
                    "complexity": "simple",
                    "estimated_tasks": 1,
                    "execution_strategy": "single_agent",
                    "priority": "high",
                    "task_description": f"Dynamic pattern match to {agent_type}",
                    "reasoning": f"Pattern-based routing to {agent_type} with {confidence:.2f} confidence",
                    "intent_name": f"dynamic_{agent_type}",
                    "confidence": "high" if confidence > 0.85 else "medium",
                    "requires_tools": [],
                    "pattern_confidence": confidence,
                    "matched_keywords": matched_keywords,
                }

            # 🤖 THIRD: Fall back to LLM routing for complex/ambiguous requests
            self.logger.info(f"🤖 Using LLM routing for: {message[:50]}...")

            # Get fresh tool discovery (with caching)
            tools = await self._get_cached_tools()

            # Create routing prompt with actual available tools
            routing_prompt = f"""
            User request: "{message}"
            
            Available specialized agents and their actual tools:
            {self._format_agent_capabilities(tools)}
            
            Context: {context.get("recent_context", "First interaction") if context else "No context"}
            
            Analyze this request and determine:
            1. Which agent type is BEST suited for this request
            2. What complexity level (simple/moderate/complex) 
            3. What execution strategy (single_agent/orchestrated)
            4. Confidence level (high/medium/low)
            
            Return a JSON response with:
            {{
                "selected_agent": "agent_type_name",
                "reasoning": "explanation of why this agent was selected",
                "complexity": "simple|moderate|complex",
                "execution_strategy": "single_agent|orchestrated",
                "confidence": "high|medium|low",
                "estimated_tasks": number,
                "requires_tools": ["tool1", "tool2"]
            }}
            
            Important: 
            - For capability questions, use "capability_inspector"
            - For factual questions (not requiring real-time data), use "knowledge_agent"
            - For weather/current events/real-time data, use "data_researcher"
            - For airtable/n8n/automation, use "automation_specialist" or "airtable_manager"
            - Match the user's request to the actual tools available
            """

            # Use OpenAI to analyze the request dynamically
            from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

            # Create a temporary agent to use OpenAI for routing
            if self.mcp_app:
                # Create agent with MCPApp context for proper server registry access
                routing_agent = Agent(
                    name="dynamic_router",
                    instruction="You are a routing agent that analyzes user requests and selects the best specialized agent.",
                    server_names=[],  # No MCP servers needed for routing
                    context=self.mcp_app.context,  # Pass MCPApp context
                )
            else:
                # Fallback to direct Agent creation
                routing_agent = Agent(
                    name="dynamic_router",
                    instruction="You are a routing agent that analyzes user requests and selects the best specialized agent.",
                    server_names=[],  # No MCP servers needed for routing
                )

            try:
                async with routing_agent:
                    llm = await routing_agent.attach_llm(OpenAIAugmentedLLM)
                    routing_result = await llm.generate_str(routing_prompt)

                    # Parse the JSON response
                    import json

                    try:
                        parsed_result = json.loads(routing_result)
                    except json.JSONDecodeError:
                        # Extract JSON from the response if it's wrapped in text
                        import re

                        json_match = re.search(r"\{.*\}", routing_result, re.DOTALL)
                        if json_match:
                            parsed_result = json.loads(json_match.group())
                        else:
                            raise ValueError("Could not parse routing response")

                            # Validate and format the response
                    selected_agent = parsed_result.get(
                        "selected_agent", "data_researcher"
                    )

                    # Normalize agent name (case-insensitive matching)
                    selected_agent_lower = selected_agent.lower()
                    if selected_agent_lower in self.agent_registry:
                        selected_agent = selected_agent_lower
                    elif selected_agent not in self.agent_registry:
                        self.logger.warning(
                            f"Unknown agent {selected_agent}, defaulting to data_researcher"
                        )
                        selected_agent = "data_researcher"

                    analysis = {
                        "required_agents": [selected_agent],
                        "complexity": parsed_result.get("complexity", "simple"),
                        "estimated_tasks": parsed_result.get("estimated_tasks", 1),
                        "execution_strategy": parsed_result.get(
                            "execution_strategy", "single_agent"
                        ),
                        "priority": "high"
                        if parsed_result.get("confidence") == "high"
                        else "medium",
                        "task_description": f"Dynamic routing to {selected_agent}",
                        "reasoning": parsed_result.get(
                            "reasoning", "Dynamic tool-based routing"
                        ),
                        "intent_name": f"dynamic_{selected_agent}",
                        "confidence": parsed_result.get("confidence", "medium"),
                        "requires_tools": parsed_result.get("requires_tools", []),
                    }

                    self.logger.info(
                        f"🎯 Dynamic routing: {message[:50]}... -> {selected_agent} (confidence: {analysis['confidence']})"
                    )
                    return analysis

            finally:
                # Step 4: Clean up dynamic agent
                try:
                    await routing_agent.__aexit__(None, None, None)
                    self.logger.info("🧹 Cleaned up dynamic agent")
                except Exception as cleanup_error:
                    self.logger.warning(
                        f"Dynamic agent cleanup warning: {cleanup_error}"
                    )

        except Exception as e:
            self.logger.error(f"Dynamic routing error: {e}")
            import traceback

            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            # Fallback to simple logic
            return await self._analyze_user_intent_fallback(message, context)

    async def _analyze_user_intent_fallback(
        self, message: str, context: Dict = None
    ) -> Dict:
        """Fallback intent analysis if structured system fails"""
        message_lower = message.lower()

        # Quick pattern matching as fallback
        if any(
            word in message_lower
            for word in ["weather", "current", "today", "now", "latest", "recent"]
        ):
            agent_type = "data_researcher"
        elif any(
            word in message_lower
            for word in ["what is", "who is", "what does", "define", "explain"]
        ):
            agent_type = "knowledge_agent"
        elif any(
            word in message_lower
            for word in ["dashboard", "financial", "revenue", "profit", "analysis"]
        ):
            agent_type = "financial_analyst"
        elif any(
            word in message_lower
            for word in ["code", "deploy", "develop", "build", "app"]
        ):
            agent_type = "code_developer"
        else:
            agent_type = "data_researcher"

        return {
            "required_agents": [agent_type],
            "complexity": "simple",
            "estimated_tasks": 1,
            "execution_strategy": "single_agent",
            "priority": "medium",
            "task_description": "Fallback analysis",
            "reasoning": f"Fallback logic selected {agent_type}",
            "intent_name": "fallback",
            "confidence": "low",
        }

    async def _execute_orchestrated_workflow(
        self, intent_analysis: Dict, message: str, agents: List[Agent]
    ) -> str:
        """Execute orchestrated multi-agent workflow as shown in sequence diagram"""
        try:
            # Special handling for capability inquiry
            if (
                intent_analysis.get("intent_name") == "capability_inquiry"
                and len(agents) == 1
                and agents[0].name == "capability_inspector"
            ):
                self.logger.info("🔍 Performing system capability introspection")
                return await self._perform_capability_introspection()

            # 🚀 NEW: Handle dynamic MCP discovery execution strategy
            if intent_analysis.get("execution_strategy") == "dynamic_discovery":
                self.logger.info("🔍 Executing dynamic MCP server discovery workflow")
                return await self._execute_dynamic_discovery_workflow(
                    intent_analysis, message, agents
                )

            # For simple single-agent requests, execute directly
            if (
                intent_analysis.get("execution_strategy") == "single_agent"
                and len(agents) == 1
            ):
                agent = agents[0]
                self.logger.info(f"🎯 Direct execution with {agent.name} agent")

                async with agent:
                    llm = await agent.attach_llm(OpenAIAugmentedLLM)
                    # Pass the original user message directly
                    result = await llm.generate_str(message)
                    return result

            # Only use orchestrator for complex multi-agent workflows
            orchestrator = Orchestrator(
                llm_factory=OpenAIAugmentedLLM,
                available_agents=agents,
                plan_type="full",  # Iterative planning
            )

            # Enhanced task description for complex requests
            task_description = f"""
            Original request: {message}
            
            Task analysis: {intent_analysis.get("task_description", "")}
            Required capabilities: {", ".join([agent.name for agent in agents])}
            
            
            Execute this request using the available specialized agents:
            {[f"- {agent.name}: {self.agent_registry[agent.name].instruction[:100]}..." for agent in agents]}
            
            Coordinate the agents to:
            1. Gather required data and research
            2. Process and analyze information  
            3. Create deliverables (dashboards, reports, etc.)
            4. Provide actionable results with URLs/links where applicable
            
            Return a comprehensive result with specific deliverables and next steps.
            """

            self.logger.info(
                f"🎯 Orchestrating {len(agents)} agents for complex workflow"
            )

            # Execute the orchestrated workflow
            result = await orchestrator.generate_str(message=task_description)

            return result

        except Exception as e:
            self.logger.error(f"Orchestration error: {e}")
            # Fallback to sequential execution
            return await self._execute_sequential_fallback(message, agents)

    async def _perform_capability_introspection(self) -> str:
        """Perform comprehensive capability introspection across all agent types"""
        try:
            self.logger.info("🔍 Starting comprehensive capability introspection...")

            capabilities_info = {
                "specialized_agents": {},
                "mcp_servers": {},
                "total_tools": 0,
                "system_overview": {},
            }

            # Gather information about each specialized agent type
            for agent_type, spec in self.agent_registry.items():
                if agent_type == "capability_inspector":
                    continue  # Skip self-reference

                try:
                    # Create a temporary agent to inspect its capabilities
                    if self.mcp_app:
                        temp_agent = Agent(
                            name=f"inspect_{agent_type}",
                            instruction=spec.instruction,
                            server_names=spec.server_names,
                            context=self.mcp_app.context,  # Pass MCPApp context
                        )
                        await temp_agent.__aenter__()
                    else:
                        temp_agent = Agent(
                            name=f"inspect_{agent_type}",
                            instruction=spec.instruction,
                            server_names=spec.server_names,
                        )
                        await temp_agent.__aenter__()

                    # Get server capabilities
                    if spec.server_names:
                        server_capabilities = {}
                        for server_name in spec.server_names:
                            try:
                                caps = await temp_agent.get_capabilities(server_name)
                                tools = await temp_agent.list_tools(server_name)

                                server_capabilities[server_name] = {
                                    "capabilities": caps.model_dump() if caps else {},
                                    "tools": [
                                        {
                                            "name": tool.name,
                                            "description": tool.description,
                                        }
                                        for tool in tools.tools
                                    ]
                                    if tools
                                    else [],
                                }
                                capabilities_info["total_tools"] += (
                                    len(tools.tools) if tools else 0
                                )

                            except Exception as e:
                                self.logger.warning(
                                    f"Could not get capabilities for {server_name}: {e}"
                                )
                                server_capabilities[server_name] = {"error": str(e)}

                        capabilities_info["specialized_agents"][agent_type] = {
                            "description": spec.instruction[:200] + "..."
                            if len(spec.instruction) > 200
                            else spec.instruction,
                            "server_names": spec.server_names,
                            "capabilities": spec.capabilities,
                            "server_details": server_capabilities,
                        }
                    else:
                        capabilities_info["specialized_agents"][agent_type] = {
                            "description": spec.instruction[:200] + "..."
                            if len(spec.instruction) > 200
                            else spec.instruction,
                            "server_names": [],
                            "capabilities": spec.capabilities,
                            "server_details": {},
                        }

                except Exception as e:
                    self.logger.warning(f"Could not introspect {agent_type}: {e}")
                    capabilities_info["specialized_agents"][agent_type] = {
                        "error": str(e),
                        "description": spec.instruction[:100] + "..."
                        if len(spec.instruction) > 100
                        else spec.instruction,
                    }

            # Create comprehensive summary
            unique_servers = set()
            for agent_info in capabilities_info["specialized_agents"].values():
                if "server_names" in agent_info:
                    unique_servers.update(agent_info["server_names"])

            capabilities_info["system_overview"] = {
                "total_specialized_agents": len(
                    [
                        a
                        for a in self.agent_registry.keys()
                        if a != "capability_inspector"
                    ]
                ),
                "unique_mcp_servers": list(unique_servers),
                "total_unique_servers": len(unique_servers),
            }

            # Format the response
            response = f"""🤖 **Meta-Agent System Capabilities**

**📊 System Overview:**
• **{capabilities_info["system_overview"]["total_specialized_agents"]} Specialized Agents** available for different task types
• **{capabilities_info["system_overview"]["total_unique_servers"]} MCP Servers** providing {capabilities_info["total_tools"]} total tools
• **Structured Intent Classification** for intelligent agent routing

**🔧 Available Specialized Agents:**

"""

            for agent_type, info in capabilities_info["specialized_agents"].items():
                if "error" in info:
                    response += f"**{agent_type.replace('_', ' ').title()}:** ❌ Error accessing\n\n"
                    continue

                response += f"**{agent_type.replace('_', ' ').title()}:**\n"
                response += f"• Purpose: {info['description'].split('.')[0]}\n"

                if info["server_names"]:
                    response += f"• MCP Servers: {', '.join(info['server_names'])}\n"

                    # List key tools for each server
                    for server_name, server_info in info.get(
                        "server_details", {}
                    ).items():
                        if "tools" in server_info and server_info["tools"]:
                            tool_names = [
                                t["name"] for t in server_info["tools"][:3]
                            ]  # Show first 3 tools
                            tool_count = len(server_info["tools"])
                            if tool_count > 3:
                                tool_names.append(f"+ {tool_count - 3} more")
                            response += f"  - {server_name}: {', '.join(tool_names)}\n"
                else:
                    response += "• Uses built-in knowledge (no external tools)\n"

                response += "\n"

            response += """**🌐 MCP Server Details:**

"""

            # Group tools by server across all agents
            server_tool_map = {}
            for agent_info in capabilities_info["specialized_agents"].values():
                if "server_details" in agent_info:
                    for server_name, server_info in agent_info[
                        "server_details"
                    ].items():
                        if server_name not in server_tool_map:
                            server_tool_map[server_name] = []
                        if "tools" in server_info:
                            server_tool_map[server_name].extend(server_info["tools"])

            for server_name, tools in server_tool_map.items():
                unique_tools = {
                    tool["name"]: tool for tool in tools
                }.values()  # Deduplicate
                response += f"**{server_name}:** {len(unique_tools)} tools\n"
                for tool in list(unique_tools)[:5]:  # Show first 5 tools
                    response += f"  • `{tool['name']}`: {tool.get('description', 'No description')[:80]}{'...' if len(tool.get('description', '')) > 80 else ''}\n"
                if len(unique_tools) > 5:
                    response += f"  • + {len(unique_tools) - 5} more tools\n"
                response += "\n"

            response += """**💡 Usage Examples:**
• `@meta-agent what is the weather in Austin?` → **Data Researcher** (brave_search, fetch)
• `@meta-agent create Q4 financial dashboard` → **Financial Analyst** (supabase, brave_search)
• `@meta-agent deploy this React app` → **Code Developer** (github, filesystem)
• `@meta-agent what is the capital of France?` → **Knowledge Agent** (built-in knowledge)

The system uses **structured intent classification** to automatically route your requests to the most appropriate specialized agent with the right tools for the job!
"""

            self.logger.info("✅ Capability introspection completed successfully")
            return response

        except Exception as e:
            self.logger.error(f"❌ Capability introspection failed: {e}")
            import traceback

            self.logger.error(f"Full traceback: {traceback.format_exc()}")

            # Fallback response
            return f"""🤖 **Meta-Agent System Capabilities**

I have access to several specialized agents with different MCP tools and capabilities:

**Available Agent Types:**
{chr(10).join([f"• **{agent_type.replace('_', ' ').title()}**: {spec.instruction.split('.')[0]}" for agent_type, spec in self.agent_registry.items() if agent_type != "capability_inspector"])}

**Key MCP Servers:**
• **Supabase**: Database operations, SQL queries, project management
• **Brave Search**: Web search and real-time information
• **Fetch**: Data retrieval from URLs and APIs  
• **GitHub**: Code repository management and deployment
• **Filesystem**: File operations and code management
• **Slack**: Team communication and notifications

Unfortunately, I encountered an error while gathering detailed capability information: {str(e)}

Try asking me to perform specific tasks, and I'll route your request to the appropriate specialized agent!
"""

    async def _execute_sequential_fallback(
        self, message: str, agents: List[Agent]
    ) -> str:
        """Fallback sequential execution if orchestration fails"""
        results = []

        for agent in agents:
            try:
                async with agent:
                    llm = await agent.attach_llm(OpenAIAugmentedLLM)
                    agent_result = await llm.generate_str(
                        f"Handle this request with your specialized capabilities: {message}"
                    )
                    results.append(f"{agent.name}: {agent_result}")
            except Exception as e:
                results.append(f"{agent.name}: Error - {str(e)}")

        return "\n\n".join(results)

    async def _store_conversation_memory(
        self, user_id: str, message: str, channel_id: str
    ):
        """Store conversation in Supabase memory as shown in sequence diagram"""
        if user_id not in self.conversation_memory:
            self.conversation_memory[user_id] = []

        conversation_entry = {
            "role": "user",
            "content": message,
            "timestamp": datetime.now().isoformat(),
            "channel_id": channel_id,
        }

        self.conversation_memory[user_id].append(conversation_entry)

        # Store in Supabase via direct MCP function call
        try:
            # Use the available MCP function directly
            query = f"""INSERT INTO interactions (user_id, channel_id, message, created_at) 
                       VALUES ('{user_id}', '{channel_id}', '{message.replace("'", "''")}', '{conversation_entry["timestamp"]}');"""

            # Create agent for Supabase operations
            if self.mcp_app:
                supabase_agent = Agent(
                    name="memory_store",
                    instruction="Execute SQL directly",
                    server_names=["supabase"],
                    context=self.mcp_app.context,  # Pass MCPApp context
                )
                await supabase_agent.__aenter__()
            else:
                supabase_agent = Agent(
                    name="memory_store",
                    instruction="Execute SQL directly",
                    server_names=["supabase"],
                )
                await supabase_agent.__aenter__()

            try:
                llm = await supabase_agent.attach_llm(OpenAIAugmentedLLM)

                # Ask the LLM to use the execute_sql tool with specific instructions
                result = await llm.generate_str(
                    """
                You must call the execute_sql tool directly with these exact parameters:
                - project_id: "qqggdvfeybfzqmgxmidt"  
                - query: "{query}"
                
                Call the execute_sql tool now with these parameters. Do not explain, just call the tool.
                """.format(query=query)
                )
                self.logger.info(
                    f"✅ Stored conversation memory for user {user_id}: {result[:100]}..."
                    if len(result) > 100
                    else f"✅ Stored conversation memory for user {user_id}: {result}"
                )
            finally:
                # Clean up agent if we created it manually
                if not self.mcp_app:
                    try:
                        await supabase_agent.__aexit__(None, None, None)
                    except:  # noqa: E722
                        pass

        except Exception as e:
            self.logger.warning(f"Could not store to Supabase: {e}")
            import traceback

            self.logger.error(f"Conversation memory error: {traceback.format_exc()}")

    async def _store_interaction_learning(
        self, user_id: str, message: str, result: str, analysis: Dict
    ):
        """Store interaction for system learning as shown in sequence diagram"""
        try:
            # Prepare the query with proper escaping
            analysis_json = json.dumps(analysis).replace("'", "''")
            result_escaped = result.replace("'", "''")
            message_escaped = message.replace("'", "''")

            query = f"""INSERT INTO interactions (user_id, message, response, analysis, intent_analysis, execution_strategy, required_agents, created_at) 
                       VALUES (
                           '{user_id}', 
                           '{message_escaped}', 
                           '{result_escaped}', 
                           '{analysis_json}',
                           '{analysis_json}',
                           '{analysis.get("execution_strategy", "")}', 
                           ARRAY{analysis.get("required_agents", [])}, 
                           NOW()
                       );"""

            if self.mcp_app:
                supabase_agent = Agent(
                    name="learning_store",
                    instruction="Execute SQL directly",
                    server_names=["supabase"],
                    context=self.mcp_app.context,  # Pass MCPApp context
                )
                await supabase_agent.__aenter__()
            else:
                supabase_agent = Agent(
                    name="learning_store",
                    instruction="Execute SQL directly",
                    server_names=["supabase"],
                )
                await supabase_agent.__aenter__()

            try:
                llm = await supabase_agent.attach_llm(OpenAIAugmentedLLM)

                # Ask the LLM to use the execute_sql tool with specific instructions
                learning_result = await llm.generate_str(
                    """
                You must call the execute_sql tool directly with these exact parameters:
                - project_id: "qqggdvfeybfzqmgxmidt"
                - query: "{query}"
                
                Then also call execute_sql again to ensure user preferences exist:
                - project_id: "qqggdvfeybfzqmgxmidt"  
                - query: "INSERT INTO user_preferences (user_id, preferences, interaction_patterns) VALUES ('{user_id}', '{{{{}}}}', '{{{{}}}}') ON CONFLICT (user_id) DO NOTHING;"
                
                Call both execute_sql tools now with these parameters. Do not explain, just call the tools.
                """.format(query=query, user_id=user_id)
                )
                self.logger.info(
                    f"✅ Stored interaction learning for user {user_id}: {learning_result[:100]}..."
                    if len(learning_result) > 100
                    else f"✅ Stored interaction learning for user {user_id}: {learning_result}"
                )
            finally:
                # Clean up agent if we created it manually
                if not self.mcp_app:
                    try:
                        await supabase_agent.__aexit__(None, None, None)
                    except:  # noqa: E722
                        pass

        except Exception as e:
            self.logger.warning(f"Could not store learning data: {e}")
            import traceback

            self.logger.error(f"Full error: {traceback.format_exc()}")

    async def _send_slack_response(
        self, channel_id: str, result: str, analysis: Dict = None, thread_ts: str = None
    ):
        """Send formatted response to Slack as shown in sequence diagram"""
        if not self.slack_client:
            print(f"Slack Response: {result}")
            return

        try:
            # Format the response based on the type of request
            if analysis and "dashboard" in result.lower():
                # Special formatting for dashboard creation
                formatted_response = f"""✅ **Q4 Revenue Dashboard Created!**

📊 **Dashboard:** {self._extract_dashboard_url(result)}

📈 **Key Insights:** {self._extract_key_insights(result)}

🎯 **Next Steps:** {self._extract_next_steps(result)}

*System learned from this interaction for future dashboard requests.*
"""
            else:
                # Standard response formatting
                formatted_response = f"""🤖 **Meta-Agent Response**

{result}

*Processed with {analysis.get("execution_strategy", "standard")} strategy using {len(analysis.get("required_agents", []))} specialized agents.*
"""

            self.slack_client.chat_postMessage(
                channel=channel_id,
                text=formatted_response,
                parse="mrkdwn",
                thread_ts=thread_ts,
            )

        except Exception as e:
            self.logger.error(f"Error sending Slack response: {e}")

    def _extract_dashboard_url(self, result: str) -> str:
        """Extract dashboard URL from result"""
        import re

        url_pattern = r"https?://[^\s]+"
        urls = re.findall(url_pattern, result)
        return urls[0] if urls else "Dashboard created (URL in result above)"

    def _extract_key_insights(self, result: str) -> str:
        """Extract key insights from result"""
        # Simple extraction - could be enhanced with LLM
        lines = result.split("\n")
        insights = [
            line
            for line in lines
            if any(
                word in line.lower()
                for word in ["revenue", "growth", "increase", "decrease", "%"]
            )
        ]
        return insights[0] if insights else "See detailed analysis above"

    def _extract_next_steps(self, result: str) -> str:
        """Extract next steps from result"""
        # Simple extraction - could be enhanced with LLM
        if "next" in result.lower():
            lines = result.split("\n")
            next_lines = [line for line in lines if "next" in line.lower()]
            return (
                next_lines[0]
                if next_lines
                else "Review dashboard and metrics regularly"
            )
        return "Review dashboard and metrics regularly"

    async def start_slack_connection(self):
        """Start the Slack WebSocket connection"""
        if not self.socket_client:
            raise ValueError("Slack not initialized. Call initialize_slack() first.")

        try:
            self.logger.info("🚀 Starting Slack WebSocket connection...")

            # Connect to Slack's Socket Mode (synchronous, not async!)
            self.socket_client.connect()
            self.logger.info("✅ Connected to Slack! Meta-Agent is ready.")

        except Exception as e:
            self.logger.error(f"Failed to connect to Slack: {e}")
            raise

    async def verify_database_data(self):
        """Verify that data was actually inserted into the database"""
        try:
            if self.mcp_app:
                supabase_agent = Agent(
                    name="data_checker",
                    instruction="Query database to check for data",
                    server_names=["supabase"],
                    context=self.mcp_app.context,  # Pass MCPApp context
                )
                await supabase_agent.__aenter__()
            else:
                supabase_agent = Agent(
                    name="data_checker",
                    instruction="Query database to check for data",
                    server_names=["supabase"],
                )
                await supabase_agent.__aenter__()

            try:
                llm = await supabase_agent.attach_llm(OpenAIAugmentedLLM)

                # Ask the LLM to query the database
                result = await llm.generate_str("""
                You must call the execute_sql tool to check for data in the interactions table:
                - project_id: "qqggdvfeybfzqmgxmidt"
                - query: "SELECT user_id, message, created_at FROM interactions ORDER BY created_at DESC LIMIT 5;"
                
                Call the execute_sql tool with these parameters and show me the results.
                """)

                self.logger.info(f"📊 Database verification result: {result}")
                return result
            finally:
                # Clean up agent if we created it manually
                if not self.mcp_app:
                    try:
                        await supabase_agent.__aexit__(None, None, None)
                    except:  # noqa: E722
                        pass

        except Exception as e:
            self.logger.error(f"❌ Database verification failed: {e}")
            return None

    async def test_database_insertion(self):
        """Test method to verify database insertion works"""
        test_user_id = "test_user_123"
        test_message = "This is a test message"
        test_channel_id = "test_channel"

        try:
            # Test conversation memory storage
            await self._store_conversation_memory(
                test_user_id, test_message, test_channel_id
            )

            # Test interaction learning storage
            test_analysis = {
                "execution_strategy": "single_agent",
                "required_agents": ["knowledge_agent"],
                "complexity": "simple",
            }
            await self._store_interaction_learning(
                test_user_id, test_message, "Test response from agent", test_analysis
            )

            self.logger.info("✅ Database insertion test completed!")
            return True

        except Exception as e:
            self.logger.error(f"❌ Database test failed: {e}")
            import traceback

            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            return False

    async def add_mcp_server(self, user_id: str, server_config: Dict) -> str:
        """Allow users to add new MCP servers through chat"""
        # Implementation for dynamic server registration
        return f"Added server {server_config.get('name', 'unknown')}"

    def load_dynamic_config(self, config_path: str = None):
        """Load dynamic configuration from file or environment"""
        try:
            import os

            # Environment variable overrides
            env_overrides = {
                "CACHE_TTL_SECONDS": "cache_ttl_seconds",
                "PATTERN_CONFIDENCE_THRESHOLD": "pattern_confidence_threshold",
                "HEALTH_CHECK_INTERVAL": "health_check_interval",
                "MEMORY_CLEANUP_INTERVAL": "memory_cleanup_interval",
            }

            for env_var, config_key in env_overrides.items():
                if env_var in os.environ:
                    try:
                        value = float(os.environ[env_var])
                        self.config[config_key] = value
                        self.logger.info(f"📊 Config override: {config_key} = {value}")
                    except ValueError:
                        self.logger.warning(
                            f"Invalid config value for {env_var}: {os.environ[env_var]}"
                        )

            # TODO: Add YAML/JSON config file loading

        except Exception as e:
            self.logger.warning(f"Config loading error: {e}")

    async def get_system_health(self) -> Dict[str, Any]:
        """Get comprehensive system health metrics"""
        return {
            "pooled_agents": {
                "count": len(self.agent_pool),
                "types": list(self.agent_pool.keys()),
                "last_health_check": {
                    agent_type: check_time.isoformat()
                    if check_time != datetime.min
                    else "never"
                    for agent_type, check_time in self.agent_last_health_check.items()
                },
            },
            "cache": {
                "tools_cached": self.discovered_tools is not None,
                "cache_age_seconds": (
                    datetime.now() - self.tools_cache_timestamp
                ).total_seconds()
                if self.tools_cache_timestamp
                else None,
                "cache_ttl": self._get_cache_ttl(),
            },
            "patterns": {
                "total_patterns": len(self.dynamic_patterns),
                "usage_stats": {
                    pattern_name: {
                        "usage_count": info["usage_count"],
                        "confidence": info["confidence"],
                    }
                    for pattern_name, info in self.dynamic_patterns.items()
                },
            },
            "conversations": {
                "active_conversations": len(self.conversation_states),
                "active_requests": len(self.request_conversations),
                "last_cleanup": self.last_cleanup_time.isoformat(),
            },
        }

    async def slack_human_input_callback(self, request: HumanInputRequest) -> str:
        """Handle human input requests by sending them to Slack and waiting for response"""
        try:
            if not self.slack_client or not self.current_thread_ts:
                self.logger.warning(
                    "No Slack client or thread context for human input, falling back to console"
                )
                return console_input_callback(request)

            # Extract context from current conversation
            channel_id = getattr(self, "current_channel_id", None)
            user_id = getattr(self, "current_user_id", None)

            if not channel_id or not user_id:
                self.logger.warning(
                    "No channel/user context for human input, falling back to console"
                )
                return console_input_callback(request)

            self.logger.info(
                f"🤖 Sending human input request to Slack for user {user_id}"
            )

            # Format the human input request for Slack
            formatted_message = f"""🤖 **Agent needs more information:**

{request.prompt}

💡 **Instructions:** {request.instructions or "Please provide the requested information."}

*Reply in this thread to continue...*
"""

            # Send the human input request to Slack
            response = self.slack_client.chat_postMessage(
                channel=channel_id,
                text=formatted_message,
                parse="mrkdwn",
                thread_ts=self.current_thread_ts,
            )

            if not response["ok"]:
                self.logger.error(
                    f"Failed to send human input request to Slack: {response}"
                )
                return console_input_callback(request)

            # Create a Future to wait for the user's response
            response_future = asyncio.Future()
            self.pending_human_inputs[user_id] = response_future

            self.logger.info(f"⏳ Waiting for human input response from user {user_id}")

            # Wait for the user's response (with timeout)
            try:
                user_response = await asyncio.wait_for(
                    response_future, timeout=300.0
                )  # 5 minute timeout
                self.logger.info(
                    f"✅ Received human input response: {user_response[:50]}..."
                )
                return user_response

            except asyncio.TimeoutError:
                self.logger.warning("⏰ Human input request timed out")
                # Clean up the pending request
                if user_id in self.pending_human_inputs:
                    del self.pending_human_inputs[user_id]

                # Send timeout message to Slack
                self.slack_client.chat_postMessage(
                    channel=channel_id,
                    text="⏰ **Request timed out** - Please try your original request again.",
                    parse="mrkdwn",
                    thread_ts=self.current_thread_ts,
                )

                return "Request timed out. Please try again."

        except Exception as e:
            self.logger.error(f"Error in Slack human input callback: {e}")
            import traceback

            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            return console_input_callback(request)

    def _clean_user_input(self, message_text: str) -> str:
        """Clean user input by removing mentions, formatting, etc."""
        import re

        # Remove bot mentions (e.g., <@U0933UC9QEB>)
        clean_text = re.sub(r"<@[A-Z0-9]+>", "", message_text).strip()

        # Remove channel mentions (e.g., <#C1234567890>)
        clean_text = re.sub(r"<#[A-Z0-9]+\|[^>]+>", "", clean_text).strip()

        # Remove URL formatting (e.g., <https://example.com|example.com>)
        clean_text = re.sub(r"<[^>]+\|[^>]+>", "", clean_text).strip()

        # Remove simple URL wrapping (e.g., <https://example.com>)
        clean_text = re.sub(r"<(https?://[^>]+)>", r"\1", clean_text).strip()

        # Clean up multiple spaces
        clean_text = re.sub(r"\s+", " ", clean_text).strip()

        return clean_text

    async def cleanup(self):
        """Clean up pooled agents and connections"""
        self.logger.info("🧹 Cleaning up pooled agents...")

        # Save learning patterns before shutdown
        self._save_learning_patterns()

        for agent_type, agent in self.agent_pool.items():
            try:
                await agent.__aexit__(None, None, None)
                self.logger.info(f"✅ Cleaned up pooled {agent_type} agent")
            except Exception as e:
                self.logger.warning(f"Could not clean up {agent_type}: {e}")

        self.agent_pool.clear()
        self.agent_pool_initialized = False

        # Clean up Supabase logging
        if self.supabase_log_handler:
            try:
                self.supabase_log_handler.close()
            except Exception as e:
                print(f"⚠️ Could not clean up Supabase logging: {e}")

    async def _send_enhanced_slack_response(
        self,
        channel_id: str,
        result: str,
        analysis: Dict,
        quality_metrics: Dict,
        thread_ts: str = None,
    ):
        """Send enhanced response to Slack with quality metrics and better formatting"""
        if not self.slack_client:
            print(f"Enhanced Slack Response: {result}")
            return

        try:
            # Create status indicators based on quality metrics
            execution_time = quality_metrics.get("execution_time", 0)
            confidence = analysis.get("confidence", "unknown")
            intent_name = analysis.get("intent_name", "unknown")

            # Timing indicators
            if execution_time < 5:
                timing_emoji = "⚡"
            elif execution_time < 15:
                timing_emoji = "⏱️"
            else:
                timing_emoji = "🐌"

            # Confidence indicators
            confidence_emoji = {"high": "🎯", "medium": "✅", "low": "❓"}.get(
                confidence, "❓"
            )

            # Enhanced response formatting
            if "dashboard" in result.lower() or "deployed" in result.lower():
                # Special formatting for deliverables
                formatted_response = f"""{timing_emoji} **{intent_name.title()} Complete!**

{result}

📊 **Performance Metrics:**
• Processing time: {execution_time:.1f}s
• Intent confidence: {confidence_emoji} {confidence}
• Agents used: {quality_metrics.get("agent_count", 1)}
• Routing: {"⚡ Fast Pattern Match" if intent_name.startswith("fast_") else "🤖 LLM Routing"}

*Optimized with pre-warmed agents & caching*
"""
            else:
                # Standard response formatting with dynamic info
                routing_info = self._get_routing_info(analysis)
                pattern_info = self._get_pattern_info(analysis)

                formatted_response = f"""{confidence_emoji} **Agent Response** ({timing_emoji} {execution_time:.1f}s)

{result}

*{routing_info} | Strategy: {analysis.get("execution_strategy", "unknown")} | {pattern_info}*
"""

            self.slack_client.chat_postMessage(
                channel=channel_id,
                text=formatted_response,
                parse="mrkdwn",
                thread_ts=thread_ts,
            )

        except Exception as e:
            self.logger.error(f"Error sending enhanced Slack response: {e}")
            # Fallback to basic response
            await self._send_slack_response(channel_id, result, analysis, thread_ts)

    def _get_routing_info(self, analysis: Dict) -> str:
        """Get routing information for display"""
        intent_name = analysis.get("intent_name", "unknown")

        if intent_name.startswith("dynamic_"):
            pattern_confidence = analysis.get("pattern_confidence", 0)
            return f"⚡ Pattern Match ({pattern_confidence:.2f})"
        else:
            return "🤖 LLM Routing"

    def _get_pattern_info(self, analysis: Dict) -> str:
        """Get pattern information for display"""
        matched_keywords = analysis.get("matched_keywords", [])

        if matched_keywords:
            keywords_display = ", ".join(matched_keywords[:2])
            if len(matched_keywords) > 2:
                keywords_display += f", +{len(matched_keywords) - 2} more"
            return f"Keywords: {keywords_display}"
        else:
            return "Cached tools: ✅"

    async def test_dynamic_routing(self):
        """Test the new dynamic routing system that uses actual tool discovery"""
        test_cases = [
            {
                "message": "what tools do you have access to?",
                "expected_agent": "capability_inspector",
                "description": "Capability inquiry",
            },
            {
                "message": "what is the weather in austin, texas?",
                "expected_agent": "data_researcher",
                "description": "Weather query (real-time data)",
            },
            {
                "message": "what is the capital of France?",
                "expected_agent": "knowledge_agent",
                "description": "Basic factual question",
            },
            {
                "message": "create Q4 revenue dashboard for our SaaS metrics",
                "expected_agent": "financial_analyst",
                "description": "Financial dashboard request",
            },
            {
                "message": "use the airtable mcp to connect to this base id",
                "expected_agent": "airtable_manager",
                "description": "Airtable MCP connection request",
            },
            {
                "message": "trigger n8n workflow to update airtable",
                "expected_agent": "automation_specialist",
                "description": "n8n workflow automation",
            },
        ]

        results = []
        for test_case in test_cases:
            try:
                analysis = await self._analyze_user_intent_dynamic(test_case["message"])
                actual_agent = analysis.get("required_agents", [None])[0]
                confidence = analysis.get("confidence", "unknown")
                reasoning = analysis.get("reasoning", "")

                agent_success = actual_agent == test_case["expected_agent"]

                result = {
                    "message": test_case["message"],
                    "description": test_case["description"],
                    "expected_agent": test_case["expected_agent"],
                    "actual_agent": actual_agent,
                    "success": agent_success,
                    "confidence": confidence,
                    "reasoning": reasoning,
                }
                results.append(result)

                status = "✅" if agent_success else "❌"
                self.logger.info(
                    f"{status} {test_case['description']}: '{test_case['message']}' -> {actual_agent} (expected: {test_case['expected_agent']}) | Confidence: {confidence}"
                )

            except Exception as e:
                self.logger.error(
                    f"❌ Dynamic routing test failed for '{test_case['message']}': {e}"
                )
                results.append(
                    {
                        "message": test_case["message"],
                        "description": test_case["description"],
                        "expected_agent": test_case["expected_agent"],
                        "actual_agent": "ERROR",
                        "success": False,
                        "error": str(e),
                    }
                )

        # Summary
        successful = sum(1 for r in results if r.get("success", False))
        total = len(results)

        self.logger.info("🧪 Dynamic routing test results:")
        self.logger.info(
            f"   Success rate: {successful}/{total} ({successful / total * 100:.1f}%)"
        )

        # Check specific test cases
        weather_test = next((r for r in results if "weather" in r["message"]), None)
        if weather_test and weather_test["success"]:
            self.logger.info(
                f"✅ Weather routing working: {weather_test['actual_agent']} selected"
            )

        airtable_test = next(
            (r for r in results if "airtable mcp" in r["message"]), None
        )
        if airtable_test and airtable_test["success"]:
            self.logger.info(
                f"✅ Airtable MCP routing working: {airtable_test['actual_agent']} selected"
            )

        if successful >= total * 0.8:  # 80% success rate
            self.logger.info("🎉 Dynamic routing system is working excellently!")
        else:
            self.logger.warning(
                f"⚠️  Dynamic routing needs tuning: {successful}/{total} tests passed"
            )

        return results

    async def _dynamic_mcp_server_discovery(
        self, query_keywords: List[str]
    ) -> List[Dict]:
        """
        Dynamically discover MCP servers from database based on query keywords
        This is called when current agents don't have the required tools
        """
        try:
            self.logger.info(f"🔍 Dynamic MCP discovery for keywords: {query_keywords}")

            # Query database for MCP servers that match the keywords
            from mcp_agent.agents.agent import Agent
            from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

            if self.mcp_app:
                discovery_agent = Agent(
                    name="mcp_discovery_agent",
                    instruction="Query database for MCP server configurations",
                    server_names=["supabase"],
                    context=self.mcp_app.context,  # Pass MCPApp context
                )
                await discovery_agent.__aenter__()
            else:
                discovery_agent = Agent(
                    name="mcp_discovery_agent",
                    instruction="Query database for MCP server configurations",
                    server_names=["supabase"],
                )
                await discovery_agent.__aenter__()

            discovered_servers = []

            try:
                llm = await discovery_agent.attach_llm(OpenAIAugmentedLLM)

                # Build keyword search conditions for qualified service names
                keyword_conditions = []
                for keyword in query_keywords:
                    # Search in server_name, display_name, and description (properly qualified)
                    keyword_conditions.append(
                        f"LOWER(s.server_name) LIKE LOWER('%{keyword}%')"
                    )
                    keyword_conditions.append(
                        f"LOWER(s.display_name) LIKE LOWER('%{keyword}%')"
                    )
                    keyword_conditions.append(
                        f"LOWER(s.description) LIKE LOWER('%{keyword}%')"
                    )

                    # TODO: Add environment variables search once we know the correct column names
                    # For now, search only in main server fields

                where_clause = " OR ".join(keyword_conditions)

                self.logger.info(
                    f"🔍 Searching for qualified services with keywords: {query_keywords}"
                )

                sql_query = f"""
                SELECT 
                    s.server_name,
                    s.display_name,
                    s.description,
                    s.transport,
                    s.url,
                    s.command,
                    s.args
                FROM mcp_servers s
                JOIN mcp_configurations c ON s.configuration_id = c.id
                WHERE c.is_active = true 
                AND s.is_enabled = true
                AND ({where_clause})
                ORDER BY s.priority ASC;
                """

                prompt = f"""
                Query the database to find MCP servers that match these keywords: {query_keywords}
                
                Use the execute_sql tool with:
                - project_id: "{self.supabase_project_id}"
                - query: "{sql_query}"
                
                Execute the SQL query and return the server details.
                """

                result = await llm.generate_str(prompt)
                self.logger.info(f"🔍 Database query result: {result[:200]}...")

                # Parse the result to extract server configurations from database
                discovered_servers = self._parse_mcp_query_result(result)

                if discovered_servers:
                    self.logger.info(
                        f"✅ Found {len(discovered_servers)} specialized servers from database"
                    )
                else:
                    self.logger.info(
                        "💡 No specialized servers found in database for these keywords"
                    )
                    # NO FALLBACK - completely database-driven

                self.logger.info(
                    f"🎯 Discovered {len(discovered_servers)} matching MCP servers"
                )
                for server in discovered_servers:
                    self.logger.info(
                        f"   - {server['server_name']}: {server['description']}"
                    )

                return discovered_servers
            finally:
                # Clean up agent if we created it manually
                if not self.mcp_app:
                    try:
                        await discovery_agent.__aexit__(None, None, None)
                    except:  # noqa: E722
                        pass

        except Exception as e:
            self.logger.warning(f"Dynamic MCP discovery failed: {e}")
            import traceback

            self.logger.warning(f"Discovery error: {traceback.format_exc()}")
            return []

    async def _create_dynamic_agent_with_servers(
        self, server_configs: List[Dict], agent_name: str = "dynamic_agent"
    ) -> Optional[Agent]:
        """
        Create a temporary agent with dynamically discovered MCP servers
        """
        try:
            server_names = [config["server_name"] for config in server_configs]

            self.logger.info(f"🚀 Creating dynamic agent with servers: {server_names}")

            # Create agent with discovered servers
            if self.mcp_app:
                # Create agent with MCPApp context for proper server registry access
                dynamic_agent = Agent(
                    name=f"{agent_name}_{int(datetime.now().timestamp())}",
                    instruction=f"""You are a dynamic agent with access to specialized MCP servers: {", ".join(server_names)}.
                    
                    Use these servers to fulfill user requests. Focus on:
                    - Using the most appropriate server for each task
                    - Providing specific, actionable results
                    - Including relevant URLs, IDs, or identifiers in responses
                    
                    Available servers: {[f"{s['server_name']}: {s['description']}" for s in server_configs]}""",
                    server_names=server_names,
                    context=self.mcp_app.context,  # Pass MCPApp context
                )
                await dynamic_agent.__aenter__()
            else:
                # Fallback to direct Agent creation
                dynamic_agent = Agent(
                    name=f"{agent_name}_{int(datetime.now().timestamp())}",
                    instruction=f"""You are a dynamic agent with access to specialized MCP servers: {", ".join(server_names)}.
                    
                    Use these servers to fulfill user requests. Focus on:
                    - Using the most appropriate server for each task
                    - Providing specific, actionable results
                    - Including relevant URLs, IDs, or identifiers in responses
                    
                    Available servers: {[f"{s['server_name']}: {s['description']}" for s in server_configs]}""",
                    server_names=server_names,
                )
                await dynamic_agent.__aenter__()

            self.logger.info(
                f"✅ Dynamic agent created with {len(server_names)} servers"
            )
            return dynamic_agent

        except Exception as e:
            self.logger.error(f"Failed to create dynamic agent: {e}")
            return None


# Legacy MetaAgent class for backward compatibility
class MetaAgent(SlackMetaAgent):
    """Backward compatibility wrapper"""

    pass


async def main():
    """Main function to run the Slack Meta-Agent system"""

    # 🗂️ Initialize Supabase logging FIRST (before Meta-Agent system)
    supabase_project_id = os.getenv("SUPABASE_PROJECT_ID", "qqggdvfeybfzqmgxmidt")
    session_id, supabase_handler = setup_supabase_logging(
        project_id=supabase_project_id,
        level="INFO",
        use_session_aggregation=True,  # Use session aggregation to combine all logs into one record
    )

    print(f"🗂️ Logging Session (Aggregated): {session_id}")

    # 🎯 DATABASE CONFIGURATION SYSTEM
    # Check if we should use database configuration
    config_name = os.getenv(
        "MCP_CONFIG_NAME", "slack_meta_agent"
    )  # Default to production-ready config
    use_database_config = os.getenv("USE_DATABASE_CONFIG", "false").lower() in [
        "true",
        "1",
        "yes",
    ]

    print(
        f"📊 Configuration Mode: {'YAML + Database (Merged)' if use_database_config else 'YAML Only'}"
    )

    # Always load base YAML configuration first
    print("📄 Loading base YAML configuration...")
    from mcp_agent.config import get_settings

    base_settings = get_settings("mcp_agent.config.yaml")

    if use_database_config:
        try:
            print(
                f"🔍 Loading additional configuration '{config_name}' from database..."
            )

            # Set environment variables for database connection
            os.environ.setdefault("SUPABASE_PROJECT_ID", supabase_project_id)
            os.environ.setdefault(
                "SUPABASE_ANON_KEY",
                "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFxZ2dkdmZleWJmenFtZ3htaWR0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTExNTA2MjEsImV4cCI6MjA2NjcyNjYyMX0.aDsWKJjhYqh-Ptq63LnP5YGnMsTiXxAbI9gMi09UEs0",
            )

            # Load additional configuration from database
            db_settings = await get_settings_from_database(
                config_name=config_name,
                config_path=None,  # Don't use fallback, we already have base YAML
            )

            # Merge configurations: YAML base + Database additional
            if db_settings and db_settings.mcp and db_settings.mcp.servers:
                # Start with base YAML servers
                merged_servers = (
                    dict(base_settings.mcp.servers)
                    if base_settings.mcp and base_settings.mcp.servers
                    else {}
                )

                # Add database servers (will override YAML if same name)
                merged_servers.update(db_settings.mcp.servers)

                # Create merged settings
                from mcp_agent.config import MCPSettings

                base_settings.mcp = MCPSettings(servers=merged_servers)

                print(f"✅ Merged configuration loaded!")
                yaml_servers = (
                    list((base_settings.mcp.servers or {}).keys())
                    if base_settings.mcp and base_settings.mcp.servers
                    else []
                )
                print(f"   - YAML servers: {yaml_servers}")
                print(f"   - Database servers: {list(db_settings.mcp.servers.keys())}")
                print(f"   - Total servers: {list(merged_servers.keys())}")

                if "arc_supabase" in merged_servers:
                    print(f"   - 🔗 ARC Supabase: Connected")
            else:
                print(f"⚠️  No additional database servers found, using YAML only")

            # Create MCPApp with merged configuration
            app_instance = MCPApp(
                name="slack_meta_agent_merged",
                settings=base_settings,  # Now contains merged servers
                human_input_callback=console_input_callback,
            )

        except Exception as e:
            print(f"❌ Database configuration merge failed: {e}")
            print(f"🔄 Using YAML configuration only...")
            # Use base YAML settings
            app_instance = MCPApp(
                name="slack_meta_agent",
                settings=base_settings,
                human_input_callback=console_input_callback,
            )
    else:
        print(
            f"📄 Using YAML configuration only (set USE_DATABASE_CONFIG=true to enable merge)"
        )
        # Use base YAML configuration
        app_instance = MCPApp(
            name="slack_meta_agent",
            settings=base_settings,
            human_input_callback=console_input_callback,
        )

    # Load Slack tokens from secrets (still need this regardless of MCP config)
    from pathlib import Path

    secrets_file = Path(__file__).parent / "mcp_agent.secrets.yaml"

    if not secrets_file.exists():
        print(
            "❌ Secrets file not found. Please copy and configure mcp_agent.secrets.yaml.example"
        )
        return

    # Load secrets
    import yaml

    with open(secrets_file) as f:
        secrets = yaml.safe_load(f)

    slack_config = secrets.get("slack", {})
    bot_token = slack_config.get("bot_token")
    app_token = slack_config.get("app_token")

    if not bot_token or not app_token:
        print(
            "❌ Slack tokens not configured. Please add bot_token and app_token to secrets file."
        )
        print("🔧 Run 'python setup.py' for setup guidance.")
        return

    # Initialize the Meta-Agent system
    async with app_instance.run() as agent_app:
        logger = logging.getLogger(
            "SlackMetaAgent"
        )  # Use consistent logger for session aggregation

        # Create and initialize the meta-agent with MCPApp context
        meta_agent = SlackMetaAgent(
            supabase_project_id=supabase_project_id,
            mcp_app=agent_app,  # Pass the MCPApp instance
        )
        meta_agent.session_id = session_id
        meta_agent.supabase_log_handler = supabase_handler

        # Note: Human input will be handled through Slack message threading
        # The console_input_callback is used as fallback for non-Slack scenarios

        try:
            # Load dynamic configuration
            logger.info("📊 Loading dynamic configuration...")
            meta_agent.load_dynamic_config()

            # Initialize Slack integration
            await meta_agent.initialize_slack(bot_token, app_token)

            # 🚀 Simple performance optimizations - log as single summary
            startup_results = {
                "tool_cache": "pending",
                "agent_pool": "pending",
                "optimizations": [
                    "connection_pooling",
                    "request_isolation",
                    "pattern_routing",
                    "persistent_learning",
                ],
            }

            # Pre-populate tool cache on startup with timeout
            try:
                # Add timeout to prevent hanging
                import asyncio

                await asyncio.wait_for(
                    meta_agent._get_cached_tools(), timeout=30.0
                )  # 30 second timeout
                startup_results["tool_cache"] = "success"
            except asyncio.TimeoutError:
                startup_results["tool_cache"] = "timeout"
            except Exception as e:
                startup_results["tool_cache"] = f"failed: {str(e)[:50]}"

            # Initialize connection-pooled agents with timeout
            try:
                await asyncio.wait_for(
                    meta_agent._initialize_agent_pool(), timeout=30.0
                )
                startup_results["agent_pool"] = "success"
            except asyncio.TimeoutError:
                startup_results["agent_pool"] = "timeout"
            except Exception as e:
                startup_results["agent_pool"] = f"failed: {str(e)[:50]}"

            # Single comprehensive startup log
            logger.info(
                f"🚀 Ready - Cache: {startup_results['tool_cache']}, Pool: {startup_results['agent_pool']}"
            )

            # Test database insertion only if TEST_DB environment variable is set
            if os.getenv("TEST_DB", "false").lower() in ["true", "1", "yes"]:
                logger.info("🧪 Testing database insertion (TEST_DB=true)...")
                db_test_success = await meta_agent.test_database_insertion()
                if db_test_success:
                    logger.info("✅ Database insertion test passed!")

                    # Verify the data was actually inserted
                    logger.info("🔍 Verifying database data...")
                    verification_result = await meta_agent.verify_database_data()
                    if verification_result:
                        logger.info("✅ Database verification completed!")
                else:
                    logger.warning(
                        "⚠️  Database insertion test failed - continuing anyway"
                    )
            else:
                logger.info("💡 Skipping database test (set TEST_DB=true to enable)")

            logger.info("💡 Skipping dynamic routing startup test for faster boot")

            # Brief optimization summary
            logger.info("🎯 Optimizations: Pooling, Caching, Pattern Routing, Learning")

            # Start the WebSocket connection
            await meta_agent.start_slack_connection()

            # Keep the connection alive
            logger.info("🤖 Meta-Agent ready! Mention @meta-agent in Slack")

            while True:
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            logger.info("👋 Shutting down Meta-Agent...")

            # Clean up pre-warmed agents
            try:
                await meta_agent.cleanup()
            except Exception as e:
                logger.warning(f"Cleanup warning: {e}")

            # Finalize session logging - write all aggregated logs to Supabase
            try:
                if hasattr(supabase_handler, "finalize_session"):
                    success = supabase_handler.finalize_session()
                    if success:
                        logger.info(
                            f"✅ Session logs saved to Supabase (session: {session_id})"
                        )
                    else:
                        logger.warning("⚠️ Failed to save session logs to Supabase")
            except Exception as e:
                logger.warning(f"Session finalization error: {e}")

        except Exception as e:
            logger.error(f"💥 Meta-Agent error: {e}")

            # Clean up on error too
            try:
                await meta_agent.cleanup()
            except:  # noqa: E722
                pass

            # Still try to finalize session logs even on error
            try:
                if hasattr(supabase_handler, "finalize_session"):
                    supabase_handler.finalize_session()
                    logger.info(
                        f"✅ Session logs saved despite error (session: {session_id})"
                    )
            except Exception as cleanup_error:
                logger.warning(f"Session cleanup error: {cleanup_error}")

            raise


if __name__ == "__main__":
    import time
    from pathlib import Path

    start = time.time()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Meta-Agent shutdown complete")
    finally:
        end = time.time()
        print(f"⏱️  Total runtime: {end - start:.2f}s")
