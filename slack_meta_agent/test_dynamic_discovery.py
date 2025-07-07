#!/usr/bin/env python3
"""
Test script to use dynamic discovery to find MCP servers in Supabase database.
"""

import asyncio
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)8s | %(name)30s | %(message)s",
    datefmt="%H:%M:%S",
)


async def test_dynamic_discovery():
    """Test the dynamic MCP server discovery system."""

    try:
        # Import the modular system
        from src.slack_meta_agent.main_modular import create_modular_slack_meta_agent

        print("🚀 Creating modular SlackMetaAgent for dynamic discovery...")

        # Create the agent with your Supabase project ID
        agent = await create_modular_slack_meta_agent(
            supabase_project_id="qqggdvfeybfzqmgxmidt",
            config_path="config/mcp_agent.config.yaml",
        )

        print("✅ Agent created successfully!")

        # Test different discovery queries
        test_queries = [
            "find all mcp servers",
            "show me arc supabase servers",
            "list available supabase servers",
            "discover mcp servers with arc",
            "what servers do we have in the database",
        ]

        print("\n🔍 Testing dynamic discovery with various queries...\n")

        for i, query in enumerate(test_queries, 1):
            print(f"{'=' * 60}")
            print(f"Test {i}: {query}")
            print(f"{'=' * 60}")

            try:
                result = await agent.handle_message(
                    message=query, user_id="test_user", channel_id="test_channel"
                )

                print(f"✅ Result:")
                print(result)
                print()

            except Exception as e:
                print(f"❌ Error for query '{query}': {e}")
                print()

        # Test specific keyword discovery
        print(f"{'=' * 60}")
        print("Testing Direct Database Query")
        print(f"{'=' * 60}")

        if agent.orchestrator and agent.orchestrator.db_ops:
            print("🔍 Querying database directly for MCP servers...")

            # Test different keyword combinations
            keyword_tests = [
                ["supabase"],
                ["arc"],
                ["arc", "supabase"],
                ["mcp"],
                [],  # Get all servers
            ]

            for keywords in keyword_tests:
                print(f"\n🎯 Searching for keywords: {keywords or 'ALL SERVERS'}")
                try:
                    servers = await agent.orchestrator.db_ops.discover_mcp_servers(
                        keywords
                    )
                    if servers:
                        print(f"   Found {len(servers)} servers:")
                        for server in servers:
                            print(
                                f"   - {server.get('server_name', 'Unknown')}: {server.get('description', 'No description')}"
                            )
                    else:
                        print("   No servers found")
                except Exception as e:
                    print(f"   Error: {e}")

        # Cleanup
        await agent.cleanup()
        print("\n✅ Dynamic discovery test completed!")

    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_dynamic_discovery())
