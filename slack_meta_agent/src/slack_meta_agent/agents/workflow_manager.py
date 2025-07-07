"""
Workflow Manager Agent - Handles all database-driven workflows.

This component is responsible for:
1. Detecting workflow triggers from messages
2. Executing different types of workflows (n8n, internal, sequential, CrewAI)
3. Managing workflow state and execution
4. Tracking workflow usage and performance

Extracted from SlackMetaAgent to provide clean separation of concerns.
"""

import aiohttp
from datetime import datetime
from typing import Dict, List, Optional, Any, TYPE_CHECKING

from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

from ..core.types import AgentComponent

# Import CrewAI for multi-agent workflows
try:
    from crewai import Agent as CrewAgent, Task, Crew, Process
    from crewai.tools import BaseTool

    CREWAI_AVAILABLE = True
except ImportError:
    # Set to None for runtime checks
    CrewAgent = None
    Task = None
    Crew = None
    Process = None
    BaseTool = None
    CREWAI_AVAILABLE = False

# Try to import crewai_tools separately as it may not always be available
try:
    from crewai_tools import SerperDevTool, WebsiteSearchTool  # type: ignore
except ImportError:
    SerperDevTool = None
    WebsiteSearchTool = None

# Import types for type checking only
if TYPE_CHECKING:
    pass  # Using Any for return types to avoid import complexity


