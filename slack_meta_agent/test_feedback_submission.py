#!/usr/bin/env python3
"""
Standalone test for Supabase feedback table submission

This script tests if we can successfully submit feedback data to the Supabase
feedback table using both the direct client and MCP fallback methods.
"""

import asyncio
import os
import sys
import json
import yaml
from datetime import datetime
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def load_supabase_credentials():
    """Load Supabase credentials from secrets file"""
    secrets_files = [
        "config/mcp_agent.secrets.yaml",
        "mcp_agent.secrets.yaml",
        "mcp-agent.secrets.yaml",
    ]

    for secrets_file in secrets_files:
        file_path = Path(secrets_file)
        if file_path.exists():
            print(f"   📄 Loading from: {secrets_file}")
            with open(file_path, "r") as f:
                secrets = yaml.safe_load(f)

            supabase_config = secrets.get("supabase", {})
            if supabase_config.get("url") and supabase_config.get("anon_key"):
                return {
                    "url": supabase_config["url"],
                    "anon_key": supabase_config["anon_key"],
                    "service_role_key": supabase_config.get("service_role_key"),
                    "project_id": "qqggdvfeybfzqmgxmidt",  # Extract from URL or config
                }

    return None


async def test_feedback_submission():
    """Test feedback submission to Supabase"""
    print("🧪 Testing Feedback Submission to Supabase")
    print("=" * 50)

    # Load credentials
    creds = load_supabase_credentials()
    if not creds:
        print("❌ Could not load Supabase credentials from secrets file")
        return False

    print(f"   ✅ Loaded credentials for project: {creds['project_id']}")
    print(f"   ✅ URL: {creds['url']}")
    print(f"   ✅ Anon key: {creds['anon_key'][:20]}...")
    print(
        f"   ✅ Service role key: {'Available' if creds['service_role_key'] else 'Not available'}"
    )

    # Test data
    test_feedback = {
        "user_id": "test_user_feedback_123",
        "channel_id": "test_channel_feedback",
        "feedback_text": "This is a test feedback submission to verify the feedback table is working correctly.",
        "category": "bug_report",
        "metadata": {
            "test_run": True,
            "timestamp": datetime.now().isoformat(),
            "test_source": "feedback_submission_test",
            "message_length": 89,
            "collection_method": "direct_test",
        },
    }

    results = []

    # Test 1: Direct Supabase Client
    print("\n🔐 Test 1: Direct Supabase Client")
    print("-" * 30)

    try:
        from src.slack_meta_agent.database.supabase_client import SupabaseDirectClient

        # Initialize direct client with credentials
        direct_client = SupabaseDirectClient(
            project_id=creds["project_id"],
            anon_key=creds["anon_key"],
            service_role_key=creds["service_role_key"],
        )

        # Test direct client health
        print("   🩺 Testing Supabase connection...")
        health_result = await direct_client.health_check()
        print(f"   Health Check: {health_result}")

        if health_result["success"]:
            print("   💾 Testing feedback submission...")
            # Test feedback submission
            result = await direct_client.store_feedback(**test_feedback)

            if result["success"]:
                print(
                    f"   ✅ Direct Client SUCCESS: Feedback ID {result.get('feedback_id', 'unknown')}"
                )
                print(f"      Stored data: {result.get('data', {})}")
                results.append(
                    {"method": "direct_client", "success": True, "details": result}
                )
            else:
                print(
                    f"   ❌ Direct Client FAILED: {result.get('error', 'Unknown error')}"
                )
                results.append(
                    {
                        "method": "direct_client",
                        "success": False,
                        "error": result.get("error"),
                    }
                )
        else:
            print(
                f"   ❌ Direct Client HEALTH CHECK FAILED: {health_result.get('error', 'Unknown error')}"
            )
            results.append(
                {
                    "method": "direct_client",
                    "success": False,
                    "error": "Health check failed",
                }
            )

    except Exception as e:
        print(f"   ❌ Direct Client EXCEPTION: {e}")
        import traceback

        print(f"   📊 Traceback: {traceback.format_exc()}")
        results.append({"method": "direct_client", "success": False, "error": str(e)})

    # Test 2: Different feedback categories (if Test 1 succeeded)
    if any(r["success"] for r in results):
        print("\n📝 Test 2: Different Feedback Categories")
        print("-" * 30)

        test_categories = [
            {"category": "feature_request", "text": "Please add dark mode support"},
            {"category": "positive", "text": "Great job on the latest update!"},
            {"category": "improvement", "text": "The dashboard could load faster"},
            {"category": "general", "text": "Just wanted to share some thoughts"},
        ]

        category_results = []

        try:
            direct_client = SupabaseDirectClient(
                project_id=creds["project_id"],
                anon_key=creds["anon_key"],
                service_role_key=creds["service_role_key"],
            )

            for i, test_case in enumerate(test_categories):
                category_feedback = {
                    **test_feedback,
                    "user_id": f"test_user_category_{i}",
                    "feedback_text": test_case["text"],
                    "category": test_case["category"],
                    "metadata": {
                        **test_feedback["metadata"],
                        "category_test": True,
                        "test_case_index": i,
                    },
                }

                result = await direct_client.store_feedback(**category_feedback)

                if result["success"]:
                    print(
                        f"   ✅ {test_case['category'].upper()}: Feedback ID {result.get('feedback_id', 'unknown')}"
                    )
                    category_results.append(
                        {"category": test_case["category"], "success": True}
                    )
                else:
                    print(
                        f"   ❌ {test_case['category'].upper()}: {result.get('error', 'Unknown error')}"
                    )
                    category_results.append(
                        {
                            "category": test_case["category"],
                            "success": False,
                            "error": result.get("error"),
                        }
                    )

        except Exception as e:
            print(f"   ❌ Category Testing EXCEPTION: {e}")

    # Test 3: Table structure verification
    print("\n🔍 Test 3: Feedback Table Structure Verification")
    print("-" * 30)

    try:
        direct_client = SupabaseDirectClient(
            project_id=creds["project_id"],
            anon_key=creds["anon_key"],
            service_role_key=creds["service_role_key"],
        )

        # Try to query the feedback table to see its structure
        try:
            # Try a simple query to see if table exists and what columns are available
            import aiohttp

            url = f"{creds['url']}/rest/v1/feedback?limit=1"
            headers = {
                "apikey": creds["anon_key"],
                "Authorization": f"Bearer {creds['anon_key']}",
                "Content-Type": "application/json",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        print(f"   ✅ Feedback table exists and is accessible")
                        print(f"   📊 Sample query returned {len(data)} record(s)")
                        if data:
                            print(f"   🔍 Available columns: {list(data[0].keys())}")
                    elif response.status == 404:
                        print(f"   ❌ Feedback table does not exist (404)")
                    else:
                        error_text = await response.text()
                        print(
                            f"   ⚠️ Table query failed: {response.status} - {error_text}"
                        )

        except Exception as table_error:
            print(f"   ❌ Table verification error: {table_error}")

    except Exception as e:
        print(f"   ❌ Structure verification EXCEPTION: {e}")

    # Summary
    print("\n📊 TEST SUMMARY")
    print("=" * 50)

    successful_methods = [r for r in results if r["success"]]
    failed_methods = [r for r in results if not r["success"]]

    print(f"✅ Successful Methods: {len(successful_methods)}/{len(results)}")
    for success in successful_methods:
        print(f"   - {success['method']}: ✅")

    if failed_methods:
        print(f"\n❌ Failed Methods: {len(failed_methods)}/{len(results)}")
        for failure in failed_methods:
            print(
                f"   - {failure['method']}: ❌ {failure.get('error', 'Unknown error')}"
            )

    if "category_results" in locals() and category_results:
        successful_categories = [r for r in category_results if r["success"]]
        print(
            f"\n📝 Category Tests: {len(successful_categories)}/{len(category_results)} successful"
        )
        for cat_result in category_results:
            status = "✅" if cat_result["success"] else "❌"
            print(f"   - {cat_result['category']}: {status}")

    # Overall result
    overall_success = len(successful_methods) > 0
    print(
        f"\n🏆 OVERALL RESULT: {'✅ FEEDBACK SYSTEM WORKING' if overall_success else '❌ FEEDBACK SYSTEM NEEDS ATTENTION'}"
    )

    if overall_success:
        print("\n💡 Next Steps:")
        print("   - Feedback table is accessible and working")
        print("   - You can safely use the feedback collection workflow")
        print("   - Consider testing the full workflow from Slack")
        print("   - Try: '@meta-agent I'd like to give feedback' in Slack")
    else:
        print("\n🔧 Troubleshooting Steps:")
        print("   - Check if feedback table exists in your Supabase project")
        print("   - Verify table schema matches expected format")
        print("   - Check row-level security (RLS) policies")
        print("   - Review error messages above for specific issues")

    return overall_success


def check_credentials():
    """Check if Supabase credentials are available"""
    print("🔍 Checking Supabase Credentials...")

    creds = load_supabase_credentials()
    return creds is not None


if __name__ == "__main__":
    print("🔬 Supabase Feedback Table Test")
    print("=" * 50)

    # Check credentials first
    if not check_credentials():
        print("\n❌ Cannot run test without Supabase credentials")
        print(
            "   Please ensure mcp_agent.secrets.yaml exists with supabase configuration"
        )
        sys.exit(1)

    # Run the async test
    try:
        success = asyncio.run(test_feedback_submission())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⏹️ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n💥 Test failed with exception: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
