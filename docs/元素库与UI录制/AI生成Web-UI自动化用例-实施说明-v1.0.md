# AI 生成 Web UI 自动化用例——实施说明 v1.0

> 日期：2026-08-14
> 范围：首期仅 Web；Android/iOS 不在本期生成范围
> 状态：已实现

## 1. 交付范围

元素库 Web 工作区新增“AI 生成 UI 用例”入口，支持：

1. `功能用例 + 元素库`：功能用例提供业务意图与预期，元素库和录制基线提供页面、元素、定位器、动作及页面跳转证据；
2. `仅元素库`：没有功能用例时，根据真实元素与录制事实生成 smoke、导航、表单和页面状态场景；
3. 选择 AI 模型、功能用例、页面范围、生成数量、关键结构断言和可选视觉回归；
4. AI 结果先保存为待评审草稿，支持查看证据、警告、人工接管原因、动态变量和执行步骤；
5. 草稿评审通过后批量写入指定模块的正式 `web` 用例。

单批默认只选当前页面，最多选择 20 页，推荐围绕一个业务流程选择 1～5 页，避免把整个项目一次塞入模型导致证据稀释。

## 2. 生成与编译链路

```text
功能用例（可选） + Web 元素库 + 最新页面快照
  + 主录制/已合并补充录制中的动作、跳转和网络摘要
        ↓
AI 只输出 action + element_id/page_key/snapshot_id
        ↓
后端事实编译器校验归属并从元素库选择真实定位器
        ↓
ui_automation_case_drafts（pending）
        ↓ 人工修改/通过/拒绝
test_cases(case_type=web, source=ai_m8_web) + test_steps
        ↓
现有 CaseExecutor → StepDispatcher → Web Runner
```

生成任务继续使用统一 `AiRun → Celery → handler` 异步范式，feature 为 `web_ui_case_gen`。草稿记录模型、Prompt 哈希/版本、关联功能用例、元素 ID、页面 key、快照 ID、可信度、警告和人工接管原因。

## 3. 事实与安全门禁

- AI 不接收定位器具体值，只接收元素 ID、语义、类型和定位器质量摘要；
- AI 不允许输出 CSS、XPath、ID 等原始定位器，后端只接受上下文中真实存在的 `element_id`；
- 编译器从元素库选择主定位器，优先唯一、已验证和高分候选；
- 元素不存在、定位器不可执行或证据不足时，生成跳过态的人工处理步骤，不虚构定位器；
- 允许动作仅包含 `goto/click/input/select/wait/assert_visible/assert_text/visual_assert/manual`；
- 不允许生成任意 JavaScript、Python、Shell、SQL 或设备变更动作；
- 需要人工处理的草稿入库后整条用例默认 `skip=true`，防止自动执行半成品。

## 4. 动态数据与特殊控件

### 4.1 动态输入

录制时的用户名、密码、手机号、邮箱、项目名等输入不会直接写死。编译器将字面值转换成 `${variable}`，并把默认值写入 `test_cases.variables`；密码默认值留空。评审人可在草稿中修改变量。

### 4.2 验证码和滑块

元素语义或类型命中验证码、滑块、人机验证等特征时，生成“测试环境绕过或人工接管”步骤。首期不尝试识别验证码，也不生成猜测的拖动轨迹。

### 4.3 其他复杂控件

iframe、Shadow DOM、新窗口、文件上传、日期和富文本等若现有元素定位器与 Runner 证据不足，首期进入待补录/人工处理，不通过通用脚本绕过白名单。后续应按独立 step type 和 Runner 扩展。

## 5. 断言策略

### 5.1 默认断言

- 功能断言：使用 `web_assert_text` 对业务结果文本做 equals/contains/regex；
- 关键结构断言：使用 `web_wait(state=visible)` 验证标题、关键按钮、表格或输入框可见；
- 不默认断言每个 CSS 属性或像素，避免脆弱用例。

### 5.2 可选视觉回归

新增 `web_assert_visual`：

- 基线必须来自项目内已录制页面截图；
- 固定浏览器视口后对比实际截图；
- 支持差异比例阈值、单像素容差和矩形 masks；
- 报告附带 baseline、actual、diff；
- 页面尺寸不一致直接失败，并提示重新确认视口和基线。

视觉回归与功能断言相互独立，默认关闭，避免动态内容造成误报。

## 6. 主要接口与数据

| 能力 | 接口 |
|---|---|
| 提交生成 | `POST /api/ai/web-ui-cases/generate` |
| 查询草稿 | `GET /api/ai/web-ui-cases/drafts` |
| 修改草稿 | `PATCH /api/ai/web-ui-cases/drafts/{id}` |
| 拒绝草稿 | `POST /api/ai/web-ui-cases/drafts/{id}/reject` |
| 批量入库 | `POST /api/ai/web-ui-cases/drafts/commit` |

新增表：`ui_automation_case_drafts`。正式用例继续使用现有 `test_cases + test_steps`，没有新增执行入口。

## 7. 验收结果

- Alembic 迁移已应用到 PostgreSQL，迁移链为单一 head：`ui_auto_case_draft_001`；
- OpenAPI 已注册 5 个 Web UI 生成与草稿接口；
- 编译器与视觉 Runner 定向测试 26 条通过；
- 前端 ESLint、TypeScript typecheck、Vite production build 通过；
- 本地已登录页面验收通过：元素库入口可见，生成弹窗可加载模型、功能用例和全部已录制页面，默认仅勾当前页面，功能用例可搜索，结构和视觉断言选项及安全提示可见；
- 未在验收中提交真实 AI 任务，避免消耗已配置模型额度和写入测试草稿。首次人工验收时建议选择一条登录功能用例 + 登录页，生成 1 条进行闭环验证。
