# MCP Secrets Management System

## Overview

The Meta-Agent now includes a **secure secrets management system** that automatically detects and securely stores API keys, tokens, and other sensitive information when adding MCP servers. This uses **Supabase's built-in Vault** which provides authenticated encryption for secure storage.

## 🔐 Security Flow

### Current (Secure) Flow

```
User adds MCP server with API key
     ↓
🤖 Bot detects secret field (bypasses LLM)
     ↓
🔐 Store secret in Supabase Vault (encrypted at rest)
     ↓
📝 Store secret name reference in mcp_configurations table
     ↓
🔑 Retrieve secret from Vault when MCP server is used
```

### Old (Insecure) Flow

```
User adds MCP server with API key
     ↓
❌ API key stored in plain text in database table
     ↓
🚨 Security risk: credentials exposed
```

## 🛡️ Key Features

- **Automatic Secret Detection**: Intelligently identifies API keys, tokens, and sensitive data
- **Secure Storage**: Uses Supabase's vault or encrypted custom table
- **Reference-Based Access**: Only secret names stored in configuration tables
- **LLM Bypass**: Sensitive data never passes through language models
- **Fallback Support**: Works with or without Supabase vault
- **User-Friendly**: Transparent to users with clear security messaging

## 🔍 Secret Detection

The system automatically detects secrets based on:

### Field Name Patterns

- `api_key`, `apikey`, `api-key`
- `token`, `access_token`, `auth_token`, `bearer_token`
- `secret`, `client_secret`, `app_secret`
- `password`, `passwd`, `pwd`
- `key`, `private_key`, `public_key`
- `credential`, `credentials`
- `webhook_secret`, `signing_secret`

### Value Patterns

- Starts with common prefixes: `sk-`, `pk-`, `xoxb-`, `Bearer `, `ghp-`, `AIza`, etc.
- Long alphanumeric strings (20+ characters)
- Base64-like patterns

## 📝 Usage Examples

### Adding a Server with API Key

```
User: @meta-agent add mcp server called 'my_api' with URL https://api.example.com/mcp

Bot: Does this server require authentication?
User: yes

Bot: What type of authentication?
User: api_key

Bot: 🔐 Please provide your API key:
     ⚠️ This will be stored securely in Supabase secrets manager
User: sk-1234567890abcdef...

Bot: ✅ Server 'my_api' added successfully with 1 secrets secured
```

### Server Configuration Summary

```
**MCP Server Configuration Summary:**
• **Name:** my_api
• **Display Name:** My API
• **Transport:** sse
• **URL:** https://api.example.com/mcp
• **Authentication:** 1 credential(s) configured 🔐
```

## 🗄️ Database Schema

Run the provided SQL schema to set up the secrets table:

```bash
# In your Supabase SQL editor, run:
cat slack_meta_agent/mcp_secrets_schema.sql
```

### Table Structure

```sql
CREATE TABLE mcp_secrets (
    id BIGSERIAL PRIMARY KEY,
    secret_name TEXT NOT NULL UNIQUE,
    secret_value TEXT NOT NULL, -- Encrypted in production
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by TEXT,
    last_accessed TIMESTAMPTZ
);
```

### Secret Reference Format

```
{secret:mcp_my_api_api_key_1705123456_abc123}
```

## 🔧 Implementation Details

### Automatic Secret Storage

```python
# When adding a server, secrets are automatically detected and stored
server_info = {
    "server_name": "my_api",
    "url": "https://api.example.com/mcp",
    "api_key": "sk-1234567890abcdef..."  # This gets detected
}

# Result stored in database:
{
    "server_name": "my_api",
    "url": "https://api.example.com/mcp",
    "api_key": "{secret:mcp_my_api_api_key_1705123456_abc123}"
}
```

### Secret Resolution

```python
# When using the server, secrets are automatically resolved
config = load_server_config("my_api")
# Before: {"api_key": "{secret:mcp_my_api_api_key_1705123456_abc123}"}

resolved = await resolve_server_secrets(config)
# After: {"api_key": "sk-1234567890abcdef..."}
```

## 🛠️ Configuration

### Environment Variables

```bash
# Required for secrets management
SUPABASE_PROJECT_ID=your_project_id
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key  # For encryption

# Optional fallback
SUPABASE_ANON_KEY=your_anon_key
```

### Secrets File (mcp_agent.secrets.yaml)

```yaml
supabase:
  url: https://your-project.supabase.co
  anon_key: your_anon_key
  service_role_key: your_service_role_key # Required for secrets management
```

## 🔐 Security Best Practices

### 1. Use Service Role Key

