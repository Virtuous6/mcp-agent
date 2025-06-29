# Session-Aggregated Logging

This document explains the new session-aggregated logging feature that combines all logs from a session into a single Supabase record instead of storing individual log entries.

## Overview

### Before (Individual Logging)

- Each log entry was stored as a separate record in `application_logs` table
- High database write volume (hundreds of individual INSERT operations per session)
- Difficult to analyze complete session data
- More expensive in terms of database operations

### After (Session Aggregation)

- All logs for a session are buffered in memory during execution
- Single record written to `session_logs` table when session completes
- Much lower database write volume (1 INSERT per session)
- Complete session data available in one record with rich metadata

## Database Schema

### New `session_logs` Table

```sql
CREATE TABLE session_logs (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    total_logs INTEGER NOT NULL DEFAULT 0,
    session_metadata JSONB DEFAULT '{}',
    logs JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Session Metadata Structure

```json
{
  "user_interactions": [
    {
      "user_id": "U123456",
      "timestamp": "2025-01-28T10:30:00Z",
      "message": "what are the columns for the table tools",
      "channel_id": "C789012"
    }
  ],
  "agents_used": ["airtable_manager", "data_researcher"],
  "tools_called": ["Get_Schema", "execute_sql", "brave_search"],
  "servers_connected": ["n8n", "supabase", "brave_search"],
  "error_count": 0,
  "warning_count": 2
}
```

### Logs Array Structure

```json
[
  {
    "level": "INFO",
    "timestamp": "2025-01-28T10:30:01Z",
    "namespace": "SlackMetaAgent",
    "message": "Processing Slack message: what are the columns...",
    "data": {},
    "user_id": "U123456",
    "channel_id": "C789012",
    "agent_name": "airtable_manager",
    "server_name": "n8n",
    "tool_name": "Get_Schema"
  }
]
```

## Usage

### Enable Session Aggregation

```python
# In main.py
session_id, supabase_handler = setup_supabase_logging(
    project_id=supabase_project_id,
    level="INFO",
    use_session_aggregation=True  # Enable session aggregation
)
```

### Disable Session Aggregation (Individual Logs)

```python
# For backward compatibility
session_id, supabase_handler = setup_supabase_logging(
    project_id=supabase_project_id,
    level="INFO",
    use_session_aggregation=False  # Use individual log records
)
```

### Session Finalization

Session logs are automatically finalized when the application shuts down:

```python
# Automatically called on shutdown
success = supabase_handler.finalize_session()
```

## Querying Session Logs

### Using the Query Script

```bash
# View recent sessions
python query_session_logs.py

# Analyze specific session
python query_session_logs.py 3fa64808-3b07-4b6f-88f2-efb55fcd5569

# View raw logs for a session
python query_session_logs.py 3fa64808-3b07-4b6f-88f2-efb55fcd5569 --raw
```

### Direct SQL Queries

#### Get Recent Sessions

```sql
SELECT
    session_id,
    start_time,
    end_time,
    total_logs,
    session_metadata->>'error_count' as errors,
    session_metadata->>'agents_used' as agents
FROM session_logs
ORDER BY start_time DESC
LIMIT 10;
```

#### Find Sessions with Errors

```sql
SELECT
    session_id,
    start_time,
    session_metadata->>'error_count' as error_count,
    session_metadata->>'agents_used' as agents_used
FROM session_logs
WHERE (session_metadata->>'error_count')::int > 0
ORDER BY start_time DESC;
```

#### Find Sessions Using Specific Agent

```sql
SELECT
    session_id,
    start_time,
    total_logs,
    session_metadata->>'agents_used' as agents
FROM session_logs
WHERE session_metadata->>'agents_used' LIKE '%airtable_manager%'
ORDER BY start_time DESC;
```

#### Analyze Log Levels in a Session

```sql
SELECT
    session_id,
    jsonb_array_elements(logs)->>'level' as log_level,
    COUNT(*) as count
FROM session_logs
WHERE session_id = '3fa64808-3b07-4b6f-88f2-efb55fcd5569'
GROUP BY session_id, log_level
ORDER BY count DESC;
```

#### Find User Interactions

```sql
SELECT
    session_id,
    start_time,
    jsonb_array_length(session_metadata->'user_interactions') as interaction_count,
    session_metadata->'user_interactions' as interactions
FROM session_logs
WHERE jsonb_array_length(session_metadata->'user_interactions') > 0
ORDER BY start_time DESC;
```

## Benefits

### Performance

- **Reduced Database Writes**: 1 write per session vs hundreds of individual writes
- **Better Query Performance**: Complete session data in single record
- **Lower Database Costs**: Fewer operations = lower costs

### Analysis

- **Session-Level Insights**: Easy to analyze complete sessions
- **Rich Metadata**: Automatically tracks agents, tools, errors, user interactions
- **Timeline Analysis**: All logs with timestamps for session flow analysis

### Operational

- **Simplified Monitoring**: One record per session to monitor
- **Better Debugging**: Complete session context in one place
- **Easier Reporting**: Session-level metrics readily available

## Migration

The new session aggregation system is backward compatible:

1. **New installations** use session aggregation by default
2. **Existing systems** can enable it by setting `use_session_aggregation=True`
3. **Both approaches** can coexist (the `SupabaseLogHandler` still supports individual logs)

## Example Session Analysis Output

```
🎯 SESSION ANALYSIS
==================================================
Session ID: 3fa64808-3b07-4b6f-88f2-efb55fcd5569
Duration: 45.2 seconds
Start: 2025-01-28T10:30:00+00:00
End: 2025-01-28T10:30:45+00:00

📊 LOG STATISTICS
Total Logs: 157
Log Levels: {
  "INFO": 145,
  "WARNING": 8,
  "ERROR": 4
}
Errors: 4
Warnings: 8

🤖 SYSTEM USAGE
Agents Used: airtable_manager, data_researcher
Tools Called: Get_Schema, execute_sql, brave_search, list_tools, get_capabilities
Servers Connected: n8n, supabase, brave_search

👥 USER INTERACTIONS
Total Interactions: 1

🔍 KEY EVENTS
  10:30:02 [INFO] Processing Slack message: what are the columns for the table tools...
  10:30:05 [INFO] Using airtable_manager agent for request
  10:30:08 [INFO] Stored conversation memory for user U09227EP6P3
  10:30:12 [ERROR] Tool call failed: connection timeout
  10:30:45 [INFO] Session logs saved to Supabase
```

This session aggregation approach provides much better visibility into the complete lifecycle of each interaction while being more efficient and cost-effective than individual log storage.
