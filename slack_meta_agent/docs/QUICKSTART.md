# 🚀 QUICK START GUIDE

## Get Your Slack Meta-Agent Running in 10 Minutes

Your Slack Meta-Agent system is **READY TO DEPLOY**! This guide will have you up and running in minutes.

## 🎯 What You're Getting

A complete Slack bot that can:

- 📊 **Create Dashboards**: "Create Q4 revenue dashboard for SaaS metrics"
- 💻 **Deploy Code**: "Deploy a new landing page to GitHub"
- 🔍 **Research Anything**: "Research competitor analysis for our product"
- 📋 **Manage Projects**: "Create project plan for new feature launch"
- 💬 **Generate Content**: "Write presentation for board meeting"

**All through natural language in Slack!**

## ⚡ FASTEST START (2 Commands)

```bash
# 1. Install dependencies and configure
python quick_start.py

# 2. Run your Meta-Agent
python main.py
```

That's it! Your Meta-Agent is now listening in Slack.

## 📝 Step-by-Step Setup

### 1. **Prerequisites Check**

```bash
python quick_start.py  # This will check everything for you
```

If missing anything:

- **Python 3.8+**: https://python.org
- **Node.js 16+**: https://nodejs.org
- **UV**: `pip install uv`

### 2. **Slack Bot Setup** (2 minutes)

1. Go to https://api.slack.com/apps
2. Click "Create New App" → "From scratch"
3. Name it "Meta-Agent" and select your workspace
4. **Enable Socket Mode**:
   - Go to "Socket Mode" → Toggle ON
   - Generate an App-Level Token → Copy `xapp-...`
5. **Set Bot Permissions**:
   - Go to "OAuth & Permissions"
   - Add scopes: `app_mentions:read`, `channels:history`, `chat:write`, `im:history`, `im:read`, `users:read`
   - Install to workspace → Copy Bot Token `xoxb-...`

### 3. **Configuration** (1 minute)

```bash
python quick_start.py  # Follow the prompts
```

Enter your:

- OpenAI API key
- Slack bot token (`xoxb-...`)
- Slack app token (`xapp-...`)

Optional (but recommended):

- GitHub token (for code deployment)
- Supabase credentials (for memory/learning)
- Brave Search API (for web research)

### 4. **Test & Run**

```bash
# Test everything works
python test_meta_agent.py --interactive

# Start your Meta-Agent
python main.py
```

## 🎉 Using Your Meta-Agent

### Example Conversations

**Financial Dashboard:**

```
@meta-agent Create Q4 revenue dashboard for SaaS metrics
```

→ _Creates live dashboard with industry benchmarks_

**Code Deployment:**

```
@meta-agent Deploy a React landing page for our new product to GitHub
```

→ _Generates code, deploys to GitHub, returns live URL_

**Research Report:**

```
@meta-agent Research AI agent market trends and competitive landscape
```

→ _Comprehensive report with data sources and insights_

**Project Planning:**

```
@meta-agent Create project plan for mobile app launch including timeline and resources
```

→ _Detailed project plan with tasks and milestones_

## 🔧 Troubleshooting

### Common Issues

**"Slack SDK not installed"**

```bash
pip install slack-sdk slack-bolt
```

**"Configuration test failed"**

```bash
# Check your secrets file
cat mcp_agent.secrets.yaml

# Verify tokens are correct format
# Bot token: xoxb-...
# App token: xapp-...
```

**"No response from agent"**

- Ensure bot is added to channel: `/invite @meta-agent`
- Check bot permissions in Slack app settings
- Verify Socket Mode is enabled

### Get Help

```bash
# Run diagnostics
python test_meta_agent.py --interactive

# Check logs
tail -f logs/meta-agent*.jsonl

# Test specific components
python test_meta_agent.py --test agents
```

## 🚀 Advanced Usage

### Add New Capabilities

```
@meta-agent add server {
  "name": "airtable",
  "transport": "sse",
  "url": "https://your-airtable-server.com/sse"
}
```

### Custom Agent Types

Edit `main.py` and add to `_initialize_agent_registry()`:

```python
"custom_agent": AgentSpec(
    name="custom_agent",
    instruction="Your specialized instruction...",
    server_names=["relevant", "servers"],
    capabilities=["custom", "capabilities"]
)
```

## 📊 System Architecture

Your Meta-Agent follows this flow:

```
Slack Message → Intent Analysis → Agent Creation → Orchestration → Results → Memory Storage
```

**Specialized Agents:**

- **Financial Analyst**: Market data, financial modeling, dashboards
- **Code Developer**: GitHub, deployments, technical solutions
- **Data Researcher**: Web search, analysis, comprehensive reports
- **Project Manager**: Planning, coordination, progress tracking
- **Communication Specialist**: Content creation, presentations

**Execution Strategies:**

- **Single Agent**: Simple requests
- **Sequential**: Step-by-step workflows
- **Parallel**: Independent tasks
- **Orchestrated**: Complex multi-agent coordination

## 🎯 Next Steps

1. **Test the system** with simple requests first
2. **Add integrations** for your specific tools (Airtable, Notion, etc.)
3. **Customize agent instructions** for your business domain
4. **Deploy to production** with proper monitoring

Your Meta-Agent is production-ready and will get smarter with each interaction!

---

**Need Help?** Check `README.md` for detailed documentation or run `python quick_start.py` for guided setup.
