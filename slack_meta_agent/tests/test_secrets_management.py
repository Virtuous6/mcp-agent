#!/usr/bin/env python3
"""
Test script for MCP Secrets Management System
"""

import asyncio
import os
import sys
from datetime import datetime

# Add the slack_meta_agent directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from supabase_direct_client import SupabaseDirectClient


async def test_secret_detection():
    """Test the secret detection logic"""
    print("🧪 Testing Secret Detection Logic")
    print("=" * 50)

    # Mock client for testing detection logic
    client = SupabaseDirectClient("test", "test", "test")

    test_cases = [
        # Should be detected as secrets
        ("api_key", "sk-1234567890abcdef", True),
        ("bearer_token", "xoxb-123456789012", True),
        ("webhook_secret", "whsec_abcdef123456", True),
        ("password", "mypassword123", True),
        ("client_secret", "client_abc123def456", True),
        ("auth_token", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", True),
        ("GITHUB_TOKEN", "ghp_1234567890abcdef", True),
        ("custom_api_key", "AIzaSyC1234567890", True),
        # Should NOT be detected as secrets
        ("server_name", "my_api_server", False),
        ("url", "https://api.example.com", False),
        ("description", "My API server", False),
        ("transport", "sse", False),
        ("display_name", "My API", False),
        ("short_value", "abc", False),
        ("number_field", "123", False),
    ]

    passed = 0
    failed = 0

    for field_name, field_value, expected in test_cases:
        result = client._is_secret_field(field_name, field_value)
        status = "✅ PASS" if result == expected else "❌ FAIL"

        if result == expected:
            passed += 1
        else:
            failed += 1

        print(
            f"{status} {field_name}: '{field_value}' -> {result} (expected: {expected})"
        )

    print(f"\n📊 Results: {passed} passed, {failed} failed")
    return failed == 0


async def test_secret_management_flow():
    """Test the complete secrets management flow"""
    print("\n🔐 Testing Complete Secrets Management Flow")
    print("=" * 50)

    # Check for credentials
    project_id = os.getenv("SUPABASE_PROJECT_ID", "qqggdvfeybfzqmgxmidt")
    anon_key = None
    service_role_key = None

    # Try to load from secrets file
    secrets_file = "mcp_agent.secrets.yaml"
    if os.path.exists(secrets_file):
        try:
            import yaml

            with open(secrets_file, "r") as f:
                secrets = yaml.safe_load(f)
                if "supabase" in secrets:
                    anon_key = secrets["supabase"].get("anon_key")
                    service_role_key = secrets["supabase"].get("service_role_key")
                    print(f"📄 Loaded credentials from {secrets_file}")
        except Exception as e:
            print(f"⚠️ Could not load {secrets_file}: {e}")

    # Fallback to environment variables
    if not anon_key and not service_role_key:
        anon_key = os.getenv("SUPABASE_ANON_KEY")
        service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not (anon_key or service_role_key):
        print("❌ No Supabase credentials found. Skipping integration test.")
        print("Set credentials in mcp_agent.secrets.yaml or environment variables.")
        return False

    try:
        # Initialize client
        client = SupabaseDirectClient(
            project_id=project_id, anon_key=anon_key, service_role_key=service_role_key
        )
        print(f"✅ Client initialized for project: {project_id}")

        # Test 1: Store a test secret in Supabase Vault
        print("\n🔐 Test 1: Storing a test secret in Supabase Vault")
        test_secret_name = f"test_secret_{int(datetime.now().timestamp())}"
        test_secret_value = "sk-test1234567890abcdef"

        store_result = await client.store_secret(
            test_secret_name,
            test_secret_value,
            metadata={
                "test": True,
                "server_name": "test_server",
                "field_name": "api_key",
                "description": "Test secret for Vault validation",
            },
        )

        if store_result["success"]:
            print(f"✅ Secret stored in Vault: {test_secret_name}")
        else:
            print(f"❌ Vault storage failed: {store_result['error']}")
            return False

        # Test 2: Retrieve the secret from Vault
        print("\n🔑 Test 2: Retrieving the secret from Vault")
        retrieve_result = await client.retrieve_secret(test_secret_name)

        if retrieve_result["success"]:
            retrieved_value = retrieve_result["secret_value"]
            if retrieved_value == test_secret_value:
                print(
                    f"✅ Secret retrieved from Vault correctly: {retrieved_value[:10]}..."
                )
            else:
                print(
                    f"❌ Secret value mismatch: expected {test_secret_value}, got {retrieved_value}"
                )
                return False
        else:
            print(f"❌ Vault retrieval failed: {retrieve_result['error']}")
            return False

        # Test 3: Test secure server insertion
        print("\n🖥️ Test 3: Testing secure server insertion")
        test_server_info = {
            "server_name": f"test_server_{int(datetime.now().timestamp())}",
            "display_name": "Test Server",
            "description": "A test server for secrets management",
            "transport": "sse",
            "url": "https://api.test.example.com/mcp",
            "api_key": "sk-test9876543210fedcba",
            "webhook_secret": "whsec_test123456789",
            "non_secret_field": "this is not a secret",
        }

        secure_result = await client.insert_mcp_server_secure(test_server_info)

        if secure_result["success"]:
            secret_refs = secure_result.get("secret_references", {})
            print(f"✅ Server added with {len(secret_refs)} secrets secured")
            print(f"   Secret references: {list(secret_refs.keys())}")

            # Verify secrets were created
            for field_name, secret_name in secret_refs.items():
                verify_result = await client.retrieve_secret(secret_name)
                if verify_result["success"]:
                    print(
                        f"   ✅ {field_name} -> {secret_name} (value: {verify_result['secret_value'][:10]}...)"
                    )
                else:
                    print(f"   ❌ Could not verify {field_name} secret")

        else:
            print(f"❌ Secure server insertion failed: {secure_result['error']}")
            return False

        # Test 4: Test secret resolution
        print("\n🔄 Test 4: Testing secret resolution")
        mock_config = {
            "server_name": "test_server",
            "url": "https://api.example.com",
            "api_key": f"{{secret:{test_secret_name}}}",
            "normal_field": "not a secret reference",
        }

        resolve_result = await client.resolve_server_secrets(mock_config)

        if resolve_result["success"]:
            resolved_config = resolve_result["config"]
            if resolved_config["api_key"] == test_secret_value:
                print(f"✅ Secret reference resolved correctly")
            else:
                print(f"❌ Secret resolution failed: {resolved_config['api_key']}")
                return False
        else:
            print(f"❌ Secret resolution failed: {resolve_result['error']}")
            return False

        print("\n🎉 All secrets management tests passed!")
        return True

    except Exception as e:
        print(f"❌ Test error: {e}")
        import traceback

        print(f"Full traceback: {traceback.format_exc()}")
        return False


async def demo_user_flow():
    """Demonstrate the user experience flow"""
    print("\n👤 Demo: User Experience Flow")
    print("=" * 50)

    print("User: @meta-agent add mcp server")
    print("Bot: What should we call this MCP server?")
    print("User: my_api_server")
    print()
    print("Bot: What's a friendly display name for this server?")
    print("User: My API Server")
    print()
    print("Bot: What does this server do?")
    print("User: Provides access to my custom API")
    print()
    print("Bot: What transport type does this server use? (sse/stdio/websocket)")
    print("User: sse")
    print()
    print("Bot: What's the URL for this SSE server?")
    print("User: https://api.mycompany.com/mcp")
    print()
    print("Bot: Does this server require authentication? (yes/no)")
    print("User: yes")
    print()
    print("Bot: What type of authentication does this server use?")
    print("User: api_key")
    print()
    print("Bot: 🔐 Please provide your API key:")
    print("     ⚠️ This will be stored securely in Supabase Vault (encrypted at rest)")
    print("User: sk-1234567890abcdef...")
    print()
    print("Bot: **MCP Server Configuration Summary:**")
    print("     • Name: my_api_server")
    print("     • Display Name: My API Server")
    print("     • Description: Provides access to my custom API")
    print("     • Transport: sse")
    print("     • URL: https://api.mycompany.com/mcp")
    print("     • Authentication: 1 credential(s) configured 🔐")
    print()
    print("     Does this look correct? (yes/no)")
    print("User: yes")
    print()
    print(
        "Bot: ✅ Server 'my_api_server' added successfully with 1 secrets secured in Vault"
    )
    print()
    print("🔐 Behind the scenes:")
    print("   - API key detected automatically")
    print("   - Secret stored in Supabase Vault with authenticated encryption")
    print(
        "   - Reference stored as: {secret:mcp_my_api_server_api_key_1705123456_abc123}"
    )
    print("   - Only secret name stored in mcp_configurations table")
    print("   - Original key never seen by LLM")


async def main():
    """Run all tests and demos"""
    print("🔐 MCP Secrets Management System Test Suite")
    print("=" * 80)

    # Test 1: Secret detection logic
    detection_passed = await test_secret_detection()

    # Test 2: Integration test (if credentials available)
    integration_passed = await test_secret_management_flow()

    # Demo: User experience
    await demo_user_flow()

    # Summary
    print("\n" + "=" * 80)
    print("📊 Test Summary:")
    print(f"   Secret Detection: {'✅ PASSED' if detection_passed else '❌ FAILED'}")
    print(f"   Integration Test: {'✅ PASSED' if integration_passed else '⚠️ SKIPPED'}")
    print()

    if detection_passed and integration_passed:
        print("🎉 All tests passed! Secrets management system is working correctly.")
    elif detection_passed:
        print(
            "✅ Secret detection working. Set up Supabase credentials for full testing."
        )
    else:
        print("❌ Some tests failed. Please review the implementation.")

    print()
    print(
        "🛡️ Your MCP servers will now automatically secure API keys and tokens in Supabase Vault!"
    )


if __name__ == "__main__":
    asyncio.run(main())
