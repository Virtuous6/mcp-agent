"""
MCP server discovery and management for the Slack Meta-Agent system

This module handles dynamic MCP server discovery and management.
"""

import logging
import json
import re
from typing import Dict, List, Optional, Any
from datetime import datetime

from mcp_agent.app import MCPApp
from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM


class MCPServerDiscovery:
    """Handles MCP server discovery and dynamic agent creation"""

    def __init__(self, mcp_app: MCPApp, supabase_project_id: str = None):
        self.mcp_app = mcp_app
        self.supabase_project_id = supabase_project_id
        self.logger = logging.getLogger("MCPServerDiscovery")

    async def discover_tools(self) -> Dict[str, Dict]:
        """Discover all available tools from connected MCP servers"""
        try:
            self.logger.info("🔍 Discovering available tools from MCP servers...")

            discovered_tools = {}

            if not self.mcp_app or not hasattr(self.mcp_app.context, "server_registry"):
                self.logger.warning("⚠️ No MCPApp context available")
                return {}

            # Get available servers from registry
            available_servers = list(
                self.mcp_app.context.server_registry.registry.keys()
            )

            self.logger.info(f"🔍 Found {len(available_servers)} configured servers")

            # For each server, try to discover tools
            for server_name in available_servers:
                try:
                    # Create a temporary agent to discover tools
                    temp_agent = Agent(
                        name=f"discovery_{server_name}",
                        instruction="Tool discovery agent",
                        server_names=[server_name],
                        context=self.mcp_app.context,
                    )

                    async with temp_agent:
                        tools_result = await temp_agent.list_tools(server_name)
                        capabilities = await temp_agent.get_capabilities(server_name)

                        discovered_tools[server_name] = {
                            "server_name": server_name,
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
                            "status": "available",
                        }

                        tool_count = len(discovered_tools[server_name]["tools"])
                        self.logger.info(
                            f"✅ Discovered {tool_count} tools from {server_name}"
                        )

                except Exception as e:
                    self.logger.warning(
                        f"⚠️ Could not discover tools for {server_name}: {e}"
                    )
                    discovered_tools[server_name] = {
                        "server_name": server_name,
                        "tools": [],
                        "capabilities": {},
                        "status": "error",
                        "error": str(e),
                    }

            self.logger.info(f"✅ Discovery complete: {len(discovered_tools)} servers")
            return discovered_tools

        except Exception as e:
            self.logger.error(f"❌ Tool discovery error: {e}")
            return {}

    async def find_servers_by_keywords(self, keywords: List[str]) -> List[Dict]:
        """Find MCP servers that match the given keywords from database"""
        try:
            self.logger.info(f"🔍 Searching for database servers matching: {keywords}")

            if not self.supabase_project_id:
                self.logger.warning(
                    "⚠️ No Supabase project ID configured for database search"
                )
                return []

            # Create agent for database queries
            discovery_agent = Agent(
                name="mcp_discovery_agent",
                instruction="Query database for MCP server configurations",
                server_names=["supabase"],
                context=self.mcp_app.context if self.mcp_app else None,
            )

            discovered_servers = []

            async with discovery_agent:
                llm = await discovery_agent.attach_llm(OpenAIAugmentedLLM)

                # Build keyword search conditions
                keyword_conditions = []

                # Look for compound keywords first (like "arc_supabase") for exact matches
                compound_keywords = [k for k in keywords if "_" in k]
                simple_keywords = [k for k in keywords if "_" not in k]

                for keyword in compound_keywords:
                    # For compound keywords like "arc_supabase", search more precisely
                    parts = keyword.split("_")
                    if len(parts) == 2:
                        org_part, service_part = parts
                        keyword_conditions.extend(
                            [
                                f"LOWER(s.server_name) LIKE LOWER('%{org_part}%{service_part}%')",
                                f"LOWER(s.server_name) LIKE LOWER('%{org_part}_{service_part}%')",
                                f"LOWER(s.display_name) LIKE LOWER('%{org_part}%{service_part}%')",
                            ]
                        )

                # Add simple keyword searches
                for keyword in simple_keywords:
                    keyword_conditions.extend(
                        [
                            f"LOWER(s.server_name) LIKE LOWER('%{keyword}%')",
                            f"LOWER(s.display_name) LIKE LOWER('%{keyword}%')",
                            f"LOWER(s.description) LIKE LOWER('%{keyword}%')",
                        ]
                    )

                where_clause = (
                    " OR ".join(keyword_conditions) if keyword_conditions else "1=1"
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
                Query the database to find MCP servers that match these keywords: {keywords}
                
                Use the execute_sql tool with:
                - project_id: "{self.supabase_project_id}"
                - query: "{sql_query}"
                
                Execute the SQL query and return the server details.
                """

                result = await llm.generate_str(prompt)
                self.logger.info(f"🔍 Database query result: {result[:200]}...")

                # Parse the result to extract server configurations
                discovered_servers = self._parse_mcp_query_result(result)

                self.logger.info(
                    f"🎯 Discovered {len(discovered_servers)} matching MCP servers from database"
                )

            return discovered_servers

        except Exception as e:
            self.logger.error(f"❌ Database server search error: {e}")
            return []

    def _parse_mcp_query_result(self, query_result: str) -> List[Dict]:
        """Parse the result from MCP server database query into server configurations"""
        servers = []

        try:
            # Try to extract JSON arrays or objects from the result
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

            # If still no JSON found, try to parse from markdown-style text
            if not servers:
                self.logger.info("🔍 Trying to parse markdown-style server data...")

                # Look for markdown-style server data blocks
                markdown_pattern = r"-\s*\*\*Server Name:\*\*\s*(\w+)"
                server_name_matches = re.findall(
                    markdown_pattern, query_result, re.IGNORECASE
                )

                for server_name in server_name_matches:
                    server_block = {"server_name": server_name}

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

        except Exception as e:
            self.logger.warning(f"Error parsing MCP query result: {e}")

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

    def get_configured_servers(self) -> List[str]:
        """Get list of configured server names"""
        try:
            if self.mcp_app and hasattr(self.mcp_app.context, "server_registry"):
                return list(self.mcp_app.context.server_registry.registry.keys())
            return []
        except Exception as e:
            self.logger.error(f"❌ Error getting configured servers: {e}")
            return []

    def score_server_matches(
        self, keywords: List[str], servers: List[str]
    ) -> Dict[str, float]:
        """Score how well keywords match available servers"""
        if not servers or not keywords:
            return {}

        scores = {}
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

            # Compound keyword matching
            if len(keywords_lower) > 1:
                compound_match = all(k in server_lower for k in keywords_lower)
                if compound_match:
                    score += 1.5  # Bonus for compound matches

            # Specificity bonus
            if "_" in server_lower and len(keywords_lower) > 1:
                score += 0.2

            scores[server] = score

        return scores

    async def create_dynamic_agent_with_server(
        self, server_config: Dict, agent_name: str = "dynamic_agent"
    ) -> Optional[Agent]:
        """Create a temporary agent with a dynamically discovered MCP server"""
        try:
            server_name = server_config["server_name"]

            self.logger.info(f"🚀 Creating dynamic agent with server: {server_name}")

            if not self.mcp_app:
                self.logger.error(
                    "❌ MCPApp context required for dynamic server creation"
                )
                return None

            # Configure the dynamic server slot with the discovered server's settings
            from mcp_agent.config import MCPServerSettings

            # Fix transport configuration for ghl-dynamic and similar servers
            transport = server_config.get("transport", "sse")
            url = server_config.get("url")
            command = server_config.get("command")
            args = server_config.get("args", [])

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
                name=server_config.get("display_name", server_name),
                description=server_config.get(
                    "description", f"Dynamically discovered server: {server_name}"
                ),
                transport=transport,
                url=url,
                command=command,
                args=args,
                headers=server_config.get("headers"),
                terminate_on_close=server_config.get("terminate_on_close", True),
            )

            # Temporarily add/update the dynamic server in the registry
            dynamic_server_name = "dynamic_server"
            if hasattr(self.mcp_app.context, "server_registry"):
                self.mcp_app.context.server_registry.registry[dynamic_server_name] = (
                    dynamic_server_config
                )
                self.logger.info(
                    f"✅ Configured dynamic server slot with {server_name} settings"
                )

                # Create agent with the dynamic server slot
                dynamic_agent = Agent(
                    name=f"{agent_name}_{int(datetime.now().timestamp())}",
                    instruction=f"""You are a dynamic agent with access to the '{server_name}' MCP server.
                    
                    Server: {server_name} ({server_config.get("transport", "stdio")} transport)
                    Description: {server_config.get("description", "Dynamically discovered server")}
                    """,
                    server_names=[dynamic_server_name],
                    context=self.mcp_app.context,
                )

                # Try to initialize the agent
                try:
                    await dynamic_agent.__aenter__()
                    self.logger.info(
                        f"✅ Dynamic agent created successfully using server slot"
                    )
                    return dynamic_agent
                except Exception as init_error:
                    self.logger.error(
                        f"❌ Failed to initialize dynamic agent: {init_error}"
                    )

                    # Clean up the dynamic server registration
                    if (
                        dynamic_server_name
                        in self.mcp_app.context.server_registry.registry
                    ):
                        del self.mcp_app.context.server_registry.registry[
                            dynamic_server_name
                        ]

                    return None
            else:
                self.logger.error("🔍 No server_registry found in MCPApp context")
                return None

        except Exception as e:
            self.logger.error(f"Failed to create dynamic agent: {e}")
            return None
