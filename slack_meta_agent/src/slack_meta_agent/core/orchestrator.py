"""
Orchestrator - The powerful execution brain that builds plans and coordinates agents.

This component is responsible for:
1. Receiving intent analysis from IntentAnalyzer
2. Using a powerful LLM to build execution plans
3. Creating specific tasks for agents
4. Monitoring task completion
5. Validating that the original query was answered
6. Adjusting plans as needed

This is the "execution brain" that ensures queries are answered completely.
"""

import asyncio
import json
from datetime import datetime
from typing import Dict, List, Optional, Any

from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
from mcp_agent.workflows.orchestrator.orchestrator import (
    Orchestrator as MCPOrchestrator,
)

from .types import (
    IncomingMessage,
    ExecutionResult,
    Intent,
    ExecutionStrategy,
    ConfidenceLevel,
    AgentComponent,
)
from ..agents.intent_analyzer import IntentAnalyzerAgent
from ..agents.registry import AgentRegistryAgent
from ..agents.tool_discovery import ToolDiscoveryAgent
from ..agents.pool_manager import PoolManagerAgent
from ..agents.workflow_manager import WorkflowManagerAgent
from .agent_logging import AgentLoggingMixin


class ExecutionPlan:
    """Represents a complete execution plan with tasks and validation criteria."""

    def __init__(self, plan_id: str, original_query: str, intent: Intent):
        self.plan_id = plan_id
        self.original_query = original_query
        self.intent = intent
        self.tasks: List[Dict[str, Any]] = []
        self.success_criteria: List[str] = []
        self.created_at = datetime.now()
        self.status = "created"
        self.adjustments: List[Dict[str, Any]] = []

    def add_task(
        self,
        task_id: str,
        agent_type: str,
        description: str,
        inputs: Dict[str, Any],
        dependencies: List[str] = None,
    ):
        """Add a task to the execution plan."""
        task = {
            "task_id": task_id,
            "agent_type": agent_type,
            "description": description,
            "inputs": inputs,
            "dependencies": dependencies or [],
            "status": "pending",
            "result": None,
            "logs": [],
            "created_at": datetime.now().isoformat(),
            "started_at": None,
            "completed_at": None,
        }
        self.tasks.append(task)

    def add_success_criterion(self, criterion: str):
        """Add a success criterion for plan validation."""
        self.success_criteria.append(criterion)

    def record_adjustment(self, reason: str, changes: Dict[str, Any]):
        """Record a plan adjustment."""
        self.adjustments.append(
            {
                "timestamp": datetime.now().isoformat(),
                "reason": reason,
                "changes": changes,
            }
        )


