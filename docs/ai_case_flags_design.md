# AI 诊断标记 + 用户反馈回流：设计方案

> 2026-07-05。目标：把 AI 诊断结论变成用例列表上**可见、可清除、可学习**的标记。
> 用户一眼看到哪些用例要人工修、哪些疑似接口缺陷、哪些是环境问题；
> 清除标记时采集结构化反馈，回流到下一次 AI 诊断，让误判不重复发生。

---

## 1. 标记体系（对应四类问题）

| flag_type | 含义 | 产生条件 | 列表展示 | 颜色/图标 |
|---|---|---|---|---|
| `manual_fix` | **需人工修改** | 分类=用例问题，但 AI 修不动：修复建议全部被预检拦截 / 多轮修复后仍失败 / 模型未给出修复 | 「需人工」 | amber + Wrench |
| `interface_defect` | **疑似接口缺陷，重点检查** | 分类=接口问题（真实响应错误/缺字段/数据不符业务） | 「疑似接口缺陷」 | red + Bug |
| `environment` | **环境问题** | 分类=环境/其他（超时/5xx/连不上/依赖缺失） | 「环境」 | slate + CloudOff |
| `ai_fixed` | **AI 已修复，建议复核**（可选） | 修复闭环验证红→绿 | 「AI已修复」 | emerald + Sparkles |

分类=正常 → 无标记，且会自动清除该用例的旧标记（见 §4）。

一条用例同时只有**一个 active 标记**（最新诊断为准）；历史标记保留（status=superseded），
清除记录即反馈数据。

## 2. 数据模型（新表 `ai_case_flags`，需 alembic 迁移）

```python
class AiCaseFlag(Base):
    __tablename__ = "ai_case_flags"

    id         = Column(Integer, primary_key=True)
    case_id    = Column(Integer, ForeignKey("test_cases.id", ondelete="CASCADE"), index=True)
    module_id  = Column(Integer, index=True)          # 冗余，列表按模块批查

    flag_type      = Column(String(30), nullable=False)   # manual_fix | interface_defect | environment | ai_fixed
    classification = Column(String(20))                    # AI 原始分类
    findings       = Column(JSONType)                      # list[str]，诊断发现
    fix_rounds     = Column(Integer, default=0)            # 尝试修复轮数
    source_ai_run_id  = Column(Integer, index=True)
    source_report_id  = Column(Integer)

    status = Column(String(20), default="active", index=True)
    # active | cleared(人工清除) | auto_cleared(后续通过/判正常) | superseded(被新诊断覆盖)

    # —— 清除即反馈（学习信号）——
    cleared_at     = Column(DateTime)
    cleared_by_id  = Column(Integer)
    cleared_reason = Column(String(30))
    # manually_fixed  已人工修复
    # misjudged       AI 判断有误（配 corrected_classification）
    # external_fixed  接口已修复 / 环境已恢复
    # wont_fix        无需处理（预期行为，如负向用例 4xx）
    corrected_classification = Column(String(20))   # misjudged 时：正常|用例问题|接口问题|环境/其他
    cleared_note   = Column(Text)                    # 自由文本（人工修复时"改了什么"最有价值）

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
```

索引：`(case_id, status)` 复合；`module_id`（列表查询）。

## 3. 标记的产生（写入点）

统一由新服务 `server/services/ai_flag_service.py` 提供 `upsert_flags_from_diagnosis(...)`，
两个调用点：

1. **多轮修复闭环结束时**（`tasks/ai_fix_verify_task.py::_finalize`）——信息最全：
   - 接口问题 → `interface_defect`；环境/其他 → `environment`
   - 用例问题 ∩ (still_red ∪ untouched_red ∪ 预检全拦) → `manual_fix`（findings 附"AI 尝试了 N 轮/为何被拦"）
   - 用例问题 ∩ 最终红→绿 → `ai_fixed`（如启用）
   - 正常 → 清除该用例旧 active 标记（auto_cleared）
2. **未触发验证的路径**（apply 端点 verify=False / 无 project_id）：应用后立即按分类写，
   用例问题一律 `manual_fix`（因为没有验证背书），findings 注明"修复已应用但未验证"。

upsert 语义：同 case 已有 active 标记 → 旧标记置 superseded，插入新标记。

## 4. 标记的消亡

| 途径 | 规则 |
|---|---|
| 新诊断覆盖 | 同用例新标记产生 → 旧 active 置 superseded；新诊断判"正常" → 旧 active 置 auto_cleared |
| 执行通过 | `manual_fix` / `ai_fixed`：该用例在后续任意报告聚合状态 passed → auto_cleared（挂在 `sync_allure_to_db`/`finalize_report` 后，批量一条 update）。`interface_defect` / `environment` **不**因通过自动清（通过≠缺陷修复，可能只是断言没测到），只能人工清或新诊断覆盖 |
| 人工清除 | 详情弹层里清除 + 反馈（§5） |

## 5. 前端交互

### 列表（AutomationCasesPage CaseTable）
- `GET /api/api_cases` 响应每行新增 `ai_flag: {flag_type, findings, fix_rounds, source_report_id, created_at} | null`
  （后端批量查 active 标记，模式同 `_latest_runs` 防 N+1）。