class WorkflowManagerAgent(AgentComponent):
    """
    Manages all database-driven workflow detection and execution.

    Supports multiple workflow types including n8n webhooks, internal
    sequential workflows, and CrewAI multi-agent orchestration.
    """

    # Constants
    DEFAULT_WEBHOOK_TIMEOUT = 60  # seconds
    DEFAULT_CONFIDENCE_THRESHOLD = 0.7
    PARTIAL_MATCH_SCORE = 0.8
    MULTI_PATTERN_BOOST = 1.1

    def __init__(
        self, pool_manager=None, db_ops=None, agent_registry=None, mcp_app=None
    ):
        super().__init__("WorkflowManager")

        self.pool_manager = pool_manager
        self.db_ops = db_ops
        self.agent_registry = agent_registry or {}
        self.mcp_app = mcp_app

        # Configuration
        self.confidence_threshold = self.DEFAULT_CONFIDENCE_THRESHOLD

    def _get_workflow_name(self, workflow: Dict, default: str = "Unknown") -> str:
        """Helper method to get workflow name with fallback."""
        return workflow.get("name", default)

    def _format_workflow_result(self, status: str, workflow: Dict, content: str) -> str:
        """Helper method to format workflow results consistently."""
        workflow_name = self._get_workflow_name(workflow)
        return f"{status} **{workflow_name}**\n\n{content}"

    async def check_workflow_triggers(
        self, message: str, context: Dict
    ) -> Optional[Dict]:
        """Check if message triggers any workflows from database."""
        try:
            # Get workflows from database
            workflows = None
            if self.pool_manager:
                workflows = await self.pool_manager.get_workflows_by_trigger(
                    message.lower().split()
                )
            elif self.db_ops:
                workflows = await self.db_ops.get_workflows_by_trigger(
                    message.lower().split()
                )
            else:
                self.logger.warning(
                    "No database access available for workflow triggers"
                )
                return None

            if not workflows or not workflows.get("success"):
                return None

            workflow_list = workflows.get("data", [])
            if not workflow_list:
                return None

            message_words = set(message.lower().split())
            best_match = None
            best_confidence = 0.0

            for workflow in workflow_list:
                if not workflow.get("is_active", True):
                    continue

                trigger_patterns = workflow.get("trigger_patterns", [])
                if not trigger_patterns:
                    continue

                # Calculate confidence based on trigger pattern matching
                confidence = self._calculate_workflow_confidence(
                    message, workflow, message_words
                )

                if (
                    confidence > best_confidence
                    and confidence >= self.confidence_threshold
                ):
                    best_confidence = confidence
                    best_match = {
                        "workflow": workflow,
                        "confidence": confidence,
                        "matched_patterns": [
                            p
                            for p in trigger_patterns
                            if any(word in message.lower() for word in p.split())
                        ],
                    }

            if best_match:
                self.logger.info(
                    f"🔄 Workflow match: {best_match['workflow']['name']} "
                    f"(confidence: {best_confidence:.2f})"
                )

                # Update workflow usage statistics
                await self._update_workflow_usage(
                    best_match["workflow"]["id"], success=None
                )

            return best_match

        except Exception as e:
            self.logger.error(f"Workflow trigger checking error: {e}")
            return None

    async def execute_workflow(
        self, workflow: Dict, user_message: str, context: Dict
    ) -> str:
        """Execute workflow based on type."""
        workflow_type = workflow.get("type", "unknown")
        workflow_name = workflow.get("name", "unnamed_workflow")

        try:
            self.logger.info(f"🔄 Executing {workflow_type} workflow: {workflow_name}")

            # Execute workflow based on type
            workflow_executors = {
                "n8n": self._execute_n8n_workflow,
                "internal": self._execute_internal_workflow,
                "sequential": self._execute_internal_workflow,  # Sequential is same as internal
                "crewai": self._execute_crew_workflow,
            }

            executor = workflow_executors.get(workflow_type)
            if executor:
                result = await executor(workflow, user_message, context)
            else:
                result = f"❌ Unknown workflow type: {workflow_type}"
                await self._update_workflow_usage(workflow.get("id"), success=False)
                return result

            # Update success metrics
            await self._update_workflow_usage(workflow.get("id"), success=True)
            return result

        except Exception as e:
            self.logger.error(f"Workflow execution error: {e}")
            await self._update_workflow_usage(workflow.get("id"), success=False)
            return f"❌ Workflow execution failed: {str(e)}"

    # Private methods

    def _calculate_workflow_confidence(
        self, message: str, workflow: Dict, message_words: set
    ) -> float:
        """Calculate confidence score for workflow trigger matching."""
        trigger_patterns = workflow.get("trigger_patterns", [])
        if not trigger_patterns:
            return 0.0

        total_score = 0.0
        pattern_count = 0

        for pattern in trigger_patterns:
            if not pattern:
                continue

            pattern_words = set(pattern.lower().split())

            # Exact pattern match gets highest score
            if pattern.lower() in message.lower():
                total_score += 1.0
                pattern_count += 1
                continue

            # Partial word matching
            matching_words = message_words.intersection(pattern_words)
            if matching_words:
                # Score based on percentage of pattern words matched
                word_match_score = len(matching_words) / len(pattern_words)
                total_score += word_match_score * self.PARTIAL_MATCH_SCORE
                pattern_count += 1

        if pattern_count == 0:
            return 0.0

        # Average confidence across all patterns
        base_confidence = total_score / pattern_count

        # Boost confidence if multiple patterns match
        if pattern_count > 1:
            base_confidence = min(1.0, base_confidence * self.MULTI_PATTERN_BOOST)

        return base_confidence

    async def _execute_n8n_workflow(
        self, workflow: Dict, user_message: str, context: Dict
    ) -> str:
        """Execute n8n workflow via webhook."""
        try:
            webhook_url = workflow.get("webhook_url")
            if not webhook_url:
                return "❌ n8n workflow missing webhook URL"

            payload = {
                "user_message": user_message,
                "user_context": context or {},
                "timestamp": datetime.utcnow().isoformat(),
                "workflow_name": workflow.get("name", "unknown"),
                "metadata": {
                    "triggered_by": "slack_meta_agent",
                    "workflow_id": workflow.get("id"),
                    "execution_time": datetime.utcnow().isoformat(),
                },
            }

            self.logger.info(f"🌐 Calling n8n webhook: {webhook_url}")

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.DEFAULT_WEBHOOK_TIMEOUT),
                ) as response:
                    if response.status == 200:
                        result_data = await response.json()
                        output = result_data.get(
                            "output",
                            result_data.get(
                                "result", "n8n workflow completed successfully"
                            ),
                        )

                        if isinstance(output, dict):
                            output = "\n".join(
                                [f"**{k}:** {v}" for k, v in output.items()]
                            )

                        return f"✅ **n8n Workflow Complete: {workflow.get('name', 'Unknown')}**\n\n{output}"

                    elif response.status == 202:
                        return f"🔄 **n8n Workflow Started: {workflow.get('name', 'Unknown')}**\n\nWorkflow is running asynchronously."

                    else:
                        error_text = await response.text()
                        return f"❌ **n8n Workflow Failed: {workflow.get('name', 'Unknown')}**\n\nHTTP {response.status}: {error_text}"

        except aiohttp.ClientTimeout:
            return f"⏱️ **n8n Workflow Timeout: {workflow.get('name', 'Unknown')}**\n\nThe workflow is taking longer than expected."
        except Exception as e:
            return f"❌ **n8n Workflow Error: {workflow.get('name', 'Unknown')}**\n\n{str(e)}"

    async def _execute_internal_workflow(
        self, workflow: Dict, user_message: str, context: Dict
    ) -> str:
        """Execute internal workflow with sequential agent steps."""
        try:
            definition = workflow.get("definition", {})
            steps = definition.get("steps", [])

            if not steps:
                return f"❌ Internal workflow '{workflow.get('name', 'Unknown')}' has no steps defined"

            results = []
            current_context = context.copy() if context else {}
            current_context["original_message"] = user_message

            self.logger.info(f"🔄 Executing {len(steps)} workflow steps")

            for i, step in enumerate(steps, 1):
                step_type = step.get("type", "unknown")
                agent_name = step.get("agent", "data_researcher")
                params = step.get("params", {})

                self.logger.info(
                    f"Step {i}/{len(steps)}: {step_type} with {agent_name}"
                )

                try:
                    # Get agent for this step
                    if agent_name in self.agent_registry:
                        agent_spec = self.agent_registry[agent_name]

                        # Create agent for this step
                        step_agent = Agent(
                            name=f"workflow_step_{i}_{agent_name}",
                            instruction=agent_spec.instruction,
                            server_names=agent_spec.server_names,
                            context=self.mcp_app.context if self.mcp_app else None,
                        )

                        async with step_agent:
                            llm = await step_agent.attach_llm(OpenAIAugmentedLLM)

                            # Create step-specific prompt
                            step_prompt = f"""
                            Workflow Step {i}: {step_type}
                            
                            Original User Request: {user_message}
                            Step Parameters: {params}
                            Previous Results: {results[-1] if results else "None"}
                            
                            Execute this workflow step and provide the specific output needed for the next step.
                            """

                            step_result = await llm.generate_str(step_prompt)
                            results.append(
                                {
                                    "step": i,
                                    "type": step_type,
                                    "agent": agent_name,
                                    "result": step_result,
                                }
                            )

                    else:
                        results.append(
                            {
                                "step": i,
                                "type": step_type,
                                "agent": agent_name,
                                "result": f"❌ Agent '{agent_name}' not found",
                            }
                        )

                except Exception as step_error:
                    self.logger.error(f"Workflow step {i} error: {step_error}")
                    results.append(
                        {
                            "step": i,
                            "type": step_type,
                            "agent": agent_name,
                            "result": f"❌ Step failed: {str(step_error)}",
                        }
                    )

            # Format final result
            workflow_result = f"✅ **Internal Workflow Complete: {workflow.get('name', 'Unknown')}**\n\n"

            for result in results:
                workflow_result += f"**Step {result['step']} ({result['type']}):** {result['result']}\n\n"

            return workflow_result

        except Exception as e:
            return f"❌ **Internal Workflow Error: {workflow.get('name', 'Unknown')}**\n\n{str(e)}"

    async def _execute_crew_workflow(
        self, workflow: Dict, user_message: str, context: Dict
    ) -> str:
        """Execute CrewAI multi-agent workflow."""
        if not CREWAI_AVAILABLE:
            return "❌ **CrewAI Not Available**: CrewAI is required for this workflow but not installed."

        try:
            workflow_name = workflow.get("name", "Unknown CrewAI Workflow")
            self.logger.info(f"🚀 Executing CrewAI Workflow: {workflow_name}")

            # Check if this is a crew_configs workflow or legacy workflows table
            crew_config = await self._get_crew_config(workflow)
            if crew_config:
                return await self._execute_configured_crew(
                    crew_config, user_message, context
                )
            else:
                return await self._execute_dynamic_crew(workflow, user_message, context)

        except Exception as e:
            self.logger.error(f"CrewAI workflow execution failed: {e}")
            return f"❌ **CrewAI Workflow Error: {workflow.get('name', 'Unknown')}**\n\n{str(e)}"

    async def _get_crew_config(self, workflow: Dict) -> Optional[Dict]:
        """Get crew configuration from crew_configs table if available."""
        try:
            if not self.pool_manager:
                return None

            # First check if workflow has a crew_config_id
            crew_config_id = workflow.get("crew_config_id")
            if crew_config_id:
                result = await self.pool_manager.get_crew_config_by_id(crew_config_id)
                if result.get("success") and result.get("data"):
                    return result["data"][0]

            # Otherwise try to find by workflow name
            workflow_name = workflow.get("name")
            if workflow_name:
                result = await self.pool_manager.get_crew_configs_by_name(workflow_name)
                if result.get("success") and result.get("data"):
                    return result["data"][0]

            return None

        except Exception as e:
            self.logger.warning(f"Could not get crew config: {e}")
            return None

    async def _execute_configured_crew(
        self, crew_config: Dict, user_message: str, context: Dict
    ) -> str:
        """Execute a crew from the crew_configs table."""
        try:
            # Parse the stored crew configuration
            crew_data = crew_config.get("crew_data", {})
            agents_data = crew_data.get("agents", [])
            tasks_data = crew_data.get("tasks", [])

            if not agents_data or not tasks_data:
                return "❌ **Invalid Crew Configuration**: Missing agents or tasks in crew_configs"

            # Create CrewAI agents
            crew_agents = []
            for agent_data in agents_data:
                crew_agent = await self._create_crew_agent(agent_data, context)
                if crew_agent:
                    crew_agents.append(crew_agent)

            if not crew_agents:
                return "❌ **No Valid Agents**: Could not create any CrewAI agents from configuration"

            # Create CrewAI tasks
            crew_tasks = []
            for task_data in tasks_data:
                task = await self._create_crew_task(
                    task_data, crew_agents, user_message
                )
                if task:
                    crew_tasks.append(task)

            if not crew_tasks:
                return "❌ **No Valid Tasks**: Could not create any CrewAI tasks from configuration"

            # Create and execute the crew
            crew = Crew(
                agents=crew_agents,
                tasks=crew_tasks,
                process=Process.sequential,
                verbose=True,
            )

            # Execute the crew
            result = crew.kickoff()

            return f"✅ **CrewAI Workflow Complete: {crew_config.get('name', 'Unknown')}**\n\n{result}"

        except Exception as e:
            self.logger.error(f"Configured crew execution failed: {e}")
            return f"❌ **Configured Crew Error**: {str(e)}"

    async def _execute_dynamic_crew(
        self, workflow: Dict, user_message: str, context: Dict
    ) -> str:
        """Create and execute a dynamic crew based on workflow configuration."""
        try:
            # Create a basic crew from workflow information
            agents_needed = workflow.get(
                "required_agents", ["data_researcher", "communication_specialist"]
            )

            # Create CrewAI agents for each required agent type
            crew_agents = []
            for agent_type in agents_needed:
                agent_spec = self.agent_registry.get(agent_type)
                if agent_spec:
                    crew_agent = await self._create_crew_agent_from_spec(
                        agent_spec, context
                    )
                    if crew_agent:
                        crew_agents.append(crew_agent)

            if not crew_agents:
                return f"❌ **No Valid Agents**: Could not create CrewAI agents for types: {agents_needed}"

            # Create a basic task for the crew
            main_task = Task(
                description=f"""
                Collaborate to fulfill this user request: {user_message}
                
                Workflow context: {workflow.get("description", "No description available")}
                
                Each agent should contribute their specialized expertise to provide a comprehensive response.
                """,
                agent=crew_agents[0],
                expected_output="A comprehensive response that fulfills the user's request using the combined expertise of all agents.",
            )

            # Create and execute the crew
            crew = Crew(
                agents=crew_agents,
                tasks=[main_task],
                process=Process.sequential,
                verbose=True,
            )

            # Execute the crew
            result = crew.kickoff()

            return f"✅ **Dynamic CrewAI Workflow Complete: {workflow.get('name', 'Unknown')}**\n\n{result}"

        except Exception as e:
            self.logger.error(f"Dynamic crew execution failed: {e}")
            return f"❌ **Dynamic Crew Error**: {str(e)}"

    async def _create_crew_agent(
        self, agent_data: Dict, context: Dict
    ) -> Optional[Any]:
        """Create a CrewAI agent from configuration data."""
        try:
            if not CREWAI_AVAILABLE:
                return None

            # Extract agent configuration
            role = agent_data.get("role", "Assistant")
            goal = agent_data.get("goal", "Help the user achieve their objectives")
            backstory = agent_data.get("backstory", "I am a helpful AI assistant")
            tools = agent_data.get("tools", [])

            # Create tools list (can be expanded with MCP tools)
            agent_tools = []
            for tool_name in tools:
                tool = await self._create_crew_tool(tool_name, context)
                if tool:
                    agent_tools.append(tool)

            # Create the CrewAI agent
            crew_agent = CrewAgent(
                role=role,
                goal=goal,
                backstory=backstory,
                tools=agent_tools,
                verbose=True,
                allow_delegation=False,
            )

            return crew_agent

        except Exception as e:
            self.logger.error(f"Failed to create crew agent: {e}")
            return None

    async def _create_crew_agent_from_spec(
        self, agent_spec, context: Dict
    ) -> Optional[Any]:
        """Create a CrewAI agent from an AgentSpec."""
        try:
            if not CREWAI_AVAILABLE:
                return None

            # Convert AgentSpec to CrewAI agent
            role = agent_spec.name.replace("_", " ").title()
            goal = f"Utilize {agent_spec.name} capabilities to help users with {', '.join(agent_spec.capabilities)}"
            backstory = agent_spec.instruction

            # Create basic tools (can be enhanced with MCP integration)
            tools = []

            # Create the CrewAI agent
            crew_agent = CrewAgent(
                role=role,
                goal=goal,
                backstory=backstory,
                tools=tools,
                verbose=True,
                allow_delegation=False,
            )

            return crew_agent

        except Exception as e:
            self.logger.error(f"Failed to create crew agent from spec: {e}")
            return None

    async def _create_crew_task(
        self, task_data: Dict, agents: List[Any], user_message: str
    ) -> Optional[Any]:
        """Create a CrewAI task from configuration data."""
        try:
            if not CREWAI_AVAILABLE or not agents:
                return None

            description = task_data.get("description", "").format(
                user_message=user_message
            )
            expected_output = task_data.get(
                "expected_output", "Complete the assigned task successfully"
            )
            agent_index = task_data.get("agent_index", 0)

            # Ensure agent index is valid
            if agent_index >= len(agents):
                agent_index = 0

            task = Task(
                description=description,
                agent=agents[agent_index],
                expected_output=expected_output,
            )

            return task

        except Exception as e:
            self.logger.error(f"Failed to create crew task: {e}")
            return None

    async def _create_crew_tool(self, tool_name: str, context: Dict) -> Optional[Any]:
        """Create a CrewAI tool by name."""
        try:
            if not CREWAI_AVAILABLE:
                return None

            # Basic tool mapping (can be expanded)
            if tool_name == "web_search" and SerperDevTool:
                return SerperDevTool()
            elif tool_name == "website_search" and WebsiteSearchTool:
                return WebsiteSearchTool()

            # Could add MCP tool integration here
            return None

        except Exception as e:
            self.logger.error(f"Failed to create crew tool {tool_name}: {e}")
            return None

    async def _update_workflow_usage(
        self, workflow_id: str, success: Optional[bool] = None
    ):
        """Update workflow usage statistics."""
        try:
            if not self.pool_manager or not workflow_id:
                return

            await self.pool_manager.update_workflow_usage(workflow_id, success)

        except Exception as e:
            self.logger.error(f"Failed to update workflow usage: {e}")

    async def health_check(self) -> Dict[str, Any]:
        """Check component health."""
        base_health = await super().health_check()

        return {
            **base_health,
            "pool_manager_available": self.pool_manager is not None,
            "db_ops_available": self.db_ops is not None,
            "agent_registry_size": len(self.agent_registry),
            "mcp_app_available": self.mcp_app is not None,
            "crewai_available": CREWAI_AVAILABLE,
            "confidence_threshold": self.confidence_threshold,
        }
