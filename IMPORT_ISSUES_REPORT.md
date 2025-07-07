# Import and Constructor Issues Report

## Summary

This report identifies all import issues and constructor mismatches in the modular micro-agent framework files.

## 🔍 Issues Found

### 1. **IntentAnalyzerAgent Constructor Mismatch**

**File:** `slack_meta_agent/src/slack_meta_agent/agents/intent_analyzer.py`
**Issue:** Constructor signature doesn't match usage in main_modular.py

**Current Constructor:**

```python
def __init__(self, patterns: Dict[str, Dict] = None, config: Dict[str, Any] = None):
```

**Expected Usage in main_modular.py (line 139):**

```python
self.intent_analyzer = IntentAnalyzerAgent(
    registry=self.registry,
    tool_discovery=self.tool_discovery,
    pool_manager=self.pool_manager,
    config=self.config,
)
```

**Solution:** Update constructor to match usage or update usage to match constructor.

---

### 2. **AgentRegistryAgent Constructor Mismatch**

**File:** `slack_meta_agent/src/slack_meta_agent/agents/registry.py`
**Issue:** Constructor signature doesn't match usage in main_modular.py

**Current Constructor:**

```python
def __init__(self, pool_manager=None, cache_ttl: int = 600):
```

**Expected Usage in main_modular.py (line 118):**

```python
self.registry = AgentRegistryAgent(
    pool_manager=self.pool_manager,
    db_ops=self.db_ops,
    config=self.config
)
```

**Solution:** Update constructor to accept `db_ops` and `config` parameters.

---

### 3. **PoolManagerAgent Constructor Mismatch**

**File:** `slack_meta_agent/src/slack_meta_agent/agents/pool_manager.py`
**Issue:** Constructor signature doesn't match usage in main_modular.py

**Current Constructor:**

```python
def __init__(
    self,
    mcp_app=None,
    max_pool_size: int = 10,
    health_check_interval: int = 300,
    recovery_delay: int = 30,
):
```

**Expected Usage in main_modular.py (line 127):**

```python
self.pool_manager_agent = PoolManagerAgent(
    registry=self.registry,
    mcp_app=self.mcp_app,
    pool_size=5,
    health_check_interval=300,
)
```

**Solution:** Update constructor to accept `registry` parameter and rename `max_pool_size` to `pool_size`.

---

### 4. **WorkflowManagerAgent Method Call Mismatch**

**File:** `slack_meta_agent/src/slack_meta_agent/agents/workflow_manager.py`
**Issue:** Method name mismatch in main_modular.py

**Current Constructor (Correct):**

```python
def __init__(self, pool_manager=None, db_ops=None, agent_registry=None, mcp_app=None):
```

**Expected Usage in main_modular.py (line 145):**

```python
self.workflow_manager = WorkflowManagerAgent(
    pool_manager=self.pool_manager,
    db_ops=self.db_ops,
    agent_registry=await self.registry.get_specs(),  # ❌ get_specs() doesn't exist
    mcp_app=self.mcp_app,
)
```

**Solution:** Change `get_specs()` to `get_agent_specs()` in main_modular.py.

---

### 5. **Method Interface Mismatches**

**Issue:** Some methods called don't exist or have different signatures:

1. **Intent Analyzer Interface:**

   - Called: `intent_analyzer.analyze(incoming)`
   - May need: `intent_analyzer.analyze(text, context)`

2. **Registry Interface:**

   - Called: `registry.get_specs()`
   - Should be: `registry.get_agent_specs()`

3. **Pool Manager Interface:**

   - Called: `pool_manager.get_agent(agent_type, request_id)`
   - Should be: `pool_manager.get_agent(agent_type, agent_spec, request_id)`

4. **Orchestrator Interface Mismatch:**

   - Orchestrator calls: `intent_analyzer.analyze(incoming)`
   - But IntentAnalyzerAgent expects: `analyze(message: IncomingMessage)`
   - However, the orchestrator also calls `analyze(text, context)` in some places

5. **Missing get_specs() method:**
   - Called: `registry.get_specs()`
   - Should be: `registry.get_agent_specs()`

---

## ✅ Files Without Issues

1. **types.py** - All imports are standard library ✓
2. **tool_discovery.py** - Constructor matches usage ✓
3. **slack_adapter.py** - Constructor matches usage ✓
4. **orchestrator.py** - Constructor matches usage ✓

---

## 🔧 Required Fixes

### Priority 1: Constructor Updates

1. **Fix IntentAnalyzerAgent constructor**
2. **Fix AgentRegistryAgent constructor**
3. **Fix PoolManagerAgent constructor**
4. **Verify WorkflowManagerAgent constructor**

### Priority 2: Method Interface Updates

1. **Standardize method signatures across all agents**
2. **Update main_modular.py calls to match actual interfaces**
3. **Add missing dependency injection methods**

### Priority 3: Import Verification

1. **Verify all database modules exist and are importable**
2. **Check MCP agent imports are correct**
3. **Ensure all type imports resolve correctly**

---

## 📋 Verification Checklist

- [ ] All constructors match their usage in main_modular.py
- [ ] All method calls use correct signatures
- [ ] All imports can be resolved
- [ ] Dependency injection is properly implemented
- [ ] Health check methods are consistent across all agents
- [ ] Cleanup methods are implemented where needed

---

## Next Steps

1. **Update constructor signatures to match usage patterns**
2. **Fix method interface mismatches**
3. **Test import resolution**
4. **Run integration test to verify all components work together**
