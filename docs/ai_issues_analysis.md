# API 模块 AI 功能四个问题：根因分析与解决方案

> 分析日期：2026-07-05。所有行号以当前代码为准。

---

## 问题 1：大纲测试点数量极不稳定（穷尽模式 10 条 ↔ 100+ 条）

### 根因

1. **温度硬编码且偏高**。所有 provider 的对话调用温度写死 0.4（`ai_gateway/gateway.py:665、704、786、813、856、875`，anthropic 0.2），采样随机性直接放大成条数方差。同样输入跑两次，模型"合并测试点"还是"逐项拆开"完全看采样。
2. **Prompt 给了 10 倍宽的数量区间**。`_COVERAGE_TEXT["exhaustive"]`（`server/api/functional_cases.py:1097`）明确写着"**可以生成几十到上百条**"——这句话本身就授权了 30~150 条的任意落点。`interface_case_outline.md:67` 又说"数量由覆盖度决定，不要凑整"，模型没有任何数值锚点。
3. **一次自由发挥式单调用规划全部接口**。接口一多，模型自行决定压缩粒度；没有"每接口 × 每字段 × 每维度"的结构化产出要求，也没有生成后的校验补齐。输出逼近 `max_tokens`（默认 8192，`gateway.py:591`）时模型还会"自觉收敛"。

### 方案（按优先级）

1. **结构化任务温度降到 0**：给 `chat_markdown` / `_chat_once` 加 `temperature` 参数（默认保持 0.4 不动老链路），`ai_generate_outline`、`ai_outline_gaps`、`diagnose_report_items`、`ai_generate_batch` 全部传 0~0.1。这是一行改动、收益最大的一步。
2. **穷尽模式改"程序化矩阵 + 分接口生成"**，把数量控制权从模型手里拿回来：
   - 第一步让模型（或直接复用 `_summarize_openapi`，`functional_cases.py:296` 已能解析出参数/必填/约束）输出结构化的接口字段清单：`[{method, path, fields:[{name, required, type, constraints}]}]`；
   - 第二步在**代码里**按维度池展开矩阵（每必填字段 × 缺失/为空/null/类型错/特殊字符，每约束字段 × min/max/超界……），逐接口小批量调 AI 只负责给每个矩阵格子起标题、排顺序；
   - 数量 = 矩阵大小 ± 小幅裁剪，天然稳定，也彻底避开单次输出 token 上限。
3. **短期 prompt 补丁**（在做 2 之前先止血）：把"可以生成几十到上百条"改成可计算的锚点，如"每个接口至少：1 正常 + 每必填字段 2 条（缺失/为空）+ 每字段 1 条类型错误 + 鉴权 3 条 + 越权/安全/响应校验/查库各至少 1 条"；并要求输出里带 `interface` 字段，返回后按 digest 里的接口清单核对，缺的接口自动补一轮（可直接复用查漏补缺链路）。
4. **后校验兜底**：`points` 数量与"接口数 × 最低条数"的期望值偏差超过阈值时，返回 warning 让前端提示"本次规划偏少，建议重试"。

---

## 问题 2：查漏补缺"完全没有补充的感觉"

### 根因

1. **信息不对称——模型根本看不到能找漏的材料**。`ai_outline_gaps`（`functional_cases.py:1482-1487`）只传 `DIGEST`、跨模块上下文、已有点、已有用例名。生成大纲时模型拿的是完整接口文档，查漏时只有几百字的 digest（字段约束经常没写全），还被塞了一大列"不要重复"的已有点。**它没有新信息，只能回答"很全"**。
2. **system prompt 自相矛盾**。`chat_markdown(prompt, cfg, timeout=180)`（`functional_cases.py:1491`）没传 `system_prompt`、没开 `json_mode`，走默认 `_system_for_markdown()`（`gateway.py:548-553`）——内容是"**不要返回 JSON**，输出 Markdown 文档"，而用户 prompt 要求严格 JSON。输出质量与解析成功率被直接拉低。对比：生成大纲用的是专门的 JSON system prompt + `json_mode=True`（`functional_cases.py:1204-1221`），查漏没有对齐。
3. **解析失败被伪装成"很全"**。`(obj or {}).get("points") or []`（`functional_cases.py:1512`）——JSON 解析失败时 `obj=None`，静默返回空数组，前端 toast"没找到遗漏，大纲已比较全面"（`FunctionalCasesPage.tsx:3324`）。用户看到的"没有遗漏"，很多次其实是**解析失败**。
4. `outline_gaps.md:39` 明确给了"若确实已经很全，返回 []"的省事出口，叠加长长的已有点列表，模型倾向直接交空卷。

