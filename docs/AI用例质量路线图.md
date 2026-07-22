# AI 用例质量路线图

> 2026-07 | 目标:让 AI 生成的用例采纳率持续上升,守住用户信任
> 原则:信任是不对称的——10 条好用例攒的信任,一批垃圾一次败光。早期宁可少而准。

## 优先级排序

### P0-1 生成前上下文注入 `🔶 记忆层注入 + few-shot 样例已上线(prompt_version v2);接口 batch 步骤已补注入(2026-07-12);响应约定回流自动化待做`
垃圾用例的头号成因是模型只拿到一句需求。生成 prompt 必须注入项目真实事实:

- OpenAPI / 接口定义、DB schema(用例引用的 endpoint、字段必须真实存在)
- 同模块存量用例作 few-shot 风格样例
- `project_context` + RAG 索引(`code_chunk` / `rag_index_task`)已具备,接进 `ai_case_generation` 链路即可

**2026-07-12 修复记录(登录用例首跑仅 17% 的复盘)**:
根因是 extract/assertion 的 JSONPath 在 batch 步骤生成,而当时只给"大纲"步骤注入了项目上下文,
batch 步骤看不到真实响应结构;加上 batch prompt 示例用通用惯例 `$.code`/`$.data.token`,模型照假惯例猜,
与真实 `$.status`/`$.data.access_token`/中文错误全对不上 → 一个信封认知错误级联 20+ 条失败。
已修:①`ai_generate_batch` 给 `interface_case_batch` 也注入 PROJECT_CONTEXT;②prompt 明确"示例仅格式,
真实路径以项目约定为准,禁止猜";③新增 `scripts/learn_response_convention.py`——从真实报告响应提炼
"响应约定"回流记忆层(api_contract)。

### P0-1b 响应约定自动回流 `✅ 已上线(2026-07 补齐成功状态码约定)`

**现状**：`run_test_task` 收尾已自动派发 `learn_response_convention_task`（api 报告 + 7 天节流），
提炼结果入 `project_contexts(api_contract)`，经 `_project_context_block` 注入生成 prompt。

**2026-07-23 修复**：项目 1 首次学习只产出一条「错误响应结构约定」，缺成功响应约定，
导致后续生成的用例大批把 `POST /api/users` 断言成 201（实际返回 200）——58 条同因失败。
根因两处，均已修：
① `collect_report_samples` 按 id 顺序取前 N 条，一份"大部分挂了"的报告样本全是错误响应
   → 改为成功/失败**均衡采样**；
② prompt 把状态码约定藏在错误响应那条的子项里 → 改为**必须单独成条**，
   并要求显式点出与 REST 惯例的差异（POST→201、DELETE→204 这类最容易想当然写错）。
重学后产出「成功状态码约定（非标准REST）：一律 status_code==200，禁止使用 201」，已验证注入生效。

<details><summary>原始动机（保留）</summary>
**动机**:换新项目 json 结构不同,目前要手动跑一次 `learn_response_convention.py`。脚本本身对任意项目/结构
通用(靠 LLM 现场提炼,非硬编码),但"每个新项目手动跑一次"仍不够优雅。
**目标终态**:新项目首次跑完接口报告后,平台**自动**从报告真实响应提炼响应约定入库 →
第二次生成就写对 JSONPath,人工零介入。做法:把 `learn_response_convention.py` 的逻辑挂进
`run_test_task` 收尾 或 `tasks/ai_tasks.py`,报告落库后异步触发(仅 api 类报告、每项目节流,避免重复学)。
**更彻底的上游方案(工作量大,列为 P2)**:生成前主动探测——接口用例生成前先真调一次(如登录)拿真实响应,
再让模型写路径,连"第一次会错"都省掉。`ai_gateway/prompts/api_probe_refine.md` 已为此备好,
难点在鉴权与写操作(mutating)的安全处理。
</details>

### P0-2 草稿自动校验与试跑 `✅ 已上线:静态校验+自修回路(两条链) + 写入后一键试跑 + 评审页"已自动修复/N 处提醒"徽标`
人的耐心最贵,跑不起来的用例绝不能出现在评审页:

1. **静态校验**:steps 符合 DSL、endpoint 存在、变量引用可解析 → 不过自动打回让模型自修
2. **自动试跑**:通过静态校验的草稿真跑一遍 → 跑绿标"已验证",跑挂自动分诊(真 bug or 用例烂)
3. 评审页展示形态:"12 条草稿,9 条已试跑通过"

### P1 评审信号埋点(数据飞轮) `✅ 已上线:后端(快照/拒因/edit_ratio/统计端点/反例回填) + 评审 UI(CaseGenerationReviewDialog,需求页 ✨ 按钮:生成→评审→采纳/拒绝带原因)`
现有 draft→编辑→采纳/拒绝流程的信号目前丢弃,要开始记录:

- 每条草稿:采纳/拒绝、编辑距离、拒绝原因
- 短期:按项目回填 prompt(采纳的做正例、拒绝的做反例)
- 长期:采纳率指标是向客户证明"AI 可信"的唯一证据,也是产品健康度指标

### P1 覆盖率视图与去重 `⬜ 未开始`
"全面"≠量大,= 知道缺口在哪:

- 保持 outline → 确认范围 → detailed 两段式(已有,`outline_gaps` prompt 可用)
- `embeddings.py` 对新草稿 vs 存量用例去重,防冗余
- 需求/接口维度覆盖缺口视图,"补全面"变成用户可见可点击的动作

### P1 记忆层回流策略 `🔶 分析文档回流已上线(含存量回填脚本);接口文档提取 api_contract 待做`
原则:**回流"事实",不回流"AI 的产出"**(防幻觉自我放大污染记忆层):

- `requirement_analyze`(需求分析文档)增加 context_items 提取回写 —— 目前只有
  `requirement_parse`(需求导入)写记忆层,分析文档内容更深却不回流
- 接口用例生成入口用户贴的接口文档 / swagger 链接(`_fetch_doc_url` 拉取内容)
  → 提取 `api_contract` 条目入库,作为 Phase C 的 API 事实来源
- **AI 生成的用例本身不回流**;用例环节的价值信号是人工采纳/拒绝(见"评审信号埋点")

### P2 MCP server `⬜ 未开始`
把 `POST /api/run_test`、报告查询、用例 CRUD 包成 MCP 协议,让客户的 coding agent
(Claude Code / Codex)直接调用平台跑回归、读结果、沉淀用例。
场景闭环:AI 写码 → MCP 跑回归 → 失败自动修 → AI 生成新用例、人评审入库 → CI 闸门兜底。
demo 价值大:从"看平台界面"变成"看 agent 写完功能自动回归自动修"。

## 北极星指标

| 指标 | 说明 |
|---|---|
| 草稿采纳率 | 被 commit 的草稿占比,按项目/模型分维度 |
| 首跑通过率 | 草稿自动试跑一次通过的比例 |
| 人工编辑距离 | 采纳前人改了多少,越低越好 |
| 30 天留存执行 | 入库用例 30 天后仍在被执行的比例(防"死用例"堆积) |
