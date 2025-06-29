#!/usr/bin/env python3
"""
Query Session Logs from Supabase

This script demonstrates how to query and analyze the session-aggregated logs
that are stored as single records per session instead of individual log entries.

Usage:
    python query_session_logs.py [session_id]
"""

import json
import sys
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import yaml
from pathlib import Path

try:
    from supabase import create_client, Client

    SUPABASE_AVAILABLE = True
except ImportError:
    print("⚠️ Supabase Python client not installed. Install with: pip install supabase")
    SUPABASE_AVAILABLE = False


class SessionLogAnalyzer:
    """Analyzes session-aggregated logs from Supabase"""

    def __init__(self):
        self.supabase_client: Optional[Client] = None
        self._load_supabase_credentials()

    def _load_supabase_credentials(self):
        """Load Supabase credentials from secrets file"""
        try:
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
                supabase_url = supabase_config.get("url")
                supabase_service_key = supabase_config.get("service_role_key")

                if supabase_url and supabase_service_key:
                    self.supabase_client = create_client(
                        supabase_url, supabase_service_key
                    )
                    print(f"✅ Connected to Supabase")
                else:
                    print(f"⚠️ Incomplete Supabase credentials in {secrets_file}")
            else:
                print("⚠️ No secrets file found for Supabase credentials")

        except Exception as e:
            print(f"⚠️ Error loading Supabase credentials: {e}")

    def get_session_logs(
        self, session_id: Optional[str] = None, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get session logs from Supabase"""
        if not self.supabase_client:
            print("❌ Supabase client not initialized")
            return []

        try:
            query = self.supabase_client.table("session_logs").select("*")

            if session_id:
                query = query.eq("session_id", session_id)
            else:
                query = query.order("start_time", desc=True).limit(limit)

            result = query.execute()
            return result.data or []

        except Exception as e:
            print(f"❌ Error querying session logs: {e}")
            return []

    def analyze_session(self, session_data: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze a single session's data"""
        logs = session_data.get("logs", [])
        metadata = session_data.get("session_metadata", {})

        # Basic analysis
        start_time = datetime.fromisoformat(
            session_data.get("start_time", "").replace("Z", "+00:00")
        )
        end_time = datetime.fromisoformat(
            session_data.get("end_time", "").replace("Z", "+00:00")
        )
        duration = end_time - start_time

        # Log level distribution
        level_counts = {}
        for log in logs:
            level = log.get("level", "UNKNOWN")
            level_counts[level] = level_counts.get(level, 0) + 1

        # Timeline of key events
        key_events = []
        for log in logs:
            if any(
                keyword in log.get("message", "").lower()
                for keyword in [
                    "error",
                    "failed",
                    "success",
                    "started",
                    "completed",
                    "user interaction",
                ]
            ):
                key_events.append(
                    {
                        "timestamp": log.get("timestamp"),
                        "level": log.get("level"),
                        "message": log.get("message", "")[:100] + "..."
                        if len(log.get("message", "")) > 100
                        else log.get("message", ""),
                        "namespace": log.get("namespace"),
                    }
                )

        return {
            "session_id": session_data.get("session_id"),
            "duration_seconds": duration.total_seconds(),
            "total_logs": len(logs),
            "log_levels": level_counts,
            "agents_used": metadata.get("agents_used", []),
            "tools_called": metadata.get("tools_called", []),
            "servers_connected": metadata.get("servers_connected", []),
            "error_count": metadata.get("error_count", 0),
            "warning_count": metadata.get("warning_count", 0),
            "user_interactions": len(metadata.get("user_interactions", [])),
            "key_events": key_events[:10],  # First 10 key events
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        }

    def print_session_summary(self, analysis: Dict[str, Any]):
        """Print a formatted session summary"""
        print(f"\n🎯 SESSION ANALYSIS")
        print(f"{'=' * 50}")
        print(f"Session ID: {analysis['session_id']}")
        print(f"Duration: {analysis['duration_seconds']:.1f} seconds")
        print(f"Start: {analysis['start_time']}")
        print(f"End: {analysis['end_time']}")

        print(f"\n📊 LOG STATISTICS")
        print(f"Total Logs: {analysis['total_logs']}")
        print(f"Log Levels: {json.dumps(analysis['log_levels'], indent=2)}")
        print(f"Errors: {analysis['error_count']}")
        print(f"Warnings: {analysis['warning_count']}")

        print(f"\n🤖 SYSTEM USAGE")
        print(
            f"Agents Used: {', '.join(analysis['agents_used']) if analysis['agents_used'] else 'None'}"
        )
        print(
            f"Tools Called: {', '.join(analysis['tools_called'][:5]) if analysis['tools_called'] else 'None'}"
        )
        if len(analysis["tools_called"]) > 5:
            print(f"  ... and {len(analysis['tools_called']) - 5} more tools")
        print(
            f"Servers Connected: {', '.join(analysis['servers_connected']) if analysis['servers_connected'] else 'None'}"
        )

        print(f"\n👥 USER INTERACTIONS")
        print(f"Total Interactions: {analysis['user_interactions']}")

        if analysis["key_events"]:
            print(f"\n🔍 KEY EVENTS")
            for event in analysis["key_events"]:
                timestamp = datetime.fromisoformat(
                    event["timestamp"].replace("Z", "+00:00")
                )
                print(
                    f"  {timestamp.strftime('%H:%M:%S')} [{event['level']}] {event['message']}"
                )

    def compare_sessions(self, session_ids: List[str]) -> Dict[str, Any]:
        """Compare multiple sessions"""
        sessions = []
        for session_id in session_ids:
            session_data = self.get_session_logs(session_id)
            if session_data:
                sessions.append(self.analyze_session(session_data[0]))

        if not sessions:
            return {}

        # Comparison metrics
        avg_duration = sum(s["duration_seconds"] for s in sessions) / len(sessions)
        avg_logs = sum(s["total_logs"] for s in sessions) / len(sessions)
        total_errors = sum(s["error_count"] for s in sessions)

        all_agents = set()
        all_tools = set()
        for s in sessions:
            all_agents.update(s["agents_used"])
            all_tools.update(s["tools_called"])

        return {
            "sessions_compared": len(sessions),
            "average_duration": avg_duration,
            "average_log_count": avg_logs,
            "total_errors_across_sessions": total_errors,
            "unique_agents_used": list(all_agents),
            "unique_tools_used": list(all_tools),
            "sessions": sessions,
        }


def main():
    """Main function for command-line usage"""
    if not SUPABASE_AVAILABLE:
        print("❌ Please install Supabase client: pip install supabase")
        return

    analyzer = SessionLogAnalyzer()

    if len(sys.argv) > 1:
        # Query specific session
        session_id = sys.argv[1]
        print(f"🔍 Querying session: {session_id}")

        sessions = analyzer.get_session_logs(session_id)
        if sessions:
            session_data = sessions[0]
            analysis = analyzer.analyze_session(session_data)
            analyzer.print_session_summary(analysis)

            # Show raw logs if requested
            if len(sys.argv) > 2 and sys.argv[2] == "--raw":
                print(f"\n📋 RAW LOGS ({len(session_data.get('logs', []))} entries)")
                print("=" * 50)
                for i, log in enumerate(
                    session_data.get("logs", [])[:20]
                ):  # First 20 logs
                    timestamp = datetime.fromisoformat(
                        log.get("timestamp", "").replace("Z", "+00:00")
                    )
                    print(
                        f"{i + 1:2d}. {timestamp.strftime('%H:%M:%S')} [{log.get('level', 'INFO'):5s}] {log.get('namespace', 'unknown'):20s} | {log.get('message', '')}"
                    )

                if len(session_data.get("logs", [])) > 20:
                    print(
                        f"... and {len(session_data.get('logs', [])) - 20} more log entries"
                    )
        else:
            print(f"❌ Session {session_id} not found")

    else:
        # Show recent sessions
        print("🔍 Querying recent sessions...")
        sessions = analyzer.get_session_logs(limit=5)

        if sessions:
            print(f"\n📊 RECENT SESSIONS ({len(sessions)} found)")
            print("=" * 70)

            for session_data in sessions:
                analysis = analyzer.analyze_session(session_data)
                print(
                    f"\n{analysis['session_id'][:8]}... | {analysis['duration_seconds']:6.1f}s | {analysis['total_logs']:3d} logs | {analysis['error_count']} errors | {len(analysis['agents_used'])} agents"
                )

            print(f"\n💡 Use 'python {sys.argv[0]} <session_id>' for detailed analysis")
            print(f"💡 Use 'python {sys.argv[0]} <session_id> --raw' to see raw logs")
        else:
            print("❌ No session logs found")


if __name__ == "__main__":
    main()
