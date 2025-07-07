-- ===== DYNAMIC META-AGENT MIGRATION SCRIPT =====
-- Project: qqggdvfeybfzqmgxmidt (TOMAS)
-- Status: SAFE MIGRATION - No existing data conflicts
--
-- This script adds the missing tables needed for dynamic functionality
-- while preserving all existing data and relationships.

-- ===== 1. AGENT-SERVER MAPPING (Many-to-Many) =====
-- This replaces hard-coded server assignments with dynamic relationships
CREATE TABLE IF NOT EXISTS agent_server_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id BIGINT NOT NULL,  -- References agent_specs.id
    server_id UUID NOT NULL,   -- References mcp_servers.id  
    priority INTEGER DEFAULT 0,
    context_filter JSONB DEFAULT '{}', -- e.g., {"organization": "ARC", "user_role": "admin"}
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Foreign key constraints
    CONSTRAINT fk_agent_server_mapping_agent 
        FOREIGN KEY (agent_id) REFERENCES agent_specs(id) ON DELETE CASCADE,
    CONSTRAINT fk_agent_server_mapping_server 
        FOREIGN KEY (server_id) REFERENCES mcp_servers(id) ON DELETE CASCADE,
        
    -- Unique constraint to prevent duplicate mappings
    CONSTRAINT unique_agent_server_mapping UNIQUE(agent_id, server_id)
);

-- ===== 2. WORKFLOWS TABLE =====
-- Enables n8n, CrewAI, and internal workflow execution
CREATE TABLE IF NOT EXISTS workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) UNIQUE NOT NULL,
    type VARCHAR(50) NOT NULL CHECK (type IN ('n8n', 'crewai', 'internal', 'sequential')),
    description TEXT,
    definition JSONB NOT NULL DEFAULT '{}',
    webhook_url TEXT, -- For n8n workflows
    trigger_patterns TEXT[] DEFAULT '{}', -- Keywords that trigger this workflow
    is_active BOOLEAN DEFAULT true,
    success_rate FLOAT DEFAULT 0.0,
    usage_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ===== 3. CREWAI CONFIGURATIONS =====
-- Stores CrewAI crew definitions
CREATE TABLE IF NOT EXISTS crew_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) UNIQUE NOT NULL,
    description TEXT,
    agents JSONB NOT NULL DEFAULT '[]', -- Array of agent configs with roles
    tasks JSONB NOT NULL DEFAULT '[]',  -- Array of task definitions
    process_type VARCHAR(50) DEFAULT 'sequential' CHECK (process_type IN ('sequential', 'hierarchical')),
    max_iterations INTEGER DEFAULT 5,
    trigger_patterns TEXT[] DEFAULT '{}', -- Keywords that trigger this crew
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ===== 4. LEARNING PATTERNS (Dynamic) =====
-- Replaces hard-coded patterns with database-driven learning
CREATE TABLE IF NOT EXISTS learning_patterns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) UNIQUE NOT NULL,
    keywords TEXT[] NOT NULL DEFAULT '{}',
    agent_name VARCHAR(255), -- Can route to agents
    workflow_name VARCHAR(255), -- Can route to workflows
    crew_name VARCHAR(255), -- Can route to crews
    confidence FLOAT DEFAULT 0.7,
    usage_count INTEGER DEFAULT 0,
    success_rate FLOAT DEFAULT 0.0,
    context_filter JSONB DEFAULT '{}',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Ensure exactly one target is specified
    CONSTRAINT check_single_target CHECK (
        (agent_name IS NOT NULL AND workflow_name IS NULL AND crew_name IS NULL) OR
        (agent_name IS NULL AND workflow_name IS NOT NULL AND crew_name IS NULL) OR
        (agent_name IS NULL AND workflow_name IS NULL AND crew_name IS NOT NULL)
    ),
    
    -- Foreign key references
    CONSTRAINT fk_learning_pattern_workflow 
        FOREIGN KEY (workflow_name) REFERENCES workflows(name) ON DELETE CASCADE,
    CONSTRAINT fk_learning_pattern_crew 
        FOREIGN KEY (crew_name) REFERENCES crew_configs(name) ON DELETE CASCADE
);

-- ===== 5. USER CONTEXTS (Enhanced from user_preferences) =====
-- Context-aware routing based on user/organization
CREATE TABLE IF NOT EXISTS user_contexts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) NOT NULL,
    organization VARCHAR(255),
    role VARCHAR(100) DEFAULT 'user',
    preferences JSONB DEFAULT '{}',
    available_servers TEXT[] DEFAULT '{}', -- Server names this user/org can access
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Unique constraint for user + organization combination
    CONSTRAINT unique_user_context UNIQUE(user_id, organization)
);

-- ===== 6. ENHANCED COLUMNS FOR EXISTING TABLES =====

