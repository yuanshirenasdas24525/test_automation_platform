# PM 重设计 · M2 API 层 实施计划

## Context

M1（数据底座，commits `7f8d292..64bb088`）已完成：5 张新表（users / roles / user_roles / tasks / version_test_summaries）+ 3 张表字段增量 + 6 个 seed role。`pm_000003` 升降级 roundtrip 已验证。

M2 目标：**在 M1 数据之上搭出 REST API + 业务服务层**，让前端 M3 直接对接。本期不动 UI、不接 auth、不发邮件 IM；只做"内网平台无登录"前提下的最小可用接口。

不变量：
- 沿用 M1 commit 风格 `feat(pm-m2):` 前缀，每 task 单独提交
- 沿用既有 router 模式（参考 `server/api/requirements.py`）：内联 Pydantic schema、`{"status":"success","data":...}` envelope、`db: DBDep` 注入、不在 router 里手动 commit、`HTTPException(400|404)` 抛错
- 沿用既有 service 模式（参考 `server/services/projects.py`、`database/data_sync.py`）：service 函数接 `session: Session` 参数，**不**自己 commit；返回 plain dict 或 None；模型聚合用 `func.sum(case((...)))` 风格
- 平台无 auth：`created_by_id` 等用户字段由 payload 显式传入
- 状态级联：Task.status 任何变化 → 在 router 内调 `task_service.recompute_requirement_status(req_id, session)` 一次

---

## 设计决策（M2 锁定）

| # | 主题 | 决策 |
|---|---|---|
| 1 | summary 生成时机 | GET /api/version-summaries/{id} 按需生成：首次 GET 实时算 + 落库；后续 GET 直读；另开 POST `/regenerate` 显式刷新 |
| 2 | created_by_id 来源 | payload 必传，前端从登录态取（M3 接入后兜上）。M2 内 curl / Swagger 测试需要手填 |
| 3 | 状态级联触发点 | router 层显式调用 `task_service.recompute_requirement_status(req_id, session)`，不用 SQLAlchemy event hook（避免隐式行为，便于排查） |
| 4 | Pydantic schema 位置 | 内联在 router 文件里（跟 `requirements.py` 一致），不集中到 `database/schemas/` |
| 5 | service 是否 commit | 不 commit；只 flush/refresh。事务由 `get_db()` 在 router 退出时统一提交 |

---

## File Structure

| 路径 | 操作 | 职责 |
|---|---|---|
| `server/api/users.py` | 新建 | User CRUD + role 关联（POST `/users/{id}/roles`、DELETE `/users/{id}/roles/{role_code}`） |
| `server/api/roles.py` | 新建 | Role 只读 list（seed 后无需增删） |
| `server/api/tasks.py` | 新建 | Task CRUD + list 过滤（assignee_dev_id / assignee_test_id / type / status / requirement_id / created_by_id / closed_at_after） + `/from-test-failure` 快捷端点 |
| `server/api/version_summaries.py` | 新建 | GET `/version-summaries/{version_id}` 按需生成 + POST `/version-summaries/{version_id}/regenerate` |
| `server/api/requirements.py` | 修改 | list 加新过滤字段；create/update 加新字段；新增 POST `/{id}/accept` |
| `server/api/project_versions.py` | 修改 | 新增 GET `/project-versions/{id}/board`（聚合 Req + Task 计数） |
| `server/api/__init__.py` + `server/main.py` | 修改 | 注册 4 个新 router（每个新文件 task 内顺手做） |
| `server/services/task_service.py` | 新建 | `recompute_requirement_status(req_id, session)`：聚合该 Req 下所有 Task.status → 计算 Req.system_status；含状态机合法性校验函数 |
| `server/services/version_summary_service.py` | 新建 | `generate(version_id, session)`：聚合 SQL 算 first_pass_rate / avg_fix_time / coverage / bug 计数 → upsert 到 version_test_summaries |

---

## 9 个 Task（递增依赖，每 task 一个 commit）

### Task 1 — `/api/users` CRUD + role 关联

**Files:** create `server/api/users.py`; modify `server/api/__init__.py`, `server/main.py`

端点：
- `POST /api/users` — body: username / full_name / email / is_active / role_codes(?[] of `ALL_ROLE_CODES`)，唯一约束冲突 → 409
- `GET /api/users` — query: is_active / role_code / q（按 username/full_name LIKE）
- `GET /api/users/{id}`
- `PUT /api/users/{id}` — partial（`exclude_unset=True`）
- `DELETE /api/users/{id}` — soft delete（`is_active=False`），不真删（保留历史 FK）
- `POST /api/users/{id}/roles` — body `{role_codes: [...]}` 全量替换；校验 `role_codes ⊆ ALL_ROLE_CODES`
- `DELETE /api/users/{id}/roles/{role_code}` — 单角色摘除

