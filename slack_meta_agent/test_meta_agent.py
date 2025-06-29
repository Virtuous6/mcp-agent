#!/usr/bin/env python3
"""
Test suite for the Slack Meta-Agent system
Tests the core functionality without requiring actual Slack/API connections
"""

import asyncio
import json
import tempfile
import yaml
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# Import the Meta-Agent classes
import sys

sys.path.append(str(Path(__file__).parent))

from main import SlackMetaAgent, MetaAgent, AgentSpec


class TestSlackMetaAgent:
    """Test suite for the Slack Meta-Agent"""

    def setup_method(self):
        """Setup for each test"""
        self.meta_agent = SlackMetaAgent()

    def test_agent_registry_initialization(self):
        """Test that the agent registry is properly initialized"""
        assert len(self.meta_agent.agent_registry) == 5
        assert "financial_analyst" in self.meta_agent.agent_registry
        assert "code_developer" in self.meta_agent.agent_registry
        assert "data_researcher" in self.meta_agent.agent_registry
        assert "project_manager" in self.meta_agent.agent_registry
        assert "communication_specialist" in self.meta_agent.agent_registry

        # Test agent spec structure
        financial_spec = self.meta_agent.agent_registry["financial_analyst"]
        assert isinstance(financial_spec, AgentSpec)
        assert financial_spec.name == "financial_analyst"
        assert "financial_analysis" in financial_spec.capabilities
        assert "supabase" in financial_spec.server_names

    @pytest.mark.asyncio
    async def test_intent_analysis(self):
        """Test intent analysis functionality"""
        with patch("main.Agent") as mock_agent_class:
            # Mock the agent and LLM
            mock_agent = AsyncMock()
            mock_llm = AsyncMock()

            # Mock successful JSON response
            mock_llm.generate_str.return_value = json.dumps(
                {
                    "required_agents": [
                        "financial_analyst",
                        "data_researcher",
                        "code_developer",
                    ],
                    "complexity": "complex",
                    "estimated_tasks": 5,
                    "execution_strategy": "orchestrated",
                    "priority": "high",
                    "task_description": "Create financial dashboard",
                }
            )

            mock_agent.attach_llm.return_value = mock_llm
            mock_agent.__aenter__.return_value = mock_agent
            mock_agent.__aexit__.return_value = None
            mock_agent_class.return_value = mock_agent

            # Test intent analysis
            message = "Create Q4 revenue dashboard for SaaS metrics"
            analysis = await self.meta_agent._analyze_user_intent(message)

            assert analysis["execution_strategy"] == "orchestrated"
            assert "financial_analyst" in analysis["required_agents"]
            assert analysis["complexity"] == "complex"

    @pytest.mark.asyncio
    async def test_agent_creation(self):
        """Test specialized agent creation"""
        with patch("main.Agent") as mock_agent_class:
            mock_agent = MagicMock()
            mock_agent_class.return_value = mock_agent

            # Test creating a financial analyst
            agent = await self.meta_agent.create_specialized_agent("financial_analyst")

            assert agent == mock_agent
            assert "financial_analyst" in self.meta_agent.specialized_agents

            # Verify agent was created with correct parameters
            spec = self.meta_agent.agent_registry["financial_analyst"]
            mock_agent_class.assert_called_with(
                name=spec.name,
                instruction=spec.instruction,
                server_names=spec.server_names,
            )

    @pytest.mark.asyncio
    async def test_slack_message_processing(self):
        """Test Slack message processing flow"""
        with patch.multiple(
            self.meta_agent,
            _store_conversation_memory=AsyncMock(),
            _analyze_user_intent=AsyncMock(),
            create_specialized_agent=AsyncMock(),
            _execute_orchestrated_workflow=AsyncMock(),
            _store_interaction_learning=AsyncMock(),
            _send_slack_response=AsyncMock(),
        ):
            # Setup mocks
            self.meta_agent._analyze_user_intent.return_value = {
                "required_agents": ["financial_analyst"],
                "execution_strategy": "orchestrated",
            }

            mock_agent = MagicMock()
            self.meta_agent.create_specialized_agent.return_value = mock_agent
            self.meta_agent._execute_orchestrated_workflow.return_value = (
                "Dashboard created successfully!"
            )

            # Test event processing
            event = {
                "user": "U123456",
                "channel": "C123456",
                "text": "Create Q4 revenue dashboard for SaaS metrics",
            }

            await self.meta_agent._process_slack_message(event)

            # Verify all steps were called
            self.meta_agent._store_conversation_memory.assert_called_once()
            self.meta_agent._analyze_user_intent.assert_called_once()
            self.meta_agent.create_specialized_agent.assert_called_once()
            self.meta_agent._execute_orchestrated_workflow.assert_called_once()
            self.meta_agent._store_interaction_learning.assert_called_once()
            self.meta_agent._send_slack_response.assert_called_once()

    def test_response_formatting(self):
        """Test Slack response formatting"""
        # Test dashboard response formatting
        result_with_dashboard = """
        I've created your Q4 revenue dashboard successfully!
        Dashboard URL: https://example.com/dashboard
        Revenue increased by 23% QoQ
        Next steps: Review monthly metrics
        """

        analysis = {
            "execution_strategy": "orchestrated",
            "required_agents": ["financial_analyst"],
        }

        # Test URL extraction
        url = self.meta_agent._extract_dashboard_url(result_with_dashboard)
        assert "https://example.com/dashboard" in url

        # Test insights extraction
        insights = self.meta_agent._extract_key_insights(result_with_dashboard)
        assert "23%" in insights

        # Test next steps extraction
        next_steps = self.meta_agent._extract_next_steps(result_with_dashboard)
        assert "Review" in next_steps

    @pytest.mark.asyncio
    async def test_sequential_fallback(self):
        """Test sequential execution fallback"""
        with patch("main.Agent") as mock_agent_class:
            # Create mock agents
            mock_agent1 = AsyncMock()
            mock_agent2 = AsyncMock()
            mock_llm1 = AsyncMock()
            mock_llm2 = AsyncMock()

            mock_llm1.generate_str.return_value = "Financial analysis complete"
            mock_llm2.generate_str.return_value = "Research complete"

            mock_agent1.attach_llm.return_value = mock_llm1
            mock_agent2.attach_llm.return_value = mock_llm2
            mock_agent1.__aenter__.return_value = mock_agent1
            mock_agent2.__aenter__.return_value = mock_agent2
            mock_agent1.__aexit__.return_value = None
            mock_agent2.__aexit__.return_value = None
            mock_agent1.name = "financial_analyst"
            mock_agent2.name = "data_researcher"

            agents = [mock_agent1, mock_agent2]
            message = "Test message"

            result = await self.meta_agent._execute_sequential_fallback(message, agents)

            assert "financial_analyst" in result
            assert "data_researcher" in result
            assert "Financial analysis complete" in result
            assert "Research complete" in result


