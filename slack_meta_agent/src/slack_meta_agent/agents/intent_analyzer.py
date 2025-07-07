"""
Intent Analyzer Agent - Handles all intent classification and routing logic.

This component is responsible for analyzing user messages and determining:
1. What the user wants to accomplish (intent classification)
2. Which agents should handle the request
3. What execution strategy to use
4. Confidence levels and metadata

Extracted from SlackMetaAgent to provide clean separation of concerns.
"""

import re
from typing import Dict, List, Optional, Any

from ..core.types import (
    Intent,
    IncomingMessage,
    ExecutionStrategy,
    ConfidenceLevel,
    PatternMatch,
    AgentComponent,
)


class IntentAnalyzerAgent(AgentComponent):
    """
    Analyzes user intent through multiple classification strategies:
    1. High-priority patterns (feedback, MCP server addition)
    2. Workflow triggers (database-driven)
    3. Dynamic pattern matching
    4. LLM-based fallback routing
    """

    def __init__(
        self,
        registry=None,
        tool_discovery=None,
        pool_manager=None,
        config: Dict[str, Any] = None,
    ):
        super().__init__("IntentAnalyzer")

        # Store injected dependencies
        self.registry = registry
        self.tool_discovery = tool_discovery
        self.pool_manager = pool_manager
        self.config = config or self._default_config()

        # Legacy pattern support (for backwards compatibility)
        self.patterns = self._load_default_patterns()

        # Workflow trigger interface (will be injected)
        self._workflow_checker = None

        # LLM router interface (will be injected)
        self._llm_router = None

    def set_workflow_checker(self, workflow_checker):
        """Inject workflow checker dependency."""
        self._workflow_checker = workflow_checker

    def set_llm_router(self, llm_router):
        """Inject LLM router dependency."""
        self._llm_router = llm_router

    async def analyze(self, message: IncomingMessage) -> Intent:
        """
        Analyze user intent with priority-ordered classification pipeline.

        Priority order:
        1. MCP server addition requests (highest priority)
        2. Feedback requests (high priority)
        3. Workflow triggers (database-driven)
        4. Organization-qualified service discovery
        5. Dynamic pattern matching
        6. LLM fallback routing
        """
        text = message.text
        context = message.context

        self.logger.info(f"🔍 Analyzing intent for: {text[:50]}...")

        # PRIORITY 1: MCP server addition requests
        if self._is_mcp_server_addition_request(text):
            self.logger.info("🔧 PRIORITY: MCP server addition detected")
            return Intent(
                name="dynamic_mcp_server_manager",
                required_agents=["mcp_server_manager"],
                execution_strategy=ExecutionStrategy.SINGLE_AGENT,
                confidence=ConfidenceLevel.HIGH,
                reasoning="Detected MCP server addition request",
                priority="high",
                estimated_tasks=1,
            )

        # PRIORITY 2: Feedback requests
        feedback_info = self._parse_feedback_from_message(text)
        if feedback_info["has_feedback"] or any(
            pattern in text.lower()
            for pattern in [
                "give feedback",
                "provide feedback",
                "share feedback",
                "my feedback",
                "here is feedback",
                "here's feedback",
            ]
        ):
            self.logger.info("💬 PRIORITY: Feedback detected")
            return Intent(
                name="dynamic_feedback_collector",
                required_agents=["feedback_collector"],
                execution_strategy=ExecutionStrategy.SINGLE_AGENT,
                confidence=ConfidenceLevel.HIGH,
                reasoning="Detected feedback collection request",
                priority="high",
                estimated_tasks=1,
                payload=feedback_info,
            )

        # PRIORITY 3: Workflow triggers (database-driven)
        if self._workflow_checker:
            workflow_match = await self._workflow_checker.check_triggers(text, context)
            if workflow_match:
                workflow = workflow_match["workflow"]
                self.logger.info(f"🔄 PRIORITY: Workflow triggered: {workflow['name']}")
                return Intent(
                    name=f"workflow_{workflow['name']}",
                    required_agents=[],
                    execution_strategy=ExecutionStrategy.WORKFLOW,
                    confidence=ConfidenceLevel.HIGH
                    if workflow_match["confidence"] > 0.85
                    else ConfidenceLevel.MEDIUM,
                    reasoning=f"Workflow trigger detected for {workflow['name']} (confidence: {workflow_match['confidence']:.2f})",
                    workflow=workflow,
                    priority="high",
                    estimated_tasks=len(
                        workflow.get("definition", {}).get("steps", [1])
                    ),
                )

        # PRIORITY 4: Organization-qualified service discovery
        discovery_keywords = self._extract_discovery_keywords_from_message(text)
        has_org_qualifier = any("_" in keyword for keyword in discovery_keywords)
        has_explicit_org_pattern = self._has_organization_service_pattern(text)

        if has_org_qualifier or has_explicit_org_pattern:
            self.logger.info(
                f"🎯 PRIORITY: Organization-qualified service detected: {discovery_keywords}"
            )
            return Intent(
                name="dynamic_discovery_qualified",
                required_agents=["data_researcher"],
                execution_strategy=ExecutionStrategy.DYNAMIC_DISCOVERY,
                confidence=ConfidenceLevel.HIGH,
                reasoning="Detected organization-qualified service requiring database server lookup",
                discovery_keywords=discovery_keywords,
                qualified_services=True,
                priority="high",
                estimated_tasks=1,
            )

        # PRIORITY 5: Dynamic pattern matching
        pattern_match = self._dynamic_pattern_match(text)
        if pattern_match:
            agent_type = pattern_match.agent
            confidence = pattern_match.confidence

            self.logger.info(
                f"⚡ Pattern match: {text[:50]}... -> {agent_type} (confidence: {confidence:.2f})"
            )
            return Intent(
                name=f"dynamic_{agent_type}",
                required_agents=[agent_type],
                execution_strategy=ExecutionStrategy.SINGLE_AGENT,
                confidence=ConfidenceLevel.HIGH
                if confidence > 0.85
                else ConfidenceLevel.MEDIUM,
                reasoning=f"Pattern-based routing to {agent_type} with {confidence:.2f} confidence",
                matched_keywords=pattern_match.matched_keywords,
                pattern_confidence=confidence,
                priority="high" if confidence > 0.85 else "medium",
                estimated_tasks=1,
            )

        # PRIORITY 6: General dynamic discovery (non-org-qualified)
        if (
            discovery_keywords
            and not has_org_qualifier
            and not has_explicit_org_pattern
        ):
            self.logger.info(f"🔍 Dynamic MCP discovery detected: {discovery_keywords}")
            return Intent(
                name="dynamic_discovery_general",
                required_agents=["data_researcher"],
                execution_strategy=ExecutionStrategy.DYNAMIC_DISCOVERY,
                confidence=ConfidenceLevel.HIGH,
                reasoning="Detected service patterns requiring database server discovery",
                discovery_keywords=discovery_keywords,
                qualified_services=False,
                priority="high",
                estimated_tasks=1,
            )

        # PRIORITY 7: LLM fallback routing
        if self._llm_router:
            self.logger.info(f"🤖 Using LLM routing for: {text[:50]}...")
            llm_intent = await self._llm_router.route(text, context)
            if llm_intent:
                return llm_intent

        # Final fallback
        return await self._analyze_user_intent_fallback(text, context)

    def _is_mcp_server_addition_request(self, message: str) -> bool:
        """Check if the message is requesting to add a new MCP server."""
        message_lower = message.lower()

        # Check for feedback context first - if this is clearly feedback,
        # don't treat "add" phrases as server addition requests
        feedback_indicators = [
            "i have feedback",
            "give feedback",
            "providing feedback",
            "my feedback",
            "here's feedback",
            "feedback:",
            "suggestion:",
            "feature request:",
        ]

        has_feedback_context = any(
            indicator in message_lower for indicator in feedback_indicators
        )
        if has_feedback_context:
            self.logger.debug(
                "💬 Feedback context detected - not treating as MCP server addition"
            )
            return False

        # Strong indicators for addition requests
        addition_indicators = [
            "add mcp",
            "add new mcp",
            "register mcp",
            "create mcp",
            "setup mcp",
            "configure mcp",
            "install mcp",
            "connect new mcp",
            "add server",
            "register server",
            "create server",
            "setup server",
            "new mcp server",
            "add mcp server",
            "i'd like to add",
            "i want to add",
            "i need to add",
            "i have an mcp server",
        ]

        has_addition_indicator = any(
            indicator in message_lower for indicator in addition_indicators
        )

        # Action + server patterns
        action_server_patterns = [
            r"\b(add|register|create|setup|configure|install|connect)\s+.{0,10}mcp",
            r"\b(add|register|create|setup|configure|install|connect)\s+.{0,10}server",
            r"new\s+mcp\s+server",
            r"mcp\s+server\s+called",
            r"mcp\s+server\s+named",
        ]

        has_action_pattern = any(
            re.search(pattern, message_lower) for pattern in action_server_patterns
        )

        # Exclude discovery patterns
        discovery_exclusions = [
            "explore mcp",
            "test mcp",
            "discover mcp",
            "find mcp",
            "list mcp",
            "show mcp",
            "what mcp",
            "which mcp",
            "available mcp",
        ]

        has_discovery_pattern = any(
            exclusion in message_lower for exclusion in discovery_exclusions
        )

        # Additional exclusions for feedback/suggestion contexts
        feedback_exclusions = [
            "suggest",
            "suggestion",
            "would be nice",
            "please add",
            "should add",
            "could add",
            "you add",
            "can you add",
        ]

        has_feedback_exclusion = any(
            exclusion in message_lower for exclusion in feedback_exclusions
        )

        result = (
            (has_addition_indicator or has_action_pattern)
            and not has_discovery_pattern
            and not has_feedback_exclusion
        )

        if result:
            self.logger.info(
                f"🔧 Detected MCP server addition request: '{message[:50]}...'"
            )

        return result

    def _parse_feedback_from_message(self, message: str) -> Dict[str, Any]:
        """Parse feedback information from the user's message."""
        message_lower = message.lower().strip()

        # Direct feedback patterns
        direct_feedback_patterns = [
            r"here\s+is\s+feedback[:\s]*(.+)",
            r"here's\s+feedback[:\s]*(.+)",
            r"my\s+feedback\s+is[:\s]*(.+)",
            r"feedback[:\s]*(.+)",
            r"i\s+have\s+feedback[\s\-:]*(.+)",
            r"i\s+think\s+(.+)",
            r"suggestion[:\s]*(.+)",
            r"improvement[:\s]*(.+)",
            r"issue\s+with[:\s]*(.+)",
            r"problem\s+with[:\s]*(.+)",
            r"bug\s+report[:\s]*(.+)",
            r"feature\s+request[:\s]*(.+)",
        ]

        # Intent-only patterns
        intent_only_patterns = [
            r"i'd?\s+like\s+to\s+give\s+feedback",
            r"i\s+want\s+to\s+give\s+feedback",
            r"give\s+feedback",
            r"provide\s+feedback",
            r"share\s+feedback",
            r"can\s+i\s+give\s+feedback",
        ]

        feedback_info = {
            "has_feedback": False,
            "feedback_text": "",
            "category": "general",
        }

        # Check for direct feedback first
        for pattern in direct_feedback_patterns:
            match = re.search(pattern, message_lower, re.IGNORECASE | re.DOTALL)
            if match:
                feedback_text = match.group(1).strip()
                if len(feedback_text) > 10:  # Ensure substantial feedback
                    feedback_info["has_feedback"] = True
                    feedback_info["feedback_text"] = feedback_text
                    feedback_info["category"] = self._categorize_feedback(feedback_text)
                    return feedback_info

        # Check if they want to give feedback
        for pattern in intent_only_patterns:
            if re.search(pattern, message_lower):
                feedback_info["has_feedback"] = False
                return feedback_info

        # If message contains feedback keywords but no clear pattern, treat as direct feedback
        feedback_keywords = [
            "feedback",
            "suggestion",
            "improvement",
            "issue",
            "problem",
            "bug",
            "feature",
        ]
        if (
            any(keyword in message_lower for keyword in feedback_keywords)
            and len(message.strip()) > 20
        ):
            feedback_info["has_feedback"] = True
            feedback_info["feedback_text"] = message.strip()
            feedback_info["category"] = self._categorize_feedback(message)

        return feedback_info

    def _categorize_feedback(self, feedback_text: str) -> str:
        """Automatically categorize feedback based on content."""
        feedback_lower = feedback_text.lower()

        # Bug reports
        if any(
            word in feedback_lower
            for word in [
                "bug",
                "error",
                "broken",
                "crash",
                "not working",
                "issue",
                "problem",
            ]
        ):
            return "bug_report"

        # Feature requests
        if any(
            word in feedback_lower
            for word in [
                "feature",
                "add",
                "new",
                "would like",
                "wish",
                "could you",
                "request",
            ]
        ):
            return "feature_request"

        # Improvements
        if any(
            word in feedback_lower
            for word in [
                "improve",
                "better",
                "enhance",
                "upgrade",
                "optimize",
                "suggestion",
            ]
        ):
            return "improvement"

        # Performance issues
        if any(
            word in feedback_lower
            for word in ["slow", "fast", "performance", "speed", "lag", "delay"]
        ):
            return "performance"

        # User experience
        if any(
            word in feedback_lower
            for word in [
                "confusing",
                "unclear",
                "difficult",
                "easy",
                "user",
                "interface",
                "ux",
            ]
        ):
            return "user_experience"

        # Positive feedback
        if any(
            word in feedback_lower
            for word in [
                "good",
                "great",
                "love",
                "excellent",
                "awesome",
                "thank",
                "helpful",
            ]
        ):
            return "positive"

        return "general"

    def _dynamic_pattern_match(self, message: str) -> Optional[PatternMatch]:
        """Advanced dynamic pattern matching with adaptive confidence."""
        message_lower = message.lower()

        # Avoid pattern matching for organization-qualified requests
        if self._has_organization_service_pattern(message):
            return None

        best_match = None
        best_confidence = 0.0

        # Dynamic confidence threshold
        base_threshold = self.config["pattern_confidence_threshold"]
        adaptive_threshold = self._get_adaptive_threshold(base_threshold)

        for pattern_name, pattern_info in self.patterns.items():
            # Multi-level confidence calculation
            confidence_scores = self._calculate_multi_level_confidence(
                message, message_lower, pattern_info, pattern_name
            )

            max_confidence = max(confidence_scores.values())

            if (
                max_confidence > best_confidence
                and max_confidence >= adaptive_threshold
            ):
                best_confidence = max_confidence
                best_match = PatternMatch(
                    pattern_name=pattern_name,
                    agent=pattern_info["agent"],
                    confidence=max_confidence,
                    matched_keywords=confidence_scores.get("keyword_matches", []),
                    match_strategy=max(confidence_scores, key=confidence_scores.get),
                    all_scores=confidence_scores,
                    threshold_used=adaptive_threshold,
                )

        if best_match:
            self._update_pattern_success(
                best_match.pattern_name, message, best_match.confidence
            )

        return best_match

    def _calculate_multi_level_confidence(
        self, message: str, message_lower: str, pattern_info: Dict, pattern_name: str
    ) -> Dict[str, float]:
        """Calculate confidence using multiple matching strategies."""
        scores = {}

        # 1. Exact keyword matching
        scores["exact_keywords"] = self._calculate_exact_keyword_confidence(
            message_lower, pattern_info
        )

        # 2. Fuzzy/partial keyword matching
        scores["fuzzy_keywords"] = self._calculate_fuzzy_keyword_confidence(
            message_lower, pattern_info
        )

        # 3. Contextual pattern matching
        scores["contextual"] = self._calculate_contextual_confidence(
            message, message_lower, pattern_info, pattern_name
        )

        # 4. Semantic structure analysis
        scores["semantic"] = self._calculate_semantic_confidence(
            message, pattern_info, pattern_name
        )

        return scores

    def _calculate_exact_keyword_confidence(
        self, message_lower: str, pattern_info: Dict
    ) -> float:
        """Improved exact keyword matching with better scoring."""
        keyword_matches = []
        for keyword in pattern_info["keywords"]:
            if keyword in message_lower:
                keyword_matches.append(keyword)

        if not keyword_matches:
            return 0.0

        total_keywords = len(pattern_info["keywords"])

        if total_keywords <= 5:
            match_ratio = len(keyword_matches) / total_keywords
        else:
            max_useful_matches = min(5, total_keywords)
            effective_matches = min(len(keyword_matches), max_useful_matches)
            match_ratio = effective_matches / max_useful_matches

        # Usage boost based on historical success
        usage_boost = min(1.3, 1.0 + (pattern_info["usage_count"] / 50))

        # Keyword length bonus
        length_bonus = 1.0 + sum(
            0.1 for keyword in keyword_matches if len(keyword) > 10
        )

        confidence = (
            pattern_info["confidence"] * match_ratio * usage_boost * length_bonus
        )
        return min(confidence, 1.0)

    def _calculate_fuzzy_keyword_confidence(
        self, message_lower: str, pattern_info: Dict
    ) -> float:
        """Fuzzy matching for partial keyword matches."""
        fuzzy_matches = []

        # Common abbreviations mapping
        abbreviations = {
            "temp": ["temperature"],
            "temps": ["temperature"],
            "feedback": ["comment", "suggestion", "opinion", "review"],
            "capabilities": ["tools", "features", "functions", "help"],
        }

        for keyword in pattern_info["keywords"]:
            if len(keyword) > 3:
                keyword_parts = keyword.replace("_", " ").split()

                if len(keyword_parts) > 1:
                    # Multi-word keyword: check if most words are present
                    found_parts = sum(
                        1 for part in keyword_parts if part in message_lower
                    )
                    if found_parts >= len(keyword_parts) * 0.6:
                        fuzzy_matches.append(keyword)
                else:
                    # Single word: check for partial matches and abbreviations
                    if len(keyword) > 6:
                        for word in message_lower.split():
                            if keyword in word or word in keyword:
                                if abs(len(keyword) - len(word)) <= 3:
                                    fuzzy_matches.append(keyword)
                                    break

                            # Check abbreviation mappings
                            if word in abbreviations:
                                if keyword in abbreviations[word]:
                                    fuzzy_matches.append(keyword)
                                    break

        if not fuzzy_matches:
            return 0.0

        fuzzy_ratio = len(fuzzy_matches) / len(pattern_info["keywords"])
        fuzzy_confidence = pattern_info["confidence"] * fuzzy_ratio * 0.7

        return min(fuzzy_confidence, 0.8)

    def _calculate_contextual_confidence(
        self, message: str, message_lower: str, pattern_info: Dict, pattern_name: str
    ) -> float:
        """Analyze message context and structure for pattern matching."""
        contextual_score = 0.0

        # Check for intent indicators specific to each pattern
        if pattern_name == "feedback":
            contextual_indicators = [
                ("feedback", 0.9),
                ("give feedback", 0.95),
                ("my feedback", 0.9),
                ("here's feedback", 0.95),
                ("suggestion", 0.7),
                ("improvement", 0.6),
                ("issue with", 0.7),
                ("problem with", 0.7),
                ("bug", 0.8),
                ("feature request", 0.8),
                ("can you add", 0.5),
                ("would be nice", 0.6),
                ("is slow", 0.7),
                ("doesn't work", 0.8),
                ("not working", 0.8),
            ]

            for indicator, weight in contextual_indicators:
                if indicator in message_lower:
                    contextual_score = max(contextual_score, weight)

        elif pattern_name == "weather":
            weather_contexts = [
                ("what's the weather", 0.95),
                ("weather in", 0.9),
                ("temperature in", 0.8),
                ("forecast for", 0.85),
                ("how's the weather", 0.9),
            ]

            for context, weight in weather_contexts:
                if context in message_lower:
                    contextual_score = max(contextual_score, weight)

        elif pattern_name == "capabilities":
            capability_contexts = [
                ("what can you", 0.95),
                ("what do you", 0.9),
                ("what tools", 0.95),
                ("help me", 0.7),
                ("how do you", 0.8),
            ]

            for context, weight in capability_contexts:
                if context in message_lower:
                    contextual_score = max(contextual_score, weight)

        # Question pattern boost for info requests
        question_indicators = ["what", "how", "can", "could", "would", "?"]
        is_question = any(
            indicator in message_lower for indicator in question_indicators
        )

        if is_question and pattern_name in ["capabilities", "knowledge"]:
            contextual_score *= 1.2

        return min(contextual_score, 1.0)

    def _calculate_semantic_confidence(
        self, message: str, pattern_info: Dict, pattern_name: str
    ) -> float:
        """Analyze semantic structure and intent."""
        semantic_score = 0.0
        words = message.lower().split()

        if pattern_name == "feedback":
            feedback_verbs = [
                "is",
                "was",
                "works",
                "doesn't",
                "need",
                "want",
                "think",
                "feel",
            ]
            feedback_adjectives = [
                "good",
                "bad",
                "great",
                "terrible",
                "slow",
                "fast",
                "confusing",
                "helpful",
            ]

            has_feedback_verb = any(verb in words for verb in feedback_verbs)
            has_feedback_adjective = any(adj in words for adj in feedback_adjectives)

            if has_feedback_verb and has_feedback_adjective:
                semantic_score = 0.6
            elif has_feedback_verb or has_feedback_adjective:
                semantic_score = 0.3

        elif pattern_name == "weather":
            has_location_words = any(
                word in words for word in ["in", "at", "for", "around"]
            )
            has_time_words = any(
                word in words for word in ["today", "tomorrow", "now", "currently"]
            )

            if has_location_words or has_time_words:
                semantic_score = 0.4

        # Message length and complexity analysis
        if len(words) > 3:
            semantic_score *= 1.1

        return min(semantic_score, 0.8)

    def _extract_discovery_keywords_from_message(self, message: str) -> List[str]:
        """Extract keywords that might indicate need for dynamic MCP server discovery."""
        message_lower = message.lower()
        keywords = []

        # Extract organization-specific qualifiers (like "ARC supabase")
        org_service_patterns = [
            r"\b(?:our\s+)?([A-Z]{2,10})\s+(supabase|database|db)\b",
            r"\b(?:our\s+)?([A-Z]{2,10})\s+(airtable|air table)\b",
            r"\b(?:our\s+)?([A-Z]{2,10})\s+(n8n|automation)\b",
            r"\b(?:our\s+)?([A-Z]{2,10})\s+(api|webhook|integration)\b",
            r"\b(?:our\s+)?([A-Z]{2,10})\s+(server|service)\b",
        ]

        for pattern in org_service_patterns:
            matches = re.findall(pattern, message, re.IGNORECASE)
            for match in matches:
                org_name = match[0].upper()
                service_type = match[1].lower()

                keywords.extend([org_name.lower(), service_type])
                keywords.append(f"{org_name.lower()}_{service_type}")

        # Extract specific service/tool names
        service_patterns = [
            r"\b(airtable|air table)\b",
            r"\b(workflow|work flow)\b",
            r"\b(webhook|web hook)\b",
            r"\b(automation|automate)\b",
            r"\b(integration|integrate)\b",
            r"\b(zapier)\b",
            r"\b(sync|synchronize)\b",
            r"\b(trigger)\b",
            r"\b(gmail|email)\b",
            r"\b(calendar)\b",
            r"\b(notion)\b",
            r"\b(slack)\b",
            r"\b(discord)\b",
            r"\b(teams)\b",
            r"\b(api)\b",
            r"\b(database|db|supabase)\b",
            r"\b(crm)\b",
        ]

        for pattern in service_patterns:
            matches = re.findall(pattern, message_lower)
            keywords.extend(matches)

        # Extract organization names (all-caps words)
        org_patterns = [r"\b([A-Z]{2,10})\b"]

        for pattern in org_patterns:
            matches = re.findall(pattern, message)
            for match in matches:
                # Only add if not a common English word
                common_words = {
                    "THE",
                    "AND",
                    "FOR",
                    "ARE",
                    "BUT",
                    "NOT",
                    "YOU",
                    "ALL",
                    "CAN",
                    "HAD",
                    "HER",
                    "WAS",
                    "ONE",
                    "OUR",
                    "OUT",
                    "DAY",
                    "GET",
                    "USE",
                }
                if match.lower() not in [w.lower() for w in common_words]:
                    keywords.append(match.lower())

        # Remove duplicates while preserving order
        unique_keywords = []
        for keyword in keywords:
            if keyword not in unique_keywords:
                unique_keywords.append(keyword)

        return unique_keywords

    def _has_organization_service_pattern(self, message: str) -> bool:
        """Check if message has organization + service patterns."""
        org_service_patterns = [
            r"\b(?:our\s+)?([A-Z]{2,10})\s+(supabase|database|db|airtable|n8n|api|webhook|integration|server|service)\b"
        ]
        return any(
            re.search(pattern, message, re.IGNORECASE)
            for pattern in org_service_patterns
        )

    def _get_adaptive_threshold(self, base_threshold: float) -> float:
        """Calculate adaptive confidence threshold based on pattern performance."""
        adaptive_threshold = base_threshold * 0.75

        # Analyze recent pattern matching success rates
        total_patterns = len(self.patterns)
        if total_patterns > 0:
            total_usage = sum(p["usage_count"] for p in self.patterns.values())
            avg_usage = total_usage / total_patterns

            if avg_usage > 5:
                adaptive_threshold *= 0.85
            elif avg_usage < 1:
                adaptive_threshold *= 1.05

        return max(0.4, min(0.85, adaptive_threshold))

    def _update_pattern_success(
        self, pattern_name: str, message: str, confidence: float
    ):
        """Update pattern based on successful match."""
        if pattern_name in self.patterns:
            self.patterns[pattern_name]["usage_count"] += 1

            # Gradually improve confidence for frequently used patterns
            usage_count = self.patterns[pattern_name]["usage_count"]
            if usage_count > 5:
                current_confidence = self.patterns[pattern_name]["confidence"]
                confidence_boost = min(0.05, usage_count * 0.001)
                self.patterns[pattern_name]["confidence"] = min(
                    1.0, current_confidence + confidence_boost
                )

    async def _analyze_user_intent_fallback(self, message: str, context) -> Intent:
        """Fallback intent analysis if structured system fails."""
        message_lower = message.lower()

        # Quick pattern matching as fallback
        if any(
            word in message_lower
            for word in ["feedback", "suggestion", "bug report", "feature request"]
        ):
            agent_type = "feedback_collector"
        elif any(
            word in message_lower
            for word in ["weather", "current", "today", "now", "latest"]
        ):
            agent_type = "data_researcher"
        elif any(
            word in message_lower for word in ["what is", "who is", "define", "explain"]
        ):
            agent_type = "knowledge_agent"
        elif any(
            word in message_lower
            for word in ["dashboard", "financial", "revenue", "analysis"]
        ):
            agent_type = "financial_analyst"
        elif any(
            word in message_lower
            for word in ["code", "deploy", "develop", "build", "app"]
        ):
            agent_type = "code_developer"
        elif any(
            word in message_lower
            for word in ["airtable", "air table", "base", "records"]
        ):
            agent_type = "airtable_manager"
        else:
            agent_type = "data_researcher"

        return Intent(
            name="fallback",
            required_agents=[agent_type],
            execution_strategy=ExecutionStrategy.SINGLE_AGENT,
            confidence=ConfidenceLevel.LOW,
            reasoning=f"Fallback logic selected {agent_type}",
            priority="medium",
            estimated_tasks=1,
        )

    def _load_default_patterns(self) -> Dict[str, Dict]:
        """Load default patterns for pattern matching."""
        return {
            "weather": {
                "keywords": [
                    "weather",
                    "temperature",
                    "forecast",
                    "climate",
                    "temp",
                    "rain",
                    "sunny",
                    "cloudy",
                ],
                "agent": "data_researcher",
                "confidence": 0.9,
                "usage_count": 0,
            },
            "knowledge": {
                "keywords": [
                    "what is",
                    "who is",
                    "what does",
                    "define",
                    "explain",
                    "capital of",
                    "meaning of",
                ],
                "agent": "knowledge_agent",
                "confidence": 0.85,
                "usage_count": 0,
            },
            "capabilities": {
                "keywords": [
                    "what tools",
                    "what can you",
                    "capabilities",
                    "what do you have access",
                    "help",
                    "commands",
                ],
                "agent": "capability_inspector",
                "confidence": 0.9,
                "usage_count": 0,
            },
            "financial": {
                "keywords": [
                    "dashboard",
                    "revenue",
                    "financial",
                    "profit",
                    "metrics",
                    "analytics",
                    "sales",
                    "kpi",
                ],
                "agent": "financial_analyst",
                "confidence": 0.8,
                "usage_count": 0,
            },
            "development": {
                "keywords": [
                    "code",
                    "deploy",
                    "develop",
                    "build",
                    "app",
                    "website",
                    "programming",
                    "github",
                ],
                "agent": "code_developer",
                "confidence": 0.8,
                "usage_count": 0,
            },
            "automation": {
                "keywords": [
                    "workflow",
                    "automate",
                    "trigger",
                    "automation",
                    "zapier",
                    "airtable workflow",
                ],
                "agent": "automation_specialist",
                "confidence": 0.85,
                "usage_count": 0,
            },
            "airtable": {
                "keywords": [
                    "airtable",
                    "air table",
                    "airtable base",
                    "airtable records",
                    "airtable data",
                ],
                "agent": "airtable_manager",
                "confidence": 0.9,
                "usage_count": 0,
            },
            "feedback": {
                "keywords": [
                    "feedback",
                    "give feedback",
                    "suggestion",
                    "improvement",
                    "issue with",
                    "bug report",
                ],
                "agent": "feedback_collector",
                "confidence": 0.90,
                "usage_count": 0,
            },
        }

    def _default_config(self) -> Dict[str, Any]:
        """Default configuration for the intent analyzer."""
        return {
            "pattern_confidence_threshold": 0.8,
            "cache_ttl_seconds": 1800,
            "enable_llm_fallback": True,
            "enable_workflow_triggers": True,
            "enable_dynamic_discovery": True,
        }

    async def health_check(self) -> Dict[str, Any]:
        """Check component health."""
        return {
            "status": "healthy",
            "component": self.name,
            "patterns_loaded": len(self.patterns),
            "workflow_checker": self._workflow_checker is not None,
            "llm_router": self._llm_router is not None,
        }
