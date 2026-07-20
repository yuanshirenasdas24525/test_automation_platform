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
| `get_coverage` | 需求-用例覆盖缺口 | `GET /api/requirements/coverage` |

核心回归回路：`run_tests` → 轮询 `get_report`（status 不再是 running）→ 读失败步骤明细。

## 配置（环境变量）

| 变量 | 说明 |
|---|---|
| `TAP_BASE_URL` | 平台地址，默认 `http://127.0.0.1:54351` |
| `TAP_TOKEN` | 固定 access_token（M1 用法；有此变量则不走账号密码） |
| `TAP_USERNAME` / `TAP_PASSWORD` | 平台账号密码；server 自动登录，401 时自动重登 |

> M1 用固定 token / 账号密码打通；scope 受限的长效 API Key 机制是 M2 前置任务
>（见草案第六节），落地后应替换掉账号密码方式。

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