class TestConfiguration:
    """Test configuration and setup"""

    def test_secrets_file_format(self):
        """Test secrets file format"""
        secrets_example = Path(__file__).parent / "mcp_agent.secrets.yaml.example"
        assert secrets_example.exists()

        with open(secrets_example) as f:
            example_config = yaml.safe_load(f)

        # Test required sections exist
        assert "openai" in example_config
        assert "slack" in example_config
        assert "supabase" in example_config
        assert "github" in example_config
        assert "brave_search" in example_config

        # Test Slack configuration structure
        slack_config = example_config["slack"]
        assert "bot_token" in slack_config
        assert "app_token" in slack_config

    def test_config_file_format(self):
        """Test main config file format"""
        config_file = Path(__file__).parent / "mcp_agent.config.yaml"
        assert config_file.exists()

        with open(config_file) as f:
            config = yaml.safe_load(f)

        # Test required sections
        assert "execution_engine" in config
        assert "mcp" in config
        assert "servers" in config["mcp"]

        # Test required servers
        servers = config["mcp"]["servers"]
        assert "slack" in servers
        assert "supabase" in servers
        assert "filesystem" in servers
        assert "github" in servers
        assert "brave_search" in servers


class TestIntegration:
    """Integration tests"""

    @pytest.mark.asyncio
    async def test_end_to_end_simulation(self):
        """Test end-to-end workflow simulation"""
        meta_agent = SlackMetaAgent()

        with (
            patch.multiple(
                meta_agent,
                _store_conversation_memory=AsyncMock(),
                _store_interaction_learning=AsyncMock(),
                _send_slack_response=AsyncMock(),
            ),
            patch("main.Agent") as mock_agent_class,
            patch("main.Orchestrator") as mock_orchestrator_class,
        ):
            # Mock intent analysis
            with patch.object(meta_agent, "_analyze_user_intent") as mock_intent:
                mock_intent.return_value = {
                    "required_agents": ["financial_analyst", "code_developer"],
                    "execution_strategy": "orchestrated",
                    "complexity": "complex",
                }

                # Mock agent creation
                mock_agent = AsyncMock()
                mock_agent_class.return_value = mock_agent

                # Mock orchestrator
                mock_orchestrator = AsyncMock()
                mock_orchestrator.generate_str.return_value = "✅ Q4 Revenue Dashboard created! Dashboard: https://dashboard.example.com Revenue up 23% QoQ"
                mock_orchestrator_class.return_value = mock_orchestrator

                # Test the full flow
                event = {
                    "user": "U123456",
                    "channel": "C123456",
                    "text": "Create Q4 revenue dashboard for SaaS metrics",
                }

                await meta_agent._process_slack_message(event)

                # Verify orchestrator was used
                mock_orchestrator_class.assert_called_once()
                mock_orchestrator.generate_str.assert_called_once()

                # Verify response was sent
                meta_agent._send_slack_response.assert_called_once()