- 行内用例名称旁渲染彩色小徽标（图标+短文案，见 §1 表），tooltip 显示前 2 条 findings。
- 状态筛选旁增加「标记」筛选：全部 / 需人工 / 疑似接口缺陷 / 环境 / AI已修复（后端 query 参数 `flag_type`）。

### 详情 / 清除（点击徽标 → Popover）
```
┌─ 疑似接口缺陷 ─────────────────────────┐
│ • 响应缺少 data.order_no 字段，文档要求必返   │
│ • $.code=500 而请求参数合法                │
│ 来源：报告 #123 · 2026-07-05 · 尝试修复 2 轮 │
│ [查看报告]                    [清除标记 ▾] │
└──────────────────────────────────────┘
清除时弹小表单（必选原因 + 可选项）：
  ○ 已人工修复          （可填：改了什么 → 喂给 AI 当经验）
  ○ AI 判断有误         （必选更正分类：正常/用例问题/接口问题/环境）
  ○ 接口已修复/环境已恢复
  ○ 无需处理（预期行为） （如负向用例 4xx 本来就对）
  备注（可选）：________
```
- `POST /api/api_cases/{case_id}/ai_flag/clear`，body `{reason, corrected_classification?, note?}`，记 operator。
- 用例编辑器（CaseDialog）里加只读小节展示当前标记 + 历史（`GET /api/api_cases/{id}/ai_flags`，phase 2）。

## 6. 反馈回流 AI（核心：让下次诊断"记得"）

三层，由浅入深：

### 6.1 用例级记忆注入（首期必做，收益最大）
`diagnose_report_items` 组装 items 时，批量查每条用例**最近 3 条已清除标记**中
reason ∈ {misjudged, wont_fix, manually_fixed} 的记录，注入 `user_feedback` 字段：

```json
"user_feedback": [
  "2026-07-01 用户更正：AI 曾判『接口问题』，实际『正常』——负向鉴权用例，401 是预期",
  "2026-06-28 用户已人工修复：改用独立测试账号，避免被改密码用例影响"
]
```
prompt（api_report_diagnose.md）新增：`user_feedback` 是用户对**本用例**历史诊断的更正，
权威性最高；分类与 fix 必须与之一致，除非本次响应呈现明显不同的新问题（此时在 findings 里说明差异）。

### 6.2 程序化硬约束（不靠模型自觉）
预检层（`ai_fix_service.preflight_report_fixes`）同步读取反馈：
- 某用例存在 `wont_fix` 或 `misjudged→正常` 的反馈 → 该用例的自动修复**直接跳过**
  （skipped 原因："用户已标记无需处理/正常"），无论模型这次怎么判。双保险。

### 6.3 项目级更正统计（phase 2）
诊断时在 REPORT_CONTEXT 追加近 90 天更正分布，如：
`用户更正统计：『接口问题→正常』6 次（多为【鉴权】【参数校验】负向用例）；『环境→接口问题』1 次`
帮模型整体校准该项目的判断倾向。数据够了再开。

## 7. 改动清单

| 层 | 文件 | 改动 |
|---|---|---|
| 模型 | `database/models/ai_case_flag.py`（新）+ `__init__.py` 导出 + alembic 迁移 | §2 |
| 服务 | `server/services/ai_flag_service.py`（新） | upsert / clear / auto_clear / 反馈查询 |
| 写入 | `tasks/ai_fix_verify_task.py::_finalize`、`functional_cases.py` apply 端点 | §3 |
| 自动清 | `database/data_sync.py::finalize_report` 后挂钩 | passed → auto_clear |
| 列表 | `server/api/api_cases.py` | `_active_flags` 批查 + `_serialize_case` 加 `ai_flag` + `flag_type` 筛选参数 |
| 清除 | `server/api/api_cases.py` | `POST /{id}/ai_flag/clear`、`GET /{id}/ai_flags` |
| 回流 | `functional_cases.py::diagnose_report_items` + `ai_fix_service.preflight` + prompt | §6.1/6.2 |
| 前端 | `AutomationCasesPage.tsx`（徽标+Popover+清除表单+筛选）、`api.ts`、`domain.ts` | §5 |

## 8. 实施顺序与工作量

| 步 | 内容 | 量 |
|---|---|---|
| 1 | 模型 + 迁移 + flag_service（含单测桩） | 0.5 天 |
| 2 | 写入点接入（finalize / apply / passed 自动清） | 0.5 天 |
| 3 | 列表 embed + 徽标 + Popover 清除表单 + 筛选 | 1 天 |
| 4 | 反馈回流（user_feedback 注入 + prompt + 预检硬约束） | 0.5 天 |

合计 ≈ 2.5 天。1→2→3 可先上线看效果，4 随后。

## 9. 待拍板

1. **`ai_fixed` 绿标要不要**：能看清 AI 动过哪些用例，但多一种标记；不要的话红→绿只出现在报告 toast 里。
2. **模块树聚合**：左侧模块树要不要显示各模块 active 标记计数（如红点 3）？首期可不做。
3. **清除权限**：平台目前是可选登录（OptionalUser），默认任何人可清、记录 operator；要收紧再说。
