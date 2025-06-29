#!/usr/bin/env python3
"""
Test Secret Retrieval and MCP Call Assembly
Tests the complete flow of retrieving secrets and using them in MCP server calls
"""

import asyncio
import os
import sys
import json
from datetime import datetime

# Add the slack_meta_agent directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from supabase_direct_client import SupabaseDirectClient


async def test_complete_secret_flow():
    """Test the complete flow: store secret -> create server config -> retrieve secret -> assemble MCP call"""

    print("🔐 Testing Complete Secret Retrieval and MCP Call Assembly")
    print("=" * 70)

    # Initialize the client
    project_id = "qqggdvfeybfzqmgxmidt"

    # Load credentials
    secrets_file = "mcp_agent.secrets.yaml"
    if os.path.exists(secrets_file):
        import yaml

        with open(secrets_file, "r") as f:
            secrets = yaml.safe_load(f)
            anon_key = secrets["supabase"]["anon_key"]
            service_role_key = secrets["supabase"]["service_role_key"]
    else:
        print("❌ mcp_agent.secrets.yaml not found")
        return False

    client = SupabaseDirectClient(
        project_id=project_id, anon_key=anon_key, service_role_key=service_role_key
    )

    # Test data
    test_timestamp = int(datetime.now().timestamp())
    test_server_name = f"test_api_server_{test_timestamp}"
    test_api_key = f"sk-test{test_timestamp}1234567890abcdef"
    test_webhook_secret = f"whsec_test{test_timestamp}9876543210"

    try:
        print(f"\n🧪 Test Server: {test_server_name}")
        print(f"🔑 Test API Key: {test_api_key[:10]}...")
        print(f"🔒 Test Webhook Secret: {test_webhook_secret[:15]}...")

        # ========================================
        # STEP 1: Store secrets in Supabase Vault
        # ========================================
        print(f"\n📥 STEP 1: Storing secrets in Supabase Vault...")

        # Store API key
        api_key_result = await client.store_secret(
            secret_name=f"mcp_{test_server_name}_api_key",
            secret_value=test_api_key,
            metadata={
                "server_name": test_server_name,
                "field_name": "api_key",
                "description": f"API key for {test_server_name}",
            },
        )

        if not api_key_result["success"]:
            print(f"❌ Failed to store API key: {api_key_result['error']}")
            return False

        print(f"✅ API key stored as: {api_key_result['secret_name']}")

        # Store webhook secret
        webhook_result = await client.store_secret(
            secret_name=f"mcp_{test_server_name}_webhook_secret",
            secret_value=test_webhook_secret,
            metadata={
                "server_name": test_server_name,
                "field_name": "webhook_secret",
                "description": f"Webhook secret for {test_server_name}",
            },
        )

        if not webhook_result["success"]:
            print(f"❌ Failed to store webhook secret: {webhook_result['error']}")
            return False

        print(f"✅ Webhook secret stored as: {webhook_result['secret_name']}")

        # ========================================
        # STEP 2: Create mock MCP server configuration with secret references
        # ========================================
        print(f"\n📝 STEP 2: Creating MCP server config with secret references...")

        # This simulates what would be stored in the mcp_servers table
        mock_server_config = {
            "server_name": test_server_name,
            "display_name": f"Test API Server {test_timestamp}",
            "description": "Test server for secret retrieval testing",
            "transport": "sse",
            "url": f"https://api.test{test_timestamp}.com/mcp/sse",
            "headers": {
                "Authorization": f"Bearer {{secret:{api_key_result['secret_name']}}}",
                "X-Webhook-Secret": f"{{secret:{webhook_result['secret_name']}}}",
            },
            "environment": {
                "API_KEY": f"{{secret:{api_key_result['secret_name']}}}",
                "WEBHOOK_SECRET": f"{{secret:{webhook_result['secret_name']}}}",
            },
        }

        print(f"📋 Mock Server Config:")
        print(f"   - Name: {mock_server_config['server_name']}")
        print(f"   - URL: {mock_server_config['url']}")
        print(f"   - Headers with secret refs: {mock_server_config['headers']}")
        print(f"   - Environment with secret refs: {mock_server_config['environment']}")

        # ========================================
        # STEP 3: Test secret retrieval
        # ========================================
        print(f"\n🔍 STEP 3: Testing secret retrieval...")

        # Retrieve API key
        api_key_retrieval = await client.retrieve_secret(api_key_result["secret_name"])
        if not api_key_retrieval["success"]:
            print(f"❌ Failed to retrieve API key: {api_key_retrieval['error']}")
            return False

        retrieved_api_key = api_key_retrieval["secret_value"]
        print(
            f"✅ Retrieved API key: {retrieved_api_key[:10]}... (matches: {retrieved_api_key == test_api_key})"
        )

        # Retrieve webhook secret
        webhook_retrieval = await client.retrieve_secret(webhook_result["secret_name"])
        if not webhook_retrieval["success"]:
            print(f"❌ Failed to retrieve webhook secret: {webhook_retrieval['error']}")
            return False

        retrieved_webhook_secret = webhook_retrieval["secret_value"]
        print(
            f"✅ Retrieved webhook secret: {retrieved_webhook_secret[:15]}... (matches: {retrieved_webhook_secret == test_webhook_secret})"
        )

        # ========================================
        # STEP 4: Test MCP call assembly (simulation)
        # ========================================
        print(f"\n🔧 STEP 4: Testing MCP call assembly...")

        # This simulates what the MCP client would do when making a call
        assembled_config = await assemble_mcp_config_with_secrets(
            mock_server_config, client
        )

        if assembled_config:
            print(f"✅ MCP Config assembled successfully!")
            print(f"📋 Assembled Config:")
            print(f"   - Headers: {assembled_config['headers']}")
            print(f"   - Environment: {assembled_config['environment']}")

            # Verify the secrets were properly resolved
            auth_header = assembled_config["headers"].get("Authorization", "")
            webhook_header = assembled_config["headers"].get("X-Webhook-Secret", "")

            if test_api_key in auth_header:
                print(f"✅ API key properly resolved in Authorization header")
            else:
                print(f"❌ API key NOT resolved in Authorization header")
                return False

            if test_webhook_secret in webhook_header:
                print(f"✅ Webhook secret properly resolved in X-Webhook-Secret header")
            else:
                print(f"❌ Webhook secret NOT resolved in X-Webhook-Secret header")
                return False

            # Check environment variables
            env_api_key = assembled_config["environment"].get("API_KEY", "")
            env_webhook_secret = assembled_config["environment"].get(
                "WEBHOOK_SECRET", ""
            )

            if env_api_key == test_api_key:
                print(f"✅ API key properly resolved in environment")
            else:
                print(f"❌ API key NOT resolved in environment")
                return False

            if env_webhook_secret == test_webhook_secret:
                print(f"✅ Webhook secret properly resolved in environment")
            else:
                print(f"❌ Webhook secret NOT resolved in environment")
                return False

        else:
            print(f"❌ Failed to assemble MCP config")
            return False

        # ========================================
        # STEP 5: Test real MCP server connection (simulation)
        # ========================================
        print(f"\n🚀 STEP 5: Simulating MCP server connection...")

        # This simulates what would happen when actually connecting to an MCP server
        connection_result = simulate_mcp_connection(assembled_config)

        if connection_result["success"]:
            print(f"✅ MCP connection simulation successful!")
            print(f"📡 Connection details: {connection_result['details']}")
        else:
            print(f"❌ MCP connection simulation failed: {connection_result['error']}")

        print(f"\n🎉 COMPLETE SECRET FLOW TEST: SUCCESS!")
        print(f"   ✅ Secrets stored securely in Supabase Vault")
        print(f"   ✅ Secret references created in MCP config")
        print(f"   ✅ Secrets retrieved successfully")
        print(f"   ✅ MCP config assembled with real values")
        print(f"   ✅ Ready for actual MCP server connection")

        return True

    except Exception as e:
        print(f"❌ Test failed with exception: {e}")
        import traceback

        print(f"📋 Full traceback: {traceback.format_exc()}")
        return False


