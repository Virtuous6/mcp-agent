"""
Slack Adapter Agent - Handles all Slack-specific interactions.

This component is responsible for:
1. Listening to Slack events
2. Normalizing Slack messages into platform-agnostic format
3. Handling Slack-specific formatting and responses
4. Managing human input callbacks for Slack threads

Extracted from SlackMetaAgent to provide clean separation of concerns.
"""

import re
import asyncio
from datetime import datetime
from typing import Dict, Optional, Any, Set

from mcp_agent.human_input.types import HumanInputRequest, HumanInputResponse

from ..core.types import (
    IncomingMessage,
    MessageContext,
    ExecutionResult,
    AgentComponent,
)


class SlackAdapterAgent(AgentComponent):
    """
    Handles all Slack-specific protocol details and message normalization.

    Isolates Slack API complexity so other components never need to deal
    with Slack threads, channels, reactions, or formatting specifics.
    """

    def __init__(self, slack_manager=None):
        super().__init__("SlackAdapter")

        self.slack_manager = slack_manager
        self.orchestrator = None  # Will be injected

        # Message deduplication
        self.processed_messages: Set[str] = set()
        self.last_cleanup_time = datetime.now()

        # Human input handling for Slack
        self.pending_human_inputs: Dict[str, asyncio.Future] = {}  # user_id -> Future
        self.current_thread_ts: Optional[str] = None
        self.current_user_id: Optional[str] = None
        self.current_channel_id: Optional[str] = None

    def set_orchestrator(self, orchestrator):
        """Inject orchestrator dependency."""
        self.orchestrator = orchestrator

    async def initialize(self, bot_token: str, app_token: str) -> bool:
        """Initialize Slack integration."""
        if not self.slack_manager:
            self.logger.error("No Slack manager provided")
            return False

        try:
            await self.slack_manager.initialize(
                bot_token, app_token, self._handle_slack_event
            )
            self.logger.info("✅ Slack adapter initialized")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Slack adapter: {e}")
            return False

    async def start_connection(self) -> None:
        """Start the Slack connection."""
        if self.slack_manager:
            await self.slack_manager.start_connection()

    async def _handle_slack_event(self, event: Dict) -> None:
        """Handle incoming Slack events and route to orchestrator."""
        try:
            # Filter and validate event
            if self._should_ignore_event(event):
                return

            self.logger.info(
                f"📩 Processing Slack event: {event.get('text', '')[:100]}..."
            )

            # Extract event data
            user_id = event.get("user")
            channel_id = event.get("channel")
            message_text = event.get("text", "")
            message_ts = event.get("ts")
            thread_ts = event.get("thread_ts")

            if not user_id or not channel_id or not message_text:
                return

            # 🚨 PRIORITY: Check if this is a response to pending human input
            if user_id in self.pending_human_inputs and thread_ts:
                await self._handle_human_input_response(user_id, message_text)
                return

            # Only process app mentions or direct messages
            if not (event.get("type") == "app_mention" or channel_id.startswith("D")):
                return

            # Set current context for human input callbacks
            self.current_user_id = user_id
            self.current_channel_id = channel_id
            self.current_thread_ts = message_ts

            # Add eyes reaction to show bot received the message
            await self._add_reaction(channel_id, message_ts, "eyes")

            # Normalize to platform-agnostic message
            incoming_message = self._normalize_slack_message(event)
            self.logger.info(
                f"🔄 Normalized message: '{incoming_message.text}' from user {user_id}"
            )

            # Send to orchestrator
            if self.orchestrator:
                self.logger.info(
                    f"📨 Sending message to orchestrator: {incoming_message.text[:50]}..."
                )
                try:
                    result = await self.orchestrator.handle(incoming_message)
                    self.logger.info(
                        f"✅ Orchestrator returned result: {len(result.response) if result and result.response else 0} chars"
                    )
                    await self._send_result_to_slack(result, incoming_message.context)
                    self.logger.info(f"📤 Response sent to Slack successfully")
                except Exception as orch_error:
                    self.logger.error(f"❌ Orchestrator failed: {orch_error}")
                    await self._send_error_to_slack(
                        f"Internal processing error: {str(orch_error)}",
                        channel_id,
                        message_ts,
                    )
            else:
                self.logger.error("No orchestrator available to handle message")
                await self._send_error_to_slack(
                    "System not properly initialized", channel_id, message_ts
                )

        except Exception as e:
            self.logger.error(f"Error processing Slack event: {e}")
            if "channel_id" in locals() and "message_ts" in locals():
                await self._send_error_to_slack(
                    f"Sorry, I encountered an error: {str(e)}", channel_id, message_ts
                )

    async def slack_human_input_callback(
        self, request: HumanInputRequest
    ) -> HumanInputResponse:
        """Handle human input requests by sending them to Slack and waiting for response."""
        try:
            if not self._can_handle_human_input():
                self.logger.warning(
                    "Cannot handle human input via Slack, falling back to console"
                )
                return await self._fallback_to_console(request)

            channel_id = self.current_channel_id
            user_id = self.current_user_id

            self.logger.info(
                f"🤖 Sending human input request to Slack for user {user_id}"
            )

            # Format the request for Slack
            formatted_message = f"""🤖 Agent needs more information:

{request.prompt}

💡 Context: {request.description or "Please provide the requested information."}

Reply in this thread to continue..."""

            # Send the request to Slack
            response = await self.slack_manager.send_response(
                channel_id,
                self._clean_markdown_from_response(formatted_message),
                self.current_thread_ts,
            )

            if not response or not response.get("ok", False):
                self.logger.error("Failed to send human input request to Slack")
                return await self._fallback_to_console(request)

            # Create future and wait for user response
            response_future = asyncio.Future()
            self.pending_human_inputs[user_id] = response_future

            self.logger.info(f"⏳ Waiting for human input response from user {user_id}")

            try:
                user_response = await asyncio.wait_for(
                    response_future, timeout=300.0
                )  # 5 minute timeout
                self.logger.info(
                    f"✅ Received human input response: {user_response[:50]}..."
                )

                return HumanInputResponse(
                    request_id=request.request_id or "slack_input",
                    response=user_response,
                )

            except asyncio.TimeoutError:
                self.logger.warning("⏰ Human input request timed out")

                # Clean up pending request
                if user_id in self.pending_human_inputs:
                    del self.pending_human_inputs[user_id]

                # Send timeout message
                await self.slack_manager.send_response(
                    channel_id,
                    "⏰ Request timed out - Please try your original request again.",
                    self.current_thread_ts,
                )

                return HumanInputResponse(
                    request_id=request.request_id or "slack_timeout",
                    response="Request timed out. Please try again.",
                )

        except Exception as e:
            self.logger.error(f"Error in Slack human input callback: {e}")
            return await self._fallback_to_console(request)

    async def cleanup(self) -> None:
        """Clean up Slack adapter resources."""
        if self.slack_manager:
            await self.slack_manager.cleanup()

        # Clear pending inputs
        for future in self.pending_human_inputs.values():
            if not future.done():
                future.cancel()
        self.pending_human_inputs.clear()

        self.logger.info("✅ Slack adapter cleanup complete")

    # Private methods

    def _should_ignore_event(self, event: Dict) -> bool:
        """Check if event should be ignored to prevent loops and duplicates."""
        # Filter for relevant events only
        if event.get("type") not in ["app_mention", "message"]:
            return True

        # Ignore bot messages and messages with subtypes
        if event.get("subtype") or event.get("bot_id"):
            self.logger.debug(
                f"Ignoring bot message or subtype: {event.get('subtype')}"
            )
            return True

        # Check for required fields
        user_id = event.get("user")
        channel_id = event.get("channel")
        message_text = event.get("text", "")
        message_ts = event.get("ts")

        if not user_id or not channel_id or not message_text or not message_ts:
            self.logger.debug("Missing required message fields")
            return True

        # Create unique message identifier for deduplication
        message_id = f"{user_id}_{channel_id}_{message_ts}_{hash(message_text)}"

        # Check if already processed
        if message_id in self.processed_messages:
            self.logger.debug(f"Duplicate message detected, ignoring: {message_id}")
            return True

        # Add to processed messages
        self.processed_messages.add(message_id)

        # Periodic cleanup of old processed messages
        now = datetime.now()
        if (now - self.last_cleanup_time).total_seconds() > 600:  # 10 minutes
            if len(self.processed_messages) > 1000:
                # Keep only last 500 message IDs
                message_list = list(self.processed_messages)
                self.processed_messages = set(message_list[-500:])
                self.logger.debug("Cleaned up old processed message IDs")
            self.last_cleanup_time = now

        return False

    def _normalize_slack_message(self, event: Dict) -> IncomingMessage:
        """Normalize Slack event into platform-agnostic message format."""
        user_id = event.get("user")
        channel_id = event.get("channel")
        message_text = event.get("text", "")
        message_ts = event.get("ts")
        thread_ts = event.get("thread_ts")

        # Clean message text
        clean_text = self._clean_user_input(message_text)

        # Create context
        context = MessageContext(
            user_id=user_id,
            channel_id=channel_id,
            message_ts=message_ts,
            thread_ts=thread_ts,
            timestamp=message_ts,
            platform="slack",
        )

        return IncomingMessage(text=clean_text, context=context, raw_event=event)

    def _clean_user_input(self, message_text: str) -> str:
        """Clean user input by removing Slack-specific formatting."""
        # Remove bot mentions (e.g., <@U0933UC9QEB>)
        clean_text = re.sub(r"<@[A-Z0-9]+>", "", message_text).strip()

        # Remove channel mentions (e.g., <#C1234567890>)
        clean_text = re.sub(r"<#[A-Z0-9]+\|[^>]+>", "", clean_text).strip()

        # Remove URL formatting (e.g., <https://example.com|example.com>)
        clean_text = re.sub(r"<[^>]+\|[^>]+>", "", clean_text).strip()

        # Remove simple URL wrapping (e.g., <https://example.com>)
        clean_text = re.sub(r"<(https?://[^>]+)>", r"\1", clean_text).strip()

        # Clean up multiple spaces
        clean_text = re.sub(r"\s+", " ", clean_text).strip()

        return clean_text

    def _clean_markdown_from_response(self, response: str) -> str:
        """Remove markdown formatting for Slack display."""
        if not response:
            return response

        # Remove bold formatting **text** -> text
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", response)

        # Replace bullet points • with dashes
        cleaned = cleaned.replace("•", "-")

        # Replace multiple consecutive dashes with single dashes
        cleaned = re.sub(r"^\s*-\s*-", "-", cleaned, flags=re.MULTILINE)

        return cleaned

    async def _send_result_to_slack(
        self, result: ExecutionResult, context: MessageContext
    ) -> None:
        """Send execution result back to Slack with enhanced formatting."""
        try:
            # Create status indicators
            execution_time = result.execution_time
            confidence = result.intent_confidence

            # Timing indicators
            timing_emoji = (
                "⚡" if execution_time < 5 else "⏱️" if execution_time < 15 else "🐌"
            )

            # Confidence indicators
            confidence_emoji = {"high": "🎯", "medium": "✅", "low": "❓"}.get(
                confidence, "❓"
            )

            # Format response based on type
            if (
                "dashboard" in result.response.lower()
                or "deployed" in result.response.lower()
            ):
                # Special formatting for deliverables
                formatted_response = f"""{timing_emoji} **Task Complete!**

{result.response}

📊 **Performance Metrics:**
• Processing time: {execution_time:.1f}s
• Intent confidence: {confidence_emoji} {confidence}
• Agents used: {result.agent_count}

*Optimized with pre-warmed agents & caching*"""
            else:
                # Standard response formatting
                formatted_response = f"""{confidence_emoji} **Agent Response** ({timing_emoji} {execution_time:.1f}s)

{result.response}

*Strategy: {result.metadata.get("execution_strategy", "unknown")} | Complexity: {result.complexity}*"""

            # Clean markdown and send
            clean_response = self._clean_markdown_from_response(formatted_response)

            await self.slack_manager.send_response(
                context.channel_id,
                clean_response,
                context.thread_ts or context.message_ts,
            )

        except Exception as e:
            self.logger.error(f"Error sending result to Slack: {e}")
            # Fallback to basic response
            clean_result = self._clean_markdown_from_response(result.response)
            await self.slack_manager.send_response(
                context.channel_id,
                clean_result,
                context.thread_ts or context.message_ts,
            )

    async def _send_error_to_slack(
        self, error_message: str, channel_id: str, thread_ts: str = None
    ) -> None:
        """Send error message to Slack."""
        try:
            clean_error = self._clean_markdown_from_response(error_message)
            await self.slack_manager.send_response(channel_id, clean_error, thread_ts)
        except Exception as e:
            self.logger.error(f"Failed to send error to Slack: {e}")

    async def _add_reaction(
        self, channel_id: str, timestamp: str, reaction: str
    ) -> None:
        """Add reaction to show message was received."""
        try:
            if (
                hasattr(self.slack_manager, "slack_client")
                and self.slack_manager.slack_client
            ):
                self.slack_manager.slack_client.reactions_add(
                    channel=channel_id, timestamp=timestamp, name=reaction
                )
                self.logger.debug(f"👀 Added {reaction} reaction")
        except Exception as e:
            self.logger.warning(f"Could not add reaction: {e}")

    async def _handle_human_input_response(
        self, user_id: str, message_text: str
    ) -> None:
        """Handle response to pending human input request."""
        self.logger.info(f"📝 Received human input response from user {user_id}")

        try:
            clean_text = self._clean_user_input(message_text)

            # Resolve the Future with user's response
            future = self.pending_human_inputs[user_id]
            if not future.done():
                future.set_result(clean_text)
                self.logger.info(f"✅ Human input resolved: {clean_text[:50]}...")

            # Clean up the pending request
            del self.pending_human_inputs[user_id]

        except Exception as e:
            self.logger.error(f"Error processing human input response: {e}")

    def _can_handle_human_input(self) -> bool:
        """Check if we can handle human input via Slack."""
        return (
            hasattr(self.slack_manager, "slack_client")
            and self.slack_manager.slack_client
            and self.current_thread_ts
            and self.current_channel_id
            and self.current_user_id
        )

    async def _fallback_to_console(
        self, request: HumanInputRequest
    ) -> HumanInputResponse:
        """Fallback to console input when Slack is unavailable."""
        try:
            from mcp_agent.human_input.handler import console_input_callback

            return await console_input_callback(request)
        except ImportError:
            # Basic fallback if console handler not available
            return HumanInputResponse(
                request_id=request.request_id or "fallback",
                response="Unable to get human input - system unavailable",
            )

    async def health_check(self) -> Dict[str, Any]:
        """Check component health."""
        base_health = await super().health_check()

        slack_available = (
            self.slack_manager is not None
            and hasattr(self.slack_manager, "slack_client")
            and self.slack_manager.slack_client is not None
        )

        return {
            **base_health,
            "slack_manager_available": self.slack_manager is not None,
            "slack_client_available": slack_available,
            "orchestrator_available": self.orchestrator is not None,
            "pending_human_inputs": len(self.pending_human_inputs),
            "processed_messages_count": len(self.processed_messages),
            "current_context": {
                "user_id": self.current_user_id,
                "channel_id": self.current_channel_id,
                "thread_ts": self.current_thread_ts,
            },
        }
