"""
Supabase database operations for the Slack Meta-Agent system

This module contains all database operations extracted from the monolithic
SlackMetaAgent class for better separation of concerns.
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM


class SupabaseOperations:
    """Handles all Supabase database operations for the meta-agent system"""

    def __init__(self, supabase_project_id: str):
        self.supabase_project_id = supabase_project_id
        self.logger = logging.getLogger("SupabaseOperations")

        # Try to initialize direct client for reliability
        self.direct_client = None
        try:
            # This would import the existing SupabaseDirectClient
            from .supabase_client import SupabaseDirectClient

            # Initialize with credentials from environment or secrets
            self.direct_client = SupabaseDirectClient(project_id=supabase_project_id)
            self.logger.info("✅ Direct Supabase client initialized")
        except Exception as e:
            self.logger.warning(f"⚠️ Could not initialize direct client: {e}")

    async def store_conversation_memory(
        self, user_id: str, message: str, channel_id: str
    ) -> Dict[str, Any]:
        """Store conversation in Supabase memory with direct client fallback"""
        try:
            # Try direct client first
            if self.direct_client:
                result = await self.direct_client.log_conversation_memory(
                    user_id, message, channel_id
                )
                if result["success"]:
                    self.logger.info(f"✅ Stored conversation for user {user_id}")
                    return result
                else:
                    self.logger.warning(f"⚠️ Direct storage failed: {result['error']}")

            # Fallback to MCP method
            return await self._store_via_mcp(
                table="interactions",
                data={
                    "user_id": user_id,
                    "channel_id": channel_id,
                    "message": message,
                    "created_at": datetime.now().isoformat(),
                },
            )

        except Exception as e:
            self.logger.error(f"❌ Conversation storage error: {e}")
            return {"success": False, "error": str(e)}

    async def store_interaction(
        self, user_id: str, message: str, response: str, analysis: Dict
    ) -> Dict[str, Any]:
        """Store interaction for system learning"""
        try:
            # Try direct client first
            if self.direct_client:
                result = await self.direct_client.log_interaction(
                    user_id, message, response, analysis
                )
                if result["success"]:
                    self.logger.info(f"✅ Stored interaction for user {user_id}")
                    return result
                else:
                    self.logger.warning(f"⚠️ Direct interaction storage failed")

            # Fallback to MCP method
            return await self._store_via_mcp(
                table="interactions",
                data={
                    "user_id": user_id,
                    "message": message,
                    "response": response,
                    "analysis": analysis,
                    "intent_analysis": analysis,
                    "execution_strategy": analysis.get("execution_strategy", ""),
                    "required_agents": analysis.get("required_agents", []),
                    "created_at": datetime.now().isoformat(),
                },
            )

        except Exception as e:
            self.logger.error(f"❌ Interaction storage error: {e}")
            return {"success": False, "error": str(e)}

    async def store_interaction_learning(
        self, user_id: str, message: str, result: str, analysis: Dict
    ) -> Dict[str, Any]:
        """Store interaction for system learning - alias for store_interaction"""
        return await self.store_interaction(user_id, message, result, analysis)

    async def store_feedback(
        self,
        user_id: str,
        channel_id: str,
        feedback_text: str,
        category: str,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Store user feedback in the database"""
        try:
            # Try direct client first
            if self.direct_client:
                result = await self.direct_client.store_feedback(
                    user_id=user_id,
                    channel_id=channel_id,
                    feedback_text=feedback_text,
                    category=category,
                    metadata=metadata,
                )
                if result["success"]:
                    self.logger.info(f"✅ Stored feedback for user {user_id}")
                    return result
                else:
                    self.logger.warning(f"⚠️ Direct feedback storage failed")

            # Fallback to MCP method
            return await self._store_via_mcp(
                table="feedback",
                data={
                    "user_id": user_id,
                    "channel_id": channel_id,
                    "feedback_text": feedback_text,
                    "category": category,
                    "metadata": metadata,
                    "created_at": datetime.now().isoformat(),
                },
            )

        except Exception as e:
            self.logger.error(f"❌ Feedback storage error: {e}")
            return {"success": False, "error": str(e)}

    async def add_mcp_server(self, server_info: Dict[str, Any]) -> Dict[str, Any]:
        """Add a new MCP server configuration to the database"""
        try:
            # Try secure insertion if direct client is available
            if self.direct_client:
                result = await self.direct_client.insert_mcp_server_secure(server_info)
                if result["success"]:
                    self.logger.info(
                        f"✅ Added MCP server: {server_info['server_name']}"
                    )
                    return result
                else:
                    self.logger.warning(f"⚠️ Direct server addition failed")

            # Fallback to MCP method
            return await self._add_mcp_server_via_mcp(server_info)

        except Exception as e:
            self.logger.error(f"❌ MCP server addition error: {e}")
            return {"success": False, "error": str(e)}

    async def discover_mcp_servers(self, keywords: List[str]) -> List[Dict]:
        """Discover MCP servers from database based on keywords"""
        try:
            # Build search query
            keyword_conditions = []
            for keyword in keywords:
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

            result = await self._query_via_mcp(sql_query)
            return self._parse_mcp_servers_result(result)

        except Exception as e:
            self.logger.error(f"❌ MCP server discovery error: {e}")
            return []

    async def find_tools_across_servers(self, tool_keywords: List[str]) -> List[Dict]:
        """Find tools by name across all available servers"""
        try:
            self.logger.info(f"🔍 Searching for tools matching: {tool_keywords}")

            # First get all active servers
            all_servers = await self.discover_mcp_servers([])  # Get all servers

            found_tools = []

            # For each server, try to discover tools and search for matching ones
            for server_config in all_servers:
                server_name = server_config.get("server_name", "unknown")
                self.logger.info(f"🔍 Checking server '{server_name}' for tools...")

                try:
                    # Try to discover tools from this server
                    tools_info = await self._discover_tools_from_server(server_config)

                    # Search for matching tools
                    for tool in tools_info:
                        tool_name = tool.get("name", "").lower()
                        tool_description = tool.get("description", "").lower()

                        # Check if any keyword matches the tool name or description
                        for keyword in tool_keywords:
                            keyword_lower = keyword.lower()
                            if (
                                keyword_lower in tool_name
                                or keyword_lower in tool_description
                                or tool_name in keyword_lower
                            ):
                                found_tools.append(
                                    {
                                        "tool_name": tool.get("name"),
                                        "tool_description": tool.get("description"),
                                        "server_name": server_name,
                                        "server_config": server_config,
                                        "tool_schema": tool.get("parameters", {}),
                                        "match_type": "tool_name"
                                        if keyword_lower in tool_name
                                        else "tool_description",
                                    }
                                )
                                self.logger.info(
                                    f"✅ Found tool '{tool['name']}' on server '{server_name}'"
                                )
                                break

                except Exception as e:
                    self.logger.warning(
                        f"⚠️ Could not discover tools from server '{server_name}': {e}"
                    )
                    continue

            self.logger.info(
                f"🎯 Found {len(found_tools)} matching tools across all servers"
            )
            return found_tools

        except Exception as e:
            self.logger.error(f"❌ Tool search across servers error: {e}")
            return []

    async def _discover_tools_from_server(self, server_config: Dict) -> List[Dict]:
        """Discover tools from a specific server configuration"""
        try:
            from mcp_agent.agents.agent import Agent
            from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

            # Create a temporary agent to connect to this server
            temp_agent = Agent(
                name=f"tool_discovery_{server_config['server_name']}",
                instruction="Discover tools from server",
                server_names=[server_config["server_name"]],
                context=None,  # Will use default context
            )

            tools = []

            async with temp_agent:
                # Try to list tools from this server
                try:
                    tools_result = await temp_agent.list_tools(
                        server_config["server_name"]
                    )

                    if tools_result and tools_result.tools:
                        tools = [
                            {
                                "name": tool.name,
                                "description": tool.description
                                or "No description available",
                                "parameters": getattr(tool, "inputSchema", {}),
                            }
                            for tool in tools_result.tools
                        ]
                except Exception as e:
                    self.logger.warning(
                        f"Could not list tools from {server_config['server_name']}: {e}"
                    )

            return tools

        except Exception as e:
            self.logger.warning(f"Could not discover tools from server: {e}")
            return []

    async def verify_data_insertion(self) -> str:
        """Verify that data was actually inserted into the database"""
        try:
            sql_query = """
            SELECT user_id, message, created_at 
            FROM interactions 
            ORDER BY created_at DESC 
            LIMIT 5;
            """

            result = await self._execute_sql_via_agent(sql_query)
            self.logger.info(f"📊 Database verification result: {result}")
            return result

        except Exception as e:
            self.logger.error(f"❌ Database verification failed: {e}")
            return f"Error: {str(e)}"

    async def _store_via_mcp(self, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Store data using MCP agent as fallback"""
        try:
            self.logger.info(f"📝 Storing to {table} via MCP (fallback)")

            # Create SQL insert statement
            if table == "interactions":
                # Prepare data for interactions table
                escaped_message = data["message"].replace("'", "''")
                escaped_response = data.get("response", "").replace("'", "''")
                analysis_json = json.dumps(data.get("analysis", {})).replace("'", "''")

                sql = f"""
                INSERT INTO interactions (
                    user_id, 
                    channel_id, 
                    message, 
                    response,
                    analysis,
                    intent_analysis,
                    execution_strategy,
                    required_agents,
                    created_at
                ) VALUES (
                    '{data["user_id"]}',
                    '{data.get("channel_id", "")}',
                    '{escaped_message}',
                    '{escaped_response}',
                    '{analysis_json}'::jsonb,
                    '{analysis_json}'::jsonb,
                    '{data.get("execution_strategy", "")}',
                    ARRAY{data.get("required_agents", [])},
                    NOW()
                );
                """

            elif table == "feedback":
                # Prepare data for feedback table
                escaped_feedback = data["feedback_text"].replace("'", "''")
                metadata_json = json.dumps(data.get("metadata", {})).replace("'", "''")

                sql = f"""
                INSERT INTO feedback (
                    user_id,
                    channel_id,
                    feedback_text,
                    category,
                    metadata,
                    created_at
                ) VALUES (
                    '{data["user_id"]}',
                    '{data.get("channel_id", "")}',
                    '{escaped_feedback}',
                    '{data.get("category", "general")}',
                    '{metadata_json}'::jsonb,
                    NOW()
                );
                """

            else:
                return {"success": False, "error": f"Unsupported table: {table}"}

            # Execute SQL using a temporary agent
            result = await self._execute_sql_via_agent(sql)
            if "INSERT" in result or "successfully" in result.lower():
                return {"success": True, "method": "mcp_fallback", "details": result}
            else:
                return {"success": False, "error": f"Insert may have failed: {result}"}

        except Exception as e:
            self.logger.error(f"❌ MCP storage error: {e}")
            return {"success": False, "error": str(e)}

    async def _query_via_mcp(self, sql_query: str) -> str:
        """Execute SQL query using MCP agent as fallback"""
        try:
            self.logger.info(f"🔍 Executing query via MCP (fallback)")
            return await self._execute_sql_via_agent(sql_query)

        except Exception as e:
            self.logger.error(f"❌ MCP query error: {e}")
            return "[]"

    async def _add_mcp_server_via_mcp(
        self, server_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Add MCP server using MCP agent as fallback"""
        try:
            self.logger.info(f"🔧 Adding MCP server via MCP (fallback)")

            # Step 1: Ensure configuration exists
            config_sql = """
            INSERT INTO mcp_configurations (name, description, is_active, created_at, updated_at)
            VALUES ('user_added_servers', 'User-added MCP servers from Slack', true, NOW(), NOW())
            ON CONFLICT (name) DO UPDATE SET updated_at = NOW()
            RETURNING id;
            """

            config_result = await self._execute_sql_via_agent(config_sql)
            self.logger.info(f"✅ Configuration ensured: {config_result}")

            # Step 2: Add the server
            server_name = server_info["server_name"].replace("'", "''")
            display_name = server_info.get("display_name", server_name).replace(
                "'", "''"
            )
            description = server_info.get("description", "").replace("'", "''")
            transport = server_info[
                "transport"
            ].lower()  # Normalize to lowercase for database constraint
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
            args = json.dumps(server_info.get("args", [])).replace("'", "''")

            server_sql = f"""
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
            """

            server_result = await self._execute_sql_via_agent(server_sql)
            self.logger.info(f"✅ Server added: {server_result}")

            if server_name.lower() in server_result.lower():
                return {
                    "success": True,
                    "method": "mcp_fallback",
                    "server_id": "added_successfully",
                    "details": server_result,
                }
            else:
                return {
                    "success": False,
                    "error": f"Could not verify server '{server_name}' was added",
                    "details": server_result,
                }

        except Exception as e:
            self.logger.error(f"❌ MCP server addition error: {e}")
            return {"success": False, "error": str(e)}

    async def _execute_sql_via_agent(self, sql: str) -> str:
        """Execute SQL using a temporary MCP agent"""
        try:
            # Create a temporary agent with Supabase access
            agent = Agent(
                name="temp_supabase_agent",
                instruction="Execute SQL directly in Supabase",
                server_names=["supabase"],
            )

            async with agent:
                llm = await agent.attach_llm(OpenAIAugmentedLLM)

                prompt = f"""
                You must call the execute_sql tool directly with these exact parameters:
                - project_id: "{self.supabase_project_id}"
                - query: "{sql}"
                
                Call the execute_sql tool now with these parameters. Do not explain, just call the tool.
                """

                result = await llm.generate_str(prompt)
                self.logger.info(f"✅ SQL executed via MCP agent: {result[:100]}...")
                return result

        except Exception as e:
            self.logger.error(f"❌ SQL execution via agent failed: {e}")
            return f"Error: {str(e)}"

    def _parse_mcp_servers_result(self, result: str) -> List[Dict]:
        """Parse MCP server discovery results"""
        try:
            # Parse JSON or text result into server configurations
            import re

            servers = []

            # Try to parse as JSON first
            try:
                if result.strip().startswith("["):
                    servers = json.loads(result)
            except json.JSONDecodeError:
                # Parse from text format
                self.logger.info("🔍 Parsing text-based server data")

            self.logger.info(f"🎯 Parsed {len(servers)} servers from result")
            return servers

        except Exception as e:
            self.logger.warning(f"❌ Server result parsing error: {e}")
            return []
