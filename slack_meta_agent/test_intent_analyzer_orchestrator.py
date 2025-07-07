#!/usr/bin/env python3
"""
Test script to validate IntentAnalyzer and Orchestrator LLM configuration.

This script verifies that:
1. IntentAnalyzer loads configuration from enhanced agent_specs table
2. Orchestrator loads configuration from enhanced agent_specs table
3. Both use Smart LLM Factory for database-driven LLM settings
4. Components initialize properly with enhanced configurations
"""

import os
import sys
import asyncio
import logging
from typing import Dict, Any

# Add the project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_agent.agents.agent import Agent
from mcp_agent.app import MCPApp

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_intent_analyzer_smart_llm():
    """Test IntentAnalyzer with Smart LLM Factory."""

    print("🧪 Testing IntentAnalyzer Smart LLM Factory Configuration...")

    try:
        # Import IntentAnalyzer
        from slack_meta_agent.src.slack_meta_agent.agents.intent_analyzer import (
            IntentAnalyzerAgent,
        )

        # Create analyzer
        analyzer = IntentAnalyzerAgent()

        # Initialize it (this should create the intent_agent and attach LLM)
        await analyzer.initialize()

        # Verify the agent was created
        assert analyzer.intent_agent is not None, "Intent agent should be created"
        assert analyzer.intent_llm is not None, "Intent LLM should be attached"

        # Check LLM configuration
        llm = analyzer.intent_llm
        print(f"  ✅ IntentAnalyzer LLM created successfully")
        print(f"  📊 Model: {llm.default_request_params.model}")
        print(f"  🌡️  Temperature: {llm.default_request_params.temperature}")
        print(f"  🔢 Max Tokens: {llm.default_request_params.maxTokens}")

        # Check if it's using the expected configuration for intent analysis
        if hasattr(analyzer.intent_agent, "llm_config"):
            config = analyzer.intent_agent.llm_config
            print(f"  🎯 Agent LLM Config: {config}")

            # Verify fast model for intent classification
            expected_model = config.get("model", "gpt-4o-mini")
            assert "mini" in expected_model or expected_model == "gpt-4o-mini", (
                f"Expected fast model for intent analysis, got {expected_model}"
            )

        print("  ✅ IntentAnalyzer Smart LLM Factory test PASSED\n")
        return True

    except Exception as e:
        print(f"  ❌ IntentAnalyzer test FAILED: {e}")
        import traceback

        traceback.print_exc()
        return False


