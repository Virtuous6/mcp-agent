import logging
import asyncio
import json
import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from mcp_agent.agents.agent import Agent
import threading
import queue


class SupabaseLogHandler(logging.Handler):
    """Custom logging handler that sends logs to Supabase database"""

    def __init__(self, project_id: str, session_id: Optional[uuid.UUID] = None):
        super().__init__()
        self.project_id = project_id
        self.session_id = session_id or uuid.uuid4()
        self.log_queue = queue.Queue()
        self.agent: Optional[Agent] = None
        self.agent_initialized = False
        self.batch_size = 10
        self.batch_timeout = 5.0  # seconds
        self.pending_logs = []
        self.last_batch_time = datetime.now()

        # Start background thread for log processing
        self.background_thread = threading.Thread(
            target=self._background_processor, daemon=True
        )
        self.background_thread.start()

    async def _initialize_agent(self):
        """Initialize the Supabase agent for database operations"""
        if not self.agent_initialized:
            try:
                self.agent = Agent(
                    name="supabase_logger",
                    instruction="You are a specialized logging agent that stores application logs in Supabase.",
                    server_names=["supabase"],
                )
                await self.agent.__aenter__()
                self.agent_initialized = True
            except Exception as e:
                print(f"⚠️ Failed to initialize Supabase logging agent: {e}")

    def emit(self, record: logging.LogRecord):
        """Called by Python logging system to handle log records"""
        try:
            # Convert log record to structured format
            log_data = {
                "session_id": str(self.session_id),
                "level": record.levelname,
                "timestamp": datetime.fromtimestamp(record.created).isoformat(),
                "namespace": record.name,
                "message": record.getMessage(),
                "data": self._extract_structured_data(record),
                "user_id": getattr(record, "user_id", None),
                "channel_id": getattr(record, "channel_id", None),
                "agent_name": getattr(record, "agent_name", None),
                "server_name": getattr(record, "server_name", None),
                "tool_name": getattr(record, "tool_name", None),
                "execution_time_ms": getattr(record, "execution_time_ms", None),
            }

            # Add to queue for background processing
            self.log_queue.put(log_data, block=False)

        except Exception as e:
            # Fallback to console if Supabase logging fails
            print(f"⚠️ Supabase logging error: {e}")
            print(f"Original log: {record.levelname} - {record.getMessage()}")

    def _extract_structured_data(self, record: logging.LogRecord) -> Dict[str, Any]:
        """Extract structured data from log record"""
        data = {}

        # Copy custom attributes
        for attr in ["data", "progress_action", "target", "tool_name", "server_name"]:
            if hasattr(record, attr):
                data[attr] = getattr(record, attr)

        # Add exception info if present
        if record.exc_info:
            data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": self.format(record) if record.exc_info else None,
            }

        return data

    def _background_processor(self):
        """Background thread that processes logs and sends them to Supabase"""
        while True:
            try:
                # Collect logs for batch processing
                logs_to_process = []
                timeout_reached = False

                # Wait for first log or timeout
                try:
                    first_log = self.log_queue.get(timeout=self.batch_timeout)
                    logs_to_process.append(first_log)
                except queue.Empty:
                    timeout_reached = True

                # Collect additional logs up to batch size
                if not timeout_reached:
                    while len(logs_to_process) < self.batch_size:
                        try:
                            log = self.log_queue.get_nowait()
                            logs_to_process.append(log)
                        except queue.Empty:
                            break

                # Process the batch if we have logs
                if logs_to_process:
                    asyncio.run(self._send_logs_to_supabase(logs_to_process))

            except Exception as e:
                print(f"⚠️ Background log processor error: {e}")

    async def _send_logs_to_supabase(self, logs: list):
        """Send a batch of logs to Supabase"""
        try:
            await self._initialize_agent()

            if not self.agent_initialized:
                return

            # Build batch insert SQL
            values_list = []
            for log in logs:
                values_list.append(f"""(
                    '{log["session_id"]}',
                    '{log["level"]}',
                    '{log["timestamp"]}',
                    '{log["namespace"].replace("'", "''")}',
                    '{log["message"].replace("'", "''")}',
                    '{json.dumps(log["data"]).replace("'", "''")}',
                    {f"'{log['user_id']}'" if log["user_id"] else "NULL"},
                    {f"'{log['channel_id']}'" if log["channel_id"] else "NULL"},
                    {f"'{log['agent_name']}'" if log["agent_name"] else "NULL"},
                    {f"'{log['server_name']}'" if log["server_name"] else "NULL"},
                    {f"'{log['tool_name']}'" if log["tool_name"] else "NULL"},
                    {log["execution_time_ms"] if log["execution_time_ms"] else "NULL"}
                )""")

            sql = f"""
            INSERT INTO application_logs (
                session_id, level, timestamp, namespace, message, data,
                user_id, channel_id, agent_name, server_name, tool_name, execution_time_ms
            ) VALUES {", ".join(values_list)};
            """

            # Execute the batch insert
            await self.agent.list_tools("supabase")  # Ensure connection is alive
            result = await self.agent.call_tool(
                "supabase", "execute_sql", {"query": sql}
            )

            print(f"✅ Sent {len(logs)} logs to Supabase")

        except Exception as e:
            print(f"⚠️ Failed to send logs to Supabase: {e}")
            # Could implement retry logic or fallback storage here

    async def close(self):
        """Clean up the logger handler"""
        if self.agent_initialized and self.agent:
            try:
                await self.agent.__aexit__(None, None, None)
            except:
                pass


def setup_supabase_logging(
    project_id: str, session_id: Optional[uuid.UUID] = None, level: str = "INFO"
):
    """Setup logging to send to Supabase instead of local files"""

    # Create session ID if not provided
    if session_id is None:
        session_id = uuid.uuid4()

    # Create and configure the Supabase handler
    supabase_handler = SupabaseLogHandler(project_id, session_id)
    supabase_handler.setLevel(getattr(logging, level.upper()))

    # Create formatter for structured logging
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    supabase_handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    # Remove any existing handlers to avoid duplicate logs
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add our Supabase handler
    root_logger.addHandler(supabase_handler)

    # Also add console handler for immediate feedback
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    print(f"✅ Supabase logging initialized for session: {session_id}")

    return session_id, supabase_handler