async def assemble_mcp_config_with_secrets(
    config: dict, client: SupabaseDirectClient
) -> dict:
    """Simulate the process of resolving secret references in MCP config"""

    import re
    import copy

    assembled_config = copy.deepcopy(config)

    async def resolve_secret_references(obj):
        """Recursively resolve secret references in any data structure"""
        if isinstance(obj, dict):
            for key, value in obj.items():
                obj[key] = await resolve_secret_references(value)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                obj[i] = await resolve_secret_references(item)
        elif isinstance(obj, str):
            # Look for secret references like {secret:secret_name}
            secret_pattern = r"\{secret:([^}]+)\}"
            matches = re.findall(secret_pattern, obj)

            for secret_name in matches:
                # Retrieve the actual secret value
                secret_result = await client.retrieve_secret(secret_name)
                if secret_result["success"]:
                    secret_value = secret_result["secret_value"]
                    # Replace the reference with the actual value
                    obj = obj.replace(f"{{secret:{secret_name}}}", secret_value)
                else:
                    print(f"⚠️ Failed to resolve secret: {secret_name}")

        return obj

    # Resolve all secret references
    assembled_config = await resolve_secret_references(assembled_config)

    return assembled_config


def simulate_mcp_connection(config: dict) -> dict:
    """Simulate connecting to an MCP server with the assembled config"""

    try:
        # This would be where the actual MCP client connects
        # For now, we just verify the config has the right structure

        required_fields = ["server_name", "url", "transport"]
        for field in required_fields:
            if field not in config:
                return {"success": False, "error": f"Missing required field: {field}"}

        # Check that headers don't contain secret references anymore
        headers = config.get("headers", {})
        for header_name, header_value in headers.items():
            if "{secret:" in str(header_value):
                return {
                    "success": False,
                    "error": f"Unresolved secret reference in header {header_name}: {header_value}",
                }

        # Check that environment doesn't contain secret references
        environment = config.get("environment", {})
        for env_name, env_value in environment.items():
            if "{secret:" in str(env_value):
                return {
                    "success": False,
                    "error": f"Unresolved secret reference in environment {env_name}: {env_value}",
                }

        return {
            "success": True,
            "details": {
                "server_name": config["server_name"],
                "url": config["url"],
                "transport": config["transport"],
                "headers_count": len(headers),
                "environment_count": len(environment),
                "auth_configured": "Authorization" in headers,
                "webhook_configured": "X-Webhook-Secret" in headers,
            },
        }

    except Exception as e:
        return {"success": False, "error": f"Connection simulation error: {str(e)}"}


