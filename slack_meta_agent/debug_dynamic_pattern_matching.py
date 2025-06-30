#!/usr/bin/env python3
"""
Enhanced Debug Script for Dynamic Pattern Matching

This script tests the new multi-level confidence calculation system,
adaptive thresholds, and real-time learning capabilities.
"""

import sys
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def test_enhanced_pattern_matching():
    """Test the enhanced dynamic pattern matching system"""
    print("🧪 Testing Enhanced Dynamic Pattern Matching System")
    print("=" * 60)

    try:
        # Import the SlackMetaAgent for method testing
        from src.slack_meta_agent.core.meta_agent import SlackMetaAgent

        print("🤖 Initializing SlackMetaAgent...")
        meta_agent = SlackMetaAgent(supabase_project_id="qqggdvfeybfzqmgxmidt")
        print("✅ SlackMetaAgent initialized successfully")

        # Test cases with varying complexity and patterns
        test_cases = [
            # Organization-qualified server test case (NEW - Testing our fix!)
            {
                "message": "Can you use the ARC Supabase to check project status?",
                "expected_pattern": None,  # Should NOT match any pattern - should go to dynamic discovery
                "description": "Organization-qualified server request (ARC Supabase)",
            },
            {
                "message": "Connect to the ARC Supabase database",
                "expected_pattern": None,  # Should NOT match any pattern - should go to dynamic discovery
                "description": "ARC organization Supabase connection request",
            },
            # Feedback test cases (should get higher confidence now)
            {
                "message": "I'd like to give feedback on the system",
                "expected_pattern": "feedback",
                "description": "Direct feedback request",
            },
            {
                "message": "The bot is really slow and confusing",
                "expected_pattern": "feedback",
                "description": "Implicit feedback with sentiment",
            },
            {
                "message": "Can you add a feature to export data?",
                "expected_pattern": "feedback",
                "description": "Feature request",
            },
            {
                "message": "Here's my suggestion: make it faster",
                "expected_pattern": "feedback",
                "description": "Suggestion format",
            },
            {
                "message": "Bug report: the search doesn't work",
                "expected_pattern": "feedback",
                "description": "Bug report",
            },
            # Weather test cases
            {
                "message": "What's the weather like in Austin today?",
                "expected_pattern": "weather",
                "description": "Standard weather query",
            },
            {
                "message": "temperature in New York",
                "expected_pattern": "weather",
                "description": "Temperature query",
            },
            {
                "message": "forecast for tomorrow",
                "expected_pattern": "weather",
                "description": "Forecast request",
            },
            # Capabilities test cases
            {
                "message": "What can you do?",
                "expected_pattern": "capabilities",
                "description": "Direct capability question",
            },
            {
                "message": "What tools do you have access to?",
                "expected_pattern": "capabilities",
                "description": "Tools inquiry",
            },
            {
                "message": "Help me understand your capabilities",
                "expected_pattern": "capabilities",
                "description": "Help request",
            },
            # Edge cases and difficult matches
            {
                "message": "The weather forecast feature needs improvement",
                "expected_pattern": "feedback",  # Should be feedback, not weather
                "description": "Compound topic (weather + feedback)",
            },
            {
                "message": "I think your capability to analyze weather is good",
                "expected_pattern": "feedback",  # Should be feedback, not capabilities/weather
                "description": "Complex feedback about capabilities",
            },
        ]

        results = []

        print("\n📊 Testing Pattern Matching Performance:")
        print("-" * 60)

        for i, test_case in enumerate(test_cases, 1):
            print(f"\n🧪 Test {i}: {test_case['description']}")
            print(f'📝 Message: "{test_case["message"]}"')

            # Test the dynamic pattern matching
            match_result = meta_agent._dynamic_pattern_match(test_case["message"])

            if match_result:
                matched_pattern = match_result["pattern"]
                confidence = match_result["confidence"]
                match_strategy = match_result.get("match_strategy", "unknown")
                all_scores = match_result.get("all_scores", {})
                threshold_used = match_result.get("threshold_used", 0.8)

                # Check if it matched the expected pattern
                success = matched_pattern == test_case["expected_pattern"]
                status = "✅ PASS" if success else "❌ FAIL"

                print(
                    f"{status} Matched: {matched_pattern} (confidence: {confidence:.3f})"
                )
                print(
                    f"   Strategy: {match_strategy} | Threshold: {threshold_used:.3f}"
                )
                print(
                    f"   All scores: {', '.join([f'{k}: {v:.3f}' for k, v in all_scores.items() if v > 0])}"
                )

                results.append(
                    {
                        "test": test_case["description"],
                        "expected": test_case["expected_pattern"],
                        "actual": matched_pattern,
                        "success": success,
                        "confidence": confidence,
                        "strategy": match_strategy,
                        "all_scores": all_scores,
                    }
                )

            else:
                print("❌ FAIL: No pattern matched")
                results.append(
                    {
                        "test": test_case["description"],
                        "expected": test_case["expected_pattern"],
                        "actual": "NO_MATCH",
                        "success": False,
                        "confidence": 0.0,
                        "strategy": "none",
                        "all_scores": {},
                    }
                )

        # Analyze results
        print("\n📈 RESULTS ANALYSIS")
        print("=" * 60)

        successful_tests = [r for r in results if r["success"]]
        success_rate = len(successful_tests) / len(results) * 100

        print(
            f"📊 Overall Success Rate: {len(successful_tests)}/{len(results)} ({success_rate:.1f}%)"
        )

        # Analyze by strategy
        strategy_stats = {}
        for result in successful_tests:
            strategy = result["strategy"]
            if strategy not in strategy_stats:
                strategy_stats[strategy] = {"count": 0, "avg_confidence": 0}
            strategy_stats[strategy]["count"] += 1
            strategy_stats[strategy]["avg_confidence"] += result["confidence"]

        print("\n🎯 Strategy Performance:")
        for strategy, stats in strategy_stats.items():
            avg_conf = stats["avg_confidence"] / stats["count"]
            print(
                f"   {strategy}: {stats['count']} matches, avg confidence: {avg_conf:.3f}"
            )

        # Show failed tests
        failed_tests = [r for r in results if not r["success"]]
        if failed_tests:
            print(f"\n❌ Failed Tests ({len(failed_tests)}):")
            for test in failed_tests:
                print(
                    f"   - {test['test']}: expected {test['expected']}, got {test['actual']}"
                )

        # Test adaptive threshold behavior
        print(f"\n🔧 Testing Adaptive Threshold Behavior:")
        print("-" * 40)

        base_threshold = meta_agent.config_dict["pattern_confidence_threshold"]
        adaptive_threshold = meta_agent._get_adaptive_threshold(base_threshold)

        print(f"📏 Base threshold: {base_threshold}")
        print(f"📏 Adaptive threshold: {adaptive_threshold:.3f}")

        # Show pattern usage statistics
        print(f"\n📚 Pattern Usage Statistics:")
        print("-" * 40)

        for pattern_name, pattern_info in meta_agent.dynamic_patterns.items():
            usage_count = pattern_info["usage_count"]
            confidence = pattern_info["confidence"]
            keyword_count = len(pattern_info["keywords"])

            print(
                f"   {pattern_name}: {usage_count} uses, {confidence:.3f} conf, {keyword_count} keywords"
            )

        # Test learning from successful matches
        print(f"\n🎓 Testing Pattern Learning:")
        print("-" * 40)

        # Simulate learning from a high-confidence feedback match
        learning_message = (
            "Here's my feedback: the system should be more responsive and user-friendly"
        )
        learning_match = meta_agent._dynamic_pattern_match(learning_message)

        if learning_match and learning_match["confidence"] > 0.8:
            print(
                f"✅ Learning opportunity detected: {learning_match['pattern']} pattern"
            )
            print(f'   Message: "{learning_message}"')
            print(f"   Confidence: {learning_match['confidence']:.3f}")

            # Check if new keywords were learned
            original_keywords = len(meta_agent.dynamic_patterns["feedback"]["keywords"])
            meta_agent._learn_from_successful_match(
                "feedback", learning_message, learning_match["confidence"]
            )
            new_keywords = len(meta_agent.dynamic_patterns["feedback"]["keywords"])

            if new_keywords > original_keywords:
                print(f"📚 Learned {new_keywords - original_keywords} new keywords!")
            else:
                print(f"📚 No new keywords learned (pattern already well-established)")

        # Show enhanced scoring breakdown for a complex example
        print(f"\n🔍 Detailed Scoring Analysis:")
        print("-" * 40)

        complex_message = "I think the weather feature could be improved"
        print(f'📝 Analyzing: "{complex_message}"')

        # Test each scoring method individually
        for pattern_name in ["feedback", "weather", "capabilities"]:
            pattern_info = meta_agent.dynamic_patterns[pattern_name]
            scores = meta_agent._calculate_multi_level_confidence(
                complex_message, complex_message.lower(), pattern_info, pattern_name
            )

            max_score = max(scores.values()) if scores.values() else 0
            print(f"\n   🎯 {pattern_name} pattern (max: {max_score:.3f}):")
            for strategy, score in scores.items():
                if score > 0:
                    print(f"      {strategy}: {score:.3f}")

        return {
            "success_rate": success_rate,
            "total_tests": len(results),
            "successful_tests": len(successful_tests),
            "strategy_stats": strategy_stats,
            "adaptive_threshold": adaptive_threshold,
            "base_threshold": base_threshold,
        }

    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback

        print(f"Full traceback: {traceback.format_exc()}")
        return None


