import logging
import asyncio
import json
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
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


class SessionLogAggregator:
    """Aggregates all logs for a session into a single database record"""

    def __init__(self, project_id: str, session_id: uuid.UUID):
        self.project_id = project_id
        self.session_id = session_id
        self.session_logs: List[Dict[str, Any]] = []
        self.session_start_time = datetime.now()
        self.session_metadata = {
            "user_interactions": [],
            "agents_used": set(),
            "tools_called": set(),
            "servers_connected": set(),
            "error_count": 0,
            "warning_count": 0,
        }
        self.supabase_client: Optional[Client] = None
        self.client_initialized = False
        self._load_supabase_credentials()
        self._lock = threading.Lock()

    def _load_supabase_credentials(self):
        """Load Supabase credentials from secrets file"""
        try:
            # First try the expected location: slack_meta_agent/config/mcp_agent.secrets.yaml
            current_dir = Path.cwd()

            # Look for slack_meta_agent directory structure first
            slack_meta_agent_dir = None
            if current_dir.name == "slack_meta_agent":
                slack_meta_agent_dir = current_dir
            else:
                # Check if we're in a subdirectory of slack_meta_agent
                for parent in current_dir.parents:
                    if parent.name == "slack_meta_agent":
                        slack_meta_agent_dir = parent
                        break

                # Check if slack_meta_agent is a subdirectory of current directory
                if not slack_meta_agent_dir:
                    potential_slack_dir = current_dir / "slack_meta_agent"
                    if potential_slack_dir.exists():
                        slack_meta_agent_dir = potential_slack_dir

            secrets_file = None

            # If we found slack_meta_agent directory, look in config subdirectory
            if slack_meta_agent_dir:
                config_dir = slack_meta_agent_dir / "config"
                if config_dir.exists():
                    for filename in [
                        "mcp_agent.secrets.yaml",
                        "mcp-agent.secrets.yaml",
                    ]:
                        potential_file = config_dir / filename
                        if potential_file.exists():
                            secrets_file = potential_file
                            break

            # Fallback: search in current directory and parents (original behavior)
            if not secrets_file:
                search_dir = current_dir
                while search_dir != search_dir.parent:
                    for filename in [
                        "mcp_agent.secrets.yaml",
                        "mcp-agent.secrets.yaml",
                    ]:
                        potential_file = search_dir / filename
                        if potential_file.exists():
                            secrets_file = potential_file
                            break
                    if secrets_file:
                        break
                    search_dir = search_dir.parent

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
                else:
                    print("⚠️ Missing Supabase URL or service key")
            except Exception as e:
                print(f"⚠️ Failed to initialize Supabase client: {e}")

    def add_log(self, log_data: Dict[str, Any]):
        """Add a log entry to the session buffer"""
        with self._lock:
            # Add timestamp if not present
            if "timestamp" not in log_data:
                log_data["timestamp"] = datetime.now().isoformat()

            self.session_logs.append(log_data)

            # Update session metadata
            if log_data.get("level") == "ERROR":
                self.session_metadata["error_count"] += 1
            elif log_data.get("level") == "WARNING":
                self.session_metadata["warning_count"] += 1

            if log_data.get("agent_name"):
                self.session_metadata["agents_used"].add(log_data["agent_name"])
            if log_data.get("tool_name"):
                self.session_metadata["tools_called"].add(log_data["tool_name"])
            if log_data.get("server_name"):
                self.session_metadata["servers_connected"].add(log_data["server_name"])
            if log_data.get("user_id"):
                user_interaction = {
                    "user_id": log_data["user_id"],
                    "timestamp": log_data["timestamp"],
                    "message": log_data.get("message", ""),
                    "channel_id": log_data.get("channel_id"),
                }
                self.session_metadata["user_interactions"].append(user_interaction)

    def finalize_session(self) -> bool:
        """Write the complete session log to Supabase as a single record"""
        try:
            self._initialize_client()

            if not self.client_initialized or not self.supabase_client:
                return False

            # Ensure the session_logs table exists
            self._ensure_session_logs_table_exists()

            # Convert sets to lists for JSON serialization
            metadata = dict(self.session_metadata)
            metadata["agents_used"] = list(metadata["agents_used"])
            metadata["tools_called"] = list(metadata["tools_called"])
            metadata["servers_connected"] = list(metadata["servers_connected"])

            # Create the session record
            session_record = {
                "session_id": str(self.session_id),
                "project_id": self.project_id,
                "start_time": self.session_start_time.isoformat(),
                "end_time": datetime.now().isoformat(),
                "total_logs": len(self.session_logs),
                "session_metadata": metadata,
                "logs": self.session_logs,  # All logs as JSON array
                "created_at": datetime.now().isoformat(),
            }

            # Insert the session record
            result = (
                self.supabase_client.table("session_logs")
                .insert(session_record)
                .execute()
            )

            if result.data:
                print(
                    f"✅ Session {self.session_id} saved with {len(self.session_logs)} logs"
                )
                return True
            else:
                print(f"⚠️ Failed to save session {self.session_id}")
                return False

        except Exception as e:
            print(f"⚠️ Error finalizing session {self.session_id}: {e}")
            return False

    def _ensure_session_logs_table_exists(self):
        """Ensure the session_logs table exists in Supabase"""
        try:
            create_table_sql = """
            CREATE TABLE IF NOT EXISTS session_logs (
                id BIGSERIAL PRIMARY KEY,
                session_id UUID NOT NULL UNIQUE,
                project_id TEXT NOT NULL,
                start_time TIMESTAMPTZ NOT NULL,
                end_time TIMESTAMPTZ NOT NULL,
                total_logs INTEGER NOT NULL DEFAULT 0,
                session_metadata JSONB DEFAULT '{}',
                logs JSONB DEFAULT '[]',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                
                -- Indexes for better query performance
                INDEX (session_id),
                INDEX (project_id),
                INDEX (start_time),
                INDEX ((session_metadata->>'error_count')),
                INDEX ((session_metadata->>'agents_used'))
            );
            """

            self.supabase_client.rpc("exec_sql", {"sql": create_table_sql}).execute()

        except Exception as e:
            # Table creation might fail if rpc function doesn't exist, that's ok
            pass