验证：
- `curl -X POST .../api/users -d '{"username":"alice","role_codes":["dev","test"]}'`
- `curl .../api/users?role_code=dev` → 看到 alice
- `to_dict()` 返回的 `role_codes` 列表跟入参一致

### Task 2 — `/api/roles` 只读

**Files:** create `server/api/roles.py`; modify `server/api/__init__.py`, `server/main.py`

端点：
- `GET /api/roles` — 返回 6 条 seed，按 id 顺序

验证：`curl .../api/roles` 返回 admin/dev/test/pm/ui/ops 6 条。

### Task 3 — `/api/tasks` CRUD + 多维过滤

**Files:** create `server/api/tasks.py`; modify `server/api/__init__.py`, `server/main.py`

端点：
- `POST /api/tasks` — body: requirement_id（必填）/ title / type / status?（默认 pending）/ severity?（type=bug 时 P0-P3 必填）/ assignee_dev_id? / assignee_test_id? / created_by_id（必填）/ description / metadata / estimated_hours / parent_task_id?；校验 type ∈ ALL_TASK_TYPES、status ∈ ALL_TASK_STATUSES、severity ∈ ALL_BUG_SEVERITIES；严格规则："type=bug 必须有 parent_task_id 且 severity 必填"
- `GET /api/tasks` — query: requirement_id / assignee_dev_id / assignee_test_id / type / status（多值用逗号分隔）/ created_by_id / closed_at_after（ISO 时间，过滤 closed_at >= 该时间）/ parent_task_id；按 created_at desc
- `GET /api/tasks/{id}` — 返回单个，含 parent_task / requirement 简要 dict（用 selectinload）
- `PUT /api/tasks/{id}` — partial；如果 status 变化 → **末尾调 `task_service.recompute_requirement_status(task.requirement_id, db.session)`**
- `DELETE /api/tasks/{id}` — 真删；删后调 recompute

验证：
- 建 Req → 建 3 个 Task（dev/test/ui_review）→ list 过滤 type=dev → 只返 1
- POST type=bug 不带 parent_task_id → 400
- POST closed_at_after=2026-05-04T00:00 → 只返已关闭

### Task 4 — `task_service.recompute_requirement_status`

**Files:** create `server/services/task_service.py`

签名：
```python
def recompute_requirement_status(requirement_id: int, session: Session) -> Optional[str]:
    """读该 Req 下所有非 bug Task，按规则算 Req.system_status。
       bug 不参与系统态聚合（bug 是缺陷，不是开发产出）。
       返回新 system_status；如果没有 Task 返回 None。"""
```

聚合规则（按优先级匹配第一条命中）：
1. 没有任何非 bug Task → return None（不写入）
2. 全部 closed 或 passed → `ready_to_release`
3. 任一 status 是 dev_done / test_doing → `testing`
4. 任一 status 是 dev_doing → `developing`
5. 全部 pending → `approved`

实现要点：
- 只 `session.query(Task.status).filter(Task.requirement_id==id, Task.type != 'bug').all()`
- session.flush() 一次让调用方继续用同一 session 看到 Req.system_status 新值
- 不 commit

测试：
- 建 1 Req + 3 dev Task（全 pending）→ recompute → `approved`
- 改 1 个 Task → dev_doing → recompute → `developing`
- 全部改 closed → recompute → `ready_to_release`

### Task 5 — `/api/tasks/from-test-failure` 快捷建 bug

**Files:** modify `server/api/tasks.py`

端点：
- `POST /api/tasks/from-test-failure` — body: parent_task_id（必填）/ related_case_id / severity（必填，P0-P3）/ title / description / metadata / created_by_id（必填）

后端逻辑：
- 取 `parent_task = session.get(Task, parent_task_id)`，404 检查
- 自动填：`type='bug'` / `status='dev_doing'` / `assignee_dev_id = parent_task.assignee_dev_id` / `requirement_id = parent_task.requirement_id`
- payload 里禁止覆盖 type / status（如果传了忽略）
- 不动 parent_task.status
- 末尾调 `task_service.recompute_requirement_status(parent_task.requirement_id, db.session)`（虽然 bug 不参与聚合，留 hook 一致）