### 方案

1. **把原始需求/接口文档带进查漏调用**：前端把生成大纲时的 `text/doc_urls`（或后端把 outline 请求的 `requirement_text` 存入 `module_outline` 表）在 `ai_outline_gaps` 请求里回传，prompt 增加 `{{REQUIREMENT_TEXT}}`、`{{VARIABLE_POOL}}`、`{{COVERAGE_LEVEL}}`。没有字段级材料，查漏就是无米之炊。
2. **调用方式对齐大纲**：`json_mode=True` + JSON system prompt + `enable_thinking=False` + 温度 0。
3. **区分"没有遗漏"与"解析失败"**：`obj` 为 None 时返回 502（或 `data.parse_failed=true`），不要静默空数组。
4. **prompt 改成强制核对矩阵**：要求模型先输出"接口 × 维度 → 已覆盖/缺失"的 checklist，再从缺失格子产出补充点。逐格核对比"自由找漏"稳定得多，也消灭了"直接说很全"的捷径。

---

## 问题 3：AI 修复参数并应用 = 0 作用（只会加断言）

### 根因（多个环节叠加，模型端根本没有证据、给了也应用不到）

1. **喂给 AI 的用例定义是 v1 遗留列，不读 steps**。`diagnose_report_items` 组装的 `def` 只有 `c.method / c.path / c.headers / c.params / c.extract_data / c.assertion`（`functional_cases.py:2025-2032`）。多步场景用例只剩第一步的影子；在步骤编辑器里改过的用例（`update_case` 带 steps 时不回写 v1 列，`server/api/cases.py:420-447`）`def` 是**陈旧甚至为空**的。模型看不到真实参数定义，怎么修参数？
2. **CASES 被拦腰截断成非法 JSON**。`json.dumps(chunk, ensure_ascii=False)[:14000]`（`functional_cases.py:2064`），而 chunk=6 条用例，每条 result 单行就允许 1200+1800+800+500+800≈5100 字符，6 条轻松超 3 万字符——**后半个 chunk 的用例数据被硬切**。模型对这些用例只能给最安全的泛泛建议：补断言。`REPORT_CONTEXT[:6000]`（2063）同理。
3. **system prompt 冲突**：`chat_markdown(prompt, cfg, timeout=240)`（2067）同问题 2，默认 system 是"不要返回 JSON"。
4. **prompt 把 fix.params 卡得极死**（`api_report_diagnose.md:49-50`）："fix 只在 classification=用例问题 时给"、"fix.params 只在参数确实写错时给（完整对象）"。在证据不足（因 1/2）时，模型的理性选择就是永远只给 assertion。
5. **应用侧只 patch 第一条 http_request step**。`applyAiReportFixes`（`ApiCasesPage.tsx:1573` 附近 `steps.findIndex(...)`）——场景多步用例第 2..N 步即使模型给了修复也落不下去；且 `params: fp` 是**整体替换**，模型给残缺对象会清掉原字段。
6. 修复用的模型写死 `firstModel`（第一个可用模型，`ApiCasesPage.tsx:1520`），不是用户在界面选的模型。

### 方案

1. **`def` 改为从 steps 读**：每条用例给出 `steps: [{step_id, method, path, headers, params, extract, assertion}]`；`fix` 结构升级为按步定位：
   ```json
   "fix": {"steps": [{"step_id": 3, "params": {...}, "headers": {...}}], "extract": {...}, "assertion": {...}}
   ```
   前端按 `step_id` 应用（保留旧格式兼容：无 step_id 时视为第一步）。
