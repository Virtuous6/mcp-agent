#!/usr/bin/env python3
"""
Test script for Direct Organization + Tool Lookup

This tests the new cleaner approach where "ARC Supabase" triggers
a direct database lookup instead of complex scoring algorithms.
"""

import sys
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


async def test_organization_tool_lookup():
    """Test the new direct organization + tool lookup system"""
    print("🧪 Testing Direct Organization + Tool Lookup System")
    print("=" * 60)

    try:
        from src.slack_meta_agent.core.meta_agent import SlackMetaAgent

        print("🤖 Initializing SlackMetaAgent...")
        meta_agent = SlackMetaAgent(supabase_project_id="qqggdvfeybfzqmgxmidt")
        print("✅ SlackMetaAgent initialized successfully")

        # Test cases for organization + tool patterns
        test_cases = [
            {
                "message": "Can you use the ARC Supabase to check project status?",
                "expected_keywords": ["arc", "supabase"],
                "description": "ARC Supabase - Primary test case",
            },
            {
                "message": "Connect to the ARC Supabase database",
                "expected_keywords": ["arc", "supabase"],
                "description": "ARC Supabase - Connection request",
            },
            {
                "message": "Show me data from Company X airtable",
                "expected_keywords": ["company", "x", "airtable"],
                "description": "Company X Airtable - Different organization",
            },
            {
                "message": "Use GitHub for this project",
                "expected_keywords": ["github"],
                "description": "GitHub only - No organization (should skip direct lookup)",
            },
            {
                "message": "Connect to our internal supabase",
                "expected_keywords": ["internal", "supabase"],
                "description": "Internal Supabase - Another org example",
            },
        ]

        print("\n🔍 Testing Pattern Detection:")
        print("-" * 40)

        for i, test_case in enumerate(test_cases, 1):
            print(f"\n🧪 Test {i}: {test_case['description']}")
            print(f'📝 Message: "{test_case["message"]}"')

            # Extract keywords from message
            keywords = meta_agent._extract_discovery_keywords_from_message(
                test_case["message"]
            )
            print(f"🔑 Extracted keywords: {keywords}")

            # Test pattern detection
            patterns = meta_agent._detect_organization_tool_patterns(
                keywords, test_case["message"]
            )

            if patterns:
                print(f"✅ Detected {len(patterns)} organization+tool pattern(s):")
                for pattern in patterns:
                    print(
                        f"   - Organization: '{pattern['organization']}', Tool: '{pattern['tool']}'"
                    )
            else:
                print("❌ No organization+tool patterns detected")

            # Test the full direct lookup (this will try database lookup)
            print("🔍 Testing direct lookup...")
            direct_match = await meta_agent._try_direct_organization_tool_lookup(
                keywords, test_case["message"]
            )

            if direct_match:
                print(
                    f"✅ Direct match found: '{direct_match['server_name']}' (score: {direct_match['score']})"
                )
                print(
                    f"   Source: {direct_match['source']}, Type: {direct_match.get('match_type', 'unknown')}"
                )
            else:
                print("ℹ️ No direct match found (expected if server not in database)")

        print("\n🎯 Testing Full Server Resolution Flow:")
        print("-" * 40)

        # Test the complete flow for ARC Supabase
        arc_message = "what projects does the ARC supabase have?"
        print(f'\n📝 Full test message: "{arc_message}"')

        keywords = meta_agent._extract_discovery_keywords_from_message(arc_message)
        print(f"🔑 Keywords: {keywords}")

        # This will test the complete flow: direct lookup first, then fallback
        print("🔄 Testing complete server resolution...")
        best_match = await meta_agent._find_best_server_match(keywords, arc_message)

        if best_match:
            print(f"✅ Final server selected: '{best_match['server_name']}'")
            print(
                f"   Source: {best_match['source']}, Score: {best_match['score']:.2f}"
            )
            if best_match.get("match_type") == "direct_organization_tool":
                print(f"   🎯 SUCCESS: Used direct organization+tool lookup!")
            else:
                print(f"   🔄 Used fallback scoring method")
        else:
            print("❌ No server match found")

        print("\n" + "=" * 60)
        print("🎉 Direct Organization + Tool Lookup Test Complete!")
        print("\n💡 Key Benefits of New Approach:")
        print("   ✅ Predictable: 'ARC Supabase' always looks for ARC's Supabase first")
        print("   ⚡ Fast: Direct database lookup instead of complex scoring")
        print("   🧹 Clean: Simple logic, less technical debt")
        print("   💪 Confident: Users can trust specific tool names work")

    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    import asyncio

    asyncio.run(test_organization_tool_lookup())
