#!/usr/bin/env python3
"""
Comprehensive Test Runner for ModularSlackMetaAgent System.

This test runner executes all tests for the modular system including:
1. Individual agent unit tests
2. Orchestrator integration tests
3. Full system integration tests
4. Performance and stress tests
5. Error handling and recovery tests

The runner provides detailed reporting and can be used for CI/CD validation.
"""

import asyncio
import logging
import os
import sys
import subprocess
import time
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

# Add the slack_meta_agent directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)


class ComprehensiveTestRunner:
    """Main test runner for the modular SlackMetaAgent system."""

    def __init__(self):
        self.start_time = None
        self.end_time = None
        self.test_suites = []
        self.overall_results = {
            "total_tests": 0,
            "passed_tests": 0,
            "failed_tests": 0,
            "skipped_tests": 0,
            "suite_results": {},
        }

    def log_suite_result(self, suite_name: str, result: Dict[str, Any]):
        """Log the result of a test suite."""
        self.test_suites.append(suite_name)
        self.overall_results["suite_results"][suite_name] = result

        # Update overall totals
        self.overall_results["total_tests"] += result.get("total", 0)
        self.overall_results["passed_tests"] += result.get("passed", 0)
        self.overall_results["failed_tests"] += result.get("failed", 0)
        self.overall_results["skipped_tests"] += result.get("skipped", 0)

    async def run_test_suite(self, suite_name: str, test_function) -> Dict[str, Any]:
        """Run a single test suite and capture results."""
        print(f"\n🧪 Running {suite_name}...")
        print("=" * 60)

        start_time = datetime.now()

        try:
            # Execute the test function
            success = await test_function()
            execution_time = (datetime.now() - start_time).total_seconds()

            return {
                "suite": suite_name,
                "success": success,
                "execution_time": execution_time,
                "error": None,
            }

        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            print(f"❌ {suite_name} failed with exception: {e}")

            return {
                "suite": suite_name,
                "success": False,
                "execution_time": execution_time,
                "error": str(e),
            }

    async def run_individual_agent_tests(self) -> bool:
        """Run tests for individual agents."""
        try:
            # Import and run individual agent tests
            from test_modular_agents import (
                test_agent_registry_initialization,
                test_tool_discovery_initialization,
                test_pool_manager_initialization,
                test_intent_analyzer_basic,
                test_workflow_manager_initialization,
                test_slack_adapter_basic,
                test_results,
            )

            print("🤖 Testing Individual Agents...")
            print("-" * 40)

            # Run each test
            tests = [
                ("AgentRegistry", test_agent_registry_initialization),
                ("ToolDiscovery", test_tool_discovery_initialization),
                ("PoolManager", test_pool_manager_initialization),
                ("IntentAnalyzer", test_intent_analyzer_basic),
                ("WorkflowManager", test_workflow_manager_initialization),
                ("SlackAdapter", test_slack_adapter_basic),
            ]

            for test_name, test_func in tests:
                try:
                    await test_func()
                except Exception as e:
                    print(f"❌ {test_name} test failed: {e}")

            # Get results from the test_results global
            summary = test_results.summary()

            self.log_suite_result(
                "Individual Agents",
                {
                    "total": summary["total"],
                    "passed": summary["passed"],
                    "failed": summary["failed"],
                    "skipped": 0,
                    "success_rate": summary["success_rate"],
                },
            )

            return summary["success_rate"] >= 70  # 70% pass rate for agents

        except ImportError as e:
            print(f"⚠️ Could not import individual agent tests: {e}")
            self.log_suite_result(
                "Individual Agents",
                {
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "skipped": 1,
                    "success_rate": 0,
                    "error": "Import failed",
                },
            )
            return False
        except Exception as e:
            print(f"❌ Individual agent tests failed: {e}")
            self.log_suite_result(
                "Individual Agents",
                {
                    "total": 0,
                    "passed": 0,
                    "failed": 1,
                    "skipped": 0,
                    "success_rate": 0,
                    "error": str(e),
                },
            )
            return False

    async def run_orchestrator_tests(self) -> bool:
        """Run orchestrator and integration tests."""
        try:
            from test_orchestrator_integration import OrchestrationTestSuite

            suite = OrchestrationTestSuite()

            # Setup
            await suite.setup()

            try:
                # Run all orchestrator tests
                await suite.test_orchestrator_initialization()
                await suite.test_message_flow_strategies()
                await suite.test_error_recovery()
                await suite.test_concurrent_processing()
                await suite.test_system_performance()
                await suite.test_health_monitoring()

                # Generate results
                total_tests = len(suite.test_results)
                passed_tests = len([t for t in suite.test_results if t["success"]])

                self.log_suite_result(
                    "Orchestrator & Integration",
                    {
                        "total": total_tests,
                        "passed": passed_tests,
                        "failed": total_tests - passed_tests,
                        "skipped": 0,
                        "success_rate": (passed_tests / total_tests * 100)
                        if total_tests > 0
                        else 0,
                    },
                )

                return passed_tests >= total_tests * 0.8  # 80% pass rate

            finally:
                await suite.cleanup()

        except ImportError as e:
            print(f"⚠️ Could not import orchestrator tests: {e}")
            self.log_suite_result(
                "Orchestrator & Integration",
                {
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "skipped": 1,
                    "success_rate": 0,
                    "error": "Import failed",
                },
            )
            return False
        except Exception as e:
            print(f"❌ Orchestrator tests failed: {e}")
            self.log_suite_result(
                "Orchestrator & Integration",
                {
                    "total": 0,
                    "passed": 0,
                    "failed": 1,
                    "skipped": 0,
                    "success_rate": 0,
                    "error": str(e),
                },
            )
            return False

    async def run_full_system_tests(self) -> bool:
        """Run full system integration tests."""
        try:
            from test_modular_agents import (
                test_modular_system_initialization,
                test_error_handling,
                test_component_communication,
                test_concurrent_performance,
            )

            print("🔗 Testing Full System Integration...")
            print("-" * 40)

            # Run system tests
            system_tests = [
                ("System Initialization", test_modular_system_initialization),
                ("Error Handling", test_error_handling),
                ("Component Communication", test_component_communication),
                ("Concurrent Performance", test_concurrent_performance),
            ]

            passed = 0
            total = len(system_tests)

            for test_name, test_func in system_tests:
                try:
                    result = await test_func()
                    if result:
                        passed += 1
                        print(f"✅ {test_name}")
                    else:
                        print(f"❌ {test_name}")
                except Exception as e:
                    print(f"❌ {test_name}: {e}")

            self.log_suite_result(
                "Full System",
                {
                    "total": total,
                    "passed": passed,
                    "failed": total - passed,
                    "skipped": 0,
                    "success_rate": (passed / total * 100) if total > 0 else 0,
                },
            )

            return passed >= total * 0.75  # 75% pass rate for system tests

        except Exception as e:
            print(f"❌ Full system tests failed: {e}")
            self.log_suite_result(
                "Full System",
                {
                    "total": 0,
                    "passed": 0,
                    "failed": 1,
                    "skipped": 0,
                    "success_rate": 0,
                    "error": str(e),
                },
            )
            return False

    async def run_smoke_tests(self) -> bool:
        """Run quick smoke tests to verify basic functionality."""
        try:
            from src.slack_meta_agent.main_modular import (
                create_modular_slack_meta_agent,
            )

            print("💨 Running Smoke Tests...")
            print("-" * 40)

            # Basic smoke test - can we create and initialize the system?
            system = None
            try:
                system = await create_modular_slack_meta_agent(
                    supabase_project_id="smoke_test", mcp_app=None
                )

                # Can we handle a basic message?
                response = await system.handle_message(
                    "Hello, this is a smoke test",
                    user_id="smoke_user",
                    channel_id="smoke_channel",
                )

                success = bool(response and len(response.strip()) > 0)

                self.log_suite_result(
                    "Smoke Tests",
                    {
                        "total": 1,
                        "passed": 1 if success else 0,
                        "failed": 0 if success else 1,
                        "skipped": 0,
                        "success_rate": 100 if success else 0,
                    },
                )

                return success

            finally:
                if system:
                    await system.cleanup()

        except Exception as e:
            print(f"❌ Smoke tests failed: {e}")
            self.log_suite_result(
                "Smoke Tests",
                {
                    "total": 1,
                    "passed": 0,
                    "failed": 1,
                    "skipped": 0,
                    "success_rate": 0,
                    "error": str(e),
                },
            )
            return False

    def check_environment(self) -> Dict[str, Any]:
        """Check the test environment and dependencies."""
        print("🔍 Checking Test Environment...")
        print("-" * 40)

        env_status = {
            "python_version": sys.version,
            "working_directory": os.getcwd(),
            "can_import_main": False,
            "can_import_agents": False,
            "can_import_orchestrator": False,
            "supabase_config_exists": False,
            "mcp_config_exists": False,
        }

        # Check imports
        try:
            from src.slack_meta_agent.main_modular import ModularSlackMetaAgent

            env_status["can_import_main"] = True
            print("✅ Can import main modular system")
        except ImportError as e:
            print(f"❌ Cannot import main modular system: {e}")

        try:
            from src.slack_meta_agent.agents.registry import AgentRegistryAgent

            env_status["can_import_agents"] = True
            print("✅ Can import agent modules")
        except ImportError as e:
            print(f"❌ Cannot import agent modules: {e}")

        try:
            from src.slack_meta_agent.core.orchestrator import Orchestrator

            env_status["can_import_orchestrator"] = True
            print("✅ Can import orchestrator")
        except ImportError as e:
            print(f"❌ Cannot import orchestrator: {e}")

        # Check configuration files
        config_paths = [
            "config/mcp_agent.config.yaml",
            "mcp_agent.config.yaml",
            "slack_meta_agent/config/mcp_agent.config.yaml",
        ]

        for config_path in config_paths:
            if os.path.exists(config_path):
                env_status["mcp_config_exists"] = True
                print(f"✅ Found MCP config at {config_path}")
                break
        else:
            print("⚠️ No MCP config file found")

        secrets_paths = [
            "config/mcp_agent.secrets.yaml",
            "mcp_agent.secrets.yaml",
            "slack_meta_agent/config/mcp_agent.secrets.yaml",
        ]

        for secrets_path in secrets_paths:
            if os.path.exists(secrets_path):
                env_status["supabase_config_exists"] = True
                print(f"✅ Found secrets config at {secrets_path}")
                break
        else:
            print("⚠️ No secrets file found (tests will use mocks)")

        return env_status

    def generate_final_report(self):
        """Generate comprehensive final test report."""
        execution_time = (self.end_time - self.start_time).total_seconds()

        print("\n" + "=" * 80)
        print("📊 COMPREHENSIVE TEST RESULTS - MODULAR SLACK META-AGENT")
        print("=" * 80)

        print(f"🕒 Execution Time: {execution_time:.2f} seconds")
        print(f"📦 Test Suites Run: {len(self.test_suites)}")
        print()

        # Overall summary
        total = self.overall_results["total_tests"]
        passed = self.overall_results["passed_tests"]
        failed = self.overall_results["failed_tests"]
        skipped = self.overall_results["skipped_tests"]
        success_rate = (passed / total * 100) if total > 0 else 0

        print(f"📈 OVERALL SUMMARY:")
        print(f"   Total Tests: {total}")
        print(f"   Passed: {passed}")
        print(f"   Failed: {failed}")
        print(f"   Skipped: {skipped}")
        print(f"   Success Rate: {success_rate:.1f}%")
        print()

        # Suite-by-suite breakdown
        print(f"📋 SUITE BREAKDOWN:")
        for suite_name, results in self.overall_results["suite_results"].items():
            status = "✅" if results.get("success_rate", 0) >= 70 else "❌"
            print(
                f"   {status} {suite_name}: {results.get('passed', 0)}/{results.get('total', 0)} "
                f"({results.get('success_rate', 0):.1f}%)"
            )

            if "error" in results:
                print(f"       Error: {results['error']}")

        print()

        # Final assessment
        print(f"🎯 FINAL ASSESSMENT:")

        if success_rate >= 90:
            print("   🎉 EXCELLENT - System is production ready!")
            assessment = "excellent"
        elif success_rate >= 80:
            print("   ✅ GOOD - System is functional with minor issues")
            assessment = "good"
        elif success_rate >= 60:
            print("   ⚠️ FAIR - System needs attention before deployment")
            assessment = "fair"
        else:
            print("   ❌ POOR - System requires significant fixes")
            assessment = "poor"

        print()

        # Recommendations
        print(f"💡 RECOMMENDATIONS:")

        if assessment == "excellent":
            print("   • System is ready for production deployment")
            print("   • Consider setting up monitoring and alerting")
            print("   • Implement gradual rollout procedures")

        elif assessment == "good":
            print("   • Review failed tests and implement fixes")
            print("   • System can be deployed with careful monitoring")
            print("   • Consider additional testing in staging environment")

        elif assessment == "fair":
            print("   • Address failing tests before deployment")
            print("   • Implement comprehensive error handling")
            print("   • Add more robust fallback mechanisms")

        else:
            print("   • Extensive fixes required before deployment")
            print("   • Review system architecture and implementation")
            print("   • Consider additional development resources")

        print()

        # Next steps
        print(f"🚀 NEXT STEPS:")
        print(f"   1. Review any failed tests in detail")
        print(f"   2. Set up proper Supabase credentials for full functionality")
        print(f"   3. Configure MCP servers for complete testing")
        print(f"   4. Test with real Slack integration")
        print(f"   5. Implement continuous integration testing")

        return success_rate >= 70  # 70% overall success rate required

    async def run_all_tests(self) -> bool:
        """Run all test suites and generate comprehensive report."""
        self.start_time = datetime.now()

        print("🧪 COMPREHENSIVE MODULAR SLACK META-AGENT TEST SUITE")
        print("=" * 80)
        print(
            f"🚀 Starting comprehensive test run at {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        print()

        # Check environment first
        env_status = self.check_environment()
        can_run_tests = (
            env_status["can_import_main"]
            and env_status["can_import_agents"]
            and env_status["can_import_orchestrator"]
        )

        if not can_run_tests:
            print("❌ Environment check failed. Cannot run tests.")
            self.end_time = datetime.now()
            return False

        # Configure logging for test run
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

        # Run test suites in order
        test_suites = [
            ("Smoke Tests", self.run_smoke_tests),
            ("Individual Agents", self.run_individual_agent_tests),
            ("Orchestrator & Integration", self.run_orchestrator_tests),
            ("Full System", self.run_full_system_tests),
        ]

        suite_results = []

        for suite_name, test_function in test_suites:
            result = await self.run_test_suite(suite_name, test_function)
            suite_results.append(result)

            # Short delay between suites
            await asyncio.sleep(1)

        self.end_time = datetime.now()

        # Generate final report
        overall_success = self.generate_final_report()

        return overall_success


async def main():
    """Main entry point for the comprehensive test runner."""
    runner = ComprehensiveTestRunner()

    try:
        success = await runner.run_all_tests()
        return success

    except KeyboardInterrupt:
        print("\n🛑 Test run interrupted by user")
        return False

    except Exception as e:
        print(f"\n❌ Test runner failed with unexpected error: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    # Change to the correct directory for testing
    test_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(test_dir)

    success = asyncio.run(main())

    # Exit with appropriate code for CI/CD
    sys.exit(0 if success else 1)
