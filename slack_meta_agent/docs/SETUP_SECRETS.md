# Quick Setup: MCP Secrets Management

## 🚀 5-Minute Setup

### 1. Supabase Vault Setup (1 minute)

Supabase Vault comes pre-installed and ready to use! No additional database setup required.

The Vault uses authenticated encryption to store secrets securely on disk.

### 2. Add Service Role Key (1 minute)

In your `mcp_agent.secrets.yaml`:

```yaml
supabase:
  url: https://your-project.supabase.co
  anon_key: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
  service_role_key: YOUR_SERVICE_ROLE_KEY_HERE # Add this line
```

**Where to find your Service Role Key:**

1. Go to [Supabase Dashboard](https://supabase.com/dashboard)
2. Select your project
3. Go to **Settings** → **API**
4. Copy the **service_role** key (NOT the anon key)

### 3. Test the System (2 minutes)

```bash
cd slack_meta_agent
python test_secrets_management.py
```

Expected output:

```
🧪 Testing Secret Detection Logic
✅ PASS api_key: 'sk-1234567890abcdef' -> True (expected: True)
✅ PASS bearer_token: 'xoxb-123456789012' -> True (expected: True)
...
📊 Results: 15 passed, 0 failed

🔐 Testing Complete Secrets Management Flow
✅ Client initialized for project: your_project_id
✅ Secret stored: test_secret_1705123456
✅ Secret retrieved correctly: sk-test123...
✅ Server added with 2 secrets secured
✅ Secret reference resolved correctly

🎉 All secrets management tests passed!
```

### 4. Try Adding a Server (1 minute)

In Slack:

```
@meta-agent add mcp server called 'test_api' with URL https://api.example.com/mcp using sse transport

Bot: Does this server require authentication?
You: yes

Bot: What type of authentication?
You: api_key

Bot: 🔐 Please provide your API key:
You: sk-1234567890abcdef...

Bot: ✅ Server 'test_api' added successfully with 1 secrets secured
```

## ✅ You're Done!

Your system now automatically:

- 🔐 **Detects** API keys and tokens
- 🛡️ **Stores** them securely in Supabase vault
- 📝 **References** them by name in configs
- 🔑 **Retrieves** them when needed
- 🚫 **Never exposes** them to LLMs

## 🆘 Troubleshooting

### "Service role key required"

```yaml
# Add to mcp_agent.secrets.yaml:
supabase:
  service_role_key: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### "Table 'mcp_secrets' doesn't exist"

```sql
-- Run in Supabase SQL editor:
CREATE TABLE mcp_secrets (
    id BIGSERIAL PRIMARY KEY,
    secret_name TEXT NOT NULL UNIQUE,
    secret_value TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### "No secrets detected"

The system looks for these patterns:

- Field names: `api_key`, `token`, `secret`, `password`
- Values starting with: `sk-`, `xoxb-`, `Bearer `, `ghp-`
- Long alphanumeric strings (20+ chars)

---

**🎉 Your MCP servers are now secure by default!**