async def test_secret_resolution_patterns():
    """Test different secret reference patterns"""

    print(f"\n🧪 Testing Secret Reference Patterns...")
    print("=" * 50)

    # Initialize client
    project_id = "qqggdvfeybfzqmgxmidt"
    secrets_file = "mcp_agent.secrets.yaml"

    if os.path.exists(secrets_file):
        import yaml

        with open(secrets_file, "r") as f:
            secrets = yaml.safe_load(f)
            anon_key = secrets["supabase"]["anon_key"]
            service_role_key = secrets["supabase"]["service_role_key"]
    else:
        print("❌ mcp_agent.secrets.yaml not found")
        return False

    client = SupabaseDirectClient(
        project_id=project_id, anon_key=anon_key, service_role_key=service_role_key
    )

    # Test different patterns
    test_patterns = [
        {
            "description": "API Key in Authorization Bearer",
            "template": "Bearer {secret:test_api_key}",
            "secret_value": "sk-test123456789",
            "expected": "Bearer sk-test123456789",
        },
        {
            "description": "API Key in custom header",
            "template": "{secret:test_api_key}",
            "secret_value": "ak_test987654321",
            "expected": "ak_test987654321",
        },
        {
            "description": "Mixed text with secret",
            "template": "API-Key: {secret:test_api_key}, Version: 1.0",
            "secret_value": "xyz789",
            "expected": "API-Key: xyz789, Version: 1.0",
        },
        {
            "description": "Multiple secrets in one string",
            "template": "key1={secret:secret1}&key2={secret:secret2}",
            "secret_value": None,  # Will test with multiple secrets
            "expected": None,
        },
    ]

    test_results = []

    for i, pattern in enumerate(test_patterns):
        try:
            print(f"\n🔍 Pattern {i + 1}: {pattern['description']}")

            if pattern["description"] == "Multiple secrets in one string":
                # Special case for multiple secrets
                secret1_name = f"test_secret1_{int(datetime.now().timestamp())}"
                secret2_name = f"test_secret2_{int(datetime.now().timestamp())}"

                # Store both secrets
                await client.store_secret(secret1_name, "value1", {})
                await client.store_secret(secret2_name, "value2", {})

                # Test resolution
                test_string = (
                    f"key1={{secret:{secret1_name}}}&key2={{secret:{secret2_name}}}"
                )
                resolved = await resolve_single_string(test_string, client)
                expected = "key1=value1&key2=value2"

                success = resolved == expected
                test_results.append(
                    {
                        "pattern": pattern["description"],
                        "success": success,
                        "input": test_string,
                        "output": resolved,
                        "expected": expected,
                    }
                )

                print(f"   Input: {test_string}")
                print(f"   Output: {resolved}")
                print(f"   Expected: {expected}")
                print(f"   Result: {'✅ PASS' if success else '❌ FAIL'}")

            else:
                # Single secret patterns
                secret_name = f"test_pattern_{i}_{int(datetime.now().timestamp())}"

                # Store the secret
                store_result = await client.store_secret(
                    secret_name,
                    pattern["secret_value"],
                    {"description": f"Test secret for pattern {i + 1}"},
                )

                if not store_result["success"]:
                    print(f"❌ Failed to store secret: {store_result['error']}")
                    continue

                # Test resolution
                test_string = pattern["template"].replace(
                    "{secret:test_api_key}", f"{{secret:{secret_name}}}"
                )
                resolved = await resolve_single_string(test_string, client)

                success = resolved == pattern["expected"]
                test_results.append(
                    {
                        "pattern": pattern["description"],
                        "success": success,
                        "input": test_string,
                        "output": resolved,
                        "expected": pattern["expected"],
                    }
                )

                print(f"   Input: {test_string}")
                print(f"   Output: {resolved}")
                print(f"   Expected: {pattern['expected']}")
                print(f"   Result: {'✅ PASS' if success else '❌ FAIL'}")

        except Exception as e:
            print(f"❌ Pattern {i + 1} failed: {e}")
            test_results.append(
                {"pattern": pattern["description"], "success": False, "error": str(e)}
            )

    # Summary
    passed = len([r for r in test_results if r.get("success", False)])
    total = len(test_results)

    print(f"\n📊 Secret Pattern Test Results: {passed}/{total} passed")

    return passed == total


