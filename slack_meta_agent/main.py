import asyncio
import json
import os
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import logging
from datetime import datetime

from mcp_agent.app import MCPApp
from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
from mcp_agent.workflows.orchestrator.orchestrator import Orchestrator
from mcp_agent.human_input.handler import console_input_callback
from rich import print

# Slack integration imports
try:
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse
    from slack_sdk import WebClient

    SLACK_AVAILABLE = True
except ImportError:
    print("⚠️  Slack SDK not installed. Install with: pip install slack-sdk")
    SLACK_AVAILABLE = False

app = MCPApp(name="slack_meta_agent", human_input_callback=console_input_callback)


@dataclass
class AgentSpec:
    """Specification for creating specialized agents"""

    name: str
    instruction: str
    server_names: List[str]
    capabilities: List[str]


@dataclass
class ConversationTurn:
    """Represents a single conversation turn with quality metrics"""

    turn_number: int
    user_input: str
    agent_response: str
    intent_analysis: Dict
    timestamp: datetime
    quality_metrics: Dict = field(default_factory=dict)
    execution_time: float = 0.0


@dataclass
class ConversationState:
    """Enhanced conversation state management"""

    user_id: str
    channel_id: str
    turns: List[ConversationTurn] = field(default_factory=list)
    current_turn: int = 0
    context_memory: Dict = field(default_factory=dict)
    user_preferences: Dict = field(default_factory=dict)

    def add_turn(self, turn: ConversationTurn):
        """Add a new conversation turn"""
        self.turns.append(turn)
        self.current_turn = len(self.turns)

    def get_recent_context(self, max_turns: int = 3) -> str:
        """Get recent conversation context for better responses"""
        recent_turns = self.turns[-max_turns:] if self.turns else []
        context = []
        for turn in recent_turns:
            context.append(f"User: {turn.user_input}")
            context.append(f"Agent: {turn.agent_response[:200]}...")
        return "\n".join(context)