-- Add organization support to mcp_servers (if not exists)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'mcp_servers' AND column_name = 'organization'
    ) THEN
        ALTER TABLE mcp_servers ADD COLUMN organization VARCHAR(255);
    END IF;
END $$;

-- Add trigger patterns to agent_specs (if not exists) 
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'agent_specs' AND column_name = 'trigger_patterns'
    ) THEN
        ALTER TABLE agent_specs ADD COLUMN trigger_patterns TEXT[] DEFAULT '{}';
    END IF;
END $$;

-- Add metadata to agent_specs (if not exists)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'agent_specs' AND column_name = 'metadata'
    ) THEN
        ALTER TABLE agent_specs ADD COLUMN metadata JSONB DEFAULT '{}';
    END IF;
END $$;

-- ===== 7. CREATE INDEXES FOR PERFORMANCE =====

-- Indexes for agent_server_mappings
CREATE INDEX IF NOT EXISTS idx_agent_server_mappings_agent_id ON agent_server_mappings(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_server_mappings_server_id ON agent_server_mappings(server_id);
CREATE INDEX IF NOT EXISTS idx_agent_server_mappings_priority ON agent_server_mappings(priority DESC);

-- Indexes for workflows
CREATE INDEX IF NOT EXISTS idx_workflows_type ON workflows(type);
CREATE INDEX IF NOT EXISTS idx_workflows_active ON workflows(is_active) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_workflows_trigger_patterns ON workflows USING GIN(trigger_patterns);

-- Indexes for learning_patterns
CREATE INDEX IF NOT EXISTS idx_learning_patterns_keywords ON learning_patterns USING GIN(keywords);
CREATE INDEX IF NOT EXISTS idx_learning_patterns_active ON learning_patterns(is_active) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_learning_patterns_confidence ON learning_patterns(confidence DESC);
CREATE INDEX IF NOT EXISTS idx_learning_patterns_usage ON learning_patterns(usage_count DESC);

-- Indexes for user_contexts
CREATE INDEX IF NOT EXISTS idx_user_contexts_user_id ON user_contexts(user_id);
CREATE INDEX IF NOT EXISTS idx_user_contexts_organization ON user_contexts(organization);
CREATE INDEX IF NOT EXISTS idx_user_contexts_available_servers ON user_contexts USING GIN(available_servers);

-- ===== 8. CREATE HELPFUL VIEWS =====

-- View to see complete agent configurations
CREATE OR REPLACE VIEW agent_configurations AS
SELECT 
    a.id,
    a.agent_type,
    a.name,
    a.instruction,
    a.capabilities,
    a.server_names as legacy_servers,
    a.usage_count,
    a.success_rate,
    COALESCE(
        ARRAY_AGG(ms.server_name ORDER BY asm.priority DESC) FILTER (WHERE ms.server_name IS NOT NULL),
        '{}'::text[]
    ) as dynamic_servers,
    a.created_at,
    a.updated_at
FROM agent_specs a
LEFT JOIN agent_server_mappings asm ON a.id = asm.agent_id
LEFT JOIN mcp_servers ms ON asm.server_id = ms.id AND ms.is_enabled = true
GROUP BY a.id, a.agent_type, a.name, a.instruction, a.capabilities, a.server_names, a.usage_count, a.success_rate, a.created_at, a.updated_at;

-- View to see workflow execution patterns
CREATE OR REPLACE VIEW workflow_analytics AS
SELECT 
    w.name,
    w.type,
    w.usage_count,
    w.success_rate,
    w.trigger_patterns,
    w.is_active,
    CASE 
        WHEN w.usage_count = 0 THEN 'Unused'
        WHEN w.success_rate > 0.8 THEN 'High Performance'
        WHEN w.success_rate > 0.5 THEN 'Good Performance'
        ELSE 'Needs Improvement'
    END as performance_status
FROM workflows w
ORDER BY w.usage_count DESC, w.success_rate DESC;

-- ===== MIGRATION COMPLETE =====
-- This schema is now ready for the dynamic meta-agent system!

COMMENT ON TABLE agent_server_mappings IS 'Dynamic many-to-many mapping between agents and MCP servers';
COMMENT ON TABLE workflows IS 'Workflow definitions for n8n, CrewAI, and internal orchestration';
COMMENT ON TABLE crew_configs IS 'CrewAI crew configurations';
COMMENT ON TABLE learning_patterns IS 'Dynamic learning patterns replacing hard-coded routing';
COMMENT ON TABLE user_contexts IS 'User context for organization-aware routing';

-- Insert success confirmation
INSERT INTO mcp_configurations (name, description, is_active) 
VALUES ('dynamic_migration_v1', 'Dynamic meta-agent migration completed successfully', true)
ON CONFLICT (name) DO UPDATE SET 
    description = EXCLUDED.description,
    updated_at = NOW(); 