# AI 功能用例与元素库生成接口自动化用例——实施规划

> 版本：v1.0  
> 日期：2026-08-13  
> 状态：实施规划，尚未开始编码  
> 目标：根据“功能测试用例 + 元素库录制事实”生成可执行接口自动化用例；没有功能用例时，也可仅根据元素库反推接口场景。

## 1. 结论

本能力建议提供两种生成模式：

1. **功能用例 + 元素库（推荐）**：功能用例提供业务意图、前置条件和预期结果；元素库提供页面、元素、用户动作、页面跳转以及 XHR/Fetch 请求响应证据。
2. **仅元素库**：没有功能用例时，从主录制基线中的页面跳转、用户动作和 XHR/Fetch 调用图反推登录、查询、详情、创建、修改等接口流程。

生成结果不是自然语言，而是平台现有 v2 Runner 可以直接执行的：

```text
test_cases(case_type="api", source="ai_recorded_api")
  └─ test_steps(step_type="http_request", config, assertion, extract)
```

关键原则：

- AI 负责“理解业务流程、规划测试场景、选择证据”；
- 平台负责“构建观测契约、编译步骤、校验变量、阻止虚构接口”；
- 所有候选先进入草稿审阅，不直接污染正式用例库；
- 生成、试跑和正式执行继续共用现有 `CaseExecutor → StepDispatcher → HttpRequestStepRunner` 链路。

## 2. “元素库”在本能力中的范围

仅有 CSS、XPath、ID 等定位器，不能生成可靠的接口用例。本项目的元素库已经与 UI 录制中心合并，因此生成时实际读取以下事实：

| 事实 | 当前数据 | 用途 |
|---|---|---|
| 页面与状态 | `UiPageSnapshot` | 确定业务页面、URL、状态版本 |
| 元素与定位器 | `UiElement` / `UiElementOccurrence` | 把功能步骤映射到真实页面操作 |
| 用户操作 | `UiRecordedAction` | 识别点击、输入、提交的顺序 |
| 页面跳转 | `UiPageTransition` | 还原业务流程图 |
| 网络请求响应 | `UiMockExchange` | 提取 method、URL、请求、响应、状态码和时序 |
| 录制上下文 | `UiRecordingSession` / Context | 记录浏览器、环境、主基线版本和来源 |

因此，“仅元素库生成”实际含义是“根据元素库绑定的主录制基线和网络录制事实生成”，不是根据按钮名字猜接口。

## 3. 输入模式

### 3.1 模式 A：功能用例 + 元素库

输入：

- 一个或多个 `case_type=functional` 的功能用例；
- 当前项目 Web 主录制基线及已合并补充会话；
- 目标模块、环境、模型、覆盖度；
- 可选 OpenAPI/Swagger、接口文档和项目变量池。

处理：

```text
功能步骤“输入关键词并点击搜索”
  → 语义匹配“项目管理 / 搜索输入框 / 搜索按钮”
  → 找到录制动作窗口
  → 找到相邻 GET /api/projects?keyword=...
  → 编译正常、空关键词、无结果、非法分页等接口场景
```

优点：业务意图最明确，可知道“为什么调用接口”和“期望业务结果”。

### 3.2 模式 B：仅元素库

输入：

- 项目 Web 主录制基线；
- 可选页面范围、标签、CRUD 风险级别、覆盖度；
- 可选 OpenAPI/Swagger。

处理：

1. 按页面、动作和时间窗口把 `UiMockExchange` 聚类；
2. 识别登录、列表、详情、筛选、分页、创建、修改等业务流；
3. 对同一 method/path 的多个样本合并为“观测契约”；
4. 生成已存在证据支持的正常、边界、鉴权和错误场景。

限制：没有功能用例时，业务预期只能来自接口响应、页面状态和命名语义，置信度低于模式 A。高风险写操作默认只生成草稿，不自动试跑。

## 4. 证据与置信度

每条生成用例必须带证据等级：

| 等级 | 条件 | 是否可自动试跑 |
|---|---|---|
| A | 功能用例 + 元素/动作 + XHR/Fetch + OpenAPI 全部匹配 | 是 |
| B | 功能用例 + XHR/Fetch，或元素流程 + OpenAPI 匹配 | 是，写操作需安全门禁 |
| C | 只有录制请求响应样本，没有正式接口契约 | 只允许已观测正常路径；其余人工确认 |
| D | 只有页面/元素语义，没有网络证据 | 阻断，不能生成可执行接口步骤 |

硬约束：