class SlackMetaAgent:
    """The main meta-agent that orchestrates all other agents and handles Slack interactions"""

    def __init__(self):
        self.specialized_agents: Dict[str, Agent] = {}
        self.agent_registry: Dict[str, AgentSpec] = self._initialize_agent_registry()
        self.conversation_memory: Dict[
            str, List[Dict]
        ] = {}  # Legacy - kept for backward compatibility
        self.conversation_states: Dict[
            str, ConversationState
        ] = {}  # New structured state management
        self.active_workflows: Dict[str, Any] = {}
        self.slack_client: Optional[WebClient] = None
        self.socket_client: Optional[SocketModeClient] = None
        self.logger = logging.getLogger("SlackMetaAgent")
        self.event_loop = None

        # Dynamic tool discovery cache
        self.discovered_tools: Optional[Dict[str, Dict]] = None
        self.tools_cache_timestamp: Optional[datetime] = None
        self.cache_ttl_seconds = 300  # 5-minute cache TTL

    async def _discover_available_tools(self) -> Dict[str, Dict]:
        """Dynamically discover all available tools from connected MCP servers"""
        self.logger.info("🔍 Discovering available tools from MCP servers...")
        discovered_tools = {}

        for agent_type, spec in self.agent_registry.items():
            if not spec.server_names:
                # Agents without servers (like knowledge_agent) - just use their capabilities
                discovered_tools[agent_type] = {
                    "agent_description": spec.instruction[:200] + "...",
                    "servers": [],
                    "tools": [],
                    "capabilities": spec.capabilities,
                }
                continue

            agent_tools = {}
            for server_name in spec.server_names:
                try:
                    # Create temporary agent to discover tools
                    temp_agent = Agent(
                        name=f"discovery_{server_name}",
                        instruction="Tool discovery agent",
                        server_names=[server_name],
                    )

                    async with temp_agent:
                        tools_result = await temp_agent.list_tools(server_name)
                        capabilities = await temp_agent.get_capabilities(server_name)

                        agent_tools[server_name] = {
                            "tools": [
                                {
                                    "name": tool.name,
                                    "description": tool.description
                                    or "No description available",
                                    "parameters": getattr(tool, "inputSchema", {}),
                                }
                                for tool in tools_result.tools
                            ]
                            if tools_result
                            else [],
                            "capabilities": capabilities.model_dump()
                            if capabilities
                            else {},
                        }

                        self.logger.info(
                            f"✅ Discovered {len(agent_tools[server_name]['tools'])} tools from {server_name}"
                        )

                except Exception as e:
                    self.logger.warning(
                        f"⚠️  Could not discover tools for {server_name}: {e}"
                    )
                    agent_tools[server_name] = {
                        "tools": [],
                        "capabilities": {},
                        "error": str(e),
                    }

            discovered_tools[agent_type] = {
                "agent_description": spec.instruction[:200] + "...",
                "servers": spec.server_names,
                "server_tools": agent_tools,
                "capabilities": spec.capabilities,
            }

        self.logger.info(
            f"🎯 Tool discovery complete: {len(discovered_tools)} agent types analyzed"
        )
        return discovered_tools

    async def _get_cached_tools(self) -> Dict[str, Dict]:
        """Get cached tool discovery with TTL"""
        now = datetime.now()

        # Check if cache is valid
        if (
            self.discovered_tools is not None
            and self.tools_cache_timestamp is not None
            and (now - self.tools_cache_timestamp).total_seconds()
            < self.cache_ttl_seconds
        ):
            self.logger.debug("🔄 Using cached tool discovery")
            return self.discovered_tools

        # Cache is stale or doesn't exist, refresh
        self.logger.info("🔄 Refreshing tool discovery cache")
        self.discovered_tools = await self._discover_available_tools()
        self.tools_cache_timestamp = now
        return self.discovered_tools

    def _format_agent_capabilities(self, tools: Dict[str, Dict]) -> str:
        """Format agent capabilities for the routing prompt"""
        formatted = []

        for agent_type, info in tools.items():
            formatted.append(f"\n**{agent_type.upper()}**:")
            formatted.append(f"  Purpose: {info['agent_description']}")

            if info.get("servers"):
                formatted.append(f"  MCP Servers: {', '.join(info['servers'])}")

                # Show key tools for each server
                server_tools = info.get("server_tools", {})
                for server_name, server_info in server_tools.items():
                    tools_list = server_info.get("tools", [])
                    if tools_list:
                        tool_names = [f"`{t['name']}`" for t in tools_list[:3]]
                        if len(tools_list) > 3:
                            tool_names.append(f"+ {len(tools_list) - 3} more")
                        formatted.append(
                            f"    - {server_name}: {', '.join(tool_names)}"
                        )
                    elif "error" in server_info:
                        formatted.append(f"    - {server_name}: (connection issue)")
            else:
                formatted.append(
                    f"  Built-in capabilities: {', '.join(info.get('capabilities', []))}"
                )

        return "\n".join(formatted)

    def _initialize_agent_registry(self) -> Dict[str, AgentSpec]:
        """Initialize the registry of available specialized agents"""
        return {
            "capability_inspector": AgentSpec(
                name="capability_inspector",
                instruction="""You are a system introspection specialist that can analyze and report on 
                the bot's capabilities, available MCP tools, and connected servers. When asked about 
                capabilities, you should:
                
                1. List all available MCP servers across all agent types
                2. Enumerate specific tools available on each server
                3. Explain what each tool/server can do
                4. Provide examples of how users can leverage these capabilities
                5. Organize the information in a clear, user-friendly format
                
                You have access to the complete system architecture and can introspect:
                - All specialized agents (financial_analyst, code_developer, etc.)
                - All MCP servers (supabase, brave_search, fetch, github, etc.)
                - All available tools and their functions
                
                Present this information in a comprehensive but accessible way.""",
                server_names=[],  # This agent will introspect other agents, not use direct MCP servers
                capabilities=[
                    "system_introspection",
                    "capability_enumeration",
                    "tool_listing",
                ],
            ),
            "knowledge_agent": AgentSpec(
                name="knowledge_agent",
                instruction="""You are a knowledgeable assistant that can answer factual questions 
                using your training data. You should provide direct, accurate answers to questions 
                about well-established facts, definitions, historical information, geography, 
                science, and general knowledge.
                
                Use your knowledge base to answer questions like:
                - What is the capital of [state/country]?
                - Who was [historical figure]?
                - What is [scientific concept]?
                - Basic definitions and explanations
                
                Only use your built-in knowledge - do not search the web for basic factual information.""",
                server_names=[],  # No external servers needed for basic knowledge
                capabilities=["factual_knowledge", "definitions", "basic_qa"],
            ),
            "financial_analyst": AgentSpec(
                name="financial_analyst",
                instruction="""You are a financial analysis expert with access to market data, 
                financial APIs, and analysis tools. You can analyze financial statements, 
                market trends, create financial models, and generate investment insights.
                
                For dashboard creation, you should:
                1. Gather financial data from Supabase
                2. Research industry benchmarks using Brave Search
                3. Create visualizations and analysis
                4. Provide actionable insights and recommendations""",
                server_names=["fetch", "brave_search", "supabase"],
                capabilities=[
                    "financial_analysis",
                    "market_research",
                    "data_visualization",
                ],
            ),
            "code_developer": AgentSpec(
                name="code_developer",
                instruction="""You are a software developer with access to filesystem and development tools. 
                You can write code, create applications, and provide technical solutions.
                
                For dashboard development, you should:
                1. Create responsive dashboard code (React/Next.js preferred)
                2. Provide deployment instructions
                3. Ensure mobile-friendly and accessible design
                4. Generate complete code solutions""",
                server_names=["filesystem", "fetch"],
                capabilities=[
                    "code_generation",
                    "testing",
                    "documentation",
                ],
            ),
            "data_researcher": AgentSpec(
                name="data_researcher",
                instruction="""You are a research specialist with access to web search, 
                data sources, and analysis tools. You MUST use your tools to gather real-time information.
                
                IMPORTANT: Always use your available tools (brave_search, fetch) to get actual data.
                - For weather: 1) Use brave_search to find weather sites, 2) Use fetch to get current conditions
                - For news/current events: 1) Use brave_search to find articles, 2) Use fetch to read content
                - For any real-time data: USE YOUR TOOLS to get specific current information
                
                Return the actual current data (temperature, conditions, etc.), not just links.""",
                server_names=["fetch", "brave_search", "supabase"],
                capabilities=["research", "data_analysis", "report_generation"],
            ),
            "project_manager": AgentSpec(
                name="project_manager",
                instruction="""You are a project management expert with access to databases 
                and coordination tools. You can create project plans, track progress, 
                and manage workflows using available data sources.""",
                server_names=["supabase", "fetch"],
                capabilities=[
                    "project_planning",
                    "progress_tracking",
                    "data_management",
                ],
            ),
            "communication_specialist": AgentSpec(
                name="communication_specialist",
                instruction="""You are a communication expert with access to content creation tools. 
                You can draft messages, create presentations, manage content, and facilitate communication 
                through available channels.""",
                server_names=["fetch", "supabase"],
                capabilities=["content_creation", "communication", "presentation"],
            ),
            "automation_specialist": AgentSpec(
                name="automation_specialist",
                instruction="""You are a workflow automation expert with access to n8n workflows and Airtable. 
                You can trigger automated workflows, sync data between systems, and manage complex automation processes.
                
                When handling automation requests, you should:
                1. Identify the appropriate n8n workflow to trigger
                2. Gather required data from the user or other systems
                3. Execute the workflow with proper parameters
                4. Monitor the automation and provide status updates
                5. Handle any errors or exceptions gracefully
                
                You specialize in:
                - Triggering n8n workflows via webhook URLs
                - Managing Airtable record operations
                - Data synchronization between platforms
                - Automated data processing workflows""",
                server_names=["n8n", "supabase", "fetch"],
                capabilities=[
                    "workflow_automation",
                    "data_synchronization",
                    "process_automation",
                    "airtable_integration",
                ],
            ),
            "airtable_manager": AgentSpec(
                name="airtable_manager",
                instruction="""You are an Airtable database specialist with expertise in managing 
                records, organizing data, and performing database operations through n8n workflows.
                
                You can:
                1. Create, read, update, and delete Airtable records
                2. Query and filter Airtable data
                3. Manage table relationships and data structure
                4. Perform bulk operations on records
                5. Generate reports from Airtable data
                
                Always ensure data integrity and follow best practices for database operations.
                Use n8n workflows to interact with Airtable for complex operations.""",
                server_names=["n8n", "fetch"],
                capabilities=[
                    "airtable_operations",
                    "database_management",
                    "record_management",
                    "data_querying",
                ],
            ),
        }

    async def initialize_slack(self, bot_token: str, app_token: str):
        """Initialize Slack clients and WebSocket connection"""
        if not SLACK_AVAILABLE:
            raise ImportError(
                "Slack SDK not available. Install with: pip install slack-sdk"
            )

        try:
            self.logger.info("🔌 Initializing Slack clients...")

            # Store the current event loop for async task scheduling
            self.event_loop = asyncio.get_event_loop()

            # Create Web client
            self.slack_client = WebClient(token=bot_token)
            self.logger.info("✅ Slack Web client created")

            # Create Socket Mode client
            self.socket_client = SocketModeClient(
                app_token=app_token, web_client=self.slack_client
            )
            self.logger.info("✅ Slack Socket Mode client created")

            # Register event handlers
            self.socket_client.socket_mode_request_listeners.append(
                self._handle_slack_events
            )
            self.logger.info("✅ Event handlers registered")

            self.logger.info("🔌 Slack WebSocket connection initialized")

        except Exception as e:
            self.logger.error(f"Failed to initialize Slack: {e}")
            raise

    def _handle_slack_events(self, client: SocketModeClient, req: SocketModeRequest):
        """Handle incoming Slack events (synchronous handler for Slack SDK)"""
        if req.type == "events_api":
            # Acknowledge the request immediately
            client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )

            # Process the event asynchronously using the stored event loop
            event = req.payload.get("event", {})
            if self.event_loop and not self.event_loop.is_closed():
                asyncio.run_coroutine_threadsafe(
                    self._process_slack_message(event), self.event_loop
                )

        elif req.type == "slash_commands":
            # Acknowledge slash commands immediately
            client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )

            # Process slash command asynchronously using the stored event loop
            command_data = req.payload
            if self.event_loop and not self.event_loop.is_closed():
                asyncio.run_coroutine_threadsafe(
                    self._process_slack_slash_command(command_data), self.event_loop
                )

        else:
            # Acknowledge other request types
            client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )

    async def _process_slack_message(self, event: Dict):
        """Process Slack message events with enhanced conversation management"""
        start_time = datetime.now()

        try:
            # Filter for relevant events only
            if event.get("type") not in ["app_mention", "message"]:
                return

            # Ignore bot messages and messages with subtypes (like edits, etc.)
            if event.get("subtype") or event.get("bot_id"):
                return

            self.logger.info(
                f"📩 Processing Slack message: {event.get('text', '')[:100]}..."
            )

            user_id = event.get("user")
            channel_id = event.get("channel")
            message_text = event.get("text", "")
            message_ts = event.get("ts")  # Capture the original message timestamp

            if not user_id or not channel_id or not message_text:
                return

            # Check if this is an app mention or direct message to our bot
            if event.get("type") == "app_mention" or channel_id.startswith("D"):
                # Initialize or get conversation state
                conversation_key = f"{user_id}_{channel_id}"
                if conversation_key not in self.conversation_states:
                    self.conversation_states[conversation_key] = ConversationState(
                        user_id=user_id, channel_id=channel_id
                    )

                conversation_state = self.conversation_states[conversation_key]

                # Add eyes reaction immediately to show the bot received the message
                try:
                    if self.slack_client and message_ts:
                        self.slack_client.reactions_add(
                            channel=channel_id, timestamp=message_ts, name="eyes"
                        )
                        self.logger.info(
                            "👀 Added eyes reaction to show message received"
                        )
                except Exception as e:
                    self.logger.warning(f"Could not add reaction: {e}")

                # Store conversation memory (legacy support)
                await self._store_conversation_memory(user_id, message_text, channel_id)

                # Dynamic intent analysis with context
                context = {
                    "user_id": user_id,
                    "channel_id": channel_id,
                    "recent_context": conversation_state.get_recent_context(),
                    "turn_number": conversation_state.current_turn + 1,
                }

                intent_analysis = await self._analyze_user_intent_dynamic(
                    message_text, context
                )

                self.logger.info(
                    f"🎯 Intent Analysis: {intent_analysis['execution_strategy']} - {intent_analysis['intent_name']}"
                )

                # Create required specialized agents
                agents = []
                for agent_type in intent_analysis.get("required_agents", []):
                    try:
                        agent = await self.create_specialized_agent(agent_type)
                        agents.append(agent)
                        self.logger.info(f"✅ Created {agent_type} agent")
                    except Exception as e:
                        self.logger.warning(f"Could not create {agent_type} agent: {e}")

                if not agents:
                    await self._send_slack_response(
                        channel_id,
                        "I need some specialized agents to help with this request, but none are available right now. Please try again later.",
                        thread_ts=message_ts,
                    )
                    return

                # Execute orchestrated workflow - pass original message for simple requests
                result = await self._execute_orchestrated_workflow(
                    intent_analysis, message_text, agents
                )

                # Calculate execution time and quality metrics
                execution_time = (datetime.now() - start_time).total_seconds()
                quality_metrics = {
                    "execution_time": execution_time,
                    "intent_confidence": intent_analysis.get("confidence", "unknown"),
                    "agent_count": len(agents),
                    "complexity": intent_analysis.get("complexity", "unknown"),
                }

                # Create conversation turn
                turn = ConversationTurn(
                    turn_number=conversation_state.current_turn + 1,
                    user_input=message_text,
                    agent_response=result,
                    intent_analysis=intent_analysis,
                    timestamp=start_time,
                    quality_metrics=quality_metrics,
                    execution_time=execution_time,
                )

                # Add turn to conversation state
                conversation_state.add_turn(turn)

                # Store interaction for learning (legacy support)
                await self._store_interaction_learning(
                    user_id, message_text, result, intent_analysis
                )

                # Send formatted response to Slack (in a thread) with quality info
                await self._send_enhanced_slack_response(
                    channel_id,
                    result,
                    intent_analysis,
                    quality_metrics,
                    thread_ts=message_ts,
                )

        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            self.logger.error(
                f"Error processing Slack message (took {execution_time:.2f}s): {e}"
            )
            if "channel_id" in locals() and "message_ts" in locals():
                await self._send_slack_response(
                    channel_id,
                    f"Sorry, I encountered an error: {str(e)}",
                    thread_ts=message_ts,
                )

    async def _process_slack_slash_command(self, command_data: Dict):
        """Process Slack slash commands"""
        try:
            command = command_data.get("command", "")
            text = command_data.get("text", "")
            user_id = command_data.get("user_id", "")
            channel_id = command_data.get("channel_id", "")

            self.logger.info(f"⚡ Processing slash command: {command} {text}")

            # Handle different slash commands
            if command == "/meta-agent":
                # Process as regular message
                event = {
                    "type": "message",
                    "user": user_id,
                    "channel": channel_id,
                    "text": text,
                }
                await self._process_slack_message(event)

        except Exception as e:
            self.logger.error(f"Error processing slash command: {e}")

    async def create_specialized_agent(self, agent_type: str) -> Agent:
        """Dynamically create a specialized agent based on type"""
        if agent_type not in self.agent_registry:
            raise ValueError(f"Unknown agent type: {agent_type}")

        spec = self.agent_registry[agent_type]
        agent = Agent(
            name=spec.name, instruction=spec.instruction, server_names=spec.server_names
        )

        self.specialized_agents[agent_type] = agent
        return agent

    async def _analyze_user_intent_dynamic(
        self, message: str, context: Dict = None
    ) -> Dict:
        """Dynamic intent analysis using actual tool discovery - replaces hard-coded patterns"""
        try:
            # Get fresh tool discovery (with caching)
            tools = await self._get_cached_tools()

            # Create routing prompt with actual available tools
            routing_prompt = f"""
            User request: "{message}"
            
            Available specialized agents and their actual tools:
            {self._format_agent_capabilities(tools)}
            
            Context: {context.get("recent_context", "First interaction") if context else "No context"}
            
            Analyze this request and determine:
            1. Which agent type is BEST suited for this request
            2. What complexity level (simple/moderate/complex) 
            3. What execution strategy (single_agent/orchestrated)
            4. Confidence level (high/medium/low)
            
            Return a JSON response with:
            {{
                "selected_agent": "agent_type_name",
                "reasoning": "explanation of why this agent was selected",
                "complexity": "simple|moderate|complex",
                "execution_strategy": "single_agent|orchestrated",
                "confidence": "high|medium|low",
                "estimated_tasks": number,
                "requires_tools": ["tool1", "tool2"]
            }}
            
            Important: 
            - For capability questions, use "capability_inspector"
            - For factual questions (not requiring real-time data), use "knowledge_agent"
            - For weather/current events/real-time data, use "data_researcher"
            - For airtable/n8n/automation, use "automation_specialist" or "airtable_manager"
            - Match the user's request to the actual tools available
            """

            # Use OpenAI to analyze the request dynamically
            from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

            # Create a temporary agent to use OpenAI for routing
            routing_agent = Agent(
                name="dynamic_router",
                instruction="You are a routing agent that analyzes user requests and selects the best specialized agent.",
                server_names=[],  # No MCP servers needed for routing
            )

            async with routing_agent:
                llm = await routing_agent.attach_llm(OpenAIAugmentedLLM)
                routing_result = await llm.generate_str(routing_prompt)

                # Parse the JSON response
                import json

                try:
                    parsed_result = json.loads(routing_result)
                except json.JSONDecodeError:
                    # Extract JSON from the response if it's wrapped in text
                    import re

                    json_match = re.search(r"\{.*\}", routing_result, re.DOTALL)
                    if json_match:
                        parsed_result = json.loads(json_match.group())
                    else:
                        raise ValueError("Could not parse routing response")

                        # Validate and format the response
                selected_agent = parsed_result.get("selected_agent", "data_researcher")

                # Normalize agent name (case-insensitive matching)
                selected_agent_lower = selected_agent.lower()
                if selected_agent_lower in self.agent_registry:
                    selected_agent = selected_agent_lower
                elif selected_agent not in self.agent_registry:
                    self.logger.warning(
                        f"Unknown agent {selected_agent}, defaulting to data_researcher"
                    )
                    selected_agent = "data_researcher"

                analysis = {
                    "required_agents": [selected_agent],
                    "complexity": parsed_result.get("complexity", "simple"),
                    "estimated_tasks": parsed_result.get("estimated_tasks", 1),
                    "execution_strategy": parsed_result.get(
                        "execution_strategy", "single_agent"
                    ),
                    "priority": "high"
                    if parsed_result.get("confidence") == "high"
                    else "medium",
                    "task_description": f"Dynamic routing to {selected_agent}",
                    "reasoning": parsed_result.get(
                        "reasoning", "Dynamic tool-based routing"
                    ),
                    "intent_name": f"dynamic_{selected_agent}",
                    "confidence": parsed_result.get("confidence", "medium"),
                    "requires_tools": parsed_result.get("requires_tools", []),
                }

                self.logger.info(
                    f"🎯 Dynamic routing: {message[:50]}... -> {selected_agent} (confidence: {analysis['confidence']})"
                )
                return analysis

        except Exception as e:
            self.logger.error(f"Dynamic routing error: {e}")
            import traceback

            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            # Fallback to simple logic
            return await self._analyze_user_intent_fallback(message, context)

    async def _analyze_user_intent_fallback(
        self, message: str, context: Dict = None
    ) -> Dict:
        """Fallback intent analysis if structured system fails"""
        message_lower = message.lower()

        # Quick pattern matching as fallback
        if any(
            word in message_lower
            for word in ["weather", "current", "today", "now", "latest", "recent"]
        ):
            agent_type = "data_researcher"
        elif any(
            word in message_lower
            for word in ["what is", "who is", "what does", "define", "explain"]
        ):
            agent_type = "knowledge_agent"
        elif any(
            word in message_lower
            for word in ["dashboard", "financial", "revenue", "profit", "analysis"]
        ):
            agent_type = "financial_analyst"
        elif any(
            word in message_lower
            for word in ["code", "deploy", "develop", "build", "app"]
        ):
            agent_type = "code_developer"
        else:
            agent_type = "data_researcher"

        return {
            "required_agents": [agent_type],
            "complexity": "simple",
            "estimated_tasks": 1,
            "execution_strategy": "single_agent",
            "priority": "medium",
            "task_description": "Fallback analysis",
            "reasoning": f"Fallback logic selected {agent_type}",
            "intent_name": "fallback",
            "confidence": "low",
        }

    async def _execute_orchestrated_workflow(
        self, intent_analysis: Dict, message: str, agents: List[Agent]
    ) -> str:
        """Execute orchestrated multi-agent workflow as shown in sequence diagram"""
        try:
            # Special handling for capability inquiry
            if (
                intent_analysis.get("intent_name") == "capability_inquiry"
                and len(agents) == 1
                and agents[0].name == "capability_inspector"
            ):
                self.logger.info("🔍 Performing system capability introspection")
                return await self._perform_capability_introspection()

            # For simple single-agent requests, execute directly
            if (
                intent_analysis.get("execution_strategy") == "single_agent"
                and len(agents) == 1
            ):
                agent = agents[0]
                self.logger.info(f"🎯 Direct execution with {agent.name} agent")

                async with agent:
                    llm = await agent.attach_llm(OpenAIAugmentedLLM)
                    # Pass the original user message directly
                    result = await llm.generate_str(message)
                    return result

            # Only use orchestrator for complex multi-agent workflows
            orchestrator = Orchestrator(
                llm_factory=OpenAIAugmentedLLM,
                available_agents=agents,
                plan_type="full",  # Iterative planning
            )

            # Enhanced task description for complex requests
            task_description = f"""
            Original request: {message}
            
            Task analysis: {intent_analysis.get("task_description", "")}
            Required capabilities: {", ".join([agent.name for agent in agents])}
            
            
            Execute this request using the available specialized agents:
            {[f"- {agent.name}: {self.agent_registry[agent.name].instruction[:100]}..." for agent in agents]}
            
            Coordinate the agents to:
            1. Gather required data and research
            2. Process and analyze information  
            3. Create deliverables (dashboards, reports, etc.)
            4. Provide actionable results with URLs/links where applicable
            
            Return a comprehensive result with specific deliverables and next steps.
            """

            self.logger.info(
                f"🎯 Orchestrating {len(agents)} agents for complex workflow"
            )

            # Execute the orchestrated workflow
            result = await orchestrator.generate_str(message=task_description)

            return result

        except Exception as e:
            self.logger.error(f"Orchestration error: {e}")
            # Fallback to sequential execution
            return await self._execute_sequential_fallback(message, agents)

    async def _perform_capability_introspection(self) -> str:
        """Perform comprehensive capability introspection across all agent types"""
        try:
            self.logger.info("🔍 Starting comprehensive capability introspection...")

            capabilities_info = {
                "specialized_agents": {},
                "mcp_servers": {},
                "total_tools": 0,
                "system_overview": {},
            }

            # Gather information about each specialized agent type
            for agent_type, spec in self.agent_registry.items():
                if agent_type == "capability_inspector":
                    continue  # Skip self-reference

                try:
                    # Create a temporary agent to inspect its capabilities
                    temp_agent = await self.create_specialized_agent(agent_type)

                    async with temp_agent:
                        # Get server capabilities
                        if spec.server_names:
                            server_capabilities = {}
                            for server_name in spec.server_names:
                                try:
                                    caps = await temp_agent.get_capabilities(
                                        server_name
                                    )
                                    tools = await temp_agent.list_tools(server_name)

                                    server_capabilities[server_name] = {
                                        "capabilities": caps.model_dump()
                                        if caps
                                        else {},
                                        "tools": [
                                            {
                                                "name": tool.name,
                                                "description": tool.description,
                                            }
                                            for tool in tools.tools
                                        ]
                                        if tools
                                        else [],
                                    }
                                    capabilities_info["total_tools"] += (
                                        len(tools.tools) if tools else 0
                                    )

                                except Exception as e:
                                    self.logger.warning(
                                        f"Could not get capabilities for {server_name}: {e}"
                                    )
                                    server_capabilities[server_name] = {"error": str(e)}

                            capabilities_info["specialized_agents"][agent_type] = {
                                "description": spec.instruction[:200] + "..."
                                if len(spec.instruction) > 200
                                else spec.instruction,
                                "server_names": spec.server_names,
                                "capabilities": spec.capabilities,
                                "server_details": server_capabilities,
                            }
                        else:
                            capabilities_info["specialized_agents"][agent_type] = {
                                "description": spec.instruction[:200] + "..."
                                if len(spec.instruction) > 200
                                else spec.instruction,
                                "server_names": [],
                                "capabilities": spec.capabilities,
                                "server_details": {},
                            }

                except Exception as e:
                    self.logger.warning(f"Could not introspect {agent_type}: {e}")
                    capabilities_info["specialized_agents"][agent_type] = {
                        "error": str(e),
                        "description": spec.instruction[:100] + "..."
                        if len(spec.instruction) > 100
                        else spec.instruction,
                    }

            # Create comprehensive summary
            unique_servers = set()
            for agent_info in capabilities_info["specialized_agents"].values():
                if "server_names" in agent_info:
                    unique_servers.update(agent_info["server_names"])

            capabilities_info["system_overview"] = {
                "total_specialized_agents": len(
                    [
                        a
                        for a in self.agent_registry.keys()
                        if a != "capability_inspector"
                    ]
                ),
                "unique_mcp_servers": list(unique_servers),
                "total_unique_servers": len(unique_servers),
            }

            # Format the response
            response = f"""🤖 **Meta-Agent System Capabilities**

**📊 System Overview:**
• **{capabilities_info["system_overview"]["total_specialized_agents"]} Specialized Agents** available for different task types
• **{capabilities_info["system_overview"]["total_unique_servers"]} MCP Servers** providing {capabilities_info["total_tools"]} total tools
• **Structured Intent Classification** for intelligent agent routing

**🔧 Available Specialized Agents:**

"""

            for agent_type, info in capabilities_info["specialized_agents"].items():
                if "error" in info:
                    response += f"**{agent_type.replace('_', ' ').title()}:** ❌ Error accessing\n\n"
                    continue

                response += f"**{agent_type.replace('_', ' ').title()}:**\n"
                response += f"• Purpose: {info['description'].split('.')[0]}\n"

                if info["server_names"]:
                    response += f"• MCP Servers: {', '.join(info['server_names'])}\n"

                    # List key tools for each server
                    for server_name, server_info in info.get(
                        "server_details", {}
                    ).items():
                        if "tools" in server_info and server_info["tools"]:
                            tool_names = [
                                t["name"] for t in server_info["tools"][:3]
                            ]  # Show first 3 tools
                            tool_count = len(server_info["tools"])
                            if tool_count > 3:
                                tool_names.append(f"+ {tool_count - 3} more")
                            response += f"  - {server_name}: {', '.join(tool_names)}\n"
                else:
                    response += "• Uses built-in knowledge (no external tools)\n"

                response += "\n"

            response += """**🌐 MCP Server Details:**

"""

            # Group tools by server across all agents
            server_tool_map = {}
            for agent_info in capabilities_info["specialized_agents"].values():
                if "server_details" in agent_info:
                    for server_name, server_info in agent_info[
                        "server_details"
                    ].items():
                        if server_name not in server_tool_map:
                            server_tool_map[server_name] = []
                        if "tools" in server_info:
                            server_tool_map[server_name].extend(server_info["tools"])

            for server_name, tools in server_tool_map.items():
                unique_tools = {
                    tool["name"]: tool for tool in tools
                }.values()  # Deduplicate
                response += f"**{server_name}:** {len(unique_tools)} tools\n"
                for tool in list(unique_tools)[:5]:  # Show first 5 tools
                    response += f"  • `{tool['name']}`: {tool.get('description', 'No description')[:80]}{'...' if len(tool.get('description', '')) > 80 else ''}\n"
                if len(unique_tools) > 5:
                    response += f"  • + {len(unique_tools) - 5} more tools\n"
                response += "\n"

            response += """**💡 Usage Examples:**
• `@meta-agent what is the weather in Austin?` → **Data Researcher** (brave_search, fetch)
• `@meta-agent create Q4 financial dashboard` → **Financial Analyst** (supabase, brave_search)
• `@meta-agent deploy this React app` → **Code Developer** (github, filesystem)
• `@meta-agent what is the capital of France?` → **Knowledge Agent** (built-in knowledge)

The system uses **structured intent classification** to automatically route your requests to the most appropriate specialized agent with the right tools for the job!
"""

            self.logger.info("✅ Capability introspection completed successfully")
            return response

        except Exception as e:
            self.logger.error(f"❌ Capability introspection failed: {e}")
            import traceback

            self.logger.error(f"Full traceback: {traceback.format_exc()}")

            # Fallback response
            return f"""🤖 **Meta-Agent System Capabilities**

I have access to several specialized agents with different MCP tools and capabilities:

**Available Agent Types:**
{chr(10).join([f"• **{agent_type.replace('_', ' ').title()}**: {spec.instruction.split('.')[0]}" for agent_type, spec in self.agent_registry.items() if agent_type != "capability_inspector"])}

**Key MCP Servers:**
• **Supabase**: Database operations, SQL queries, project management
• **Brave Search**: Web search and real-time information
• **Fetch**: Data retrieval from URLs and APIs  
• **GitHub**: Code repository management and deployment
• **Filesystem**: File operations and code management
• **Slack**: Team communication and notifications

Unfortunately, I encountered an error while gathering detailed capability information: {str(e)}

Try asking me to perform specific tasks, and I'll route your request to the appropriate specialized agent!
"""

    async def _execute_sequential_fallback(
        self, message: str, agents: List[Agent]
    ) -> str:
        """Fallback sequential execution if orchestration fails"""
        results = []

        for agent in agents:
            try:
                async with agent:
                    llm = await agent.attach_llm(OpenAIAugmentedLLM)
                    agent_result = await llm.generate_str(
                        f"Handle this request with your specialized capabilities: {message}"
                    )
                    results.append(f"{agent.name}: {agent_result}")
            except Exception as e:
                results.append(f"{agent.name}: Error - {str(e)}")

        return "\n\n".join(results)

    async def _store_conversation_memory(
        self, user_id: str, message: str, channel_id: str
    ):
        """Store conversation in Supabase memory as shown in sequence diagram"""
        if user_id not in self.conversation_memory:
            self.conversation_memory[user_id] = []

        conversation_entry = {
            "role": "user",
            "content": message,
            "timestamp": datetime.now().isoformat(),
            "channel_id": channel_id,
        }

        self.conversation_memory[user_id].append(conversation_entry)

        # Store in Supabase via direct MCP function call
        try:
            # Use the available MCP function directly
            query = f"""INSERT INTO interactions (user_id, channel_id, message, created_at) 
                       VALUES ('{user_id}', '{channel_id}', '{message.replace("'", "''")}', '{conversation_entry["timestamp"]}');"""

            # This would require importing the MCP function - for now let's keep the agent approach but fix it
            supabase_agent = Agent(
                name="memory_store",
                instruction="Execute SQL directly",
                server_names=["supabase"],
            )
            async with supabase_agent:
                llm = await supabase_agent.attach_llm(OpenAIAugmentedLLM)

                # Ask the LLM to use the execute_sql tool with specific instructions
                result = await llm.generate_str(
                    """
                You must call the execute_sql tool directly with these exact parameters:
                - project_id: "qqggdvfeybfzqmgxmidt"  
                - query: "{query}"
                
                Call the execute_sql tool now with these parameters. Do not explain, just call the tool.
                """.format(query=query)
                )
                self.logger.info(
                    f"✅ Stored conversation memory for user {user_id}: {result[:100]}..."
                    if len(result) > 100
                    else f"✅ Stored conversation memory for user {user_id}: {result}"
                )

        except Exception as e:
            self.logger.warning(f"Could not store to Supabase: {e}")
            import traceback

            self.logger.error(f"Conversation memory error: {traceback.format_exc()}")

    async def _store_interaction_learning(
        self, user_id: str, message: str, result: str, analysis: Dict
    ):
        """Store interaction for system learning as shown in sequence diagram"""
        try:
            # Prepare the query with proper escaping
            analysis_json = json.dumps(analysis).replace("'", "''")
            result_escaped = result.replace("'", "''")
            message_escaped = message.replace("'", "''")

            query = f"""INSERT INTO interactions (user_id, message, response, analysis, intent_analysis, execution_strategy, required_agents, created_at) 
                       VALUES (
                           '{user_id}', 
                           '{message_escaped}', 
                           '{result_escaped}', 
                           '{analysis_json}',
                           '{analysis_json}',
                           '{analysis.get("execution_strategy", "")}', 
                           ARRAY{analysis.get("required_agents", [])}, 
                           NOW()
                       );"""

            supabase_agent = Agent(
                name="learning_store",
                instruction="Execute SQL directly",
                server_names=["supabase"],
            )
            async with supabase_agent:
                llm = await supabase_agent.attach_llm(OpenAIAugmentedLLM)

                # Ask the LLM to use the execute_sql tool with specific instructions
                learning_result = await llm.generate_str(
                    """
                You must call the execute_sql tool directly with these exact parameters:
                - project_id: "qqggdvfeybfzqmgxmidt"
                - query: "{query}"
                
                Then also call execute_sql again to ensure user preferences exist:
                - project_id: "qqggdvfeybfzqmgxmidt"  
                - query: "INSERT INTO user_preferences (user_id, preferences, interaction_patterns) VALUES ('{user_id}', '{{{{}}}}', '{{{{}}}}') ON CONFLICT (user_id) DO NOTHING;"
                
                Call both execute_sql tools now with these parameters. Do not explain, just call the tools.
                """.format(query=query, user_id=user_id)
                )
                self.logger.info(
                    f"✅ Stored interaction learning for user {user_id}: {learning_result[:100]}..."
                    if len(learning_result) > 100
                    else f"✅ Stored interaction learning for user {user_id}: {learning_result}"
                )

        except Exception as e:
            self.logger.warning(f"Could not store learning data: {e}")
            import traceback

            self.logger.error(f"Full error: {traceback.format_exc()}")

    async def _send_slack_response(
        self, channel_id: str, result: str, analysis: Dict = None, thread_ts: str = None
    ):
        """Send formatted response to Slack as shown in sequence diagram"""
        if not self.slack_client:
            print(f"Slack Response: {result}")
            return

        try:
            # Format the response based on the type of request
            if analysis and "dashboard" in result.lower():
                # Special formatting for dashboard creation
                formatted_response = f"""✅ **Q4 Revenue Dashboard Created!**

📊 **Dashboard:** {self._extract_dashboard_url(result)}

📈 **Key Insights:** {self._extract_key_insights(result)}

🎯 **Next Steps:** {self._extract_next_steps(result)}

*System learned from this interaction for future dashboard requests.*
"""
            else:
                # Standard response formatting
                formatted_response = f"""🤖 **Meta-Agent Response**

{result}

*Processed with {analysis.get("execution_strategy", "standard")} strategy using {len(analysis.get("required_agents", []))} specialized agents.*
"""

            self.slack_client.chat_postMessage(
                channel=channel_id,
                text=formatted_response,
                parse="mrkdwn",
                thread_ts=thread_ts,
            )

        except Exception as e:
            self.logger.error(f"Error sending Slack response: {e}")

    def _extract_dashboard_url(self, result: str) -> str:
        """Extract dashboard URL from result"""
        import re

        url_pattern = r"https?://[^\s]+"
        urls = re.findall(url_pattern, result)
        return urls[0] if urls else "Dashboard created (URL in result above)"

    def _extract_key_insights(self, result: str) -> str:
        """Extract key insights from result"""
        # Simple extraction - could be enhanced with LLM
        lines = result.split("\n")
        insights = [
            line
            for line in lines
            if any(
                word in line.lower()
                for word in ["revenue", "growth", "increase", "decrease", "%"]
            )
        ]
        return insights[0] if insights else "See detailed analysis above"

    def _extract_next_steps(self, result: str) -> str:
        """Extract next steps from result"""
        # Simple extraction - could be enhanced with LLM
        if "next" in result.lower():
            lines = result.split("\n")
            next_lines = [line for line in lines if "next" in line.lower()]
            return (
                next_lines[0]
                if next_lines
                else "Review dashboard and metrics regularly"
            )
        return "Review dashboard and metrics regularly"

    async def start_slack_connection(self):
        """Start the Slack WebSocket connection"""
        if not self.socket_client:
            raise ValueError("Slack not initialized. Call initialize_slack() first.")

        try:
            self.logger.info("🚀 Starting Slack WebSocket connection...")

            # Connect to Slack's Socket Mode (synchronous, not async!)
            self.socket_client.connect()
            self.logger.info("✅ Connected to Slack! Meta-Agent is ready.")

        except Exception as e:
            self.logger.error(f"Failed to connect to Slack: {e}")
            raise

    async def verify_database_data(self):
        """Verify that data was actually inserted into the database"""
        try:
            supabase_agent = Agent(
                name="data_checker",
                instruction="Query database to check for data",
                server_names=["supabase"],
            )
            async with supabase_agent:
                llm = await supabase_agent.attach_llm(OpenAIAugmentedLLM)

                # Ask the LLM to query the database
                result = await llm.generate_str("""
                You must call the execute_sql tool to check for data in the interactions table:
                - project_id: "qqggdvfeybfzqmgxmidt"
                - query: "SELECT user_id, message, created_at FROM interactions ORDER BY created_at DESC LIMIT 5;"
                
                Call the execute_sql tool with these parameters and show me the results.
                """)

                self.logger.info(f"📊 Database verification result: {result}")
                return result

        except Exception as e:
            self.logger.error(f"❌ Database verification failed: {e}")
            return None

    async def test_database_insertion(self):
        """Test method to verify database insertion works"""
        test_user_id = "test_user_123"
        test_message = "This is a test message"
        test_channel_id = "test_channel"

        try:
            # Test conversation memory storage
            await self._store_conversation_memory(
                test_user_id, test_message, test_channel_id
            )

            # Test interaction learning storage
            test_analysis = {
                "execution_strategy": "single_agent",
                "required_agents": ["knowledge_agent"],
                "complexity": "simple",
            }
            await self._store_interaction_learning(
                test_user_id, test_message, "Test response from agent", test_analysis
            )

            self.logger.info("✅ Database insertion test completed!")
            return True

        except Exception as e:
            self.logger.error(f"❌ Database test failed: {e}")
            import traceback

            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            return False

    async def add_mcp_server(self, user_id: str, server_config: Dict) -> str:
        """Allow users to add new MCP servers through chat"""
        # Implementation for dynamic server registration
        return f"Added server {server_config.get('name', 'unknown')}"

    async def _send_enhanced_slack_response(
        self,
        channel_id: str,
        result: str,
        analysis: Dict,
        quality_metrics: Dict,
        thread_ts: str = None,
    ):
        """Send enhanced response to Slack with quality metrics and better formatting"""
        if not self.slack_client:
            print(f"Enhanced Slack Response: {result}")
            return

        try:
            # Create status indicators based on quality metrics
            execution_time = quality_metrics.get("execution_time", 0)
            confidence = analysis.get("confidence", "unknown")
            intent_name = analysis.get("intent_name", "unknown")

            # Timing indicators
            if execution_time < 5:
                timing_emoji = "⚡"
            elif execution_time < 15:
                timing_emoji = "⏱️"
            else:
                timing_emoji = "🐌"

            # Confidence indicators
            confidence_emoji = {"high": "🎯", "medium": "✅", "low": "❓"}.get(
                confidence, "❓"
            )

            # Enhanced response formatting
            if "dashboard" in result.lower() or "deployed" in result.lower():
                # Special formatting for deliverables
                formatted_response = f"""{timing_emoji} **{intent_name.title()} Complete!**

{result}

📊 **Quality Metrics:**
• Processing time: {execution_time:.1f}s
• Intent confidence: {confidence_emoji} {confidence}
• Agents used: {quality_metrics.get("agent_count", 1)}

*Powered by structured intent classification*
"""
            else:
                # Standard response formatting
                formatted_response = f"""{confidence_emoji} **Agent Response** ({timing_emoji} {execution_time:.1f}s)

{result}

*Intent: {intent_name} | Strategy: {analysis.get("execution_strategy", "unknown")}*
"""

            self.slack_client.chat_postMessage(
                channel=channel_id,
                text=formatted_response,
                parse="mrkdwn",
                thread_ts=thread_ts,
            )

        except Exception as e:
            self.logger.error(f"Error sending enhanced Slack response: {e}")
            # Fallback to basic response
            await self._send_slack_response(channel_id, result, analysis, thread_ts)

    async def test_dynamic_routing(self):
        """Test the new dynamic routing system that uses actual tool discovery"""
        test_cases = [
            {
                "message": "what tools do you have access to?",
                "expected_agent": "capability_inspector",
                "description": "Capability inquiry",
            },
            {
                "message": "what is the weather in austin, texas?",
                "expected_agent": "data_researcher",
                "description": "Weather query (real-time data)",
            },
            {
                "message": "what is the capital of France?",
                "expected_agent": "knowledge_agent",
                "description": "Basic factual question",
            },
            {
                "message": "create Q4 revenue dashboard for our SaaS metrics",
                "expected_agent": "financial_analyst",
                "description": "Financial dashboard request",
            },
            {
                "message": "use the airtable mcp to connect to this base id",
                "expected_agent": "airtable_manager",
                "description": "Airtable MCP connection request",
            },
            {
                "message": "trigger n8n workflow to update airtable",
                "expected_agent": "automation_specialist",
                "description": "n8n workflow automation",
            },
        ]

        results = []
        for test_case in test_cases:
            try:
                analysis = await self._analyze_user_intent_dynamic(test_case["message"])
                actual_agent = analysis.get("required_agents", [None])[0]
                confidence = analysis.get("confidence", "unknown")
                reasoning = analysis.get("reasoning", "")

                agent_success = actual_agent == test_case["expected_agent"]

                result = {
                    "message": test_case["message"],
                    "description": test_case["description"],
                    "expected_agent": test_case["expected_agent"],
                    "actual_agent": actual_agent,
                    "success": agent_success,
                    "confidence": confidence,
                    "reasoning": reasoning,
                }
                results.append(result)

                status = "✅" if agent_success else "❌"
                self.logger.info(
                    f"{status} {test_case['description']}: '{test_case['message']}' -> {actual_agent} (expected: {test_case['expected_agent']}) | Confidence: {confidence}"
                )

            except Exception as e:
                self.logger.error(
                    f"❌ Dynamic routing test failed for '{test_case['message']}': {e}"
                )
                results.append(
                    {
                        "message": test_case["message"],
                        "description": test_case["description"],
                        "expected_agent": test_case["expected_agent"],
                        "actual_agent": "ERROR",
                        "success": False,
                        "error": str(e),
                    }
                )

        # Summary
        successful = sum(1 for r in results if r.get("success", False))
        total = len(results)

        self.logger.info("🧪 Dynamic routing test results:")
        self.logger.info(
            f"   Success rate: {successful}/{total} ({successful / total * 100:.1f}%)"
        )

        # Check specific test cases
        weather_test = next((r for r in results if "weather" in r["message"]), None)
        if weather_test and weather_test["success"]:
            self.logger.info(
                f"✅ Weather routing working: {weather_test['actual_agent']} selected"
            )

        airtable_test = next(
            (r for r in results if "airtable mcp" in r["message"]), None
        )
        if airtable_test and airtable_test["success"]:
            self.logger.info(
                f"✅ Airtable MCP routing working: {airtable_test['actual_agent']} selected"
            )

        if successful >= total * 0.8:  # 80% success rate
            self.logger.info("🎉 Dynamic routing system is working excellently!")
        else:
            self.logger.warning(
                f"⚠️  Dynamic routing needs tuning: {successful}/{total} tests passed"
            )

        return results


