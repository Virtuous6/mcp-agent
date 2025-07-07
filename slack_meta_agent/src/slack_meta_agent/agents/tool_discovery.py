"""
Tool Discovery Agent - Handles MCP tool discovery and caching.

This component is responsible for:
1. Discovering tools from MCP servers
2. Caching tool catalogs with TTL
3. Managing tool capability information
4. Providing search and filtering capabilities

Extracted from SlackMetaAgent to provide clean separation of concerns.
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any

from mcp_agent.agents.agent import Agent

from ..core.types import ToolCatalog, AgentSpec, AgentComponent


class ToolDiscoveryAgent(AgentComponent):
    """
    Discovers and catalogs tools from MCP servers with intelligent caching.

    Provides comprehensive tool discovery across all configured agents and servers,
    with performance optimizations and search capabilities.
    """

    def __init__(
        self,
        mcp_app=None,
        cache_ttl: int = 1800,  # 30 minutes default
        discovery_timeout: int = 10,
    ):  # 10 seconds per server
        super().__init__("ToolDiscovery")

        self.mcp_app = mcp_app
        self.cache_ttl = cache_ttl
        self.discovery_timeout = discovery_timeout

        # Cache management
        self._tool_catalog: Optional[ToolCatalog] = None
        self._cache_timestamp: Optional[datetime] = None

        # Performance metrics
        self._discovery_count = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._total_discovery_time = 0.0

    async def discover_tools(
        self, agent_specs: Dict[str, AgentSpec], force_refresh: bool = False
    ) -> ToolCatalog:
        """
        Discover tools from all MCP servers with intelligent caching.

        Args:
            agent_specs: Dictionary of agent specifications
            force_refresh: Force cache refresh even if valid

        Returns:
            ToolCatalog with discovered tools and capabilities
        """
        # Check cache validity
        if not force_refresh and self._is_cache_valid():
            self._cache_hits += 1
            self.logger.debug("🔄 Using cached tool catalog")
            return self._tool_catalog

        self._cache_misses += 1
        self._discovery_count += 1

        start_time = datetime.now()
        self.logger.info("🔍 Starting comprehensive tool discovery...")

        try:
            catalog = await self._perform_discovery(agent_specs)

            # Update cache
            self._tool_catalog = catalog
            self._cache_timestamp = datetime.now()

            # Update metrics
            discovery_time = (datetime.now() - start_time).total_seconds()
            self._total_discovery_time += discovery_time
            catalog.discovery_time = discovery_time

            self.logger.info(
                f"✅ Tool discovery complete: {catalog.total_tools} tools from "
                f"{len(catalog.servers)} servers in {discovery_time:.2f}s"
            )

            return catalog

        except Exception as e:
            self.logger.error(f"Tool discovery failed: {e}")
            # Return empty catalog on failure
            return ToolCatalog(
                agents={},
                servers={},
                total_tools=0,
                cache_timestamp=datetime.now(),
                discovery_time=(datetime.now() - start_time).total_seconds(),
            )

    async def find_tools_by_name(
        self, tool_name: str, agent_specs: Dict[str, AgentSpec] = None
    ) -> List[Dict[str, Any]]:
        """
        Find tools by name across all servers.

        Args:
            tool_name: Name or partial name to search for
            agent_specs: Agent specifications (if None, uses cached catalog)

        Returns:
            List of matching tools with server information
        """
        if agent_specs:
            catalog = await self.discover_tools(agent_specs)
        elif self._tool_catalog:
            catalog = self._tool_catalog
        else:
            self.logger.warning("No tool catalog available for search")
            return []

        matches = []
        tool_name_lower = tool_name.lower()

        for server_name, tools in catalog.servers.items():
            for tool in tools:
                if tool_name_lower in tool["name"].lower():
                    matches.append(
                        {
                            **tool,
                            "server_name": server_name,
                            "match_type": "exact"
                            if tool["name"].lower() == tool_name_lower
                            else "partial",
                        }
                    )

        # Sort by match quality (exact matches first)
        matches.sort(key=lambda x: (x["match_type"] != "exact", x["name"]))

        self.logger.info(f"🔍 Found {len(matches)} tools matching '{tool_name}'")
        return matches

    async def find_tools_by_capability(
        self, capability: str, agent_specs: Dict[str, AgentSpec] = None
    ) -> List[Dict[str, Any]]:
        """
        Find tools by capability or description keywords.

        Args:
            capability: Capability keyword to search for
            agent_specs: Agent specifications (if None, uses cached catalog)

        Returns:
            List of matching tools with relevance scores
        """
        if agent_specs:
            catalog = await self.discover_tools(agent_specs)
        elif self._tool_catalog:
            catalog = self._tool_catalog
        else:
            self.logger.warning("No tool catalog available for search")
            return []

        matches = []
        capability_lower = capability.lower()

        for server_name, tools in catalog.servers.items():
            for tool in tools:
                score = 0.0

                # Check tool name
                if capability_lower in tool["name"].lower():
                    score += 1.0

                # Check description
                description = tool.get("description", "").lower()
                if capability_lower in description:
                    score += 0.8

                # Check parameter names
                parameters = tool.get("parameters", {}).get("properties", {})
                for param_name in parameters:
                    if capability_lower in param_name.lower():
                        score += 0.5

                if score > 0:
                    matches.append(
                        {**tool, "server_name": server_name, "relevance_score": score}
                    )

        # Sort by relevance score
        matches.sort(key=lambda x: x["relevance_score"], reverse=True)

        self.logger.info(
            f"🔍 Found {len(matches)} tools with capability '{capability}'"
        )
        return matches

    async def get_server_capabilities(
        self, server_name: str, agent_specs: Dict[str, AgentSpec] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get detailed capabilities for a specific server.

        Args:
            server_name: Name of the server
            agent_specs: Agent specifications (if None, uses cached catalog)

        Returns:
            Server capabilities and tool information
        """
        if agent_specs:
            catalog = await self.discover_tools(agent_specs)
        elif self._tool_catalog:
            catalog = self._tool_catalog
        else:
            return None

        if server_name not in catalog.servers:
            return None

        tools = catalog.servers[server_name]

        return {
            "server_name": server_name,
            "tool_count": len(tools),
            "tools": tools,
            "capabilities": list(
                set(
                    tool.get("description", "").split()[0]
                    for tool in tools
                    if tool.get("description")
                )
            ),
            "parameter_types": self._analyze_parameter_types(tools),
        }

    def invalidate_cache(self) -> None:
        """Manually invalidate the tool catalog cache."""
        self._tool_catalog = None
        self._cache_timestamp = None
        self.logger.info("🔄 Tool catalog cache invalidated")

    async def get_metrics(self) -> Dict[str, Any]:
        """Get tool discovery performance metrics."""
        return {
            "discovery_count": self._discovery_count,
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "cache_hit_ratio": self._cache_hits
            / (self._cache_hits + self._cache_misses)
            if (self._cache_hits + self._cache_misses) > 0
            else 0,
            "average_discovery_time": self._total_discovery_time / self._discovery_count
            if self._discovery_count > 0
            else 0,
            "cache_valid": self._is_cache_valid(),
            "cache_age_seconds": (
                datetime.now() - self._cache_timestamp
            ).total_seconds()
            if self._cache_timestamp
            else None,
            "total_tools_cached": self._tool_catalog.total_tools
            if self._tool_catalog
            else 0,
            "servers_cached": len(self._tool_catalog.servers)
            if self._tool_catalog
            else 0,
        }

    # Private methods

    def _is_cache_valid(self) -> bool:
        """Check if the current cache is still valid."""
        if not self._tool_catalog or not self._cache_timestamp:
            return False

        age = datetime.now() - self._cache_timestamp
        return age.total_seconds() < self.cache_ttl

    async def _perform_discovery(
        self, agent_specs: Dict[str, AgentSpec]
    ) -> ToolCatalog:
        """Perform actual tool discovery from MCP servers."""
        discovered_tools = {}
        agent_info = {}
        total_tools = 0

        self.logger.info("🔍 Discovering tools from MCP servers...")

        if self.mcp_app:
            self.logger.info("✅ Using MCPApp context for tool discovery")

            for agent_type, spec in agent_specs.items():
                if not spec.server_names:
                    # Agents without servers - just use their capabilities
                    agent_info[agent_type] = {
                        "agent_description": spec.instruction[:200] + "..."
                        if len(spec.instruction) > 200
                        else spec.instruction,
                        "servers": [],
                        "tools": [],
                        "capabilities": spec.capabilities,
                    }
                    continue

                agent_tools = {}
                for server_name in spec.server_names:
                    try:
                        # Create temporary agent for discovery
                        temp_agent = Agent(
                            name=f"discovery_{server_name}",
                            instruction="Tool discovery agent",
                            server_names=[server_name],
                            context=self.mcp_app.context,
                        )

                        async with temp_agent:
                            # Add timeout for individual server discovery
                            tools_result = await asyncio.wait_for(
                                temp_agent.list_tools(server_name),
                                timeout=self.discovery_timeout,
                            )
                            capabilities = await asyncio.wait_for(
                                temp_agent.get_capabilities(server_name), timeout=5.0
                            )

                            server_tools = []
                            if tools_result:
                                for tool in tools_result.tools:
                                    tool_info = {
                                        "name": tool.name,
                                        "description": tool.description
                                        or "No description available",
                                        "parameters": getattr(tool, "inputSchema", {}),
                                    }
                                    server_tools.append(tool_info)
                                    total_tools += 1

                            agent_tools[server_name] = {
                                "tools": server_tools,
                                "capabilities": capabilities.model_dump()
                                if capabilities
                                else {},
                            }

                            # Store in global server catalog
                            if server_name not in discovered_tools:
                                discovered_tools[server_name] = []
                            discovered_tools[server_name].extend(server_tools)

                            # Log significant discoveries
                            if len(server_tools) > 5:
                                self.logger.info(
                                    f"✅ Discovered {len(server_tools)} tools from {server_name}"
                                )

                    except asyncio.TimeoutError:
                        self.logger.warning(
                            f"⚠️ Tool discovery timed out for {server_name}"
                        )
                        agent_tools[server_name] = {
                            "tools": [],
                            "capabilities": {},
                            "error": f"Timeout connecting to {server_name}",
                        }
                    except Exception as e:
                        self.logger.warning(
                            f"⚠️ Could not discover tools for {server_name}: {e}"
                        )
                        agent_tools[server_name] = {
                            "tools": [],
                            "capabilities": {},
                            "error": str(e),
                        }

                agent_info[agent_type] = {
                    "agent_description": spec.instruction[:200] + "..."
                    if len(spec.instruction) > 200
                    else spec.instruction,
                    "servers": spec.server_names,
                    "server_tools": agent_tools,
                    "capabilities": spec.capabilities,
                }
        else:
            # Fallback method without MCPApp
            self.logger.warning("⚠️ No MCPApp context - using fallback tool discovery")
            for agent_type, spec in agent_specs.items():
                agent_info[agent_type] = {
                    "agent_description": spec.instruction[:200] + "..."
                    if len(spec.instruction) > 200
                    else spec.instruction,
                    "servers": spec.server_names,
                    "server_tools": {},
                    "capabilities": spec.capabilities,
                    "error": "No MCPApp context available",
                }

        # Create catalog
        catalog = ToolCatalog(
            agents=agent_info,
            servers=discovered_tools,
            total_tools=total_tools,
            cache_timestamp=datetime.now(),
        )

        # Log discovery summary
        total_servers = sum(
            len(info.get("servers", [])) for info in agent_info.values()
        )
        self.logger.info(
            f"🎯 Discovery complete: {len(agent_info)} agents, {total_servers} servers, {total_tools} tools"
        )

        return catalog

    def _analyze_parameter_types(self, tools: List[Dict[str, Any]]) -> Dict[str, int]:
        """Analyze parameter types across tools for capability assessment."""
        param_types = {}

        for tool in tools:
            parameters = tool.get("parameters", {}).get("properties", {})
            for param_name, param_info in parameters.items():
                param_type = param_info.get("type", "unknown")
                param_types[param_type] = param_types.get(param_type, 0) + 1

        return param_types

    async def health_check(self) -> Dict[str, Any]:
        """Check component health."""
        base_health = await super().health_check()
        metrics = await self.get_metrics()

        return {
            **base_health,
            **metrics,
            "mcp_app_available": self.mcp_app is not None,
            "cache_status": "valid" if self._is_cache_valid() else "invalid",
        }
