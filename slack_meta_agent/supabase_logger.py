import logging
import asyncio
import json
import uuid
from datetime import datetime
from typing import Optional, Dict, Any
import threading
import queue
import os
import yaml
from pathlib import Path

# Supabase direct client
try:
    from supabase import create_client, Client

    SUPABASE_AVAILABLE = True
except ImportError:
    print("⚠️ Supabase Python client not installed. Install with: pip install supabase")
    SUPABASE_AVAILABLE = False


class SupabaseLogHandler(logging.Handler):
    """Custom logging handler that sends logs to Supabase database using direct client"""

    def __init__(self, project_id: str, session_id: Optional[uuid.UUID] = None):
        super().__init__()
        self.project_id = project_id
        self.session_id = session_id or uuid.uuid4()
        self.log_queue = queue.Queue()
        self.supabase_client: Optional[Client] = None
        self.client_initialized = False
        self.batch_size = 50  # Larger batches to reduce noise
        self.batch_timeout = 10.0  # Longer timeout for better batching
        self.pending_logs = []
        self.last_batch_time = datetime.now()

        # Load Supabase credentials
        self.supabase_url = None
        self.supabase_service_key = None
        self._load_supabase_credentials()

        # Start background thread for log processing
        self.background_thread = threading.Thread(
            target=self._background_processor, daemon=True
        )
        self.background_thread.start()

    def _load_supabase_credentials(self):
        """Load Supabase credentials from secrets file"""
        try:
            # Look for secrets file in current directory and parent directories
            current_dir = Path.cwd()
            secrets_file = None

            while current_dir != current_dir.parent:
                for filename in ["mcp_agent.secrets.yaml", "mcp-agent.secrets.yaml"]:
                    potential_file = current_dir / filename
                    if potential_file.exists():
                        secrets_file = potential_file
                        break
                if secrets_file:
                    break
                current_dir = current_dir.parent

            if secrets_file:
                with open(secrets_file, "r") as f:
                    secrets = yaml.safe_load(f)

                supabase_config = secrets.get("supabase", {})
                self.supabase_url = supabase_config.get("url")
                self.supabase_service_key = supabase_config.get("service_role_key")

                if self.supabase_url and self.supabase_service_key:
                    print(f"✅ Loaded Supabase credentials from {secrets_file}")
                else:
                    print(f"⚠️ Incomplete Supabase credentials in {secrets_file}")
            else:
                print("⚠️ No secrets file found for Supabase credentials")

        except Exception as e:
            print(f"⚠️ Error loading Supabase credentials: {e}")

    def _initialize_client(self):
        """Initialize the Supabase client for database operations"""
        if not self.client_initialized and SUPABASE_AVAILABLE:
            try:
                if self.supabase_url and self.supabase_service_key:
                    self.supabase_client = create_client(
                        self.supabase_url, self.supabase_service_key
                    )
                    self.client_initialized = True
                    # Silent initialization
                else:
                    print("⚠️ Missing Supabase URL or service key")
            except Exception as e:
                print(f"⚠️ Failed to initialize Supabase client: {e}")

    def emit(self, record: logging.LogRecord):
        """Called by Python logging system to handle log records"""
        try:
            # Filter out noisy logs
            if self._should_filter_log(record):
                return

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

    def _should_filter_log(self, record: logging.LogRecord) -> bool:
        """Filter out noisy logs that clutter the database - AGGRESSIVE filtering"""

        # Only allow logs from our SlackMetaAgent and critical errors
        if record.name == "SlackMetaAgent":
            return False  # Always allow our app logs

        # Allow critical errors from any source
        if record.levelno >= logging.ERROR:
            return False  # Always allow errors

        # Block everything else during startup (very aggressive)
        message = record.getMessage()

        # Block all MCP framework noise
        if record.name == "mcp_agent":
            return True

        # Block all HTTP/networking noise
        if record.name in [
            "httpx",
            "urllib3",
            "requests",
            "httpcore",
            "h11",
            "h2",
            "hpack",
        ]:
            return True

        # Block progress indicators and status messages
        if any(
            phrase in message
            for phrase in [
                "Supabase client initialized",
                "Sent",
                "logs to Supabase",
                "Up and running",
                "Connection",
                "successfully",
                "MCPApp",
                "initialized",
                "Loading",
                "configuration",
                "Startup Complete",
                "SYSTEM OPTIMIZATIONS",
                "Meta-Agent is running",
                "Try mentioning",
                "Example:",
                "Press Ctrl+C",
            ]
        ):
            return True

        # Block everything else except errors and our app
        return True

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
                    # Check for sentinel value to stop processing
                    if first_log is None:
                        break
                    logs_to_process.append(first_log)
                except queue.Empty:
                    timeout_reached = True

                # Collect additional logs up to batch size
                if not timeout_reached:
                    while len(logs_to_process) < self.batch_size:
                        try:
                            log = self.log_queue.get_nowait()
                            # Check for sentinel value
                            if log is None:
                                break
                            logs_to_process.append(log)
                        except queue.Empty:
                            break

                # Process the batch if we have logs
                if logs_to_process:
                    self._send_logs_to_supabase(logs_to_process)

            except Exception as e:
                print(f"⚠️ Background log processor error: {e}")

    def _send_logs_to_supabase(self, logs: list):
        """Send a batch of logs to Supabase using direct client"""
        try:
            self._initialize_client()

            if not self.client_initialized or not self.supabase_client:
                return

            # Ensure the application_logs table exists
            self._ensure_logs_table_exists()

            # Prepare log records for insertion
            log_records = []
            for log in logs:
                record = {
                    "session_id": log["session_id"],
                    "level": log["level"],
                    "timestamp": log["timestamp"],
                    "namespace": log["namespace"],
                    "message": log["message"],
                    "data": log["data"],
                    "user_id": log["user_id"],
                    "channel_id": log["channel_id"],
                    "agent_name": log["agent_name"],
                    "server_name": log["server_name"],
                    "tool_name": log["tool_name"],
                    "execution_time_ms": log["execution_time_ms"],
                }
                log_records.append(record)

            # Insert logs using Supabase client
            result = (
                self.supabase_client.table("application_logs")
                .insert(log_records)
                .execute()
            )

            if result.data:
                pass  # Silent success - no need to spam console
            else:
                print(f"⚠️ No data returned from Supabase insert")

        except Exception as e:
            print(f"⚠️ Failed to send logs to Supabase: {e}")
            # Could implement retry logic or fallback storage here

    def _ensure_logs_table_exists(self):
        """Ensure the application_logs table exists in Supabase"""
        try:
            # Create table if it doesn't exist
            create_table_sql = """
            CREATE TABLE IF NOT EXISTS application_logs (
                id BIGSERIAL PRIMARY KEY,
                session_id UUID NOT NULL,
                level TEXT NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                namespace TEXT NOT NULL,
                message TEXT NOT NULL,
                data JSONB DEFAULT '{}',
                user_id TEXT,
                channel_id TEXT,
                agent_name TEXT,
                server_name TEXT,
                tool_name TEXT,
                execution_time_ms INTEGER,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """

            # Execute the table creation
            self.supabase_client.rpc("exec_sql", {"sql": create_table_sql}).execute()

        except Exception as e:
            # Table creation might fail if rpc function doesn't exist, that's ok
            # The table might already exist or need manual creation
            pass

    def close(self):
        """Synchronous cleanup for Python logging system"""
        # Mark as closed to prevent further log processing
        self.client_initialized = False
        try:
            if hasattr(self, "background_thread") and self.background_thread.is_alive():
                # Signal the background thread to stop
                self.log_queue.put(None)  # Sentinel value to stop processing
        except:
            pass


