#!/usr/bin/env python3
"""
Test script for the feedback collection system

This script tests the complete feedback collection workflow including:
- Pattern detection for feedback keywords
- Feedback parsing and categorization
- Database storage functionality
"""

import asyncio
import sys
import os
import json
from datetime import datetime

# Add the slack_meta_agent directory to Python path
sys.path.append(os.path.dirname(__file__))

from main import SlackMetaAgent
from supabase_direct_client import SupabaseDirectClient


async def test_feedback_pattern_detection():
    """Test the feedback pattern detection logic"""
    print("🧪 Testing Feedback Pattern Detection")
    print("=" * 50)

    # Create a test meta-agent
    meta_agent = SlackMetaAgent(supabase_project_id="qqggdvfeybfzqmgxmidt")

    test_cases = [
        {
            "message": "I'd like to give feedback",
            "expected_has_feedback": False,
            "description": "Intent to give feedback (no content yet)",
        },
        {
            "message": "here is feedback: the system is really helpful",
            "expected_has_feedback": True,
            "description": "Direct feedback with content",
        },
        {
            "message": "I think the bot could be faster",
            "expected_has_feedback": True,
            "description": "Implicit feedback suggestion",
        },
        {
            "message": "bug report: the dashboard doesn't load properly",
            "expected_has_feedback": True,
            "description": "Bug report feedback",
        },
        {
            "message": "feature request: add dark mode please",
            "expected_has_feedback": True,
            "description": "Feature request feedback",
        },
        {
            "message": "what is the weather today?",
            "expected_has_feedback": False,
            "description": "Non-feedback message",
        },
    ]

    results = []
    for test_case in test_cases:
        try:
            feedback_info = meta_agent._parse_feedback_from_message(
                test_case["message"]
            )

            success = (
                feedback_info["has_feedback"] == test_case["expected_has_feedback"]
            )

            result = {
                "message": test_case["message"],
                "description": test_case["description"],
                "expected": test_case["expected_has_feedback"],
                "actual": feedback_info["has_feedback"],
                "category": feedback_info.get("category", "none"),
                "success": success,
            }
            results.append(result)

            status = "✅ PASS" if success else "❌ FAIL"
            print(f"{status} {test_case['description']}")
            print(f"    Message: '{test_case['message']}'")
            print(f"    Expected has_feedback: {test_case['expected_has_feedback']}")
            print(f"    Actual has_feedback: {feedback_info['has_feedback']}")
            if feedback_info["has_feedback"]:
                print(f"    Category: {feedback_info.get('category', 'none')}")
                print(f"    Text: '{feedback_info.get('feedback_text', '')[:50]}...'")
            print()

        except Exception as e:
            print(f"❌ ERROR {test_case['description']}: {e}")
            results.append(
                {
                    "message": test_case["message"],
                    "description": test_case["description"],
                    "success": False,
                    "error": str(e),
                }
            )

    successful = sum(1 for r in results if r.get("success", False))
    total = len(results)
    print(
        f"📊 Pattern Detection Results: {successful}/{total} tests passed ({successful / total * 100:.1f}%)"
    )

    return results


async def test_feedback_categorization():
    """Test the feedback categorization logic"""
    print("\n🏷️ Testing Feedback Categorization")
    print("=" * 50)

    meta_agent = SlackMetaAgent(supabase_project_id="qqggdvfeybfzqmgxmidt")

    test_cases = [
        ("The system crashed when I clicked the button", "bug_report"),
        ("I would like a dark mode feature", "feature_request"),
        ("The interface could be more intuitive", "improvement"),
        ("The app is running slowly", "performance"),
        ("This is confusing to use", "user_experience"),
        ("Great job on the new update!", "positive"),
        ("I have some general thoughts about the system", "general"),
    ]

    results = []
    for feedback_text, expected_category in test_cases:
        try:
            actual_category = meta_agent._categorize_feedback(feedback_text)
            success = actual_category == expected_category

            results.append(
                {
                    "feedback": feedback_text,
                    "expected": expected_category,
                    "actual": actual_category,
                    "success": success,
                }
            )

            status = "✅ PASS" if success else "❌ FAIL"
            print(f"{status} '{feedback_text[:40]}...'")
            print(f"    Expected: {expected_category}")
            print(f"    Actual: {actual_category}")
            print()

        except Exception as e:
            print(f"❌ ERROR categorizing '{feedback_text}': {e}")
            results.append(
                {
                    "feedback": feedback_text,
                    "expected": expected_category,
                    "actual": "error",
                    "success": False,
                    "error": str(e),
                }
            )

    successful = sum(1 for r in results if r.get("success", False))
    total = len(results)
    print(
        f"📊 Categorization Results: {successful}/{total} tests passed ({successful / total * 100:.1f}%)"
    )

    return results