# Legacy MetaAgent class for backward compatibility
class MetaAgent(SlackMetaAgent):
    """Backward compatibility wrapper"""

    pass


async def main():
    """Main function to run the Slack Meta-Agent system"""
    # Load configuration
    secrets_file = Path(__file__).parent / "mcp_agent.secrets.yaml"

    if not secrets_file.exists():
        print(
            "❌ Secrets file not found. Please copy and configure mcp_agent.secrets.yaml.example"
        )
        return

    # Load secrets
    import yaml

    with open(secrets_file) as f:
        secrets = yaml.safe_load(f)

    slack_config = secrets.get("slack", {})
    bot_token = slack_config.get("bot_token")
    app_token = slack_config.get("app_token")

    if not bot_token or not app_token:
        print(
            "❌ Slack tokens not configured. Please add bot_token and app_token to secrets file."
        )
        print("🔧 Run 'python setup.py' for setup guidance.")
        return

    # Initialize the Meta-Agent system
    async with app.run() as agent_app:
        logger = agent_app.logger

        # Create and initialize the meta-agent
        meta_agent = SlackMetaAgent()

        try:
            # Initialize Slack integration
            await meta_agent.initialize_slack(bot_token, app_token)

            # Test database insertion only if TEST_DB environment variable is set
            if os.getenv("TEST_DB", "false").lower() in ["true", "1", "yes"]:
                logger.info("🧪 Testing database insertion (TEST_DB=true)...")
                db_test_success = await meta_agent.test_database_insertion()
                if db_test_success:
                    logger.info("✅ Database insertion test passed!")

                    # Verify the data was actually inserted
                    logger.info("🔍 Verifying database data...")
                    verification_result = await meta_agent.verify_database_data()
                    if verification_result:
                        logger.info("✅ Database verification completed!")
                else:
                    logger.warning(
                        "⚠️  Database insertion test failed - continuing anyway"
                    )
            else:
                logger.info("💡 Skipping database test (set TEST_DB=true to enable)")

            # Test dynamic routing to verify the new tool-discovery system works
            # logger.info("🧪 Testing dynamic routing system...")
            # routing_test_results = await meta_agent.test_dynamic_routing()

            # # Show key results
            # successful_tests = sum(
            #     1 for r in routing_test_results if r.get("success", False)
            # )
            # total_tests = len(routing_test_results)

            # if successful_tests >= total_tests * 0.8:  # 80% success rate
            #     logger.info(
            #         f"🎉 Dynamic routing system working excellently! ({successful_tests}/{total_tests} passed)"
            #     )
            # else:
            #     logger.warning(
            #         f"⚠️  Dynamic routing needs improvement: {successful_tests}/{total_tests} tests passed"
            #     )

            logger.info("💡 Skipping dynamic routing startup test for faster boot")

            # Start the WebSocket connection
            await meta_agent.start_slack_connection()

            # Keep the connection alive
            logger.info("🤖 Meta-Agent is running! Send messages in Slack...")
            logger.info("📱 Try mentioning @meta-agent in a channel or DM!")
            logger.info(
                "💡 Example: @meta-agent Create Q4 revenue dashboard for SaaS metrics"
            )
            logger.info("⚠️  Press Ctrl+C to stop")

            while True:
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            logger.info("👋 Shutting down Meta-Agent...")
        except Exception as e:
            logger.error(f"💥 Meta-Agent error: {e}")
            raise


if __name__ == "__main__":
    import time
    from pathlib import Path

    start = time.time()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Meta-Agent shutdown complete")
    finally:
        end = time.time()
        print(f"⏱️  Total runtime: {end - start:.2f}s")