def test_fuzzy_matching():
    """Test the fuzzy matching capabilities specifically"""
    print("\n🔍 Testing Fuzzy Matching Capabilities")
    print("=" * 50)

    try:
        from src.slack_meta_agent.core.meta_agent import SlackMetaAgent

        meta_agent = SlackMetaAgent(supabase_project_id="qqggdvfeybfzqmgxmidt")

        fuzzy_test_cases = [
            {
                "message": "Can you give me some feedback on this?",
                "description": "Partial 'give feedback' match",
            },
            {
                "message": "What's the temp outside?",
                "description": "Abbreviated 'temperature' -> 'temp'",
            },
            {
                "message": "Show me your tool capabilities",
                "description": "Compound keyword matching",
            },
        ]

        for test_case in fuzzy_test_cases:
            print(f'\n📝 Testing: "{test_case["message"]}"')
            print(f"   Description: {test_case['description']}")

            match = meta_agent._dynamic_pattern_match(test_case["message"])

            if match:
                print(
                    f"   ✅ Matched: {match['pattern']} (confidence: {match['confidence']:.3f})"
                )
                print(f"   📊 Strategy: {match['match_strategy']}")

                # Show detailed scores
                scores = match.get("all_scores", {})
                print(f"   🔍 Detailed scores:")
                for strategy, score in scores.items():
                    if score > 0:
                        print(f"      {strategy}: {score:.3f}")
            else:
                print(f"   ❌ No match found")

    except Exception as e:
        print(f"❌ Fuzzy matching test error: {e}")


