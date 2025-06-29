"""
Supabase MCP Configuration Loader

This module provides functionality to load MCP Agent configurations from Supabase
instead of YAML files, enabling dynamic configuration management.
"""

import json
import logging
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path

# MCP Agent imports
from mcp_agent.config import (
    Settings,
    MCPSettings,
    MCPServerSettings,
    LoggerSettings,
    OpenAISettings,
    AnthropicSettings,
    AzureSettings,
    GoogleSettings,
    BedrockSettings,
    CohereSettings,
    TemporalSettings,
    UsageTelemetrySettings,
    OpenTelemetrySettings,
)

# For direct Supabase connection (bypass MCP server)
try:
    from supabase import create_client, Client

    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False


@dataclass
class DatabaseConfig:
    """Configuration for database connection"""

    project_id: str
    anon_key: str
    service_role_key: Optional[str] = None
    url: Optional[str] = None

    def __post_init__(self):
        if not self.url:
            self.url = f"https://{self.project_id}.supabase.co"

    async def execute_query(self, query: str) -> Dict[str, Any]:
        """Execute a raw SQL query against the database using direct table operations"""
        try:
            if not SUPABASE_AVAILABLE:
                raise ImportError("Supabase client not available")

            # Use service role key for backend operations, fallback to anon key
            key = self.service_role_key or self.anon_key
            client = create_client(self.url, key)

            # For simple SELECT queries, use table operations
            if query.strip().upper().startswith("SELECT"):
                # This is a simplified approach - for production, you'd want a proper SQL parser
                if "mcp_servers" in query.lower():
                    response = client.table("mcp_servers").select("*").execute()
                    return {"data": response.data, "status": "success"}
                elif "mcp_configurations" in query.lower():
                    response = client.table("mcp_configurations").select("*").execute()
                    return {"data": response.data, "status": "success"}

            # For complex queries, return a placeholder
            return {
                "data": [
                    {"info": "Complex query execution not implemented in direct mode"}
                ],
                "status": "limited",
            }

        except Exception as e:
            return {"data": None, "status": "error", "error": str(e)}


