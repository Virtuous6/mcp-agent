# Agent Specification Examples

This directory contains example enhanced agent specifications that demonstrate the CrewAI-style configuration format.

## Available Examples

- **financial_analyst.json** - A senior financial analyst with market research capabilities
- **code_developer.json** - A full-stack developer with code generation expertise

## Importing Examples

To import these specs into your database:

```bash
# Set environment variables
export SUPABASE_URL="your-supabase-url"
export SUPABASE_SERVICE_ROLE_KEY="your-service-role-key"

# Import a spec
python ../../scripts/manage_agent_specs.py import-spec financial_analyst.json
python ../../scripts/manage_agent_specs.py import-spec code_developer.json

# Verify import
python ../../scripts/manage_agent_specs.py list
```

## Creating Your Own Specs

Use these examples as templates to create your own specialized agents:

1. Copy an existing spec file
2. Modify the fields according to your needs
3. Import using the CLI tool
4. Test the agent in your system

## Key Fields to Customize

- **id**: Unique identifier for your agent
- **name**: Human-friendly display name
- **backstory**: Rich persona that shapes the agent's behavior
- **llm_model**: Choose between gpt-4o, gpt-4o-mini, etc.
- **temperature**: Lower for consistency, higher for creativity
- **tools**: Array of MCP server names the agent can access
- **constraints**: Rules that guide agent behavior
- **few_shot_examples**: Examples that demonstrate expected behavior

## Best Practices

1. **Backstory Matters**: A rich backstory leads to more contextual responses
2. **Temperature Tuning**: Use 0.1-0.3 for analytical tasks, 0.5-0.7 for creative tasks
3. **Tool Selection**: Only give agents the tools they need
4. **Constraints**: Use constraints to enforce quality and safety
5. **Examples**: Provide 2-3 clear examples of expected behavior