2. **废除 `[:14000]` 粗暴截断**：按字符预算动态收缩——超预算就把 `chunk_size` 从 6 降到 2~3，甚至逐用例调用；宁可多调几次，不可喂断头 JSON。截断单条 result 字段时保留"请求体"优先级最高（修参数靠它）。
3. **调用对齐**：`json_mode=True` + JSON system prompt + 温度 0。
4. **应用层做合并而非整体替换**：`fix.params` 与原 `config.params` 做浅合并 + 前端展示 diff 供确认；避免 AI 残缺输出清空正确字段。
5. **闭环验证**：应用后可选自动重跑该用例，失败则回滚（编辑历史 batch 已支持回滚，成本低）。
6. 模型选择用用户当前选中的模型，而不是 `firstModel`。

---

## 问题 4：重复用例检测维度不准

### 根因

`_detect_duplicate_cases`（`test_result_analysis_service.py:836-918`）只有**一个维度**：请求签名逐字节相等（method+path+headers+params+body+data_type canonical 化后 `json.dumps` 相等）。两头都错：

- **误报**：签名不含 `assertion / extract / sql_query / 前后置`。请求完全相同但校验目标不同的用例（"登录成功（extract token）" vs "登录成功响应字段校验"、幂等/重复提交类用例、依赖前序状态的用例）被 0.95 置信度判为重复并**建议删除**——删了就丢断言/丢 token 产出。
- **漏报**：语义重复但 body 里有 `function:random_xxx`、`${var}`、时间戳字面量差一个字符 → 判不重复。变量引用和动态函数没做归一化。
- **范围窄**：只在同一报告内比对；没跑进同一次报告的模块内重复检不到。

### 方案

1. **签名纳入校验语义**：请求相同且 assertion/extract/sql 也相同 → 高置信度"重复，建议删除"；请求相同但校验不同 → 降级为"疑似重叠"，建议**合并断言**而不是删除（生成侧已有 `_merge_response_check_cases` 的合并逻辑，`functional_cases.py:1033`，可复用）。
2. **归一化动态值再比较**：canonical 化时把 `${xxx}` → `<VAR>`、`function:xxx(...)` → `<FUNC>`、疑似时间戳/随机串 → `<DYN>`，可召回"只差随机值"的真重复。
3. **类别互斥规则**：用例名带【幂等】【重复提交】【响应校验】等类别的，不与主流程用例互判重复（重复请求正是它们的测试手段）。
4. **补一个模块级检测入口**（不依赖报告），可选叠加 `ai_gateway/embeddings.py` 做语义近重复召回 + 规则精判，near-duplicate 给低置信度人工复核。

---

## 横切问题（四个问题共享的病根）

1. **温度不可配且偏高**——所有结构化 JSON 任务应为 0。
2. **`chat_markdown` 默认 system prompt 与 JSON 任务冲突**——凡是要 JSON 的调用点（`ai_outline_gaps`、`diagnose_report_items`）都应显式传 JSON system prompt + `json_mode=True`。
3. **解析失败静默降级**——查漏返回空数组、诊断 salvage 丢对象，都把故障伪装成"AI 觉得没问题"。失败要暴露，不要吞。
4. **硬编码截断**（`[:14000]`、`[:6000]`）在数据量大时悄悄毁掉输入完整性。

## 模型侧因素：思考模式与推理设置（补充）

上面四个问题的分析偏"平台侧"，但模型本身的推理设置同样是重要变量，且现状处理很不一致：

### 现状盘点

| Provider | 思考模式处理 | 问题 |
|---|---|---|
| zai (GLM) | `enable_thinking` 参数，仅 False 时下发 `thinking:{type:disabled}`，默认 True（`zai_provider.py:36-71`） | 只有大纲生成传了 False（`functional_cases.py:1219`）；**查漏、诊断修复走默认 True**，慢且行为不一致 |
| deepseek | 只在 `_openai_markdown` 里靠"`base_url` 含 api.deepseek.com 且 system_prompt 含 JSON"的启发式关思考（`gateway.py:669-677`） | 查漏/诊断没传 system_prompt → 启发式不命中 → **thinking 保持开启** → 注释里自己写了会"偶发只返回 reasoning_content / 空 content"→ 解析失败 → 查漏静默返回"没有遗漏"。**这是问题 2/3 的模型侧帮凶** |
| anthropic | 完全不支持 extended thinking / budget_tokens（`anthropic_provider.py`） | Claude 系推理能力用不上；且开 thinking 时要求 temperature=1，现在写死 0.3 将来会直接报错 |
| openai | 不支持 `reasoning_effort`；o 系/gpt-5 系不接受 `temperature`、要用 `max_completion_tokens` | 配置推理模型可能直接 400 |
| ollama | 无 think 参数处理（qwen3 等支持 `think:false`） | 本地推理模型思考不可控 |

