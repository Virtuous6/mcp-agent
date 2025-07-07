"""
Slack client management for the Slack Meta-Agent system

This module handles all Slack-specific communication and integration.
"""

import asyncio
import logging
from typing import Dict, Callable, Optional

try:
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse
    from slack_sdk import WebClient

    SLACK_AVAILABLE = True
except ImportError:
    SLACK_AVAILABLE = False


class SlackClientManager:
    """Manages Slack client connections and message handling"""

    def __init__(self):
        self.logger = logging.getLogger("SlackClientManager")
        self.slack_client: Optional[WebClient] = None
        self.socket_client: Optional[SocketModeClient] = None
        self.event_loop = None
        self.message_handler: Optional[Callable] = None

    async def initialize(
        self, bot_token: str, app_token: str, message_handler: Callable
    ):
        """Initialize Slack clients and set message handler"""
        if not SLACK_AVAILABLE:
            raise ImportError(
                "Slack SDK not available. Install with: pip install slack-sdk"
            )

        try:
            self.logger.info("🔌 Initializing Slack clients...")
            self.message_handler = message_handler
            self.event_loop = asyncio.get_event_loop()

            # Create Web client
            self.slack_client = WebClient(token=bot_token)
            self.logger.info("✅ Slack Web client created")

            # Create Socket Mode client
            self.socket_client = SocketModeClient(
                app_token=app_token, web_client=self.slack_client
            )

            # Register event handlers
            self.socket_client.socket_mode_request_listeners.append(
                self._handle_slack_events
            )
            self.logger.info("✅ Slack Socket Mode client configured")

        except Exception as e:
            self.logger.error(f"Failed to initialize Slack: {e}")
            raise

    async def start_connection(self):
        """Start the Slack WebSocket connection"""
        if not self.socket_client:
            raise ValueError("Slack not initialized. Call initialize() first.")

        try:
            self.logger.info("🚀 Starting Slack WebSocket connection...")

            # Run the blocking connect call in a thread pool to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.socket_client.connect)

            self.logger.info("✅ Connected to Slack! Meta-Agent is ready.")

            # Give the connection a moment to stabilize
            await asyncio.sleep(1)

        except Exception as e:
            self.logger.error(f"Failed to connect to Slack: {e}")
            raise

    async def send_response(self, channel_id: str, message: str, thread_ts: str = None):
        """Send a response message to Slack"""
        if not self.slack_client:
            self.logger.warning("No Slack client available, printing message:")
            print(f"Slack Response: {message}")
            return None

        try:
            response = self.slack_client.chat_postMessage(
                channel=channel_id,
                text=message,
                parse="mrkdwn",
                thread_ts=thread_ts,
            )
            return response
        except Exception as e:
            self.logger.error(f"Error sending Slack response: {e}")
            return None

    def _handle_slack_events(self, client: SocketModeClient, req: SocketModeRequest):
        """Handle incoming Slack events (synchronous handler for Slack SDK)"""
        if req.type == "events_api":
            # Acknowledge the request immediately
            client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )

            # Process the event asynchronously
            event = req.payload.get("event", {})
            if (
                self.event_loop
                and not self.event_loop.is_closed()
                and self.message_handler
            ):
                try:
                    asyncio.run_coroutine_threadsafe(
                        self.message_handler(event), self.event_loop
                    )
                except RuntimeError as e:
                    if "shutdown" in str(e):
                        self.logger.warning(
                            "Event loop is shutting down, ignoring event"
                        )
                    else:
                        self.logger.error(f"Error scheduling event handler: {e}")

        elif req.type == "slash_commands":
            # Acknowledge slash commands immediately
            client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )

            # Process slash command asynchronously
            command_data = req.payload
            if (
                self.event_loop
                and not self.event_loop.is_closed()
                and self.message_handler
            ):
                try:
                    # Convert slash command to event format
                    event = {
                        "type": "message",
                        "user": command_data.get("user_id", ""),
                        "channel": command_data.get("channel_id", ""),
                        "text": command_data.get("text", ""),
                    }
                    asyncio.run_coroutine_threadsafe(
                        self.message_handler(event), self.event_loop
                    )
                except RuntimeError as e:
                    if "shutdown" in str(e):
                        self.logger.warning(
                            "Event loop is shutting down, ignoring command"
                        )
                    else:
                        self.logger.error(f"Error scheduling command handler: {e}")

        else:
            # Acknowledge other request types
            client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )

    async def cleanup(self):
        """Clean up Slack connections"""
        try:
            if self.socket_client:
                self.socket_client.disconnect()
                self.logger.info("✅ Slack connection closed")
        except Exception as e:
            self.logger.warning(f"Error cleaning up Slack connection: {e}")