class Orchestrator(AgentComponent):
    """
    The powerful execution orchestrator that builds plans and coordinates agents.

    Uses a powerful LLM to analyze queries and intents, build execution plans,
    create specific tasks, monitor execution, and validate results.
    """

    def __init__(
        self,
        intent_analyzer: IntentAnalyzerAgent,
        registry: AgentRegistryAgent,
        tool_discovery: ToolDiscoveryAgent,
        pool_manager: PoolManagerAgent,
        workflow_manager: WorkflowManagerAgent,
        mcp_app=None,
    ):
        super().__init__("Orchestrator")

        # Core components
        self.intent_analyzer = intent_analyzer
        self.registry = registry
        self.tool_discovery = tool_discovery
        self.pool_manager = pool_manager
        self.workflow_manager = workflow_manager
        self.mcp_app = mcp_app

        # Powerful LLM for plan building (using GPT-4 or similar)
        self.planner_llm = None

        # State tracking
        self.active_plans: Dict[str, ExecutionPlan] = {}
        self.execution_metrics: Dict[str, Any] = {
            "total_plans": 0,
            "successful_plans": 0,
            "failed_plans": 0,
            "average_execution_time": 0.0,
            "total_adjustments": 0,
        }

    async def initialize(self):
        """Initialize the orchestrator with a powerful LLM."""
        try:
            # Initialize powerful LLM for plan building
            # We'll use a high-capability model for planning
            self.planner_llm = OpenAIAugmentedLLM(
                model="gpt-4o",  # Use most capable model for planning
                temperature=0.1,  # Lower temperature for more consistent planning
                max_tokens=4000,  # Higher token limit for detailed plans
            )

            self.logger.info("🧠 Orchestrator initialized with powerful planning LLM")

        except Exception as e:
            self.logger.error(f"Failed to initialize orchestrator: {e}")
            raise

    async def execute_with_intent(
        self, incoming: IncomingMessage, intent: Intent
    ) -> ExecutionResult:
        """
        Main execution method - builds plan and coordinates execution.

        This is the core method that:
        1. Analyzes query and intent with powerful LLM
        2. Builds detailed execution plan
        3. Creates specific tasks for agents
        4. Monitors execution
        5. Validates results
        6. Makes adjustments if needed
        """
        start_time = datetime.now()
        plan_id = f"plan_{int(start_time.timestamp())}_{incoming.context.user_id}"

        try:
            self.logger.info(f"🎭 Starting plan execution for: {incoming.text[:50]}...")

            # Step 1: Build execution plan with powerful LLM
            plan = await self._build_execution_plan(incoming, intent, plan_id)
            self.active_plans[plan_id] = plan
            self.execution_metrics["total_plans"] += 1

            self.logger.info(f"📋 Plan created with {len(plan.tasks)} tasks")

            # Step 2: Execute tasks
            execution_results = await self._execute_plan(plan)

            # Step 3: Validate results against original query
            validation_result = await self._validate_execution(plan, execution_results)

            # Step 4: Make adjustments if needed
            if not validation_result["complete"]:
                self.logger.info("🔄 Query not fully answered, making adjustments...")
                adjusted_results = await self._adjust_and_retry(
                    plan, execution_results, validation_result
                )
                execution_results.update(adjusted_results)

                # Re-validate after adjustments
                validation_result = await self._validate_execution(
                    plan, execution_results
                )

            # Step 5: Compile final response
            final_response = await self._compile_final_response(
                plan, execution_results, validation_result
            )

            # Calculate metrics
            execution_time = (datetime.now() - start_time).total_seconds()
            self.execution_metrics["successful_plans"] += 1
            self._update_average_execution_time(execution_time)

            self.logger.info(
                f"✅ Plan {plan_id} completed successfully in {execution_time:.2f}s"
            )

            return ExecutionResult(
                success=True,
                response=final_response,
                execution_time=execution_time,
                intent_confidence=intent.confidence,
                agent_count=len(plan.tasks),
                metadata={
                    "plan_id": plan_id,
                    "tasks_completed": len(
                        [t for t in plan.tasks if t["status"] == "completed"]
                    ),
                    "adjustments_made": len(plan.adjustments),
                    "validation_score": validation_result.get("score", 0.0),
                    "intent_name": intent.name,
                },
            )

        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            self.execution_metrics["failed_plans"] += 1

            self.logger.error(
                f"❌ Plan {plan_id} failed after {execution_time:.2f}s: {e}"
            )

            return ExecutionResult(
                success=False,
                response=f"I encountered an error while processing your request: {str(e)}",
                execution_time=execution_time,
                intent_confidence=intent.confidence,
                agent_count=0,
                error=str(e),
                metadata={
                    "plan_id": plan_id,
                    "error": str(e),
                    "execution_stage": "plan_execution",
                },
            )

        finally:
            # Cleanup
            if plan_id in self.active_plans:
                del self.active_plans[plan_id]

    async def _build_execution_plan(
        self, incoming: IncomingMessage, intent: Intent, plan_id: str
    ) -> ExecutionPlan:
        """Build a detailed execution plan using powerful LLM."""

        # Get available agents and their capabilities
        available_agents = await self.registry.get_agent_specs()
        available_tools = self.tool_discovery.get_cached_catalog()

        # Convert AgentSpec objects to serializable dictionaries
        serializable_agents = {}
        for name, spec in available_agents.items():
            if hasattr(spec, "__dict__"):
                # Convert AgentSpec to dictionary
                serializable_agents[name] = {
                    "name": getattr(spec, "name", name),
                    "capabilities": getattr(spec, "capabilities", []),
                    "instruction": getattr(spec, "instruction", ""),
                    "server_names": getattr(spec, "server_names", []),
                }
            else:
                # Already a dictionary
                serializable_agents[name] = spec

        # Build context for the planner
        planning_prompt = f"""
You are an expert execution planner. Analyze the user's query and the classified intent, then create a detailed execution plan.

ORIGINAL QUERY: "{incoming.text}"

INTENT ANALYSIS:
- Intent Name: {intent.name}
- Confidence: {intent.confidence.value}
- Suggested Agents: {intent.required_agents}
- Execution Strategy: {intent.execution_strategy.value}
- Reasoning: {intent.reasoning}

AVAILABLE AGENTS:
{json.dumps(serializable_agents, indent=2)}

AVAILABLE TOOLS:
{json.dumps(available_tools, indent=2)}

CONTEXT:
- User ID: {incoming.context.user_id}
- Channel: {incoming.context.channel_id}
- Platform: {incoming.context.platform}

Your task is to create a comprehensive execution plan that will fully answer the user's query. You can:
1. Adjust the suggested agents if needed
2. Add additional agents for completeness
3. Create specific tasks with clear inputs
4. Define success criteria
5. Plan for validation

Return a JSON plan with this structure:
{{
    "analysis": "Your analysis of the query and intent",
    "adjustments": "Any adjustments to the intent analyzer's suggestions",
    "tasks": [
        {{
            "task_id": "task_1",
            "agent_type": "agent_name",
            "description": "Specific task description",
            "inputs": {{"key": "value"}},
            "dependencies": ["task_id_if_any"]
        }}
    ],
    "success_criteria": [
        "Specific criterion 1",
        "Specific criterion 2"
    ],
    "reasoning": "Why this plan will fully answer the query"
}}

Focus on creating a plan that will completely satisfy the user's request with high quality results.
"""

        try:
            # Get plan from powerful LLM
            plan_response = await self.planner_llm.generate_str(planning_prompt)

            # Parse the plan
            plan_data = json.loads(plan_response)

            # Create execution plan object
            plan = ExecutionPlan(plan_id, incoming.text, intent)

            # Add tasks
            for task_data in plan_data["tasks"]:
                plan.add_task(
                    task_id=task_data["task_id"],
                    agent_type=task_data["agent_type"],
                    description=task_data["description"],
                    inputs=task_data["inputs"],
                    dependencies=task_data.get("dependencies", []),
                )

            # Add success criteria
            for criterion in plan_data["success_criteria"]:
                plan.add_success_criterion(criterion)

            # Record any adjustments from intent analysis
            if plan_data.get("adjustments"):
                plan.record_adjustment(
                    reason="Initial plan optimization",
                    changes={"adjustments": plan_data["adjustments"]},
                )

            self.logger.info(
                f"📋 Plan built: {plan_data.get('reasoning', 'No reasoning provided')}"
            )

            return plan

        except Exception as e:
            self.logger.error(f"Failed to build execution plan: {e}")
            # Fallback to simple plan based on intent
            return self._build_fallback_plan(incoming, intent, plan_id)

    async def _execute_plan(self, plan: ExecutionPlan) -> Dict[str, Any]:
        """Execute all tasks in the plan."""
        results = {}

        # Build dependency graph
        task_deps = {task["task_id"]: task["dependencies"] for task in plan.tasks}
        completed_tasks = set()

        while len(completed_tasks) < len(plan.tasks):
            # Find tasks ready to execute (dependencies completed)
            ready_tasks = []
            for task in plan.tasks:
                if task["status"] == "pending" and all(
                    dep in completed_tasks for dep in task["dependencies"]
                ):
                    ready_tasks.append(task)

            if not ready_tasks:
                self.logger.error(
                    "No tasks ready to execute - possible circular dependency"
                )
                break

            # Execute ready tasks in parallel
            task_results = await asyncio.gather(
                *[self._execute_task(task, plan) for task in ready_tasks],
                return_exceptions=True,
            )

            # Process results
            for task, result in zip(ready_tasks, task_results):
                if isinstance(result, Exception):
                    self.logger.error(f"Task {task['task_id']} failed: {result}")
                    task["status"] = "failed"
                    task["result"] = {"error": str(result)}
                else:
                    task["status"] = "completed"
                    task["result"] = result
                    results[task["task_id"]] = result

                task["completed_at"] = datetime.now().isoformat()
                completed_tasks.add(task["task_id"])

        return results

    async def _execute_task(
        self, task: Dict[str, Any], plan: ExecutionPlan
    ) -> Dict[str, Any]:
        """Execute a single task with an agent."""
        task_id = task["task_id"]
        agent_type = task["agent_type"]

        self.logger.info(f"🚀 Executing task {task_id} with {agent_type}")

        task["status"] = "running"
        task["started_at"] = datetime.now().isoformat()

        # Get agent from pool
        agent = await self.pool_manager.get_agent(agent_type, plan.plan_id)

        if not agent:
            raise Exception(f"Could not get agent of type: {agent_type}")

        try:
            async with agent:
                # Configure agent with task-specific LLM if needed
                llm = await self._get_task_optimized_llm(agent, task)

                # Build task prompt
                task_prompt = self._build_task_prompt(task, plan)

                # Execute task
                self.logger.info(
                    f"🤖 Agent {agent.name} executing: {task['description']}"
                )
                result = await llm.generate_str(task_prompt)

                # Log what the agent did
                task["logs"].append(
                    {
                        "timestamp": datetime.now().isoformat(),
                        "agent": agent.name,
                        "action": "task_execution",
                        "description": task["description"],
                        "result_length": len(result) if result else 0,
                    }
                )

                self.logger.info(f"✅ Task {task_id} completed by {agent.name}")

                return {
                    "task_id": task_id,
                    "agent": agent.name,
                    "result": result,
                    "logs": task["logs"],
                }

        except Exception as e:
            task["logs"].append(
                {
                    "timestamp": datetime.now().isoformat(),
                    "agent": agent_type,
                    "action": "task_execution",
                    "error": str(e),
                }
            )
            raise

    async def _get_task_optimized_llm(self, agent: Agent, task: Dict[str, Any]):
        """Get task-optimized LLM for the agent."""
        agent_type = task["agent_type"]

        # Configure LLM based on agent type and task
        if agent_type in ["feedback_collector", "capability_inspector"]:
            # Use faster, cheaper model for simple tasks
            return await agent.attach_llm(
                OpenAIAugmentedLLM, model="gpt-4o-mini", temperature=0.3
            )
        elif agent_type in ["financial_analyst", "code_developer"]:
            # Use more powerful model for complex analysis
            return await agent.attach_llm(
                OpenAIAugmentedLLM, model="gpt-4o", temperature=0.1
            )
        elif agent_type in ["data_researcher", "knowledge_agent"]:
            # Balanced model for research tasks
            return await agent.attach_llm(
                OpenAIAugmentedLLM, model="gpt-4o", temperature=0.2
            )
        else:
            # Default configuration
            return await agent.attach_llm(OpenAIAugmentedLLM)

    def _build_task_prompt(self, task: Dict[str, Any], plan: ExecutionPlan) -> str:
        """Build a specific prompt for the task."""
        return f"""
TASK: {task["description"]}

ORIGINAL QUERY: "{plan.original_query}"

TASK INPUTS:
{json.dumps(task["inputs"], indent=2)}

CONTEXT:
- This is part of a larger plan to answer the user's query
- Your specific role is: {task["description"]}
- Task ID: {task["task_id"]}

INSTRUCTIONS:
1. Focus specifically on this task
2. Provide detailed, actionable results
3. Log your reasoning and actions
4. Be thorough and accurate

Execute this task and provide comprehensive results.
"""

    async def _validate_execution(
        self, plan: ExecutionPlan, results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate that the execution results answer the original query."""

        validation_prompt = f"""
Evaluate whether the execution results fully answer the original user query.

ORIGINAL QUERY: "{plan.original_query}"

INTENT: {plan.intent.name} (confidence: {plan.intent.confidence.value})

SUCCESS CRITERIA:
{json.dumps(plan.success_criteria, indent=2)}

EXECUTION RESULTS:
{json.dumps(results, indent=2)}

Evaluate:
1. Does the execution fully answer the original query?
2. Are all success criteria met?
3. What's missing if anything?
4. Overall quality score (0-1)

Return JSON:
{{
    "complete": true/false,
    "score": 0.0-1.0,
    "criteria_met": ["criterion1", "criterion2"],
    "criteria_missing": ["criterion3"],
    "missing_elements": ["what's missing"],
    "quality_assessment": "detailed assessment",
    "recommendations": ["improvement suggestions"]
}}
"""

        try:
            validation_response = await self.planner_llm.generate_str(validation_prompt)
            return json.loads(validation_response)
        except Exception as e:
            self.logger.error(f"Validation failed: {e}")
            return {
                "complete": True,  # Assume complete if validation fails
                "score": 0.5,
                "quality_assessment": f"Validation failed: {e}",
            }

    async def _adjust_and_retry(
        self, plan: ExecutionPlan, results: Dict[str, Any], validation: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Make adjustments and retry if query wasn't fully answered."""

        adjustment_prompt = f"""
The execution didn't fully answer the user's query. Create additional tasks to complete the answer.

ORIGINAL QUERY: "{plan.original_query}"

CURRENT RESULTS:
{json.dumps(results, indent=2)}

VALIDATION FEEDBACK:
{json.dumps(validation, indent=2)}

Create additional tasks to address the missing elements:

Return JSON:
{{
    "additional_tasks": [
        {{
            "task_id": "adjustment_task_1",
            "agent_type": "agent_name",
            "description": "Specific task description",
            "inputs": {{"key": "value"}},
            "dependencies": []
        }}
    ],
    "reasoning": "Why these tasks will complete the answer"
}}
"""

        try:
            adjustment_response = await self.planner_llm.generate_str(adjustment_prompt)
            adjustment_data = json.loads(adjustment_response)

            # Add adjustment tasks to plan
            for task_data in adjustment_data["additional_tasks"]:
                plan.add_task(
                    task_id=task_data["task_id"],
                    agent_type=task_data["agent_type"],
                    description=task_data["description"],
                    inputs=task_data["inputs"],
                    dependencies=task_data.get("dependencies", []),
                )

            # Record adjustment
            plan.record_adjustment(
                reason="Query not fully answered", changes=adjustment_data
            )

            self.execution_metrics["total_adjustments"] += 1

            # Execute additional tasks
            additional_results = await self._execute_plan(plan)

            return additional_results

        except Exception as e:
            self.logger.error(f"Adjustment failed: {e}")
            return {}

    async def _compile_final_response(
        self, plan: ExecutionPlan, results: Dict[str, Any], validation: Dict[str, Any]
    ) -> str:
        """Compile the final response from all execution results."""

        compilation_prompt = f"""
Compile a comprehensive final response from the execution results.

ORIGINAL QUERY: "{plan.original_query}"

EXECUTION RESULTS:
{json.dumps(results, indent=2)}

VALIDATION:
{json.dumps(validation, indent=2)}

PLAN ADJUSTMENTS:
{json.dumps(plan.adjustments, indent=2)}

Create a final response that:
1. Directly answers the user's query
2. Is clear and well-structured
3. Incorporates all relevant results
4. Acknowledges any limitations
5. Is helpful and actionable

Write a natural, conversational response that fully addresses the user's request.
"""

        try:
            final_response = await self.planner_llm.generate_str(compilation_prompt)
            return final_response
        except Exception as e:
            self.logger.error(f"Response compilation failed: {e}")
            # Fallback to simple concatenation
            return self._compile_fallback_response(results)

    def _compile_fallback_response(self, results: Dict[str, Any]) -> str:
        """Fallback response compilation."""
        response_parts = []
        for task_id, result in results.items():
            if isinstance(result, dict) and "result" in result:
                response_parts.append(result["result"])

        return (
            "\n\n".join(response_parts)
            if response_parts
            else "I was unable to process your request completely."
        )

    def _build_fallback_plan(
        self, incoming: IncomingMessage, intent: Intent, plan_id: str
    ) -> ExecutionPlan:
        """Build a simple fallback plan when LLM planning fails."""
        plan = ExecutionPlan(plan_id, incoming.text, intent)

        # Add simple task based on intent
        agent_type = (
            intent.required_agents[0] if intent.required_agents else "data_researcher"
        )

        plan.add_task(
            task_id="fallback_task",
            agent_type=agent_type,
            description=f"Handle user query: {incoming.text}",
            inputs={"query": incoming.text, "intent": intent.name},
        )

        plan.add_success_criterion("User query is addressed")

        return plan

    def _update_average_execution_time(self, execution_time: float):
        """Update average execution time metric."""
        current_avg = self.execution_metrics["average_execution_time"]
        total_successful = self.execution_metrics["successful_plans"]

        if total_successful == 1:
            self.execution_metrics["average_execution_time"] = execution_time
        else:
            # Calculate rolling average
            self.execution_metrics["average_execution_time"] = (
                (current_avg * (total_successful - 1)) + execution_time
            ) / total_successful

    async def health_check(self) -> Dict[str, Any]:
        """Check orchestrator and all component health."""
        base_health = await super().health_check()

        # Check all components
        component_health = {}
        for name, component in [
            ("intent_analyzer", self.intent_analyzer),
            ("registry", self.registry),
            ("tool_discovery", self.tool_discovery),
            ("pool_manager", self.pool_manager),
            ("workflow_manager", self.workflow_manager),
        ]:
            if component:
                try:
                    component_health[name] = await component.health_check()
                except Exception as e:
                    component_health[name] = {"status": "error", "error": str(e)}
            else:
                component_health[name] = {"status": "missing"}

        return {
            **base_health,
            "active_plans": len(self.active_plans),
            "execution_metrics": self.execution_metrics,
            "planner_llm_available": self.planner_llm is not None,
            "mcp_app_available": self.mcp_app is not None,
            "components": component_health,
        }
