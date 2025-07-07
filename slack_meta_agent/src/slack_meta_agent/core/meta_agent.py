"""
Core SlackMetaAgent class - refactored from monolithic main.py

This module contains the main SlackMetaAgent class with core functionality,
delegating specific concerns to specialized modules.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Set

from mcp_agent.app import MCPApp
from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
from mcp_agent.workflows.orchestrator.orchestrator import Orchestrator
from mcp_agent.human_input.types import HumanInputRequest, HumanInputResponse

from .conversation import ConversationState, ConversationTurn
from .agent_spec import AgentSpec
from ..database.supabase_operations import SupabaseOperations
from ..database.supabase_pool_manager import SupabasePoolManager

# from ..routing.intent_analyzer import IntentAnalyzer  # Not using the separate class, using original method
from ..slack.slack_client import SlackClientManager
from ..mcp.server_discovery import MCPServerDiscovery
from ..utils.config_loader import ConfigLoader

# Import direct Supabase client for reliability
try:
    from ..database.supabase_client import SupabaseDirectClient
except ImportError:
    SupabaseDirectClient = None

# Import universal MCP exploration strategy
try:
    from ..utils.mcp_strategy import (
        explore_any_mcp_server,
        print_exploration_results,
    )
except ImportError:
    explore_any_mcp_server = None
    print_exploration_results = None

# Import CrewAI for multi-agent workflows
try:
    from crewai import Agent as CrewAgent, Task, Crew, Process
    from crewai.tools import BaseTool
    from crewai_tools import SerperDevTool, WebsiteSearchTool

    CREWAI_AVAILABLE = True
except ImportError:
    CrewAgent = None
    Task = None
    Crew = None
    Process = None
    BaseTool = None
    SerperDevTool = None
    WebsiteSearchTool = None
    CREWAI_AVAILABLE = False


class SlackMetaAgent:
    """
    The main meta-agent that orchestrates specialized agents and handles Slack interactions.

    This class focuses on high-level coordination and delegates specific concerns
    to specialized modules.
    """

    def __init__(self, supabase_project_id: str = None, mcp_app: MCPApp = None):
        self.logger = logging.getLogger("SlackMetaAgent")
        self.supabase_project_id = supabase_project_id
        self.mcp_app = mcp_app

        # Initialize specialized modules
        self.config = ConfigLoader()
        self.db_ops = SupabaseOperations(supabase_project_id)
        # self.intent_analyzer = IntentAnalyzer(self.config)  # Not using separate class, using original method
        self.slack_manager = SlackClientManager()
        self.mcp_discovery = MCPServerDiscovery(mcp_app, supabase_project_id)

        # High-performance database pool manager
        self.pool_manager: Optional[SupabasePoolManager] = None

        # Core state
        self.conversation_states: Dict[str, ConversationState] = {}
        # 🔄 CHANGE: Use dynamic agent registry by default, fallback to static
        self.agent_registry: Dict[str, AgentSpec] = {}  # Will be populated dynamically
        self.session_id = None

        # Agent pool and management
        self.agent_pool: Dict[str, Agent] = {}
        self.agent_pool_initialized = False
        self.agent_last_health_check = {}

        # Add message deduplication tracking
        self.processed_messages: Set[str] = set()
        self.last_cleanup_time = datetime.now()

        # Initialize direct Supabase client for reliability
        self.supabase_direct_client: Optional[SupabaseDirectClient] = None
        if supabase_project_id and SupabaseDirectClient:
            try:
                # Try to get keys from mcp_agent.secrets.yaml first, then environment
                anon_key = None
                service_role_key = None

                # Load from secrets YAML file - check multiple possible paths
                secrets_paths = [
                    "config/mcp_agent.secrets.yaml",  # Main refactored script path
                    "mcp_agent.secrets.yaml",  # Current directory fallback
                    "slack_meta_agent/config/mcp_agent.secrets.yaml",  # Full path fallback
                ]

                secrets_loaded = False
                for secrets_file in secrets_paths:
                    if os.path.exists(secrets_file):
                        import yaml

                        with open(secrets_file, "r") as f:
                            secrets = yaml.safe_load(f)
                            if "supabase" in secrets:
                                anon_key = secrets["supabase"].get("anon_key")
                                service_role_key = secrets["supabase"].get(
                                    "service_role_key"
                                )
                                self.logger.info(
                                    f"📄 Loaded Supabase credentials from {secrets_file}"
                                )
                                secrets_loaded = True
                                break

                # Fallback to environment variables
                if not anon_key and not service_role_key:
                    anon_key = os.getenv("SUPABASE_ANON_KEY")
                    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
                    if anon_key or service_role_key:
                        self.logger.info(
                            "🌍 Using Supabase credentials from environment variables"
                        )
                        secrets_loaded = True

                if anon_key or service_role_key:
                    self.supabase_direct_client = SupabaseDirectClient(
                        project_id=supabase_project_id,
                        anon_key=anon_key,
                        service_role_key=service_role_key,
                    )
                    self.logger.info(
                        "✅ Direct Supabase client initialized for reliable operations"
                    )

                    # Initialize high-performance pool manager
                    self.pool_manager = SupabasePoolManager(
                        project_id=supabase_project_id,
                        anon_key=anon_key,
                        service_role_key=service_role_key,
                        max_connections=8,  # Optimized for meta-agent workload
                        default_cache_ttl=300,  # 5 minutes default cache
                        enable_metrics=True,
                    )
                    self.logger.info(
                        "⚡ High-performance Supabase pool manager initialized"
                    )
                else:
                    if not secrets_loaded:
                        self.logger.warning(
                            "⚠️ No secrets file found at expected paths and no environment variables - direct client disabled"
                        )
                        self.logger.info(
                            f"💡 Checked paths: {', '.join(secrets_paths)}"
                        )
                    else:
                        self.logger.warning(
                            "⚠️ Supabase credentials found but missing keys - direct client disabled"
                        )
            except Exception as e:
                self.logger.warning(
                    f"⚠️ Could not initialize direct Supabase client: {e}"
                )

        # Dynamic tool discovery cache
        self.discovered_tools: Optional[Dict[str, Dict]] = None
        self.tools_cache_timestamp: Optional[datetime] = None

        # Human input handling for Slack
        self.pending_human_inputs: Dict[
            str, asyncio.Future
        ] = {}  # user_id -> Future[str]
        self.current_thread_ts: Optional[str] = (
            None  # Track current thread for human input
        )
        self.current_user_id: Optional[str] = None
        self.current_channel_id: Optional[str] = None

        # Request-level conversation isolation (key: request_id)
        self.request_conversations: Dict[str, Dict] = {}

        # Configuration with defaults from main.py
        self.config_dict = {
            "cache_ttl_seconds": 1800,  # 30 minutes
            "pattern_confidence_threshold": 0.8,
            "health_check_interval": 300,  # 5 minutes
            "memory_cleanup_interval": 3600,  # 1 hour
            "learning_persistence_file": "slack_meta_agent/data/pattern_learning.json",
        }

        # Load persistent learning patterns (will be optimized after async init)
        self.dynamic_patterns = self._load_learning_patterns()
        self._patterns_loaded_from_db = False

        # Usage tracking
        self.agent_usage_stats = {}

        # MCPApp callback override tracking
        self.mcp_callback_override_success = False

        # Legacy conversation memory for backward compatibility
        self.conversation_memory: Dict[str, List[Dict]] = {}

    async def initialize_optimized_components(self):
        """Initialize optimized database components after async context is available"""
        if not self.pool_manager:
            self.logger.info(
                "⚠️ Pool manager not available, loading agents from fallback"
            )
            # Load fallback static registry if no pool manager
            self.agent_registry = self._initialize_agent_registry()
            return

        try:
            self.logger.info("⚡ Initializing dynamic agent system...")

            # Initialize pool manager
            await self.pool_manager.initialize()

            # 🎯 PRIORITY: Load agents from database first
            try:
                dynamic_registry = await self._initialize_agent_registry_dynamic()
                if dynamic_registry and len(dynamic_registry) > 0:
                    self.agent_registry = dynamic_registry
                    self.logger.info(
                        f"✅ Loaded {len(dynamic_registry)} agents dynamically from database"
                    )
                else:
                    self.logger.warning(
                        "⚠️ No dynamic agents found, using static fallback"
                    )
                    self.agent_registry = self._initialize_agent_registry()
            except Exception as e:
                self.logger.warning(
                    f"Dynamic agent loading failed: {e}, using static fallback"
                )
                self.agent_registry = self._initialize_agent_registry()

            # Load optimized patterns if not already loaded
            if not self._patterns_loaded_from_db:
                optimized_patterns = await self._load_learning_patterns_optimized()
                if optimized_patterns and len(optimized_patterns) > 0:
                    self.dynamic_patterns = optimized_patterns
                    self._patterns_loaded_from_db = True
                    self.logger.info(
                        f"✅ Loaded {len(optimized_patterns)} patterns from database"
                    )
                else:
                    self.logger.info("📚 Using default patterns")

            self.logger.info("✅ Dynamic agent system initialized successfully")

        except Exception as e:
            self.logger.error(f"Failed to initialize dynamic components: {e}")
            self.logger.info("🔄 Loading static fallback components")
            self.agent_registry = self._initialize_agent_registry()

    async def get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics from the pool manager"""
        if not self.pool_manager:
            return {"error": "Pool manager not available"}

        return self.pool_manager.get_performance_metrics()

    async def _initialize_agent_registry_dynamic(self) -> Dict[str, AgentSpec]:
        """Load agent definitions from Supabase dynamically (OPTIMIZED VERSION)"""
        if not self.pool_manager:
            # Fallback to original method if pool manager not available
            return self._initialize_agent_registry()

        try:
            await self.pool_manager.initialize()
            self.logger.info("📚 Loading agents from database with optimized pool...")

            # Use optimized agent loading
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
                        metadata=agent_data.get("metadata", {}),
                    )

                self.logger.info(
                    f"✅ Loaded {len(registry)} agents from database (cached for 10min)"
                )
                return registry
            else:
                self.logger.warning(
                    f"No agents found in database: {result.get('error', 'Unknown error')}"
                )
                return self._initialize_agent_registry()  # Fallback

        except Exception as e:
            self.logger.error(f"Failed to load agents from database: {e}")
            return self._initialize_agent_registry()  # Fallback

    def _initialize_agent_registry(self) -> Dict[str, AgentSpec]:
        """Initialize the registry of available specialized agents - FULL VERSION from main.py"""
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
                server_names=["Airtable", "supabase", "fetch"],
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
                - Learning server capabilities through systematic testing
                
                Use the human input callback to:
                1. Explain what exploration will involve
                2. Get explicit permission before starting
                3. Share findings and ask for next steps
                4. Confirm before making any potentially destructive operations""",
                server_names=[],  # Uses dynamic server configuration
                capabilities=[
                    "server_exploration",
                    "schema_analysis",
                    "parameter_discovery",
                    "error_learning",
                    "adaptive_testing",
                ],
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
                - args: Command arguments (optional, defaults to empty array)
                
                Be conversational and helpful. Adapt to what the user provides and ask for missing info naturally.""",
                server_names=["supabase"],
                capabilities=[
                    "mcp_server_registration",
                    "server_validation",
                    "database_management",
                    "configuration_management",
                ],
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
                - User experience feedback
                
                Always be encouraging and make users feel heard. Their feedback is valuable for improving the system.""",
                server_names=["supabase"],
                capabilities=[
                    "feedback_collection",
                    "feedback_categorization",
                    "database_storage",
                    "user_interaction",
                ],
            ),
        }

    # Add all the missing core methods from main.py
    async def _load_learning_patterns_optimized(self) -> Dict[str, Dict]:
        """Load persistent learning patterns from database with optimized caching"""
        if not self.pool_manager:
            return self._load_learning_patterns()

        try:
            self.logger.info("📚 Loading learning patterns with optimized pool...")

            result = await self.pool_manager.get_learning_patterns_optimized()

            if result["success"] and result["data"]:
                pattern_dict = {}
                for pattern in result["data"]:
                    target = (
                        pattern["agent_name"]
                        or pattern["workflow_name"]
                        or pattern["crew_name"]
                    )

                    pattern_dict[pattern["name"]] = {
                        "keywords": pattern["keywords"],
                        "agent": pattern["agent_name"],
                        "workflow": pattern["workflow_name"],
                        "crew": pattern["crew_name"],
                        "target": target,
                        "confidence": pattern["confidence"],
                        "usage_count": pattern["usage_count"],
                        "success_rate": pattern.get("success_rate", 0.0),
                        "context_filter": pattern.get("context_filter", {}),
                    }

                self.logger.info(
                    f"✅ Loaded {len(pattern_dict)} patterns from database (cached for 10min)"
                )
                return pattern_dict
            else:
                self.logger.warning("No patterns found in database, using defaults")
                return self._load_learning_patterns()  # Fallback

        except Exception as e:
            self.logger.error(f"Failed to load patterns from database: {e}")
            return self._load_learning_patterns()  # Fallback

    def _load_learning_patterns(self) -> Dict[str, Dict]:
        """Load persistent learning patterns from file or create defaults"""
        try:
            patterns_file = self.config_dict["learning_persistence_file"]
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
                    "workflow",
                    "automate",
                    "trigger",
                    "automation",
                    "zapier",
                    "airtable workflow",
                ],
                "agent": "automation_specialist",
                "confidence": 0.85,
                "usage_count": 0,
            },
            "airtable": {
                "keywords": [
                    "airtable",
                    "air table",
                    "airtable base",
                    "airtable records",
                    "airtable data",
                    "base id",
                    "table records",
                    "airtable api",
                    "airtable database",
                    "records in airtable",
                    "update airtable",
                    "create airtable",
                    "delete airtable",
                    "query airtable",
                ],
                "agent": "airtable_manager",
                "confidence": 0.9,
                "usage_count": 0,
            },
            "server_exploration": {
                "keywords": [
                    "unknown server",
                    "explore server",
                    "test server",
                    "server capabilities",
                    "discover tools",
                    "mcp server",
                    "new endpoint",
                    "what tools does",
                    "how to use",
                    "server analysis",
                ],
                "agent": "mcp_server_explorer",
                "confidence": 0.9,
                "usage_count": 0,
            },
            "add_mcp_server": {
                "keywords": [
                    "add mcp server",
                    "add mcp",
                    "register mcp",
                    "new mcp server",
                    "connect mcp",
                    "add server",
                    "register server",
                    "create mcp",
                    "setup mcp",
                    "configure mcp",
                ],
                "agent": "mcp_server_manager",
                "confidence": 0.95,
                "usage_count": 0,
            },
            "feedback": {
                "keywords": [
                    "feedback",
                    "give feedback",
                    "provide feedback",
                    "share feedback",
                    "i'd like to give feedback",
                    "i want to give feedback",
                    "here is feedback",
                    "here's feedback",
                    "my feedback",
                    "feedback on",
                    "suggestion",
                    "improvement",
                    "issue with",
                    "problem with",
                    "bug report",
                    "feature request",
                ],
                "agent": "feedback_collector",
                "confidence": 0.90,
                "usage_count": 0,
            },
        }

        self.logger.info("📚 Using default learning patterns")
        return default_patterns

    def _save_learning_patterns(self):
        """Persist learning patterns to file"""
        try:
            patterns_file = self.config_dict["learning_persistence_file"]
            os.makedirs(os.path.dirname(patterns_file), exist_ok=True)

            with open(patterns_file, "w") as f:
                json.dump(self.dynamic_patterns, f, indent=2)

            self.logger.debug(f"💾 Saved learning patterns to {patterns_file}")
        except Exception as e:
            self.logger.warning(f"Could not save learning patterns: {e}")

    def _update_pattern_learning(self, message: str, agent_used: str, success: bool):
        """Update learning patterns based on interaction success"""
        message_words = set(message.lower().split())

        # Create agent entry if it doesn't exist in dynamic patterns
        if agent_used not in self.dynamic_patterns:
            self.dynamic_patterns[agent_used] = {
                "keywords": [],
                "agent": agent_used,
                "confidence": 0.7,
                "usage_count": 0,
            }

        # Track successful keywords
        if success:
            for word in message_words:
                if len(word) > 3:  # Ignore short words
                    # Add word to keywords if not already present
                    if word not in self.dynamic_patterns[agent_used]["keywords"]:
                        self.dynamic_patterns[agent_used]["keywords"].append(word)

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
                                if (
                                    compound_keyword
                                    not in self.dynamic_patterns[agent_used]["keywords"]
                                ):
                                    self.dynamic_patterns[agent_used][
                                        "keywords"
                                    ].append(compound_keyword)
                                    self.logger.debug(
                                        f"📚 Added compound keyword: {compound_keyword} -> {agent_used}"
                                    )
                                break  # Only add one new keyword per successful interaction

        # Persist learning after updates
        self._save_learning_patterns()

    def _is_mcp_server_addition_request(self, message: str) -> bool:
        """Check if the message is requesting to add a new MCP server"""
        message_lower = message.lower()

        # 🚨 CRITICAL: Check for feedback context first - if this is clearly feedback,
        # don't treat "add" phrases as server addition requests
        feedback_indicators = [
            "i have feedback",
            "give feedback",
            "providing feedback",
            "my feedback",
            "here's feedback",
            "feedback:",
            "suggestion:",
            "feature request:",
        ]

        has_feedback_context = any(
            indicator in message_lower for indicator in feedback_indicators
        )

        if has_feedback_context:
            self.logger.debug(
                f"💬 Feedback context detected - not treating as MCP server addition: '{message[:50]}...'"
            )
            return False

        # Strong indicators that this is an addition request (not discovery)
        addition_indicators = [
            "add mcp",
            "add new mcp",
            "register mcp",
            "create mcp",
            "setup mcp",
            "configure mcp",
            "install mcp",
            "connect new mcp",
            "add server",
            "register server",
            "create server",
            "setup server",
            "new mcp server",
            "add mcp server",
            "register mcp server",
            "create mcp server",
            "i'd like to add",
            "i want to add",
            "i need to add",
            "like to add",
            "want to add",
            "need to add",
            "i have an mcp server",
            "i have a server",
            "add a server",
            "add an mcp",
            "to add",
            "server to add",
            "mcp to add",
        ]

        # Check for action + server patterns
        has_addition_indicator = any(
            indicator in message_lower for indicator in addition_indicators
        )

        # Additional patterns with action verbs
        action_server_patterns = [
            r"\b(add|register|create|setup|configure|install|connect)\s+.{0,10}mcp",
            r"\b(add|register|create|setup|configure|install|connect)\s+.{0,10}server",
            r"new\s+mcp\s+server",
            r"mcp\s+server\s+called",
            r"mcp\s+server\s+named",
        ]

        has_action_pattern = any(
            re.search(pattern, message_lower) for pattern in action_server_patterns
        )

        # Exclude discovery patterns that might confuse it
        discovery_exclusions = [
            "explore mcp",
            "test mcp",
            "discover mcp",
            "find mcp",
            "list mcp",
            "show mcp",
            "what mcp",
            "which mcp",
            "available mcp",
        ]

        has_discovery_pattern = any(
            exclusion in message_lower for exclusion in discovery_exclusions
        )

        # Additional exclusions for feedback/suggestion contexts
        feedback_exclusions = [
            "suggest",
            "suggestion",
            "would be nice",
            "please add",
            "should add",
            "could add",
            "you add",
            "can you add",
        ]

        has_feedback_exclusion = any(
            exclusion in message_lower for exclusion in feedback_exclusions
        )

        # Return true if we have addition indicators and no discovery/feedback patterns
        result = (
            (has_addition_indicator or has_action_pattern)
            and not has_discovery_pattern
            and not has_feedback_exclusion
        )

        if result:
            self.logger.info(
                f"🔧 Detected MCP server addition request: '{message[:50]}...'"
            )

        return result

    def _dynamic_pattern_match(self, message: str) -> Optional[Dict[str, Any]]:
        """Advanced dynamic pattern matching with adaptive confidence and contextual analysis"""
        message_lower = message.lower()

        # CHECK: Avoid pattern matching for organization-qualified requests
        # These should be handled by dynamic discovery instead
        org_service_patterns = [
            r"\b(?:our\s+)?([A-Z]{2,10})\s+(supabase|database|db|airtable|n8n|api|webhook|integration|server|service)\b"
        ]
        has_org_qualifier = any(
            re.search(pattern, message, re.IGNORECASE)
            for pattern in org_service_patterns
        )

        if has_org_qualifier:
            self.logger.info(
                f"⚠️ Skipping pattern matching for organization-qualified request: {message[:50]}..."
            )
            return None

        best_match = None
        best_confidence = 0.0

        # Dynamic confidence threshold based on pattern performance
        base_threshold = self.config_dict["pattern_confidence_threshold"]
        adaptive_threshold = self._get_adaptive_threshold(base_threshold)

        for pattern_name, pattern_info in self.dynamic_patterns.items():
            # Multi-level confidence calculation
            confidence_scores = self._calculate_multi_level_confidence(
                message, message_lower, pattern_info, pattern_name
            )

            # Take the highest confidence from different matching strategies
            max_confidence = max(confidence_scores.values())

            if (
                max_confidence > best_confidence
                and max_confidence >= adaptive_threshold
            ):
                best_confidence = max_confidence
                best_match = {
                    "agent": pattern_info["agent"],
                    "pattern": pattern_name,
                    "confidence": max_confidence,
                    "matched_keywords": confidence_scores.get("keyword_matches", []),
                    "match_strategy": max(confidence_scores, key=confidence_scores.get),
                    "all_scores": confidence_scores,
                    "threshold_used": adaptive_threshold,
                }

        # Real-time pattern learning
        if best_match:
            self._update_pattern_success(
                best_match["pattern"], message, best_match["confidence"]
            )
        else:
            # Learn from failed matches - could indicate need for new patterns
            self._analyze_failed_match(message, message_lower)

        return best_match

    def _calculate_multi_level_confidence(
        self, message: str, message_lower: str, pattern_info: Dict, pattern_name: str
    ) -> Dict[str, float]:
        """Calculate confidence using multiple matching strategies"""
        scores = {}

        # 1. Exact Keyword Matching (Original approach, improved)
        scores["exact_keywords"] = self._calculate_exact_keyword_confidence(
            message_lower, pattern_info
        )

        # 2. Fuzzy/Partial Keyword Matching
        scores["fuzzy_keywords"] = self._calculate_fuzzy_keyword_confidence(
            message_lower, pattern_info
        )

        # 3. Contextual Pattern Matching
        scores["contextual"] = self._calculate_contextual_confidence(
            message, message_lower, pattern_info, pattern_name
        )

        # 4. Semantic Structure Analysis
        scores["semantic"] = self._calculate_semantic_confidence(
            message, pattern_info, pattern_name
        )

        # 5. User History Pattern Matching (if available)
        scores["user_history"] = self._calculate_user_history_confidence(
            message, pattern_info, pattern_name
        )

        return scores

    def _calculate_exact_keyword_confidence(
        self, message_lower: str, pattern_info: Dict
    ) -> float:
        """Improved exact keyword matching with better scoring"""
        keyword_matches = []
        for keyword in pattern_info["keywords"]:
            if keyword in message_lower:
                keyword_matches.append(keyword)

        if not keyword_matches:
            return 0.0

        # Improved confidence calculation
        total_keywords = len(pattern_info["keywords"])

        if total_keywords <= 5:
            # Small keyword sets: use simple ratio
            match_ratio = len(keyword_matches) / total_keywords
        else:
            # Large keyword sets: use logarithmic scaling
            max_useful_matches = min(5, total_keywords)
            effective_matches = min(len(keyword_matches), max_useful_matches)
            match_ratio = effective_matches / max_useful_matches

        # Usage boost based on historical success
        usage_boost = min(1.3, 1.0 + (pattern_info["usage_count"] / 50))

        # Keyword length bonus (longer, more specific keywords get higher scores)
        length_bonus = 1.0 + sum(
            0.1 for keyword in keyword_matches if len(keyword) > 10
        )

        confidence = (
            pattern_info["confidence"] * match_ratio * usage_boost * length_bonus
        )
        return min(confidence, 1.0)  # Cap at 1.0

    def _calculate_fuzzy_keyword_confidence(
        self, message_lower: str, pattern_info: Dict
    ) -> float:
        """Fuzzy matching for partial keyword matches"""
        fuzzy_matches = []

        # Common abbreviations mapping
        abbreviations = {
            "temp": ["temperature"],
            "temps": ["temperature"],
            "feedback": ["comment", "suggestion", "opinion", "review"],
            "capabilities": ["tools", "features", "functions", "help"],
        }

        for keyword in pattern_info["keywords"]:
            # Check for partial matches and common variations
            if len(keyword) > 3:  # Only fuzzy match longer keywords
                # Split compound keywords and check parts
                keyword_parts = keyword.replace("_", " ").split()

                if len(keyword_parts) > 1:
                    # Multi-word keyword: check if most words are present
                    found_parts = sum(
                        1 for part in keyword_parts if part in message_lower
                    )
                    if (
                        found_parts >= len(keyword_parts) * 0.6
                    ):  # Reduced from 70% to 60%
                        fuzzy_matches.append(keyword)
                else:
                    # Single word: check for partial matches and abbreviations
                    if len(keyword) > 6:  # Only for longer words
                        for word in message_lower.split():
                            # Original partial matching
                            if keyword in word or word in keyword:
                                if abs(len(keyword) - len(word)) <= 3:  # Similar length
                                    fuzzy_matches.append(keyword)
                                    break

                            # NEW: Check abbreviation mappings
                            if word in abbreviations:
                                if keyword in abbreviations[word]:
                                    fuzzy_matches.append(keyword)
                                    break

        if not fuzzy_matches:
            return 0.0

        # Calculate fuzzy confidence (lower than exact matches)
        fuzzy_ratio = len(fuzzy_matches) / len(pattern_info["keywords"])
        fuzzy_confidence = (
            pattern_info["confidence"] * fuzzy_ratio * 0.7
        )  # 70% of full confidence

        return min(fuzzy_confidence, 0.8)  # Cap fuzzy matches at 0.8

    def _calculate_contextual_confidence(
        self, message: str, message_lower: str, pattern_info: Dict, pattern_name: str
    ) -> float:
        """Analyze message context and structure for pattern matching"""
        contextual_score = 0.0

        # Check for intent indicators specific to each pattern
        if pattern_name == "feedback":
            contextual_indicators = [
                # Direct feedback phrases
                ("feedback", 0.9),
                ("give feedback", 0.95),
                ("my feedback", 0.9),
                ("here's feedback", 0.95),
                # Feedback-like structures
                ("suggestion", 0.7),
                ("improvement", 0.6),
                ("issue with", 0.7),
                ("problem with", 0.7),
                ("bug", 0.8),
                ("feature request", 0.8),
                # Question patterns that might be feedback
                ("can you add", 0.5),
                ("add a feature", 0.75),  # NEW
                ("add feature", 0.70),  # NEW
                ("would be nice", 0.6),
                ("should have", 0.5),
                # NEW: Implicit feedback patterns
                ("is slow", 0.7),
                ("is confusing", 0.75),
                ("doesn't work", 0.8),
                ("not working", 0.8),
                ("could be better", 0.7),
                ("needs improvement", 0.75),
                ("really slow", 0.7),
                ("too slow", 0.75),
                ("very confusing", 0.75),
                ("i think", 0.5),
                ("in my opinion", 0.65),
            ]

            for indicator, weight in contextual_indicators:
                if indicator in message_lower:
                    contextual_score = max(contextual_score, weight)

        elif pattern_name == "weather":
            # Weather-specific contextual analysis
            weather_contexts = [
                ("what's the weather", 0.95),
                ("weather in", 0.9),
                ("temperature in", 0.8),
                ("forecast for", 0.85),
                ("how's the weather", 0.9),
            ]

            for context, weight in weather_contexts:
                if context in message_lower:
                    contextual_score = max(contextual_score, weight)

        elif pattern_name == "capabilities":
            # Capability inquiry contexts
            capability_contexts = [
                ("what can you", 0.95),
                ("what do you", 0.9),
                ("what tools", 0.95),
                ("help me", 0.7),
                ("how do you", 0.8),
            ]

            for context, weight in capability_contexts:
                if context in message_lower:
                    contextual_score = max(contextual_score, weight)

        # Check for question patterns that might modify confidence
        question_indicators = ["what", "how", "can", "could", "would", "?"]
        is_question = any(
            indicator in message_lower for indicator in question_indicators
        )

        if is_question and pattern_name in ["capabilities", "knowledge"]:
            contextual_score *= 1.2  # Boost question patterns for info requests

        return min(contextual_score, 1.0)

    def _calculate_semantic_confidence(
        self, message: str, pattern_info: Dict, pattern_name: str
    ) -> float:
        """Analyze semantic structure and intent"""
        semantic_score = 0.0

        # Simple semantic analysis based on message structure
        words = message.lower().split()

        if pattern_name == "feedback":
            # Check for feedback-like sentence structures
            feedback_verbs = [
                "is",
                "was",
                "works",
                "doesn't",
                "need",
                "want",
                "think",
                "feel",
            ]
            feedback_adjectives = [
                "good",
                "bad",
                "great",
                "terrible",
                "slow",
                "fast",
                "confusing",
                "helpful",
            ]

            has_feedback_verb = any(verb in words for verb in feedback_verbs)
            has_feedback_adjective = any(adj in words for adj in feedback_adjectives)

            if has_feedback_verb and has_feedback_adjective:
                semantic_score = 0.6
            elif has_feedback_verb or has_feedback_adjective:
                semantic_score = 0.3

        elif pattern_name == "weather":
            # Check for location + weather structure
            has_location_words = any(
                word in words for word in ["in", "at", "for", "around"]
            )
            has_time_words = any(
                word in words for word in ["today", "tomorrow", "now", "currently"]
            )

            if has_location_words or has_time_words:
                semantic_score = 0.4

        # Message length and complexity analysis
        if len(words) > 3:  # Longer messages might be more intentional
            semantic_score *= 1.1

        return min(semantic_score, 0.8)

    def _calculate_user_history_confidence(
        self, message: str, pattern_info: Dict, pattern_name: str
    ) -> float:
        """Calculate confidence based on user's historical patterns"""
        # This could be enhanced with actual user history tracking
        # For now, return a base score that could be improved with user data

        # If we have user context, we could check their typical language patterns
        user_id = getattr(self, "current_user_id", None)

        if user_id and hasattr(self, "user_language_patterns"):
            # This would be implemented with actual user pattern tracking
            return 0.0

        return 0.0  # No user history available yet

    def _get_adaptive_threshold(self, base_threshold: float) -> float:
        """Calculate adaptive confidence threshold based on pattern performance"""
        # Start with a more reasonable base threshold for multi-level matching
        adaptive_threshold = (
            base_threshold * 0.75
        )  # Reduce base threshold significantly

        # Analyze recent pattern matching success rates
        total_patterns = len(self.dynamic_patterns)
        if total_patterns > 0:
            # Calculate average pattern usage
            total_usage = sum(p["usage_count"] for p in self.dynamic_patterns.values())
            avg_usage = total_usage / total_patterns

            # More aggressive threshold adaptation
            if avg_usage > 5:  # Lower threshold for regular use (was 10)
                adaptive_threshold *= 0.85  # Reduce threshold by 15% (was 10%)
            elif avg_usage < 1:  # If patterns are rarely matching (was 2)
                adaptive_threshold *= 1.05  # Slight increase (was 10%)

        # More permissive bounds for multi-level matching
        return max(0.4, min(0.85, adaptive_threshold))

    def _update_pattern_success(
        self, pattern_name: str, message: str, confidence: float
    ):
        """Update pattern based on successful match"""
        if pattern_name in self.dynamic_patterns:
            # Increment usage count
            self.dynamic_patterns[pattern_name]["usage_count"] += 1

            # Extract potential new keywords from successful matches
            self._learn_from_successful_match(pattern_name, message, confidence)

            # Update pattern confidence based on usage success
            current_confidence = self.dynamic_patterns[pattern_name]["confidence"]
            # Gradually improve confidence for frequently used patterns
            usage_count = self.dynamic_patterns[pattern_name]["usage_count"]
            if usage_count > 5:  # After 5 successful uses
                confidence_boost = min(0.05, usage_count * 0.001)  # Small boost
                self.dynamic_patterns[pattern_name]["confidence"] = min(
                    1.0, current_confidence + confidence_boost
                )

    def _learn_from_successful_match(
        self, pattern_name: str, message: str, confidence: float
    ):
        """Learn new keywords from successful pattern matches"""
        if confidence > 0.8:  # Only learn from high-confidence matches
            words = message.lower().split()

            # Look for new relevant words that aren't already in the pattern
            existing_keywords = set(self.dynamic_patterns[pattern_name]["keywords"])

            # Find words that appear with existing keywords
            for word in words:
                if (
                    len(word) > 3  # Meaningful length
                    and word not in existing_keywords  # Not already included
                    and word.isalpha()  # Only alphabetic words
                    and word
                    not in [
                        "this",
                        "that",
                        "with",
                        "from",
                        "they",
                        "them",
                        "have",
                        "will",
                        "been",
                        "were",
                        "said",
                        "each",
                        "which",
                        "their",
                        "would",
                        "there",
                        "could",
                        "other",
                    ]
                ):  # Common words to ignore
                    # Add as potential keyword if pattern is feedback-related
                    if (
                        pattern_name == "feedback"
                        and len(self.dynamic_patterns[pattern_name]["keywords"]) < 25
                    ):
                        # Only add if it seems feedback-related
                        feedback_related_words = [
                            "report",
                            "suggest",
                            "recommend",
                            "complain",
                            "praise",
                            "comment",
                            "opinion",
                            "thought",
                            "idea",
                        ]
                        if word in feedback_related_words:
                            self.dynamic_patterns[pattern_name]["keywords"].append(word)
                            self.logger.info(
                                f"📚 Learned new keyword '{word}' for {pattern_name} pattern"
                            )

    def _analyze_failed_match(self, message: str, message_lower: str):
        """Analyze messages that didn't match any pattern to identify gaps"""
        # Look for common feedback patterns that might not be covered
        potential_feedback_indicators = [
            "review",
            "rating",
            "comment",
            "opinion",
            "thoughts",
            "evaluate",
            "assessment",
            "critique",
            "praise",
            "complain",
            "satisfied",
            "disappointed",
        ]

        # If message contains feedback-like words but didn't match feedback pattern
        has_feedback_words = any(
            word in message_lower for word in potential_feedback_indicators
        )

        if has_feedback_words and "feedback" in self.dynamic_patterns:
            # This might indicate our feedback pattern needs expansion
            self.logger.debug(
                f"🔍 Potential missed feedback pattern: '{message[:50]}...'"
            )

            # Could automatically suggest pattern improvements here

    async def _discover_available_tools(self) -> Dict[str, Dict]:
        """Dynamically discover all available tools from connected MCP servers"""
        self.logger.info("🔍 Discovering available tools from MCP servers...")

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
                    "agent_description": spec.instruction[:200] + "..."
                    if len(spec.instruction) > 200
                    else spec.instruction,
                    "servers": spec.server_names,
                    "server_tools": agent_tools,
                    "capabilities": spec.capabilities,
                }
        else:
            # Fallback to old method if no MCPApp available
            self.logger.warning("⚠️  No MCPApp context - using fallback tool discovery")
            for agent_type, spec in self.agent_registry.items():
                discovered_tools[agent_type] = {
                    "agent_description": spec.instruction[:200] + "..."
                    if len(spec.instruction) > 200
                    else spec.instruction,
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
            "airtable_manager",  # Airtable operations
            "automation_specialist",  # Workflow automation
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

            except Exception as e:
                self.logger.warning(f"Could not initialize {agent_type}: {e}")

        self.agent_pool_initialized = True
        self.logger.info(f"🔄 Connection pool ready with {len(self.agent_pool)} agents")

    async def _health_check_agents(self):
        """Perform health checks on pooled agents and recover if needed"""
        now = datetime.now()

        for agent_type, agent in list(self.agent_pool.items()):
            last_check = self.agent_last_health_check.get(agent_type, datetime.min)

            if (now - last_check).total_seconds() < self.config_dict[
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
        return self.config_dict["cache_ttl_seconds"]

    async def _cleanup_memory(self):
        """Periodic memory cleanup and optimization"""
        now = datetime.now()

        if (now - self.last_cleanup_time).total_seconds() < self.config_dict[
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

    async def _analyze_user_intent_dynamic(
        self, message: str, context: Dict = None
    ) -> Dict:
        """Dynamic intent analysis using actual tool discovery - replaces hard-coded patterns"""
        try:
            # 🔧 FIRST: Check for MCP server addition requests (highest priority)
            if self._is_mcp_server_addition_request(message):
                self.logger.info(
                    f"🔧 PRIORITY: MCP server addition detected for: {message[:50]}..."
                )

                return {
                    "required_agents": ["mcp_server_manager"],
                    "complexity": "simple",
                    "estimated_tasks": 1,
                    "execution_strategy": "single_agent",
                    "priority": "high",
                    "task_description": "MCP server addition workflow",
                    "reasoning": "Detected MCP server addition request",
                    "intent_name": "dynamic_mcp_server_manager",
                    "confidence": "high",
                    "requires_tools": [],
                }

            # 💬 SECOND: Check for feedback requests (high priority)
            feedback_info = self._parse_feedback_from_message(message)
            if feedback_info["has_feedback"] or any(
                pattern in message.lower()
                for pattern in [
                    "give feedback",
                    "provide feedback",
                    "share feedback",
                    "my feedback",
                    "here is feedback",
                    "here's feedback",
                    "feedback on",
                    "feedback about",
                    "i want to give feedback",
                ]
            ):
                self.logger.info(
                    f"💬 PRIORITY: Feedback detected for: {message[:50]}..."
                )

                return {
                    "required_agents": ["feedback_collector"],
                    "complexity": "simple",
                    "estimated_tasks": 1,
                    "execution_strategy": "single_agent",
                    "priority": "high",
                    "task_description": "User feedback collection and storage",
                    "reasoning": "Detected feedback collection request",
                    "intent_name": "dynamic_feedback_collector",
                    "confidence": "high",
                    "requires_tools": [],
                }

            # 🔄 THIRD: Check for workflow triggers (database-driven workflows)
            workflow_match = await self._check_workflow_triggers(message, context)
            if workflow_match:
                workflow = workflow_match["workflow"]
                self.logger.info(
                    f"🔄 PRIORITY: Workflow triggered: {workflow['name']} for: {message[:50]}..."
                )

                return {
                    "required_agents": [],  # Workflows handle their own execution
                    "complexity": "workflow",
                    "estimated_tasks": len(
                        workflow.get("definition", {}).get("steps", [1])
                    ),
                    "execution_strategy": "workflow",
                    "priority": "high",
                    "task_description": f"Execute {workflow['type']} workflow: {workflow['name']}",
                    "reasoning": f"Workflow trigger detected for {workflow['name']} (confidence: {workflow_match['confidence']:.2f})",
                    "intent_name": f"workflow_{workflow['name']}",
                    "confidence": "high"
                    if workflow_match["confidence"] > 0.85
                    else "medium",
                    "workflow": workflow,
                    "workflow_confidence": workflow_match["confidence"],
                }

            # 🎯 FOURTH: Check if we need dynamic MCP server discovery for organization-qualified servers (NEW PRIORITY)
            # This needs to happen BEFORE pattern matching to avoid generic supabase/airtable patterns intercepting qualified requests
            message_keywords = self._extract_discovery_keywords_from_message(message)

            # Check for organization-qualified patterns specifically (e.g., "ARC supabase", "ACME airtable")
            has_org_qualifier = any("_" in keyword for keyword in message_keywords)
            org_service_patterns = [
                r"\b(?:our\s+)?([A-Z]{2,10})\s+(supabase|database|db|airtable|n8n|api|webhook|integration|server|service)\b"
            ]
            has_explicit_org_pattern = any(
                re.search(pattern, message, re.IGNORECASE)
                for pattern in org_service_patterns
            )

            if has_org_qualifier or has_explicit_org_pattern:
                needs_dynamic_discovery = await self._check_if_needs_dynamic_discovery(
                    message, message_keywords
                )

                if needs_dynamic_discovery:
                    self.logger.info(
                        f"🎯 PRIORITY: Organization-qualified service detected: {message[:50]}... keywords: {message_keywords}"
                    )

                    # Select appropriate agent type for the discovered tools
                    agent_type = (
                        "data_researcher"  # This agent can work with any MCP tools
                    )

                    return {
                        "required_agents": [agent_type],
                        "complexity": "dynamic",
                        "estimated_tasks": 1,
                        "execution_strategy": "dynamic_discovery",
                        "priority": "high",
                        "task_description": f"Dynamic MCP discovery for organization-qualified services",
                        "reasoning": f"Detected organization-qualified service requiring database server lookup",
                        "intent_name": f"dynamic_discovery_{agent_type}",
                        "confidence": "high",
                        "requires_tools": [],
                        "discovery_keywords": message_keywords,
                        "qualified_services": True,
                    }

            # 🚀 FIFTH: Dynamic pattern matching (moved after feedback, workflows, and org-qualified check)
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

            # 🔍 SIXTH: Check if we need dynamic MCP server discovery (for non-org-qualified requests)
            if not has_org_qualifier and not has_explicit_org_pattern:
                needs_dynamic_discovery = await self._check_if_needs_dynamic_discovery(
                    message, message_keywords
                )

                if needs_dynamic_discovery:
                    self.logger.info(
                        f"🔍 Dynamic MCP discovery detected for: {message[:50]}... keywords: {message_keywords}"
                    )

                    # Select appropriate agent type for the discovered tools
                    agent_type = (
                        "data_researcher"  # This agent can work with any MCP tools
                    )

                    return {
                        "required_agents": [agent_type],
                        "complexity": "dynamic",
                        "estimated_tasks": 1,
                        "execution_strategy": "dynamic_discovery",
                        "priority": "high",
                        "task_description": f"Dynamic MCP discovery for qualified services",
                        "reasoning": f"Detected service patterns requiring database server discovery",
                        "intent_name": f"dynamic_discovery_{agent_type}",
                        "confidence": "high",
                        "requires_tools": [],
                        "discovery_keywords": message_keywords,
                        "qualified_services": False,
                    }

            # 🤖 SEVENTH: Fall back to LLM routing for complex/ambiguous requests
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
            - For airtable/workflow/automation, use "automation_specialist" or "airtable_manager"
            - For adding/registering/configuring MCP servers, use "mcp_server_manager"
            - For feedback/suggestions/complaints, use "feedback_collector"
            - Match the user's request to the actual tools available
            """

            # Use OpenAI to analyze the request dynamically
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
                    try:
                        parsed_result = json.loads(routing_result)
                    except json.JSONDecodeError as json_error:
                        self.logger.warning(f"JSON parsing failed: {json_error}")
                        self.logger.debug(
                            f"Raw routing result: {routing_result[:500]}..."
                        )

                        # Extract JSON from the response if it's wrapped in text
                        json_match = re.search(
                            r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}",
                            routing_result,
                            re.DOTALL,
                        )
                        if json_match:
                            try:
                                parsed_result = json.loads(json_match.group())
                                self.logger.info(
                                    "✅ Successfully extracted JSON from wrapped response"
                                )
                            except json.JSONDecodeError as inner_error:
                                self.logger.error(
                                    f"Failed to parse extracted JSON: {inner_error}"
                                )
                                self.logger.debug(
                                    f"Extracted JSON: {json_match.group()}"
                                )
                                # Fallback to pattern matching instead of failing completely
                                self.logger.info(
                                    "🔄 Falling back to pattern matching due to JSON parsing failure"
                                )
                                pattern_match = self._dynamic_pattern_match(message)
                                if pattern_match:
                                    return {
                                        "required_agents": [pattern_match["agent"]],
                                        "complexity": "simple",
                                        "execution_strategy": "single_agent",
                                        "priority": "high",
                                        "task_description": f"Pattern match fallback to {pattern_match['agent']}",
                                        "reasoning": f"JSON parsing failed, used pattern matching with {pattern_match['confidence']:.2f} confidence",
                                        "intent_name": f"pattern_fallback_{pattern_match['agent']}",
                                        "confidence": "medium",
                                        "requires_tools": [],
                                    }
                                else:
                                    # Ultimate fallback - use intent analysis fallback
                                    return await self._analyze_user_intent_fallback(
                                        message, context
                                    )
                        else:
                            self.logger.error(
                                "Could not extract JSON from routing response"
                            )
                            self.logger.debug(f"Full response: {routing_result}")
                            # Try pattern matching as fallback
                            self.logger.info(
                                "🔄 Falling back to pattern matching due to JSON extraction failure"
                            )
                            pattern_match = self._dynamic_pattern_match(message)
                            if pattern_match:
                                return {
                                    "required_agents": [pattern_match["agent"]],
                                    "complexity": "simple",
                                    "execution_strategy": "single_agent",
                                    "priority": "high",
                                    "task_description": f"Pattern match fallback to {pattern_match['agent']}",
                                    "reasoning": f"JSON extraction failed, used pattern matching with {pattern_match['confidence']:.2f} confidence",
                                    "intent_name": f"pattern_fallback_{pattern_match['agent']}",
                                    "confidence": "medium",
                                    "requires_tools": [],
                                }
                            else:
                                # Ultimate fallback
                                return await self._analyze_user_intent_fallback(
                                    message, context
                                )

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
                # Clean up dynamic agent
                try:
                    await routing_agent.__aexit__(None, None, None)
                    self.logger.debug("🧹 Cleaned up dynamic routing agent")
                except Exception as cleanup_error:
                    self.logger.warning(
                        f"Dynamic agent cleanup warning: {cleanup_error}"
                    )

        except Exception as e:
            self.logger.error(f"Dynamic routing error: {e}")
            # Fallback to simple logic
            return await self._analyze_user_intent_fallback(message, context)

    async def _analyze_user_intent_fallback(
        self, message: str, context: Dict = None
    ) -> Dict:
        """Fallback intent analysis if structured system fails"""
        message_lower = message.lower()

        # Quick pattern matching as fallback - include feedback detection
        if any(
            word in message_lower
            for word in [
                "feedback",
                "give feedback",
                "provide feedback",
                "suggestion",
                "bug report",
                "feature request",
                "improvement",
                "issue with",
                "add this feedback",
                "leave feedback",
            ]
        ):
            agent_type = "feedback_collector"
        elif any(
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
        elif any(
            word in message_lower
            for word in ["airtable", "air table", "base", "records"]
        ):
            agent_type = "airtable_manager"
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

    async def initialize_slack(self, bot_token: str, app_token: str):
        """Initialize Slack integration"""
        await self.slack_manager.initialize(
            bot_token, app_token, self._handle_slack_message
        )

    async def start_slack_connection(self):
        """Start the Slack connection using SlackClientManager"""
        await self.slack_manager.start_connection()

    def _should_ignore_message(self, event: Dict) -> bool:
        """Check if message should be ignored to prevent loops and duplicates"""
        # Filter for relevant events only
        if event.get("type") not in ["app_mention", "message"]:
            return True

        # Ignore bot messages and messages with subtypes (like edits, etc.)
        if event.get("subtype") or event.get("bot_id"):
            self.logger.debug(
                f"Ignoring bot message or subtype: {event.get('subtype')}"
            )
            return True

        # Check for required fields
        user_id = event.get("user")
        channel_id = event.get("channel")
        message_text = event.get("text", "")
        message_ts = event.get("ts")

        if not user_id or not channel_id or not message_text or not message_ts:
            self.logger.debug("Missing required message fields")
            return True

        # Create unique message identifier for deduplication
        message_id = f"{user_id}_{channel_id}_{message_ts}_{hash(message_text)}"

        # Check if we've already processed this exact message
        if message_id in self.processed_messages:
            self.logger.debug(f"Duplicate message detected, ignoring: {message_id}")
            return True

        # Add to processed messages (with cleanup)
        self.processed_messages.add(message_id)

        # Periodic cleanup of old processed messages (every 10 minutes)
        now = datetime.now()
        if (now - self.last_cleanup_time).total_seconds() > 600:  # 10 minutes
            # Keep only last 1000 message IDs to prevent memory growth
            if len(self.processed_messages) > 1000:
                # Convert to list, sort by timestamp, keep most recent 500
                message_list = list(self.processed_messages)
                self.processed_messages = set(message_list[-500:])
                self.logger.debug("Cleaned up old processed message IDs")
            self.last_cleanup_time = now

        return False

    async def _handle_slack_message(self, event: Dict):
        """Handle Slack messages - delegates to the full processing pipeline"""
        await self._process_slack_message(event)

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
            self.logger.debug(
                f"🔍 Checking human input: user_id={user_id}, thread_ts={thread_ts}, pending_users={list(self.pending_human_inputs.keys())}"
            )

            if user_id in self.pending_human_inputs and thread_ts:
                self.logger.info(
                    f"📝 Received human input response from user {user_id} in thread {thread_ts}"
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
            elif user_id in self.pending_human_inputs:
                self.logger.warning(
                    f"⚠️ User {user_id} has pending input but no thread_ts in response"
                )
            elif thread_ts:
                self.logger.debug(
                    f"🔍 Message in thread {thread_ts} but no pending input for user {user_id}"
                )

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
                    if (
                        hasattr(self.slack_manager, "slack_client")
                        and self.slack_manager.slack_client
                        and message_ts
                    ):
                        self.slack_manager.slack_client.reactions_add(
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
                    await self.slack_manager.send_response(
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
                # Add safety check for None values
                safe_intent_analysis = intent_analysis or {}
                safe_quality_metrics = quality_metrics or {}
                safe_result = (
                    result or "I encountered an issue processing your request."
                )

                await self._send_enhanced_slack_response(
                    channel_id,
                    safe_result,
                    safe_intent_analysis,
                    safe_quality_metrics,
                    thread_ts=message_ts,
                )

        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            self.logger.error(
                f"Error processing Slack message (took {execution_time:.2f}s): {e}"
            )
            if "channel_id" in locals() and "message_ts" in locals():
                error_message = f"Sorry, I encountered an error: {str(e)}"
                clean_error = self._clean_markdown_from_response(error_message)
                await self.slack_manager.send_response(
                    channel_id,
                    clean_error,
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

    def _clean_user_input(self, message_text: str) -> str:
        """Clean user input by removing mentions, formatting, etc."""
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

    def _clean_markdown_from_response(self, response: str) -> str:
        """Remove markdown formatting from responses to provide clean text"""
        if not response:
            return response

        # Remove bold formatting **text** -> text
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", response)

        # Replace bullet points • with dashes
        cleaned = cleaned.replace("•", "-")

        # Replace multiple consecutive dashes with single dashes
        cleaned = re.sub(r"^\s*-\s*-", "-", cleaned, flags=re.MULTILINE)

        return cleaned

    async def _send_enhanced_slack_response(
        self,
        channel_id: str,
        result: str,
        analysis: Dict,
        quality_metrics: Dict,
        thread_ts: str = None,
    ):
        """Send enhanced response to Slack with quality metrics and better formatting"""
        try:
            # Ensure we have valid dictionaries
            safe_analysis = analysis or {}
            safe_quality_metrics = quality_metrics or {}

            # Create status indicators based on quality metrics
            execution_time = safe_quality_metrics.get("execution_time", 0)
            confidence = safe_analysis.get("confidence", "unknown")
            intent_name = safe_analysis.get("intent_name", "unknown")

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
• Agents used: {safe_quality_metrics.get("agent_count", 1)}
• Routing: {"⚡ Fast Pattern Match" if intent_name.startswith("fast_") else "🤖 LLM Routing"}

*Optimized with pre-warmed agents & caching*
"""
            else:
                # Standard response formatting with dynamic info
                routing_info = self._get_routing_info(safe_analysis)
                pattern_info = self._get_pattern_info(safe_analysis)

                formatted_response = f"""{confidence_emoji} **Agent Response** ({timing_emoji} {execution_time:.1f}s)

{result}

*{routing_info} | Strategy: {safe_analysis.get("execution_strategy", "unknown")} | {pattern_info}*
"""

            # Clean markdown from response before sending
            clean_response = self._clean_markdown_from_response(formatted_response)
            await self.slack_manager.send_response(
                channel_id, clean_response, thread_ts
            )

        except Exception as e:
            self.logger.error(f"Error sending enhanced Slack response: {e}")
            # Fallback to basic response with safe values (also clean markdown)
            clean_result = self._clean_markdown_from_response(result)
            await self.slack_manager.send_response(channel_id, clean_result, thread_ts)

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

    # Add additional missing methods from the main.py
    async def _store_conversation_memory(
        self, user_id: str, message: str, channel_id: str
    ):
        """Store conversation in Supabase memory with direct client fallback"""
        if user_id not in self.conversation_memory:
            self.conversation_memory[user_id] = []

        conversation_entry = {
            "role": "user",
            "content": message,
            "timestamp": datetime.now().isoformat(),
            "channel_id": channel_id,
        }

        self.conversation_memory[user_id].append(conversation_entry)

        # Try direct Supabase client first for reliability
        try:
            if self.supabase_direct_client:
                result = await self.supabase_direct_client.log_conversation_memory(
                    user_id, message, channel_id
                )
                if result["success"]:
                    self.logger.info(
                        f"✅ Stored conversation memory for user {user_id} via direct client"
                    )
                    return
                else:
                    self.logger.warning(
                        f"⚠️ Direct memory logging failed: {result['error']}, falling back to MCP"
                    )

            # Store using database operations
            await self.db_ops.store_conversation_memory(user_id, message, channel_id)

        except Exception as e:
            self.logger.warning(f"Could not store to Supabase: {e}")

    async def _store_interaction_learning(
        self, user_id: str, message: str, result: str, analysis: Dict
    ):
        """Store interaction for system learning with direct client fallback"""
        try:
            # Try direct Supabase client first for reliability
            if self.supabase_direct_client:
                direct_result = await self.supabase_direct_client.log_interaction(
                    user_id, message, result, analysis
                )
                if direct_result["success"]:
                    self.logger.info(
                        f"✅ Stored interaction learning for user {user_id} via direct client"
                    )
                    return
                else:
                    self.logger.warning(
                        f"⚠️ Direct interaction logging failed: {direct_result['error']}, falling back to MCP"
                    )

            # Store using database operations
            await self.db_ops.store_interaction_learning(
                user_id, message, result, analysis
            )

        except Exception as e:
            self.logger.warning(f"Could not store learning data: {e}")

    async def _cleanup_memory(self):
        """Periodic memory cleanup and optimization"""
        now = datetime.now()

        if (now - self.last_cleanup_time).total_seconds() < self.config_dict[
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

        self.last_cleanup_time = now

    async def _health_check_agents(self):
        """Perform health checks on pooled agents and recover if needed"""
        now = datetime.now()

        for agent_type, agent in list(self.agent_pool.items()):
            last_check = self.agent_last_health_check.get(agent_type, datetime.min)

            if (now - last_check).total_seconds() < self.config_dict[
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
                        self.config_dict[config_key] = value
                        self.logger.info(f"📊 Config override: {config_key} = {value}")
                    except ValueError:
                        self.logger.warning(
                            f"Invalid config value for {env_var}: {os.environ[env_var]}"
                        )

        except Exception as e:
            self.logger.warning(f"Config loading error: {e}")

    async def slack_human_input_callback(
        self, request: HumanInputRequest
    ) -> HumanInputResponse:
        """Handle human input requests by sending them to Slack and waiting for response"""
        try:
            if (
                not hasattr(self.slack_manager, "slack_client")
                or not self.slack_manager.slack_client
                or not self.current_thread_ts
            ):
                self.logger.warning(
                    "No Slack client or thread context for human input, falling back to console"
                )
                # Import and call the console fallback
                from mcp_agent.human_input.handler import console_input_callback

                return await console_input_callback(request)

            # Extract context from current conversation
            channel_id = getattr(self, "current_channel_id", None)
            user_id = getattr(self, "current_user_id", None)

            if not channel_id or not user_id:
                self.logger.warning(
                    "No channel/user context for human input, falling back to console"
                )
                from mcp_agent.human_input.handler import console_input_callback

                return await console_input_callback(request)

            self.logger.info(
                f"🤖 Sending human input request to Slack for user {user_id}"
            )

            # Format the human input request for Slack
            formatted_message = f"""🤖 Agent needs more information:

{request.prompt}

💡 Context: {request.description or "Please provide the requested information."}

Reply in this thread to continue...
"""

            # Clean markdown from the message before sending
            clean_message = self._clean_markdown_from_response(formatted_message)

            # Send the human input request to Slack
            response = await self.slack_manager.send_response(
                channel_id,
                clean_message,
                self.current_thread_ts,
            )

            # Check if response was successful
            if response is None or not response.get("ok", False):
                self.logger.error(f"Failed to send human input request to Slack")
                from mcp_agent.human_input.handler import console_input_callback

                return await console_input_callback(request)

            # Create a Future to wait for the user's response
            response_future = asyncio.Future()
            self.pending_human_inputs[user_id] = response_future

            self.logger.info(
                f"⏳ Waiting for human input response from user {user_id} in thread {self.current_thread_ts}"
            )
            self.logger.debug(
                f"🔍 Pending inputs now: {list(self.pending_human_inputs.keys())}"
            )

            # Wait for the user's response (with timeout)
            try:
                user_response = await asyncio.wait_for(
                    response_future, timeout=300.0
                )  # 5 minute timeout
                self.logger.info(
                    f"✅ Received human input response: {user_response[:50]}..."
                )
                return HumanInputResponse(
                    request_id=request.request_id or "slack_input",
                    response=user_response,
                )

            except asyncio.TimeoutError:
                self.logger.warning("⏰ Human input request timed out")
                # Clean up the pending request
                if user_id in self.pending_human_inputs:
                    del self.pending_human_inputs[user_id]

                # Send timeout message to Slack
                timeout_message = (
                    "⏰ Request timed out - Please try your original request again."
                )
                clean_timeout = self._clean_markdown_from_response(timeout_message)
                await self.slack_manager.send_response(
                    channel_id,
                    clean_timeout,
                    self.current_thread_ts,
                )

                return HumanInputResponse(
                    request_id=request.request_id or "slack_timeout",
                    response="Request timed out. Please try again.",
                )

        except Exception as e:
            self.logger.error(f"Error in Slack human input callback: {e}")
            import traceback

            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            from mcp_agent.human_input.handler import console_input_callback

            return await console_input_callback(request)

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

    async def verify_database_data(self):
        """Verify that data was actually inserted into the database"""
        try:
            # Use the database operations module to verify data
            result = await self.db_ops.verify_data_insertion()
            self.logger.info(f"📊 Database verification result: {result}")
            return result
        except Exception as e:
            self.logger.error(f"❌ Database verification failed: {e}")
            return None

    async def test_mcp_server_addition(self):
        """Test MCP server addition functionality"""
        self.logger.info("🧪 Testing MCP server addition functionality...")

        test_cases = [
            {
                "message": "add mcp server called 'test_api' with URL https://api.test.com/mcp using sse transport",
                "description": "Partial info provided",
                "expected_parsed": {
                    "server_name": "test_api",
                    "url": "https://api.test.com/mcp",
                    "transport": "sse",
                },
            },
            {
                "message": "register new mcp server",
                "description": "No info provided",
                "expected_parsed": {},
            },
        ]

        results = []

        # Test addition detection
        self.logger.info("🔧 Testing MCP server addition detection...")
        addition_test_cases = [
            "add mcp server called 'test_api'",
            "I need to add mcp server called my_api",
            "register new mcp server",
        ]

        for test_message in addition_test_cases:
            is_addition = self._is_mcp_server_addition_request(test_message)
            if is_addition:
                results.append(
                    {
                        "test": f"Addition detection: '{test_message[:30]}...'",
                        "status": "✅ PASS",
                        "details": "Correctly identified as MCP server addition request",
                    }
                )
            else:
                results.append(
                    {
                        "test": f"Addition detection: '{test_message[:30]}...'",
                        "status": "❌ FAIL",
                        "details": "Failed to identify as MCP server addition request",
                    }
                )

        # Test pattern matching
        self.logger.info("🎯 Testing pattern matching...")
        for test_case in test_cases:
            pattern_match = self._dynamic_pattern_match(test_case["message"])

            if pattern_match and pattern_match.get("agent") == "mcp_server_manager":
                results.append(
                    {
                        "test": f"Pattern match: {test_case['description']}",
                        "status": "✅ PASS",
                        "details": f"Matched with confidence {pattern_match.get('confidence', 0):.2f}",
                    }
                )
            else:
                results.append(
                    {
                        "test": f"Pattern match: {test_case['description']}",
                        "status": "❌ FAIL",
                        "details": f"Expected mcp_server_manager, got {pattern_match.get('agent', 'none') if pattern_match else 'no match'}",
                    }
                )

        # Generate summary
        passed_tests = len([r for r in results if r["status"] == "✅ PASS"])
        total_tests = len(results)
        success_rate = (passed_tests / total_tests) * 100

        report = f"""🧪 **MCP Server Addition Test Report**

**Summary:** {passed_tests}/{total_tests} tests passed ({success_rate:.1f}% success rate)

**Test Results:**
"""

        for result in results:
            report += f"\n{result['status']} **{result['test']}**"
            report += f"\n   Details: {result['details']}\n"

        if success_rate >= 80:
            report += "\n🎉 **Overall Status: READY FOR USE**"
            report += "\nThe MCP server addition functionality is working correctly!"
        else:
            report += "\n⚠️ **Overall Status: NEEDS ATTENTION** "
            report += f"\nSome tests failed. Please review the {total_tests - passed_tests} failing test(s)."

        self.logger.info(f"🧪 Test completed: {passed_tests}/{total_tests} passed")
        return report

    async def test_mcp_server_addition(self):
        """Test MCP server addition workflow"""
        try:
            test_message = (
                "Add a new MCP server called test-server with URL http://localhost:3000"
            )
            result = await self.add_mcp_server_workflow(test_message, "test_user")
            print(f"MCP Server Addition Test Result: {result}")
            return True
        except Exception as e:
            print(f"MCP Server Addition Test Failed: {e}")
            return False

    async def test_crewai_integration(self):
        """Test CrewAI workflow integration"""
        try:
            if not CREWAI_AVAILABLE:
                print("❌ CrewAI not available - skipping test")
                return False

            # Test with a simple dynamic crew workflow
            test_workflow = {
                "id": "test_crew_001",
                "name": "Test CrewAI Workflow",
                "type": "crewai",
                "description": "Test workflow for CrewAI integration",
                "required_agents": ["data_researcher", "communication_specialist"],
                "is_active": True,
            }

            test_message = "Create a brief report about artificial intelligence trends"
            test_context = {"user_id": "test_user", "channel_id": "test_channel"}

            self.logger.info("🧪 Testing CrewAI integration...")

            # Test the CrewAI workflow execution
            result = await self._execute_crew_workflow(
                test_workflow, test_message, test_context
            )

            if result and not result.startswith("❌"):
                self.logger.info("✅ CrewAI integration test passed")
                print(f"CrewAI Test Result: {result[:200]}...")
                return True
            else:
                self.logger.error(f"❌ CrewAI integration test failed: {result}")
                print(f"CrewAI Test Failed: {result}")
                return False

        except Exception as e:
            self.logger.error(f"CrewAI integration test error: {e}")
            print(f"CrewAI Test Exception: {e}")
            return False

    # ===== MISSING CORE METHODS FROM ORIGINAL MAIN.PY =====

    async def _execute_orchestrated_workflow(
        self, intent_analysis: Dict, message: str, agents: List[Agent]
    ) -> str:
        """Execute orchestrated multi-agent workflow"""
        try:
            # Special handling for workflow execution (NEW)
            if intent_analysis.get("execution_strategy") == "workflow":
                self.logger.info("🔄 Executing database-driven workflow")
                workflow = intent_analysis.get("workflow")
                if workflow:
                    return await self._execute_workflow(
                        workflow, message, intent_analysis
                    )
                else:
                    return "❌ Workflow configuration missing from intent analysis"

            # Special handling for MCP server addition workflow
            if (
                len(agents) == 1
                and hasattr(agents[0], "name")
                and "mcp_server_manager" in agents[0].name
            ) or intent_analysis.get("intent_name", "").startswith(
                "dynamic_mcp_server_manager"
            ):
                self.logger.info("🔧 Executing MCP server addition workflow")
                user_id = getattr(self, "current_user_id", "unknown_user")
                return await self.add_mcp_server_workflow(message, user_id)

            # Special handling for feedback collection workflow
            if (
                len(agents) == 1
                and hasattr(agents[0], "name")
                and "feedback_collector" in agents[0].name
            ) or intent_analysis.get("intent_name", "").startswith(
                "dynamic_feedback_collector"
            ):
                self.logger.info("💬 Executing feedback collection workflow")
                user_id = getattr(self, "current_user_id", "unknown_user")
                return await self.feedback_collection_workflow(message, user_id)

            # Handle dynamic MCP discovery execution strategy
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

                # Special handling for capability_inspector to search for specific tools
                if "capability_inspector" in agent.name:
                    # Extract tool names from the message
                    tool_name = self._extract_tool_name_from_message(message)
                    if tool_name:
                        self.logger.info(f"🔍 Tool search requested for: {tool_name}")
                        return await self._perform_capability_introspection(tool_name)
                    else:
                        return await self._perform_capability_introspection()

                async with agent:
                    llm = await agent.attach_llm(OpenAIAugmentedLLM)
                    result = await llm.generate_str(message)
                    return result

            # Use orchestrator for complex multi-agent workflows
            orchestrator = Orchestrator(
                llm_factory=OpenAIAugmentedLLM,
                available_agents=agents,
                plan_type="full",
            )

            task_description = f"""
            Original request: {message}
            
            Task analysis: {intent_analysis.get("task_description", "")}
            Required capabilities: {", ".join([agent.name for agent in agents])}
            
            Execute this request using the available specialized agents and provide actionable results.
            """

            self.logger.info(
                f"🎯 Orchestrating {len(agents)} agents for complex workflow"
            )
            result = await orchestrator.generate_str(message=task_description)
            return result

        except Exception as e:
            self.logger.error(f"Orchestration error: {e}")
            return await self._execute_sequential_fallback(message, agents)

    # ===== WORKFLOW EXECUTION ENGINE =====

    async def _check_workflow_triggers(
        self, message: str, context: Dict
    ) -> Optional[Dict]:
        """Check if message triggers any workflows from database"""
        try:
            # Try pool manager first, fallback to db_ops
            workflows = None
            if self.pool_manager:
                workflows = await self.pool_manager.get_workflows_by_trigger(
                    message.lower().split()
                )
            else:
                # Fallback to direct database operations
                try:
                    workflows = await self.db_ops.get_workflows_by_trigger(
                        message.lower().split()
                    )
                except Exception as db_error:
                    self.logger.warning(f"Database workflow lookup failed: {db_error}")
                    return None

            if not workflows or not workflows.get("success"):
                return None

            workflow_list = workflows.get("data", [])
            if not workflow_list:
                return None

            message_words = set(message.lower().split())

            best_match = None
            best_confidence = 0.0

            for workflow in workflow_list:
                if not workflow.get("is_active", True):
                    continue

                trigger_patterns = workflow.get("trigger_patterns", [])
                if not trigger_patterns:
                    continue

                # Calculate confidence based on trigger pattern matching
                confidence = self._calculate_workflow_confidence(
                    message, workflow, message_words
                )

                if (
                    confidence > best_confidence and confidence >= 0.7
                ):  # Minimum threshold
                    best_confidence = confidence
                    best_match = {
                        "workflow": workflow,
                        "confidence": confidence,
                        "matched_patterns": [
                            p
                            for p in trigger_patterns
                            if any(word in message.lower() for word in p.split())
                        ],
                    }

            if best_match:
                self.logger.info(
                    f"🔄 Workflow match: {best_match['workflow']['name']} (confidence: {best_confidence:.2f})"
                )
                # Update workflow usage statistics
                await self._update_workflow_usage(
                    best_match["workflow"]["id"], success=None
                )  # Pre-execution tracking

            return best_match

        except Exception as e:
            self.logger.error(f"Workflow trigger checking error: {e}")
            return None

    def _calculate_workflow_confidence(
        self, message: str, workflow: Dict, message_words: set
    ) -> float:
        """Calculate confidence score for workflow trigger matching"""
        trigger_patterns = workflow.get("trigger_patterns", [])
        if not trigger_patterns:
            return 0.0

        total_score = 0.0
        pattern_count = 0

        for pattern in trigger_patterns:
            if not pattern:
                continue

            pattern_words = set(pattern.lower().split())

            # Exact pattern match gets highest score
            if pattern.lower() in message.lower():
                total_score += 1.0
                pattern_count += 1
                continue

            # Partial word matching
            matching_words = message_words.intersection(pattern_words)
            if matching_words:
                # Score based on percentage of pattern words matched
                word_match_score = len(matching_words) / len(pattern_words)
                total_score += word_match_score * 0.8  # Slightly lower than exact match
                pattern_count += 1

        if pattern_count == 0:
            return 0.0

        # Average confidence across all patterns, with bonus for multiple pattern matches
        base_confidence = total_score / pattern_count

        # Boost confidence if multiple patterns match
        if pattern_count > 1:
            base_confidence = min(1.0, base_confidence * 1.1)

        return base_confidence

    async def _execute_workflow(
        self, workflow: Dict, user_message: str, context: Dict
    ) -> str:
        """Execute workflow based on type"""
        workflow_type = workflow.get("type", "unknown")
        workflow_name = workflow.get("name", "unnamed_workflow")

        try:
            self.logger.info(f"🔄 Executing {workflow_type} workflow: {workflow_name}")

            if workflow_type == "n8n":
                result = await self._execute_n8n_workflow(
                    workflow, user_message, context
                )
            elif workflow_type == "internal":
                result = await self._execute_internal_workflow(
                    workflow, user_message, context
                )
            elif workflow_type == "sequential":
                result = await self._execute_sequential_workflow(
                    workflow, user_message, context
                )
            elif workflow_type == "crewai":
                # Execute CrewAI multi-agent workflows
                result = await self._execute_crew_workflow(
                    workflow, user_message, context
                )
            else:
                result = f"❌ Unknown workflow type: {workflow_type}"
                await self._update_workflow_usage(workflow.get("id"), success=False)
                return result

            # Update success metrics
            await self._update_workflow_usage(workflow.get("id"), success=True)
            return result

        except Exception as e:
            self.logger.error(f"Workflow execution error: {e}")
            await self._update_workflow_usage(workflow.get("id"), success=False)
            return f"❌ Workflow execution failed: {str(e)}"

    async def _execute_n8n_workflow(
        self, workflow: Dict, user_message: str, context: Dict
    ) -> str:
        """Execute n8n workflow via webhook"""
        try:
            webhook_url = workflow.get("webhook_url")
            if not webhook_url:
                return "❌ n8n workflow missing webhook URL"

            import aiohttp
            from datetime import datetime

            payload = {
                "user_message": user_message,
                "user_context": context or {},
                "timestamp": datetime.utcnow().isoformat(),
                "workflow_name": workflow.get("name", "unknown"),
                "metadata": {
                    "triggered_by": "slack_meta_agent",
                    "workflow_id": workflow.get("id"),
                    "execution_time": datetime.utcnow().isoformat(),
                },
            }

            self.logger.info(f"🌐 Calling n8n webhook: {webhook_url}")

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),  # 60 second timeout
                ) as response:
                    if response.status == 200:
                        result_data = await response.json()
                        output = result_data.get(
                            "output",
                            result_data.get(
                                "result", "n8n workflow completed successfully"
                            ),
                        )

                        if isinstance(output, dict):
                            # Format dict output nicely
                            output = "\n".join(
                                [f"**{k}:** {v}" for k, v in output.items()]
                            )

                        return f"✅ **n8n Workflow Complete: {workflow.get('name', 'Unknown')}**\n\n{output}"

                    elif response.status == 202:
                        return f"🔄 **n8n Workflow Started: {workflow.get('name', 'Unknown')}**\n\nWorkflow is running asynchronously. You'll be notified when it completes."

                    else:
                        error_text = await response.text()
                        return f"❌ **n8n Workflow Failed: {workflow.get('name', 'Unknown')}**\n\nHTTP {response.status}: {error_text}"

        except aiohttp.ClientTimeout:
            return f"⏱️ **n8n Workflow Timeout: {workflow.get('name', 'Unknown')}**\n\nThe workflow is taking longer than expected. It may still be running in the background."
        except Exception as e:
            return f"❌ **n8n Workflow Error: {workflow.get('name', 'Unknown')}**\n\n{str(e)}"

    async def _execute_internal_workflow(
        self, workflow: Dict, user_message: str, context: Dict
    ) -> str:
        """Execute internal workflow with sequential agent steps"""
        try:
            definition = workflow.get("definition", {})
            steps = definition.get("steps", [])

            if not steps:
                return f"❌ Internal workflow '{workflow.get('name', 'Unknown')}' has no steps defined"

            results = []
            current_context = context.copy() if context else {}
            current_context["original_message"] = user_message

            self.logger.info(f"🔄 Executing {len(steps)} workflow steps")

            for i, step in enumerate(steps, 1):
                step_type = step.get("type", "unknown")
                agent_name = step.get("agent", "data_researcher")
                params = step.get("params", {})

                self.logger.info(
                    f"Step {i}/{len(steps)}: {step_type} with {agent_name}"
                )

                try:
                    # Get agent for this step
                    if agent_name in self.agent_registry:
                        agent_spec = self.agent_registry[agent_name]

                        # Create agent for this step
                        from mcp_agent.agents import Agent

                        step_agent = Agent(
                            name=f"workflow_step_{i}_{agent_name}",
                            instruction=agent_spec.instruction,
                            server_names=agent_spec.server_names,
                            context=self.mcp_app.context if self.mcp_app else None,
                        )

                        async with step_agent:
                            llm = await step_agent.attach_llm(OpenAIAugmentedLLM)

                            # Create step-specific prompt
                            step_prompt = f"""
                            Workflow Step {i}: {step_type}
                            
                            Original User Request: {user_message}
                            Step Parameters: {params}
                            Previous Results: {results[-1] if results else "None"}
                            
                            Execute this workflow step and provide the specific output needed for the next step.
                            """

                            step_result = await llm.generate_str(step_prompt)
                            results.append(
                                {
                                    "step": i,
                                    "type": step_type,
                                    "agent": agent_name,
                                    "result": step_result,
                                }
                            )

                    else:
                        results.append(
                            {
                                "step": i,
                                "type": step_type,
                                "agent": agent_name,
                                "result": f"❌ Agent '{agent_name}' not found",
                            }
                        )

                except Exception as step_error:
                    self.logger.error(f"Workflow step {i} error: {step_error}")
                    results.append(
                        {
                            "step": i,
                            "type": step_type,
                            "agent": agent_name,
                            "result": f"❌ Step failed: {str(step_error)}",
                        }
                    )

            # Format final result
            workflow_result = f"✅ **Internal Workflow Complete: {workflow.get('name', 'Unknown')}**\n\n"

            for result in results:
                workflow_result += f"**Step {result['step']} ({result['type']}):** {result['result']}\n\n"

            return workflow_result

        except Exception as e:
            return f"❌ **Internal Workflow Error: {workflow.get('name', 'Unknown')}**\n\n{str(e)}"

    async def _execute_sequential_workflow(
        self, workflow: Dict, user_message: str, context: Dict
    ) -> str:
        """Execute sequential workflow (simplified internal workflow)"""
        # Sequential workflows are simpler versions of internal workflows
        return await self._execute_internal_workflow(workflow, user_message, context)

    async def _execute_crew_workflow(
        self, workflow: Dict, user_message: str, context: Dict
    ) -> str:
        """Execute CrewAI multi-agent workflow"""
        if not CREWAI_AVAILABLE:
            return f"❌ **CrewAI Not Available**: CrewAI is required for this workflow but not installed. Please install: pip install crewai crewai-tools"

        try:
            workflow_name = workflow.get("name", "Unknown CrewAI Workflow")
            self.logger.info(f"🚀 Executing CrewAI Workflow: {workflow_name}")

            # Check if this is a crew_configs workflow or legacy workflows table
            crew_config = await self._get_crew_config(workflow)
            if crew_config:
                return await self._execute_configured_crew(
                    crew_config, user_message, context
                )
            else:
                return await self._execute_dynamic_crew(workflow, user_message, context)

        except Exception as e:
            self.logger.error(f"CrewAI workflow execution failed: {e}")
            return f"❌ **CrewAI Workflow Error: {workflow.get('name', 'Unknown')}**\n\n{str(e)}"

    async def _get_crew_config(self, workflow: Dict) -> Optional[Dict]:
        """Get crew configuration from crew_configs table if available"""
        try:
            if not self.pool_manager:
                return None

            # First check if workflow has a crew_config_id
            crew_config_id = workflow.get("crew_config_id")
            if crew_config_id:
                result = await self.pool_manager.get_crew_config_by_id(crew_config_id)
                if result.get("success") and result.get("data"):
                    return result["data"][0]

            # Otherwise try to find by workflow name
            workflow_name = workflow.get("name")
            if workflow_name:
                result = await self.pool_manager.get_crew_configs_by_name(workflow_name)
                if result.get("success") and result.get("data"):
                    return result["data"][0]

            return None

        except Exception as e:
            self.logger.warning(f"Could not get crew config: {e}")
            return None

    async def _execute_configured_crew(
        self, crew_config: Dict, user_message: str, context: Dict
    ) -> str:
        """Execute a crew from the crew_configs table"""
        try:
            # Parse the stored crew configuration
            crew_data = crew_config.get("crew_data", {})
            agents_data = crew_data.get("agents", [])
            tasks_data = crew_data.get("tasks", [])

            if not agents_data or not tasks_data:
                return f"❌ **Invalid Crew Configuration**: Missing agents or tasks in crew_configs"

            # Create CrewAI agents
            crew_agents = []
            for agent_data in agents_data:
                crew_agent = await self._create_crew_agent(agent_data, context)
                if crew_agent:
                    crew_agents.append(crew_agent)

            if not crew_agents:
                return f"❌ **No Valid Agents**: Could not create any CrewAI agents from configuration"

            # Create CrewAI tasks
            crew_tasks = []
            for task_data in tasks_data:
                task = await self._create_crew_task(
                    task_data, crew_agents, user_message
                )
                if task:
                    crew_tasks.append(task)

            if not crew_tasks:
                return f"❌ **No Valid Tasks**: Could not create any CrewAI tasks from configuration"

            # Create and execute the crew
            crew = Crew(
                agents=crew_agents,
                tasks=crew_tasks,
                process=Process.sequential,  # Default to sequential
                verbose=True,
            )

            # Execute the crew
            result = crew.kickoff()

            # Format the result
            return f"✅ **CrewAI Workflow Complete: {crew_config.get('name', 'Unknown')}**\n\n{result}"

        except Exception as e:
            self.logger.error(f"Configured crew execution failed: {e}")
            return f"❌ **Configured Crew Error**: {str(e)}"

    async def _execute_dynamic_crew(
        self, workflow: Dict, user_message: str, context: Dict
    ) -> str:
        """Create and execute a dynamic crew based on workflow configuration"""
        try:
            # Create a basic crew from workflow information
            agents_needed = workflow.get(
                "required_agents", ["data_researcher", "communication_specialist"]
            )

            # Create CrewAI agents for each required agent type
            crew_agents = []
            for agent_type in agents_needed:
                agent_spec = self.agent_registry.get(agent_type)
                if agent_spec:
                    crew_agent = await self._create_crew_agent_from_spec(
                        agent_spec, context
                    )
                    if crew_agent:
                        crew_agents.append(crew_agent)

            if not crew_agents:
                return f"❌ **No Valid Agents**: Could not create CrewAI agents for types: {agents_needed}"

            # Create a basic task for the crew
            main_task = Task(
                description=f"""
                Collaborate to fulfill this user request: {user_message}
                
                Workflow context: {workflow.get("description", "No description available")}
                
                Each agent should contribute their specialized expertise to provide a comprehensive response.
                """,
                agent=crew_agents[0],  # Assign to first agent as primary
                expected_output="A comprehensive response that fulfills the user's request using the combined expertise of all agents.",
            )

            # Create and execute the crew
            crew = Crew(
                agents=crew_agents,
                tasks=[main_task],
                process=Process.sequential,
                verbose=True,
            )

            # Execute the crew
            result = crew.kickoff()

            return f"✅ **Dynamic CrewAI Workflow Complete: {workflow.get('name', 'Unknown')}**\n\n{result}"

        except Exception as e:
            self.logger.error(f"Dynamic crew execution failed: {e}")
            return f"❌ **Dynamic Crew Error**: {str(e)}"

    async def _create_crew_agent(
        self, agent_data: Dict, context: Dict
    ) -> Optional[CrewAgent]:
        """Create a CrewAI agent from configuration data"""
        try:
            if not CREWAI_AVAILABLE:
                return None

            # Extract agent configuration
            role = agent_data.get("role", "Assistant")
            goal = agent_data.get("goal", "Help the user achieve their objectives")
            backstory = agent_data.get("backstory", "I am a helpful AI assistant")
            tools = agent_data.get("tools", [])

            # Create tools list (can be expanded with MCP tools)
            agent_tools = []
            for tool_name in tools:
                tool = await self._create_crew_tool(tool_name, context)
                if tool:
                    agent_tools.append(tool)

            # Create the CrewAI agent
            crew_agent = CrewAgent(
                role=role,
                goal=goal,
                backstory=backstory,
                tools=agent_tools,
                verbose=True,
                allow_delegation=False,
            )

            return crew_agent

        except Exception as e:
            self.logger.error(f"Failed to create crew agent: {e}")
            return None

    async def _create_crew_agent_from_spec(
        self, agent_spec: AgentSpec, context: Dict
    ) -> Optional[CrewAgent]:
        """Create a CrewAI agent from an AgentSpec"""
        try:
            if not CREWAI_AVAILABLE:
                return None

            # Convert AgentSpec to CrewAI agent
            role = agent_spec.name.replace("_", " ").title()
            goal = f"Utilize {agent_spec.name} capabilities to help users with {', '.join(agent_spec.capabilities)}"
            backstory = agent_spec.instruction

            # Create basic tools (can be enhanced with MCP integration)
            tools = []

            # Create the CrewAI agent
            crew_agent = CrewAgent(
                role=role,
                goal=goal,
                backstory=backstory,
                tools=tools,
                verbose=True,
                allow_delegation=False,
            )

            return crew_agent

        except Exception as e:
            self.logger.error(f"Failed to create crew agent from spec: {e}")
            return None

    async def _create_crew_task(
        self, task_data: Dict, agents: List[CrewAgent], user_message: str
    ) -> Optional[Task]:
        """Create a CrewAI task from configuration data"""
        try:
            if not CREWAI_AVAILABLE or not agents:
                return None

            description = task_data.get("description", "").format(
                user_message=user_message
            )
            expected_output = task_data.get(
                "expected_output", "Complete the assigned task successfully"
            )
            agent_index = task_data.get("agent_index", 0)

            # Ensure agent index is valid
            if agent_index >= len(agents):
                agent_index = 0

            task = Task(
                description=description,
                agent=agents[agent_index],
                expected_output=expected_output,
            )

            return task

        except Exception as e:
            self.logger.error(f"Failed to create crew task: {e}")
            return None

    async def _create_crew_tool(
        self, tool_name: str, context: Dict
    ) -> Optional[BaseTool]:
        """Create a CrewAI tool by name"""
        try:
            if not CREWAI_AVAILABLE:
                return None

            # Basic tool mapping (can be expanded)
            if tool_name == "web_search" and SerperDevTool:
                return SerperDevTool()
            elif tool_name == "website_search" and WebsiteSearchTool:
                return WebsiteSearchTool()

            # Could add MCP tool integration here
            return None

        except Exception as e:
            self.logger.error(f"Failed to create crew tool {tool_name}: {e}")
            return None

    async def _update_workflow_usage(
        self, workflow_id: str, success: Optional[bool] = None
    ):
        """Update workflow usage statistics"""
        try:
            if not self.pool_manager or not workflow_id:
                return

            await self.pool_manager.update_workflow_usage(workflow_id, success)

        except Exception as e:
            self.logger.error(f"Failed to update workflow usage: {e}")

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

    async def _dynamic_mcp_server_discovery_optimized(
        self, query_keywords: List[str]
    ) -> List[Dict]:
        """Dynamically discover MCP servers from database with optimized caching"""
        if not self.pool_manager:
            return await self._dynamic_mcp_server_discovery(query_keywords)

        try:
            self.logger.info(
                f"🔍 Optimized MCP discovery for keywords: {query_keywords}"
            )

            result = await self.pool_manager.find_servers_by_keywords(query_keywords)

            if result["success"] and result["data"]:
                return result["data"]
            else:
                self.logger.warning(
                    f"No servers found: {result.get('error', 'Unknown error')}"
                )
                return []

        except Exception as e:
            self.logger.warning(f"Optimized MCP discovery failed: {e}")
            # Fallback to original method
            return await self._dynamic_mcp_server_discovery(query_keywords)

    async def _dynamic_mcp_server_discovery(
        self, query_keywords: List[str]
    ) -> List[Dict]:
        """Dynamically discover MCP servers from database based on query keywords"""
        try:
            self.logger.info(f"🔍 Dynamic MCP discovery for keywords: {query_keywords}")
            return await self.mcp_discovery.find_servers_by_keywords(query_keywords)
        except Exception as e:
            self.logger.warning(f"Dynamic MCP discovery failed: {e}")
            return []

    async def _execute_dynamic_discovery_workflow(
        self, intent_analysis: Dict, message: str, agents: List[Agent]
    ) -> str:
        """Execute the dynamic MCP server discovery workflow"""
        try:
            keywords = intent_analysis.get("discovery_keywords", [])
            self.logger.info(f"🔍 Starting enhanced discovery for keywords: {keywords}")

            # Find best match from database
            discovered_servers = await self._dynamic_mcp_server_discovery(keywords)

            if not discovered_servers:
                return f"⚠️ No MCP servers found for '{', '.join(keywords)}'"

            # Use the first discovered server
            server_data = discovered_servers[0]
            self.logger.info(f"✅ Using database server '{server_data['server_name']}'")

            # Create dynamic agent with the database server
            dynamic_agent = await self._create_dynamic_agent_with_servers(
                [server_data], "database_agent"
            )

            if not dynamic_agent:
                if agents:
                    return await self._execute_sequential_fallback(message, agents)
                else:
                    return f"❌ Failed to create agent for database server '{server_data['server_name']}'"

            try:
                async with dynamic_agent:
                    llm = await dynamic_agent.attach_llm(OpenAIAugmentedLLM)
                    enhanced_prompt = f"""
                    Original request: {message}
                    
                    You have access to the specialized '{server_data["server_name"]}' MCP server:
                    - Description: {server_data.get("description", "Specialized server")}
                    - Transport: {server_data.get("transport", "unknown")}
                    
                    Use the tools from this server to fulfill the user's request.
                    """
                    result = await llm.generate_str(enhanced_prompt)
                    return result
            finally:
                # Clean up dynamic server registration
                if (
                    self.mcp_app
                    and hasattr(self.mcp_app.context, "server_registry")
                    and "dynamic_server"
                    in self.mcp_app.context.server_registry.registry
                ):
                    del self.mcp_app.context.server_registry.registry["dynamic_server"]

        except Exception as e:
            self.logger.error(f"Enhanced discovery workflow error: {e}")
            if agents:
                return await self._execute_sequential_fallback(message, agents)
            else:
                return f"❌ Enhanced discovery failed: {str(e)}"

    async def _create_dynamic_agent_with_servers(
        self, server_configs: List[Dict], agent_name: str = "dynamic_agent"
    ) -> Optional[Agent]:
        """Create a temporary agent with dynamically discovered MCP servers"""
        try:
            if not server_configs or not self.mcp_app:
                return None

            primary_config = server_configs[0]
            server_name = primary_config["server_name"]

            self.logger.info(f"🚀 Creating dynamic agent with server: {server_name}")

            # Configure dynamic server
            from mcp_agent.config import MCPServerSettings

            # Fix transport configuration for ghl-dynamic and similar servers
            transport = primary_config.get("transport", "sse")
            url = primary_config.get("url")
            command = primary_config.get("command")
            args = primary_config.get("args", [])

            # Special handling for servers with SSE URLs but stdio transport
            if server_name == "ghl-dynamic" and url and url.endswith("/sse"):
                self.logger.info(f"🔧 Fixing ghl-dynamic transport configuration")
                transport = "sse"
                command = None  # SSE doesn't use command
                args = []  # SSE doesn't use args
            elif url and url.endswith("/sse") and transport == "stdio":
                self.logger.info(
                    f"🔧 Detected SSE URL with stdio transport, switching to SSE"
                )
                transport = "sse"
                command = None
                args = []

            dynamic_server_config = MCPServerSettings(
                name=primary_config.get("display_name", server_name),
                description=primary_config.get(
                    "description", f"Dynamically discovered server: {server_name}"
                ),
                transport=transport,
                url=url,
                command=command,
                args=args,
                terminate_on_close=True,
            )

            self.logger.info(f"🔧 Server config - transport: {transport}, url: {url}")
            if command:
                self.logger.info(f"🔧 Server config - command: {command}, args: {args}")

            # Add to registry temporarily
            dynamic_server_name = "dynamic_server"
            if hasattr(self.mcp_app.context, "server_registry"):
                self.mcp_app.context.server_registry.registry[dynamic_server_name] = (
                    dynamic_server_config
                )

                # Create agent
                dynamic_agent = Agent(
                    name=f"{agent_name}_{int(datetime.now().timestamp())}",
                    instruction=f"""You are a dynamic agent with access to the '{server_name}' MCP server.
                    
                    This server provides GoHighLevel (GHL) functionality with pre-configured API access.
                    Use the available tools to fulfill user requests with actual data.
                    
                    When a user asks about GHL functionality, use the tools from this server to:
                    - Retrieve contacts, campaigns, opportunities, or other GHL data
                    - Perform actions like creating/updating records
                    - Provide real-time information from the GHL system
                    
                    Always list available tools first if you're unsure what actions you can perform.""",
                    server_names=[dynamic_server_name],
                    context=self.mcp_app.context,
                    human_input_callback=self.slack_human_input_callback,
                )

                await dynamic_agent.__aenter__()
                return dynamic_agent

            return None

        except Exception as e:
            self.logger.error(f"Failed to create dynamic agent: {e}")
            return None

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
        # Look for patterns like "airtable", "workflow", "webhook", etc.
        service_patterns = [
            r"\b(airtable|air table)\b",
            r"\b(workflow|work flow)\b",
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
        """Check if the message requires dynamic MCP server discovery using similarity scoring"""
        if not keywords:
            return False

        self.logger.debug(f"🔍 Finding best server match for keywords: {keywords}")

        # Get configured servers
        configured_servers = []
        if self.mcp_app and hasattr(self.mcp_app.context, "server_registry"):
            configured_servers = list(
                self.mcp_app.context.server_registry.registry.keys()
            )

        # Score configured servers
        best_configured_match = self._score_server_matches(keywords, configured_servers)

        # Estimate database match potential
        potential_database_matches = self._estimate_database_match_potential(
            keywords, message
        )

        self.logger.debug(f"🎯 Best configured match: {best_configured_match}")
        self.logger.debug(f"🔍 Database match potential: {potential_database_matches}")

        # Decision logic: Use dynamic discovery if database might have better matches
        if best_configured_match and best_configured_match["score"] >= 0.8:
            # High confidence configured match - but still check if database might be more specific
            if (
                potential_database_matches["specificity_score"]
                > best_configured_match["score"]
            ):
                self.logger.info(
                    f"🔍 Database might have more specific match than configured '{best_configured_match['server']}' (score: {best_configured_match['score']:.2f}) - exploring database"
                )
                return True
            else:
                self.logger.info(
                    f"✅ Using configured server '{best_configured_match['server']}' (score: {best_configured_match['score']:.2f}) - skipping database search"
                )
                return False

        elif best_configured_match and best_configured_match["score"] >= 0.5:
            # Medium confidence configured match - check database for better options
            if (
                potential_database_matches["has_compound_keywords"]
                or potential_database_matches["specificity_score"] > 0.7
            ):
                self.logger.info(
                    f"🔍 Configured match '{best_configured_match['server']}' (score: {best_configured_match['score']:.2f}) might have better database alternatives"
                )
                return True
            else:
                self.logger.info(
                    f"✅ Using configured server '{best_configured_match['server']}' (score: {best_configured_match['score']:.2f})"
                )
                return False

        else:
            # Low or no configured match - definitely check database
            self.logger.info(
                f"🔍 No strong configured match (best: {best_configured_match['score'] if best_configured_match else 0:.2f}) - checking database"
            )
            return True

    def _score_server_matches(
        self, keywords: List[str], servers: List[str]
    ) -> Optional[Dict[str, Any]]:
        """Score how well keywords match available servers"""
        if not servers or not keywords:
            return None

        best_match = None
        best_score = 0.0

        keywords_lower = [k.lower() for k in keywords]

        for server in servers:
            server_lower = server.lower()
            score = 0.0

            # Exact keyword match
            for keyword in keywords_lower:
                if keyword == server_lower:
                    score += 1.0
                elif keyword in server_lower:
                    score += 0.8
                elif server_lower in keyword:
                    score += 0.6

            # Compound keyword matching (e.g., "arc_airtable" vs ["arc", "airtable"])
            if len(keywords_lower) > 1:
                compound_match = all(k in server_lower for k in keywords_lower)
                if compound_match:
                    score += 1.5  # Bonus for compound matches

            # Specificity bonus (longer, more specific server names get slight bonus)
            if "_" in server_lower and len(keywords_lower) > 1:
                score += 0.2

            if score > best_score:
                best_score = score
                best_match = {
                    "server": server,
                    "score": score,
                    "keywords_matched": keywords_lower,
                }

        return best_match

    def _estimate_database_match_potential(
        self, keywords: List[str], message: str
    ) -> Dict[str, Any]:
        """Estimate the potential for finding better matches in database"""
        keywords_lower = [k.lower() for k in keywords]

        # Check for compound keywords that suggest specific instances
        compound_keywords = [k for k in keywords if "_" in k]

        # Check for organization + service patterns
        org_keywords = [
            k for k in keywords if len(k) <= 10 and k.isupper() and len(k) >= 2
        ]
        service_keywords = [
            k
            for k in keywords
            if k.lower() in ["airtable", "supabase", "workflow", "api", "webhook"]
        ]

        has_org_service_combo = len(org_keywords) > 0 and len(service_keywords) > 0

        # Calculate specificity score
        specificity_score = 0.0
        if compound_keywords:
            specificity_score += 0.8
        if has_org_service_combo:
            specificity_score += 0.7
        if len(keywords) > 1:
            specificity_score += 0.3

        return {
            "has_compound_keywords": len(compound_keywords) > 0,
            "has_org_service_combo": has_org_service_combo,
            "specificity_score": min(specificity_score, 1.0),
            "compound_keywords": compound_keywords,
            "org_keywords": org_keywords,
            "service_keywords": service_keywords,
        }

    async def add_mcp_server_workflow(self, user_message: str, user_id: str) -> str:
        """Handle the workflow for adding a new MCP server from Slack"""
        try:
            self.logger.info(
                f"🔧 Starting MCP server addition workflow for user {user_id}"
            )

            # Parse any server info from the initial message
            server_info = self._parse_server_info_from_message(user_message)

            # Guide user through gathering complete server information
            complete_server_info = await self._gather_server_information(
                server_info, user_id
            )

            if not complete_server_info:
                return "❌ MCP server addition was cancelled or incomplete."

            # Add the server to the database
            result = await self._add_server_to_database(complete_server_info)

            if result["success"]:
                return f"""✅ **MCP Server Added Successfully!**

**Server Details:**
• **Name:** {complete_server_info["server_name"]}
• **Display Name:** {complete_server_info["display_name"]}
• **Transport:** {complete_server_info["transport"]}
• **URL/Command:** {complete_server_info.get("url") or complete_server_info.get("command", "N/A")}

The server is now available for use! You can reference it in future requests.

*Server ID: {result.get("server_id", "unknown")}*"""
            else:
                return f"❌ Failed to add MCP server: {result.get('error', 'Unknown error')}"

        except Exception as e:
            self.logger.error(f"MCP server addition workflow error: {e}")
            return f"❌ Error adding MCP server: {str(e)}"

    async def feedback_collection_workflow(
        self, user_message: str, user_id: str
    ) -> str:
        """Handle the workflow for collecting user feedback"""
        try:
            self.logger.info(
                f"💬 Starting feedback collection workflow for user {user_id}"
            )

            # Parse feedback from the message
            feedback_info = self._parse_feedback_from_message(user_message)

            if feedback_info["has_feedback"]:
                # User provided feedback directly in their message
                feedback_text = feedback_info["feedback_text"]
                category = feedback_info["category"]

                self.logger.info(
                    f"📝 Direct feedback detected: {feedback_text[:50]}..."
                )
            else:
                # User wants to give feedback but didn't provide it yet
                feedback_request = HumanInputRequest(
                    request_id=f"feedback_{int(datetime.now().timestamp())}",
                    prompt="💬 **Thanks for wanting to share feedback!**\n\nWhat would you like to tell us? This could be:\n• General feedback about the system\n• Bug reports or issues you've encountered\n• Feature requests or suggestions\n• Ideas for improvements\n\nPlease share your thoughts:",
                    description="Collecting user feedback",
                )

                # Get feedback from user
                response = await self.slack_human_input_callback(feedback_request)

                if not response or not response.response.strip():
                    return (
                        "❌ No feedback provided. Feel free to share feedback anytime!"
                    )

                feedback_text = response.response.strip()
                category = self._categorize_feedback(feedback_text)

                self.logger.info(
                    f"📝 Interactive feedback collected: {feedback_text[:50]}..."
                )

            # Store feedback in database
            result = await self._store_feedback_in_database(
                user_id=user_id,
                channel_id=getattr(self, "current_channel_id", None),
                feedback_text=feedback_text,
                category=category,
                metadata={
                    "message_length": len(feedback_text),
                    "collection_method": "direct"
                    if feedback_info["has_feedback"]
                    else "interactive",
                    "original_message": user_message[:100]
                    if len(user_message) < 100
                    else user_message[:100] + "...",
                },
            )

            if result["success"]:
                return f"""✅ Thank you for your feedback!

Your feedback has been recorded and will help us improve the system.

Feedback Summary:
- Category: {category.replace("_", " ").title()}
- Length: {len(feedback_text)} characters
- Recorded: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

Is there anything else you'd like to share or any other feedback you have?"""
            else:
                return f"❌ Sorry, there was an issue recording your feedback: {result.get('error', 'Unknown error')}. Please try again."

        except Exception as e:
            self.logger.error(f"Feedback collection workflow error: {e}")
            return f"❌ Error collecting feedback: {str(e)}"

    def _parse_feedback_from_message(self, message: str) -> Dict[str, Any]:
        """Parse feedback information from the user's message"""
        message_lower = message.lower().strip()

        # Patterns that indicate the user is providing feedback directly
        direct_feedback_patterns = [
            r"here\s+is\s+feedback[:\s]*(.+)",
            r"here's\s+feedback[:\s]*(.+)",
            r"my\s+feedback\s+is[:\s]*(.+)",
            r"feedback[:\s]*(.+)",
            r"i\s+have\s+feedback[\s\-:]*(.+)",  # Added for "I have feedback - ..."
            r"i\s+think\s+(.+)",
            r"suggestion[:\s]*(.+)",
            r"improvement[:\s]*(.+)",
            r"issue\s+with[:\s]*(.+)",
            r"problem\s+with[:\s]*(.+)",
            r"bug\s+report[:\s]*(.+)",
            r"feature\s+request[:\s]*(.+)",
        ]

        # Patterns that indicate user wants to give feedback but hasn't provided it yet
        intent_only_patterns = [
            r"i'd?\s+like\s+to\s+give\s+feedback",
            r"i\s+want\s+to\s+give\s+feedback",
            r"give\s+feedback",
            r"provide\s+feedback",
            r"share\s+feedback",
            r"can\s+i\s+give\s+feedback",
            r"how\s+do\s+i\s+give\s+feedback",
        ]

        feedback_info = {
            "has_feedback": False,
            "feedback_text": "",
            "category": "general",
        }

        # Check for direct feedback first
        for pattern in direct_feedback_patterns:
            match = re.search(pattern, message_lower, re.IGNORECASE | re.DOTALL)
            if match:
                feedback_text = match.group(1).strip()
                if len(feedback_text) > 10:  # Ensure it's substantial feedback
                    feedback_info["has_feedback"] = True
                    feedback_info["feedback_text"] = feedback_text
                    feedback_info["category"] = self._categorize_feedback(feedback_text)
                    self.logger.info(
                        f"📝 Direct feedback parsed: {feedback_text[:30]}..."
                    )
                    return feedback_info

        # If no direct feedback found, check if they want to give feedback
        for pattern in intent_only_patterns:
            if re.search(pattern, message_lower):
                feedback_info["has_feedback"] = False
                self.logger.info(
                    "💭 User wants to give feedback but hasn't provided it yet"
                )
                return feedback_info

        # If message contains feedback keywords but no clear pattern, treat as direct feedback
        feedback_keywords = [
            "feedback",
            "suggestion",
            "improvement",
            "issue",
            "problem",
            "bug",
            "feature",
        ]
        if (
            any(keyword in message_lower for keyword in feedback_keywords)
            and len(message.strip()) > 20
        ):
            feedback_info["has_feedback"] = True
            feedback_info["feedback_text"] = message.strip()
            feedback_info["category"] = self._categorize_feedback(message)
            self.logger.info(f"📝 Implicit feedback detected: {message[:30]}...")

        return feedback_info

    def _categorize_feedback(self, feedback_text: str) -> str:
        """Automatically categorize feedback based on content"""
        feedback_lower = feedback_text.lower()

        # Bug reports
        if any(
            word in feedback_lower
            for word in [
                "bug",
                "error",
                "broken",
                "crash",
                "not working",
                "issue",
                "problem",
            ]
        ):
            return "bug_report"

        # Feature requests
        if any(
            word in feedback_lower
            for word in [
                "feature",
                "add",
                "new",
                "would like",
                "wish",
                "could you",
                "request",
            ]
        ):
            return "feature_request"

        # Improvements
        if any(
            word in feedback_lower
            for word in [
                "improve",
                "better",
                "enhance",
                "upgrade",
                "optimize",
                "suggestion",
            ]
        ):
            return "improvement"

        # Performance issues
        if any(
            word in feedback_lower
            for word in ["slow", "fast", "performance", "speed", "lag", "delay"]
        ):
            return "performance"

        # User experience
        if any(
            word in feedback_lower
            for word in [
                "confusing",
                "unclear",
                "difficult",
                "easy",
                "user",
                "interface",
                "ux",
            ]
        ):
            return "user_experience"

        # Positive feedback
        if any(
            word in feedback_lower
            for word in [
                "good",
                "great",
                "love",
                "excellent",
                "awesome",
                "thank",
                "helpful",
            ]
        ):
            return "positive"

        # Default to general
        return "general"

    async def _store_feedback_in_database(
        self,
        user_id: str,
        channel_id: str,
        feedback_text: str,
        category: str,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Store feedback in the database"""
        try:
            # Try direct Supabase client first for reliability
            if self.supabase_direct_client:
                result = await self.supabase_direct_client.store_feedback(
                    user_id=user_id,
                    channel_id=channel_id,
                    feedback_text=feedback_text,
                    category=category,
                    metadata=metadata,
                )
                if result["success"]:
                    self.logger.info(
                        f"✅ Stored feedback for user {user_id} via direct client"
                    )
                    return result
                else:
                    self.logger.warning(
                        f"⚠️ Direct feedback storage failed: {result['error']}, falling back to MCP"
                    )

            # Fallback to database operations
            return await self.db_ops.store_feedback(
                user_id, channel_id, feedback_text, category, metadata
            )

        except Exception as e:
            self.logger.error(f"❌ Feedback storage error: {e}")
            return {"success": False, "error": str(e)}

    def _parse_server_info_from_message(self, message: str) -> Dict[str, Any]:
        """Parse any server information from the user's initial message"""
        server_info = {}
        message_lower = message.lower()

        # Extract server name patterns
        name_patterns = [
            r"server\s+(?:name\s+)?['\"]?(\w+)['\"]?",
            r"mcp\s+['\"]?(\w+)['\"]?",
            r"add\s+['\"]?(\w+)['\"]?\s+server",
        ]

        for pattern in name_patterns:
            match = re.search(pattern, message_lower)
            if match:
                server_info["server_name"] = match.group(1)
                break

        # Extract URL patterns
        url_patterns = [
            r"(https?://[^\s]+)",
            r"url[:\s]+([^\s]+)",
        ]

        for pattern in url_patterns:
            match = re.search(pattern, message)
            if match:
                server_info["url"] = match.group(1)
                break

        # Extract transport type
        if any(transport in message_lower for transport in ["sse", "server-sent"]):
            server_info["transport"] = "sse"
        elif "stdio" in message_lower:
            server_info["transport"] = "stdio"
        elif "websocket" in message_lower:
            server_info["transport"] = "websocket"

        # Extract description hints
        if "description" in message_lower:
            desc_match = re.search(
                r"description[:\s]+['\"]?([^'\"]+)['\"]?", message, re.IGNORECASE
            )
            if desc_match:
                server_info["description"] = desc_match.group(1).strip()

        self.logger.info(f"📝 Parsed server info from message: {server_info}")
        return server_info

    async def _gather_server_information(
        self, initial_info: Dict[str, Any], user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Interactively gather complete server information from the user"""
        try:
            server_info = initial_info.copy()

            # Required fields and their prompts
            required_fields = {
                "server_name": "What should we call this MCP server? (e.g., 'my_api_server')",
                "display_name": "What's a friendly display name for this server? (e.g., 'My API Server')",
                "description": "What does this server do? Please provide a brief description.",
                "transport": "What transport type does this server use? Options: **stdio** (Command Line), **sse** (Server-Sent Events), **streamable_http** (HTTP Streaming), or **websocket**",
            }

            # Gather required fields
            for field, prompt in required_fields.items():
                if field not in server_info or not server_info[field]:
                    response = await self._ask_user_for_info(prompt, user_id)
                    if not response or response.lower() in ["cancel", "quit", "exit"]:
                        return None
                    server_info[field] = response.strip()

            # Validate and gather transport-specific fields
            transport = server_info["transport"].lower()

            # Validate transport against database constraints
            allowed_transports = ["stdio", "sse", "streamable_http", "websocket"]
            if transport not in allowed_transports:
                self.logger.warning(f"Invalid transport type: {transport}")
                transport_response = await self._ask_user_for_info(
                    f"Invalid transport type '{server_info['transport']}'. Please choose from: **stdio**, **sse**, **streamable_http**, or **websocket**",
                    user_id,
                )
                if not transport_response or transport_response.lower() in [
                    "cancel",
                    "quit",
                    "exit",
                ]:
                    return None
                transport = transport_response.strip().lower()

                # Re-validate the new transport
                if transport not in allowed_transports:
                    return None

            # Store normalized transport type
            server_info["transport"] = transport

            if transport in ["sse", "websocket", "streamable_http"]:
                # Check if URL is missing or invalid (not a proper URL)
                current_url = server_info.get("url", "")
                is_valid_url = current_url and (
                    current_url.startswith("http://")
                    or current_url.startswith("https://")
                )

                if not current_url or not is_valid_url:
                    # Clear any invalid URL that might have been set
                    if current_url and not is_valid_url:
                        self.logger.warning(
                            f"Invalid URL detected: '{current_url}', prompting for correct URL"
                        )
                        server_info.pop("url", None)

                    url_response = await self._ask_user_for_info(
                        f"What's the URL for this {transport.upper()} server? (e.g., 'https://api.example.com/mcp')",
                        user_id,
                    )
                    if not url_response or url_response.lower() in [
                        "cancel",
                        "quit",
                        "exit",
                    ]:
                        return None
                    server_info["url"] = url_response.strip()

            elif transport == "stdio":
                if "command" not in server_info or not server_info["command"]:
                    cmd_response = await self._ask_user_for_info(
                        "What command should we run to start this server? (e.g., 'node server.js' or 'python server.py')",
                        user_id,
                    )
                    if not cmd_response or cmd_response.lower() in [
                        "cancel",
                        "quit",
                        "exit",
                    ]:
                        return None
                    server_info["command"] = cmd_response.strip()

                # Optional: Ask for command arguments
                args_response = await self._ask_user_for_info(
                    "Any command-line arguments? (Press Enter to skip, or provide space-separated args)",
                    user_id,
                )
                if (
                    args_response
                    and args_response.strip()
                    and args_response.lower() not in ["skip", "none", ""]
                ):
                    server_info["args"] = args_response.strip().split()
                else:
                    server_info["args"] = []

            # Set defaults for optional fields
            if "args" not in server_info:
                server_info["args"] = []

            # Show summary and confirm
            summary = f"""**MCP Server Configuration Summary:**
• **Name:** {server_info["server_name"]}
• **Display Name:** {server_info["display_name"]}
• **Description:** {server_info["description"]}
• **Transport:** {server_info["transport"]}"""

            if server_info.get("url"):
                summary += f"\n• **URL:** {server_info['url']}"
            if server_info.get("command"):
                summary += f"\n• **Command:** {server_info['command']}"
            if server_info.get("args"):
                summary += f"\n• **Arguments:** {' '.join(server_info['args'])}"

            summary += "\n\nDoes this look correct? Reply **yes** to add the server or **no** to cancel."

            confirmation = await self._ask_user_for_info(summary, user_id)

            if confirmation and confirmation.lower().startswith("y"):
                return server_info
            else:
                return None

        except Exception as e:
            self.logger.error(f"Error gathering server information: {e}")
            return None

    async def _ask_user_for_info(self, prompt: str, user_id: str) -> Optional[str]:
        """Ask the user for information using the Slack human input system"""
        try:
            request = HumanInputRequest(
                request_id=f"mcp_server_input_{int(datetime.now().timestamp())}",
                prompt=f"🔧 **MCP Server Setup**\n\n{prompt}",
                description="Setting up a new MCP server",
            )

            response = await self.slack_human_input_callback(request)
            return response.response if response else None

        except Exception as e:
            self.logger.error(f"Error asking user for info: {e}")
            return None

    async def _add_server_to_database(
        self, server_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Add the MCP server configuration to the Supabase database"""
        try:
            # Use direct client first for better security with secrets
            if self.supabase_direct_client:
                self.logger.info(
                    "🔐 Using secure MCP server insertion with secrets management"
                )
                result = await self.supabase_direct_client.insert_mcp_server_secure(
                    server_info
                )

                if result["success"]:
                    return {
                        "success": True,
                        "server_id": result.get("server_id", "unknown"),
                        "message": f"✅ Server '{server_info['server_name']}' added successfully",
                    }
                else:
                    self.logger.warning(
                        f"⚠️ Secure insertion failed: {result['error']}, falling back"
                    )

            # Fallback to database operations
            result = await self.db_ops.add_mcp_server(server_info)
            return result

        except Exception as e:
            self.logger.error(f"❌ Database operation error: {e}")
            return {"success": False, "error": str(e)}

    async def cleanup(self):
        """Clean up resources"""
        await self.slack_manager.cleanup()

        # Clean up pre-warmed agents
        try:
            for agent in self.agent_pool.values():
                try:
                    await agent.__aexit__(None, None, None)
                except Exception as e:
                    self.logger.warning(f"Could not clean up agent: {e}")
            self.agent_pool.clear()
        except Exception as e:
            self.logger.warning(f"Cleanup warning: {e}")

        # Save learning patterns before shutdown
        self._save_learning_patterns()

        # Clear processed messages to free memory
        if hasattr(self, "processed_messages"):
            self.processed_messages.clear()

    # Add all the missing critical methods from main.py
    def _load_learning_patterns(self) -> Dict[str, Dict]:
        """Load persistent learning patterns from file or create defaults"""
        try:
            patterns_file = self.config_dict["learning_persistence_file"]
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
                    "workflow",
                    "automate",
                    "trigger",
                    "automation",
                    "zapier",
                    "airtable workflow",
                ],
                "agent": "automation_specialist",
                "confidence": 0.85,
                "usage_count": 0,
            },
            "airtable": {
                "keywords": [
                    "airtable",
                    "air table",
                    "airtable base",
                    "airtable records",
                    "airtable data",
                    "base id",
                    "table records",
                    "airtable api",
                    "airtable database",
                    "records in airtable",
                    "update airtable",
                    "create airtable",
                    "delete airtable",
                    "query airtable",
                ],
                "agent": "airtable_manager",
                "confidence": 0.9,
                "usage_count": 0,
            },
            "server_exploration": {
                "keywords": [
                    "unknown server",
                    "explore server",
                    "test server",
                    "server capabilities",
                    "discover tools",
                    "mcp server",
                    "new endpoint",
                    "what tools does",
                    "how to use",
                    "server analysis",
                ],
                "agent": "mcp_server_explorer",
                "confidence": 0.9,
                "usage_count": 0,
            },
            "add_mcp_server": {
                "keywords": [
                    "add mcp server",
                    "add mcp",
                    "register mcp",
                    "new mcp server",
                    "connect mcp",
                    "add server",
                    "register server",
                    "create mcp",
                    "setup mcp",
                    "configure mcp",
                ],
                "agent": "mcp_server_manager",
                "confidence": 0.95,
                "usage_count": 0,
            },
            "feedback": {
                "keywords": [
                    "feedback",
                    "give feedback",
                    "provide feedback",
                    "share feedback",
                    "i'd like to give feedback",
                    "i want to give feedback",
                    "here is feedback",
                    "here's feedback",
                    "my feedback",
                    "feedback on",
                    "suggestion",
                    "improvement",
                    "issue with",
                    "problem with",
                    "bug report",
                    "feature request",
                ],
                "agent": "feedback_collector",
                "confidence": 0.90,
                "usage_count": 0,
            },
        }

        self.logger.info("📚 Using default learning patterns")
        return default_patterns

    # ======= MISSING CRITICAL SERVER DISCOVERY METHODS =======
    # These methods were missing from the refactored version and are essential
    # for the advanced MCP server discovery and user disambiguation functionality

    async def _find_best_server_match(
        self, keywords: List[str], message: str
    ) -> Optional[Dict[str, Any]]:
        """Find the best server match with ORGANIZATION+TOOL direct lookup priority"""

        # 🎯 PRIORITY 1: Direct Organization + Tool Lookup
        org_tool_match = await self._try_direct_organization_tool_lookup(
            keywords, message
        )
        if org_tool_match:
            self.logger.info(
                f"🎯 Direct organization+tool match: '{org_tool_match['server_name']}' (source: {org_tool_match['source']})"
            )
            return org_tool_match

        # 🔄 FALLBACK: Traditional scoring-based matching
        self.logger.info(
            "🔄 No direct organization+tool match found, falling back to scoring-based matching"
        )

        # Get configured servers and score them
        configured_servers = []
        if self.mcp_app and hasattr(self.mcp_app.context, "server_registry"):
            configured_servers = list(
                self.mcp_app.context.server_registry.registry.keys()
            )

        all_configured_matches = self._score_all_server_matches(
            keywords, configured_servers, "configured"
        )

        # Get database servers and score them
        discovered_servers = await self._dynamic_mcp_server_discovery(keywords)
        database_server_names = (
            [s["server_name"] for s in discovered_servers] if discovered_servers else []
        )
        all_database_matches = self._score_all_server_matches(
            keywords, database_server_names, "database"
        )

        # Add server data to database matches
        for match in all_database_matches:
            match["server_data"] = next(
                (
                    s
                    for s in discovered_servers
                    if s["server_name"] == match["server_name"]
                ),
                None,
            )

        # Combine all candidates
        all_candidates = all_configured_matches + all_database_matches

        if not all_candidates:
            return None

        # Simple sorting by score for fallback matching
        all_candidates.sort(key=lambda x: x["score"], reverse=True)

        # Check for ambiguity and ask user if needed
        disambiguation_result = await self._handle_server_disambiguation(
            all_candidates, keywords, message
        )

        if disambiguation_result:
            source_emoji = "🗄️" if disambiguation_result["source"] == "database" else "⚙️"
            self.logger.info(
                f"🎯 User selected: {source_emoji} {disambiguation_result['server_name']} (source: {disambiguation_result['source']}, score: {disambiguation_result['score']:.2f})"
            )
            return disambiguation_result

        # Return the best match
        best_match = all_candidates[0]
        source_emoji = "🗄️" if best_match["source"] == "database" else "⚙️"
        self.logger.info(
            f"✅ Using {source_emoji} '{best_match['server_name']}' (source: {best_match['source']}, score: {best_match['score']:.2f}) - fallback match"
        )

        return best_match

    async def _try_direct_organization_tool_lookup(
        self, keywords: List[str], message: str
    ) -> Optional[Dict[str, Any]]:
        """Try direct database lookup for ORGANIZATION + TOOL patterns (e.g., 'ARC Supabase')"""

        if len(keywords) < 2:
            return None

        # Detect organization + tool patterns
        org_tool_patterns = self._detect_organization_tool_patterns(keywords, message)

        if not org_tool_patterns:
            return None

        for pattern in org_tool_patterns:
            org_name = pattern["organization"]
            tool_name = pattern["tool"]

            self.logger.info(f"🔍 Direct lookup: {org_name} + {tool_name}")

            # Try exact database lookup for this organization + tool combination
            direct_match = await self._lookup_exact_organization_tool(
                org_name, tool_name
            )

            if direct_match:
                return {
                    "source": "database",
                    "server_name": direct_match["server_name"],
                    "score": 3.0,  # Highest priority score
                    "keywords_matched": keywords,
                    "server_data": direct_match,
                    "match_type": "direct_organization_tool",
                    "organization": org_name,
                    "tool": tool_name,
                }

        return None

    def _detect_organization_tool_patterns(
        self, keywords: List[str], message: str
    ) -> List[Dict[str, str]]:
        """Detect ORGANIZATION + TOOL patterns in keywords and message"""

        common_tools = [
            "supabase",
            "postgres",
            "mysql",
            "redis",
            "mongodb",
            "sqlite",
            "airtable",
            "github",
            "slack",
            "discord",
            "notion",
            "figma",
            "anthropic",
            "openai",
            "claude",
            "aws",
            "azure",
            "gcp",
        ]

        keywords_lower = [k.lower() for k in keywords]
        patterns = []

        # Look for adjacent organization + tool pairs
        for i, keyword in enumerate(keywords_lower):
            if keyword in common_tools:
                # Check if previous keyword could be an organization
                if i > 0:
                    prev_keyword = keywords_lower[i - 1]
                    if prev_keyword not in common_tools and len(prev_keyword) >= 2:
                        patterns.append({"organization": prev_keyword, "tool": keyword})

                # Check if next keyword could be an organization
                if i < len(keywords_lower) - 1:
                    next_keyword = keywords_lower[i + 1]
                    if next_keyword not in common_tools and len(next_keyword) >= 2:
                        patterns.append({"organization": next_keyword, "tool": keyword})

        # Also check message for patterns like "ARC Supabase" or "Company's Tool"
        import re

        org_tool_regex = r"\b([A-Z][A-Za-z\s]{1,20})\s+(supabase|postgres|mysql|redis|mongodb|sqlite|airtable|github|slack|discord|notion|figma)\b"
        matches = re.finditer(org_tool_regex, message, re.IGNORECASE)

        for match in matches:
            org = match.group(1).strip().lower()
            tool = match.group(2).lower()
            patterns.append({"organization": org, "tool": tool})

        return patterns

    async def _lookup_exact_organization_tool(
        self, org_name: str, tool_name: str
    ) -> Optional[Dict[str, Any]]:
        """Perform exact database lookup for organization + tool combination"""

        # Generate possible server name variations
        possible_names = [
            f"{org_name}_{tool_name}",
            f"{org_name}-{tool_name}",
            f"{org_name}{tool_name}",
            f"{tool_name}_{org_name}",
            f"{tool_name}-{org_name}",
            f"{org_name}_supabase"
            if tool_name == "supabase"
            else f"{org_name}_{tool_name}",
        ]

        # Remove duplicates while preserving order
        seen = set()
        unique_names = []
        for name in possible_names:
            if name not in seen:
                seen.add(name)
                unique_names.append(name)

        self.logger.info(f"🔍 Trying exact lookups for: {unique_names}")

        # Try each possible name variation
        for server_name in unique_names:
            discovered_servers = await self._dynamic_mcp_server_discovery([server_name])

            if discovered_servers:
                # Found exact match!
                match = discovered_servers[0]
                self.logger.info(f"✅ Found exact match: '{server_name}' in database")
                return match

        # Also try broader organization search
        org_servers = await self._dynamic_mcp_server_discovery([org_name])

        for server in org_servers:
            server_name_lower = server["server_name"].lower()
            if tool_name in server_name_lower and org_name in server_name_lower:
                self.logger.info(
                    f"✅ Found broad match: '{server['server_name']}' contains both {org_name} and {tool_name}"
                )
                return server

        return None

    async def _handle_server_disambiguation(
        self, candidates: List[Dict[str, Any]], keywords: List[str], message: str
    ) -> Optional[Dict[str, Any]]:
        """Handle cases where multiple servers could match - ask user to choose"""

        # Disambiguation scenarios:
        # 1. Best match has low confidence (< 0.8)
        # 2. Multiple matches with similar scores (within 0.3 of each other)
        # 3. More than 3 potential matches above threshold

        best_score = candidates[0]["score"] if candidates else 0

        # Check if we should ask user for disambiguation
        should_disambiguate = False
        reason = ""

        if best_score < 0.8:
            should_disambiguate = True
            reason = f"low confidence (best score: {best_score:.2f})"

        elif len(candidates) >= 2:
            # Check if there are multiple similar matches
            similar_matches = [c for c in candidates if c["score"] >= best_score - 0.3]
            if len(similar_matches) >= 2:
                should_disambiguate = True
                reason = f"{len(similar_matches)} similar matches (within 0.3 points)"

        elif len(candidates) > 3:
            should_disambiguate = True
            reason = f"many potential matches ({len(candidates)} found)"

        if not should_disambiguate:
            return None

        self.logger.info(f"🤔 Asking user for disambiguation due to: {reason}")

        # Limit to top 5 options to avoid overwhelming user
        top_candidates = candidates[:5]

        return await self._ask_user_to_choose_server(top_candidates, keywords, message)

    async def _ask_user_to_choose_server(
        self, candidates: List[Dict[str, Any]], keywords: List[str], message: str
    ) -> Optional[Dict[str, Any]]:
        """Present server options to user and get their choice"""

        try:
            from mcp_agent.human_input.types import HumanInputRequest

            # Format the options for the user
            options_text = (
                "🤔 **I found multiple servers that might match your request:**\n\n"
            )

            for i, candidate in enumerate(candidates, 1):
                source_emoji = "⚙️" if candidate["source"] == "configured" else "🗄️"
                score_text = f"(match: {candidate['score']:.1f})"

                server_description = ""
                if candidate["source"] == "database" and candidate.get("server_data"):
                    server_description = (
                        f" - {candidate['server_data'].get('description', '')}"
                    )

                options_text += f"**{i}.** {source_emoji} `{candidate['server_name']}` {score_text}{server_description}\n"

            options_text += f"\n**Original request:** {message[:100]}{'...' if len(message) > 100 else ''}"
            options_text += f"\n**Keywords detected:** {', '.join(keywords)}"
            options_text += "\n\n**Please reply with:**"
            options_text += "\n• The **number** (1, 2, 3, etc.) of your choice"
            options_text += "\n• The **server name** you want to use"
            options_text += "\n• **'none'** if none of these are correct"
            options_text += "\n• **'auto'** to let me pick the best one"

            choice_request = HumanInputRequest(
                request_id=f"server_choice_{int(datetime.now().timestamp())}",
                prompt=options_text,
                description="Choosing between multiple server options",
            )

            # Get user's choice
            response = await self.slack_human_input_callback(choice_request)

            if not response or not response.response.strip():
                self.logger.info("❌ No server choice provided by user")
                return None

            user_choice = response.response.strip().lower()

            # Parse user's choice
            if user_choice in ["none", "skip", "cancel"]:
                self.logger.info("❌ User declined all server options")
                return None

            elif user_choice in ["auto", "automatic", "best"]:
                self.logger.info("✅ User chose automatic selection")
                return candidates[0]  # Return the best match

            # Try to parse as number
            try:
                choice_num = int(user_choice)
                if 1 <= choice_num <= len(candidates):
                    selected = candidates[choice_num - 1]
                    self.logger.info(
                        f"✅ User selected option {choice_num}: {selected['server_name']}"
                    )
                    return selected
                else:
                    self.logger.warning(f"⚠️ User choice {choice_num} out of range")
                    return candidates[0]  # Fallback to best match
            except ValueError:
                # Try to match by server name
                for candidate in candidates:
                    if user_choice in candidate["server_name"].lower():
                        self.logger.info(
                            f"✅ User selected by name: {candidate['server_name']}"
                        )
                        return candidate

                self.logger.warning(f"⚠️ Could not parse user choice: '{user_choice}'")
                return candidates[0]  # Fallback to best match

        except Exception as e:
            self.logger.error(f"Error in server disambiguation: {e}")
            return None  # Let the system pick automatically

    async def _use_configured_server(
        self, match: Dict[str, Any], message: str, agents: List[Agent]
    ) -> str:
        """Use a server from the configuration"""
        server_name = match["server_name"]

        try:
            # Use an appropriate agent that has access to this server
            agent_type = self._find_agent_for_server(server_name)
            if not agent_type:
                agent_type = "data_researcher"  # Fallback

            agent = await self.get_pooled_agent(
                agent_type, f"configured_server_{int(datetime.now().timestamp())}"
            )

            async with agent:
                llm = await agent.attach_llm(OpenAIAugmentedLLM)
                enhanced_prompt = f"""
                Original request: {message}
                
                You have access to the '{server_name}' MCP server which matches the user's request.
                Use the appropriate tools from this server to fulfill the request.
                
                Focus on providing specific, actionable results with relevant data.
                """
                result = await llm.generate_str(enhanced_prompt)
                return result

        except Exception as e:
            self.logger.warning(f"Failed to use configured server '{server_name}': {e}")
            if agents:
                return await self._execute_sequential_fallback(message, agents)
            else:
                return f"❌ Failed to use configured server '{server_name}': {str(e)}"

    async def _use_database_server(
        self, match: Dict[str, Any], message: str, agents: List[Agent]
    ) -> str:
        """Use a server from the database"""
        server_data = match["server_data"]

        if not server_data:
            self.logger.error("No server data available for database server")
            if agents:
                return await self._execute_sequential_fallback(message, agents)
            else:
                return "❌ Database server data missing"

        # Create dynamic agent with the database server
        dynamic_agent = await self._create_dynamic_agent_with_servers(
            [server_data], "database_agent"
        )

        if not dynamic_agent:
            self.logger.warning("❌ Failed to create dynamic agent for database server")
            if agents:
                return await self._execute_sequential_fallback(message, agents)
            else:
                return f"❌ Failed to create agent for database server '{server_data['server_name']}'"

        try:
            async with dynamic_agent:
                llm = await dynamic_agent.attach_llm(OpenAIAugmentedLLM)

                enhanced_prompt = f"""
                Original request: {message}
                
                You have access to the specialized '{server_data["server_name"]}' MCP server:
                - Description: {server_data.get("description", "Specialized server")}
                - Transport: {server_data.get("transport", "unknown")}
                
                Use the tools from this server to fulfill the user's request. Focus on providing specific, 
                actionable results with relevant data.
                """

                result = await llm.generate_str(enhanced_prompt)
                return result

        finally:
            # Clean up dynamic server registration
            try:
                if (
                    self.mcp_app
                    and hasattr(self.mcp_app.context, "server_registry")
                    and "dynamic_server"
                    in self.mcp_app.context.server_registry.registry
                ):
                    del self.mcp_app.context.server_registry.registry["dynamic_server"]
                    self.logger.debug("🧹 Cleaned up dynamic server registration")
            except Exception as cleanup_error:
                self.logger.warning(f"Dynamic server cleanup warning: {cleanup_error}")

    def _find_agent_for_server(self, server_name: str) -> Optional[str]:
        """Find which agent type can use the given server"""
        server_lower = server_name.lower()

        for agent_type, spec in self.agent_registry.items():
            if server_name in spec.server_names:
                return agent_type
            # Also check case-insensitive
            if any(s.lower() == server_lower for s in spec.server_names):
                return agent_type

        return None

    # Add more missing methods that are crucial for server exploration and configuration generation
    def _generate_potential_server_configs(
        self, keywords: List[str], message: str
    ) -> List[Dict]:
        """Generate potential server configurations based on keywords and message context"""
        configs = []

        # Look for compound keywords that might be server names (like "arc_supabase")
        compound_keywords = [k for k in keywords if "_" in k]

        for compound in compound_keywords:
            # Try to map compound keywords to potential server configurations
            if "supabase" in compound.lower():
                configs.append(
                    {
                        "server_name": compound,
                        "display_name": compound.replace("_", " ").title(),
                        "description": f"Potential Supabase server: {compound}",
                        "transport": "sse",  # Try SSE first as it's common for n8n workflows
                        "url": self._guess_server_url(compound, "supabase"),
                    }
                )
            elif "workflow" in compound.lower() or "automation" in compound.lower():
                configs.append(
                    {
                        "server_name": compound,
                        "display_name": compound.replace("_", " ").title(),
                        "description": f"Potential workflow automation server: {compound}",
                        "transport": "sse",
                        "url": self._guess_server_url(compound, "workflow"),
                    }
                )

        # Also try organization + service combinations
        org_keywords = [
            k for k in keywords if len(k) <= 10 and k.isupper() and len(k) >= 2
        ]
        service_keywords = [
            k
            for k in keywords
            if k.lower() in ["supabase", "airtable", "api", "webhook", "workflow"]
        ]

        for org in org_keywords:
            for service in service_keywords:
                server_name = f"{org.lower()}_{service.lower()}"
                if server_name not in [
                    c["server_name"] for c in configs
                ]:  # Avoid duplicates
                    configs.append(
                        {
                            "server_name": server_name,
                            "display_name": f"{org} {service.title()}",
                            "description": f"Potential {org} {service} server",
                            "transport": "sse",
                            "url": self._guess_server_url(server_name, service.lower()),
                        }
                    )

        self.logger.info(f"🎯 Generated {len(configs)} potential server configurations")
        return configs[:3]  # Limit to top 3 to avoid too many exploration attempts

    def _guess_server_url(self, server_name: str, service_type: str) -> str:
        """Attempt to guess server URL based on patterns (this is speculative)"""
        # This is a fallback - in practice, users should provide actual URLs
        # But we can make educated guesses based on common patterns

        if service_type == "supabase" and "arc" in server_name.lower():
            # Based on the pattern we discovered earlier
            return "https://advertisingreportcard.app.n8n.cloud/mcp/[workflow-id]/sse"
        elif service_type == "workflow":
            return f"https://example.app.n8n.cloud/mcp/{server_name}/sse"
        else:
            return f"https://api.{server_name}.com/mcp/sse"

    # ======= ADDITIONAL MISSING SERVER EXPLORATION METHODS =======

    async def _offer_server_exploration(
        self, keywords: List[str], message: str
    ) -> Optional[str]:
        """Offer MCP server exploration for unknown servers with human-in-the-loop confirmation"""
        try:
            # Check if this looks like a server exploration request
            if not self._looks_like_server_exploration_request(keywords, message):
                return None

            self.logger.info(f"🔍 Offering server exploration for keywords: {keywords}")

            # Use human input callback to get permission
            exploration_request = HumanInputRequest(
                request_id=f"explore_{int(datetime.now().timestamp())}",
                prompt=f"🔍 **Unknown MCP Server Exploration Request**\n\nI found keywords that might indicate an unknown MCP server: {', '.join(keywords)}\n\nWould you like me to systematically explore and test this server to discover its capabilities?\n\n**This exploration will:**\n- Test multiple parameter combinations\n- Make test calls to discover server capabilities\n- Learn from error messages to adapt approach\n- Generate actionable recommendations\n\n**Please respond with 'yes' to proceed or 'no' to skip**",
                description="Request permission to explore unknown MCP server",
            )

            # Get user confirmation
            response = await self.slack_human_input_callback(exploration_request)

            if response.response.lower().strip() in ["yes", "y", "explore", "proceed"]:
                self.logger.info("✅ User confirmed server exploration")
                return await self._execute_server_exploration(keywords, message)
            else:
                self.logger.info("❌ User declined server exploration")
                return "🔍 Server exploration declined. I'll try standard approaches instead."

        except Exception as e:
            self.logger.error(f"Server exploration offer error: {e}")
            return None

    def _looks_like_server_exploration_request(
        self, keywords: List[str], message: str
    ) -> bool:
        """Determine if this looks like a request that could benefit from server exploration"""
        message_lower = message.lower()

        # Look for patterns that suggest unknown server exploration might be helpful
        exploration_indicators = [
            # Direct server/tool references
            any(
                keyword.endswith("_server") or keyword.endswith("_api")
                for keyword in keywords
            ),
            any(keyword.startswith("mcp_") for keyword in keywords),
            # Unknown service patterns with qualifiers (like "ARC supabase")
            len([k for k in keywords if "_" in k]) > 0,
            # Data access requests for potentially unknown sources
            any(
                phrase in message_lower
                for phrase in [
                    "records in",
                    "data from",
                    "connect to",
                    "access to",
                    "table in",
                    "database",
                    "api",
                    "endpoint",
                    "service",
                ]
            ),
            # Organization-specific requests (uppercase acronyms + service)
            any(len(k) <= 10 and k.isupper() for k in keywords if len(k) >= 2),
        ]

        # Only offer exploration if multiple indicators suggest this might be beneficial
        indicator_count = sum(1 for indicator in exploration_indicators if indicator)

        self.logger.debug(
            f"Exploration indicators for '{message[:50]}...': {indicator_count}/5"
        )
        return indicator_count >= 2  # Need at least 2 indicators

    async def _execute_server_exploration(
        self, keywords: List[str], message: str
    ) -> str:
        """Execute systematic server exploration using the universal strategy"""
        try:
            self.logger.info(
                f"🚀 Starting systematic server exploration for: {keywords}"
            )

            # Try to construct potential server configurations from keywords
            potential_configs = self._generate_potential_server_configs(
                keywords, message
            )

            if not potential_configs:
                return "🔍 Could not determine potential server configurations from the request. Please provide more specific server details (URL, name, or connection information)."

            exploration_results = []

            for config in potential_configs:
                self.logger.info(
                    f"🔍 Exploring potential server: {config.get('server_name', 'Unknown')}"
                )

                try:
                    # Use the universal exploration strategy if available
                    if explore_any_mcp_server:
                        results = await explore_any_mcp_server(config)

                        if results and not results.get("error"):
                            exploration_results.append(
                                {"config": config, "results": results, "success": True}
                            )

                            # If we found working patterns, we can stop exploring
                            if results.get("working_patterns"):
                                self.logger.info(
                                    f"✅ Found working patterns, stopping exploration"
                                )
                                break
                        else:
                            exploration_results.append(
                                {"config": config, "results": results, "success": False}
                            )
                    else:
                        # Fallback exploration without universal strategy
                        exploration_results.append(
                            {
                                "config": config,
                                "error": "Universal MCP strategy not available",
                                "success": False,
                            }
                        )

                except Exception as e:
                    self.logger.warning(
                        f"Exploration failed for {config.get('server_name', 'Unknown')}: {e}"
                    )
                    exploration_results.append(
                        {"config": config, "error": str(e), "success": False}
                    )

            # Format comprehensive exploration report
            return self._format_exploration_report(
                exploration_results, keywords, message
            )

        except Exception as e:
            self.logger.error(f"Server exploration execution error: {e}")
            return f"❌ Server exploration failed: {str(e)}"

    def _format_exploration_report(
        self,
        exploration_results: List[Dict],
        keywords: List[str],
        original_message: str,
    ) -> str:
        """Format a comprehensive exploration report for the user"""
        report = []

        report.append("🔍 **MCP Server Exploration Report**")
        report.append("=" * 50)
        report.append(
            f"**Original Request:** {original_message[:100]}{'...' if len(original_message) > 100 else ''}"
        )
        report.append(f"**Keywords Analyzed:** {', '.join(keywords)}")
        report.append("")

        successful_explorations = [r for r in exploration_results if r.get("success")]
        failed_explorations = [r for r in exploration_results if not r.get("success")]

        if successful_explorations:
            report.append("✅ **Successful Explorations:**")
            report.append("")

            for result in successful_explorations:
                config = result["config"]
                results = result["results"]

                report.append(f"**🎯 {config['display_name']}**")
                report.append(f"   Server: {config['server_name']}")
                report.append(f"   Transport: {config['transport']}")

                if results.get("tools_discovered"):
                    tools = results["tools_discovered"]
                    report.append(f"   Tools Found: {len(tools)}")
                    for tool in tools[:3]:  # Show first 3 tools
                        report.append(
                            f"      - {tool['name']}: {tool['description'][:60]}..."
                        )

                if results.get("working_patterns"):
                    patterns = results["working_patterns"]
                    report.append(f"   Working Patterns: {len(patterns)}")
                    for pattern in patterns[:2]:  # Show first 2 patterns
                        report.append(f"      - {pattern['params']}")

                if results.get("recommendations"):
                    report.append("   Recommendations:")
                    for rec in results["recommendations"][:3]:
                        report.append(f"      - {rec}")

                report.append("")

        if failed_explorations:
            report.append("❌ **Failed Explorations:**")
            for result in failed_explorations:
                config = result["config"]
                error = result.get("error", "Unknown error")
                report.append(f"   - {config['display_name']}: {error[:80]}...")
            report.append("")

        # Summary and next steps
        if successful_explorations:
            best_result = successful_explorations[0]
            report.append("🚀 **Recommended Next Steps:**")
            report.append(f"1. Use server: {best_result['config']['server_name']}")

            if best_result["results"].get("working_patterns"):
                best_pattern = best_result["results"]["working_patterns"][0]
                report.append(f"2. Try parameters: {best_pattern['params']}")

            report.append("3. Explore additional parameter variations")
            report.append("4. Contact admin if authentication is needed")
        else:
            report.append("💡 **Alternative Approaches:**")
            report.append("1. Verify server URLs and connection details")
            report.append("2. Check if authentication credentials are needed")
            report.append("3. Confirm server is running and accessible")
            report.append("4. Try different transport methods (stdio, websocket)")

        return "\n".join(report)

    def _parse_mcp_query_result(self, query_result: str) -> List[Dict]:
        """Parse the result from MCP server database query into server configurations"""
        servers = []

        try:
            # First, try to extract JSON arrays or objects from the result
            # Look for JSON arrays first
            json_array_matches = re.findall(r"\[[^\[\]]*\]", query_result, re.DOTALL)
            for json_str in json_array_matches:
                try:
                    server_list = json.loads(json_str)
                    if isinstance(server_list, list):
                        for server_data in server_list:
                            if (
                                isinstance(server_data, dict)
                                and "server_name" in server_data
                            ):
                                servers.append(server_data)
                        if servers:  # If we found valid servers in an array, use them
                            break
                except json.JSONDecodeError:
                    continue

            # If no array found, try individual JSON objects
            if not servers:
                json_matches = re.findall(r"\{[^{}]*\}", query_result)
                for json_str in json_matches:
                    try:
                        server_data = json.loads(json_str)
                        if (
                            isinstance(server_data, dict)
                            and "server_name" in server_data
                        ):
                            servers.append(server_data)
                    except json.JSONDecodeError:
                        continue

            # If still no JSON found, try to parse from markdown-style text like:
            # - **Server Name:** arc_supabase
            # - **Display Name:** ARC supabase
            if not servers:
                self.logger.info("🔍 Trying to parse markdown-style server data...")

                # Look for markdown-style server data blocks
                markdown_pattern = r"-\s*\*\*Server Name:\*\*\s*(\w+)"
                server_name_matches = re.findall(
                    markdown_pattern, query_result, re.IGNORECASE
                )

                for server_name in server_name_matches:
                    server_block = {}
                    server_block["server_name"] = server_name

                    # Extract other fields using patterns
                    patterns = {
                        "display_name": r"-\s*\*\*Display Name:\*\*\s*([^\n]+)",
                        "description": r"-\s*\*\*Description:\*\*\s*([^\n]+)",
                        "transport": r"-\s*\*\*Transport:\*\*\s*([^\n]+)",
                        "url": r"-\s*\*\*URL:\*\*\s*([^\n]+)",
                        "command": r"-\s*\*\*Command:\*\*\s*([^\n]+)",
                    }

                    for field, pattern in patterns.items():
                        match = re.search(pattern, query_result, re.IGNORECASE)
                        if match:
                            value = match.group(1).strip()
                            if value and value.lower() not in ["null", "none", ""]:
                                server_block[field] = value

                    # Set default args
                    server_block["args"] = []

                    if server_block.get("server_name"):
                        servers.append(server_block)
                        self.logger.info(
                            f"✅ Parsed server from markdown: {server_block['server_name']}"
                        )

            # If still no structured data, try old key:value parsing
            if not servers:
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
                        key = (
                            key.strip()
                            .lower()
                            .replace("*", "")
                            .replace("-", "")
                            .strip()
                        )
                        value = value.strip().strip("\"'")

                        if key in [
                            "server_name",
                            "server name",
                            "display_name",
                            "display name",
                            "description",
                            "transport",
                            "url",
                            "command",
                        ]:
                            normalized_key = key.replace(" ", "_")
                            current_server[normalized_key] = value
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
            import traceback

            self.logger.warning(f"Full parsing error: {traceback.format_exc()}")

        # Ensure each server has required fields with defaults
        for server in servers:
            server.setdefault(
                "description",
                f"Specialized MCP server: {server.get('server_name', 'unknown')}",
            )
            server.setdefault("transport", "stdio")
            server.setdefault("args", [])

        self.logger.info(
            f"🎯 Successfully parsed {len(servers)} servers from query result"
        )
        for server in servers:
            self.logger.info(
                f"   - {server['server_name']}: {server.get('transport', 'stdio')} @ {server.get('url', 'no-url')}"
            )

        return servers

    # ======= END OF ADDITIONAL MISSING METHODS =======

    # Add the missing methods from lines 3093-4092 of main.py
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

    async def add_mcp_server(self, user_id: str, server_config: Dict) -> str:
        """Allow users to add new MCP servers through chat"""
        try:
            # Convert server_config to the format expected by our workflow
            server_info = {
                "server_name": server_config.get("name")
                or server_config.get("server_name"),
                "display_name": server_config.get("display_name")
                or server_config.get("name", "Unknown Server"),
                "description": server_config.get(
                    "description", "User-added MCP server"
                ),
                "transport": server_config.get("transport", "sse"),
                "url": server_config.get("url"),
                "command": server_config.get("command"),
                "args": server_config.get("args", []),
            }

            # Add the server to the database using our new system
            result = await self._add_server_to_database(server_info)

            if result["success"]:
                return f"✅ Successfully added MCP server '{server_info['server_name']}' to the database!"
            else:
                return f"❌ Failed to add MCP server: {result.get('error', 'Unknown error')}"

        except Exception as e:
            self.logger.error(f"Error in add_mcp_server: {e}")
            return f"❌ Error adding MCP server: {str(e)}"

    async def _send_slack_response(
        self, channel_id: str, result: str, analysis: Dict = None, thread_ts: str = None
    ):
        """Send formatted response to Slack as shown in sequence diagram"""
        if not hasattr(self, "slack_manager") or not self.slack_manager.slack_client:
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

*Processed with {analysis.get("execution_strategy", "standard") if analysis else "standard"} strategy using {len(analysis.get("required_agents", [])) if analysis else 0} specialized agents.*
"""

            self.slack_manager.slack_client.chat_postMessage(
                channel=channel_id,
                text=formatted_response,
                parse="mrkdwn",
                thread_ts=thread_ts,
            )

        except Exception as e:
            self.logger.error(f"Error sending Slack response: {e}")

    def _extract_dashboard_url(self, result: str) -> str:
        """Extract dashboard URL from result"""
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
                "message": "trigger workflow to update airtable",
                "expected_agent": "automation_specialist",
                "description": "workflow automation via airtable server",
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

    async def _perform_capability_introspection(
        self, specific_tool_search: str = None
    ) -> str:
        """Perform comprehensive capability introspection across all agent types"""
        try:
            self.logger.info("🔍 Starting comprehensive capability introspection...")

            capabilities_info = {
                "specialized_agents": {},
                "mcp_servers": {},
                "total_tools": 0,
                "system_overview": {},
                "tool_search_results": [],
            }

            # If specific tool search is requested, search for tools across all servers
            if specific_tool_search:
                self.logger.info(
                    f"🔍 Searching for specific tool: {specific_tool_search}"
                )
                tool_keywords = [
                    specific_tool_search,
                    specific_tool_search.replace("-", "_"),
                    specific_tool_search.replace("_", "-"),
                ]

                try:
                    found_tools = await self.db_ops.find_tools_across_servers(
                        tool_keywords
                    )
                    capabilities_info["tool_search_results"] = found_tools
                    self.logger.info(f"✅ Found {len(found_tools)} matching tools")
                except Exception as e:
                    self.logger.warning(f"⚠️ Tool search failed: {e}")
                    capabilities_info["tool_search_results"] = []

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
                        elif "error" in server_info:
                            response += f"  - {server_name}: (connection issue)\n"
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

            # Add tool search results if any were found
            if capabilities_info["tool_search_results"]:
                response += f"""**🔍 Tool Search Results for "{specific_tool_search}":**

"""
                for tool_result in capabilities_info["tool_search_results"]:
                    response += f"**Found: `{tool_result['tool_name']}`**\n"
                    response += f"• **Server:** {tool_result['server_name']}\n"
                    response += (
                        f"• **Description:** {tool_result['tool_description']}\n"
                    )
                    response += f"• **Match Type:** {tool_result['match_type']}\n"

                    # Show some parameter info if available
                    if tool_result.get("tool_schema") and tool_result[
                        "tool_schema"
                    ].get("properties"):
                        params = list(tool_result["tool_schema"]["properties"].keys())[
                            :3
                        ]
                        response += f"• **Parameters:** {', '.join(params)}"
                        if len(tool_result["tool_schema"]["properties"]) > 3:
                            response += f" + {len(tool_result['tool_schema']['properties']) - 3} more"
                        response += "\n"

                    response += "\n"

                response += f"""**💡 How to use this tool:**
To use the `{specific_tool_search}` tool, you can:
1. Ask me to perform a task that requires this specific tool
2. I'll automatically route your request to the appropriate agent
3. The agent will use the `{tool_result["server_name"]}` server to access the tool

Example: `@meta-agent use the {specific_tool_search} tool to [describe what you want to do]`

"""
            elif specific_tool_search:
                response += f"""**🔍 Tool Search Results for "{specific_tool_search}":**

❌ **No matching tools found** in the database.

This could mean:
1. The tool name might be slightly different (try variations like `{specific_tool_search.replace("-", "_")}` or `{specific_tool_search.replace("_", "-")}`)
2. The tool might be on a server that isn't currently configured
3. The tool might need to be added to the database

Try asking me to:
• List all available tools: `@meta-agent what tools do you have access to?`
• Search for similar tools: `@meta-agent find tools related to [keyword]`
• Add a new MCP server: `@meta-agent I want to add a new MCP server`

"""

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

    # ======= COMPLETE MCP SERVER ADDITION WORKFLOW METHODS =======

    async def _try_fallback_servers(
        self, primary_config: Dict, agent_name: str
    ) -> Optional[Agent]:
        """Try fallback servers if dynamic server creation fails"""
        try:
            if hasattr(self.mcp_app.context, "server_registry"):
                available_servers = list(
                    self.mcp_app.context.server_registry.registry.keys()
                )
                self.logger.info(f"🔍 Available servers in MCPApp: {available_servers}")

                # Try using an existing registered server that might work
                potential_servers = ["supabase", "arc_supabase"]
                for potential_server in potential_servers:
                    if potential_server in available_servers:
                        self.logger.info(
                            f"🔄 Fallback: Trying with existing server '{potential_server}'"
                        )

                        fallback_agent = Agent(
                            name=f"fallback_{agent_name}_{int(datetime.now().timestamp())}",
                            instruction=f"""You are an agent with access to the '{potential_server}' MCP server.
                            
                            The user asked about ARC Supabase records. Be PROACTIVE:
                            1. FIRST: Use list_tables to show what tables are available
                            2. THEN: Use execute_sql or get_all_rows to show sample data from relevant tables
                            3. FOCUS: Show actual table names, row counts, and sample records
                            4. AVOID: Asking for clarification - explore the database first!
                            
                            For time-based queries, look for tables with date/timestamp columns.
                            Show the user what data exists rather than asking what they want.
                            
                            Original request context: {primary_config.get("description", "ARC Supabase data query")}
                            """,
                            server_names=[potential_server],
                            context=self.mcp_app.context,
                            human_input_callback=self.slack_human_input_callback,
                        )

                        try:
                            await fallback_agent.__aenter__()
                            self.logger.info(
                                f"✅ Fallback agent created with '{potential_server}' server"
                            )
                            return fallback_agent
                        except Exception as fallback_error:
                            self.logger.warning(
                                f"⚠️ Fallback with '{potential_server}' also failed: {fallback_error}"
                            )
                            continue

            return None
        except Exception as e:
            self.logger.warning(f"Fallback server creation failed: {e}")
            return None

    async def _gather_authentication_details(
        self, server_info: Dict[str, Any], user_id: str
    ):
        """Gather authentication details for the server"""
        try:
            auth_type_response = await self._ask_user_for_info(
                """What type of authentication does this server use?

**Common options:**
• **api_key** - API Key in headers
• **bearer_token** - Bearer token in headers
• **basic_auth** - Username/password
• **custom** - Custom authentication

**Please specify the type:**""",
                user_id,
            )

            if not auth_type_response:
                return

            auth_type = auth_type_response.strip().lower()

            if auth_type in ["api_key", "apikey", "api-key"]:
                api_key = await self._ask_user_for_info(
                    "🔐 **Please provide your API key:**\n\n⚠️ This will be stored securely in Supabase secrets manager, not in plain text.",
                    user_id,
                )
                if api_key:
                    server_info["api_key"] = api_key.strip()

            elif auth_type in ["bearer_token", "bearer", "token"]:
                token = await self._ask_user_for_info(
                    "🔐 **Please provide your bearer token:**\n\n⚠️ This will be stored securely in Supabase secrets manager, not in plain text.",
                    user_id,
                )
                if token:
                    server_info["bearer_token"] = token.strip()

            elif auth_type in ["basic_auth", "basic"]:
                username = await self._ask_user_for_info(
                    "What's the username for basic auth?",
                    user_id,
                )
                password = await self._ask_user_for_info(
                    "🔐 **What's the password for basic auth?**\n\n⚠️ This will be stored securely in Supabase secrets manager, not in plain text.",
                    user_id,
                )
                if username and password:
                    server_info["auth_username"] = username.strip()
                    server_info["auth_password"] = password.strip()

            elif auth_type == "custom":
                # Allow custom field names
                field_name = await self._ask_user_for_info(
                    "What should we call this credential field? (e.g., 'webhook_secret', 'signing_key', etc.)",
                    user_id,
                )
                if field_name:
                    credential_value = await self._ask_user_for_info(
                        f"🔐 **Please provide the value for {field_name}:**\n\n⚠️ This will be stored securely in Supabase secrets manager, not in plain text.",
                        user_id,
                    )
                    if credential_value:
                        server_info[field_name.strip()] = credential_value.strip()

        except Exception as e:
            self.logger.error(f"Error gathering authentication details: {e}")

    async def _gather_environment_variables(
        self, server_info: Dict[str, Any], user_id: str
    ):
        """Gather environment variables for stdio servers"""
        try:
            while True:
                env_name = await self._ask_user_for_info(
                    "What's the name of the environment variable? (e.g., 'API_KEY', 'DATABASE_URL')\n\n**Type 'done' when finished adding variables:**",
                    user_id,
                )

                if not env_name or env_name.lower() in ["done", "finished", "exit"]:
                    break

                env_value = await self._ask_user_for_info(
                    f"🔐 **What's the value for {env_name}?**\n\n⚠️ If this is sensitive (API key, password, etc.), it will be stored securely in Supabase secrets manager.",
                    user_id,
                )

                if env_value:
                    # Store with env_ prefix to distinguish from direct server fields
                    server_info[f"env_{env_name.strip()}"] = env_value.strip()

        except Exception as e:
            self.logger.error(f"Error gathering environment variables: {e}")

    def _mask_sensitive_data(self, server_info: Dict[str, Any]) -> Dict[str, Any]:
        """Create a masked version of server info for display purposes"""
        masked_info = server_info.copy()

        for field_name, field_value in server_info.items():
            if (
                isinstance(field_value, str)
                and self.supabase_direct_client
                and self.supabase_direct_client._is_secret_field(
                    field_name, field_value
                )
            ):
                # Mask sensitive values for display
                if len(field_value) > 8:
                    masked_info[field_name] = (
                        field_value[:4]
                        + "*" * (len(field_value) - 8)
                        + field_value[-4:]
                    )
                else:
                    masked_info[field_name] = "*" * len(field_value)

        return masked_info

    async def _ask_user_for_info(self, prompt: str, user_id: str) -> Optional[str]:
        """Ask the user for information using the Slack human input system"""
        try:
            from mcp_agent.human_input.types import HumanInputRequest

            request = HumanInputRequest(
                request_id=f"mcp_server_input_{int(datetime.now().timestamp())}",
                prompt=f"🔧 **MCP Server Setup**\n\n{prompt}",
                description="Setting up a new MCP server",
            )

            response = await self.slack_human_input_callback(request)
            return response.response if response else None

        except Exception as e:
            self.logger.error(f"Error asking user for info: {e}")
            return None

    async def _add_server_to_database(
        self, server_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Add the MCP server configuration to the Supabase database with secure secrets handling"""
        try:
            # 🔐 Use secure insertion method if direct client is available
            if self.supabase_direct_client:
                self.logger.info(
                    "🔐 Using secure MCP server insertion with secrets management"
                )

                # Use the new secure insertion method
                result = await self.supabase_direct_client.insert_mcp_server_secure(
                    server_info
                )

                if result["success"]:
                    secret_count = len(result.get("secret_references", {}))

                    # Verify the server was added
                    verify_result = (
                        await self.supabase_direct_client.verify_server_exists(
                            server_info["server_name"]
                        )
                    )

                    if verify_result["success"] and verify_result["exists"]:
                        return {
                            "success": True,
                            "server_id": result.get("server_id", "unknown"),
                            "message": f"✅ Server '{server_info['server_name']}' added successfully with {secret_count} secrets secured",
                            "secret_references": result.get("secret_references", {}),
                            "details": verify_result["data"],
                        }
                    else:
                        return {
                            "success": False,
                            "error": f"Server added but verification failed: {verify_result.get('error', 'Unknown error')}",
                            "details": result,
                        }
                else:
                    self.logger.warning(
                        f"⚠️ Secure insertion failed: {result['error']}, falling back to standard method"
                    )
                    # Fall through to standard MCP method

            # Fallback to MCP method if direct client is unavailable or failed
            self.logger.info("🔄 Falling back to MCP-based server addition")

            # Create agent for database operations
            db_agent = Agent(
                name="mcp_server_db_manager",
                instruction="Execute SQL operations for MCP server management",
                server_names=["supabase"],
                context=self.mcp_app.context if self.mcp_app else None,
            )

            async with db_agent:
                llm = await db_agent.attach_llm(OpenAIAugmentedLLM)

                # Step 1: Ensure configuration exists
                config_prompt = f"""Execute this SQL to ensure the configuration exists:

                Project ID: {self.supabase_project_id}

                INSERT INTO mcp_configurations (name, description, is_active, created_at, updated_at)
                VALUES ('user_added_servers', 'User-added MCP servers from Slack', true, NOW(), NOW())
                ON CONFLICT (name) DO UPDATE SET updated_at = NOW()
                RETURNING id;

                Use the execute_sql tool to run this query."""

                config_result = await llm.generate_str(config_prompt)
                self.logger.info(f"✅ Configuration result: {config_result}")

                # Step 2: Prepare server data (warn about potential security issues)
                server_name = server_info["server_name"].replace("'", "''")
                display_name = server_info.get("display_name", server_name).replace(
                    "'", "''"
                )
                description = server_info.get("description", "").replace("'", "''")
                transport = server_info["transport"]
                url = (
                    server_info.get("url", "").replace("'", "''")
                    if server_info.get("url")
                    else None
                )
                command = (
                    server_info.get("command", "").replace("'", "''")
                    if server_info.get("command")
                    else None
                )

                # ⚠️ WARNING: In fallback mode, secrets will be stored in plain text
                # This should only happen if direct client fails
                sensitive_fields = []
                for field_name, field_value in server_info.items():
                    if (
                        field_name
                        not in [
                            "server_name",
                            "display_name",
                            "description",
                            "transport",
                            "url",
                            "command",
                            "args",
                        ]
                        and isinstance(field_value, str)
                        and len(field_value) > 0
                    ):
                        sensitive_fields.append(field_name)

                if sensitive_fields:
                    self.logger.warning(
                        f"⚠️ SECURITY WARNING: Storing {len(sensitive_fields)} potentially sensitive fields in plain text: {sensitive_fields}"
                    )

                args = json.dumps(server_info.get("args", [])).replace("'", "''")

                server_prompt = f"""Execute this SQL to add the MCP server:

                Project ID: {self.supabase_project_id}

                INSERT INTO mcp_servers (
                    configuration_id,
                    server_name,
                    display_name,
                    description,
                    transport,
                    url,
                    command,
                    args,
                    is_enabled,
                    priority,
                    created_at,
                    updated_at
                )
                VALUES (
                    (SELECT id FROM mcp_configurations WHERE name = 'user_added_servers' LIMIT 1),
                    '{server_name}',
                    '{display_name}',
                    '{description}',
                    '{transport}',
                    {f"'{url}'" if url else "NULL"},
                    {f"'{command}'" if command else "NULL"},
                    '{args}'::jsonb,
                    true,
                    100,
                    NOW(),
                    NOW()
                )
                RETURNING id, server_name;

                Use the execute_sql tool to run this query."""

                server_result = await llm.generate_str(server_prompt)
                self.logger.info(f"✅ Server addition result: {server_result}")

                # Step 3: Verify the server was added
                verify_prompt = f"""Execute this SQL to verify the server was added:

                Project ID: {self.supabase_project_id}

                SELECT id, server_name, display_name, transport, url, command
                FROM mcp_servers 
                WHERE server_name = '{server_name}'
                ORDER BY created_at DESC 
                LIMIT 1;

                Use the execute_sql tool to run this query."""

                verify_result = await llm.generate_str(verify_prompt)
                self.logger.info(f"🔍 Verification result: {verify_result}")

                # Check if the operation was successful
                if (
                    isinstance(verify_result, str)
                    and server_name.lower() in verify_result.lower()
                ):
                    warning_message = ""
                    if sensitive_fields:
                        warning_message = f" ⚠️ WARNING: {len(sensitive_fields)} sensitive field(s) stored in plain text"

                    return {
                        "success": True,
                        "server_id": "added_successfully",
                        "message": f"✅ Server '{server_name}' added to database successfully via MCP{warning_message}",
                        "details": verify_result,
                        "security_warning": bool(sensitive_fields),
                    }
                else:
                    return {
                        "success": False,
                        "error": f"Could not verify server '{server_name}' was added to database",
                        "details": str(verify_result),
                    }

        except Exception as e:
            self.logger.error(f"❌ Database operation error: {e}")
            import traceback

            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            return {"success": False, "error": str(e)}

    # ======= END OF COMPLETE MCP SERVER ADDITION WORKFLOW =======

    def _score_all_server_matches(
        self, keywords: List[str], servers: List[str], source: str
    ) -> List[Dict[str, Any]]:
        """Score all servers and return all matches above threshold (simplified for fallback)"""
        if not servers or not keywords:
            return []

        matches = []
        keywords_lower = [k.lower() for k in keywords]

        for server in servers:
            server_lower = server.lower()
            score = 0.0

            # Exact keyword match
            for keyword in keywords_lower:
                if keyword == server_lower:
                    score += 1.0
                elif keyword in server_lower:
                    score += 0.8
                elif server_lower in keyword:
                    score += 0.6

            # Compound keyword matching (e.g., "weather_api" vs ["weather", "api"])
            if len(keywords_lower) > 1:
                compound_match = all(k in server_lower for k in keywords_lower)
                if compound_match:
                    score += 1.5  # Bonus for compound matches

            # Specificity bonus (longer, more specific server names get slight bonus)
            if "_" in server_lower and len(keywords_lower) > 1:
                score += 0.2

            # Only include matches above minimum threshold
            if score >= 0.5:
                matches.append(
                    {
                        "source": source,
                        "server_name": server,
                        "score": score,
                        "keywords_matched": keywords_lower,
                        "server_data": None,  # Will be filled for database servers
                    }
                )

        return matches

    def _extract_tool_name_from_message(self, message: str) -> Optional[str]:
        """Extract tool name from user message when asking about specific tools"""
        import re

        message_lower = message.lower()

        # Common patterns for tool requests
        tool_patterns = [
            r"use the ([a-zA-Z0-9_-]+) tool",
            r"find the ([a-zA-Z0-9_-]+) tool",
            r"what.*([a-zA-Z0-9_-]+) tool",
            r"([a-zA-Z0-9_-]+) tool",
            r"ghl[_-]?dynamic",  # Specific pattern for ghl-dynamic
            r"([a-zA-Z0-9]+)[_-]([a-zA-Z0-9]+)",  # General compound tool names
        ]

        for pattern in tool_patterns:
            matches = re.findall(pattern, message_lower)
            if matches:
                if isinstance(matches[0], tuple):
                    # For compound patterns like ghl-dynamic
                    tool_name = "_".join(matches[0])
                else:
                    tool_name = matches[0]

                # Clean up the tool name
                tool_name = tool_name.strip()

                # Skip common words that aren't tool names
                skip_words = {
                    "the",
                    "this",
                    "that",
                    "use",
                    "find",
                    "what",
                    "can",
                    "you",
                    "have",
                    "access",
                    "to",
                }
                if tool_name not in skip_words and len(tool_name) > 2:
                    self.logger.info(f"🎯 Extracted tool name: {tool_name}")
                    return tool_name

        return None
