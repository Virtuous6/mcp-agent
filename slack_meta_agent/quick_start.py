#!/usr/bin/env python3
"""
Quick Start Script for Slack Meta-Agent System
Helps users get up and running quickly with minimal configuration
"""

import os
import sys
import subprocess
import asyncio
import yaml
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


def print_banner():
    """Print the startup banner"""
    banner = """
    🤖 SLACK META-AGENT QUICK START
    ================================
    
    This script will help you set up and run your Slack Meta-Agent system.
    The Meta-Agent can orchestrate specialized agents for:
    
    • 💰 Financial Analysis & Dashboards
    • 💻 Code Development & Deployment  
    • 🔍 Data Research & Reports
    • 📋 Project Management
    • 💬 Communication & Content
    """
    console.print(Panel(banner, style="bold blue"))


def check_prerequisites():
    """Check if all prerequisites are installed"""
    console.print("\n🔍 Checking Prerequisites...", style="bold yellow")

    requirements = {
        "python": {"cmd": [sys.executable, "--version"], "min": "3.8"},
        "node": {"cmd": ["node", "--version"], "min": "16.0"},
        "npm": {"cmd": ["npm", "--version"], "min": "7.0"},
        "uv": {"cmd": ["uv", "--version"], "min": "0.1"},
    }

    missing = []

    for name, config in requirements.items():
        try:
            result = subprocess.run(
                config["cmd"], capture_output=True, text=True, check=True
            )
            version = result.stdout.strip()
            console.print(f"   ✅ {name}: {version}")
        except (subprocess.CalledProcessError, FileNotFoundError):
            console.print(f"   ❌ {name}: Missing")
            missing.append(name)

    if missing:
        console.print(
            f"\n⚠️  Missing prerequisites: {', '.join(missing)}", style="bold red"
        )
        console.print("Please install them first:")
        console.print("• Python 3.8+: https://python.org")
        console.print("• Node.js 16+: https://nodejs.org")
        console.print("• UV: pip install uv")
        return False

    console.print("✅ All prerequisites installed!", style="bold green")
    return True


def setup_secrets_file():
    """Setup the secrets configuration file"""
    console.print("\n🔐 Setting Up Configuration...", style="bold yellow")

    secrets_file = Path("mcp_agent.secrets.yaml")
    example_file = Path("mcp_agent.secrets.yaml.example")

    if secrets_file.exists():
        console.print("   ✅ Secrets file already exists")
        if not Confirm.ask("   🔄 Do you want to reconfigure it?"):
            return True

    if not example_file.exists():
        console.print("   ❌ Example secrets file not found", style="bold red")
        return False

    # Interactive configuration
    console.print("\n📝 Let's configure your API keys and tokens...")

    # OpenAI API Key
    openai_key = Prompt.ask("🔑 Enter your OpenAI API key (sk-...)", password=True)

    # Slack Configuration
    console.print("\n🔗 Slack Bot Setup:")
    console.print("   1. Go to https://api.slack.com/apps")
    console.print("   2. Create a new app or select existing")
    console.print("   3. Enable Socket Mode")
    console.print(
        "   4. Add bot scopes: app_mentions:read, channels:history, chat:write, im:history, im:read, users:read"
    )

    slack_bot_token = Prompt.ask(
        "🤖 Enter your Slack Bot Token (xoxb-...)", password=True
    )
    slack_app_token = Prompt.ask(
        "📱 Enter your Slack App Token (xapp-...)", password=True
    )

    # Optional configurations
    github_token = ""
    supabase_url = ""
    supabase_key = ""
    brave_key = ""

    if Confirm.ask(
        "\n🐙 Do you want to configure GitHub integration? (for code deployment)"
    ):
        github_token = Prompt.ask(
            "   Enter your GitHub Personal Access Token (ghp-...)", password=True
        )

    if Confirm.ask("\n🗄️  Do you want to configure Supabase? (for memory/learning)"):
        supabase_url = Prompt.ask("   Enter your Supabase URL (https://...supabase.co)")
        supabase_key = Prompt.ask("   Enter your Supabase Anon Key", password=True)

    if Confirm.ask("\n🔍 Do you want to configure Brave Search? (for web research)"):
        brave_key = Prompt.ask(
            "   Enter your Brave Search API Key (BSA-...)", password=True
        )

    # Create secrets file
    secrets = {
        "openai": {"api_key": openai_key},
        "slack": {
            "bot_token": slack_bot_token,
            "app_token": slack_app_token,
        },
    }

    if github_token:
        secrets["github"] = {"personal_access_token": github_token}

    if supabase_url and supabase_key:
        secrets["supabase"] = {"url": supabase_url, "anon_key": supabase_key}

    if brave_key:
        secrets["brave_search"] = {"api_key": brave_key}

    with open(secrets_file, "w") as f:
        yaml.dump(secrets, f, default_flow_style=False)

    console.print(f"✅ Configuration saved to {secrets_file}", style="bold green")
    return True


