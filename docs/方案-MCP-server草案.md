# MCP Server 设计草案

> 2026-07 | 目标:把平台的执行/报告/用例能力包成 MCP,让 coding agent(Claude Code / Codex)
> 直接调用——AI 写完代码 → 调平台跑回归 → 读结果 → 失败自愈,形成"AI 时代的验证层"闭环。

## 一、为什么做(定位)

平台的核心资产不是"又一个测试界面",而是**确定性的验证基础设施**:用例库(规格资产)、
v2 统一执行管道(秒级/免费/可重复)、真机池、CI 闸门。coding agent 是"工人",平台是
"工厂+仓库"——两者互补。MCP 是把这个仓库暴露给 agent 的标准接口。

一句话价值:**AI 编码工具越普及,能"贴需求→写码→自动回归→自愈"的平台越值钱。**
MCP 让平台成为 agent 的落地容器,而不是被 agent 替代。

## 二、边界(先划清不做什么)

- **只读多、写少**:优先暴露"触发执行 / 读报告 / 查用例 / 生成草稿"。**用例入库、AI 修复应用**
  这类写操作要么需人工评审(草稿 commit),要么给 agent 但带 dry-run + 审计。
- **不暴露** 用户管理、配置中心、设备管理等运维面——超出"验证"范畴,风险高。
- **不做** 破坏性批量操作(批量删用例/删报告)。

## 三、工具清单(基于真实 API)

| MCP 工具 | 映射后端 | 类型 | 说明 |
|---|---|---|---|
| `list_projects` | `GET /api/projects` | 读 | agent 先拿 project_id |
| `list_cases` | `GET /api/functional_cases`、`GET /api/cases` | 读 | 按 project/module/type 列用例 |
| `run_tests` | `POST /api/run_test` | 写(执行) | 传 project+category+case_ids,返回 report_id/task_id |
| `get_report` | `GET /api/reports/{id}` | 读 | 轮询报告状态与通过率 |
| `get_report_failures` | `GET /api/reports/{id}` + step 明细 | 读 | 返回失败用例的请求/响应/断言(agent 自愈的关键输入) |
| `diagnose_report` | `POST /api/functional_cases/ai_diagnose_report` | 写(AI) | 触发 AI 诊断,返回 ai_run_id |
| `apply_report_fixes` | `POST /api/functional_cases/ai_report_fix/apply` | 写(需确认) | 应用修复,带 verify 自动重跑;默认 dry-run |
| `generate_case_drafts` | `POST /api/ai/case-generation` | 写(AI) | 从需求生成草稿(进评审队列,不直接入库) |
| `list_case_drafts` | `GET /api/ai/case-drafts` | 读 | 查待评审草稿 |
| `get_coverage` | `GET /api/requirements/coverage` | 读 | 覆盖缺口,让 agent 知道该补哪 |

**关键设计**:`run_tests` + `get_report` + `get_report_failures` 三件套构成 agent 的核心回归回路;
`generate_case_drafts` 产出的是**草稿**,人类仍在评审环上把关"什么算对"——AI 可以提议,但入库要人点头。

## 四、闭环示例(agent 视角)

```
用户对 Claude Code:"给订单模块加了字段,确认没破坏回归"
  → agent 调 list_cases(module=订单)           # 知道有哪些回归用例
  → agent 调 run_tests(case_ids=[...])          # 触发,拿 report_id
  → agent 轮询 get_report(report_id)            # 等跑完
  → 若有失败:get_report_failures               # 拿真实请求/响应
      → agent 自己改代码修 bug,或
      → diagnose_report + apply_report_fixes(dry_run) 让平台给用例修复建议
  → agent 调 get_coverage                       # 新字段有没有对应用例?没有则
      → generate_case_drafts(需求)              # 生成草稿,提示用户去评审入库
```

## 五、技术方案

- **形态**:独立进程的 MCP server(Python,`mcp` SDK 或 stdio JSON-RPC),与 FastAPI 同仓、
  复用 `server/services/*`。**不新写业务逻辑**,只做"MCP 工具 → 现有 service/HTTP"的薄封装。
- **两种接法(二选一)**:
  1. **HTTP 客户端式**:MCP server 拿配置里的 base_url + API token,调平台 REST。好处:平台无改动、
     可跨机部署;成本:多一跳、要管 token。
  2. **进程内直连式**:MCP server import `server.services`,直接开 DB session。好处:快、无网络;
     成本:与平台强耦合、要共享部署环境。
  **建议先做 1(HTTP 式)**,解耦、好测,也天然支持"agent 在别的机器上"。
- **鉴权**:平台现在是 JWT Bearer(短期 access + 长期 refresh)。给 MCP 单独发一类
  **长效 service token / API key**(在配置中心加一张 api_keys 表,scope 限制到只读+执行),
  不要让 MCP 用会过期的用户 access token。← 这是落地前必须先补的后端能力。
- **返回裁剪**:报告/用例响应可能很大,MCP 工具要做**字段裁剪 + 分页**,只回 agent 决策需要的
  (失败用例的 method/path/status_code/断言 actual),别把整份 Allure JSON 塞给模型。

## 六、落地前的前置依赖(按顺序)

1. **API Key 机制**(必须先做):service token 签发 + scope 校验,替代 MCP 用用户 JWT。
2. **报告失败明细的精简读接口**:现有 `GET /api/reports/{id}` 可能不够结构化,
   补一个 `GET /api/reports/{id}/failures` 直接返回 agent 要的最小集。
3. **run_test 的同步等待选项 或 明确的轮询协议**:agent 需要知道"跑完了没",
   现在靠轮询 report status,MCP 工具封装好轮询 + 超时。

## 七、里程碑

- **M1(PoC,~1 周)**:HTTP 式 MCP server + 3 个只读工具(list_cases/get_report/get_coverage)
  + run_tests,用固定 token 打通"agent 触发回归、读结果"。做一个 Claude Code 里的 demo。
- **M2**:补 API Key 机制、失败明细接口、diagnose/apply(dry-run)。
- **M3**:generate_case_drafts + 评审回环;打包成可分发的 MCP 配置,写接入文档。

## 八、风险

- **写操作的安全**:apply_report_fixes / 入库必须默认 dry-run + 人工确认,否则 agent 可能污染用例库。
- **token 泄露面**:service token 权限要最小化(只读+执行,不含删除/配置)。
- **成本**:generate/diagnose 都烧 LLM token,MCP 层要有调用频率提示,避免 agent 循环狂刷。
- **定位漂移**:别把 MCP 做成"又一个 API 网关"。它的价值在于**恰好暴露 agent 做验证需要的那几个动作**,克制是特性。
