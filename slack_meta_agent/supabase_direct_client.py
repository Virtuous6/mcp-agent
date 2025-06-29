"""
Direct Supabase client for reliable database operations
Bypasses MCP infrastructure for speed and reliability
"""

import json
import asyncio
import aiohttp
from typing import Dict, Any, List, Optional
from datetime import datetime
import logging


class SupabaseDirectClient:
    """Direct Supabase client for reliable database operations"""

    def __init__(
        self, project_id: str, anon_key: str = None, service_role_key: str = None
    ):
        self.project_id = project_id
        self.base_url = f"https://{project_id}.supabase.co"
        self.anon_key = anon_key
        self.service_role_key = service_role_key
        self.logger = logging.getLogger(__name__)

        # Use service role key if available, otherwise anon key
        self.api_key = service_role_key if service_role_key else anon_key

        if not self.api_key:
            raise ValueError("Either anon_key or service_role_key must be provided")

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for Supabase API requests"""
        return {
            "apikey": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    async def execute_sql(self, query: str) -> Dict[str, Any]:
        """Execute raw SQL query directly against Supabase"""
        try:
            url = f"{self.base_url}/rest/v1/rpc/execute_sql"

            async with aiohttp.ClientSession() as session:
                payload = {"query": query}

                async with session.post(
                    url,
                    headers=self._get_headers(),
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        self.logger.info(f"✅ SQL executed successfully")
                        return {"success": True, "data": result}
                    else:
                        error_text = await response.text()
                        self.logger.error(
                            f"❌ SQL execution failed: {response.status} - {error_text}"
                        )
                        return {
                            "success": False,
                            "error": f"HTTP {response.status}: {error_text}",
                        }

        except Exception as e:
            self.logger.error(f"❌ SQL execution error: {e}")
            return {"success": False, "error": str(e)}

    async def insert_mcp_server(self, server_info: Dict[str, Any]) -> Dict[str, Any]:
        """Insert MCP server configuration directly into Supabase"""
        try:
            # Step 1: Ensure configuration exists
            config_result = await self.upsert_configuration()
            if not config_result["success"]:
                return config_result

            # Step 2: Insert server
            url = f"{self.base_url}/rest/v1/mcp_servers"

            # Prepare server data
            server_data = {
                "configuration_id": config_result["config_id"],
                "server_name": server_info["server_name"],
                "display_name": server_info.get(
                    "display_name", server_info["server_name"]
                ),
                "description": server_info.get("description", ""),
                "transport": server_info["transport"],
                "url": server_info.get("url"),
                "command": server_info.get("command"),
                "args": server_info.get("args", []),
                "is_enabled": True,
                "priority": 100,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=self._get_headers(),
                    json=server_data,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status in [200, 201]:
                        result = await response.json()
                        self.logger.info(
                            f"✅ Server '{server_info['server_name']}' added successfully"
                        )
                        return {
                            "success": True,
                            "server_id": result[0]["id"] if result else "unknown",
                            "data": result,
                        }
                    else:
                        error_text = await response.text()
                        self.logger.error(
                            f"❌ Server insertion failed: {response.status} - {error_text}"
                        )
                        return {
                            "success": False,
                            "error": f"HTTP {response.status}: {error_text}",
                        }

        except Exception as e:
            self.logger.error(f"❌ Server insertion error: {e}")
            return {"success": False, "error": str(e)}

    async def upsert_configuration(self) -> Dict[str, Any]:
        """Ensure the user_added_servers configuration exists"""
        try:
            url = f"{self.base_url}/rest/v1/mcp_configurations"

            config_data = {
                "name": "user_added_servers",
                "description": "User-added MCP servers from Slack",
                "is_active": True,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }

            # First try to get existing config
            async with aiohttp.ClientSession() as session:
                get_url = f"{url}?name=eq.user_added_servers"
                async with session.get(
                    get_url,
                    headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status == 200:
                        existing = await response.json()
                        if existing:
                            config_id = existing[0]["id"]
                            self.logger.info(
                                f"✅ Using existing configuration: {config_id}"
                            )
                            return {"success": True, "config_id": config_id}

                # If not found, create new one
                headers = self._get_headers()
                headers["Prefer"] = "return=representation"

                async with session.post(
                    url,
                    headers=headers,
                    json=config_data,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status in [200, 201]:
                        result = await response.json()
                        config_id = result[0]["id"] if result else None
                        self.logger.info(f"✅ Created new configuration: {config_id}")
                        return {"success": True, "config_id": config_id}
                    else:
                        error_text = await response.text()
                        self.logger.error(
                            f"❌ Configuration creation failed: {response.status} - {error_text}"
                        )
                        return {
                            "success": False,
                            "error": f"HTTP {response.status}: {error_text}",
                        }

        except Exception as e:
            self.logger.error(f"❌ Configuration upsert error: {e}")
            return {"success": False, "error": str(e)}

    async def log_interaction(
        self, user_id: str, message: str, result: str, analysis: Dict = None
    ) -> Dict[str, Any]:
        """Log user interaction directly to Supabase (with schema-aware fallback)"""
        try:
            url = f"{self.base_url}/rest/v1/interactions"

            # Try with 'response' column first (more likely to exist than 'result')
            interaction_data = {
                "user_id": user_id,
                "message": message,
                "response": result,  # Changed from 'result' to 'response'
                "analysis": analysis or {},
                "created_at": datetime.utcnow().isoformat(),  # Changed from 'timestamp'
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=self._get_headers(),
                    json=interaction_data,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status in [200, 201]:
                        self.logger.info(f"✅ Interaction logged for user {user_id}")
                        return {"success": True}
                    else:
                        error_text = await response.text()
                        # If it's a schema error, try with minimal data
                        if "schema cache" in error_text or response.status == 400:
                            self.logger.debug(
                                f"📝 Schema mismatch, trying minimal interaction log..."
                            )
                            minimal_data = {
                                "user_id": user_id,
                                "message": message,
                                "created_at": datetime.utcnow().isoformat(),
                            }
                            async with session.post(
                                url,
                                headers=self._get_headers(),
                                json=minimal_data,
                                timeout=aiohttp.ClientTimeout(total=30),
                            ) as retry_response:
                                if retry_response.status in [200, 201]:
                                    self.logger.info(
                                        f"✅ Minimal interaction logged for user {user_id}"
                                    )
                                    return {"success": True}

                        self.logger.debug(
                            f"⚠️ Interaction logging failed: {response.status} - {error_text}"
                        )
                        return {
                            "success": False,
                            "error": f"HTTP {response.status}: {error_text}",
                        }

        except Exception as e:
            self.logger.debug(f"⚠️ Interaction logging error: {e}")
            return {"success": False, "error": str(e)}

    async def log_conversation_memory(
        self, user_id: str, message: str, channel_id: str
    ) -> Dict[str, Any]:
        """Log conversation memory directly to Supabase (falls back gracefully if table doesn't exist)"""
        try:
            # Try conversation_memory table first
            url = f"{self.base_url}/rest/v1/conversation_memory"

            memory_data = {
                "user_id": user_id,
                "message": message,
                "channel_id": channel_id,
                "created_at": datetime.utcnow().isoformat(),
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=self._get_headers(),
                    json=memory_data,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status in [200, 201]:
                        self.logger.info(f"✅ Memory logged for user {user_id}")
                        return {"success": True}
                    elif response.status == 404:
                        # Table doesn't exist, try fallback to interactions table
                        self.logger.debug(
                            f"📝 conversation_memory table not found, trying interactions fallback"
                        )
                        fallback_url = f"{self.base_url}/rest/v1/interactions"
                        fallback_data = {
                            "user_id": user_id,
                            "message": message,
                            "channel_id": channel_id,
                            "created_at": datetime.utcnow().isoformat(),
                        }
                        async with session.post(
                            fallback_url,
                            headers=self._get_headers(),
                            json=fallback_data,
                            timeout=aiohttp.ClientTimeout(total=30),
                        ) as fallback_response:
                            if fallback_response.status in [200, 201]:
                                self.logger.info(
                                    f"✅ Memory logged via interactions fallback for user {user_id}"
                                )
                                return {"success": True}

                        self.logger.debug(f"⚠️ Memory logging fallback also failed")
                        return {
                            "success": False,
                            "error": "No suitable table found for memory logging",
                        }
                    else:
                        error_text = await response.text()
                        self.logger.debug(
                            f"⚠️ Memory logging failed: {response.status} - {error_text}"
                        )
                        return {
                            "success": False,
                            "error": f"HTTP {response.status}: {error_text}",
                        }

        except Exception as e:
            self.logger.debug(f"⚠️ Memory logging error: {e}")
            return {"success": False, "error": str(e)}

    async def verify_server_exists(self, server_name: str) -> Dict[str, Any]:
        """Verify that a server exists in the database"""
        try:
            url = f"{self.base_url}/rest/v1/mcp_servers?server_name=eq.{server_name}"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        exists = len(result) > 0
                        self.logger.info(f"✅ Server '{server_name}' exists: {exists}")
                        return {"success": True, "exists": exists, "data": result}
                    else:
                        error_text = await response.text()
                        self.logger.error(
                            f"❌ Server verification failed: {response.status} - {error_text}"
                        )
                        return {
                            "success": False,
                            "error": f"HTTP {response.status}: {error_text}",
                        }

        except Exception as e:
            self.logger.error(f"❌ Server verification error: {e}")
            return {"success": False, "error": str(e)}

    async def health_check(self) -> Dict[str, Any]:
        """Check if the Supabase connection is healthy"""
        try:
            url = f"{self.base_url}/rest/v1/"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status == 200:
                        self.logger.info("✅ Supabase connection healthy")
                        return {"success": True, "status": "healthy"}
                    else:
                        self.logger.error(
                            f"❌ Supabase connection unhealthy: {response.status}"
                        )
                        return {
                            "success": False,
                            "status": "unhealthy",
                            "code": response.status,
                        }

        except Exception as e:
            self.logger.error(f"❌ Supabase health check error: {e}")
            return {"success": False, "status": "error", "error": str(e)}
