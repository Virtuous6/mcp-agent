"""
Agent Registry Agent - Manages agent specifications and discovery.

This component handles:
1. Loading static agent definitions
2. Loading dynamic agent definitions from database
3. Caching and invalidation
4. Agent registration and updates

Extracted from SlackMetaAgent to provide clean separation of concerns.
"""

from typing import Dict, Optional, Any, List
from datetime import datetime

from ..core.types import AgentComponent
from ..models import AgentSpec, EnhancedAgentSpec


class AgentRegistryAgent(AgentComponent):
    """
    Manages the registry of available specialized agents.

    Provides both static fallback definitions and dynamic database-driven
    agent specifications with intelligent caching and fallback strategies.
    """

    def __init__(
        self, pool_manager=None, db_ops=None, config=None, cache_ttl: int = 600
    ):  # 10 minutes default cache
        super().__init__("AgentRegistry")

        self.pool_manager = pool_manager
        self.db_ops = db_ops
        self.config = config
        self.cache_ttl = cache_ttl

        # Cache management
        self._registry_cache: Optional[Dict[str, AgentSpec]] = None
        self._cache_timestamp: Optional[datetime] = None
        self._static_registry: Optional[Dict[str, AgentSpec]] = None

        # Metrics
        self._load_count = 0
        self._cache_hits = 0
        self._cache_misses = 0

        # Agent registries
        self.agents: Dict[str, AgentSpec] = {}
        self.enhanced_agents: Dict[str, EnhancedAgentSpec] = {}
        self.agent_index: Dict[str, List[str]] = {}  # capability -> [agent_names]
        self._initialize_agents()

    async def initialize(self) -> bool:
        """Initialize the agent registry."""
        try:
            # Pre-load static registry
            self._static_registry = self._load_static_registry()
            self.logger.info(
                f"✅ Loaded {len(self._static_registry)} static agent definitions"
            )

            # Try to load dynamic registry on startup
            if self.pool_manager:
                try:
                    await self.pool_manager.initialize()
                    dynamic_registry = await self._load_dynamic_registry()
                    if dynamic_registry:
                        self._registry_cache = dynamic_registry
                        self._cache_timestamp = datetime.now()
                        self.logger.info(
                            f"✅ Loaded {len(dynamic_registry)} dynamic agents on initialization"
                        )
                    else:
                        self.logger.info(
                            "📚 No dynamic agents found, will use static fallback"
                        )
                except Exception as e:
                    self.logger.warning(f"Dynamic loading failed on init: {e}")

            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize agent registry: {e}")
            return False

    async def get_agent_specs(self) -> Dict[str, AgentSpec]:
        """
        Get current agent specifications with intelligent caching.

        Strategy:
        1. Check cache validity
        2. Try dynamic loading from database
        3. Fallback to static definitions
        """
        # Check cache validity
        if self._is_cache_valid():
            self._cache_hits += 1
            self.logger.debug("📋 Using cached agent registry")
            return self._registry_cache

        self._cache_misses += 1
        self._load_count += 1

        # Try dynamic loading
        if self.pool_manager:
            try:
                self.logger.info("🔄 Loading agents dynamically from database...")
                dynamic_registry = await self._load_dynamic_registry()

                if dynamic_registry and len(dynamic_registry) > 0:
                    # Update cache
                    self._registry_cache = dynamic_registry
                    self._cache_timestamp = datetime.now()

                    self.logger.info(
                        f"✅ Loaded {len(dynamic_registry)} agents dynamically"
                    )
                    return dynamic_registry
                else:
                    self.logger.warning(
                        "⚠️ No dynamic agents found, using static fallback"
                    )

            except Exception as e:
                self.logger.warning(
                    f"Dynamic agent loading failed: {e}, using static fallback"
                )

        # Fallback to static registry
        if not self._static_registry:
            self._static_registry = self._load_static_registry()

        self.logger.info(
            f"📚 Using static agent registry ({len(self._static_registry)} agents)"
        )
        return self._static_registry

    async def get_agent_spec(self, agent_type: str) -> Optional[AgentSpec]:
        """Get specification for a specific agent type."""
        registry = await self.get_agent_specs()
        return registry.get(agent_type)

    async def register_agent(self, agent_spec: AgentSpec) -> bool:
        """Register a new agent specification (for dynamic agents)."""
        if not self.pool_manager:
            self.logger.warning("Cannot register agent: no pool manager available")
            return False

        try:
            # Store in database
            result = await self.pool_manager.insert_dynamic_agent(agent_spec)

            if result.get("success"):
                # Invalidate cache to force reload
                self._invalidate_cache()
                self.logger.info(f"✅ Registered new agent: {agent_spec.name}")
                return True
            else:
                self.logger.error(f"Failed to register agent: {result.get('error')}")
                return False

        except Exception as e:
            self.logger.error(f"Error registering agent {agent_spec.name}: {e}")
            return False

    async def update_agent(self, agent_type: str, updates: Dict[str, Any]) -> bool:
        """Update an existing agent specification."""
        if not self.pool_manager:
            self.logger.warning("Cannot update agent: no pool manager available")
            return False

        try:
            result = await self.pool_manager.update_dynamic_agent(agent_type, updates)

            if result.get("success"):
                # Invalidate cache to force reload
                self._invalidate_cache()
                self.logger.info(f"✅ Updated agent: {agent_type}")
                return True
            else:
                self.logger.error(f"Failed to update agent: {result.get('error')}")
                return False

        except Exception as e:
            self.logger.error(f"Error updating agent {agent_type}: {e}")
            return False

    async def delete_agent(self, agent_type: str) -> bool:
        """Delete an agent specification (for dynamic agents only)."""
        if not self.pool_manager:
            self.logger.warning("Cannot delete agent: no pool manager available")
            return False

        try:
            result = await self.pool_manager.delete_dynamic_agent(agent_type)

            if result.get("success"):
                # Invalidate cache to force reload
                self._invalidate_cache()
                self.logger.info(f"✅ Deleted agent: {agent_type}")
                return True
            else:
                self.logger.error(f"Failed to delete agent: {result.get('error')}")
                return False

        except Exception as e:
            self.logger.error(f"Error deleting agent {agent_type}: {e}")
            return False

    def invalidate_cache(self) -> None:
        """Manually invalidate the cache to force reload on next access."""
        self._invalidate_cache()
        self.logger.info("🔄 Agent registry cache invalidated")

    async def get_metrics(self) -> Dict[str, Any]:
        """Get registry performance metrics."""
        registry = await self.get_agent_specs()

        return {
            "total_agents": len(registry),
            "dynamic_agents": len(
                [spec for spec in registry.values() if spec.is_dynamic]
            ),
            "static_agents": len(
                [spec for spec in registry.values() if not spec.is_dynamic]
            ),
            "load_count": self._load_count,
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "cache_hit_ratio": self._cache_hits
            / (self._cache_hits + self._cache_misses)
            if (self._cache_hits + self._cache_misses) > 0
            else 0,
            "cache_valid": self._is_cache_valid(),
            "cache_age_seconds": (
                datetime.now() - self._cache_timestamp
            ).total_seconds()
            if self._cache_timestamp
            else None,
            "pool_manager_available": self.pool_manager is not None,
        }

    # Private methods

    def _is_cache_valid(self) -> bool:
        """Check if the current cache is still valid."""
        if not self._registry_cache or not self._cache_timestamp:
            return False

        age = datetime.now() - self._cache_timestamp
        return age.total_seconds() < self.cache_ttl

    def _invalidate_cache(self) -> None:
        """Invalidate the current cache."""
        self._registry_cache = None
        self._cache_timestamp = None

    async def _load_dynamic_registry(self) -> Optional[Dict[str, AgentSpec]]:
        """Load agent definitions from database."""
        if not self.pool_manager:
            return None

        try:
            result = await self.pool_manager.get_all_dynamic_agents()

            if result["success"] and result["data"]:
                registry = {}
                for agent_data in result["data"]:
                    # Extract server names from dynamic_servers column
                    server_names = agent_data.get("dynamic_servers", [])
                    if server_names and server_names[0]:  # Filter out None values
                        server_names = [s for s in server_names if s]
                    else:
                        server_names = []

                    registry[agent_data["agent_type"]] = AgentSpec(
                        name=agent_data["name"],
                        instruction=agent_data["instruction"],
                        server_names=server_names,
                        capabilities=agent_data.get("capabilities", []),
                        is_dynamic=True,
                    )

                return registry
            else:
                self.logger.warning(
                    f"No agents found in database: {result.get('error', 'Unknown error')}"
                )
                return None

        except Exception as e:
            self.logger.error(f"Failed to load agents from database: {e}")
            return None

    def _load_static_registry(self) -> Dict[str, AgentSpec]:
        """Load static agent definitions as fallback."""
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
                server_names=[],
                capabilities=[
                    "system_introspection",
                    "capability_enumeration",
                    "tool_listing",
                ],
                is_dynamic=False,
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
                server_names=[],
                capabilities=["factual_knowledge", "definitions", "basic_qa"],
                is_dynamic=False,
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
                is_dynamic=False,
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
                capabilities=["code_generation", "testing", "documentation"],
                is_dynamic=False,
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
                is_dynamic=False,
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
                is_dynamic=False,
            ),
            "communication_specialist": AgentSpec(
                name="communication_specialist",
                instruction="""You are a communication expert with access to content creation tools. 
                You can draft messages, create presentations, manage content, and facilitate communication 
                through available channels.""",
                server_names=["fetch", "supabase"],
                capabilities=["content_creation", "communication", "presentation"],
                is_dynamic=False,
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
                server_names=["Airtable", "supabase", "fetch"],
                capabilities=[
                    "workflow_automation",
                    "data_synchronization",
                    "process_automation",
                    "airtable_integration",
                ],
                is_dynamic=False,
            ),
            "airtable_manager": AgentSpec(
                name="airtable_manager",
                instruction="""You are an Airtable database specialist with expertise in managing 
                records, organizing data, and performing database operations through MCP servers.
                
                You can:
                1. Create, read, update, and delete Airtable records
                2. Query and filter Airtable data
                3. Manage table relationships and data structure
                4. Perform bulk operations on records
                5. Generate reports from Airtable data
                
                Always ensure data integrity and follow best practices for database operations.
                Use the Airtable MCP server which provides both direct Airtable access and n8n workflow capabilities.""",
                server_names=["Airtable", "fetch"],
                capabilities=[
                    "airtable_operations",
                    "database_management",
                    "record_management",
                    "data_querying",
                ],
                is_dynamic=False,
            ),
            "mcp_server_explorer": AgentSpec(
                name="mcp_server_explorer",
                instruction="""You are an MCP server exploration specialist that can systematically 
                analyze and test unknown MCP servers to discover their capabilities and extract valuable data.
                
                When users ask about unknown servers or need to explore new MCP endpoints, you can:
                1. Analyze server schemas and tool requirements
                2. Test parameter combinations intelligently  
                3. Learn from error messages to adapt approach
                4. Extract meaningful data from unknown servers
                5. Provide actionable recommendations for server usage
                
                ALWAYS get user confirmation before exploring unknown servers, as this involves:
                - Making multiple test calls to external systems
                - Potentially triggering workflows or database operations
                - Learning server capabilities through systematic testing""",
                server_names=[],
                capabilities=[
                    "server_exploration",
                    "schema_analysis",
                    "parameter_discovery",
                    "error_learning",
                    "adaptive_testing",
                ],
                is_dynamic=False,
            ),
            "mcp_server_manager": AgentSpec(
                name="mcp_server_manager",
                instruction="""You are an MCP server management specialist that helps users add new MCP servers to the system.
                
                When users want to add MCP servers, you guide them through the process by:
                1. Gathering essential server information (name, URL, transport type)
                2. Asking for missing details using human input prompts
                3. Validating server configuration before adding
                4. Storing the server configuration in the database
                5. Confirming successful addition
                
                Required information for MCP servers:
                - server_name: Unique identifier (required)
                - display_name: Human-readable name (required) 
                - description: What the server does (required)
                - transport: "stdio", "sse", "streamable_http", or "websocket" (required)
                - url: For SSE/websocket/streamable_http servers (required if transport is sse/websocket/streamable_http)
                - command: For stdio servers (required if transport is stdio)
                - args: Command arguments (optional, defaults to empty array)""",
                server_names=["supabase"],
                capabilities=[
                    "mcp_server_registration",
                    "server_validation",
                    "database_management",
                    "configuration_management",
                ],
                is_dynamic=False,
            ),
            "feedback_collector": AgentSpec(
                name="feedback_collector",
                instruction="""You are a feedback collection specialist that helps users provide feedback, suggestions, and report issues.
                
                When users want to give feedback, you should:
                1. Welcome their feedback warmly and professionally
                2. If they just say they want to give feedback, ask them what their feedback is about
                3. If they provide feedback directly, acknowledge it and collect it
                4. Categorize feedback appropriately (general, bug_report, feature_request, improvement, etc.)
                5. Store the feedback in the database with proper metadata
                6. Thank them and let them know their feedback has been recorded
                7. Ask if they have any additional feedback
                
                Types of feedback to handle:
                - General feedback about the system
                - Bug reports and issues
                - Feature requests
                - Suggestions for improvements
                - User experience feedback""",
                server_names=["supabase"],
                capabilities=[
                    "feedback_collection",
                    "feedback_categorization",
                    "database_storage",
                    "user_interaction",
                ],
                is_dynamic=False,
            ),
        }

    async def health_check(self) -> Dict[str, Any]:
        """Check component health."""
        base_health = await super().health_check()
        metrics = await self.get_metrics()

        return {
            **base_health,
            **metrics,
            "pool_manager_status": "available" if self.pool_manager else "unavailable",
            "cache_status": "valid" if self._is_cache_valid() else "invalid",
        }

    def _initialize_agents(self):
        """Initialize agent registry with enhanced specs"""

        # Define enhanced agent specifications
        self.enhanced_agents = {
            "intent_analyzer": EnhancedAgentSpec(
                id="intent_analyzer",
                name="Intent Classification Specialist",
                role="Analyze user messages and classify intent with high accuracy",
                backstory="You are a linguistic expert trained in understanding user intent across various communication styles. You've analyzed millions of conversations and can quickly identify patterns, context, and underlying needs.",
                goal="Accurately classify user intent to enable optimal agent selection and execution strategy",
                llm_model="gpt-4o-mini",
                temperature=0.1,
                tools=["pattern_matcher", "context_analyzer"],
                constraints=[
                    "Prefer SINGLE_AGENT for simple requests",
                    "Only suggest ORCHESTRATED for genuinely complex multi-step tasks",
                    "Always provide confidence assessment",
                ],
                system_prompt="You are an expert at understanding user intent. Focus on: 1) Identifying the core request, 2) Determining complexity, 3) Selecting appropriate execution strategy.",
                capabilities=["intent_classification", "complexity_assessment"],
                is_dynamic=False,
            ),
            "orchestrator": EnhancedAgentSpec(
                id="orchestrator",
                name="Master Execution Planner",
                role="Build and execute sophisticated multi-agent plans",
                backstory="You are a strategic mastermind with deep experience in project management and systems thinking. You excel at breaking down complex problems into actionable steps and coordinating teams.",
                goal="Ensure every user query is answered completely through optimal agent coordination",
                llm_model="gpt-4o",
                temperature=0.1,
                allow_delegation=True,
                tools=["plan_builder", "task_monitor", "result_validator"],
                constraints=[
                    "Build minimal but complete plans",
                    "Validate results match user needs",
                    "Monitor execution for quality",
                ],
                system_prompt="You are the execution brain. Your job is to: 1) Analyze queries deeply, 2) Build minimal but complete plans, 3) Monitor execution, 4) Validate results match user needs.",
                few_shot_examples=[
                    {
                        "query": "What's the weather in NYC?",
                        "response": "Single task with data_researcher to fetch current NYC weather",
                    }
                ],
                capabilities=[
                    "plan_building",
                    "task_orchestration",
                    "result_validation",
                ],
                is_dynamic=False,
            ),
            "data_researcher": EnhancedAgentSpec(
                id="data_researcher",
                name="Senior Research Analyst",
                role="Gather real-time data and conduct thorough research",
                backstory="You are a meticulous researcher with a background in investigative journalism and data science. You never accept surface-level information and always dig deeper to find accurate, current data.",
                goal="Provide accurate, real-time information with proper sources",
                llm_model="gpt-4o",
                temperature=0.2,
                tools=["brave_search", "fetch", "supabase"],
                constraints=[
                    "Always use tools for current data",
                    "Provide specific numbers and details",
                    "Include data freshness timestamps",
                ],
                system_prompt="You MUST use your tools to get real-time data. Never guess or use training data for current information. Always: 1) Search for sources, 2) Fetch actual data, 3) Provide specific details with timestamps.",
                capabilities=["research", "data_analysis", "report_generation"],
                is_dynamic=False,
            ),
            "supabase_analyst": EnhancedAgentSpec(
                id="supabase_analyst",
                name="Database Architecture Expert",
                role="Analyze and manage Supabase database schemas and data",
                backstory="You are a database architect with deep expertise in PostgreSQL and Supabase. You've designed and optimized hundreds of database schemas and understand the intricacies of data modeling, performance tuning, and security.",
                goal="Provide expert database analysis, optimization, and management for Supabase projects",
                llm_model="gpt-4o",
                temperature=0.1,
                tools=["supabase"],
                constraints=[
                    "Always verify schema before operations",
                    "Provide performance implications",
                    "Consider security and RLS policies",
                ],
                system_prompt="You are a Supabase expert. Focus on: 1) Analyzing existing schemas, 2) Optimizing queries, 3) Ensuring security best practices, 4) Providing clear explanations of database operations.",
                capabilities=[
                    "database_analysis",
                    "schema_design",
                    "query_optimization",
                    "data_management",
                ],
                is_dynamic=False,
            ),
            # ... add more enhanced specs for other agents
        }

        # Convert enhanced specs to legacy specs for backward compatibility
        for agent_id, enhanced_spec in self.enhanced_agents.items():
            self.agents[agent_id] = enhanced_spec.to_legacy_spec()

        # Also include legacy specs that haven't been enhanced yet
        legacy_agents = {
            # ... existing code ...
        }

    async def load_agent_spec(self, agent_id: str) -> Optional[EnhancedAgentSpec]:
        """Load enhanced agent spec from database or fallback to code."""
        try:
            # Try database first if pool_manager is available
            if self.pool_manager and hasattr(self.pool_manager, "supabase_client"):
                response = (
                    await self.pool_manager.supabase_client.table("agent_specs")
                    .select("*")
                    .eq("id", agent_id)
                    .single()
                    .execute()
                )
                if response.data:
                    return EnhancedAgentSpec.from_dict(response.data)
        except Exception as e:
            self.logger.warning(f"Could not load from DB: {e}")

        # Fallback to enhanced static specs
        return self.enhanced_agents.get(agent_id)

    async def save_agent_spec(self, spec: EnhancedAgentSpec) -> bool:
        """Save or update an agent spec in the database."""
        try:
            if self.pool_manager and hasattr(self.pool_manager, "supabase_client"):
                data = spec.to_dict()
                response = (
                    await self.pool_manager.supabase_client.table("agent_specs")
                    .upsert(data)
                    .execute()
                )
                return bool(response.data)
        except Exception as e:
            self.logger.error(f"Failed to save agent spec: {e}")
        return False

    async def refresh_specs_from_db(self):
        """Refresh all agent specs from database."""
        try:
            if self.pool_manager and hasattr(self.pool_manager, "supabase_client"):
                response = (
                    await self.pool_manager.supabase_client.table("agent_specs")
                    .select("*")
                    .execute()
                )
                if response.data:
                    for spec_data in response.data:
                        spec = EnhancedAgentSpec.from_dict(spec_data)
                        self.enhanced_agents[spec.id] = spec
                        # Also update legacy specs for compatibility
                        self.agents[spec.id] = spec.to_legacy_spec()
                    self._build_capability_index()
                    self.logger.info(
                        f"Loaded {len(response.data)} agent specs from database"
                    )
        except Exception as e:
            self.logger.warning(f"Failed to refresh specs from DB: {e}")
