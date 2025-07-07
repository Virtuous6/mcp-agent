-- Migration: Create agent_specs table for enhanced agent configuration
-- Version: 002
-- Description: Adds CrewAI-style agent specifications with rich personas and configuration

CREATE TABLE IF NOT EXISTS agent_specs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    backstory TEXT,
    goal TEXT,
    
    -- Behavior Configuration
    allow_delegation BOOLEAN DEFAULT false,
    verbose BOOLEAN DEFAULT true,
    cache BOOLEAN DEFAULT true,
    
    -- LLM Configuration
    llm_provider TEXT DEFAULT 'openai',
    llm_model TEXT DEFAULT 'gpt-4o-mini',
    temperature FLOAT DEFAULT 0.3 CHECK (temperature >= 0 AND temperature <= 1),
    max_tokens INTEGER DEFAULT 2000 CHECK (max_tokens > 0),
    
    -- Execution Settings
    max_iterations INTEGER DEFAULT 10 CHECK (max_iterations > 0),
    tools TEXT[], -- Array of tool IDs
    memory_policy JSONB DEFAULT '{}',
    timeout_ms INTEGER DEFAULT 30000 CHECK (timeout_ms > 0),
    
    -- Advanced Prompting
    system_prompt TEXT,
    few_shot_examples JSONB DEFAULT '[]',
    constraints TEXT[],
    output_format TEXT DEFAULT 'markdown',
    
    -- Legacy compatibility
    capabilities TEXT[],
    is_dynamic BOOLEAN DEFAULT false,
    
    -- Metadata
    version TEXT DEFAULT '1.0.0',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create index for faster lookups
CREATE INDEX idx_agent_specs_name ON agent_specs(name);

-- Create update trigger for updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_agent_specs_updated_at 
    BEFORE UPDATE ON agent_specs 
    FOR EACH ROW 
    EXECUTE FUNCTION update_updated_at_column();

-- Insert default enhanced agent specs
INSERT INTO agent_specs (
    id, name, role, backstory, goal, llm_model, temperature, tools, constraints, system_prompt
) VALUES 
(
    'intent_analyzer',
    'Intent Classification Specialist',
    'Analyze user messages and classify intent with high accuracy',
    'You are a linguistic expert trained in understanding user intent across various communication styles. You''ve analyzed millions of conversations and can quickly identify patterns, context, and underlying needs.',
    'Accurately classify user intent to enable optimal agent selection and execution strategy',
    'gpt-4o-mini',
    0.1,
    ARRAY['pattern_matcher', 'context_analyzer'],
    ARRAY[
        'Prefer SINGLE_AGENT for simple requests',
        'Only suggest ORCHESTRATED for genuinely complex multi-step tasks',
        'Always provide confidence assessment'
    ],
    'You are an expert at understanding user intent. Focus on: 1) Identifying the core request, 2) Determining complexity, 3) Selecting appropriate execution strategy.'
),
(
    'orchestrator',
    'Master Execution Planner',
    'Build and execute sophisticated multi-agent plans',
    'You are a strategic mastermind with deep experience in project management and systems thinking. You excel at breaking down complex problems into actionable steps and coordinating teams.',
    'Ensure every user query is answered completely through optimal agent coordination',
    'gpt-4o',
    0.1,
    ARRAY['plan_builder', 'task_monitor', 'result_validator'],
    ARRAY[
        'Build minimal but complete plans',
        'Validate results match user needs',
        'Monitor execution for quality'
    ],
    'You are the execution brain. Your job is to: 1) Analyze queries deeply, 2) Build minimal but complete plans, 3) Monitor execution, 4) Validate results match user needs.'
),
(
    'data_researcher',
    'Senior Research Analyst',
    'Gather real-time data and conduct thorough research',
    'You are a meticulous researcher with a background in investigative journalism and data science. You never accept surface-level information and always dig deeper to find accurate, current data.',
    'Provide accurate, real-time information with proper sources',
    'gpt-4o',
    0.2,
    ARRAY['brave_search', 'fetch', 'supabase'],
    ARRAY[
        'Always use tools for current data',
        'Provide specific numbers and details',
        'Include data freshness timestamps'
    ],
    'You MUST use your tools to get real-time data. Never guess or use training data for current information. Always: 1) Search for sources, 2) Fetch actual data, 3) Provide specific details with timestamps.'
); 