class SupabaseSessionLogHandler(logging.Handler):
    """Logging handler that aggregates logs by session instead of individual records"""

    def __init__(self, project_id: str, session_id: Optional[uuid.UUID] = None):
        super().__init__()
        self.project_id = project_id
        self.session_id = session_id or uuid.uuid4()

        # Use session aggregator instead of individual log storage
        self.session_aggregator = SessionLogAggregator(self.project_id, self.session_id)

        # Still keep original functionality as backup
        self.log_queue = queue.Queue()
        self.supabase_client: Optional[Client] = None
        self.client_initialized = False
        self.batch_size = 50
        self.batch_timeout = 10.0
        self.pending_logs = []
        self.last_batch_time = datetime.now()

        self._load_supabase_credentials()

        # Background thread for processing (optional - for backup logging)
        self.background_thread = threading.Thread(
            target=self._background_processor, daemon=True
        )
        self.background_thread.start()

    def _load_supabase_credentials(self):
        """Load Supabase credentials from secrets file"""
        try:
            # First try the expected location: slack_meta_agent/config/mcp_agent.secrets.yaml
            current_dir = Path.cwd()

            # Look for slack_meta_agent directory structure first
            slack_meta_agent_dir = None
            if current_dir.name == "slack_meta_agent":
                slack_meta_agent_dir = current_dir
            else:
                # Check if we're in a subdirectory of slack_meta_agent
                for parent in current_dir.parents:
                    if parent.name == "slack_meta_agent":
                        slack_meta_agent_dir = parent
                        break

                # Check if slack_meta_agent is a subdirectory of current directory
                if not slack_meta_agent_dir:
                    potential_slack_dir = current_dir / "slack_meta_agent"
                    if potential_slack_dir.exists():
                        slack_meta_agent_dir = potential_slack_dir

            secrets_file = None

            # If we found slack_meta_agent directory, look in config subdirectory
            if slack_meta_agent_dir:
                config_dir = slack_meta_agent_dir / "config"
                if config_dir.exists():
                    for filename in [
                        "mcp_agent.secrets.yaml",
                        "mcp-agent.secrets.yaml",
                    ]:
                        potential_file = config_dir / filename
                        if potential_file.exists():
                            secrets_file = potential_file
                            break

            # Fallback: search in current directory and parents (original behavior)
            if not secrets_file:
                search_dir = current_dir
                while search_dir != search_dir.parent:
                    for filename in [
                        "mcp_agent.secrets.yaml",
                        "mcp-agent.secrets.yaml",
                    ]:
                        potential_file = search_dir / filename
                        if potential_file.exists():
                            secrets_file = potential_file
                            break
                    if secrets_file:
                        break
                    search_dir = search_dir.parent

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

    def emit(self, record: logging.LogRecord):
        """Called by Python logging system - now aggregates logs by session"""
        try:
            # Filter out noisy logs
            if self._should_filter_log(record):
                return

            # Convert log record to structured format
            log_data = {
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

            # Add to session aggregator (primary method)
            self.session_aggregator.add_log(log_data)

            # Also add to queue for backup individual logging (optional)
            # self.log_queue.put(log_data, block=False)

        except Exception as e:
            print(f"⚠️ Session logging error: {e}")
            print(f"Original log: {record.levelname} - {record.getMessage()}")

    def finalize_session(self) -> bool:
        """Call this when the session ends to write all logs as one record"""
        return self.session_aggregator.finalize_session()

    def get_session_stats(self) -> Dict[str, Any]:
        """Get current session statistics"""
        return {
            "session_id": str(self.session_id),
            "total_logs": len(self.session_aggregator.session_logs),
            "start_time": self.session_aggregator.session_start_time.isoformat(),
            "metadata": dict(self.session_aggregator.session_metadata),
        }

    def _should_filter_log(self, record: logging.LogRecord) -> bool:
        """Filter out noisy logs that clutter the database - AGGRESSIVE filtering"""

        # Allow logs from our app loggers and critical errors
        if (
            record.name in ["SlackMetaAgent", "mcp_agent"]
            or record.name.startswith("mcp_agent.")
            or record.name.startswith(
                "slack_meta_agent."
            )  # Allow all slack_meta_agent logs
            or "Orchestrator" in record.name  # Show orchestrator activity
            or "Agent" in record.name  # Show all agent activity
            or "SlackAdapter" in record.name  # Show Slack events
            or "IntentAnalyzer" in record.name  # Show intent analysis
        ):
            return False  # Always allow our app logs

        # Allow critical errors from any source
        if record.levelno >= logging.ERROR:
            return False  # Always allow errors

        # Block everything else during startup (very aggressive)
        message = record.getMessage()

        # Skip the mcp_agent filter since we allow it above

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


# Keep original handler for backward compatibility
class SupabaseLogHandler(SupabaseSessionLogHandler):
    """Original handler - now extends session handler but with individual logging enabled"""

    def __init__(self, project_id: str, session_id: Optional[uuid.UUID] = None):
        super().__init__(project_id, session_id)
        # Enable individual logging for backward compatibility
        self.use_individual_logging = True

    def emit(self, record: logging.LogRecord):
        """Override to also do individual logging"""
        # Call parent's session aggregation
        super().emit(record)

        # Also do individual logging if enabled
        if self.use_individual_logging:
            try:
                if self._should_filter_log(record):
                    return

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

                # Add to queue for individual logging
                self.log_queue.put(log_data, block=False)

            except Exception as e:
                print(f"⚠️ Individual logging error: {e}")


class FilteredConsoleHandler(logging.StreamHandler):
    """Console handler that filters out noisy logs"""

    def emit(self, record):
        """Only emit if log passes our filter"""
        if not self._should_filter_log(record):
            super().emit(record)

    def _should_filter_log(self, record: logging.LogRecord) -> bool:
        """Filter out noisy logs - SUPER CLEAN console output"""

        # Show SlackMetaAgent logs and critical errors
        if (
            record.name == "SlackMetaAgent"
            or record.name.startswith("slack_meta_agent.")  # Show agent activity
            or "Orchestrator" in record.name  # Show orchestrator
            or "Agent" in record.name  # Show all agents
            or "SlackAdapter" in record.name  # Show Slack events
        ):
            return False  # Always show our app logs

        # Always show critical errors
        if record.levelno >= logging.ERROR:
            return False

        # Block absolutely everything else for clean startup
        return True


def setup_supabase_logging(
    project_id: str,
    session_id: Optional[uuid.UUID] = None,
    level: str = "INFO",
    use_session_aggregation: bool = True,
):
    """Setup logging to send to Supabase with option for session aggregation

    Args:
        project_id: Supabase project ID
        session_id: Optional session UUID
        level: Logging level
        use_session_aggregation: If True, aggregates all logs into one session record.
                                If False, uses individual log records.

    Returns:
        tuple: (session_id, supabase_handler)
    """

    # FIRST: Set root logger to ERROR level to silence ALL framework noise
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.ERROR)  # Block everything except errors

    # Remove any existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create session ID if not provided
    if session_id is None:
        session_id = uuid.uuid4()

    # Choose handler type based on preference
    if use_session_aggregation:
        supabase_handler = SupabaseSessionLogHandler(project_id, session_id)
        print(f"📝 Using session-aggregated logging (session: {session_id})")
    else:
        supabase_handler = SupabaseLogHandler(project_id, session_id)
        print(f"📝 Using individual log records (session: {session_id})")

    supabase_handler.setLevel(logging.INFO)

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

    # Set our app loggers to INFO level (they will bypass the root level filtering)
    slack_logger = logging.getLogger("SlackMetaAgent")
    slack_logger.setLevel(logging.INFO)
    slack_logger.propagate = True  # Allow it to reach our handlers

    # Also set mcp_agent loggers to INFO level to capture bot activity
    mcp_logger = logging.getLogger("mcp_agent")
    mcp_logger.setLevel(logging.INFO)
    mcp_logger.propagate = True

    return session_id, supabase_handler
