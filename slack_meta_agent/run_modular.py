#!/usr/bin/env python3
"""
Launcher script for the modular SlackMetaAgent system.

This script can be run directly from the slack_meta_agent directory
and handles the import path issues by running the module properly.
"""

import sys
import os
import subprocess


def main():
    """Run the modular SlackMetaAgent system."""

    # Get the current directory and find the root mcp-agent directory
    current_dir = os.getcwd()
    slack_meta_agent_dir = os.path.dirname(os.path.abspath(__file__))

    # Go up one level to find mcp-agent root
    mcp_agent_root = os.path.dirname(slack_meta_agent_dir)

    # Check if we're in the right place
    if not os.path.exists(os.path.join(mcp_agent_root, "src", "mcp_agent")):
        print("❌ Error: Could not find mcp-agent root directory")
        print(f"   Current path: {current_dir}")
        print(f"   Expected mcp-agent root: {mcp_agent_root}")
        print(
            "\n💡 Please run this script from the slack_meta_agent directory within the mcp-agent repository"
        )
        sys.exit(1)

    # Change to the mcp-agent root directory
    os.chdir(mcp_agent_root)

    print(f"🚀 Running modular SlackMetaAgent from {mcp_agent_root}")
    print("📁 Changed working directory to mcp-agent root")

    # Run the module using the proper Python module syntax
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "slack_meta_agent.src.slack_meta_agent.main_modular",
            ],
            cwd=mcp_agent_root,
        )
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        print("\n🛑 Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Error running modular system: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
