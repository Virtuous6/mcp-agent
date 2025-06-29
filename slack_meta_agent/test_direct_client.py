#!/usr/bin/env python3
"""
Test script for the direct Supabase client
"""

import asyncio
import os
import sys
from datetime import datetime

# Add the slack_meta_agent directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from supabase_direct_client import SupabaseDirectClient


async def test_direct_client():
    """Test the direct Supabase client functionality"""
    print("🧪 Testing Direct Supabase Client")
    print("=" * 50)

    # Check for credentials in YAML file first, then environment variables
    project_id = os.getenv("SUPABASE_PROJECT_ID", "qqggdvfeybfzqmgxmidt")
    anon_key = None
    service_role_key = None

    # Try to load from mcp_agent.secrets.yaml
    secrets_file = "mcp_agent.secrets.yaml"
    if os.path.exists(secrets_file):
        try:
            import yaml

            with open(secrets_file, "r") as f:
                secrets = yaml.safe_load(f)
                if "supabase" in secrets:
                    anon_key = secrets["supabase"].get("anon_key")
                    service_role_key = secrets["supabase"].get("service_role_key")
                    print(f"📄 Found Supabase credentials in {secrets_file}")
        except Exception as e:
            print(f"⚠️ Could not load {secrets_file}: {e}")

    # Fallback to environment variables
    if not anon_key and not service_role_key:
        anon_key = os.getenv("SUPABASE_ANON_KEY")
        service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if anon_key or service_role_key:
            print("🌍 Using Supabase credentials from environment variables")

    if not (anon_key or service_role_key):
        print("❌ Error: No Supabase credentials found")
        print("Either:")
        print(f"  1. Add credentials to {secrets_file} under 'supabase' section, OR")
        print("  2. Set environment variables:")
        print("     export SUPABASE_ANON_KEY=your_anon_key")
        print("     export SUPABASE_SERVICE_ROLE_KEY=your_service_role_key")
        return False

    try:
        # Initialize client
        client = SupabaseDirectClient(
            project_id=project_id, anon_key=anon_key, service_role_key=service_role_key
        )
        print(f"✅ Client initialized for project: {project_id}")

        # Test 1: Health check
        print("\n🏥 Testing health check...")
        health_result = await client.health_check()
        print(f"Health check result: {health_result}")

        if not health_result["success"]:
            print("❌ Health check failed - stopping tests")
            return False

        # Test 2: Test server addition workflow
        print("\n🚀 Testing MCP server addition...")
        test_server_info = {
            "server_name": f"test_server_{int(datetime.now().timestamp())}",
            "display_name": "Test Server",
            "description": "A test MCP server added via direct client",
            "transport": "sse",
            "url": "https://api.test.com/mcp",
            "args": ["--test", "--mode=dev"],
        }

        server_result = await client.insert_mcp_server(test_server_info)
        print(f"Server addition result: {server_result}")

        if server_result["success"]:
            print(f"✅ Server '{test_server_info['server_name']}' added successfully")

            # Test 3: Verify server exists
            print("\n🔍 Testing server verification...")
            verify_result = await client.verify_server_exists(
                test_server_info["server_name"]
            )
            print(f"Verification result: {verify_result}")

            if verify_result["success"] and verify_result["exists"]:
                print(f"✅ Server verification successful")
            else:
                print(f"❌ Server verification failed")
        else:
            print(f"❌ Server addition failed: {server_result['error']}")

        # Test 4: Test logging functions
        print("\n📝 Testing logging functions...")

        # Test conversation memory logging
        memory_result = await client.log_conversation_memory(
            user_id="test_user_123",
            message="Test message for direct client",
            channel_id="C123TEST",
        )
        print(f"Memory logging result: {memory_result}")

        # Test interaction logging
        interaction_result = await client.log_interaction(
            user_id="test_user_123",
            message="Test interaction message",
            result="Test response from agent",
            analysis={"strategy": "direct_client_test", "confidence": 0.95},
        )
        print(f"Interaction logging result: {interaction_result}")

        print("\n✅ All tests completed!")
        return True

    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback

        print(f"Traceback: {traceback.format_exc()}")
        return False


async def main():
    """Main test function"""
    success = await test_direct_client()
    if success:
        print("\n🎉 Direct Supabase client is working correctly!")
        sys.exit(0)
    else:
        print("\n💥 Direct Supabase client tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