class SupabaseConfigLoader:
    """Loads MCP Agent configurations from Supabase database"""

    def __init__(self, db_config: DatabaseConfig):
        self.db_config = db_config
        self.client: Optional[Client] = None
        self.logger = logging.getLogger(__name__)
        self._cache: Dict[str, Any] = {}
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl_seconds = 300  # 5 minutes

        if not SUPABASE_AVAILABLE:
            raise ImportError(
                "Supabase client not available. Install with: pip install supabase"
            )

    def _get_client(self) -> Client:
        """Get or create Supabase client"""
        if self.client is None:
            # Use service role key for backend operations, fallback to anon key
            key = self.db_config.service_role_key or self.db_config.anon_key
            self.client = create_client(self.db_config.url, key)
        return self.client

    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid"""
        if self._cache_timestamp is None:
            return False

        age = (datetime.now() - self._cache_timestamp).total_seconds()
        return age < self._cache_ttl_seconds

    async def load_configuration(
        self, config_name: str, use_cache: bool = True
    ) -> Optional[Settings]:
        """
        Load configuration from Supabase database

        Args:
            config_name: Name of the configuration to load
            use_cache: Whether to use cached configuration

        Returns:
            Settings object or None if not found
        """
        try:
            # Check cache first
            if use_cache and self._is_cache_valid() and config_name in self._cache:
                self.logger.debug(f"Using cached configuration for {config_name}")
                return self._cache[config_name]

            client = self._get_client()

            # Use the database function to get configuration with servers
            response = client.rpc(
                "get_mcp_configuration", {"config_name": config_name}
            ).execute()

            if not response.data:
                self.logger.warning(
                    f"Configuration '{config_name}' not found in database"
                )
                return None

            config_data = response.data

            # Convert database format to Settings object
            settings = self._convert_db_to_settings(config_data)

            # Cache the result
            self._cache[config_name] = settings
            self._cache_timestamp = datetime.now()

            self.logger.info(f"Loaded configuration '{config_name}' from database")
            return settings

        except Exception as e:
            self.logger.error(f"Failed to load configuration from database: {e}")
            return None

    def _convert_db_to_settings(self, config_data: Dict) -> Settings:
        """Convert database configuration format to Settings object"""
        config = config_data.get("configuration", {})
        servers_data = config_data.get("servers", [])

        # Build MCP servers dictionary
        mcp_servers = {}
        for server in servers_data:
            server_name = server["server_name"]

            # Convert database format to MCPServerSettings
            mcp_server = MCPServerSettings(
                name=server.get("display_name") or server_name,
                description=server.get("description"),
                transport=server.get("transport", "stdio"),
                command=server.get("command"),
                args=server.get("args", []),
                url=server.get("url"),
                headers=server.get("headers", {}),
                http_timeout_seconds=server.get("http_timeout_seconds"),
                read_timeout_seconds=server.get("read_timeout_seconds"),
                terminate_on_close=server.get("terminate_on_close", True),
                env=server.get("env_vars", {}),
            )

            # Handle auth_settings
            if server.get("auth_settings"):
                # Convert auth_settings to proper format if needed
                pass

            # Handle roots
            if server.get("roots"):
                mcp_server.roots = server["roots"]

            mcp_servers[server_name] = mcp_server

        # Build Settings object
        settings_data = {
            "execution_engine": config.get("execution_engine", "asyncio"),
            "mcp": MCPSettings(servers=mcp_servers),
        }

        # Add logger settings
        if config.get("logger_settings"):
            logger_settings = config["logger_settings"]
            settings_data["logger"] = LoggerSettings(**logger_settings)

        # Add LLM provider settings
        if config.get("openai_settings"):
            settings_data["openai"] = OpenAISettings(**config["openai_settings"])

        if config.get("anthropic_settings"):
            settings_data["anthropic"] = AnthropicSettings(
                **config["anthropic_settings"]
            )

        if config.get("azure_settings"):
            settings_data["azure"] = AzureSettings(**config["azure_settings"])

        if config.get("google_settings"):
            settings_data["google"] = GoogleSettings(**config["google_settings"])

        if config.get("bedrock_settings"):
            settings_data["bedrock"] = BedrockSettings(**config["bedrock_settings"])

        if config.get("cohere_settings"):
            settings_data["cohere"] = CohereSettings(**config["cohere_settings"])

        if config.get("temporal_settings"):
            settings_data["temporal"] = TemporalSettings(**config["temporal_settings"])

        if config.get("usage_telemetry_settings"):
            settings_data["usage_telemetry"] = UsageTelemetrySettings(
                **config["usage_telemetry_settings"]
            )

        if config.get("otel_settings"):
            settings_data["otel"] = OpenTelemetrySettings(**config["otel_settings"])

        return Settings(**settings_data)

    def list_configurations(self) -> List[Dict[str, Any]]:
        """List all available configurations"""
        try:
            client = self._get_client()

            response = (
                client.table("mcp_configurations")
                .select(
                    "id, name, description, execution_engine, is_active, created_at, updated_at"
                )
                .execute()
            )

            return response.data

        except Exception as e:
            self.logger.error(f"Failed to list configurations: {e}")
            return []

    def create_configuration(self, config_data: Dict[str, Any]) -> Optional[str]:
        """Create a new configuration in the database"""
        try:
            client = self._get_client()

            # Insert main configuration
            config_response = (
                client.table("mcp_configurations").insert(config_data).execute()
            )

            if not config_response.data:
                raise Exception("Failed to create configuration")

            config_id = config_response.data[0]["id"]
            self.logger.info(f"Created configuration with ID: {config_id}")

            # Clear cache
            self._cache.clear()
            self._cache_timestamp = None

            return config_id

        except Exception as e:
            self.logger.error(f"Failed to create configuration: {e}")
            return None

    def update_configuration(self, config_name: str, updates: Dict[str, Any]) -> bool:
        """Update an existing configuration"""
        try:
            client = self._get_client()

            response = (
                client.table("mcp_configurations")
                .update(updates)
                .eq("name", config_name)
                .execute()
            )

            if response.data:
                self.logger.info(f"Updated configuration: {config_name}")

                # Clear cache
                self._cache.clear()
                self._cache_timestamp = None

                return True
            else:
                self.logger.warning(
                    f"Configuration '{config_name}' not found for update"
                )
                return False

        except Exception as e:
            self.logger.error(f"Failed to update configuration: {e}")
            return False

    def delete_configuration(self, config_name: str) -> bool:
        """Delete a configuration (and all its servers)"""
        try:
            client = self._get_client()

            response = (
                client.table("mcp_configurations")
                .delete()
                .eq("name", config_name)
                .execute()
            )

            if response.data:
                self.logger.info(f"Deleted configuration: {config_name}")

                # Clear cache
                self._cache.clear()
                self._cache_timestamp = None

                return True
            else:
                self.logger.warning(
                    f"Configuration '{config_name}' not found for deletion"
                )
                return False

        except Exception as e:
            self.logger.error(f"Failed to delete configuration: {e}")
            return False


class ConfigurationManager:
    """High-level configuration management with fallback to YAML"""

    def __init__(
        self,
        supabase_config: Optional[DatabaseConfig] = None,
        fallback_to_yaml: bool = True,
    ):
        self.supabase_config = supabase_config
        self.fallback_to_yaml = fallback_to_yaml
        self.db_loader: Optional[SupabaseConfigLoader] = None
        self.logger = logging.getLogger(__name__)

        # Initialize database loader if config provided
        if supabase_config and SUPABASE_AVAILABLE:
            try:
                self.db_loader = SupabaseConfigLoader(supabase_config)
            except Exception as e:
                self.logger.warning(f"Failed to initialize database loader: {e}")
                self.db_loader = None

    async def get_settings(
        self, config_name: Optional[str] = None, config_path: Optional[str] = None
    ) -> Settings:
        """
        Get settings with database-first approach and YAML fallback

        Args:
            config_name: Name of configuration in database
            config_path: Path to YAML config file (fallback)

        Returns:
            Settings object
        """
        # Try database first
        if self.db_loader and config_name:
            try:
                db_settings = await self.db_loader.load_configuration(config_name)
                if db_settings:
                    self.logger.info(f"Using database configuration: {config_name}")
                    return db_settings
            except Exception as e:
                self.logger.warning(f"Database configuration failed: {e}")

        # Fallback to YAML
        if self.fallback_to_yaml:
            self.logger.info("Falling back to YAML configuration")
            from mcp_agent.config import get_settings

            return get_settings(config_path)

        # If no fallback, return default settings
        self.logger.warning("No configuration found, using defaults")
        return Settings()

    def migrate_yaml_to_database(
        self, yaml_path: str, config_name: str, description: Optional[str] = None
    ) -> bool:
        """
        Migrate YAML configuration to database

        Args:
            yaml_path: Path to YAML configuration file
            config_name: Name for the database configuration
            description: Optional description

        Returns:
            True if successful
        """
        if not self.db_loader:
            self.logger.error("Database loader not available")
            return False

        try:
            # Load YAML configuration
            from mcp_agent.config import get_settings

            yaml_settings = get_settings(yaml_path)

            # Convert to database format
            config_data = self._convert_settings_to_db_format(
                yaml_settings, config_name, description
            )

            # Create in database
            config_id = self.db_loader.create_configuration(
                config_data["configuration"]
            )
            if not config_id:
                return False

            # Create servers
            for server_data in config_data["servers"]:
                server_data["configuration_id"] = config_id
                self._create_server_in_db(server_data)

            self.logger.info(
                f"Successfully migrated {yaml_path} to database as '{config_name}'"
            )
            return True

        except Exception as e:
            self.logger.error(f"Failed to migrate YAML to database: {e}")
            return False

    def _convert_settings_to_db_format(
        self, settings: Settings, config_name: str, description: Optional[str] = None
    ) -> Dict[str, Any]:
        """Convert Settings object to database format"""

        # Main configuration
        config_data = {
            "name": config_name,
            "description": description or f"Migrated from YAML configuration",
            "execution_engine": settings.execution_engine,
        }

        # Add provider settings
        if settings.logger:
            config_data["logger_settings"] = settings.logger.model_dump()

        if settings.openai:
            # Don't include API key in database - keep in secrets
            openai_data = settings.openai.model_dump()
            openai_data.pop("api_key", None)
            config_data["openai_settings"] = openai_data

        if settings.anthropic:
            anthropic_data = settings.anthropic.model_dump()
            anthropic_data.pop("api_key", None)
            config_data["anthropic_settings"] = anthropic_data

        if settings.azure:
            azure_data = settings.azure.model_dump()
            azure_data.pop("api_key", None)
            config_data["azure_settings"] = azure_data

        if settings.google:
            google_data = settings.google.model_dump()
            google_data.pop("api_key", None)
            config_data["google_settings"] = google_data

        if settings.bedrock:
            config_data["bedrock_settings"] = settings.bedrock.model_dump()

        if settings.cohere:
            cohere_data = settings.cohere.model_dump()
            cohere_data.pop("api_key", None)
            config_data["cohere_settings"] = cohere_data

        if settings.temporal:
            config_data["temporal_settings"] = settings.temporal.model_dump()

        if settings.usage_telemetry:
            config_data["usage_telemetry_settings"] = (
                settings.usage_telemetry.model_dump()
            )

        if settings.otel:
            config_data["otel_settings"] = settings.otel.model_dump()

        # Convert MCP servers
        servers = []
        if settings.mcp and settings.mcp.servers:
            for server_name, server_config in settings.mcp.servers.items():
                server_data = {
                    "server_name": server_name,
                    "display_name": server_config.name,
                    "description": server_config.description,
                    "transport": server_config.transport,
                    "command": server_config.command,
                    "args": server_config.args,
                    "url": server_config.url,
                    "headers": server_config.headers,
                    "http_timeout_seconds": server_config.http_timeout_seconds,
                    "read_timeout_seconds": server_config.read_timeout_seconds,
                    "terminate_on_close": server_config.terminate_on_close,
                    "roots": server_config.roots,
                    "env_vars": server_config.env or {},
                }
                servers.append(server_data)

        return {"configuration": config_data, "servers": servers}

    def _create_server_in_db(self, server_data: Dict[str, Any]) -> bool:
        """Create a server record in the database"""
        try:
            client = self.db_loader._get_client()

            # Separate env vars
            env_vars = server_data.pop("env_vars", {})

            # Insert server
            server_response = client.table("mcp_servers").insert(server_data).execute()

            if not server_response.data:
                return False

            server_id = server_response.data[0]["id"]

            # Insert environment variables
            for var_name, var_value in env_vars.items():
                env_data = {
                    "server_id": server_id,
                    "var_name": var_name,
                    "var_value": var_value,
                    "is_secret": var_name.lower()
                    in ["api_key", "token", "secret", "password"],
                }
                client.table("mcp_server_env_vars").insert(env_data).execute()

            return True

        except Exception as e:
            self.logger.error(f"Failed to create server in database: {e}")
            return False


def create_config_manager_from_env() -> ConfigurationManager:
    """Create configuration manager using environment variables"""

    # Get Supabase configuration from environment
    project_id = os.getenv("SUPABASE_PROJECT_ID")
    anon_key = os.getenv("SUPABASE_ANON_KEY")
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not project_id or not anon_key:
        # No database config, use YAML only
        return ConfigurationManager(fallback_to_yaml=True)

    db_config = DatabaseConfig(
        project_id=project_id, anon_key=anon_key, service_role_key=service_role_key
    )

    return ConfigurationManager(supabase_config=db_config, fallback_to_yaml=True)


# Global configuration manager instance
_config_manager: Optional[ConfigurationManager] = None


async def get_settings_from_database(
    config_name: str, config_path: Optional[str] = None
) -> Settings:
    """
    Convenience function to get settings with database-first approach

    Args:
        config_name: Name of configuration in database
        config_path: Fallback YAML path

    Returns:
        Settings object
    """
    global _config_manager

    if _config_manager is None:
        _config_manager = create_config_manager_from_env()

    return await _config_manager.get_settings(config_name, config_path)
