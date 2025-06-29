#!/usr/bin/env python3
"""
User Feedback Workflow Test

This script tests the complete user feedback collection workflow,
simulating how users would actually submit feedback through Slack.
"""

import asyncio
import os
import sys
import json
import yaml
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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


async def test_user_feedback_workflow():
    """Test the complete user feedback workflow"""
    print("🧪 Testing User Feedback Workflow")
    print("=" * 50)

    # Load credentials
    creds = load_supabase_credentials()
    if not creds:
        print("❌ Could not load Supabase credentials")
        return False

    # Test cases for different user feedback scenarios
    test_scenarios = [
        {
            "name": "Direct Feedback",
            "user_message": "here's my feedback: The system works great but could be faster",
            "expected_category": "improvement",
            "description": "User provides feedback directly in their message",
        },
        {
            "name": "Bug Report",
            "user_message": "I found a bug with the dashboard - it's not loading properly",
            "expected_category": "bug_report",
            "description": "User reports a bug",
        },
        {
            "name": "Feature Request",
            "user_message": "Can you add dark mode support? That would be awesome!",
            "expected_category": "feature_request",
            "description": "User requests a new feature",
        },
        {
            "name": "Positive Feedback",
            "user_message": "I love the new updates! Great job on the improvements",
            "expected_category": "positive",
            "description": "User gives positive feedback",
        },
        {
            "name": "Feedback Intent Only",
            "user_message": "I'd like to give feedback",
            "expected_category": "general",
            "description": "User wants to give feedback but hasn't provided it yet",
            "requires_human_input": True,
        },
    ]

    results = []

    try:
        # Import and initialize the SlackMetaAgent
        from src.slack_meta_agent.core.meta_agent import SlackMetaAgent

        print("🤖 Initializing SlackMetaAgent...")

        # Initialize the meta agent with minimal setup
        meta_agent = SlackMetaAgent(supabase_project_id=creds["project_id"])

        # Mock the current context for human input
        meta_agent.current_user_id = "test_user_workflow"
        meta_agent.current_channel_id = "test_channel_workflow"
        meta_agent.current_thread_ts = "1234567890.123456"

        print("✅ SlackMetaAgent initialized successfully")

    except Exception as e:
        print(f"❌ Failed to initialize SlackMetaAgent: {e}")
        import traceback

        traceback.print_exc()
        return False

    # Test feedback parsing
    print("\n🔍 Test 1: Feedback Message Parsing")
    print("-" * 40)

    for scenario in test_scenarios:
        try:
            print(f"\n   📝 Testing: {scenario['name']}")
            print(f"      Message: '{scenario['user_message']}'")

            # Test the feedback parsing method
            feedback_info = meta_agent._parse_feedback_from_message(
                scenario["user_message"]
            )

            print(f"      Has Feedback: {feedback_info['has_feedback']}")
            print(f"      Category: {feedback_info['category']}")

            if feedback_info["has_feedback"]:
                print(
                    f"      Feedback Text: '{feedback_info['feedback_text'][:50]}...'"
                )

                # Verify category matches expectation
                if feedback_info["category"] == scenario["expected_category"]:
                    print(
                        f"      ✅ Category matches expected: {scenario['expected_category']}"
                    )
                    results.append(
                        {
                            "scenario": scenario["name"],
                            "parsing": "success",
                            "category_match": True,
                        }
                    )
                else:
                    print(
                        f"      ⚠️ Category mismatch: got {feedback_info['category']}, expected {scenario['expected_category']}"
                    )
                    results.append(
                        {
                            "scenario": scenario["name"],
                            "parsing": "success",
                            "category_match": False,
                        }
                    )
            else:
                if scenario.get("requires_human_input"):
                    print(f"      ✅ Correctly identified as requiring human input")
                    results.append(
                        {
                            "scenario": scenario["name"],
                            "parsing": "success",
                            "category_match": True,
                        }
                    )
                else:
                    print(f"      ❌ Failed to detect feedback")
                    results.append(
                        {
                            "scenario": scenario["name"],
                            "parsing": "failed",
                            "category_match": False,
                        }
                    )

        except Exception as e:
            print(f"      ❌ Parsing error: {e}")
            results.append(
                {"scenario": scenario["name"], "parsing": "error", "error": str(e)}
            )

    # Test feedback categorization
    print("\n🏷️ Test 2: Feedback Categorization")
    print("-" * 40)

    categorization_tests = [
        {"text": "The app crashes when I click save", "expected": "bug_report"},
        {"text": "Please add email notifications", "expected": "feature_request"},
        {"text": "The dashboard could load faster", "expected": "improvement"},
        {"text": "Amazing work on the latest update!", "expected": "positive"},
        {"text": "Just some general thoughts about the system", "expected": "general"},
    ]

    for test in categorization_tests:
        try:
            category = meta_agent._categorize_feedback(test["text"])
            status = "✅" if category == test["expected"] else "⚠️"
            print(
                f"   {status} '{test['text'][:40]}...' -> {category} (expected: {test['expected']})"
            )

        except Exception as e:
            print(f"   ❌ Categorization error: {e}")

    # Test workflow with database storage (for direct feedback scenarios)
    print("\n💾 Test 3: End-to-End Feedback Storage")
    print("-" * 40)

    direct_feedback_scenarios = [
        s for s in test_scenarios if not s.get("requires_human_input")
    ]
    storage_results = []

    for scenario in direct_feedback_scenarios:
        try:
            print(f"\n   🔄 Testing storage for: {scenario['name']}")

            # Test the feedback collection workflow
            user_id = f"test_user_{scenario['name'].lower().replace(' ', '_')}"
            result = await meta_agent.feedback_collection_workflow(
                scenario["user_message"], user_id
            )

            print(f"      Workflow Result: {result[:100]}...")

            # Check if the result indicates success
            if "✅" in result and "recorded" in result.lower():
                print(f"      ✅ Feedback workflow completed successfully")
                storage_results.append(
                    {"scenario": scenario["name"], "storage": "success"}
                )
            else:
                print(f"      ⚠️ Workflow result unclear")
                storage_results.append(
                    {
                        "scenario": scenario["name"],
                        "storage": "unclear",
                        "result": result,
                    }
                )

        except Exception as e:
            print(f"      ❌ Workflow error: {e}")
            import traceback

            print(f"      Traceback: {traceback.format_exc()}")
            storage_results.append(
                {"scenario": scenario["name"], "storage": "error", "error": str(e)}
            )

    # Test intent detection (should route to feedback_collector agent)
    print("\n🎯 Test 4: Intent Detection for Feedback")
    print("-" * 40)

    feedback_intent_messages = [
        "I want to give feedback",
        "here's my feedback: great system",
        "I'd like to provide some feedback",
        "feedback: needs improvement",
    ]

    intent_results = []

    for message in feedback_intent_messages:
        try:
            # Test dynamic intent analysis
            intent_analysis = await meta_agent._analyze_user_intent_dynamic(message)

            required_agents = intent_analysis.get("required_agents", [])
            intent_name = intent_analysis.get("intent_name", "")

            print(f"   📝 '{message}' -> {required_agents}")

            if "feedback_collector" in required_agents:
                print(f"      ✅ Correctly routed to feedback_collector")
                intent_results.append({"message": message, "routing": "correct"})
            else:
                print(
                    f"      ⚠️ Routed to: {required_agents} (expected: feedback_collector)"
                )
                intent_results.append(
                    {
                        "message": message,
                        "routing": "incorrect",
                        "actual": required_agents,
                    }
                )

        except Exception as e:
            print(f"      ❌ Intent analysis error: {e}")
            intent_results.append(
                {"message": message, "routing": "error", "error": str(e)}
            )

    # Test Summary
    print("\n📊 TEST SUMMARY")
    print("=" * 50)

    # Parsing results
    parsing_success = len([r for r in results if r.get("parsing") == "success"])
    category_matches = len([r for r in results if r.get("category_match")])
    print(f"📝 Feedback Parsing: {parsing_success}/{len(results)} successful")
    print(f"🏷️ Category Accuracy: {category_matches}/{len(results)} correct")

    # Storage results
    if storage_results:
        storage_success = len(
            [r for r in storage_results if r.get("storage") == "success"]
        )
        print(
            f"💾 End-to-End Storage: {storage_success}/{len(storage_results)} successful"
        )

    # Intent routing results
    if intent_results:
        routing_success = len(
            [r for r in intent_results if r.get("routing") == "correct"]
        )
        print(f"🎯 Intent Routing: {routing_success}/{len(intent_results)} correct")

    # Overall assessment
    overall_success = (
        parsing_success >= len(results) * 0.8  # 80% parsing success
        and category_matches >= len(results) * 0.7  # 70% category accuracy
        and (
            not storage_results
            or len([r for r in storage_results if r.get("storage") == "success"]) > 0
        )  # At least one storage success
        and (
            not intent_results
            or len([r for r in intent_results if r.get("routing") == "correct"])
            >= len(intent_results) * 0.8
        )  # 80% routing success
    )

    print(
        f"\n🏆 OVERALL RESULT: {'✅ USER FEEDBACK WORKFLOW WORKING' if overall_success else '❌ NEEDS ATTENTION'}"
    )

    if overall_success:
        print("\n💡 Next Steps:")
        print("   - The feedback workflow is ready for user testing")
        print("   - Try these commands in Slack:")
        print("     • '@meta-agent I'd like to give feedback'")
        print("     • '@meta-agent here's my feedback: [your feedback]'")
        print("     • '@meta-agent I found a bug with [description]'")
        print("   - Check your Supabase dashboard for new feedback entries")
    else:
        print("\n🔧 Issues to address:")
        if parsing_success < len(results) * 0.8:
            print("   - Feedback parsing needs improvement")
        if category_matches < len(results) * 0.7:
            print("   - Category detection accuracy needs work")
        if storage_results and not any(
            r.get("storage") == "success" for r in storage_results
        ):
            print("   - Database storage workflow needs debugging")
        if (
            intent_results
            and len([r for r in intent_results if r.get("routing") == "correct"])
            < len(intent_results) * 0.8
        ):
            print("   - Intent routing to feedback_collector needs adjustment")

    return overall_success


if __name__ == "__main__":
    print("🔬 User Feedback Workflow Test")
    print("=" * 50)

    # Check credentials first
    creds = load_supabase_credentials()
    if not creds:
        print("❌ Cannot run test without Supabase credentials")
        sys.exit(1)

    # Run the async test
    try:
        success = asyncio.run(test_user_feedback_workflow())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⏹️ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n💥 Test failed with exception: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
