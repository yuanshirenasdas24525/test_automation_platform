# 平台 MCP Server（M1 PoC）

把平台的"验证能力"（触发回归 / 读报告 / 查用例 / 查覆盖率）以 MCP 工具的形式暴露给
coding agent（Claude Code / Codex 等）。设计草案与边界见
[docs/方案-MCP-server草案.md](../docs/方案-MCP-server草案.md)。

## 工具清单

| 工具 | 说明 | 映射接口 |
|---|---|---|
| `list_projects` | 列项目，拿 project_id | `GET /api/projects/list` |
| `list_modules` | 项目模块扁平列表 | `GET /api/modules` |
| `list_cases` | 列用例（功能用例分页 / 自动化用例按模块层级） | `GET /api/functional_cases`、`GET /api/content/{pid}` |
| `run_tests` | 触发自动化执行，返回 report_id | `POST /api/run_test` |
| `get_report` | 读报告（默认只回失败/错误步骤，字段裁剪） | `GET /api/reports/{id}` |
| `get_report_failures` | 失败明细最小集（按用例分组） | `GET /api/reports/{id}/failures` |
| `get_coverage` | 需求-用例覆盖缺口 | `GET /api/requirements/coverage` |
| `list_ai_models` | 平台配置的 AI 模型列表 | `GET /api/ai-models` |
| `diagnose_report` | 报告失败用例 AI 诊断（异步） | `POST /api/functional_cases/ai_diagnose_report` |
| `get_ai_run` | 轮询 AI 任务状态/结果 | `GET /api/ai/runs/{id}` |
| `apply_report_fixes` | 应用 AI 修复（写用例，带 preflight/verify/回滚） | `POST /api/functional_cases/ai_report_fix/apply` |

核心回归回路：`run_tests` → 轮询 `get_report`（status 不再是 running）→ `get_report_failures` 读失败明细。

## 配置（环境变量）

| 变量 | 说明 |
|---|---|
| `TAP_BASE_URL` | 平台地址，默认 `http://127.0.0.1:54351` |
| `TAP_API_KEY` | **推荐**：长效 API Key（`tap_` 开头），scope 受限 |
| `TAP_TOKEN` | 固定 access_token（临时调试用） |
| `TAP_USERNAME` / `TAP_PASSWORD` | 平台账号密码；server 自动登录，401 时自动重登 |

### 签发 API Key（管理员）

```bash
# 用管理员 JWT 调一次；scopes 可选 read（GET）/ execute（run_test）/ ai（诊断+应用修复）
curl -X POST http://127.0.0.1:54351/api/api-keys \
  -H "Authorization: Bearer <admin_access_token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "mcp-server", "scopes": ["read", "execute", "ai"]}'
# 响应里的 data.api_key 只出现这一次，立即保存到 TAP_API_KEY
```

API Key 的硬边界：摸不到 `/api/auth`、`/api/api-keys`、`/api/users`、`/api/roles`；
非 GET 只放行 scope 显式允许的那几个路径。吊销：`DELETE /api/api-keys/{id}`。

## 在 Claude Code 中接入

```bash
claude mcp add test-platform \
  -e TAP_BASE_URL=http://127.0.0.1:54351 \
  -e TAP_USERNAME=<账号> \
  -e TAP_PASSWORD=<密码> \
  -- /path/to/test_automation_platform/venv/bin/python -m mcp_server.server
```

或在项目 `.mcp.json` 里：

```json
{
  "mcpServers": {
    "test-platform": {
      "command": "/path/to/venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/test_automation_platform",
      "env": {
        "TAP_BASE_URL": "http://127.0.0.1:54351",
        "TAP_USERNAME": "<账号>",
        "TAP_PASSWORD": "<密码>"
      }
    }
  }
}
```

接入后在对话里直接说，例如："给订单模块加了字段，帮我跑一遍 api 回归确认没破坏"——
agent 会自己 `list_cases` → `run_tests` → 轮询 `get_report`。

## 边界（刻意不做的）

- 不暴露用户管理、配置中心、设备管理、删除类操作；
- 用例入库 / AI 修复应用等写操作留给 M2+（需 dry-run + 人工评审）；
- server 只做 HTTP 薄封装：不 import `server.services`、不开 DB session。
