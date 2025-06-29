"""
Intent analysis and routing for the Slack Meta-Agent system

This module handles the complex intent analysis logic that was previously
embedded in the monolithic SlackMetaAgent class.
"""

import logging
from typing import Dict, List, Optional, Any

from .pattern_matcher import PatternMatcher


class IntentAnalyzer:
    """Analyzes user intent and determines appropriate routing strategy"""

    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger("IntentAnalyzer")
        self.pattern_matcher = PatternMatcher(config)

    async def analyze_intent(
        self, message: str, context: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Analyze user intent and determine routing strategy

        Returns a routing decision with agent requirements and execution strategy
        """
        try:
            # Step 1: Check for high-priority patterns (MCP server addition, etc.)
            priority_intent = self._check_priority_patterns(message)
            if priority_intent:
                return priority_intent

            # Step 2: Dynamic pattern matching
            pattern_match = self.pattern_matcher.match_patterns(message)
            if pattern_match:
                return self._build_intent_from_pattern(pattern_match, message)

            # Step 3: Check for dynamic MCP discovery needs
            discovery_intent = self._check_discovery_needs(message)
            if discovery_intent:
                return discovery_intent

            # Step 4: LLM-based routing for complex cases
            return self._llm_based_routing(message, context)

        except Exception as e:
            self.logger.error(f"Intent analysis error: {e}")
            return self._fallback_intent(message)

    def _check_priority_patterns(self, message: str) -> Optional[Dict[str, Any]]:
        """Check for high-priority patterns that override normal routing"""
        message_lower = message.lower()

        # MCP server addition requests
        if self._is_mcp_server_addition(message_lower):
            return {
                "required_agents": ["mcp_server_manager"],
                "complexity": "simple",
                "execution_strategy": "single_agent",
                "priority": "high",
                "intent_name": "mcp_server_addition",
                "confidence": "high",
                "reasoning": "Detected MCP server addition request",
            }

        # Feedback collection requests
        if self._is_feedback_request(message_lower):
            return {
                "required_agents": ["feedback_collector"],
                "complexity": "simple",
                "execution_strategy": "single_agent",
                "priority": "high",
                "intent_name": "feedback_collection",
                "confidence": "high",
                "reasoning": "Detected feedback collection request",
            }

        return None

    def _is_mcp_server_addition(self, message_lower: str) -> bool:
        """Check if message is requesting MCP server addition"""
        addition_indicators = [
            "add mcp",
            "register mcp",
            "create mcp",
            "setup mcp",
            "add server",
            "register server",
            "new mcp server",
        ]
        return any(indicator in message_lower for indicator in addition_indicators)

    def _is_feedback_request(self, message_lower: str) -> bool:
        """Check if message is feedback-related"""
        feedback_indicators = [
            "feedback",
            "give feedback",
            "provide feedback",
            "suggestion",
            "bug report",
            "feature request",
            "improvement",
        ]
        return any(indicator in message_lower for indicator in feedback_indicators)

    def _build_intent_from_pattern(
        self, pattern_match: Dict[str, Any], message: str
    ) -> Dict[str, Any]:
        """Build intent response from pattern match"""
        return {
            "required_agents": [pattern_match["agent"]],
            "complexity": "simple",
            "execution_strategy": "single_agent",
            "priority": "high" if pattern_match["confidence"] > 0.85 else "medium",
            "intent_name": f"pattern_{pattern_match['pattern']}",
            "confidence": "high" if pattern_match["confidence"] > 0.85 else "medium",
            "reasoning": f"Pattern match to {pattern_match['agent']} with {pattern_match['confidence']:.2f} confidence",
            "pattern_confidence": pattern_match["confidence"],
            "matched_keywords": pattern_match.get("matched_keywords", []),
        }

    def _check_discovery_needs(self, message: str) -> Optional[Dict[str, Any]]:
        """Check if message requires dynamic MCP server discovery"""
        # Extract keywords that might indicate specific services
        keywords = self._extract_discovery_keywords(message)

        if not keywords:
            return None

        # Check if we need database discovery for these keywords
        needs_discovery = self._evaluate_discovery_necessity(keywords, message)

        if needs_discovery:
            return {
                "required_agents": ["data_researcher"],  # Can work with dynamic tools
                "complexity": "dynamic",
                "execution_strategy": "dynamic_discovery",
                "priority": "high",
                "intent_name": "dynamic_mcp_discovery",
                "confidence": "high",
                "reasoning": "Detected qualified service patterns requiring database discovery",
                "discovery_keywords": keywords,
            }

        return None

    def _extract_discovery_keywords(self, message: str) -> List[str]:
        """Extract keywords that might indicate need for MCP discovery"""
        import re

        keywords = []
        message_lower = message.lower()

        # Look for organization + service patterns (e.g., "ARC supabase")
        org_service_patterns = [
            r"\b([A-Z]{2,10})\s+(supabase|airtable|database|api)\b",
            r"\b([A-Z]{2,10})_(supabase|airtable|database|api)\b",
        ]

        for pattern in org_service_patterns:
            matches = re.findall(pattern, message, re.IGNORECASE)
            for match in matches:
                org, service = match
                keywords.extend(
                    [org.lower(), service.lower(), f"{org.lower()}_{service.lower()}"]
                )

        # Look for specific service names
        service_patterns = [
            r"\b(airtable|supabase|webhook|automation|workflow)\b",
        ]

        for pattern in service_patterns:
            matches = re.findall(pattern, message_lower)
            keywords.extend(matches)

        return list(set(keywords))  # Remove duplicates

    def _evaluate_discovery_necessity(self, keywords: List[str], message: str) -> bool:
        """Evaluate if discovery is necessary for these keywords"""
        # For now, simple heuristic - if we have compound keywords or
        # organization-specific qualifiers, likely need discovery
        compound_keywords = [k for k in keywords if "_" in k]
        has_org_patterns = any(len(k) <= 10 and k.isupper() for k in keywords)

        return len(compound_keywords) > 0 or has_org_patterns

    def _llm_based_routing(
        self, message: str, context: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Use LLM for complex routing decisions"""
        # This would use OpenAI to analyze the request
        # For now, provide a simplified implementation

        message_lower = message.lower()

        # Simple keyword-based fallback routing
        if any(
            word in message_lower for word in ["weather", "current", "news", "latest"]
        ):
            agent_type = "data_researcher"
        elif any(word in message_lower for word in ["what is", "who is", "define"]):
            agent_type = "knowledge_agent"
        elif any(word in message_lower for word in ["capabilities", "tools", "help"]):
            agent_type = "capability_inspector"
        else:
            agent_type = "data_researcher"  # Default

        return {
            "required_agents": [agent_type],
            "complexity": "simple",
            "execution_strategy": "single_agent",
            "priority": "medium",
            "intent_name": f"llm_routed_{agent_type}",
            "confidence": "medium",
            "reasoning": f"LLM-based routing selected {agent_type}",
        }

    def _fallback_intent(self, message: str) -> Dict[str, Any]:
        """Fallback intent when analysis fails"""
        return {
            "required_agents": ["knowledge_agent"],
            "complexity": "simple",
            "execution_strategy": "single_agent",
            "priority": "low",
            "intent_name": "fallback",
            "confidence": "low",
            "reasoning": "Fallback routing due to analysis error",
        }