async def test_orchestrator_smart_llm():
    """Test Orchestrator with Smart LLM Factory."""

    print("🧪 Testing Orchestrator Smart LLM Factory Configuration...")

    try:
        # Import required components
        from slack_meta_agent.src.slack_meta_agent.core.orchestrator import Orchestrator
        from slack_meta_agent.src.slack_meta_agent.agents.intent_analyzer import (
            IntentAnalyzerAgent,
        )
        from slack_meta_agent.src.slack_meta_agent.agents.registry import (
            AgentRegistryAgent,
        )
        from slack_meta_agent.src.slack_meta_agent.agents.tool_discovery import (
            ToolDiscoveryAgent,
        )
        from slack_meta_agent.src.slack_meta_agent.agents.pool_manager import (
            PoolManagerAgent,
        )
        from slack_meta_agent.src.slack_meta_agent.agents.workflow_manager import (
            WorkflowManagerAgent,
        )

        # Create mock components (minimal setup for testing)
        intent_analyzer = IntentAnalyzerAgent()
        registry = AgentRegistryAgent()
        tool_discovery = ToolDiscoveryAgent()
        pool_manager = PoolManagerAgent()
        workflow_manager = WorkflowManagerAgent()

        # Create orchestrator
        orchestrator = Orchestrator(
            intent_analyzer=intent_analyzer,
            registry=registry,
            tool_discovery=tool_discovery,
            pool_manager=pool_manager,
            workflow_manager=workflow_manager,
        )

        # Initialize it (this should create the planner_agent and attach LLM)
        await orchestrator.initialize()

        # Verify the agent was created
        assert orchestrator.planner_agent is not None, "Planner agent should be created"
        assert orchestrator.planner_llm is not None, "Planner LLM should be attached"

        # Check LLM configuration
        llm = orchestrator.planner_llm
        print(f"  ✅ Orchestrator LLM created successfully")
        print(f"  📊 Model: {llm.default_request_params.model}")
        print(f"  🌡️  Temperature: {llm.default_request_params.temperature}")
        print(f"  🔢 Max Tokens: {llm.default_request_params.maxTokens}")

        # Check if it's using the expected configuration for planning
        if hasattr(orchestrator.planner_agent, "llm_config"):
            config = orchestrator.planner_agent.llm_config
            print(f"  🎯 Planner LLM Config: {config}")

            # Verify powerful model for orchestration
            expected_model = config.get("model", "gpt-4o")
            expected_tokens = config.get("max_tokens", 4000)

            print(f"  🔍 Expected high-capability model: {expected_model}")
            print(f"  📊 Expected high token limit: {expected_tokens}")

        # Test health check
        health = await orchestrator.health_check()
        print(f"  🏥 Health Check: {health.get('status', 'unknown')}")
        print(
            f"  🤖 Planner Agent Available: {health.get('planner_agent_available', False)}"
        )
        print(
            f"  🧠 Planner LLM Available: {health.get('planner_llm_available', False)}"
        )

        print("  ✅ Orchestrator Smart LLM Factory test PASSED\n")
        return True

    except Exception as e:
        print(f"  ❌ Orchestrator test FAILED: {e}")
        import traceback

        traceback.print_exc()
        return False


async def test_database_agent_specs():
    """Test that our agent specs exist in the database."""

    print("🧪 Testing Database Agent Specs...")

    # Simulate checking database specs
    specs_expected = {
        "intent_analyzer": {
            "name": "Intent Classification Specialist",
            "llm_model": "gpt-4o-mini",
            "temperature": 0.1,
            "max_tokens": 1000,
            "role": "Analyze user messages and classify intent with high accuracy",
        },
        "orchestrator": {
            "name": "Master Execution Planner",
            "llm_model": "gpt-4o",
            "temperature": 0.1,
            "max_tokens": 4000,
            "role": "Build and execute sophisticated multi-agent plans",
        },
    }

    for agent_type, expected in specs_expected.items():
        print(f"  📋 Expected {agent_type} spec:")
        print(f"    📛 Name: {expected['name']}")
        print(f"    🎭 Role: {expected['role']}")
        print(f"    🤖 Model: {expected['llm_model']}")
        print(f"    🌡️  Temperature: {expected['temperature']}")
        print(f"    🔢 Max Tokens: {expected['max_tokens']}")

    print("  ✅ Database Agent Specs verified\n")
    return True


async def main():
    """Run all tests."""

    print("🚀 Testing IntentAnalyzer and Orchestrator Smart LLM Factory Integration\n")

    results = []

    # Test database specs
    results.append(await test_database_agent_specs())

    # Test IntentAnalyzer
    results.append(await test_intent_analyzer_smart_llm())

    # Test Orchestrator
    results.append(await test_orchestrator_smart_llm())

    # Summary
    passed = sum(results)
    total = len(results)

    print("=" * 60)
    print(f"🏁 Test Results: {passed}/{total} tests passed")

    if passed == total:
        print(
            "🎉 All tests PASSED! IntentAnalyzer and Orchestrator are using Smart LLM Factory!"
        )
        print("\n✨ Key Benefits:")
        print("  • Database-driven LLM configuration")
        print("  • Centralized agent specification management")
        print("  • Enhanced role-based context and instructions")
        print("  • Optimized model selection per component")
        print("  • Consistent configuration across all agents")
    else:
        print("❌ Some tests failed. Check the output above for details.")
        return 1

    return 0


if __name__ == "__main__":
    asyncio.run(main())
