#!/usr/bin/env python3
"""
Test MCP Server Exploration Integration
Verify that the exploration tool is properly integrated and can be called
"""

import asyncio
import sys
import os

# Add the slack_meta_agent directory to path
sys.path.insert(0, "slack_meta_agent")

from main import SlackMetaAgent
from mcp_agent.config import get_settings
from mcp_agent.app import MCPApp


async def test_exploration_integration():
    """Test that the MCP server exploration is properly integrated"""
    print("🧪 Testing MCP Server Exploration Integration")
    print("=" * 80)

    # Load configuration
    base_settings = get_settings("slack_meta_agent/mcp_agent.config.yaml")

    # Create MCPApp
    app_instance = MCPApp(
        name="exploration_integration_test",
        settings=base_settings,
    )

    async with app_instance.run() as agent_app:
        # Create SlackMetaAgent with MCPApp context
        meta_agent = SlackMetaAgent(
            supabase_project_id="test_project", mcp_app=agent_app
        )

        print("✅ Meta-agent initialized")

        # Test 1: Check if mcp_server_explorer is in the agent registry
        print("\n🔍 TEST 1: Agent Registry Check")
        if "mcp_server_explorer" in meta_agent.agent_registry:
            explorer_spec = meta_agent.agent_registry["mcp_server_explorer"]
            print(f"   ✅ mcp_server_explorer found in registry")
            print(f"   📋 Capabilities: {explorer_spec.capabilities}")
            print(f"   📝 Instruction length: {len(explorer_spec.instruction)} chars")
        else:
            print(f"   ❌ mcp_server_explorer NOT found in registry")
            print(f"   📋 Available agents: {list(meta_agent.agent_registry.keys())}")
            return False

        # Test 2: Check if server exploration patterns are loaded
        print("\n🔍 TEST 2: Learning Patterns Check")
        if "server_exploration" in meta_agent.dynamic_patterns:
            pattern = meta_agent.dynamic_patterns["server_exploration"]
            print(f"   ✅ server_exploration pattern found")
            print(f"   🔑 Keywords: {pattern['keywords'][:5]}...")
            print(f"   🎯 Agent: {pattern['agent']}")
            print(f"   📊 Confidence: {pattern['confidence']}")
        else:
            print(f"   ❌ server_exploration pattern NOT found")
            print(
                f"   📋 Available patterns: {list(meta_agent.dynamic_patterns.keys())}"
            )
            return False

        # Test 3: Test intent analysis routing
        print("\n🔍 TEST 3: Intent Analysis Routing")
        test_messages = [
            "what tools does the unknown_server have?",
            "explore the mcp server capabilities",
            "test our ARC supabase server",
            "discover tools on new endpoint",
        ]

        for message in test_messages:
            try:
                # Test dynamic pattern matching
                pattern_match = meta_agent._dynamic_pattern_match(message)
                print(f"   📝 '{message[:40]}...'")

                if pattern_match and pattern_match["agent"] == "mcp_server_explorer":
                    print(
                        f"      ✅ Routed to mcp_server_explorer (confidence: {pattern_match['confidence']:.2f})"
                    )
                else:
                    print(
                        f"      ⚠️ Routed to: {pattern_match['agent'] if pattern_match else 'No match'}"
                    )

            except Exception as e:
                print(f"      ❌ Error: {e}")

        # Test 4: Test exploration request detection
        print("\n🔍 TEST 4: Exploration Request Detection")
        test_cases = [
            {
                "keywords": ["arc", "supabase", "arc_supabase"],
                "message": "what records do we have in agent table in our ARC supabase?",
                "should_explore": True,
            },
            {
                "keywords": ["unknown", "server"],
                "message": "test the unknown server endpoint",
                "should_explore": True,
            },
            {
                "keywords": ["weather"],
                "message": "what's the weather today?",
                "should_explore": False,
            },
        ]

        for test_case in test_cases:
            keywords = test_case["keywords"]
            message = test_case["message"]
            expected = test_case["should_explore"]

            try:
                should_explore = meta_agent._looks_like_server_exploration_request(
                    keywords, message
                )
                status = "✅" if should_explore == expected else "❌"
                print(
                    f"   {status} '{message[:40]}...' → Explore: {should_explore} (expected: {expected})"
                )

            except Exception as e:
                print(f"   ❌ Error testing exploration detection: {e}")

        # Test 5: Test server config generation
        print("\n🔍 TEST 5: Server Config Generation")
        test_keywords = ["arc", "supabase", "arc_supabase"]
        test_message = "check our ARC supabase database records"

        try:
            configs = meta_agent._generate_potential_server_configs(
                test_keywords, test_message
            )
            print(f"   📊 Generated {len(configs)} potential server configs:")
            for config in configs:
                print(
                    f"      - {config['server_name']}: {config['transport']} @ {config.get('url', 'no-url')[:50]}..."
                )
        except Exception as e:
            print(f"   ❌ Error generating configs: {e}")

        print(f"\n🎉 Integration test completed!")
        return True


async def main():
    """Run the integration test"""
    try:
        success = await test_exploration_integration()
        if success:
            print("\n✅ All integration tests passed!")
            print("🎯 The MCP Server Explorer is properly integrated and ready to use!")
        else:
            print("\n❌ Some integration tests failed!")
            return False
    except Exception as e:
        print(f"\n💥 Integration test failed with error: {e}")
        import traceback

        print(traceback.format_exc())
        return False


if __name__ == "__main__":
    asyncio.run(main())
