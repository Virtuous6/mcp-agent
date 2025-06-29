#!/usr/bin/env python3
"""
Automated setup script for the Slack Meta-Agent system
Handles initial configuration and validation
"""

import os
import sys
import shutil
import subprocess
import asyncio
from pathlib import Path
from typing import Dict, List, Optional


class MetaAgentSetup:
    def __init__(self):
        self.project_root = Path(__file__).parent
        self.required_files = [
            "mcp_agent.config.yaml",
            "mcp_agent.secrets.yaml.example",
            "main.py",
            "requirements.txt",
            "README.md",
        ]

    def print_banner(self):
        """Print setup banner"""
        print("=" * 60)
        print("🤖 SLACK META-AGENT SETUP")
        print("=" * 60)
        print("This script will help you set up your Slack Meta-Agent system.")
        print()

    def check_prerequisites(self) -> bool:
        """Check if all prerequisites are installed"""
        print("🔍 Checking Prerequisites...")

        prerequisites = {
            "python": {"command": [sys.executable, "--version"], "min_version": "3.8"},
            "node": {"command": ["node", "--version"], "min_version": "16.0"},
            "npm": {"command": ["npm", "--version"], "min_version": "7.0"},
            "docker": {"command": ["docker", "--version"], "min_version": "20.0"},
            "uv": {"command": ["uv", "--version"], "min_version": "0.1"},
        }

        all_good = True

        for name, config in prerequisites.items():
            try:
                result = subprocess.run(
                    config["command"], capture_output=True, text=True, check=True
                )
                version = result.stdout.strip()
                print(f"   ✅ {name}: {version}")
            except (subprocess.CalledProcessError, FileNotFoundError):
                print(f"   ❌ {name}: Not found or not working")
                all_good = False

        if not all_good:
            print("\n⚠️  Some prerequisites are missing. Please install them first:")
            print("   • Python 3.8+ (https://python.org)")
            print("   • Node.js 16+ (https://nodejs.org)")
            print("   • Docker (https://docker.com)")
            print("   • UV package manager (pip install uv)")
            return False

        print("✅ All prerequisites check out!")
        return True

    def check_project_structure(self) -> bool:
        """Verify project structure"""
        print("\n📁 Checking Project Structure...")

        missing_files = []
        for file in self.required_files:
            file_path = self.project_root / file
            if file_path.exists():
                print(f"   ✅ {file}")
            else:
                print(f"   ❌ {file} (missing)")
                missing_files.append(file)

        if missing_files:
            print(f"\n⚠️  Missing {len(missing_files)} required files.")
            return False

        print("✅ Project structure is complete!")
        return True

    def setup_secrets_file(self) -> bool:
        """Setup the secrets configuration file"""
        print("\n🔐 Setting up Secrets Configuration...")

        secrets_file = self.project_root / "mcp_agent.secrets.yaml"
        example_file = self.project_root / "mcp_agent.secrets.yaml.example"

        if secrets_file.exists():
            print("   ✅ Secrets file already exists")
            return True

        if not example_file.exists():
            print("   ❌ Example secrets file not found")
            return False

        # Copy example to actual secrets file
        shutil.copy2(example_file, secrets_file)
        print(f"   ✅ Created {secrets_file}")
        print(f"   ⚠️  Please edit {secrets_file} with your actual API keys and tokens")

        return True

    def install_dependencies(self) -> bool:
        """Install Python dependencies"""
        print("\n📦 Installing Dependencies...")

        try:
            # Install with UV
            print("   Installing MCP Agent framework...")
            result = subprocess.run(
                ["uv", "sync", "--all-extras", "--all-packages", "--group", "dev"],
                cwd=self.project_root.parent,  # Run from mcp-agent root
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                print("   ✅ MCP Agent framework installed")
            else:
                print(f"   ❌ Error installing framework: {result.stderr}")
                return False

            # Install additional dependencies
            print("   Installing additional dependencies...")
            result = subprocess.run(
                ["pip", "install", "-r", "requirements.txt"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                print("   ✅ Additional dependencies installed")
            else:
                print(f"   ❌ Error installing dependencies: {result.stderr}")
                return False

        except Exception as e:
            print(f"   ❌ Error during installation: {str(e)}")
            return False

        print("✅ All dependencies installed successfully!")
        return True

    def setup_directories(self) -> bool:
        """Create necessary directories"""
        print("\n📂 Setting up Directories...")

        directories = ["logs", "data", "cache"]

        for dir_name in directories:
            dir_path = self.project_root / dir_name
            dir_path.mkdir(exist_ok=True)
            print(f"   ✅ Created/verified {dir_name}/ directory")

        print("✅ Directory structure ready!")
        return True

    def validate_configuration(self) -> bool:
        """Validate the configuration files"""
        print("\n🔧 Validating Configuration...")

        try:
            # Try to import and run basic validation
            sys.path.insert(0, str(self.project_root))

            print("   Testing configuration loading...")
            from mcp_agent.app import MCPApp

            app = MCPApp(name="setup_test")
            print("   ✅ Configuration loads successfully")

            print("   Testing meta-agent import...")
            from main import MetaAgent

            meta = MetaAgent()
            print(
                f"   ✅ Meta-agent initializes with {len(meta.agent_registry)} agent types"
            )

        except Exception as e:
            print(f"   ❌ Configuration validation failed: {str(e)}")
            return False

        print("✅ Configuration validation passed!")
        return True

    def run_tests(self) -> bool:
        """Run the test suite"""
        print("\n🧪 Running Test Suite...")

        try:
            # Import and run our test module
            sys.path.insert(0, str(self.project_root))
            from test_meta_agent import run_all_tests

            # Run tests
            result = asyncio.run(run_all_tests())

            if result:
                print("✅ All tests passed!")
                return True
            else:
                print("❌ Some tests failed. Check the output above.")
                return False

        except Exception as e:
            print(f"❌ Error running tests: {str(e)}")
            return False

    def print_next_steps(self):
        """Print next steps for the user"""
        print("\n" + "=" * 60)
        print("🎉 SETUP COMPLETE!")
        print("=" * 60)
        print()
        print("Next steps to get your Meta-Agent running:")
        print()
        print("1. 📝 Configure your API keys:")
        print("   • Edit mcp_agent.secrets.yaml with your actual credentials")
        print("   • OpenAI API key (required)")
        print("   • Slack bot tokens (for Slack integration)")
        print("   • Supabase keys (for memory and storage)")
        print("   • GitHub token (for code operations)")
        print()
        print("2. 🤖 Set up your Slack bot:")
        print("   • Go to https://api.slack.com/apps")
        print("   • Create a new app and enable Socket Mode")
        print("   • Add the required bot scopes (see README)")
        print("   • Install the app to your workspace")
        print()
        print("3. 💾 Set up Supabase:")
        print("   • Create a project at https://supabase.com")
        print("   • Run the SQL schema from the README")
        print("   • Get your API keys from Settings → API")
        print()
        print("4. 🚀 Run your Meta-Agent:")
        print("   python main.py")
        print()
        print("5. 🧪 Test individual components:")
        print("   python test_meta_agent.py")
        print("   python test_meta_agent.py --interactive")
        print()
        print("📚 For detailed information, see README.md")
        print("💡 Need help? Check the documentation or open an issue")
        print()

    def run_setup(self):
        """Run the complete setup process"""
        self.print_banner()

        steps = [
            ("Prerequisites", self.check_prerequisites),
            ("Project Structure", self.check_project_structure),
            ("Secrets Configuration", self.setup_secrets_file),
            ("Dependencies", self.install_dependencies),
            ("Directories", self.setup_directories),
            ("Configuration", self.validate_configuration),
            ("Tests", self.run_tests),
        ]

        for step_name, step_func in steps:
            if not step_func():
                print(f"\n❌ Setup failed at step: {step_name}")
                print("Please fix the issues above and run setup again.")
                return False

        self.print_next_steps()
        return True


def main():
    """Main entry point"""
    setup = MetaAgentSetup()

    try:
        success = setup.run_setup()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n👋 Setup cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error during setup: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