`AiModelConfig.extra`（`ai_model_service.py:55-68`）已经是一个可透传任意键的 dict，**基础设施是现成的，只是网关不读**。

### 思考模式对四个问题的实际影响

- **问题 1（数量不稳定）**：这里有个反直觉的点——大纲生成对 GLM 是**关了思考**的（`enable_thinking=False`）。规划类任务恰恰是思考收益最大的场景：关思考的模型更容易"偷懒合并测试点"，这可能直接贡献了 10 条档的低值；但开思考又要面对 max_tokens 预算（GLM 思考 token 计入总预算）和 180s 超时。正确姿势是：**开思考 + 中等强度 + max_tokens ≥ 16k + timeout 上调**，而不是一刀切关掉。
- **问题 2/3（查漏/修复）**：DeepSeek 思考未关 → 空 content 偶发 → 解析失败被静默吞掉；GLM 思考开着 → 240s 超时风险。模型侧和平台侧（截断、system prompt 冲突）叠加，产出自然只剩"补断言"。
- 数量方差本身主要还是 temperature 的锅，但思考模式决定了"下限质量"：推理模型关思考后表现会掉到普通模型水平。

### 方案：任务画像 × 模型能力的统一调度

1. **模型配置扩展**（利用现有 extra 机制，UI 加三个字段）：
   - `extra_reasoning`: `"on" | "off" | "auto"`（模型是否为推理模型/是否允许思考）
   - `extra_reasoning_effort`: `"low" | "medium" | "high"`（映射：openai `reasoning_effort`；anthropic `budget_tokens` 如 2k/8k/24k；GLM/deepseek 只有开关则 low=off）
   - `extra_temperature`: 覆盖默认温度
2. **网关按"任务画像"统一解析**，替代散落的 hack。建议画像：

   | 任务 | 思考 | 温度 | 说明 |
   |---|---|---|---|
   | 大纲规划 / 查漏补缺 | on, medium | 0 | 规划完整性靠推理；预算和超时同步放大 |
   | 批量生成用例 JSON | off / low | 0 | 输出长、结构死，思考性价比低还挤占输出预算 |
   | 诊断 + 参数修复 | on, low~medium | 0 | 要推断依赖链/变量来源，低思考有收益 |
   | 连通性测试 | off | — | 现状已正确 |

3. **provider 层补齐**：anthropic 加 `thinking:{type:"enabled", budget_tokens}`（注意开思考必须 temperature=1、max_tokens > budget）；openai 识别推理模型换 `reasoning_effort`/`max_completion_tokens`；ollama 加 `think` 开关；deepseek/zai 改为显式参数驱动，删掉 system_prompt 关键字启发式。
4. **选型建议写进配置中心提示**：规划/诊断类任务配推理模型（GLM-5 thinking、DeepSeek-R、Claude/o 系），批量 JSON 生成配快模型；两类任务允许配不同默认模型（现在诊断修复还写死用 `firstModel`，一并改掉）。

## 建议实施顺序

| 顺序 | 改动 | 成本 | 收益 |
|---|---|---|---|
| 1 | 温度参数化 + JSON 调用对齐（json_mode/system prompt） | 半天 | 问题 1/2/3 立即缓解 |
| 2 | 查漏补缺带上原始文档 + 解析失败显式报错 | 半天 | 问题 2 |
| 3 | 诊断 def 改读 steps + 动态 chunk 防截断 + fix 按 step 定位 | 1-2 天 | 问题 3 |
| 4 | 重复检测签名纳入断言 + 动态值归一化 | 1 天 | 问题 4 |
| 5 | 任务画像 × 模型思考设置统一调度（extra_reasoning 等） | 1-2 天 | 问题 1/2/3 的模型侧根因 |
| 6 | 穷尽模式程序化矩阵生成 | 2-3 天 | 问题 1 根治 |
