-- ===== DYNAMIC META-AGENT DATA SEEDING SCRIPT =====
-- Project: qqggdvfeybfzqmgxmidt (TOMAS)
-- 
-- This script seeds the new dynamic tables with existing agent data
-- and creates initial patterns based on interaction history.

-- ===== 1. CREATE AGENT-SERVER MAPPINGS =====
-- Map existing agents to current MCP servers based on their server_names

-- Get server IDs for reference
DO $$
DECLARE
    arc_supabase_id UUID;
    ghl_dynamic_id UUID;
    agent_rec RECORD;
    server_name TEXT;
    priority_counter INTEGER;
BEGIN
    -- Get server IDs
    SELECT id INTO arc_supabase_id FROM mcp_servers WHERE server_name = 'arc_supabase';
    SELECT id INTO ghl_dynamic_id FROM mcp_servers WHERE server_name = 'ghl-dynamic';
    
    -- Loop through each agent and create mappings
    FOR agent_rec IN SELECT id, agent_type, server_names FROM agent_specs LOOP
        priority_counter := 0;
        
        -- Loop through each server in the agent's server_names array
        IF agent_rec.server_names IS NOT NULL THEN
            FOREACH server_name IN ARRAY agent_rec.server_names LOOP
                IF server_name = 'arc_supabase' AND arc_supabase_id IS NOT NULL THEN
                    INSERT INTO agent_server_mappings (agent_id, server_id, priority, context_filter)
                    VALUES (agent_rec.id, arc_supabase_id, priority_counter, '{}')
                    ON CONFLICT (agent_id, server_id) DO NOTHING;
                    priority_counter := priority_counter + 1;
                    
                ELSIF server_name = 'ghl-dynamic' AND ghl_dynamic_id IS NOT NULL THEN
                    INSERT INTO agent_server_mappings (agent_id, server_id, priority, context_filter)
                    VALUES (agent_rec.id, ghl_dynamic_id, priority_counter, '{}')
                    ON CONFLICT (agent_id, server_id) DO NOTHING;
                    priority_counter := priority_counter + 1;
                    
                END IF;
            END LOOP;
        END IF;
        
        RAISE NOTICE 'Created mappings for agent: %', agent_rec.agent_type;
    END LOOP;
END $$;

-- ===== 2. CREATE INITIAL LEARNING PATTERNS =====
-- Based on interaction history and common usage patterns

-- Pattern for data research and weather queries
INSERT INTO learning_patterns (name, keywords, agent_name, confidence, usage_count, success_rate, is_active)
VALUES (
    'data_research_pattern',
    ARRAY['weather', 'data', 'research', 'search', 'find', 'current', 'latest', 'information'],
    'data_researcher',
    0.9,
    14, -- Based on dynamic_discovery usage
    0.85,
    true
) ON CONFLICT (name) DO UPDATE SET
    keywords = EXCLUDED.keywords,
    confidence = EXCLUDED.confidence,
    updated_at = NOW();

-- Pattern for capability inspection
INSERT INTO learning_patterns (name, keywords, agent_name, confidence, usage_count, success_rate, is_active)
VALUES (
    'capability_inspection_pattern',
    ARRAY['capabilities', 'what can you', 'what tools', 'help', 'commands', 'available', 'access'],
    'capability_inspector',
    0.95,
    5, -- Estimated from single_agent usage
    0.90,
    true
) ON CONFLICT (name) DO UPDATE SET
    keywords = EXCLUDED.keywords,
    confidence = EXCLUDED.confidence,
    updated_at = NOW();

-- Pattern for feedback collection
INSERT INTO learning_patterns (name, keywords, agent_name, confidence, usage_count, success_rate, is_active)
VALUES (
    'feedback_collection_pattern',
    ARRAY['feedback', 'give feedback', 'suggestion', 'improvement', 'issue', 'problem', 'bug report'],
    'feedback_collector',
    0.90,
    3, -- Estimated from feedback table count
    0.80,
    true
) ON CONFLICT (name) DO UPDATE SET
    keywords = EXCLUDED.keywords,
    confidence = EXCLUDED.confidence,
    updated_at = NOW();

