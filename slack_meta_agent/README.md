# 🤖 Slack Meta-Agent System

A comprehensive Slack bot system built on the MCP (Model Context Protocol) agent framework that can dynamically orchestrate specialized agents to solve complex problems.

## 🌟 Features

- **Universal Slack Interface**: Single bot that handles all types of requests
- **Smart Routing**: Intelligent routing that uses LLM knowledge for basic facts, web search only when needed
- **Dynamic Agent Orchestration**: Automatically deploys specialized agents based on task requirements
- **Intelligent Task Analysis**: AI-powered intent analysis and execution planning
- **Memory & Learning**: Persistent conversation memory and user preference learning via Supabase
- **Extensible Architecture**: Add new MCP servers and capabilities through chat interface
- **Multi-Agent Coordination**: Uses orchestrator and swarm patterns for complex workflows

### 🎯 Smart Routing System

The meta-agent uses intelligent routing to optimize response speed and resource usage:

- **Knowledge Agent**: Handles basic factual questions using LLM knowledge
  - "What is the capital of California?" → Direct LLM response
  - "Who was the first president?" → No web search needed
  - "Define machine learning" → Uses training data
- **Data Researcher**: Handles current/dynamic information requiring web search
  - "What's Apple's current stock price?" → Live web search
  - "Latest news about AI" → Real-time information
  - "Today's weather forecast" → Current data needed

This prevents unnecessary web searches for well-known facts, resulting in faster responses and better resource efficiency.

## 🏗️ Architecture

```
Slack User → Meta-Agent → Intent Analyzer → Specialized Agents → MCP Servers → Results
                      ↓
                  Supabase (Memory, Workflows, Learning)
```

### Specialized Agent Types

1. **Knowledge Agent** - Basic factual questions, definitions, well-established information
2. **Financial Analyst** - Market research, financial modeling, investment analysis
3. **Code Developer** - Software development, deployment, GitHub management
4. **Data Researcher** - Current information gathering, live data, web research
5. **Project Manager** - Planning, coordination, progress tracking
6. **Communication Specialist** - Content creation, presentations, messaging

## 🚀 Quick Start

### Prerequisites

- Python 3.8+
- Node.js (for MCP servers)
- Docker (for GitHub server)
- Slack workspace with bot permissions
- Supabase project
- OpenAI API key

### Installation

```bash
# Clone and setup
git clone <your-repo>
cd slack_meta_agent

# Install dependencies
uv sync --all-extras --all-packages --group dev

# Copy and configure secrets
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
# Edit mcp_agent.secrets.yaml with your actual credentials

# Test the system
python main.py

# Test routing improvements
python test_routing_improvement.py
```

### Slack Bot Setup

1. **Create Slack App**: Go to https://api.slack.com/apps
2. **Enable Socket Mode**: For real-time WebSocket connection
3. **Add Bot Scopes**:
   - `app_mentions:read`
   - `channels:history`
   - `chat:write`
   - `im:history`
   - `im:read`
   - `users:read`
4. **Get Tokens**:
   - Bot Token (`xoxb-...`)
   - App Token (`xapp-...`)
5. **Add to your secrets.yaml**

### Supabase Setup

1. **Create Project**: Go to https://supabase.com
2. **Get API Keys**: From Settings → API
3. **Create Tables** (the agent will help with this):

   ```sql
   -- User interactions and learning
   CREATE TABLE interactions (
     id SERIAL PRIMARY KEY,
     user_id TEXT,
     message TEXT,
     result TEXT,
     analysis JSONB,
     timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW()
   );

   -- User preferences and memory
   CREATE TABLE user_profiles (
     user_id TEXT PRIMARY KEY,
     preferences JSONB,
     capabilities_used JSONB,
     created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
     updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
   );

   -- Workflow templates and patterns
   CREATE TABLE workflows (
     id SERIAL PRIMARY KEY,
     name TEXT,
     description TEXT,
     agents_used TEXT[],
     success_rate FLOAT,
     template JSONB,
     created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
   );
   ```

## 📋 Implementation Phases

### Phase 1: Foundation (Week 1) ✅

