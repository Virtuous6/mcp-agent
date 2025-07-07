"""
Supabase Specialist Agent - Optimized for efficient database queries.
"""

from typing import Dict, Any, List
from ..core.types import AgentComponent
from ..utils.context_optimizer import context_optimizer


class SupabaseSpecialistAgent(AgentComponent):
    """Specialized agent for efficient Supabase operations."""

    def __init__(self, pool_manager=None, mcp_app=None):
        super().__init__("SupabaseSpecialist")
        self.pool_manager = pool_manager
        self.mcp_app = mcp_app
        self.query_optimizations = {
            "table_listing": "SELECT table_name, table_schema FROM information_schema.tables WHERE table_schema = 'public' LIMIT 20",
            "table_summary": "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = 'public' ORDER BY table_name LIMIT 50",
            "row_counts": "SELECT schemaname, tablename, n_tup_ins as row_count FROM pg_stat_user_tables LIMIT 20",
        }

    async def get_optimized_table_summary(self) -> str:
        """Get a concise summary of available tables without overwhelming data."""

        try:
            # Get basic table information
            if self.mcp_app:
                # Use MCP to get table list efficiently
                agent = await self.pool_manager.get_agent(
                    "data_researcher", "table_summary"
                )

                async with agent:
                    prompt = """
                    Use Supabase MCP to get a SUMMARY of available tables. 
                    
                    IMPORTANT: 
                    - Only list table names and basic info
                    - Do NOT retrieve full table contents
                    - Use list_tables tool to get table names only
                    - If you must show data, limit to 5 rows maximum per table
                    
                    Provide a concise summary of what tables are available.
                    """

                    result = await agent.run(prompt)

                    # Apply context optimization
                    if result and len(result) > 20000:  # ~5k tokens
                        self.logger.warning(
                            "Applying context optimization to table summary"
                        )
                        result = context_optimizer.create_summary_for_large_data(
                            result, "Supabase Tables"
                        )

                    return result

            return "Unable to access Supabase - MCP app not available"

        except Exception as e:
            self.logger.error(f"Error getting table summary: {e}")
            return f"Error retrieving table information: {str(e)}"

    async def get_specific_table_info(self, table_name: str, limit: int = 5) -> str:
        """Get information about a specific table with row limits."""

        try:
            if self.mcp_app:
                agent = await self.pool_manager.get_agent(
                    "data_researcher", f"table_{table_name}"
                )

                async with agent:
                    prompt = f"""
                    Get information about the '{table_name}' table in Supabase.
                    
                    IMPORTANT:
                    - Show table structure (columns and types)
                    - Show only {limit} sample rows maximum
                    - Do NOT retrieve all data
                    - Focus on structure and sample data only
                    
                    Table: {table_name}
                    """

                    result = await agent.run(prompt)

                    # Apply context optimization
                    if result and len(result) > 15000:  # ~3.5k tokens
                        result = context_optimizer.truncate_large_responses(
                            result, max_tokens=3500
                        )

                    return result

            return f"Unable to access table '{table_name}' - MCP app not available"

        except Exception as e:
            self.logger.error(f"Error getting table info for {table_name}: {e}")
            return f"Error retrieving table '{table_name}': {str(e)}"

    def create_optimized_instructions(self) -> str:
        """Create optimized instructions for Supabase queries."""
        return """
        When working with Supabase:
        
        1. ALWAYS limit data retrieval:
           - Use LIMIT clauses (max 10-20 rows for demos)
           - Request table schemas before full data
           - Focus on structure over content
        
        2. For table listings:
           - Use list_tables tool first
           - Only show table names and basic metadata
           - Don't automatically fetch all table contents
        
        3. For specific queries:
           - Ask what specific information the user wants
           - Limit results to relevant data only
           - Provide summaries for large datasets
        
        4. Optimize for token usage:
           - Summarize large results
           - Show samples rather than full datasets
           - Focus on answering the specific question asked
        """