验证：
- 建 Req + 1 个 dev Task（assignee_dev=alice）→ POST from-test-failure → 新 bug.assignee_dev_id == alice、status==dev_doing、parent_task 不动

### Task 6 — `/api/requirements` 加新字段 + accept 端点

**Files:** modify `server/api/requirements.py`

改动：
- 扩 `RequirementCreate` / `RequirementUpdate`：加 version_id / business_status / assignee_pm_id（system_status 不开放写入，由 task_service 维护；accepted_at 由 accept 端点维护）
- list 端点加 query：version_id / system_status / business_status / assignee_pm_id；status / source 保留
- 新增 `POST /api/requirements/{id}/accept` — body: pm_id?（可选；若传则校验该 user 有 role=pm）；逻辑：
  - 校验 req.system_status == 'ready_to_release'，否则 409 "需求未完成开发测试，不能验收"
  - 设 `business_status='accepted'` / `accepted_at=func.now()`
  - 不 commit（router 退出时统一）
- `to_dict()` 已含新字段（M1 已加），不动

验证：
- POST accept 一个 system_status=approved 的 Req → 409
- 把 Req 下所有 Task 设 closed → recompute → system_status=ready_to_release → POST accept → business_status=accepted、accepted_at 非 null

### Task 7 — `version_summary_service.generate`

**Files:** create `server/services/version_summary_service.py`

签名：
```python
def generate(version_id: int, session: Session) -> dict:
    """聚合该 version 的 Requirement / Task / TestCase / TestReport 数据，
       upsert 到 version_test_summaries 一行；返回 to_dict()。"""
```

聚合 SQL（参考 `database/data_sync.py:122-202` 风格）：
- `total_requirements`：count(Requirement.id) where version_id=v
- `total_tasks`：count(Task.id) join req on req.version_id=v；type != 'bug'
- `total_bugs / pX_bugs`：count + sum(case((severity=='P0',1),else_=0))
- `passed / failed / blocked`：聚合该 version 下所有 TestCase 关联的 TestReport，按 status 分桶（注意 TestCase.version_id 在 M1 已加）
- `total_test_cases`：count(TestCase.id) where version_id=v
- `first_pass_rate`：`1 - (total_bugs / NULLIF(total dev_tasks, 0))`，dev_tasks 指 type='dev' 的 Task；NULLIF 防 0 除
- `avg_fix_time_hours`：bug type 的 Task，AVG(EXTRACT(EPOCH FROM closed_at - created_at) / 3600)；SQLite 用 julianday 算
- `test_coverage`：count(distinct Requirement.id where 至少有一个 TestCase 关联到该 req 通过 module 间接) / total_requirements；本期简化：count(Requirement.id where version_id=v 且 EXISTS（TestCase.version_id=v 同 module_id）) / total
- `payload`：dump 一份完整 ID 列表 `{requirement_ids: [...], task_ids: [...], bug_ids: [...]}`，便于发布后回溯

upsert 逻辑：
```python
existing = session.query(VersionTestSummary).filter_by(version_id=version_id).first()
if existing:
    for k, v in stats.items(): setattr(existing, k, v)
    existing.generated_at = func.now()
    summary = existing
else:
    summary = VersionTestSummary(version_id=version_id, **stats, generated_at=func.now())
    session.add(summary)
session.flush()
session.refresh(summary)
return summary.to_dict()
```

验证：跑 setup → 建 1 Version + 1 Req + 2 Task + 1 Bug → service.generate → 检查 first_pass_rate=0.5 / total_bugs=1 / payload 含 ID 列表

### Task 8 — `/api/version-summaries` 端点

**Files:** create `server/api/version_summaries.py`; modify `server/api/__init__.py`, `server/main.py`

端点：
- `GET /api/version-summaries/{version_id}` — 按需生成：先查表，没有 → 调 service.generate；有 → 直接返 to_dict
- `POST /api/version-summaries/{version_id}/regenerate` — 强制重算

验证：
- 第一次 GET 不存在的 version_id → 触发 generate、返回 stats
- 第二次 GET → 直接读缓存（无 generate 日志）
- 改一个 Task 状态 → POST regenerate → 数据更新

### Task 9 — `/project-versions/{id}/board` 看板聚合

**Files:** modify `server/api/project_versions.py`

端点：
- `GET /api/project-versions/{id}/board` — 返回：
  ```
  {
    version: {...to_dict()},
    requirements_by_status: {
      approved: [...req.to_dict()],
      developing: [...],
      testing: [...],
      ready_to_release: [...]
    },
    task_counts_by_type: {dev: {pending: n, dev_doing: n, ...}, test: {...}, bug: {by_severity: {P0:n,P1:n,...}}},
  }
  ```
