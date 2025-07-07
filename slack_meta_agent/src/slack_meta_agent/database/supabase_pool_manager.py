"""
Optimized Supabase Pool Manager for high-performance dynamic meta-agent operations.
Provides connection pooling, query caching, and batch operations.
"""

import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
import logging
from contextlib import asynccontextmanager

try:
    from supabase import create_client, Client
    from postgrest import APIResponse
except ImportError:
    Client = None
    APIResponse = None


@dataclass
class QueryCacheEntry:
    """Cache entry for database queries"""

    data: Any
    timestamp: datetime
    ttl_seconds: int
    query_hash: str


@dataclass
class ConnectionMetrics:
    """Track connection pool metrics"""

    total_queries: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    avg_query_time: float = 0.0
    active_connections: int = 0

    @property
    def cache_hit_rate(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return (self.cache_hits / self.total_queries) * 100


class SupabasePoolManager:
    """
    High-performance Supabase connection and query manager for dynamic meta-agent.

    Features:
    - Connection pooling and reuse
    - Intelligent query caching with TTL
    - Batch operations for efficiency
    - Query performance monitoring
    - Automatic retry with backoff
    - Pattern-based query optimization
    """

    def __init__(
        self,
        project_id: str,
        anon_key: str = None,
        service_role_key: str = None,
        max_connections: int = 10,
        default_cache_ttl: int = 300,  # 5 minutes
        enable_metrics: bool = True,
    ):
        self.project_id = project_id
        self.anon_key = anon_key
        self.service_role_key = service_role_key
        self.max_connections = max_connections
        self.default_cache_ttl = default_cache_ttl
        self.enable_metrics = enable_metrics

        # Connection pool
        self._connection_pool: List[Client] = []
        self._pool_lock = asyncio.Lock()
        self._active_connections = 0

        # Query cache
        self._query_cache: Dict[str, QueryCacheEntry] = {}
        self._cache_lock = asyncio.Lock()

        # Metrics
        self.metrics = ConnectionMetrics()

        # Logger
        self.logger = logging.getLogger("SupabasePoolManager")

        # Common query patterns (for optimization)
        self._query_patterns = {
            "agents": {
                "get_all_agents": "SELECT * FROM agent_configurations ORDER BY name",
                "get_agent_servers": """
                    SELECT ms.server_name, asm.priority 
                    FROM agent_server_mappings asm 
                    JOIN mcp_servers ms ON asm.server_id = ms.id 
                    WHERE asm.agent_id = $1 AND ms.is_enabled = true 
                    ORDER BY asm.priority DESC
                """,
            },
            "patterns": {
                "get_active_patterns": "SELECT * FROM learning_patterns WHERE is_active = true ORDER BY confidence DESC",
                "update_pattern_usage": """
                    UPDATE learning_patterns 
                    SET usage_count = usage_count + 1, updated_at = NOW() 
                    WHERE name = $1
                """,
            },
            "servers": {
                "find_servers_by_keywords": """
                    SELECT * FROM mcp_servers 
                    WHERE is_enabled = true 
                    AND (server_name ILIKE ANY($1) OR display_name ILIKE ANY($1))
                    ORDER BY priority ASC
                """,
                "get_server_config": "SELECT * FROM mcp_servers WHERE server_name = $1 AND is_enabled = true",
            },
            "workflows": {
                "get_workflows_by_trigger": """
                    SELECT * FROM workflows 
                    WHERE is_active = true 
                    AND trigger_patterns && $1 
                    ORDER BY usage_count DESC, success_rate DESC
                """,
            },
            "user_contexts": {
                "get_user_context": """
                    SELECT * FROM user_contexts 
                    WHERE user_id = $1 
                    ORDER BY updated_at DESC 
                    LIMIT 1
                """,
            },
        }

        self.logger.info(f"🔧 Initialized SupabasePoolManager for project {project_id}")

    async def initialize(self):
        """Initialize the connection pool"""
        if not self.anon_key and not self.service_role_key:
            raise ValueError(
                "At least one of anon_key or service_role_key must be provided"
            )

        # Create initial connections
        for i in range(min(3, self.max_connections)):  # Start with 3 connections
            await self._create_connection()

        self.logger.info(
            f"✅ Connection pool initialized with {len(self._connection_pool)} connections"
        )

    async def _create_connection(self) -> Client:
        """Create a new Supabase client connection"""
        url = f"https://{self.project_id}.supabase.co"

        # Prefer service role key for internal operations, fallback to anon key
        key = self.service_role_key or self.anon_key

        if not key:
            raise ValueError("No valid Supabase key available")

        client = create_client(url, key)
        self._connection_pool.append(client)
        self.logger.debug(
            f"🔗 Created new connection (pool size: {len(self._connection_pool)})"
        )
        return client

    @asynccontextmanager
    async def get_connection(self):
        """Get a connection from the pool (context manager)"""
        async with self._pool_lock:
            if not self._connection_pool:
                await self._create_connection()

            connection = self._connection_pool.pop(0)
            self._active_connections += 1
            self.metrics.active_connections = self._active_connections

        try:
            yield connection
        finally:
            async with self._pool_lock:
                self._connection_pool.append(connection)
                self._active_connections -= 1
                self.metrics.active_connections = self._active_connections

    def _generate_cache_key(self, table: str, query: str, params: Tuple = None) -> str:
        """Generate cache key for query"""
        import hashlib

        cache_data = f"{table}:{query}:{str(params) if params else ''}"
        return hashlib.md5(cache_data.encode()).hexdigest()

    async def _get_from_cache(self, cache_key: str) -> Optional[Any]:
        """Get data from cache if valid"""
        async with self._cache_lock:
            if cache_key in self._query_cache:
                entry = self._query_cache[cache_key]
                if datetime.now() - entry.timestamp < timedelta(
                    seconds=entry.ttl_seconds
                ):
                    self.metrics.cache_hits += 1
                    self.logger.debug(f"📦 Cache HIT: {cache_key[:8]}...")
                    return entry.data
                else:
                    # Expired entry
                    del self._query_cache[cache_key]

        self.metrics.cache_misses += 1
        self.logger.debug(f"📦 Cache MISS: {cache_key[:8]}...")
        return None

    async def _store_in_cache(self, cache_key: str, data: Any, ttl_seconds: int = None):
        """Store data in cache"""
        ttl = ttl_seconds or self.default_cache_ttl

        async with self._cache_lock:
            self._query_cache[cache_key] = QueryCacheEntry(
                data=data,
                timestamp=datetime.now(),
                ttl_seconds=ttl,
                query_hash=cache_key,
            )

            # Clean up old entries (keep cache manageable)
            if len(self._query_cache) > 1000:
                await self._cleanup_cache()

    async def _cleanup_cache(self):
        """Clean up expired cache entries"""
        now = datetime.now()
        expired_keys = []

        for key, entry in self._query_cache.items():
            if now - entry.timestamp >= timedelta(seconds=entry.ttl_seconds):
                expired_keys.append(key)

        for key in expired_keys:
            del self._query_cache[key]

        self.logger.debug(f"🧹 Cleaned up {len(expired_keys)} expired cache entries")

    async def query_with_cache(
        self,
        table: str,
        query_type: str = "select",
        filters: Dict = None,
        columns: str = "*",
        limit: int = None,
        order: str = None,
        cache_ttl: int = None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """
        Execute query with intelligent caching

        Args:
            table: Table name
            query_type: 'select', 'insert', 'update', 'delete'
            filters: WHERE conditions as dict
            columns: Columns to select
            limit: LIMIT clause
            order: ORDER BY clause
            cache_ttl: Cache TTL in seconds
            use_cache: Whether to use caching
        """
        start_time = time.time()

        # Generate cache key for SELECT queries
        cache_key = None
        if query_type == "select" and use_cache:
            cache_key = self._generate_cache_key(
                table, f"{columns}:{str(filters)}:{limit}:{order}"
            )

            # Check cache first
            cached_result = await self._get_from_cache(cache_key)
            if cached_result is not None:
                return cached_result

        # Execute query
        async with self.get_connection() as client:
            try:
                query = client.table(table)

                # Apply query parameters
                if query_type == "select":
                    if columns != "*":
                        query = query.select(columns)
                    else:
                        query = query.select("*")

                    if filters:
                        for field, value in filters.items():
                            if isinstance(value, list):
                                query = query.in_(field, value)
                            elif isinstance(value, dict) and "op" in value:
                                # Advanced operators like {'op': 'gte', 'value': 100}
                                op = value["op"]
                                val = value["value"]
                                if op == "gte":
                                    query = query.gte(field, val)
                                elif op == "lte":
                                    query = query.lte(field, val)
                                elif op == "gt":
                                    query = query.gt(field, val)
                                elif op == "lt":
                                    query = query.lt(field, val)
                                elif op == "like":
                                    query = query.like(field, val)
                                elif op == "ilike":
                                    query = query.ilike(field, val)
                            else:
                                query = query.eq(field, value)

                    if order:
                        query = query.order(order)
                    if limit:
                        query = query.limit(limit)

                    response = query.execute()

                elif query_type == "insert":
                    response = query.insert(filters).execute()
                elif query_type == "update":
                    update_data = filters.pop("_update_data", {})
                    response = query.update(update_data)
                    for field, value in filters.items():
                        response = response.eq(field, value)
                    response = response.execute()
                elif query_type == "delete":
                    response = query.delete()
                    for field, value in filters.items():
                        response = response.eq(field, value)
                    response = response.execute()
                else:
                    raise ValueError(f"Unsupported query_type: {query_type}")

                # Process response
                result = {
                    "success": True,
                    "data": response.data,
                    "count": len(response.data) if response.data else 0,
                    "query_time": time.time() - start_time,
                }

                # Cache SELECT results
                if query_type == "select" and use_cache and cache_key:
                    await self._store_in_cache(cache_key, result, cache_ttl)

                # Update metrics
                self.metrics.total_queries += 1
                self.metrics.avg_query_time = (
                    self.metrics.avg_query_time * (self.metrics.total_queries - 1)
                    + result["query_time"]
                ) / self.metrics.total_queries

                return result

            except Exception as e:
                self.logger.error(f"❌ Query error on {table}: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "data": None,
                    "count": 0,
                    "query_time": time.time() - start_time,
                }

    async def execute_raw_sql(
        self,
        sql: str,
        params: List = None,
        cache_ttl: int = None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """Execute raw SQL with caching support"""
        start_time = time.time()

        # Generate cache key for cacheable queries
        cache_key = None
        if use_cache and sql.strip().upper().startswith("SELECT"):
            cache_key = self._generate_cache_key(
                "raw_sql", sql, tuple(params) if params else None
            )
            cached_result = await self._get_from_cache(cache_key)
            if cached_result is not None:
                return cached_result

        # Execute query
        async with self.get_connection() as client:
            try:
                # Use RPC for raw SQL execution
                if params:
                    response = client.rpc(
                        "execute_sql", {"query": sql, "params": params}
                    ).execute()
                else:
                    response = client.rpc("execute_sql", {"query": sql}).execute()

                result = {
                    "success": True,
                    "data": response.data,
                    "count": len(response.data) if response.data else 0,
                    "query_time": time.time() - start_time,
                }

                # Cache SELECT results
                if use_cache and cache_key:
                    await self._store_in_cache(cache_key, result, cache_ttl)

                self.metrics.total_queries += 1
                return result

            except Exception as e:
                self.logger.error(f"❌ Raw SQL error: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "data": None,
                    "count": 0,
                    "query_time": time.time() - start_time,
                }

    async def batch_operations(self, operations: List[Dict]) -> List[Dict[str, Any]]:
        """
        Execute multiple operations in batch for efficiency

        Args:
            operations: List of operation dicts with keys:
                - table: table name
                - query_type: 'select', 'insert', 'update', 'delete'
                - filters: query filters
                - other query parameters
        """
        results = []

        # Group operations by type for potential optimization
        async with self.get_connection() as client:
            for operation in operations:
                try:
                    result = await self.query_with_cache(**operation)
                    results.append(result)
                except Exception as e:
                    results.append(
                        {"success": False, "error": str(e), "operation": operation}
                    )

        return results

    # Dynamic Architecture Specific Methods

    async def get_all_dynamic_agents(self) -> Dict[str, Any]:
        """Get all agents with their dynamic server assignments (optimized)"""
        return await self.query_with_cache(
            table="agent_configurations",
            columns="*",
            cache_ttl=600,  # 10 minutes cache
            order="name",
        )

    async def find_servers_by_keywords(self, keywords: List[str]) -> Dict[str, Any]:
        """Find MCP servers by keywords (optimized with cache)"""
        # Convert keywords to ILIKE patterns
        patterns = [f"%{keyword}%" for keyword in keywords]

        return await self.execute_raw_sql(
            sql="""
                SELECT * FROM mcp_servers 
                WHERE is_enabled = true 
                AND (server_name ILIKE ANY($1) OR display_name ILIKE ANY($1) OR description ILIKE ANY($1))
                ORDER BY priority ASC, created_at DESC
            """,
            params=[patterns],
            cache_ttl=300,  # 5 minutes cache
            use_cache=True,
        )

    async def get_learning_patterns_optimized(self) -> Dict[str, Any]:
        """Get active learning patterns with optimized caching"""
        return await self.query_with_cache(
            table="learning_patterns",
            filters={"is_active": True},
            order="confidence.desc,usage_count.desc",
            cache_ttl=600,  # 10 minutes cache
        )

    async def update_pattern_usage_batch(
        self, pattern_names: List[str]
    ) -> List[Dict[str, Any]]:
        """Update multiple pattern usage counts in batch"""
        operations = []
        for pattern_name in pattern_names:
            operations.append(
                {
                    "table": "learning_patterns",
                    "query_type": "update",
                    "filters": {
                        "name": pattern_name,
                        "_update_data": {
                            "usage_count": "usage_count + 1",
                            "updated_at": "NOW()",
                        },
                    },
                    "use_cache": False,  # Don't cache updates
                }
            )

        return await self.batch_operations(operations)

    async def get_user_context_fast(self, user_id: str) -> Dict[str, Any]:
        """Get user context with fast caching"""
        return await self.query_with_cache(
            table="user_contexts",
            filters={"user_id": user_id},
            order="updated_at.desc",
            limit=1,
            cache_ttl=900,  # 15 minutes cache
        )

    async def get_agent_servers_optimized(
        self, agent_id: int, user_context: Dict = None
    ) -> Dict[str, Any]:
        """Get agent servers with context-aware filtering"""
        user_org = user_context.get("organization") if user_context else None

        sql = """
            SELECT DISTINCT ms.server_name, asm.priority, ms.description, ms.transport, ms.url
            FROM agent_server_mappings asm 
            JOIN mcp_servers ms ON asm.server_id = ms.id 
            WHERE asm.agent_id = $1 
            AND ms.is_enabled = true 
            AND (
                asm.context_filter = '{}'::jsonb 
                OR (asm.context_filter->>'organization' = $2 OR $2 IS NULL)
            )
            ORDER BY asm.priority DESC, ms.priority ASC
        """

        return await self.execute_raw_sql(
            sql=sql,
            params=[agent_id, user_org],
            cache_ttl=300,  # 5 minutes cache
        )

    async def invalidate_cache_pattern(self, pattern: str):
        """Invalidate cache entries matching pattern"""
        async with self._cache_lock:
            keys_to_remove = []
            for key, entry in self._query_cache.items():
                if pattern in entry.query_hash:
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                del self._query_cache[key]

            self.logger.info(
                f"🗑️ Invalidated {len(keys_to_remove)} cache entries matching '{pattern}'"
            )

    # ===== WORKFLOW EXECUTION METHODS =====

    async def get_workflows_by_trigger(
        self, trigger_words: List[str]
    ) -> Dict[str, Any]:
        """Get workflows that match trigger patterns (optimized with cache)"""
        try:
            # Use cached query pattern for workflow triggers
            return await self.execute_raw_sql(
                sql="""
                    SELECT w.*, 
                           CASE 
                               WHEN w.usage_count > 0 THEN w.success_count::float / w.usage_count 
                               ELSE 0.0 
                           END as success_rate
                    FROM workflows w
                    WHERE w.is_active = true 
                    AND EXISTS (
                        SELECT 1 
                        FROM unnest(w.trigger_patterns) AS pattern
                        WHERE LOWER(pattern) ~ ANY($1)
                        OR EXISTS (
                            SELECT 1 
                            FROM unnest(string_to_array(LOWER(pattern), ' ')) AS pattern_word
                            WHERE pattern_word = ANY($2)
                        )
                    )
                    ORDER BY w.usage_count DESC, w.success_count DESC
                """,
                params=[
                    [
                        f"\\b{word.lower()}\\b" for word in trigger_words
                    ],  # Regex patterns
                    [word.lower() for word in trigger_words],  # Exact word matches
                ],
                cache_ttl=180,  # 3 minutes cache (workflows change less frequently)
                use_cache=True,
            )
        except Exception as e:
            self.logger.error(f"Error getting workflows by trigger: {e}")
            return {"success": False, "error": str(e), "data": []}

    async def update_workflow_usage(
        self, workflow_id: str, success: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Update workflow usage statistics"""
        try:
            if success is True:
                # Successful execution
                update_data = {
                    "usage_count": "usage_count + 1",
                    "success_count": "success_count + 1",
                    "last_executed_at": "NOW()",
                    "updated_at": "NOW()",
                }
            elif success is False:
                # Failed execution
                update_data = {
                    "usage_count": "usage_count + 1",
                    "failure_count": "failure_count + 1",
                    "last_executed_at": "NOW()",
                    "updated_at": "NOW()",
                }
            else:
                # Pre-execution tracking (just mark as started)
                update_data = {"last_executed_at": "NOW()", "updated_at": "NOW()"}

            return await self.execute_raw_sql(
                sql=f"""
                    UPDATE workflows 
                    SET {", ".join([f"{k} = {v}" for k, v in update_data.items()])}
                    WHERE id = $1
                    RETURNING *
                """,
                params=[workflow_id],
                use_cache=False,  # Don't cache updates
            )

        except Exception as e:
            self.logger.error(f"Error updating workflow usage: {e}")
            return {"success": False, "error": str(e)}

    async def get_active_workflows(self) -> Dict[str, Any]:
        """Get all active workflows (cached)"""
        return await self.query_with_cache(
            table="workflows",
            filters={"is_active": True},
            order="usage_count.desc,success_count.desc",
            cache_ttl=300,  # 5 minutes cache
        )

    async def get_workflow_by_id(self, workflow_id: str) -> Dict[str, Any]:
        """Get specific workflow by ID (cached)"""
        return await self.query_with_cache(
            table="workflows",
            filters={"id": workflow_id},
            cache_ttl=600,  # 10 minutes cache
            limit=1,
        )

    async def get_workflow_execution_history(
        self, workflow_id: str, limit: int = 10
    ) -> Dict[str, Any]:
        """Get workflow execution history (cached)"""
        return await self.execute_raw_sql(
            sql="""
                SELECT 
                    we.*,
                    w.name as workflow_name,
                    w.type as workflow_type
                FROM workflow_executions we
                JOIN workflows w ON we.workflow_id = w.id
                WHERE we.workflow_id = $1
                ORDER BY we.executed_at DESC
                LIMIT $2
            """,
            params=[workflow_id, limit],
            cache_ttl=60,  # 1 minute cache for execution history
            use_cache=True,
        )

    # ===== CREWAI INTEGRATION METHODS =====

    async def get_crew_configs_by_trigger(
        self, trigger_words: List[str]
    ) -> Dict[str, Any]:
        """Get CrewAI crew configurations that match trigger patterns"""
        try:
            return await self.execute_raw_sql(
                sql="""
                    SELECT cc.*, 
                           CASE 
                               WHEN cc.usage_count > 0 THEN cc.success_count::float / cc.usage_count 
                               ELSE 0.0 
                           END as success_rate
                    FROM crew_configs cc
                    WHERE cc.is_active = true 
                    AND EXISTS (
                        SELECT 1 
                        FROM unnest(cc.trigger_patterns) AS pattern
                        WHERE LOWER(pattern) ~ ANY($1)
                        OR EXISTS (
                            SELECT 1 
                            FROM unnest(string_to_array(LOWER(pattern), ' ')) AS pattern_word
                            WHERE pattern_word = ANY($2)
                        )
                    )
                    ORDER BY cc.usage_count DESC, cc.success_count DESC
                """,
                params=[
                    [f"\\b{word.lower()}\\b" for word in trigger_words],
                    [word.lower() for word in trigger_words],
                ],
                cache_ttl=180,  # 3 minutes cache
                use_cache=True,
            )
        except Exception as e:
            self.logger.error(f"Error getting crew configs by trigger: {e}")
            return {"success": False, "error": str(e), "data": []}

    async def update_crew_usage(
        self, crew_id: str, success: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Update crew configuration usage and success statistics"""
        try:
            update_data = {
                "usage_count": "usage_count + 1",  # Will be handled as raw SQL
                "last_used": datetime.utcnow().isoformat(),
            }

            if success is not None:
                if success:
                    update_data["success_count"] = "success_count + 1"
                else:
                    update_data["failure_count"] = "failure_count + 1"

                # Update success rate
                update_data["success_rate"] = """
                    CASE 
                        WHEN (success_count + failure_count + 1) > 0 
                        THEN ROUND((success_count::float + {}) / (success_count + failure_count + 1) * 100, 2)
                        ELSE 0 
                    END
                """.format(1 if success else 0)

            return await self.execute_raw_sql(
                sql="""
                    UPDATE crew_configs 
                    SET usage_count = usage_count + 1,
                        last_used = NOW(),
                        success_count = CASE WHEN $2 = true THEN success_count + 1 ELSE success_count END,
                        failure_count = CASE WHEN $2 = false THEN failure_count + 1 ELSE failure_count END,
                        success_rate = CASE 
                            WHEN (success_count + failure_count + 1) > 0 
                            THEN ROUND((success_count + CASE WHEN $2 = true THEN 1 ELSE 0 END)::float / (success_count + failure_count + 1) * 100, 2)
                            ELSE 0 
                        END
                    WHERE id = $1
                    RETURNING *
                """,
                params=[crew_id, success],
                use_cache=False,
            )

        except Exception as e:
            self.logger.error(f"Failed to update crew usage: {e}")
            return {"success": False, "error": str(e)}

    async def get_crew_config_by_id(self, crew_id: str) -> Dict[str, Any]:
        """Get crew configuration by ID"""
        return await self.query_with_cache(
            table="crew_configs",
            filters={"id": crew_id, "is_active": True},
            cache_ttl=300,  # 5 minutes cache
        )

    async def get_crew_configs_by_name(self, crew_name: str) -> Dict[str, Any]:
        """Get crew configurations by name"""
        return await self.query_with_cache(
            table="crew_configs",
            filters={"name": crew_name, "is_active": True},
            cache_ttl=300,  # 5 minutes cache
            order="created_at.desc",
        )

    async def get_all_active_crew_configs(self) -> Dict[str, Any]:
        """Get all active crew configurations"""
        return await self.query_with_cache(
            table="crew_configs",
            filters={"is_active": True},
            cache_ttl=600,  # 10 minutes cache
            order="name",
        )

    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get connection pool and query performance metrics"""
        return {
            "connection_pool": {
                "total_connections": len(self._connection_pool),
                "active_connections": self._active_connections,
                "max_connections": self.max_connections,
                "pool_utilization": (self._active_connections / self.max_connections)
                * 100,
            },
            "query_performance": {
                "total_queries": self.metrics.total_queries,
                "avg_query_time": round(self.metrics.avg_query_time * 1000, 2),  # ms
                "cache_hit_rate": round(self.metrics.cache_hit_rate, 2),  # %
                "cache_entries": len(self._query_cache),
            },
            "cache_breakdown": {
                "cache_hits": self.metrics.cache_hits,
                "cache_misses": self.metrics.cache_misses,
                "total_cached_queries": self.metrics.cache_hits
                + self.metrics.cache_misses,
            },
        }

    async def cleanup(self):
        """Clean up resources"""
        async with self._cache_lock:
            self._query_cache.clear()

        async with self._pool_lock:
            self._connection_pool.clear()

        self.logger.info("🧹 SupabasePoolManager cleaned up")
