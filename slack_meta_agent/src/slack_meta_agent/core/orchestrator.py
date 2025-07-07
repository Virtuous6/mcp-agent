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
from mcp_agent.workflows.llm.augmented_llm import RequestParams
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
from .llm_factory import (
    SmartLLMFactory,
    create_smart_llm_factory,
    create_intent_optimized_llm_factory,
    create_task_optimized_llm_factory,
)
from ..utils.context_optimizer import context_optimizer


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
        db_ops=None,
    ):
        super().__init__("Orchestrator")

        # Core components
        self.intent_analyzer = intent_analyzer
        self.registry = registry
        self.tool_discovery = tool_discovery
        self.pool_manager = pool_manager
        self.workflow_manager = workflow_manager
        self.mcp_app = mcp_app

        # Database operations
        self.db_ops = db_ops
        if not self.db_ops:
            # Initialize database operations if not provided
            self._initialize_database_operations()

        # Powerful LLM agent for plan building (using GPT-4 or similar)
        self.planner_agent = None
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

    def _initialize_database_operations(self):
        """Initialize database operations if not provided."""
        try:
            from ..database.supabase_operations import SupabaseOperations
            import os
            from pathlib import Path
            import yaml

            # Get project ID
            project_id = os.getenv("SUPABASE_PROJECT_ID", "qqggdvfeybfzqmgxmidt")

            # Try to load credentials from secrets file
            try:
                secrets_file = (
                    Path(__file__).parent.parent.parent
                    / "config"
                    / "mcp_agent.secrets.yaml"
                )
                if secrets_file.exists():
                    with open(secrets_file, "r") as f:
                        secrets = yaml.safe_load(f)

                    supabase_config = secrets.get("TRIBEsupabase", {})
                    anon_key = supabase_config.get("anon_key")
                    service_role_key = supabase_config.get("service_role_key")

                    # Initialize with credentials
                    self.db_ops = SupabaseOperations(
                        supabase_project_id=project_id,
                        anon_key=anon_key,
                        service_role_key=service_role_key,
                    )
                    self.logger.info(
                        "✅ Initialized database operations with credentials"
                    )
                else:
                    # Fallback without credentials (will use MCP method)
                    self.db_ops = SupabaseOperations(supabase_project_id=project_id)
                    self.logger.info(
                        "✅ Initialized database operations without credentials (MCP fallback)"
                    )

            except Exception as e:
                self.logger.warning(
                    f"Could not load secrets: {e}, creating basic db_ops"
                )
                self.db_ops = SupabaseOperations(supabase_project_id=project_id)

        except Exception as e:
            self.logger.error(f"Failed to initialize database operations: {e}")
            self.db_ops = None

    async def initialize(self):
        """Initialize the orchestrator with Smart LLM Factory for powerful planning."""
        try:
            # Create a specialized agent for orchestration and planning
            self.planner_agent = Agent(
                name="orchestrator_planner",
                instruction="""You are a master execution planner and strategic coordinator. 
                Your role is to analyze complex user queries and build optimal execution plans 
                that ensure complete and accurate responses. You excel at breaking down complex 
                problems into actionable steps, selecting the right agents for each task, and 
                ensuring all user needs are met. You are methodical, thorough, and focus on 
                delivering comprehensive solutions.""",
                server_names=[],  # Planning doesn't need MCP servers directly
                context=getattr(self.mcp_app, "context", None)
                if self.mcp_app
                else None,
            )

            # Configure high-capability LLM settings that can be overridden by database
            self.planner_agent.llm_config = {
                "model": "gpt-4o",  # Use most capable model for planning
                "temperature": 0.1,  # Lower temperature for consistent planning
                "max_tokens": 4000,  # Higher token limit for detailed plans
                "provider": "openai",
            }

            # Use smart LLM factory for database-driven configuration
            llm_factory = create_smart_llm_factory(self.planner_agent)
            self.planner_llm = await self.planner_agent.attach_llm(llm_factory)

            self.logger.info(
                "🧠 Orchestrator initialized with Smart LLM Factory and database-driven planning configuration"
            )

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

            # Handle dynamic discovery execution strategy
            if intent.execution_strategy == ExecutionStrategy.DYNAMIC_DISCOVERY:
                self.logger.info(f"🔍 Executing dynamic MCP discovery workflow")
                return await self._execute_dynamic_discovery(
                    incoming, intent, start_time
                )

            # Fast-path execution for simple single-agent queries
            if self._should_use_fast_path(intent):
                self.logger.info(f"🚀 Using fast-path execution for simple query")
                return await self._execute_fast_path(incoming, intent, start_time)

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

    def _should_use_fast_path(self, intent: Intent) -> bool:
        """Determine if we should use fast-path execution for simple queries."""
        # Use fast-path for single-agent queries with high confidence
        if (
            intent.execution_strategy == ExecutionStrategy.SINGLE_AGENT
            and intent.confidence in [ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM]
            and len(intent.required_agents) == 1
            and intent.estimated_tasks <= 1
        ):
            return True

        # Use fast-path for specific simple intent types
        simple_intents = [
            "weather_inquiry",
            "greeting",
            "question",
            "capability_inquiry",
            "basic_research",
            "simple_lookup",
            "factual_question",
        ]
        if intent.name in simple_intents:
            return True

        return False

    async def _execute_fast_path(
        self, incoming: IncomingMessage, intent: Intent, start_time: datetime
    ) -> ExecutionResult:
        """Execute simple queries directly without complex orchestration."""
        try:
            agent_type = (
                intent.required_agents[0]
                if intent.required_agents
                else "data_researcher"
            )

            self.logger.info(f"⚡ Fast-path execution with {agent_type}")

            # Get agent from pool
            agent = await self.pool_manager.get_agent(
                agent_type, f"fastpath_{int(start_time.timestamp())}"
            )

            if not agent:
                raise Exception(f"Could not get agent of type: {agent_type}")

            try:
                async with agent:
                    # Use optimized LLM for fast execution
                    llm = await self._get_fast_path_llm(agent, intent)

                    # Build simple prompt
                    prompt = self._build_fast_path_prompt(incoming, intent)

                    # Execute directly
                    result = await llm.generate_str(prompt)

                    execution_time = (datetime.now() - start_time).total_seconds()

                    self.logger.info(f"✅ Fast-path completed in {execution_time:.2f}s")

                    return ExecutionResult(
                        success=True,
                        response=result,
                        execution_time=execution_time,
                        intent_confidence=intent.confidence,
                        agent_count=1,
                        metadata={
                            "execution_type": "fast_path",
                            "agent_type": agent_type,
                            "intent_name": intent.name,
                        },
                    )

            except Exception as e:
                self.logger.error(f"Fast-path execution failed: {e}")
                raise

        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            return ExecutionResult(
                success=False,
                response=f"I encountered an error while processing your request: {str(e)}",
                execution_time=execution_time,
                intent_confidence=intent.confidence,
                agent_count=0,
                error=str(e),
                metadata={
                    "execution_type": "fast_path_failed",
                    "error": str(e),
                },
            )

    async def _get_fast_path_llm(self, agent: Agent, intent: Intent):
        """Get optimized LLM for fast-path execution using database configuration."""
        # Use the smart factory that respects database settings and optimizes for intent
        llm_factory = create_intent_optimized_llm_factory(
            intent_name=intent.name,
            confidence=intent.confidence.value
            if hasattr(intent.confidence, "value")
            else "medium",
        )

        return await agent.attach_llm(llm_factory)

    def _build_fast_path_prompt(self, incoming: IncomingMessage, intent: Intent) -> str:
        """Build optimized prompt for fast-path execution."""
        return f"""
USER REQUEST: {incoming.text}

INTENT: {intent.name} (confidence: {intent.confidence.value})

INSTRUCTIONS:
1. Provide a direct, helpful response to the user's request
2. Be concise but complete
3. Use your available tools as needed
4. Focus on accuracy and relevance

Execute this request efficiently and provide a clear response.
"""

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

