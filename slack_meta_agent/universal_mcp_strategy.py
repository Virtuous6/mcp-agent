#!/usr/bin/env python3
"""
Universal MCP Server Strategy
A systematic approach to exploring and extracting data from any unknown MCP server

This answers the question: "How can we test MCP servers when we don't know them
and pull valuable data?" by providing a systematic exploration framework.
"""

import asyncio
import json
from typing import Dict, List, Any, Tuple


async def explore_any_mcp_server(server_config: Dict) -> Dict[str, Any]:
    """
    Universal MCP server exploration strategy that adapts to any unknown server

    🎯 THE ADAPTATION PROCESS:
    1. Schema Analysis → Understand required parameters
    2. Error Learning → Learn from failures to adapt
    3. Pattern Testing → Try common parameter combinations
    4. Data Extraction → Extract valuable information
    """
    from mcp_agent.app import MCPApp
    from mcp_agent.config import get_settings, MCPServerSettings
    from mcp_agent.agents.agent import Agent

    print(f"🎯 EXPLORING: {server_config.get('server_name', 'Unknown Server')}")
    print("=" * 80)

    base_settings = get_settings("slack_meta_agent/mcp_agent.config.yaml")
    app_instance = MCPApp(name="universal_explorer", settings=base_settings)

    results = {
        "server_info": server_config,
        "tools_discovered": [],
        "working_patterns": [],
        "data_extracted": [],
        "recommendations": [],
    }

    async with app_instance.run() as agent_app:
        # Configure server dynamically
        dynamic_config = MCPServerSettings(
            name=server_config.get("display_name", "Universal Server"),
            description=server_config.get("description", "Server being explored"),
            transport=server_config.get("transport", "sse"),
            url=server_config.get("url"),
            command=server_config.get("command"),
            args=server_config.get("args", []),
            terminate_on_close=True,
        )

        agent_app.context.server_registry.registry["universal_server"] = dynamic_config

        try:
            agent = Agent(
                name="universal_explorer",
                instruction="Systematically explore unknown MCP server",
                server_names=["universal_server"],
                context=agent_app.context,
            )

            async with agent:
                print("✅ Connected to server")

                # STEP 1: Schema Analysis
                tools = await discover_and_analyze_tools(agent)
                results["tools_discovered"] = tools

                # STEP 2: Progressive Parameter Testing
                for tool in tools:
                    print(f"\n🔧 EXPLORING: {tool['name']}")
                    working_patterns, data_samples = await test_tool_comprehensively(
                        agent, tool
                    )

                    results["working_patterns"].extend(working_patterns)
                    results["data_extracted"].extend(data_samples)

                # STEP 3: Generate Actionable Recommendations
                results["recommendations"] = generate_recommendations(results)

                return results

        except Exception as e:
            print(f"❌ Exploration failed: {e}")
            results["error"] = str(e)
            return results

        finally:
            if "universal_server" in agent_app.context.server_registry.registry:
                del agent_app.context.server_registry.registry["universal_server"]


async def discover_and_analyze_tools(agent) -> List[Dict]:
    """STEP 1: Schema Analysis - Understand what tools exist and their requirements"""
    try:
        tools_result = await agent.list_tools("universal_server")
        tools = []

        if tools_result:
            for tool in tools_result.tools:
                schema = getattr(tool, "inputSchema", {})
                properties = schema.get("properties", {})
                required = schema.get("required", [])

                tool_info = {
                    "name": tool.name,
                    "description": tool.description or "No description",
                    "schema": schema,
                    "required_params": required,
                    "optional_params": [
                        p for p in properties.keys() if p not in required
                    ],
                    "parameter_types": {
                        k: v.get("type", "unknown") for k, v in properties.items()
                    },
                }
                tools.append(tool_info)

                print(f"   📌 {tool.name}")
                print(f"      Required: {required}")
                print(f"      Optional: {tool_info['optional_params']}")

        print(f"✅ Discovered {len(tools)} tools")
        return tools

    except Exception as e:
        print(f"❌ Tool discovery failed: {e}")
        return []


async def test_tool_comprehensively(agent, tool: Dict) -> Tuple[List[Dict], List[Any]]:
    """STEP 2: Progressive Parameter Testing - Systematically test parameter combinations"""
    working_patterns = []
    data_samples = []
    error_patterns = []

    # Strategy 1: Required Parameter Testing
    if tool["required_params"]:
        print(f"      🔍 Testing required parameters...")
        patterns = generate_smart_parameter_combinations(tool["required_params"])

        for params in patterns:
            success, data, error = await test_single_call(agent, tool["name"], params)
            if success and data:
                working_patterns.append({"params": params, "data": data})
                data_samples.append(data)
                print(f"         ✅ SUCCESS: {params}")
            elif error:
                error_patterns.append({"params": params, "error": error})

    # Strategy 2: Common Pattern Testing
    print(f"      🔍 Testing common patterns...")
    common_patterns = [
        {},
        {"all": True},
        {"limit": 5},
        {"Limit": 5},
        {"table": "users"},
        {"table": "data"},
        {"id": 1},
    ]

    for params in common_patterns:
        success, data, error = await test_single_call(agent, tool["name"], params)
        if success and data:
            working_patterns.append({"params": params, "data": data})
            data_samples.append(data)
            print(f"         ✅ SUCCESS: {params}")
        elif error:
            error_patterns.append({"params": params, "error": error})

    # Strategy 3: Error-Driven Adaptation
    if error_patterns and not working_patterns:
        print(f"      🔄 Adapting based on {len(error_patterns)} error patterns...")
        adapted_params = adapt_from_error_messages(error_patterns)

        for params in adapted_params:
            success, data, error = await test_single_call(agent, tool["name"], params)
            if success and data:
                working_patterns.append({"params": params, "data": data})
                data_samples.append(data)
                print(f"         ✅ ADAPTED SUCCESS: {params}")

    print(
        f"      📊 Found {len(working_patterns)} working patterns, {len(data_samples)} data samples"
    )
    return working_patterns, data_samples


