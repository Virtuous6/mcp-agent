#!/usr/bin/env python3
"""
Supabase Direct Client - Backward Compatibility Wrapper

This file maintains backward compatibility by importing the SupabaseDirectClient
from its refactored location.

The actual implementation has been moved to src/slack_meta_agent/database/supabase_client.py
"""

# Import from the refactored location
from src.slack_meta_agent.database.supabase_client import SupabaseDirectClient

# Re-export for backward compatibility
__all__ = ["SupabaseDirectClient"]
