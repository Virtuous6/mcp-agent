#!/usr/bin/env python3
"""
Simple launcher that uses the proven working SlackMetaAgent.

This uses the existing, tested SlackMetaAgent class that we know works
from main_refactored.py, but with a simpler setup.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add the src directory to the path
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.join(current_dir, "src")
sys.path.insert(0, src_dir)

# Configure basic logging
logging.basicConfig(level=logging.INFO)

from mcp_agent.app import MCPApp
from mcp_agent.human_input.handler import console_input_callback
from slack_meta_agent.core.meta_agent import SlackMetaAgent


async def main():
    """Simple main function using the proven working SlackMetaAgent."""

    print("🚀 Starting Simple Slack Meta-Agent...")

    # Load configuration
    config_file = "config/mcp_agent.config.yaml"
    if not os.path.exists(config_file):
        print(f"❌ Config file not found: {config_file}")
        return

    # Load secrets
    secrets_file = "config/mcp_agent.secrets.yaml"
    if not os.path.exists(secrets_file):
        print(f"❌ Secrets file not found: {secrets_file}")
        return

    import yaml

    with open(secrets_file) as f:
        secrets = yaml.safe_load(f)

    slack_config = secrets.get("slack", {})
    bot_token = slack_config.get("bot_token")
    app_token = slack_config.get("app_token")

    if not bot_token or not app_token:
        print("❌ Slack tokens not configured in secrets file")
        return

    # Load MCP configuration
    from mcp_agent.config import get_settings

    settings = get_settings(config_file)

    # Create MCPApp
    app_instance = MCPApp(
        name="simple_slack_agent",
        settings=settings,
        human_input_callback=console_input_callback,
    )

    # Initialize and run
    async with app_instance.run() as agent_app:
        logger = logging.getLogger("SimpleSlackAgent")

        # Create the proven working SlackMetaAgent
        meta_agent = SlackMetaAgent(
            supabase_project_id="qqggdvfeybfzqmgxmidt",  # Your project ID
            mcp_app=agent_app,
        )

        try:
            # Load configuration
            meta_agent.load_dynamic_config()

            # Initialize Slack
            await meta_agent.initialize_slack(bot_token, app_token)

            # Start connection
            await meta_agent.start_slack_connection()

            print("✅ Slack Meta-Agent connected and ready!")
            print("🤖 Try mentioning your bot: @tomas hello")
            print("Press Ctrl+C to stop...")

            # Keep running
            while True:
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            print("\n🛑 Shutting down...")
            try:
                await meta_agent.cleanup()
            except:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Shutdown complete")