def generate_smart_parameter_combinations(required_params: List[str]) -> List[Dict]:
    """Generate intelligent parameter combinations based on parameter names"""
    combinations = []

    for param in required_params:
        param_lower = param.lower()

        # Smart defaults based on parameter names
        if param_lower in ["limit", "count", "max", "size"]:
            combinations.extend([{param: 5}, {param: 10}, {param: 1}])
        elif param_lower in ["id", "user_id", "item_id", "row_id"]:
            combinations.extend([{param: 1}, {param: 2}])
        elif param_lower in ["table", "table_name", "tablename"]:
            for table in ["users", "data", "records", "items", "logs"]:
                combinations.append({param: table})
        elif param_lower in ["name", "username", "title"]:
            combinations.extend([{param: "test"}, {param: "admin"}, {param: "sample"}])
        elif param_lower in ["query", "search", "filter"]:
            combinations.extend([{param: ""}, {param: "*"}, {param: "test"}])
        else:
            # Try different data types for unknown parameters
            combinations.extend(
                [{param: 1}, {param: "test"}, {param: True}, {param: []}, {param: {}}]
            )

    # Combine all required parameters with smart defaults
    if len(required_params) > 1:
        combined = {}
        for param in required_params:
            if param.lower() in ["limit", "count"]:
                combined[param] = 5
            elif param.lower() in ["table", "table_name"]:
                combined[param] = "users"
            elif param.lower() in ["id", "user_id"]:
                combined[param] = 1
            else:
                combined[param] = "test"
        combinations.append(combined)

    return combinations


async def test_single_call(
    agent, tool_name: str, arguments: Dict
) -> Tuple[bool, Any, str]:
    """Test a single tool call and categorize the result"""
    try:
        result = await agent.call_tool(
            name=tool_name, arguments=arguments, server_name="universal_server"
        )

        if result.isError:
            error_text = result.content[0].text if result.content else "Unknown error"
            return False, None, error_text
        else:
            # Extract meaningful data
            for content in result.content:
                if hasattr(content, "text"):
                    try:
                        # Try parsing as JSON
                        data = json.loads(content.text)
                        if data and data != [] and data != {}:
                            return True, data, None
                    except json.JSONDecodeError:
                        # Non-JSON text response
                        if content.text and content.text.strip() not in [
                            "[]",
                            "{}",
                            "",
                            "null",
                        ]:
                            return True, content.text, None

            # Successful call but no meaningful data
            return True, None, None

    except Exception as e:
        return False, None, str(e)


def adapt_from_error_messages(error_patterns: List[Dict]) -> List[Dict]:
    """STEP 3: Error-Driven Adaptation - Learn from error messages to generate better parameters"""
    adapted_params = []

    for error_pattern in error_patterns:
        error_msg = error_pattern["error"].lower()
        base_params = error_pattern["params"].copy()

        # 🔍 Common error-driven adaptations
        if "select condition" in error_msg:
            # Need specific selection criteria
            adapted_params.extend(
                [
                    {**base_params, "id": 1},
                    {**base_params, "where": {"id": 1}},
                    {**base_params, "filter": {"id": 1}},
                    {**base_params, "conditions": [{"field": "id", "value": 1}]},
                ]
            )

        elif "null value" in error_msg and "constraint" in error_msg:
            # Missing required database fields
            adapted_params.extend(
                [
                    {**base_params, "user_account_id": 1},
                    {**base_params, "required_field": "test"},
                    {**base_params, "name": "test", "id": 1},
                    {**base_params, "data": {"name": "test", "id": 1}},
                ]
            )

        elif "bad request" in error_msg or "invalid" in error_msg:
            # Parameter format/structure issues
            adapted_params.extend(
                [
                    # Try string versions of numbers
                    {
                        k: str(v) if isinstance(v, int) else v
                        for k, v in base_params.items()
                    },
                    # Try wrapping in data object
                    {"data": base_params},
                    # Try different parameter structure
                    {"params": base_params, "options": {}},
                ]
            )

        elif "missing" in error_msg:
            # Missing parameters - try adding common ones
            adapted_params.extend(
                [
                    {**base_params, "api_key": "test"},
                    {**base_params, "auth": "test"},
                    {**base_params, "token": "test"},
                ]
            )

    return adapted_params