-- Pattern for Airtable operations (high usage)
INSERT INTO learning_patterns (name, keywords, agent_name, confidence, usage_count, success_rate, is_active)
VALUES (
    'airtable_operations_pattern',
    ARRAY['airtable', 'air table', 'base', 'records', 'database', 'table', 'data management'],
    'airtable_manager',
    0.85,
    8, -- Estimated from interaction patterns
    0.75,
    true
) ON CONFLICT (name) DO UPDATE SET
    keywords = EXCLUDED.keywords,
    confidence = EXCLUDED.confidence,
    updated_at = NOW();

-- Pattern for automation and workflows
INSERT INTO learning_patterns (name, keywords, agent_name, confidence, usage_count, success_rate, is_active)
VALUES (
    'automation_pattern',
    ARRAY['automation', 'workflow', 'automate', 'trigger', 'sync', 'integrate'],
    'automation_specialist',
    0.80,
    6, -- Estimated from interaction patterns
    0.70,
    true
) ON CONFLICT (name) DO UPDATE SET
    keywords = EXCLUDED.keywords,
    confidence = EXCLUDED.confidence,
    updated_at = NOW();

-- Pattern for knowledge queries
INSERT INTO learning_patterns (name, keywords, agent_name, confidence, usage_count, success_rate, is_active)
VALUES (
    'knowledge_pattern',
    ARRAY['what is', 'who is', 'define', 'explain', 'meaning', 'capital', 'history'],
    'knowledge_agent',
    0.85,
    4, -- Estimated usage
    0.90,
    true
) ON CONFLICT (name) DO UPDATE SET
    keywords = EXCLUDED.keywords,
    confidence = EXCLUDED.confidence,
    updated_at = NOW();

-- ===== 3. CREATE INITIAL USER CONTEXTS =====
-- Based on existing user_preferences data

INSERT INTO user_contexts (user_id, organization, role, preferences, available_servers)
SELECT 
    up.user_id,
    'default', -- Default organization
    'user', -- Default role
    up.preferences,
    ARRAY['arc_supabase', 'ghl-dynamic'] -- Both servers available by default
FROM user_preferences up
ON CONFLICT (user_id, organization) DO UPDATE SET
    preferences = EXCLUDED.preferences,
    available_servers = EXCLUDED.available_servers,
    updated_at = NOW();

-- ===== 4. CREATE EXAMPLE WORKFLOWS =====
-- Sample n8n workflow configuration
INSERT INTO workflows (name, type, description, definition, webhook_url, trigger_patterns, is_active)
VALUES (
    'airtable_sync_workflow',
    'n8n',
    'Synchronize data between Airtable and external systems',
    '{"nodes": ["airtable_trigger", "data_processor", "webhook_output"], "version": "1.0"}',
    'https://advertisingreportcard.app.n8n.cloud/webhook/airtable-sync',
    ARRAY['sync airtable', 'update records', 'airtable workflow'],
    true
) ON CONFLICT (name) DO UPDATE SET
    description = EXCLUDED.description,
    trigger_patterns = EXCLUDED.trigger_patterns,
    updated_at = NOW();

-- Sample internal workflow
INSERT INTO workflows (name, type, description, definition, trigger_patterns, is_active)
VALUES (
    'data_analysis_workflow',
    'internal',
    'Multi-step data analysis using multiple agents',
    '{"steps": [
        {"type": "data_collection", "agent": "data_researcher", "params": {"source": "web"}},
        {"type": "analysis", "agent": "financial_analyst", "params": {"format": "dashboard"}},
        {"type": "communication", "agent": "communication_specialist", "params": {"channel": "slack"}}
    ]}',
    ARRAY['analyze data', 'create analysis', 'data workflow'],
    true
) ON CONFLICT (name) DO UPDATE SET
    description = EXCLUDED.description,
    trigger_patterns = EXCLUDED.trigger_patterns,
    updated_at = NOW();

