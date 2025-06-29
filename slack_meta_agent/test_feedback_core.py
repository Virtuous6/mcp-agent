#!/usr/bin/env python3
"""
Core Feedback Functionality Test

This script tests the core feedback functionality without MCP dependencies,
focusing on parsing, categorization, and direct database storage.
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
            with open(file_path, "r") as f:
                secrets = yaml.safe_load(f)

            supabase_config = secrets.get("supabase", {})
            if supabase_config.get("url") and supabase_config.get("anon_key"):
                return {
                    "url": supabase_config["url"],
                    "anon_key": supabase_config["anon_key"],
                    "service_role_key": supabase_config.get("service_role_key"),
                    "project_id": "qqggdvfeybfzqmgxmidt",
                }

    return None


async def test_feedback_core_functionality():
    """Test core feedback functionality without MCP dependencies"""
    print("🧪 Testing Core Feedback Functionality")
    print("=" * 50)

    # Load credentials
    creds = load_supabase_credentials()
    if not creds:
        print("❌ Could not load Supabase credentials")
        return False

    try:
        # Import the SlackMetaAgent for method testing
        from src.slack_meta_agent.core.meta_agent import SlackMetaAgent

        print("🤖 Initializing SlackMetaAgent (lightweight)...")

        # Initialize with minimal setup
        meta_agent = SlackMetaAgent(supabase_project_id=creds["project_id"])

        print("✅ SlackMetaAgent initialized successfully")

    except Exception as e:
        print(f"❌ Failed to initialize SlackMetaAgent: {e}")
        return False

    # Test 1: Feedback Message Parsing
    print("\n🔍 Test 1: Core Feedback Message Parsing")
    print("-" * 40)

    test_messages = [
        {
            "message": "here's my feedback: The system works great but could be faster",
            "should_detect": True,
            "expected_category": ["improvement", "performance"],  # Either is acceptable
        },
        {
            "message": "I found a bug with the dashboard - it's not loading properly",
            "should_detect": True,
            "expected_category": ["bug_report"],
        },
        {
            "message": "Can you add dark mode support? That would be awesome!",
            "should_detect": False,  # This might not be detected as direct feedback
            "expected_category": ["feature_request"],
        },
        {
            "message": "feedback: Amazing work on the latest update!",
            "should_detect": True,
            "expected_category": ["positive", "general"],
        },
        {
            "message": "I'd like to give feedback",
            "should_detect": False,  # Intent only, no actual feedback yet
            "expected_category": ["general"],
        },
    ]

    parsing_results = []

    for i, test in enumerate(test_messages):
        try:
            print(f"\n   📝 Test {i + 1}: '{test['message'][:50]}...'")

            feedback_info = meta_agent._parse_feedback_from_message(test["message"])

            # Check detection
            detected = feedback_info["has_feedback"]
            should_detect = test["should_detect"]
            detection_correct = detected == should_detect

            print(
                f"      Detected: {detected} (expected: {should_detect}) {'✅' if detection_correct else '❌'}"
            )

            if detected:
                category = feedback_info["category"]
                category_correct = category in test["expected_category"]
                print(
                    f"      Category: {category} (expected: {test['expected_category']}) {'✅' if category_correct else '⚠️'}"
                )
                print(f"      Text: '{feedback_info['feedback_text'][:60]}...'")

                parsing_results.append(
                    {
                        "test": f"Test {i + 1}",
                        "detection": "correct" if detection_correct else "incorrect",
                        "category": "correct" if category_correct else "incorrect",
                    }
                )
            else:
                print(f"      Category: {feedback_info['category']}")
                parsing_results.append(
                    {
                        "test": f"Test {i + 1}",
                        "detection": "correct" if detection_correct else "incorrect",
                        "category": "n/a",
                    }
                )

        except Exception as e:
            print(f"      ❌ Error: {e}")
            parsing_results.append(
                {
                    "test": f"Test {i + 1}",
                    "detection": "error",
                    "category": "error",
                    "error": str(e),
                }
            )

    # Test 2: Feedback Categorization
    print("\n🏷️ Test 2: Feedback Categorization Engine")
    print("-" * 40)

    categorization_tests = [
        {"text": "The app crashes when I click save", "expected": "bug_report"},
        {"text": "Please add email notifications", "expected": "feature_request"},
        {
            "text": "The dashboard loads too slowly",
            "expected": ["improvement", "performance"],
        },
        {"text": "Amazing work on the latest update!", "expected": "positive"},
        {"text": "Just some general thoughts", "expected": "general"},
        {
            "text": "It's confusing how to use this feature",
            "expected": "user_experience",
        },
        {
            "text": "Error message appears when uploading files",
            "expected": "bug_report",
        },
    ]

    categorization_results = []

    for test in categorization_tests:
        try:
            category = meta_agent._categorize_feedback(test["text"])
            expected = test["expected"]

            if isinstance(expected, list):
                correct = category in expected
                expected_display = " or ".join(expected)
            else:
                correct = category == expected
                expected_display = expected

            status = "✅" if correct else "⚠️"
            print(
                f"   {status} '{test['text'][:40]}...' -> {category} (expected: {expected_display})"
            )

            categorization_results.append(
                {
                    "text": test["text"][:40],
                    "result": "correct" if correct else "incorrect",
                    "actual": category,
                    "expected": expected,
                }
            )

        except Exception as e:
            print(f"   ❌ Error categorizing '{test['text'][:40]}...': {e}")
            categorization_results.append(
                {"text": test["text"][:40], "result": "error", "error": str(e)}
            )

    # Test 3: Direct Database Storage (bypassing MCP)
    print("\n💾 Test 3: Direct Database Storage")
    print("-" * 40)

    storage_results = []

    try:
        from src.slack_meta_agent.database.supabase_client import SupabaseDirectClient

        # Initialize direct client
        direct_client = SupabaseDirectClient(
            project_id=creds["project_id"],
            anon_key=creds["anon_key"],
            service_role_key=creds["service_role_key"],
        )

        # Test feedback storage
        test_feedback_data = {
            "user_id": "test_user_core_functionality",
            "channel_id": "test_channel_core",
            "feedback_text": "This is a test of the core feedback functionality - direct storage test",
            "category": "general",
            "metadata": {
                "test_type": "core_functionality_test",
                "timestamp": datetime.now().isoformat(),
                "automated_test": True,
            },
        }

        print("   💾 Testing direct database storage...")
        result = await direct_client.store_feedback(**test_feedback_data)

        if result["success"]:
            print(
                f"   ✅ Direct storage SUCCESS: Feedback ID {result.get('feedback_id')}"
            )
            storage_results.append(
                {
                    "method": "direct",
                    "result": "success",
                    "id": result.get("feedback_id"),
                }
            )
        else:
            print(f"   ❌ Direct storage FAILED: {result.get('error')}")
            storage_results.append(
                {"method": "direct", "result": "failed", "error": result.get("error")}
            )

    except Exception as e:
        print(f"   ❌ Storage test error: {e}")
        storage_results.append({"method": "direct", "result": "error", "error": str(e)})

    # Test 4: Intent Pattern Matching
    print("\n🎯 Test 4: Intent Pattern Matching")
    print("-" * 40)

    intent_test_messages = [
        "I want to give feedback",
        "here's my feedback: great system",
        "I'd like to provide some feedback",
        "feedback: needs improvement",
        "give feedback about the app",
        "my feedback is that it works well",
    ]

    pattern_results = []

    for message in intent_test_messages:
        try:
            # Test the pattern matching
            pattern_match = meta_agent._dynamic_pattern_match(message)

            if pattern_match and pattern_match.get("agent") == "feedback_collector":
                print(
                    f"   ✅ '{message}' -> feedback_collector (confidence: {pattern_match.get('confidence', 0):.2f})"
                )
                pattern_results.append({"message": message, "result": "correct"})
            else:
                agent = pattern_match.get("agent", "none") if pattern_match else "none"
                print(f"   ⚠️ '{message}' -> {agent} (expected: feedback_collector)")
                pattern_results.append(
                    {"message": message, "result": "incorrect", "actual": agent}
                )

        except Exception as e:
            print(f"   ❌ Pattern matching error for '{message}': {e}")
            pattern_results.append(
                {"message": message, "result": "error", "error": str(e)}
            )

    # Summary
    print("\n📊 TEST SUMMARY")
    print("=" * 50)

    # Calculate success rates
    parsing_correct = len(
        [r for r in parsing_results if r.get("detection") == "correct"]
    )
    parsing_total = len(parsing_results)

    categorization_correct = len(
        [r for r in categorization_results if r.get("result") == "correct"]
    )
    categorization_total = len(categorization_results)

    storage_successful = len(
        [r for r in storage_results if r.get("result") == "success"]
    )
    storage_total = len(storage_results)

    pattern_correct = len([r for r in pattern_results if r.get("result") == "correct"])
    pattern_total = len(pattern_results)

    print(
        f"🔍 Message Parsing: {parsing_correct}/{parsing_total} correct ({parsing_correct / parsing_total * 100:.1f}%)"
    )
    print(
        f"🏷️ Categorization: {categorization_correct}/{categorization_total} correct ({categorization_correct / categorization_total * 100:.1f}%)"
    )
    print(f"💾 Database Storage: {storage_successful}/{storage_total} successful")
    print(
        f"🎯 Pattern Matching: {pattern_correct}/{pattern_total} correct ({pattern_correct / pattern_total * 100:.1f}%)"
    )

    # Overall assessment
    overall_success = (
        parsing_correct >= parsing_total * 0.7  # 70% parsing accuracy
        and categorization_correct
        >= categorization_total * 0.6  # 60% categorization (it's tricky)
        and storage_successful > 0  # At least database works
        and pattern_correct >= pattern_total * 0.8  # 80% pattern matching
    )

    print(
        f"\n🏆 OVERALL RESULT: {'✅ CORE FUNCTIONALITY WORKING' if overall_success else '❌ NEEDS IMPROVEMENT'}"
    )

    if overall_success:
        print("\n💡 Core Functionality Assessment:")
        print("   ✅ Feedback detection and parsing working")
        print("   ✅ Database storage operational")
        print("   ✅ Intent routing functional")
        print("   ✅ Ready for integration testing")
        print("\n🚀 Next Steps:")
        print("   1. Test via actual Slack interface:")
        print("      • '@meta-agent I'd like to give feedback'")
        print("      • '@meta-agent here's my feedback: [your feedback]'")
        print("   2. Verify end-to-end workflow in production Slack environment")
        print("   3. Check Supabase dashboard for new feedback entries")
    else:
        print("\n🔧 Areas Needing Attention:")
        if parsing_correct < parsing_total * 0.7:
            print("   - Feedback message parsing needs improvement")
        if categorization_correct < categorization_total * 0.6:
            print("   - Category detection could be enhanced")
        if storage_successful == 0:
            print("   - Database storage needs debugging")
        if pattern_correct < pattern_total * 0.8:
            print("   - Pattern matching needs tuning")

    return overall_success


if __name__ == "__main__":
    print("🔬 Core Feedback Functionality Test")
    print("=" * 50)

    # Check credentials first
    creds = load_supabase_credentials()
    if not creds:
        print("❌ Cannot run test without Supabase credentials")
        sys.exit(1)

    # Run the async test
    try:
        success = asyncio.run(test_feedback_core_functionality())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⏹️ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n💥 Test failed with exception: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