class FilteredConsoleHandler(logging.StreamHandler):
    """Console handler that filters out noisy logs"""

    def emit(self, record):
        """Only emit if log passes our filter"""
        if not self._should_filter_log(record):
            super().emit(record)

    def _should_filter_log(self, record: logging.LogRecord) -> bool:
        """Filter out noisy logs - SUPER CLEAN console output"""

        # Only show SlackMetaAgent logs and critical errors
        if record.name == "SlackMetaAgent":
            return False  # Always show our app logs

        # Always show critical errors
        if record.levelno >= logging.ERROR:
            return False

        # Block absolutely everything else for clean startup
        return True


def setup_supabase_logging(
    project_id: str, session_id: Optional[uuid.UUID] = None, level: str = "INFO"
):
    """Setup logging to send to Supabase instead of local files - ULTRA CLEAN"""

    # FIRST: Set root logger to ERROR level to silence ALL framework noise
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.ERROR)  # Block everything except errors

    # Remove any existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create session ID if not provided
    if session_id is None:
        session_id = uuid.uuid4()

    # Create and configure the Supabase handler (for database storage)
    supabase_handler = SupabaseLogHandler(project_id, session_id)
    supabase_handler.setLevel(logging.INFO)  # Store all app logs

    # Create formatter for structured logging
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    supabase_handler.setFormatter(formatter)

    # Create an ULTRA filtered console handler (only SlackMetaAgent + errors)
    console_handler = FilteredConsoleHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # Add our filtered handlers to root
    root_logger.addHandler(supabase_handler)
    root_logger.addHandler(console_handler)

    # NOW set SlackMetaAgent logger to INFO level (it will bypass the root level filtering)
    slack_logger = logging.getLogger("SlackMetaAgent")
    slack_logger.setLevel(logging.INFO)
    slack_logger.propagate = True  # Allow it to reach our handlers

    # Silent logging initialization
    return session_id, supabase_handler
