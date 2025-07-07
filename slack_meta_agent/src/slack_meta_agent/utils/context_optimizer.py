"""
Context optimization utilities for managing token limits and large responses.
"""

import re
import json
from typing import Dict, List, Any, Optional, Tuple
import logging


class ContextOptimizer:
    """Optimizes context to prevent token limit issues."""

    def __init__(self, max_tokens: int = 100000):
        self.max_tokens = max_tokens
        self.logger = logging.getLogger(__name__)

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimation (1 token ≈ 4 characters for English)."""
        return len(text) // 4

    def truncate_large_responses(self, content: str, max_tokens: int = 10000) -> str:
        """Intelligently truncate large responses while preserving structure."""

        if self.estimate_tokens(content) <= max_tokens:
            return content

        self.logger.warning(
            f"Truncating large response ({self.estimate_tokens(content)} tokens)"
        )

        # Try to parse as JSON and truncate arrays/objects
        try:
            data = json.loads(content)
            return self._truncate_json_data(data, max_tokens)
        except (json.JSONDecodeError, TypeError):
            pass

        # Handle table-like data
        if "|" in content and "\n" in content:
            return self._truncate_table_data(content, max_tokens)

        # Handle list-like data
        if content.count("\n") > 50:  # Many lines
            return self._truncate_list_data(content, max_tokens)

        # Simple text truncation
        max_chars = max_tokens * 4
        if len(content) > max_chars:
            return content[:max_chars] + "\n\n... [Response truncated due to length]"

        return content

    def _truncate_json_data(self, data: Any, max_tokens: int) -> str:
        """Truncate JSON data intelligently."""
        if isinstance(data, list):
            # Show first few items and indicate truncation
            truncated_items = []
            token_count = 0

            for item in data[:20]:  # Limit to first 20 items
                item_str = json.dumps(item, indent=2)
                item_tokens = self.estimate_tokens(item_str)

                if token_count + item_tokens > max_tokens:
                    break

                truncated_items.append(item)
                token_count += item_tokens

            result = {
                "data": truncated_items,
                "truncated": len(data) > len(truncated_items),
                "total_items": len(data),
                "showing": len(truncated_items),
            }

            return json.dumps(result, indent=2)

        elif isinstance(data, dict):
            # Truncate large dictionary values
            truncated = {}
            for key, value in data.items():
                if isinstance(value, (list, dict)):
                    truncated[key] = self._truncate_json_data(value, max_tokens // 4)
                else:
                    truncated[key] = value

            return json.dumps(truncated, indent=2)

        return json.dumps(data, indent=2)

    def _truncate_table_data(self, content: str, max_tokens: int) -> str:
        """Truncate table-like data."""
        lines = content.split("\n")

        # Keep header lines and first several data rows
        header_lines = []
        data_lines = []

        for line in lines:
            if (
                "---" in line
                or line.startswith("|")
                and ("id" in line.lower() or "name" in line.lower())
            ):
                header_lines.append(line)
            else:
                data_lines.append(line)

        # Keep first 10 data rows
        max_rows = 10
        truncated_lines = header_lines + data_lines[:max_rows]

        if len(data_lines) > max_rows:
            truncated_lines.append(
                f"... [Showing {max_rows} of {len(data_lines)} rows]"
            )

        return "\n".join(truncated_lines)

    def _truncate_list_data(self, content: str, max_tokens: int) -> str:
        """Truncate list-like data."""
        lines = content.split("\n")

        # Keep first portion of lines
        max_lines = min(30, max_tokens // 20)  # Rough estimate

        if len(lines) > max_lines:
            truncated_lines = lines[:max_lines]
            truncated_lines.append(f"\n... [Showing {max_lines} of {len(lines)} lines]")
            return "\n".join(truncated_lines)

        return content

    def optimize_tool_results(
        self, tool_results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Optimize tool results to prevent context overflow."""
        optimized_results = []
        total_tokens = 0

        for result in tool_results:
            # Estimate current tokens
            result_str = json.dumps(result, indent=2)
            result_tokens = self.estimate_tokens(result_str)

            # If this result would push us over the limit, truncate it
            if total_tokens + result_tokens > self.max_tokens:
                self.logger.warning(f"Truncating tool result to prevent overflow")

                # Truncate the content field if it exists
                if "content" in result:
                    result["content"] = self.truncate_large_responses(
                        result["content"],
                        max_tokens=self.max_tokens
                        - total_tokens
                        - 1000,  # Leave buffer
                    )

                # Add truncation indicator
                result["_truncated"] = True

            optimized_results.append(result)
            total_tokens += self.estimate_tokens(json.dumps(result, indent=2))

            # Hard stop if we're getting too big
            if total_tokens > self.max_tokens:
                break

        return optimized_results

    def create_summary_for_large_data(
        self, content: str, data_type: str = "data"
    ) -> str:
        """Create a summary for large datasets instead of including full content."""

        lines = content.split("\n")

        summary = f"📊 **{data_type.title()} Summary**\n\n"

        # Count different types of content
        if "|" in content:  # Table data
            header_lines = [
                l
                for l in lines
                if "|" in l and ("id" in l.lower() or "name" in l.lower())
            ]
            data_lines = [
                l
                for l in lines
                if "|" in l and l not in header_lines and not "---" in l
            ]

            summary += f"🔢 **Table Data**: {len(data_lines)} rows found\n"

            if header_lines:
                summary += f"📋 **Columns**: {header_lines[0]}\n"

            if data_lines:
                summary += f"🔍 **Sample Row**: {data_lines[0]}\n"

        else:
            summary += f"📄 **Content**: {len(lines)} lines of data\n"
            summary += f"📏 **Size**: ~{self.estimate_tokens(content)} tokens\n"

        summary += "\n💡 *Use more specific queries to see detailed data*"

        return summary


# Global instance
context_optimizer = ContextOptimizer()
