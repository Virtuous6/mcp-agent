#!/usr/bin/env python3
"""
Debug Pattern Matching

This script debugs the pattern matching functionality to see why
feedback patterns aren't being matched correctly.
"""

import sys
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def debug_pattern_matching():
    """Debug the pattern matching functionality"""
    print("🔍 Debug Pattern Matching")
    print("=" * 50)

    try:
        # Import the SlackMetaAgent for method testing
        from src.slack_meta_agent.core.meta_agent import SlackMetaAgent

        print("🤖 Initializing SlackMetaAgent...")
        meta_agent = SlackMetaAgent(supabase_project_id="qqggdvfeybfzqmgxmidt")
        print("✅ SlackMetaAgent initialized successfully")

        # Check what patterns are loaded
        print(f"\n📚 Loaded Patterns: {len(meta_agent.dynamic_patterns)}")
        for pattern_name, pattern_info in meta_agent.dynamic_patterns.items():
            print(
                f"   - {pattern_name}: {len(pattern_info['keywords'])} keywords, confidence: {pattern_info['confidence']}"
            )

        print(
            f"\n🎯 Confidence Threshold: {meta_agent.config_dict['pattern_confidence_threshold']}"
        )

        # Test feedback pattern specifically
        if "feedback" in meta_agent.dynamic_patterns:
            feedback_pattern = meta_agent.dynamic_patterns["feedback"]
            print(f"\n💬 Feedback Pattern Details:")
            print(f"   Agent: {feedback_pattern['agent']}")
            print(f"   Confidence: {feedback_pattern['confidence']}")
            print(
                f"   Keywords: {feedback_pattern['keywords'][:5]}..."
                + (
                    f" (+{len(feedback_pattern['keywords']) - 5} more)"
                    if len(feedback_pattern["keywords"]) > 5
                    else ""
                )
            )

        # Test messages
        test_messages = [
            "I want to give feedback",
            "here's my feedback: great system",
            "feedback: needs improvement",
            "give feedback about the app",
        ]

        print(f"\n🧪 Testing Pattern Matching:")
        print("-" * 40)

        for message in test_messages:
            print(f"\n📝 Testing: '{message}'")

            # Check keyword matches manually
            message_lower = message.lower()
            feedback_keywords = meta_agent.dynamic_patterns["feedback"]["keywords"]

            matched_keywords = []
            for keyword in feedback_keywords:
                if keyword in message_lower:
                    matched_keywords.append(keyword)

            print(f"   Keywords found: {matched_keywords}")

            if matched_keywords:
                # Calculate confidence manually
                match_ratio = len(matched_keywords) / len(feedback_keywords)
                base_confidence = meta_agent.dynamic_patterns["feedback"]["confidence"]
                usage_boost = 1.0  # No usage yet
                calculated_confidence = base_confidence * match_ratio * usage_boost

                print(
                    f"   Match ratio: {len(matched_keywords)}/{len(feedback_keywords)} = {match_ratio:.3f}"
                )
                print(f"   Base confidence: {base_confidence}")
                print(f"   Calculated confidence: {calculated_confidence:.3f}")
                print(
                    f"   Threshold: {meta_agent.config_dict['pattern_confidence_threshold']}"
                )
                print(
                    f"   Above threshold: {'✅' if calculated_confidence >= meta_agent.config_dict['pattern_confidence_threshold'] else '❌'}"
                )

            # Test actual pattern matching
            pattern_match = meta_agent._dynamic_pattern_match(message)

            if pattern_match:
                print(
                    f"   🎯 Pattern Match Result: {pattern_match['agent']} (confidence: {pattern_match['confidence']:.3f})"
                )
            else:
                print(f"   ❌ No pattern match found")

        print(f"\n💡 Debugging Summary:")
        print(
            f"   - Patterns loaded correctly: {'✅' if 'feedback' in meta_agent.dynamic_patterns else '❌'}"
        )
        print(
            f"   - Confidence threshold: {meta_agent.config_dict['pattern_confidence_threshold']}"
        )
        print(
            f"   - Feedback pattern confidence: {meta_agent.dynamic_patterns['feedback']['confidence']}"
        )

        # Test with a simple, obvious feedback message
        simple_test = "feedback"
        print(f"\n🔬 Simple Test: '{simple_test}'")
        simple_match = meta_agent._dynamic_pattern_match(simple_test)
        if simple_match:
            print(
                f"   ✅ Simple match: {simple_match['agent']} (confidence: {simple_match['confidence']:.3f})"
            )
        else:
            print(f"   ❌ Even simple 'feedback' doesn't match")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    debug_pattern_matching()