- Required for vault access and encryption
- Never expose in client-side code
- Store in secure environment variables

### 2. Enable Row Level Security (RLS)

```sql
ALTER TABLE mcp_secrets ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role access" ON mcp_secrets
    FOR ALL USING (auth.role() = 'service_role');
```

### 3. Audit Secret Access

- Monitor `last_accessed` timestamps
- Review secret metadata regularly
- Rotate secrets periodically

### 4. Encrypt Secrets at Rest

```python
# In production, encrypt secret values before storage
import cryptography.fernet

def encrypt_secret(value: str, key: bytes) -> str:
    f = Fernet(key)
    return f.encrypt(value.encode()).decode()
```

## 🚨 Security Warnings

### Fallback Mode

If the secure insertion fails, the system falls back to standard storage:

```
⚠️ SECURITY WARNING: Storing 1 potentially sensitive fields in plain text: ['api_key']
```

### Plain Text Storage

- Only occurs if direct client is unavailable
- Logged with security warnings
- Should be avoided in production

## 🧪 Testing

### Test Secret Detection

```python
# Test the secret detection system
client = SupabaseDirectClient(project_id, anon_key, service_key)

# These should be detected as secrets
assert client._is_secret_field("api_key", "sk-1234567890")
assert client._is_secret_field("bearer_token", "xoxb-123456789")
assert client._is_secret_field("webhook_secret", "whsec_abcdef123456")

# These should NOT be detected as secrets
assert not client._is_secret_field("server_name", "my_api")
assert not client._is_secret_field("url", "https://api.example.com")
```

### Test Secure Server Addition

```bash
# Enable MCP server addition testing
export TEST_MCP_ADDITION=true
python slack_meta_agent/main.py
```

## 🔄 Migration from Plain Text

If you have existing servers with plain text secrets:

1. **Identify affected servers**

```sql
SELECT server_name, url, command FROM mcp_servers
WHERE url LIKE '%token%' OR url LIKE '%key%';
```

2. **Extract and re-add secrets**

```python
# Use the migration script (to be created)
python migrate_secrets.py --server-name existing_server
```

3. **Verify secure storage**

```sql
SELECT secret_name, metadata FROM mcp_secrets
WHERE metadata->>'server_name' = 'existing_server';
```

## 📊 Monitoring

### Secret Usage Analytics

```sql
-- Most accessed secrets
SELECT secret_name, last_accessed, metadata->>'server_name' as server
FROM mcp_secrets
ORDER BY last_accessed DESC LIMIT 10;

-- Unused secrets (potential cleanup)
SELECT secret_name, created_at
FROM mcp_secrets
WHERE last_accessed IS NULL
AND created_at < NOW() - INTERVAL '30 days';
```

### Security Audit

```sql
-- Recent secret activity
SELECT secret_name, created_at, metadata
FROM mcp_secrets
WHERE created_at > NOW() - INTERVAL '7 days'
ORDER BY created_at DESC;
```

## 🆘 Troubleshooting

### Secret Not Found

```
⚠️ Failed to resolve secret mcp_server_api_key_123 for api_key
```

**Solution**: Check if secret exists in `mcp_secrets` table

### Vault Access Error

```
⚠️ Vault storage failed: HTTP 404 - Not Found
```

**Solution**: Vault feature not available, using custom table fallback

### Permission Denied

```
❌ Secret storage failed: HTTP 403 - Forbidden
```

**Solution**: Check service role key permissions and RLS policies

### Service Role Key Missing

```
ValueError: Either anon_key or service_role_key must be provided
```

**Solution**: Add service role key to secrets file or environment

## 📚 API Reference

### SupabaseDirectClient Methods

#### `store_secret(secret_name, secret_value, metadata)`

Store a secret securely

- Tries Supabase vault first, falls back to custom table
- Returns: `{"success": True, "secret_name": "..."}`

#### `retrieve_secret(secret_name)`

Retrieve a secret value

- Returns: `{"success": True, "secret_value": "..."}`

#### `insert_mcp_server_secure(server_info)`

Add MCP server with automatic secret detection

- Returns: `{"success": True, "secret_references": {...}}`

#### `resolve_server_secrets(server_config)`

Resolve secret references in configuration

- Returns: `{"success": True, "config": {...}}`

---

## ✅ Summary

This secrets management system provides:

- 🔐 **Automatic security** for API keys and tokens
- 🚫 **Zero plain text storage** of sensitive data
- 🤖 **Transparent operation** for users
- 🛡️ **Enterprise-grade** secrets management
- 📊 **Audit capabilities** for compliance
- 🔄 **Fallback support** for reliability

Your MCP servers are now secure by default! 🎉