async def test_database_storage():
    """Test the direct database storage functionality"""
    print("\n💾 Testing Database Storage")
    print("=" * 50)

    try:
        # Initialize direct client (same as in main.py)
        import yaml

        secrets_file = "mcp_agent.secrets.yaml"
        if os.path.exists(secrets_file):
            with open(secrets_file, "r") as f:
                secrets = yaml.safe_load(f)
                supabase_config = secrets.get("supabase", {})
                anon_key = supabase_config.get("anon_key")
                service_role_key = supabase_config.get("service_role_key")
        else:
            anon_key = os.getenv("SUPABASE_ANON_KEY")
            service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

        if not (anon_key or service_role_key):
            print("❌ No Supabase credentials found")
            return False

        client = SupabaseDirectClient(
            project_id="qqggdvfeybfzqmgxmidt",
            anon_key=anon_key,
            service_role_key=service_role_key,
        )

        print("✅ Direct client initialized")

        # Test storing feedback
        test_feedback = {
            "user_id": f"test_user_{int(datetime.now().timestamp())}",
            "channel_id": "test_channel",
            "feedback_text": "This is a test feedback from the test script",
            "category": "general",
            "metadata": {
                "test_run": True,
                "timestamp": datetime.now().isoformat(),
                "source": "test_script",
            },
        }

        result = await client.store_feedback(**test_feedback)

        if result["success"]:
            print(f"✅ Feedback stored successfully")
            print(f"    Feedback ID: {result['feedback_id']}")
            print(f"    User: {test_feedback['user_id']}")
            print(f"    Category: {test_feedback['category']}")
            return True
        else:
            print(f"❌ Feedback storage failed: {result['error']}")
            return False

    except Exception as e:
        print(f"❌ Database storage test error: {e}")
        return False


async def test_dynamic_pattern_matching():
    """Test that feedback patterns are properly integrated into dynamic pattern matching"""
    print("\n🎯 Testing Dynamic Pattern Matching Integration")
    print("=" * 50)

    meta_agent = SlackMetaAgent(supabase_project_id="qqggdvfeybfzqmgxmidt")

    # Test feedback pattern detection in dynamic patterns
    feedback_messages = [
        "I'd like to give feedback",
        "here is my feedback about the system",
        "I want to report a bug",
        "feature request for the app",
    ]

    results = []
    for message in feedback_messages:
        try:
            pattern_match = meta_agent._dynamic_pattern_match(message)

            if pattern_match and pattern_match.get("agent") == "feedback_collector":
                results.append(
                    {
                        "message": message,
                        "success": True,
                        "agent": pattern_match["agent"],
                        "confidence": pattern_match["confidence"],
                        "pattern": pattern_match["pattern"],
                    }
                )
                print(
                    f"✅ '{message[:30]}...' -> {pattern_match['agent']} (confidence: {pattern_match['confidence']:.2f})"
                )
            else:
                results.append(
                    {
                        "message": message,
                        "success": False,
                        "agent": pattern_match.get("agent", "none")
                        if pattern_match
                        else "none",
                    }
                )
                print(
                    f"❌ '{message[:30]}...' -> {pattern_match.get('agent', 'none') if pattern_match else 'no match'}"
                )

        except Exception as e:
            print(f"❌ ERROR processing '{message}': {e}")
            results.append({"message": message, "success": False, "error": str(e)})

    successful = sum(1 for r in results if r.get("success", False))
    total = len(results)
    print(
        f"📊 Pattern Matching Results: {successful}/{total} tests passed ({successful / total * 100:.1f}%)"
    )

    return results


async def main():
    """Run all feedback system tests"""
    print("🧪 Feedback System Test Suite")
    print("=" * 60)
    print()

    try:
        # Run all tests
        pattern_results = await test_feedback_pattern_detection()
        categorization_results = await test_feedback_categorization()
        storage_success = await test_database_storage()
        matching_results = await test_dynamic_pattern_matching()

        # Calculate overall results
        pattern_success = sum(
            1 for r in pattern_results if r.get("success", False)
        ) / len(pattern_results)
        categorization_success = sum(
            1 for r in categorization_results if r.get("success", False)
        ) / len(categorization_results)
        matching_success = sum(
            1 for r in matching_results if r.get("success", False)
        ) / len(matching_results)

        print("\n" + "=" * 60)
        print("🎯 OVERALL TEST RESULTS")
        print("=" * 60)
        print(f"Pattern Detection:     {pattern_success * 100:.1f}% success rate")
        print(
            f"Categorization:        {categorization_success * 100:.1f}% success rate"
        )
        print(f"Database Storage:      {'✅ PASS' if storage_success else '❌ FAIL'}")
        print(f"Dynamic Matching:      {matching_success * 100:.1f}% success rate")

        overall_success = (
            (pattern_success + categorization_success + matching_success) / 3 * 100
        )
        storage_weight = 25  # Database storage is critical

        if storage_success and overall_success >= 80:
            print(f"\n🎉 FEEDBACK SYSTEM IS READY FOR USE!")
            print(f"   Overall Score: {overall_success:.1f}%")
            print(f"\n💡 You can now use these commands in Slack:")
            print(f"   • '@meta-agent I'd like to give feedback'")
            print(f"   • '@meta-agent here is feedback: [your feedback]'")
            print(f"   • '@meta-agent bug report: [issue description]'")
            print(f"   • '@meta-agent feature request: [request details]'")
        else:
            print(f"\n⚠️  FEEDBACK SYSTEM NEEDS ATTENTION")
            print(f"   Overall Score: {overall_success:.1f}%")
            if not storage_success:
                print(f"   Critical Issue: Database storage failed")
            print(f"   Please review the failing tests above")

    except Exception as e:
        print(f"\n❌ Test suite error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