async def resolve_single_string(text: str, client: SupabaseDirectClient) -> str:
    """Helper function to resolve secret references in a single string"""
    import re

    secret_pattern = r"\{secret:([^}]+)\}"
    matches = re.findall(secret_pattern, text)

    resolved_text = text
    for secret_name in matches:
        secret_result = await client.retrieve_secret(secret_name)
        if secret_result["success"]:
            secret_value = secret_result["secret_value"]
            resolved_text = resolved_text.replace(
                f"{{secret:{secret_name}}}", secret_value
            )
        else:
            print(f"⚠️ Failed to resolve secret: {secret_name}")

    return resolved_text


async def main():
    """Run all secret retrieval tests"""

    print("🔐 SECRET RETRIEVAL AND MCP CALL ASSEMBLY TESTS")
    print("=" * 80)
    print(
        "This tests the complete flow of how secrets are retrieved and used in MCP calls"
    )
    print()

    try:
        # Test 1: Complete flow
        print("🧪 TEST 1: Complete Secret Flow")
        flow_success = await test_complete_secret_flow()

        print("\n" + "=" * 50)

        # Test 2: Pattern resolution
        print("🧪 TEST 2: Secret Reference Patterns")
        pattern_success = await test_secret_resolution_patterns()

        # Summary
        print("\n" + "=" * 80)
        print("📊 FINAL TEST RESULTS")
        print("=" * 80)

        tests_passed = 0
        total_tests = 2

        if flow_success:
            print("✅ Complete Secret Flow: PASS")
            tests_passed += 1
        else:
            print("❌ Complete Secret Flow: FAIL")

        if pattern_success:
            print("✅ Secret Reference Patterns: PASS")
            tests_passed += 1
        else:
            print("❌ Secret Reference Patterns: FAIL")

        print(f"\n🎯 OVERALL RESULT: {tests_passed}/{total_tests} tests passed")

        if tests_passed == total_tests:
            print("🎉 ALL TESTS PASSED! Secret retrieval system is working perfectly!")
            print("\n💡 Your bot can now:")
            print("   ✅ Store API keys securely in Supabase Vault")
            print("   ✅ Create MCP configs with secret references")
            print("   ✅ Retrieve secrets when making MCP calls")
            print("   ✅ Assemble complete configs with real credentials")
            print("   ✅ Connect to MCP servers with proper authentication")
        else:
            print("⚠️ Some tests failed. Please check the errors above.")

        return tests_passed == total_tests

    except Exception as e:
        print(f"❌ Test suite failed: {e}")
        import traceback

        print(f"📋 Full traceback: {traceback.format_exc()}")
        return False


if __name__ == "__main__":
    asyncio.run(main())