def generate_recommendations(results: Dict) -> List[str]:
    """Generate actionable recommendations based on exploration results"""
    recommendations = []

    # Working patterns analysis
    if results["working_patterns"]:
        recommendations.append(
            f"✅ Found {len(results['working_patterns'])} working parameter patterns"
        )

        # Show most successful patterns
        for pattern in results["working_patterns"][:3]:
            recommendations.append(f"   🎯 Use: {pattern['params']}")

    # Data extraction analysis
    if results["data_extracted"]:
        recommendations.append(
            f"📊 Successfully extracted {len(results['data_extracted'])} data samples"
        )

        # Analyze data types
        data_types = set()
        for data in results["data_extracted"]:
            data_types.add(type(data).__name__)
        recommendations.append(f"   📋 Data types: {', '.join(data_types)}")
    else:
        recommendations.append(
            "📭 No data extracted - database may be empty or require authentication"
        )

    # Tool-specific recommendations
    tool_names = [t["name"] for t in results["tools_discovered"]]

    if any("getall" in name.lower() or "list" in name.lower() for name in tool_names):
        recommendations.append(
            "💡 For bulk data: Use list/getall tools with Limit parameter"
        )

    if any(
        "get" in name.lower() and "getall" not in name.lower() for name in tool_names
    ):
        recommendations.append(
            "💡 For specific records: Use get tools with ID or filter conditions"
        )

    if any("create" in name.lower() or "insert" in name.lower() for name in tool_names):
        recommendations.append(
            "💡 For testing structure: Use create tools to understand required fields"
        )

    return recommendations


def print_exploration_results(results: Dict):
    """Print comprehensive, actionable exploration results"""
    print(f"\n🎯 UNIVERSAL EXPLORATION RESULTS")
    print("=" * 80)

    # Summary
    print(f"\n📊 SUMMARY:")
    print(f"   🔧 Tools discovered: {len(results['tools_discovered'])}")
    print(f"   ✅ Working patterns: {len(results['working_patterns'])}")
    print(f"   📊 Data samples: {len(results['data_extracted'])}")

    # Tools discovered
    if results["tools_discovered"]:
        print(f"\n🔧 TOOLS DISCOVERED:")
        for tool in results["tools_discovered"]:
            print(f"   📌 {tool['name']}: {tool['description'][:60]}...")
            if tool["required_params"]:
                print(f"      Required: {tool['required_params']}")

    # Working patterns
    if results["working_patterns"]:
        print(f"\n✅ WORKING PATTERNS (TOP 5):")
        for i, pattern in enumerate(results["working_patterns"][:5], 1):
            print(f"   [{i}] {pattern['params']}")
            if isinstance(pattern.get("data"), dict):
                print(f"       → Returns: {list(pattern['data'].keys())[:3]}...")
            elif pattern.get("data"):
                print(f"       → Returns: {str(pattern['data'])[:50]}...")

    # Data samples
    if results["data_extracted"]:
        print(f"\n📊 DATA SAMPLES (TOP 3):")
        for i, data in enumerate(results["data_extracted"][:3], 1):
            print(f"   [{i}] {str(data)[:100]}{'...' if len(str(data)) > 100 else ''}")

    # Actionable recommendations
    print(f"\n💡 ACTIONABLE RECOMMENDATIONS:")
    for rec in results["recommendations"]:
        print(f"   {rec}")

    # Next steps
    print(f"\n🚀 NEXT STEPS:")
    if results["working_patterns"]:
        best_pattern = results["working_patterns"][0]
        print(f"   1. Use pattern: {best_pattern['params']}")
        print(f"   2. Scale up with different Limit values")
        print(f"   3. Try variations of working parameters")
    else:
        print(f"   1. Check authentication requirements")
        print(f"   2. Verify database has data")
        print(f"   3. Review error messages for more clues")


async def main():
    """Example: Apply universal strategy to ARC Supabase"""
    arc_supabase_config = {
        "server_name": "arc_supabase",
        "display_name": "ARC Supabase",
        "description": "ARC Supabase n8n workflow server",
        "transport": "sse",
        "url": "https://advertisingreportcard.app.n8n.cloud/mcp/e566affb-4734-41c0-9295-a62be89771a4/sse",
    }

    print("🎯 UNIVERSAL MCP SERVER EXPLORATION STRATEGY")
    print("=" * 80)
    print("This strategy systematically explores any unknown MCP server to:")
    print("1. 🔍 Discover tools and their parameter requirements")
    print("2. 🧪 Test parameter combinations intelligently")
    print("3. 📚 Learn from errors to adapt approach")
    print("4. 📊 Extract valuable data and insights")
    print("5. 💡 Generate actionable recommendations")
    print("")

    results = await explore_any_mcp_server(arc_supabase_config)
    print_exploration_results(results)

    print(f"\n🎉 EXPLORATION COMPLETE!")
    print(f"This strategy can be applied to ANY unknown MCP server!")


if __name__ == "__main__":
    asyncio.run(main())
