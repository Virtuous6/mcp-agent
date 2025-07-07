"""
Main entry point for the modular SlackMetaAgent system.

This module wires together all the micro-agents and provides a clean
interface that can replace the monolithic SlackMetaAgent.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

try:
    from mcp_agent.app import MCPApp
except ImportError:
    # Handle case where MCPApp is not available due to circular imports
    MCPApp = None

from .core.orchestrator import Orchestrator
from .agents.intent_analyzer import IntentAnalyzerAgent
from .agents.registry import AgentRegistryAgent
from .agents.tool_discovery import ToolDiscoveryAgent
from .agents.pool_manager import PoolManagerAgent
from .agents.workflow_manager import WorkflowManagerAgent
from .agents.slack_adapter import SlackAdapterAgent

# Import database components from original system
from .database.supabase_operations import SupabaseOperations
from .database.supabase_pool_manager import SupabasePoolManager
from .slack.slack_client import SlackClientManager
from .utils.config_loader import ConfigLoader


class ModularSlackMetaAgent:
    """
    Modular SlackMetaAgent that replaces the monolithic version.

    This system uses the micro-agent architecture with clean separation
    of concerns and dependency injection.
    """

    def __init__(self, supabase_project_id: str = None, mcp_app: MCPApp = None):
        self.logger = logging.getLogger("ModularSlackMetaAgent")
        self.supabase_project_id = supabase_project_id
        self.mcp_app = mcp_app

        # Core components (will be initialized)
        self.config = ConfigLoader()
        self.slack_manager = SlackClientManager()

        # Database components (will be re-initialized with credentials later)
        self.db_ops = None
        self.pool_manager: Optional[SupabasePoolManager] = None

        # Micro-agents
        self.intent_analyzer: Optional[IntentAnalyzerAgent] = None
        self.registry: Optional[AgentRegistryAgent] = None
        self.tool_discovery: Optional[ToolDiscoveryAgent] = None
        self.pool_manager_agent: Optional[PoolManagerAgent] = None
        self.workflow_manager: Optional[WorkflowManagerAgent] = None
        self.slack_adapter: Optional[SlackAdapterAgent] = None
        self.orchestrator: Optional[Orchestrator] = None

        # Initialize database pool manager
        self._initialize_database_pool()

    def _initialize_database_pool(self):
        """Initialize high-performance database pool manager."""
        try:
            # Load credentials from secrets file or environment
            anon_key = None
            service_role_key = None
            supabase_url = None
            supabase_access_token = None

            # Try to load from secrets YAML file
            secrets_paths = [
                "config/mcp_agent.secrets.yaml",
                "mcp_agent.secrets.yaml",
                "slack_meta_agent/config/mcp_agent.secrets.yaml",
            ]

            for secrets_file in secrets_paths:
                if os.path.exists(secrets_file):
                    import yaml

                    with open(secrets_file, "r") as f:
                        secrets = yaml.safe_load(f)

                        # First try TRIBEsupabase section (our current setup)
                        if "TRIBEsupabase" in secrets:
                            tribe_config = secrets["TRIBEsupabase"]
                            anon_key = tribe_config.get("anon_key")
                            service_role_key = tribe_config.get("service_role_key")
                            supabase_url = tribe_config.get("url")
                            self.logger.info(
                                f"📄 Loaded Supabase credentials from TRIBEsupabase section in {secrets_file}"
                            )

                        # Also check for MCP server environment variables
                        if "mcp" in secrets and "servers" in secrets["mcp"]:
                            mcp_servers = secrets["mcp"]["servers"]

                            # Set up Supabase MCP server environment variables
                            if "TRIBEsupabase" in mcp_servers:
                                supabase_env = mcp_servers["TRIBEsupabase"].get(
                                    "env", {}
                                )
                                if "SUPABASE_ACCESS_TOKEN" in supabase_env:
                                    supabase_access_token = supabase_env[
                                        "SUPABASE_ACCESS_TOKEN"
                                    ]
                                    os.environ["SUPABASE_ACCESS_TOKEN"] = (
                                        supabase_access_token
                                    )
                                    self.logger.info(
                                        "🔑 Set SUPABASE_ACCESS_TOKEN for MCP server"
                                    )
                                if "SUPABASE_PROJECT_ID" in supabase_env:
                                    os.environ["SUPABASE_PROJECT_ID"] = supabase_env[
                                        "SUPABASE_PROJECT_ID"
                                    ]
                                    self.logger.info(
                                        "🆔 Set SUPABASE_PROJECT_ID for MCP server"
                                    )

                            # Set up Brave Search MCP server environment variables
                            if "brave_search" in mcp_servers:
                                brave_env = mcp_servers["brave_search"].get("env", {})
                                if "BRAVE_API_KEY" in brave_env:
                                    os.environ["BRAVE_API_KEY"] = brave_env[
                                        "BRAVE_API_KEY"
                                    ]
                                    self.logger.info(
                                        "🔍 Set BRAVE_API_KEY for MCP server"
                                    )

                            # Set up any other MCP server environment variables
                            for server_name, server_config in mcp_servers.items():
                                if "env" in server_config:
                                    for env_var, env_val in server_config[
                                        "env"
                                    ].items():
                                        if (
                                            env_var not in os.environ
                                        ):  # Don't override existing env vars
                                            os.environ[env_var] = env_val
                                            self.logger.info(
                                                f"🔧 Set {env_var} for {server_name} MCP server"
                                            )

                        # Fallback to legacy supabase section
                        if (
                            not anon_key
                            and not service_role_key
                            and "supabase" in secrets
                        ):
                            anon_key = secrets["supabase"].get("anon_key")
                            service_role_key = secrets["supabase"].get(
                                "service_role_key"
                            )
                            supabase_url = secrets["supabase"].get("url")
                            self.logger.info(
                                f"📄 Loaded Supabase credentials from supabase section in {secrets_file}"
                            )

                        if anon_key or service_role_key:
                            break

            # Fallback to environment variables
            if not anon_key and not service_role_key:
                anon_key = os.getenv("SUPABASE_ANON_KEY")
                service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
                supabase_url = os.getenv("SUPABASE_URL")

            # Extract project ID from URL if not provided
            if not self.supabase_project_id and supabase_url:
                import re

                match = re.search(r"https://([^.]+)\.supabase\.co", supabase_url)
                if match:
                    self.supabase_project_id = match.group(1)
                    self.logger.info(
                        f"📍 Extracted project ID: {self.supabase_project_id}"
                    )

            if (anon_key or service_role_key) and self.supabase_project_id:
                # Update the database operations with credentials
                self.db_ops = SupabaseOperations(
                    supabase_project_id=self.supabase_project_id,
                    anon_key=anon_key,
                    service_role_key=service_role_key,
                )

                self.pool_manager = SupabasePoolManager(
                    project_id=self.supabase_project_id,
                    anon_key=anon_key,
                    service_role_key=service_role_key,
                    max_connections=8,
                    default_cache_ttl=300,
                    enable_metrics=True,
                )
                self.logger.info(
                    "⚡ High-performance Supabase pool manager initialized with credentials"
                )
            else:
                self.logger.warning("⚠️ Missing Supabase credentials or project ID")
                # Create a fallback db_ops without credentials
                if self.supabase_project_id:
                    self.db_ops = SupabaseOperations(
                        supabase_project_id=self.supabase_project_id
                    )

        except Exception as e:
            self.logger.warning(f"⚠️ Could not initialize database pool: {e}")
            # Ensure we have a db_ops even if pool fails
            if not self.db_ops and self.supabase_project_id:
                self.db_ops = SupabaseOperations(
                    supabase_project_id=self.supabase_project_id
                )

    async def initialize(self) -> bool:
        """Initialize all micro-agents and wire them together."""
        try:
            self.logger.info("🚀 Initializing modular SlackMetaAgent system...")

            # Initialize database pool manager
            if self.pool_manager:
                await self.pool_manager.initialize()

            # Initialize micro-agents in dependency order
            await self._initialize_micro_agents()

            # Wire dependencies between components
            await self._wire_dependencies()

            self.logger.info(
                "✅ Modular SlackMetaAgent system initialized successfully"
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize modular system: {e}")
            return False

    async def _initialize_micro_agents(self):
        """Initialize all micro-agents."""

        # 1. Agent Registry (no dependencies)
        self.registry = AgentRegistryAgent(
            pool_manager=self.pool_manager, db_ops=self.db_ops, config=self.config
        )
        await self.registry.initialize()

        # 2. Tool Discovery (depends on mcp_app)
        self.tool_discovery = ToolDiscoveryAgent(mcp_app=self.mcp_app, cache_ttl=300)
        await self.tool_discovery.initialize()

        # 3. Pool Manager Agent (depends on registry, mcp_app)
        self.pool_manager_agent = PoolManagerAgent(
            registry=self.registry,
            mcp_app=self.mcp_app,
            pool_size=5,
            health_check_interval=300,
        )
        await self.pool_manager_agent.initialize()

        # 4. Intent Analyzer (depends on registry, tool_discovery, pool_manager)
        self.intent_analyzer = IntentAnalyzerAgent(
            registry=self.registry,
            tool_discovery=self.tool_discovery,
            pool_manager=self.pool_manager_agent,
            config=self.config,
        )
        await self.intent_analyzer.initialize()

        # 5. Workflow Manager (depends on registry, tool_discovery, pool_manager)
        self.workflow_manager = WorkflowManagerAgent(
            pool_manager=self.pool_manager_agent,
            db_ops=self.db_ops,
            agent_registry=await self.registry.get_agent_specs(),
            mcp_app=self.mcp_app,
        )

        # 6. Slack Adapter (no dependencies initially)
        self.slack_adapter = SlackAdapterAgent(slack_manager=self.slack_manager)

        # 7. Orchestrator (depends on all other agents)
        self.orchestrator = Orchestrator(
            intent_analyzer=self.intent_analyzer,
            registry=self.registry,
            tool_discovery=self.tool_discovery,
            pool_manager=self.pool_manager_agent,
            workflow_manager=self.workflow_manager,
            mcp_app=self.mcp_app,
        )
        await self.orchestrator.initialize()

    async def _wire_dependencies(self):
        """Wire dependencies between components."""

        # Inject orchestrator into slack adapter
        self.slack_adapter.set_orchestrator(self.orchestrator)

        # Inject workflow manager as workflow checker into intent analyzer
        self.intent_analyzer.set_workflow_checker(self.workflow_manager)

        # Set human input callback for pool manager
        if hasattr(self.pool_manager_agent, "set_human_input_callback"):
            self.pool_manager_agent.set_human_input_callback(
                self.slack_adapter.slack_human_input_callback
            )

    async def initialize_slack(self, bot_token: str, app_token: str) -> bool:
        """Initialize Slack integration."""
        if not self.slack_adapter:
            self.logger.error("Slack adapter not initialized")
            return False

        return await self.slack_adapter.initialize(bot_token, app_token)

    async def start_slack_connection(self):
        """Start the Slack connection."""
        if self.slack_adapter:
            await self.slack_adapter.start_connection()

    async def handle_message(
        self, message: str, user_id: str = "test_user", channel_id: str = "test_channel"
    ) -> str:
        """
        Handle a message directly (useful for testing or non-Slack interfaces).

        This uses the new flow: Intent Analysis → Orchestration
        """
        if not self.intent_analyzer or not self.orchestrator:
            return "❌ System not properly initialized"

        try:
            from .core.types import IncomingMessage, MessageContext

            # Create normalized message
            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id,
                message_ts=str(int(datetime.now().timestamp())),
                thread_ts=None,
                timestamp=str(int(datetime.now().timestamp())),
                platform="direct",
            )

            incoming = IncomingMessage(text=message, context=context, raw_event={})

            # Step 1: Analyze intent first
            self.logger.info(f"🧠 Analyzing intent for: {message[:50]}...")
            intent = await self.intent_analyzer.analyze(incoming)

            self.logger.info(
                f"✅ Intent classified: {intent.name} (confidence: {intent.confidence.value})"
            )

            # Step 2: Orchestrate execution with intent context
            self.logger.info(f"🎭 Orchestrating execution with intent: {intent.name}")
            result = await self.orchestrator.execute_with_intent(incoming, intent)

            return result.response

        except Exception as e:
            self.logger.error(f"Error handling direct message: {e}")
            return f"❌ Error processing message: {str(e)}"

    async def health_check(self) -> dict:
        """Get comprehensive system health check."""
        health = {
            "system": "ModularSlackMetaAgent",
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "components": {},
        }

        # Check each component
        components = [
            ("intent_analyzer", self.intent_analyzer),
            ("registry", self.registry),
            ("tool_discovery", self.tool_discovery),
            ("pool_manager_agent", self.pool_manager_agent),
            ("workflow_manager", self.workflow_manager),
            ("slack_adapter", self.slack_adapter),
            ("orchestrator", self.orchestrator),
        ]

        for name, component in components:
            if component:
                try:
                    health["components"][name] = await component.health_check()
                except Exception as e:
                    health["components"][name] = {"status": "error", "error": str(e)}
                    health["status"] = "degraded"
            else:
                health["components"][name] = {"status": "not_initialized"}
                health["status"] = "degraded"

        # Check database connectivity
        if self.pool_manager:
            try:
                db_health = self.pool_manager.get_performance_metrics()
                health["database"] = db_health
            except Exception as e:
                health["database"] = {"status": "error", "error": str(e)}
                health["status"] = "degraded"

        return health

    async def get_performance_metrics(self) -> dict:
        """Get comprehensive performance metrics."""
        metrics = {
            "timestamp": datetime.now().isoformat(),
            "database": {},
            "orchestrator": {},
            "agents": {},
        }

        # Database metrics
        if self.pool_manager:
            try:
                metrics["database"] = await self.pool_manager.get_performance_metrics()
            except Exception as e:
                metrics["database"] = {"error": str(e)}

        # Orchestrator metrics
        if self.orchestrator:
            try:
                orchestrator_health = await self.orchestrator.health_check()
                metrics["orchestrator"] = orchestrator_health.get(
                    "execution_metrics", {}
                )
            except Exception as e:
                metrics["orchestrator"] = {"error": str(e)}

        # Individual agent metrics
        for name, component in [
            ("pool_manager", self.pool_manager_agent),
            ("tool_discovery", self.tool_discovery),
            ("intent_analyzer", self.intent_analyzer),
        ]:
            if component:
                try:
                    component_health = await component.health_check()
                    metrics["agents"][name] = component_health
                except Exception as e:
                    metrics["agents"][name] = {"error": str(e)}

        return metrics

    async def cleanup(self):
        """Clean up all resources."""
        self.logger.info("🧹 Cleaning up modular SlackMetaAgent system...")

        # Cleanup components in reverse order
        components = [
            self.slack_adapter,
            self.orchestrator,
            self.workflow_manager,
            self.pool_manager_agent,
            self.tool_discovery,
            self.intent_analyzer,
            self.registry,
        ]

        for component in components:
            if component:
                try:
                    await component.cleanup()
                except Exception as e:
                    self.logger.warning(f"Component cleanup warning: {e}")

        # Cleanup database pool
        if self.pool_manager:
            try:
                await self.pool_manager.cleanup()
            except Exception as e:
                self.logger.warning(f"Database pool cleanup warning: {e}")

        self.logger.info("✅ Modular SlackMetaAgent cleanup complete")


# Factory function for easy instantiation
async def create_modular_slack_meta_agent(
    supabase_project_id: str = None, mcp_app: MCPApp = None, config_path: str = None
) -> ModularSlackMetaAgent:
    """
    Factory function to create and initialize a ModularSlackMetaAgent.

    Args:
        supabase_project_id: Supabase project ID for database operations
        mcp_app: MCPApp instance with configured servers
        config_path: Path to MCP configuration file

    Returns:
        Initialized ModularSlackMetaAgent instance
    """
    # If no MCPApp is provided, create one from config
    if mcp_app is None and MCPApp is not None:
        # Try to find config file
        config_file = config_path or "config/mcp_agent.config.yaml"
        if not os.path.exists(config_file):
            config_file = "mcp_agent.config.yaml"
        if not os.path.exists(config_file):
            config_file = "slack_meta_agent/config/mcp_agent.config.yaml"

        if os.path.exists(config_file):
            try:
                # Create MCPApp from config
                import logging

                logger = logging.getLogger("ModularSlackMetaAgent.Factory")
                logger.info(f"🔧 Creating MCPApp from config: {config_file}")

                mcp_app = MCPApp(
                    name="slack_meta_agent_modular",
                    settings=config_file,  # Pass config file path as settings
                )
                await mcp_app.initialize()
                logger.info("✅ MCPApp created and initialized successfully")
            except Exception as e:
                import logging

                logger = logging.getLogger("ModularSlackMetaAgent.Factory")
                logger.warning(f"⚠️ Failed to create MCPApp from config: {e}")
                logger.info(
                    "🔄 Continuing without MCPApp - some features will be limited"
                )

    agent = ModularSlackMetaAgent(supabase_project_id, mcp_app)

    success = await agent.initialize()
    if not success:
        raise RuntimeError("Failed to initialize ModularSlackMetaAgent")

    return agent


# Example usage
async def main():
    """Example usage of the modular system."""
    import os
    import yaml

    # Get configuration from environment variables first
    supabase_project_id = os.getenv("SUPABASE_PROJECT_ID")
    slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
    slack_app_token = os.getenv("SLACK_APP_TOKEN")

    # If environment variables are not set, try to load from secrets file
    if not all([supabase_project_id, slack_bot_token, slack_app_token]):
        print("📄 Environment variables not found, trying to load from secrets file...")

        # Try to load from secrets YAML file
        secrets_paths = [
            "config/mcp_agent.secrets.yaml",
            "mcp_agent.secrets.yaml",
            "slack_meta_agent/config/mcp_agent.secrets.yaml",
        ]

        for secrets_file in secrets_paths:
            if os.path.exists(secrets_file):
                print(f"📄 Loading credentials from {secrets_file}")
                with open(secrets_file, "r") as f:
                    secrets = yaml.safe_load(f)

                    # Load Slack credentials
                    if "slack" in secrets:
                        slack_config = secrets["slack"]
                        slack_bot_token = slack_bot_token or slack_config.get(
                            "bot_token"
                        )
                        slack_app_token = slack_app_token or slack_config.get(
                            "app_token"
                        )

                    # Load Supabase project ID
                    if not supabase_project_id:
                        # First try the MCP section
                        if (
                            "mcp" in secrets
                            and "servers" in secrets["mcp"]
                            and "supabase" in secrets["mcp"]["servers"]
                        ):
                            supabase_project_id = secrets["mcp"]["servers"]["supabase"][
                                "env"
                            ].get("SUPABASE_PROJECT_ID")
                        # Then try the TRIBEsupabase section
                        elif "TRIBEsupabase" in secrets:
                            supabase_url = secrets["TRIBEsupabase"].get("url")
                            if supabase_url:
                                import re

                                match = re.search(
                                    r"https://([^.]+)\.supabase\.co", supabase_url
                                )
                                if match:
                                    supabase_project_id = match.group(1)
                        # Finally try the legacy supabase section
                        elif "supabase" in secrets:
                            # Extract from URL if available
                            supabase_url = secrets["supabase"].get("url")
                            if supabase_url:
                                import re

                                match = re.search(
                                    r"https://([^.]+)\.supabase\.co", supabase_url
                                )
                                if match:
                                    supabase_project_id = match.group(1)

                    print(f"✅ Loaded credentials from {secrets_file}")
                    break

    if not all([supabase_project_id, slack_bot_token, slack_app_token]):
        print("❌ Missing required configuration:")
        if not supabase_project_id:
            print("  - SUPABASE_PROJECT_ID")
        if not slack_bot_token:
            print("  - SLACK_BOT_TOKEN")
        if not slack_app_token:
            print("  - SLACK_APP_TOKEN")
        print(
            "\nPlease set environment variables or configure slack_meta_agent/config/mcp_agent.secrets.yaml"
        )
        return

    try:
        # Create and initialize the modular agent with MCP servers
        agent = await create_modular_slack_meta_agent(
            supabase_project_id=supabase_project_id,
            config_path="config/mcp_agent.config.yaml",
        )

        # Initialize Slack
        slack_success = await agent.initialize_slack(slack_bot_token, slack_app_token)
        if not slack_success:
            print("❌ Failed to initialize Slack")
            return

        print("✅ Modular SlackMetaAgent started successfully!")
        print("🔍 System Health:")
        health = await agent.health_check()
        print(f"   Status: {health['status']}")
        print(
            f"   Components: {len([c for c in health['components'].values() if c.get('status') != 'error'])}/{len(health['components'])} healthy"
        )

        # Start Slack connection
        print("🚀 Starting Slack connection...")
        await agent.start_slack_connection()

        # Keep the program running to maintain Slack connection
        print("✅ Slack connection established! Press Ctrl+C to stop.")
        print("🤖 Try mentioning your bot in Slack: @tomas hello")

        try:
            # Keep the event loop running indefinitely
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 Shutting down...")
            await agent.cleanup()

    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        if "agent" in locals():
            await agent.cleanup()
    except Exception as e:
        print(f"❌ Error: {e}")
        if "agent" in locals():
            await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