CRITICAL PLANNING PRINCIPLES:
1. PREFER SIMPLICITY - Don't over-engineer simple requests
2. For single-agent intents, create ONE task with ONE agent
3. Only create multiple tasks if genuinely required for complex workflows
4. Weather queries, basic questions, and simple lookups need ONE task only
5. Don't create "backend services" or "development tasks" for simple data requests
6. Focus on DIRECT execution, not elaborate infrastructure

TASK CREATION GUIDELINES:
- Weather inquiry: ONE task with data_researcher to get weather data
- Simple question: ONE task with knowledge_agent to answer
- Basic research: ONE task with data_researcher to find information
- Only create multiple tasks for genuinely complex multi-step processes

Your task is to create a simple, efficient execution plan that will fully answer the user's query without over-engineering.

IMPORTANT: You MUST return a valid JSON object. Do not include any explanatory text before or after the JSON.

Return ONLY this JSON structure:
{{
    "analysis": "Your analysis of the query and intent",
    "adjustments": "Any adjustments to the intent analyzer's suggestions",
    "tasks": [
        {{
            "task_id": "task_1",
            "agent_type": "agent_name",
            "description": "Direct task description - no backend development",
            "inputs": {{"query": "{incoming.text}", "intent": "{intent.name}"}},
            "dependencies": []
        }}
    ],
    "success_criteria": [
        "User query is directly answered",
        "Response is accurate and helpful"
    ],
    "reasoning": "Simple plan that directly addresses the query without over-engineering"
}}