- [x] Basic project structure
- [x] Core configuration files
- [x] Meta-agent class with specialized agent registry
- [x] Intent analysis system
- [x] Single-agent execution

### Phase 2: Multi-Agent Coordination (Week 2)

- [ ] Orchestrator integration for complex tasks
- [ ] Swarm pattern implementation
- [ ] Agent handoff mechanisms
- [ ] Parallel task execution

### Phase 3: Slack Integration (Week 3)

- [ ] WebSocket connection to Slack
- [ ] Real-time message handling
- [ ] Event processing pipeline
- [ ] User session management

### Phase 4: Memory & Learning (Week 4)

- [ ] Supabase schema implementation
- [ ] Conversation memory system
- [ ] User preference learning
- [ ] Workflow pattern recognition

### Phase 5: Dynamic Capabilities (Week 5)

- [ ] Runtime MCP server registration
- [ ] Chat-based server configuration
- [ ] Capability discovery system
- [ ] Auto-scaling agent deployment

### Phase 6: Advanced Features (Week 6)

- [ ] Workflow templates
- [ ] Performance monitoring
- [ ] Error recovery systems
- [ ] Usage analytics

## 🔧 Configuration

### Adding New MCP Servers

You can add servers through chat:

```
@meta-agent add server {
  "name": "airtable",
  "transport": "sse",
  "url": "https://your-airtable-server.com/sse",
  "headers": {"Authorization": "Bearer YOUR_TOKEN"}
}
```

Or via config file:

```yaml
mcp:
  servers:
    airtable:
      transport: sse
      url: "https://your-airtable-server.com/sse"
      headers:
        Authorization: "Bearer YOUR_TOKEN"
```

### Customizing Agents

Add new agent types to the registry:

```python
"custom_agent": AgentSpec(
    name="custom_agent",
    instruction="Your custom instruction...",
    server_names=["server1", "server2"],
    capabilities=["capability1", "capability2"]
)
```

## 🎯 Example Use Cases

### Financial Dashboard Creation

```
User: "Create a Q4 revenue dashboard for our SaaS metrics"

Meta-Agent:
1. Deploys Financial Analyst + Data Researcher + Code Developer
2. Financial Analyst analyzes revenue data from Supabase
3. Data Researcher gathers industry benchmarks
4. Code Developer creates interactive dashboard
5. Results delivered to Slack with live dashboard link
```

### Automated Code Review

```
User: "Review the new authentication PR and deploy if tests pass"

Meta-Agent:
1. Deploys Code Developer + Project Manager
2. Code Developer reviews PR, runs tests, checks security
3. Project Manager coordinates deployment pipeline
4. Automated deployment with status updates to Slack
```

### Market Research Report

```
User: "Research the competitive landscape for AI coding assistants"

Meta-Agent:
1. Deploys Data Researcher + Communication Specialist
2. Data Researcher gathers competitor data, pricing, features
3. Communication Specialist creates professional report
4. Report stored in Supabase, summary posted to Slack
```

## 🛠️ Development

### Testing Individual Components

```bash
# Test intent analysis
python -c "
from main import MetaAgent
import asyncio
async def test():
    meta = MetaAgent()
    result = await meta.analyze_user_intent('Create a financial report')
    print(result)
asyncio.run(test())
"

# Test agent creation
python -c "
from main import MetaAgent
import asyncio
async def test():
    meta = MetaAgent()
    agent = await meta.create_specialized_agent('financial_analyst')
    print(f'Created agent: {agent.name}')
asyncio.run(test())
"
```

### Debugging

- Logs are stored in `logs/meta-agent-*.jsonl`
- Use `rich` console for formatted output
- Enable debug logging in config

## 🚢 Deployment

### Docker Deployment

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install uv && uv sync --all-extras
CMD ["python", "main.py"]
```

### Cloud Deployment

- **AWS Lambda**: For serverless event handling
- **Railway/Render**: For always-on WebSocket connection
- **Google Cloud Run**: For scalable container deployment

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🔗 Resources

- [MCP Agent Framework Documentation](https://github.com/your-org/mcp-agent)
- [Slack API Documentation](https://api.slack.com/)
- [Supabase Documentation](https://supabase.com/docs)
- [OpenAI API Documentation](https://platform.openai.com/docs)