- method/path 必须来自 OpenAPI 或 `UiMockExchange`；
- 参数名必须来自契约或真实请求，不允许 AI 自由创造；
- 断言字段必须在响应 schema 或已脱敏样本中出现；
- 敏感值只能引用变量池，如 `${token}`、`${password}`；
- 证据不足时生成 `blocking_warnings`，不能标记为可执行。

## 5. 核心流程

```text
选择输入模式与范围
  → 构建录制证据图
  → 构建观测 API 契约
  → AI 规划测试点
  → 确定性编译 http_request 步骤
  → 静态预检与去重
  → 安全探测（可选）
  → 草稿审阅
  → 批量入库
  → 现有执行链路试跑
  → 报告和失败反馈回流
```

### 5.1 录制证据图

新增 `recorded_api_evidence_builder`，把事实组织成图：

```text
FunctionalCase
  → BusinessStep
  → PageSnapshot
  → UiElement
  → UiRecordedAction
  → UiMockExchange
  → PageTransition / ResponseState
```

匹配顺序：

1. 功能步骤中的页面名、元素名与别名精确命中；
2. 操作类型、语义名称和页面路由加权检索；
3. 动作前后时间窗内的网络请求关联；
4. method/path、请求参数和响应字段一致性复核；
5. 一个步骤存在多个候选时保留 Top 3，由 AI 排序但不得绕过事实校验。

### 5.2 观测 API 契约

新增 `recorded_api_contract_builder`，将多个 `UiMockExchange` 合并为现有 `api_case_contract` 能识别的目录：

- URL 归一化：把动态 ID 归纳为 `/projects/{project_id}`；
- 合并 query/path/header/body 参数及出现频率；
- 根据真实 JSON 值推断基础类型、可空性和枚举候选；
- 汇总状态码、响应字段和样本；
- 与 OpenAPI 合并时以 OpenAPI 为结构事实，以录制样本补充 example；
- 每个字段保留 `mock_exchange_ids` 和 `source_session_ids` 作为追溯证据。

该产物继续交给现有：

- `server/services/api_case_contract.py::compile_generated_case`；
- `server/services/generation_probe_refine.py`；
- 变量来源、隔离性和 preflight 校验。

## 6. 场景生成规则

### 6.1 有功能用例

每条业务流程至少规划：

- 主成功路径；
- 功能用例已明确的异常路径；
- 功能步骤涉及的必填、边界、枚举和鉴权场景；
- 跨接口变量提取与传递，如登录 token、创建后 ID；
- 必要清理步骤，或显式标记“需要隔离数据”。

### 6.2 无功能用例

按观测接口生成：

- 每个已观测 operation 的 smoke 用例；
- 查询接口：分页、筛选、空结果；
- 详情接口：有效 ID、录制证据支持的不存在场景；
- 鉴权接口：未认证、失效 token（只有契约/样本支持时）；
- 写接口：只生成已观测成功路径；负向场景和自动试跑默认关闭；
- 相邻接口根据提取变量组合成业务链，如“登录 → 列表 → 详情”。

## 7. 复用与改造范围

### 7.1 直接复用

- `AI_FEATURE_API_CASE_GEN`：继续作为 API 生成任务类型；在 `input_payload.generation_source` 区分 `document`、`functional_library`、`library_only`；
- `AiRun`：保存任务状态、模型、成本、输入证据和完整草稿；
- `ai_generate_outline` / `ai_generate_batch` 的规划与审阅交互；
- `api_case_contract` 的结构化契约、编译和校验；
- 现有 `AiGeneratedCase.compiled_case` 和前端草稿列表；
- `casesApi.create`、统一编号、批量试跑和执行报告。

### 7.2 新增服务

| 文件 | 职责 |
|---|---|
| `server/services/recorded_api_evidence_builder.py` | 功能步骤、页面、元素、动作、网络请求关联 |
| `server/services/recorded_api_contract_builder.py` | XHR/Fetch 样本合并为观测契约 |
| `server/services/recorded_api_scenario_planner.py` | 两种输入模式的测试点规划和置信度 |
| `ai_gateway/prompts/recorded_api_outline.md` | 基于证据图规划接口场景 |
| `ai_gateway/prompts/recorded_api_batch.md` | 将场景映射为契约编译输入 |

首期不新增数据库表。草稿和历史继续保存在 `AiRun.output_payload.draft`；正式用例通过 `generation_metadata` 记录来源：

```json
{
  "generator": "recorded_api_case_gen",
  "generation_source": "functional_library",
  "functional_case_ids": [101],
  "recording_session_ids": [12, 15],
  "element_ids": [33, 34],
  "mock_exchange_ids": [9001, 9002],
  "contract_hash": "...",
  "evidence_confidence": "A",
  "generation_run_id": 88
}
```