-- ===== 5. CREATE EXAMPLE CREWAI CONFIGURATION =====
INSERT INTO crew_configs (name, description, agents, tasks, process_type, trigger_patterns, is_active)
VALUES (
    'research_analysis_crew',
    'A crew that researches a topic and creates a comprehensive analysis',
    '[
        {
            "name": "researcher",
            "role": "Research Specialist",
            "goal": "Gather comprehensive information on the given topic",
            "backstory": "Expert researcher with access to web search and data sources"
        },
        {
            "name": "analyst", 
            "role": "Data Analyst",
            "goal": "Analyze the researched data and create insights",
            "backstory": "Financial and data analyst with expertise in creating reports"
        },
        {
            "name": "communicator",
            "role": "Communication Expert", 
            "goal": "Present the analysis in a clear, actionable format",
            "backstory": "Expert at creating clear, compelling presentations"
        }
    ]',
    '[
        {
            "description": "Research the topic: {user_message}",
            "agent_index": 0,
            "expected_output": "Comprehensive research findings with sources"
        },
        {
            "description": "Analyze the research data and create insights",
            "agent_index": 1, 
            "expected_output": "Data analysis with key insights and recommendations"
        },
        {
            "description": "Create a final presentation of the analysis",
            "agent_index": 2,
            "expected_output": "Well-formatted analysis report"
        }
    ]',
    'sequential',
    ARRAY['research and analyze', 'comprehensive analysis', 'research crew'],
    true
) ON CONFLICT (name) DO UPDATE SET
    description = EXCLUDED.description,
    agents = EXCLUDED.agents,
    tasks = EXCLUDED.tasks,
    updated_at = NOW();

-- ===== 6. UPDATE AGENT METADATA =====
-- Add metadata to existing agents for better context

UPDATE agent_specs SET 
    metadata = '{"specialization": "financial", "requires_auth": false, "processing_time": "medium"}'
WHERE agent_type = 'financial_analyst';

UPDATE agent_specs SET 
    metadata = '{"specialization": "development", "requires_auth": false, "processing_time": "long"}'
WHERE agent_type = 'code_developer';

UPDATE agent_specs SET 
    metadata = '{"specialization": "research", "requires_auth": false, "processing_time": "short"}'
WHERE agent_type = 'data_researcher';

UPDATE agent_specs SET 
    metadata = '{"specialization": "management", "requires_auth": false, "processing_time": "medium"}'
WHERE agent_type = 'project_manager';

UPDATE agent_specs SET 
    metadata = '{"specialization": "communication", "requires_auth": false, "processing_time": "short"}'
WHERE agent_type = 'communication_specialist';

-- ===== 7. VERIFICATION QUERIES =====
-- These can be run to verify the seeding was successful

-- Verify agent-server mappings
DO $$
DECLARE
    mapping_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO mapping_count FROM agent_server_mappings;
    RAISE NOTICE 'Created % agent-server mappings', mapping_count;
END $$;

-- Verify learning patterns
DO $$
DECLARE
    pattern_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO pattern_count FROM learning_patterns;
    RAISE NOTICE 'Created % learning patterns', pattern_count;
END $$;

-- Verify workflows
DO $$
DECLARE
    workflow_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO workflow_count FROM workflows;
    RAISE NOTICE 'Created % workflows', workflow_count;
END $$;

-- Verify crew configs
DO $$
DECLARE
    crew_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO crew_count FROM crew_configs;
    RAISE NOTICE 'Created % crew configurations', crew_count;
END $$;

-- Insert completion marker
INSERT INTO mcp_configurations (name, description, is_active) 
VALUES ('dynamic_seeding_v1', 'Dynamic meta-agent data seeding completed successfully', true)
ON CONFLICT (name) DO UPDATE SET 
    description = EXCLUDED.description,
    updated_at = NOW();

-- Success message
DO $$
BEGIN
    RAISE NOTICE '✅ SEEDING COMPLETE: Dynamic meta-agent system is ready!';
    RAISE NOTICE '📊 Next steps: Update code to use dynamic loading instead of hard-coded agents';
    RAISE NOTICE '🔄 Ready for hot reload and dynamic configuration management';
END $$; 