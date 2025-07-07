#!/usr/bin/env python3
"""
Enable verbose logging for the modular SlackMetaAgent to see agent activity.
Run this instead of run_modular.py when you want to see detailed logs.
"""

import logging
import sys
import os

# Add the project root to Python path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.insert(0, project_root)


def setup_verbose_logging():
    """Configure logging to show all agent activity."""
    # Set up root logger with DEBUG level
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create console handler with detailed formatting
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # Create detailed formatter that shows component names clearly
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)8s | %(name)30s | %(message)s", datefmt="%H:%M:%S"
    )
    console_handler.setFormatter(formatter)

    # Add handler to root logger
    root_logger.addHandler(console_handler)

    # Enable specific component loggers
    component_loggers = [
        "ModularSlackMetaAgent",
        "Orchestrator",
        "SlackAdapterAgent",
        "IntentAnalyzerAgent",
        "ToolDiscoveryAgent",
        "PoolManagerAgent",
        "WorkflowManagerAgent",
        "AgentRegistryAgent",
        "SlackClientManager",
    ]

    for component in component_loggers:
        logger = logging.getLogger(component)
        logger.setLevel(logging.DEBUG)
        logger.propagate = True

    # Also enable the full module path loggers
    module_loggers = [
        "slack_meta_agent.core.orchestrator",
        "slack_meta_agent.agents.intent_analyzer",
        "slack_meta_agent.agents.slack_adapter",
        "slack_meta_agent.agents.tool_discovery",
        "slack_meta_agent.agents.pool_manager",
        "slack_meta_agent.agents.workflow_manager",
        "slack_meta_agent.agents.registry",
        "slack_meta_agent.slack.slack_client",
    ]

    for module in module_loggers:
        logger = logging.getLogger(module)
        logger.setLevel(logging.DEBUG)
        logger.propagate = True

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("slack_sdk").setLevel(logging.WARNING)

    print("🔍 **VERBOSE LOGGING ENABLED**")
    print("You'll now see detailed logs from all micro-agents:")
    print("  🎯 Orchestrator - Main coordination")
    print("  🧠 IntentAnalyzer - Intent classification")
    print("  📱 SlackAdapter - Slack event handling")
    print("  🔧 ToolDiscovery - Server/tool discovery")
    print("  👥 PoolManager - Agent pool management")
    print("  🔄 WorkflowManager - Workflow execution")
    print("  📋 Registry - Agent specifications")
    print("-" * 60)


async def main():
    """Run the modular system with verbose logging."""
    # Set up verbose logging first
    setup_verbose_logging()

    # Set the missing SUPABASE_PROJECT_ID environment variable
    if not os.getenv("SUPABASE_PROJECT_ID"):
        os.environ["SUPABASE_PROJECT_ID"] = "qqggdvfeybfzqmgxmidt"
        print(f"🔧 Set SUPABASE_PROJECT_ID environment variable: qqggdvfeybfzqmgxmidt")

    try:
        # Import and run the modular system with correct path
        from src.slack_meta_agent.main_modular import main as modular_main

        await modular_main()
    except KeyboardInterrupt:
        print("\n🛑 Shutting down verbose logging session...")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