- 用 `selectinload(Requirement.tasks)` 一次拉完，避免 N+1

验证：建一个 version + 多个 Req + Task → GET board → 看到分组结构 + 计数

---

## Critical Files Referenced（不修改、只参考）

| 用途 | 路径 |
|---|---|
| router 风格模板 | `server/api/requirements.py` |
| 自定义 action 端点示例 | `server/api/devices.py:278`（release_device） |
| DBDep 用法 + lifecycle | `server/api/deps.py:16-38` |
| 聚合 SQL 风格 | `database/data_sync.py:122-202`（finalize_report） |
| service 风格 | `server/services/projects.py:79-111`（list_projects_with_stats） |
| router 注册 | `server/api/__init__.py`、`server/main.py` 的 `for router in (...)` 循环 |
| Task 模型常量 | `database/models/task.py`（`ALL_TASK_TYPES` / `ALL_TASK_STATUSES` / `ALL_BUG_SEVERITIES`） |
| Requirement 模型常量 | `database/models/requirement.py`（`ALL_REQUIREMENT_SYSTEM_STATUSES` / `ALL_REQUIREMENT_BUSINESS_STATUSES`） |

---

## End-to-End Verification（完成全部 9 task 后）

```bash
# 0. 起服务
CELERY_TASK_ALWAYS_EAGER=1 python3 server/main.py &

# 1. 用户 + 角色
USER_ID=$(curl -sX POST localhost:54351/api/users \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","role_codes":["dev","test"]}' | jq -r '.data.id')
curl -s localhost:54351/api/users?role_code=dev | jq

# 2. 建 Req + 3 Task
REQ_ID=$(curl -sX POST localhost:54351/api/requirements \
  -H "Content-Type: application/json" \
  -d "{\"project_id\":1,\"title\":\"E2E\",\"version_id\":1,\"assignee_pm_id\":$USER_ID}" | jq -r '.data.id')

for T in dev test ui_review; do
  curl -sX POST localhost:54351/api/tasks -H "Content-Type: application/json" \
    -d "{\"requirement_id\":$REQ_ID,\"title\":\"$T\",\"type\":\"$T\",\"created_by_id\":$USER_ID,\"assignee_dev_id\":$USER_ID}"
done

# 3. 状态流转 → 自动级联
TASK1=$(curl -s "localhost:54351/api/tasks?requirement_id=$REQ_ID" | jq -r '.data[0].id')
curl -sX PUT localhost:54351/api/tasks/$TASK1 -H "Content-Type: application/json" \
  -d '{"status":"dev_doing"}'
curl -s localhost:54351/api/requirements/$REQ_ID | jq '.data.system_status'  # → "developing"

# 4. 全 close → ready_to_release → accept
for tid in $(curl -s "localhost:54351/api/tasks?requirement_id=$REQ_ID" | jq -r '.data[].id'); do
  curl -sX PUT localhost:54351/api/tasks/$tid -H "Content-Type: application/json" -d '{"status":"closed"}'
done
curl -s localhost:54351/api/requirements/$REQ_ID | jq '.data.system_status'  # → "ready_to_release"
curl -sX POST localhost:54351/api/requirements/$REQ_ID/accept -H "Content-Type: application/json" -d '{}'

# 5. from-test-failure
curl -sX POST localhost:54351/api/tasks/from-test-failure -H "Content-Type: application/json" \
  -d "{\"parent_task_id\":$TASK1,\"severity\":\"P1\",\"title\":\"login crash\",\"created_by_id\":$USER_ID}"

# 6. version summary 按需生成
curl -s localhost:54351/api/version-summaries/1 | jq

# 7. version board
curl -s localhost:54351/api/project-versions/1/board | jq
```

每条命令都应得到 `{"status":"success",...}` 响应。

---

## Out of Scope（M2 不做）

- 前端工作台 → M3
- auth / login / current_user → 后期
- IM / 邮件通知 → 后期；M2 在 system_status flip 到 ready_to_release 时**不**发通知
- 工时统计 / 燃尽图
- audit_log 审计日志
- 跨项目权限（一人多项目）
- bug 分配给非创建人时的转单流（暂时只有 created_by + assignee_dev）

---

## Next Step

approve 后保存到 `docs/superpowers/plans/2026-05-04-pm-redesign-m2-api-layer.md`，按 task-by-task 派发给 Opencode（沿用 M1 节奏）。