if __name__ == "__main__":
    # Run the enhanced tests
    results = test_enhanced_pattern_matching()

    # Run fuzzy matching tests
    test_fuzzy_matching()

    # Summary
    if results:
        print(f"\n🎉 SUMMARY")
        print("=" * 50)
        print(f"✅ Success Rate: {results['success_rate']:.1f}%")
        print(f"📊 Tests: {results['successful_tests']}/{results['total_tests']}")
        print(
            f"🎯 Adaptive Threshold: {results['adaptive_threshold']:.3f} (base: {results['base_threshold']})"
        )

        if results["success_rate"] >= 85:
            print(f"\n🚀 EXCELLENT: Enhanced pattern matching is working great!")
        elif results["success_rate"] >= 70:
            print(
                f"\n✅ GOOD: Enhanced pattern matching is working well with room for improvement"
            )
        else:
            print(f"\n⚠️ NEEDS WORK: Enhanced pattern matching needs tuning")

        print(f"\n💡 The system now uses:")
        print(
            f"   • Multi-level confidence scoring (exact, fuzzy, contextual, semantic)"
        )
        print(f"   • Adaptive thresholds based on pattern performance")
        print(f"   • Real-time learning from successful matches")
        print(f"   • Contextual analysis for better intent detection")
    else:
        print(f"\n❌ Tests failed to complete")