def install_dependencies():
    """Install required dependencies"""
    console.print("\n📦 Installing Dependencies...", style="bold yellow")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        # Install MCP Agent framework
        task1 = progress.add_task("Installing MCP Agent framework...", total=1)
        try:
            result = subprocess.run(
                ["uv", "sync", "--all-extras", "--all-packages", "--group", "dev"],
                cwd=Path(".."),  # Run from parent directory
                capture_output=True,
                text=True,
                check=True,
            )
            progress.update(task1, completed=1)
            console.print("   ✅ MCP Agent framework installed")
        except subprocess.CalledProcessError as e:
            console.print(
                f"   ❌ Error installing framework: {e.stderr}", style="bold red"
            )
            return False

        # Install additional dependencies
        task2 = progress.add_task("Installing additional dependencies...", total=1)
        try:
            result = subprocess.run(
                ["pip", "install", "-r", "requirements.txt"],
                capture_output=True,
                text=True,
                check=True,
            )
            progress.update(task2, completed=1)
            console.print("   ✅ Additional dependencies installed")
        except subprocess.CalledProcessError as e:
            console.print(
                f"   ❌ Error installing dependencies: {e.stderr}", style="bold red"
            )
            return False

    return True


def test_configuration():
    """Test the configuration"""
    console.print("\n🧪 Testing Configuration...", style="bold yellow")

    try:
        # Test imports
        from main import SlackMetaAgent

        console.print("   ✅ Main imports work")

        # Test agent initialization
        meta_agent = SlackMetaAgent()
        agent_count = len(meta_agent.agent_registry)
        console.print(
            f"   ✅ Meta-agent initialized with {agent_count} specialized agents"
        )

        # Test configuration loading
        secrets_file = Path("mcp_agent.secrets.yaml")
        if secrets_file.exists():
            with open(secrets_file) as f:
                secrets = yaml.safe_load(f)

            if "openai" in secrets and "slack" in secrets:
                console.print("   ✅ Configuration file format is valid")
            else:
                console.print("   ⚠️  Configuration may be incomplete", style="yellow")

        console.print("✅ Configuration test passed!", style="bold green")
        return True

    except Exception as e:
        console.print(f"   ❌ Configuration test failed: {e}", style="bold red")
        return False


def run_meta_agent():
    """Run the Meta-Agent system"""
    console.print("\n🚀 Starting Slack Meta-Agent...", style="bold green")

    try:
        # Import and run
        from main import main

        console.print("🔌 Connecting to Slack...")
        console.print("🤖 Meta-Agent is starting up...")
        console.print("\n" + "=" * 50)
        console.print("💡 READY! Your Meta-Agent is now listening for Slack messages!")
        console.print("=" * 50)
        console.print("Try sending a message like:")
        console.print("   • 'Create Q4 revenue dashboard for SaaS metrics'")
        console.print("   • 'Research competitor analysis for our product'")
        console.print("   • 'Deploy a new landing page to GitHub'")
        console.print("=" * 50)
        console.print("Press Ctrl+C to stop the agent\n")

        # Run the main function
        asyncio.run(main())

    except KeyboardInterrupt:
        console.print("\n👋 Meta-Agent stopped by user", style="bold yellow")
    except Exception as e:
        console.print(f"\n💥 Error running Meta-Agent: {e}", style="bold red")
        console.print("\n🔧 Troubleshooting:")
        console.print("1. Check your API keys in mcp_agent.secrets.yaml")
        console.print("2. Ensure your Slack bot has the right permissions")
        console.print(
            "3. Run 'python test_meta_agent.py --interactive' for diagnostics"
        )


def main():
    """Main quick start process"""
    print_banner()

    # Step 1: Check prerequisites
    if not check_prerequisites():
        console.print(
            "\n❌ Setup failed. Please install missing prerequisites.", style="bold red"
        )
        return

    # Step 2: Setup configuration
    if not setup_secrets_file():
        console.print("\n❌ Configuration setup failed.", style="bold red")
        return

    # Step 3: Install dependencies
    if not install_dependencies():
        console.print("\n❌ Dependency installation failed.", style="bold red")
        return

    # Step 4: Test configuration
    if not test_configuration():
        console.print("\n❌ Configuration test failed.", style="bold red")
        return

    # Step 5: Ask if user wants to run now
    console.print("\n✅ Setup Complete!", style="bold green")

    if Confirm.ask("🚀 Do you want to start the Meta-Agent now?"):
        run_meta_agent()
    else:
        console.print("\n🎯 To start later, run: python main.py")
        console.print("🧪 To test first, run: python test_meta_agent.py --interactive")


if __name__ == "__main__":
    main()