def run_interactive_tests():
    """Run interactive tests for manual verification"""
    print("🧪 Running Interactive Tests for Slack Meta-Agent")
    print("=" * 60)

    # Test 1: Agent Registry
    print("\n1. Testing Agent Registry...")
    meta_agent = SlackMetaAgent()
    for agent_type, spec in meta_agent.agent_registry.items():
        print(f"   ✅ {agent_type}: {len(spec.capabilities)} capabilities")

    # Test 2: Mock Slack Message
    print("\n2. Testing Mock Slack Message Processing...")

    async def test_mock_message():
        mock_event = {
            "user": "U123TEST",
            "channel": "C123TEST",
            "text": "Create Q4 revenue dashboard for SaaS metrics",
        }

        print(f"   📨 Processing: {mock_event['text']}")

        # This would process the message in a real scenario
        # For testing, we just verify the structure
        user_id = mock_event["user"]
        message_text = mock_event["text"]

        print(f"   👤 User: {user_id}")
        print(f"   💬 Message: {message_text}")
        print("   ✅ Mock processing complete")

    asyncio.run(test_mock_message())

    # Test 3: Configuration Validation
    print("\n3. Testing Configuration...")
    config_file = Path(__file__).parent / "mcp_agent.config.yaml"
    secrets_example = Path(__file__).parent / "mcp_agent.secrets.yaml.example"

    if config_file.exists():
        print("   ✅ Configuration file found")
    else:
        print("   ❌ Configuration file missing")

    if secrets_example.exists():
        print("   ✅ Secrets example found")
    else:
        print("   ❌ Secrets example missing")

    print("\n✅ Interactive tests complete!")
    print("\n🚀 Next Steps:")
    print("1. Copy mcp_agent.secrets.yaml.example to mcp_agent.secrets.yaml")
    print("2. Fill in your actual API keys and tokens")
    print("3. Run: python main.py")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test the Slack Meta-Agent system")
    parser.add_argument(
        "--interactive", action="store_true", help="Run interactive tests"
    )
    parser.add_argument(
        "--test", choices=["config", "agents", "all"], help="Run specific test category"
    )

    args = parser.parse_args()

    if args.interactive:
        run_interactive_tests()
    elif args.test:
        if args.test == "config":
            pytest.main(["-v", "test_meta_agent.py::TestConfiguration"])
        elif args.test == "agents":
            pytest.main(["-v", "test_meta_agent.py::TestSlackMetaAgent"])
        elif args.test == "all":
            pytest.main(["-v", "test_meta_agent.py"])
    else:
        # Run all tests by default
        pytest.main(["-v", "test_meta_agent.py"])