Focus on creating a SIMPLE plan that directly answers the user's request. Avoid creating unnecessary complexity."""

        try:
            # Get plan from powerful LLM
            plan_response = await self.planner_llm.generate_str(planning_prompt)

            # Clean and parse the plan response
            plan_data = self._parse_json_response(plan_response, "planning")

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

                # Optimize result to prevent context overflow
                if result and len(result) > 40000:  # ~10k tokens
                    self.logger.warning(
                        f"Large result detected ({len(result)} chars), applying context optimization"
                    )
                    result = context_optimizer.truncate_large_responses(
                        result, max_tokens=10000
                    )

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
        """Get task-optimized LLM for the agent using database configuration."""
        agent_type = task["agent_type"]

        # Determine task complexity based on agent type
        if agent_type in ["feedback_collector", "capability_inspector"]:
            task_type = "simple"
        elif agent_type in ["financial_analyst", "code_developer"]:
            task_type = "complex"
        elif agent_type in ["data_researcher", "knowledge_agent"]:
            task_type = "research"
        else:
            task_type = "general"

        # Use the smart factory that respects database settings and optimizes for task type
        llm_factory = create_task_optimized_llm_factory(
            task_type=task_type, agent_type=agent_type
        )

        return await agent.attach_llm(llm_factory)

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
            return self._parse_json_response(validation_response, "validation")
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
            adjustment_data = self._parse_json_response(
                adjustment_response, "adjustment"
            )

            # Add adjustment tasks to plan
            for task_data in adjustment_data.get("additional_tasks", []):
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

        # Optimize results to prevent context overflow
        optimized_results = {}
        for task_id, result in results.items():
            if isinstance(result, dict) and "result" in result:
                result_content = result["result"]
                if result_content and len(result_content) > 30000:  # ~7.5k tokens
                    self.logger.warning(f"Optimizing large result for task {task_id}")
                    result["result"] = context_optimizer.truncate_large_responses(
                        result_content, max_tokens=7500
                    )
            optimized_results[task_id] = result

        compilation_prompt = f"""
Compile a comprehensive final response from the execution results.

ORIGINAL QUERY: "{plan.original_query}"

EXECUTION RESULTS:
{json.dumps(optimized_results, indent=2)}

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

    async def _execute_dynamic_discovery(
        self, incoming: IncomingMessage, intent: Intent, start_time: datetime
    ) -> ExecutionResult:
        """Execute dynamic MCP server discovery workflow."""
        try:
            keywords = intent.payload.get("discovery_keywords", [])
            self.logger.info(f"🔍 Starting dynamic discovery for keywords: {keywords}")

            # Find servers from database
            discovered_servers = await self._find_database_servers(keywords)

            # Check if user specifically wants only database servers or all servers
            query_lower = incoming.text.lower()
            database_only_keywords = [
                "supabase servers",
                "database servers",
                "servers in supabase",
                "available supabase",
                "supabase database",
                "servers in the database",
                "have in the database",
                "stored in database",
                "database only",
                "in the database",
            ]
            wants_database_only = any(
                keyword in query_lower for keyword in database_only_keywords
            )

            if wants_database_only:
                # User specifically wants servers from Supabase database only
                all_servers = discovered_servers
                self.logger.info(
                    f"🎯 Database-only request: {len(all_servers)} servers from database"
                )
            else:
                # General discovery - combine database and configured servers
                configured_servers = await self._get_configured_servers()

                # Combine both sources for complete server list
                all_servers = []
                server_names_seen = set()

                # Add database servers first
                for server in discovered_servers:
                    server_name = server.get("server_name")
                    if server_name and server_name not in server_names_seen:
                        all_servers.append(server)
                        server_names_seen.add(server_name)

                # Add configured servers (avoid duplicates)
                for server in configured_servers:
                    server_name = server.get("server_name")
                    if server_name and server_name not in server_names_seen:
                        all_servers.append(server)
                        server_names_seen.add(server_name)

                self.logger.info(
                    f"🎯 Combined total: {len(all_servers)} servers (database + configured)"
                )

            if not all_servers:
                return ExecutionResult(
                    success=False,
                    response=f"⚠️ No MCP servers found for '{', '.join(keywords)}'",
                    execution_time=(datetime.now() - start_time).total_seconds(),
                    intent_confidence=intent.confidence,
                    agent_count=0,
                    metadata={
                        "discovery_keywords": keywords,
                        "servers_found": 0,
                    },
                )

            # Use the combined server list
            discovered_servers = all_servers

            # Check if user wants a LIST vs. CONNECTION to specific server
            query_lower = incoming.text.lower()
            list_keywords = [
                "list",
                "show all",
                "available",
                "what servers",
                "all servers",
                "find all",  # Add this pattern
                "get all",  # Add this pattern
            ]
            wants_list = any(keyword in query_lower for keyword in list_keywords)

            # If user wants a list of servers, return the list instead of connecting
            if wants_list and len(discovered_servers) > 0:
                self.logger.info(
                    f"📋 User wants list of servers, returning {len(discovered_servers)} servers instead of connecting"
                )

                # Format the server list response
                server_list = []
                for server in discovered_servers:
                    server_info = f"**{server['server_name']}**"
                    if server.get("display_name"):
                        server_info += f" ({server['display_name']})"
                    if server.get("description"):
                        server_info += f"\n  - {server['description']}"
                    if server.get("url"):
                        server_info += f"\n  - URL: {server['url']}"
                    if server.get("transport"):
                        server_info += f"\n  - Transport: {server['transport']}"
                    server_list.append(server_info)

                response = (
                    f"Found {len(discovered_servers)} MCP server(s):\n\n"
                    + "\n\n".join(server_list)
                )

                execution_time = (datetime.now() - start_time).total_seconds()
                return ExecutionResult(
                    success=True,
                    response=response,
                    execution_time=execution_time,
                    intent_confidence=intent.confidence,
                    agent_count=0,
                    metadata={
                        "execution_type": "server_list",
                        "discovery_keywords": keywords,
                        "servers_found": len(discovered_servers),
                        "servers": [s["server_name"] for s in discovered_servers],
                    },
                )

            # Use the first discovered server for connection-based queries
            server_data = discovered_servers[0]
            self.logger.info(
                f"✅ Using database server '{server_data['server_name']}' for connection"
            )

            # Create dynamic agent with the database server
            dynamic_agent = await self._create_dynamic_agent_with_server(server_data)

            if not dynamic_agent:
                return ExecutionResult(
                    success=False,
                    response=f"❌ Failed to create agent for database server '{server_data['server_name']}'",
                    execution_time=(datetime.now() - start_time).total_seconds(),
                    intent_confidence=intent.confidence,
                    agent_count=0,
                    metadata={
                        "discovery_keywords": keywords,
                        "server_name": server_data["server_name"],
                        "error": "agent_creation_failed",
                    },
                )

            try:
                async with dynamic_agent:
                    # Get optimized LLM for the task
                    llm = await self._get_fast_path_llm(dynamic_agent, intent)

                    # Check if user wants to see server capabilities vs. use the server
                    is_capability_query = any(
                        word in incoming.text.lower()
                        for word in [
                            "show me",
                            "what can",
                            "capabilities",
                            "tools available",
                            "what does",
                            "servers",
                        ]
                    )

                    if is_capability_query:
                        enhanced_prompt = f"""
                        The user asked: "{incoming.text}"
                        
                        You have connected to the '{server_data["server_name"]}' MCP server:
                        - Description: {server_data.get("description", "Specialized server")}
                        - Transport: {server_data.get("transport", "unknown")}
                        
                        The user wants to know about the SERVER CAPABILITIES, not execute tasks.
                        
                        Please:
                        1. List the available MCP tools/capabilities on this server
                        2. Describe what each tool can do
                        3. Provide examples of how to use the tools
                        4. Do NOT execute any tools unless specifically requested
                        
                        Focus on showing what the user CAN DO with this MCP server.
                        """
                    else:
                        enhanced_prompt = f"""
                        Original request: {incoming.text}
                        
                        You have access to the specialized '{server_data["server_name"]}' MCP server:
                        - Description: {server_data.get("description", "Specialized server")}
                        - Transport: {server_data.get("transport", "unknown")}
                        
                        Use the tools from this server to fulfill the user's request.
                        Provide specific, actionable results with relevant data.
                        """

                    result = await llm.generate_str(enhanced_prompt)

                    execution_time = (datetime.now() - start_time).total_seconds()

                    self.logger.info(
                        f"✅ Dynamic discovery completed in {execution_time:.2f}s"
                    )

                    return ExecutionResult(
                        success=True,
                        response=result,
                        execution_time=execution_time,
                        intent_confidence=intent.confidence,
                        agent_count=1,
                        metadata={
                            "execution_type": "dynamic_discovery",
                            "discovery_keywords": keywords,
                            "server_name": server_data["server_name"],
                            "server_transport": server_data.get("transport", "unknown"),
                        },
                    )

            finally:
                # Clean up dynamic server registration
                await self._cleanup_dynamic_server()

        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            self.logger.error(f"Dynamic discovery failed: {e}")

            return ExecutionResult(
                success=False,
                response=f"❌ Dynamic discovery failed: {str(e)}",
                execution_time=execution_time,
                intent_confidence=intent.confidence,
                agent_count=0,
                error=str(e),
                metadata={
                    "execution_type": "dynamic_discovery_failed",
                    "discovery_keywords": keywords,
                    "error": str(e),
                },
            )

    async def _find_database_servers(self, keywords: List[str]) -> List[Dict]:
        """Find MCP servers from database based on keywords."""
        try:
            self.logger.info(f"🔍 Database server discovery for keywords: {keywords}")

            # Use database operations if available
            if self.db_ops:
                self.logger.info("✅ Using database operations for server discovery")
                servers = await self.db_ops.discover_mcp_servers(keywords)
                if servers:
                    self.logger.info(f"🎯 Found {len(servers)} servers from database")
                    return servers
                else:
                    self.logger.info("No servers found in database for keywords")
                    return []

            # Try pool manager method if db_ops not available
            if self.pool_manager and hasattr(
                self.pool_manager, "find_servers_by_keywords"
            ):
                self.logger.info("✅ Using pool manager for server discovery")
                result = await self.pool_manager.find_servers_by_keywords(keywords)
                if result.get("success"):
                    return result.get("data", [])
                else:
                    self.logger.warning(
                        f"Pool manager discovery failed: {result.get('error')}"
                    )
                    return []

            self.logger.warning("❌ No database discovery methods available")
            return []

        except Exception as e:
            self.logger.error(f"❌ Database server search failed: {e}")
            return []

    async def _get_configured_servers(self) -> List[Dict]:
        """Get MCP servers configured in YAML files."""
        try:
            configured_servers = []

            if (
                self.mcp_app
                and hasattr(self.mcp_app, "context")
                and hasattr(self.mcp_app.context, "server_registry")
            ):
                registry = self.mcp_app.context.server_registry.registry
                self.logger.info(f"📋 Found {len(registry)} configured servers in YAML")

                for server_name, server_config in registry.items():
                    # Convert MCPServerSettings to our discovery format
                    server_info = {
                        "server_name": server_name,
                        "display_name": server_config.name or server_name,
                        "description": server_config.description
                        or f"Configured MCP server: {server_name}",
                        "transport": server_config.transport or "unknown",
                        "url": server_config.url,
                        "command": server_config.command,
                        "args": server_config.args or [],
                        "source": "yaml_config",
                    }
                    configured_servers.append(server_info)

            self.logger.info(
                f"✅ Retrieved {len(configured_servers)} configured servers"
            )
            return configured_servers

        except Exception as e:
            self.logger.error(f"❌ Failed to get configured servers: {e}")
            return []

    async def _create_dynamic_agent_with_server(
        self, server_data: Dict
    ) -> Optional[Agent]:
        """Create a dynamic agent with the specified server."""
        try:
            if not self.mcp_app or not hasattr(self.mcp_app, "context"):
                self.logger.error(
                    "No MCP app context available for dynamic agent creation"
                )
                return None

            from mcp_agent.config import MCPServerSettings

            # Debug: Log the server data being used
            self.logger.info(
                f"🔧 Creating dynamic agent with server data: {server_data}"
            )

            # Extract and validate URL
            url = server_data.get("url")
            transport = server_data.get("transport", "sse")

            # Log the URL value for debugging
            self.logger.info(f"🔧 Server URL: {url}, Transport: {transport}")

            # Validate URL for non-stdio transports
            if transport in ["sse", "websocket", "streamable_http"] and not url:
                self.logger.error(f"❌ Missing URL for {transport} transport")
                return None

            # Create dynamic server configuration
            dynamic_config = MCPServerSettings(
                name=server_data.get("server_name", "dynamic_server"),
                description=server_data.get(
                    "description", "Dynamically discovered server"
                ),
                transport=transport,
                url=url,
                command=server_data.get("command"),
                args=server_data.get("args", []),
                terminate_on_close=True,
            )

            # Log the final config being registered
            self.logger.info(
                f"🔧 Registering dynamic server config: name={dynamic_config.name}, transport={dynamic_config.transport}, url={dynamic_config.url}"
            )

            # Register the dynamic server
            self.mcp_app.context.server_registry.registry["dynamic_server"] = (
                dynamic_config
            )

            # Create agent with the dynamic server
            agent = Agent(
                name=f"dynamic_{server_data['server_name']}_agent",
                instruction=f"Agent with access to {server_data['server_name']} MCP server",
                server_names=["dynamic_server"],
                context=self.mcp_app.context,
            )

            self.logger.info(
                f"✅ Created dynamic agent for {server_data['server_name']}"
            )
            return agent

        except Exception as e:
            self.logger.error(f"Failed to create dynamic agent: {e}")
            return None

    async def _cleanup_dynamic_server(self):
        """Clean up dynamic server registration."""
        try:
            if (
                self.mcp_app
                and hasattr(self.mcp_app, "context")
                and hasattr(self.mcp_app.context, "server_registry")
                and "dynamic_server" in self.mcp_app.context.server_registry.registry
            ):
                del self.mcp_app.context.server_registry.registry["dynamic_server"]
                self.logger.debug("🧹 Cleaned up dynamic server registration")
        except Exception as e:
            self.logger.warning(f"Dynamic server cleanup warning: {e}")

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
            "planner_agent_available": self.planner_agent is not None,
            "planner_llm_available": self.planner_llm is not None,
            "mcp_app_available": self.mcp_app is not None,
            "components": component_health,
        }

    def _parse_json_response(self, response: str, context: str) -> Dict[str, Any]:
        """Parse a JSON response with robust error handling."""
        try:
            # Clean the response by removing any extra text
            cleaned_response = response.strip()

            # Try to find JSON within the response
            json_start = cleaned_response.find("{")
            json_end = cleaned_response.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_str = cleaned_response[json_start:json_end]
                return json.loads(json_str)
            else:
                # If no JSON found, try parsing the whole response
                return json.loads(cleaned_response)

        except json.JSONDecodeError as e:
            self.logger.error(f"JSON decode error in {context}: {e}")
            self.logger.debug(f"Raw {context} response: {response}")
            return {}
        except Exception as e:
            self.logger.error(f"Failed to parse {context} response: {e}")
            self.logger.debug(f"Raw {context} response: {response}")
            return {}
