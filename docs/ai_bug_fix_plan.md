# AI 一键修复 Bug 方案

> 基于 AGENTS.md 的 AI 任务统一范式，在 Bug 列表中添加 AI 直接修复 Bug 的能力。
> 详细实施按照 [`../AGENTS.md`](../AGENTS.md) 的规范。

---

## 一、功能概述

用户在 Bug 列表中点击"AI 修复"，选择智能体（LLM / CLI），系统自动拉取代码仓库 → AI 分析修复 → git commit + push → Bug 标记"已修复"。无 Git 仓库的项目仅生成修复建议文本。

---

## 二、智能体类型

| 智能体 | 类型 | 执行方式 | Docker 可用 |
|--------|------|----------|------------|
| LLM Agent（通用） | `llm` | `ai_gateway.chat_json("bug_fix", ...)` + RAG 代码上下文 → 生成 unified diff → `patch` apply | ✓ 开箱即用 |
| OpenCode | `llm` | 同 LLM Agent，使用本项目 `coding_agent` 的 RAG + diff 能力 | ✓ 开箱即用 |
| Codex | `cli` | `subprocess.run(["codex", ...], cwd=repo_dir)` | ✗ 需装 Node + npm 包 |
| Claude Code | `cli` | `subprocess.run(["claude", "-p", ...], cwd=repo_dir)` | ✗ 需装 Node + npm 包 |

**主路径 = LLM Agent**（Docker + 本地都能跑，不用额外安装工具）。

---

## 三、系统链路

```
前端 Bug 列表 [AI修复] 按钮
  → BugFixDialog（智能体选择）
    → POST /api/tasks/{id}/ai-fix  { agent_name }
      → 创建 AiRun(status=pending, feature='bug_fix')
      → Celery: run_bug_fix_task.delay(ai_run_id)
        ├─ 有 Git 配置 ──────────────────────────
        │  ① GitOps.ensure_clone()
        │  ② GitOps.temp_branch("fix/bug-{id}-{ts}")
        │  ③ _execute_agent(bug, agent_cfg, repo_dir)
        │     ├─ LLM: RAG 检索 → chat_json → diff → patch apply
        │     └─ CLI: subprocess.run → 直接修改文件
        │  ④ GitOps.commit_all("fix: {bug.title}")
        │  ⑤ GitOps.push(branch)
        │  ⑥ Task.status = dev_done, fix_description / fix_commit_sha 写入
        │
        └─ 无 Git 配置 ───────────────────────────
           ① _execute_agent(bug, agent_cfg, None)
           ② 修复建议写入 Task.metadata.fix_suggestion

      前端轮询 GET /api/ai/runs/{id}
        → success: 刷新 Bug 列表，显示"已修复"状态
        → failed:  toast 错误信息

POST /api/tasks/{id}/ai-fix/rollback
  → git push origin --delete <fix_branch>
  → Task.status → 原状态，清空 fix_* 字段
```

---

## 四、数据库改动

### 4.1 Task 表新增字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `fix_description` | Text, nullable | AI 写的修复说明 |
| `fix_commit_sha` | String(64), nullable | Git commit SHA |
| `fix_commit_branch` | String(200), nullable | 临时分支名 |
| `fix_suggestion` | Text, nullable | 无 Git 时的修复建议文本 |
| `fix_agent_used` | String(50), nullable | 使用的智能体名称 |
| `fix_ai_run_id` | Integer FK → ai_runs.id, nullable | 关联 AI 运行记录 |

### 4.2 AiRun feature 常量

```python
FEATURE_BUG_FIX = "bug_fix"
```

### 4.3 智能体配置（存 config_store，category = `bug_fix_agent`）

```json
{
  "name": "opencode",
  "agent_type": "llm",
  "enabled": true,
  "provider": "anthropic",
  "model": "claude-3-5-sonnet-20241022",
  "prompt_feature": "bug_fix"
}
```

```json
{
  "name": "codex",
  "agent_type": "cli",
  "enabled": true,
  "command": "codex exec --model gpt-4o '{{prompt}}'",
  "check_cmd": "which codex"
}
```

---

## 五、文件清单

### 新建文件

| 文件 | 说明 |
|------|------|
| `server/services/bug_fix_service.py` | Agent 抽象 + 调度 + GitOps 编排 |
| `server/api/bug_fix.py` | 3 个 REST 端点 |
| `tasks/bug_fix_task.py` | Celery 异步修复任务 |
| `ai_gateway/prompts/bug_fix.md` | LLM 修复 prompt 模板 |
| `frontend/src/pages/versions/tabs/BugFixDialog.tsx` | 智能体选择弹窗 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `database/models/task.py` | 加 fix_* 字段 |
| `database/models/ai_run.py` | 加 FEATURE_BUG_FIX |
| `server/api/__init__.py` | 注册 bug_fix 路由 |
| `tasks/__init__.py` | 导出 bug_fix_task |
| `celery_app.py` | 注册 task |
| `frontend/src/types/domain.ts` | 加 BugFix 相关类型 |
| `frontend/src/lib/api.ts` | 加 bugFixApi |
| `frontend/src/pages/versions/tabs/BugTab.tsx` | 加 [AI修复] 按钮 |
| `frontend/src/pages/tasks/TaskDetailPage.tsx` | 展示修复结果 + 回滚按钮 |

---

## 六、API 接口

### `POST /api/tasks/{task_id}/ai-fix`

触发 AI 修复 Bug。

**请求体：**
```json
{
  "agent_name": "opencode"
}
```

**响应：**
```json
{
  "status": "success",
  "data": {
    "ai_run_id": 42,
    "celery_task_id": "abc-123"
  }
}
```

### `POST /api/tasks/{task_id}/ai-fix/rollback`

回滚 AI 修复。

**请求体：**
```json
{
  "ai_run_id": 42
}
```

**响应：**
```json
{
  "status": "success",
  "data": {
    "message": "已回滚修复，分支 fix/bug-5-1747200000 已删除"
  }
}
```

### `GET /api/ai/bug-fix-agents`

获取可用智能体列表。

**响应：**
```json
{
  "status": "success",
  "data": {
    "agents": [
      { "name": "opencode", "agent_type": "llm", "enabled": true, "available": true },
      { "name": "codex", "agent_type": "cli", "enabled": true, "available": false }
    ]
  }
}
```