## 8. API 设计

建议新增薄编排入口，不复制现有生成器：

```http
POST /api/ai/recorded-api-case-generation
```

```json
{
  "project_id": 1,
  "module_id": 10,
  "source_mode": "functional_library",
  "functional_case_ids": [101, 102],
  "page_keys": [],
  "baseline_version": 3,
  "coverage": "standard",
  "model_name": "default",
  "allow_write_probe": false,
  "user_prompt": ""
}
```

`functional_case_ids` 在 `library_only` 模式可为空。返回 `{ai_run_id}`，前端继续使用通用 AI 任务轮询。

辅助接口：

```text
GET  /api/ai/recorded-api-case-generation/{run_id}          读取证据摘要与草稿
POST /api/ai/recorded-api-case-generation/{run_id}/recheck  重新构建契约并校验
POST /api/ai/recorded-api-case-generation/{run_id}/commit   后端事务化批量入库
```

## 9. 页面方案

入口位于“API 自动化用例”页的“AI 生成”菜单：

1. `根据功能用例和元素库生成`；
2. `仅根据元素库生成`。

向导分四步：

1. **选择来源**：功能用例、主基线版本、页面范围；
2. **证据预览**：匹配到的页面、元素、接口数量和未匹配步骤；
3. **场景审阅**：成功/异常/边界/鉴权测试点；
4. **用例审阅**：method/path、参数、提取、断言、证据等级和阻断原因。

草稿卡片必须能展开查看“为什么生成”：功能步骤、元素、录制动作和 XHR/Fetch 样本均可追溯。

## 10. 安全边界

- 默认禁止对生产环境执行生成期探测；
- `POST/PUT/PATCH/DELETE` 默认不自动试跑，除非测试环境明确开启并存在数据清理策略；
- 密码、token、Cookie、身份证、银行卡等继续使用现有脱敏策略；
- 录制样本中的真实凭据不进入 Prompt；
- 对未命中契约的接口、字段和状态码直接阻断；
- AI 输出不能绕过 `compile_generated_case` 和 preflight；
- 提交前展示将新增、覆盖、跳过的用例数量，不静默修改现有用例。

## 11. 分阶段实施

### M1：证据与契约（后端）

- 构建主基线页面、元素、动作、请求关联图；
- XHR/Fetch → 观测 API 契约；
- 提供证据预览接口；
- 单测动态路径、参数合并、脱敏和来源追踪。

### M2：两种来源生成

- 接入功能用例语义匹配；
- 实现 `library_only` 流程聚类；
- 新增 Prompt；
- 输出场景、置信度和阻断原因。

### M3：确定性编译与草稿

- 接入现有 `api_case_contract`；
- 生成 `compiled_case`；
- 完成变量、断言、隔离性、去重和安全探测；
- 草稿历史写入 `AiRun`。

### M4：前端闭环

- 两个生成入口与四步向导；
- 证据链查看、人工编辑、批量入库；
- 可执行用例试跑；
- 失败报告关联回生成证据。

### M5：反馈优化

- 统计生成采纳率、人工修改率、首次试跑通过率；
- 执行失败区分“产品缺陷 / 用例错误 / 环境错误”；
- 定位器、接口契约或功能用例变化时提示草稿过期，不自动覆盖。

## 12. 验收标准

1. 有功能用例时，至少 90% 的明确业务步骤能关联到页面/动作/接口或给出明确未匹配原因；
2. 无功能用例时，可以从主基线生成每个已观测 operation 的 smoke 草稿；
3. 生成用例的 method/path 100% 来自 OpenAPI 或录制请求；
4. 所有入库用例通过 `compile_generated_case` 和 preflight；
5. 每条用例可以追溯到功能用例、页面、元素、动作和网络证据中的至少一项；
6. 未达到 C 级证据的候选不能写入为可执行用例；
7. 敏感信息不出现在 Prompt、草稿、日志或报告；
8. 只读请求可一键试跑，写请求默认要求人工确认；
9. 正式执行继续走唯一 v2 Runner，不新增旁路；
10. 生成历史可以恢复、重新校验、再次提交且不会重复创建相同用例。

## 13. 推荐实施顺序

优先做 `M1 → library_only smoke → 功能用例映射 → 完整前端`。先证明录制的 XHR/Fetch 能稳定编译成现有 API 步骤，再增加 AI 语义组合；这样即使模型不可用，平台仍能提供基于事实的接口清单和 smoke 草稿，不会把核心正确性押在 Prompt 上。
