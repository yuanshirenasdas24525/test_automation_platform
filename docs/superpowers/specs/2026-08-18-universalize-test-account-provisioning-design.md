# 通用化"测试账号 / 测试数据准备"设计

- 日期：2026-08-18
- 状态：设计已确认，待写实现计划
- 范围：平台"通用化"体检的**第一个子项目**（子项目 A）。只覆盖 Web UI 用例运行前的"测试账号 / 测试数据准备"。接口契约来源通用化（子项目 B）、验证码通用化/清理（子项目 C）各自单独走 spec，不在本文范围。

## 1. 背景与问题

平台定位是**通用测试平台**：应能测任意被测系统（SUT），而不是只顺手能测自己。当前 `server/services/web_test_data_service.py` 及周边虽号称"可配置"，实际把"SUT = 平台自己"的假设写进了默认值与运行机制，导致换个真实 SUT 就处处别扭。已定位的耦合点：

1. **`config/pytest_config.py::_calibrate_shared_accounts`**（第 119 行定义、第 207 行调用）：每轮跑前直接 `UPDATE users SET password_hash` 改**平台自己的库**，把测试账号密码重置回配置值。只有"SUT = 平台、共用一个库"才做得到；测外部系统时既连不进人家的库、也不该这么做。
2. **`web_test_data_service.py` + `server/api/config_schemas.py` 的默认值**全是平台自身 API 形状：默认登录 `/api/auth/login`、取 token `$.data.access_token`、建号 `/api/users`+role `test`、清号 `/api/users/${user_id}/purge-test-account`。新项目接入时表单预填的全是"你跟我一样"的假设，真实 SUT 要逐项覆盖，"可配置"沦为"必须全改"。

附带发现（本子项目一并处理或标注）：账号工厂的"配置驱动 HTTP provider"是这些平台特定默认的载体，复杂鉴权场景终归要回退脚本——因此本设计将其整体删除。

（体检中的其它耦合点——接口契约来源限制在平台 `/docs`、验证码写死某 forex 客户接口——归入子项目 B/C，本文不处理。）

## 2. 目标与非目标

**目标**
- 平台核心在准备测试账号时**不对 SUT 发任何出站 HTTP、不碰任何 SUT/平台数据库**。
- 任意 SUT 都能接入，且"最少是可配置的"：非程序员靠填静态账号即可；仅在需要动态账号时才写脚本。
- 删除 `_calibrate_shared_accounts` 自测 hack 与配置驱动 HTTP 工厂及其平台特定默认值。
- 保持 Web UI 自动化端到端可正常运行（含平台自测项目）。

**非目标**
- 不改 runner 执行层（已是 SUT 无关）。
- 不改用例侧 `test_data_requirement` 的语义（"用例声明需要什么账号"这层保持）。
- 不做子项目 B/C。

## 3. 核心模型：拆开"要什么"与"怎么拿到"

- **用例只声明"需要什么"**（不变）：`test_data_requirement` 描述所需账号状态（正常/停用/锁定/边界/空表单/不存在用户）。SUT 无关。
- **项目声明"怎么拿到"**（改造重点），三类满足方式：

| 用例要的 | 平台怎么满足 | 是否碰 SUT |
|---|---|---|
| 空用户名/空密码、不存在的用户 | 本地生成假值（同现状） | 否 |
| 正常/管理员/停用/锁定/边界 等真实状态账号 | 从项目**静态账号池**按 `state` 匹配挑一个 | 否（账号由项目预先备好） |
| 需要每次新鲜/隔离账号的项目 | 调项目挂的**唯一一个 workflow 脚本**（prepare + cleanup） | 由脚本决定，平台不假设 |

平台核心不再含任何"调 SUT 接口"的代码。

**静态 vs 脚本的解析是确定性的（无模糊"是否要隔离"判断）**——对"要真实状态账号"的 requirement，按固定顺序：
1. 静态池有 `enabled` 且 `state` 匹配的账号 → 用它；
2. 否则若配了 `dynamic_script` → 调脚本拿新鲜账号；
3. 否则 → `WebTestDataError`（HTTP 422）。

需要"每轮新鲜账号"的破坏性场景，做法是**让那类 requirement 的 state 不出现在静态池里**（自然落到脚本），而非引入新的用例字段。

## 4. 配置结构

存储位置不变：`config_store` 的 `web/test_accounts` 组，项目级、密码加密、读时掩码。字段大换血，仅两项：

**① 静态账号池** `web/test_accounts/accounts`（JSON 数组）：
```json
[
  { "label": "普通用户", "username": "qa_normal",   "password": "enc:v1:…", "state": "normal",   "enabled": true },
  { "label": "管理员",   "username": "qa_admin",    "password": "enc:v1:…", "state": "admin",    "enabled": true },
  { "label": "停用账号", "username": "qa_disabled", "password": "enc:v1:…", "state": "disabled", "enabled": true },
  { "label": "锁定账号", "username": "qa_locked",   "password": "enc:v1:…", "state": "locked",   "enabled": true }
]
```
- `state` 取值：`normal | admin | disabled | locked | boundary`，是 `test_data_requirement` profile 的落点。
- 不需要真账号的场景（空表单、不存在用户）不在此配置，平台本地生成。
- 密码字段沿用 `enc:v1:` 加密；配置查询接口对其掩码。

**② 可选动态脚本** `web/test_accounts/dynamic_script`（字符串，脚本库 workflow 脚本名，空=不启用）：
```json
"dynamic_script": "provision_fresh_account"
```
脚本负责 prepare（返回 `{username, password, cleanup_token}`）与 cleanup；调 SUT 什么接口全由脚本决定。

**删除的字段**（连同 `config_schemas.py` 对应 schema）：
`api_base_url · login_method · login_path · login_body · token_jsonpath · auth_header · auth_scheme · create_method · create_path · create_body · user_id_jsonpath · cleanup_method · cleanup_path · timeout_seconds · provider`

## 5. 代码组件

把单文件 `web_test_data_service.py` 拆成小包 `server/services/test_accounts/`：

| 模块 | 职责 | 来源 |
|---|---|---|
| `secrets.py` | 密码 encode/decode/mask、`is_test_account_secret`（调整"哪些 key 算密钥"=池内 password） | 原样搬 |
| `requirements.py` | `infer_account_requirement` / `validate_account_requirement`（用例要什么账号，SUT 无关） | 原样搬 |
| `sources.py` | 新 `load_account_sources`：读静态池 + `dynamic_script`；**无 HTTP 字段、无平台默认、无 browser_base_url 回落** | 替换 `load_test_account_config` |
| `resolver.py` | 新 `resolve_account`：本地生成 / 静态池按 state 匹配 / 调脚本钩子 + 生成 cleanup 令牌 | 重写 |
| `binding.py` | `prepare_web_test_data`（签名不变，编排入口）+ `cleanup_web_test_accounts`（仅脚本清理） | 保留公开入口，重写内部 |

`__init__.py` 重导出公开符号。

**删除**
- 整个 `_HttpTestAccountClient` 及 `_url/_request/_auth_headers/create_account`、HTTP 清号、只服务于它的 jsonpath/模板渲染 helper、所有 `DEFAULT_HTTP_*` 常量。
- `config/pytest_config.py::_calibrate_shared_accounts`（第 119 行）及其调用（第 207 行）。
- `tests/services/test_web_test_data_service.py` 中 HTTP provider 相关测试。
- **彻底删除 `server/services/web_test_data_service.py` 文件名**（方案 b）：连历史数据迁移里的 `from server.services.web_test_data_service import ...` 一并改指到新包。

**修改（触点）**
| 文件 | 改动 |
|---|---|
| `server/api/config_schemas.py` | `WEB_TEST_ACCOUNT_CONFIG_SCHEMA`（约 631–840 行）砍掉 ~15 HTTP 字段，换成 `accounts` + `dynamic_script` |
| `server/api/config.py` | 引用 `mask_test_account_config` 不变（对池内 password 生效），import 路径改到新包 |
| `server/api/users.py` | 引用密钥 helper，import 改到 `test_accounts.secrets` |
| `server/services/web_ui_case_generation_service.py` | 引用 `infer_account_requirement` 等，import 改到 `test_accounts.requirements` |
| `tasks/run_test_task.py` | 调 `cleanup_web_test_accounts` 不变（入口保留），import 改到新包 |
| 历史数据迁移若干 | import 改到新包（配合彻底删旧文件） |

**不动**：`server/api/runs.py`——`prepare_web_test_data(...)` 入口签名不变。

## 6. 运行时流程

```
POST /api/run_test → run_test(同步 def)
  └─ prepare_web_test_data(session, cases, project_id)            [binding.py]
       sources = load_account_sources(...)   读静态池 + dynamic_script
       for 每条 web 用例:
         requirement = 用例.test_data_requirement(或推断)
         resolved = resolve_account(requirement, sources)         [resolver.py]
           ├─ 空表单 / 不存在用户  → 本地生成假值
           ├─ 要真实状态账号(确定性顺序):
           │    ├─ 1. 静态池有 enabled 且 state 匹配 → 用它
           │    ├─ 2. 否则配了 dynamic_script → 调脚本 prepare → {user,pwd,cleanup令牌}
           │    └─ 3. 否则 → WebTestDataError → HTTP 422
         绑定 user/pwd 进用例变量;有 cleanup 令牌则收集
       return cleanup_tokens
  └─ 派 Celery 执行
  └─ 任务 finally → cleanup_web_test_accounts(tokens)  仅对脚本造的号调脚本清理
```
静态池账号常驻、无需清理；仅脚本造的号 prepare/cleanup 成对。

## 7. reset hack 的替代（去掉直连库重置密码后如何兜底）

平台核心不再"替项目保平安"。破坏性用例（改密码/删除）污染共享账号的问题，用三条诚实机制替代，按优先推荐：

1. **隔离优于恢复（默认推荐）**：破坏性场景用 `dynamic_script` 每轮领一次性新鲜账号，跑脏无所谓，cleanup 销毁。
2. **多账号 + 不拿共享号做破坏（纯静态项目）**：静态池多声明几个，破坏性用例指向可弃账号；用例 requirement/profile 已能区分。
3. **项目脚本自恢复**：某项目（含平台自测自己）确需"每轮重置某账号密码"，把该逻辑写进它自己的 `dynamic_script` prepare。该脚本合法拥有目标系统访问权（平台自测脚本本就能连平台库）。**能力不丢，只从平台核心挪到它该在的项目脚本层**，不再污染 `pytest_config.py`。

## 8. 迁移（一次性 data migration）

对每个有 `web/test_accounts` 旧配置的项目：
- `shared_username` + `shared_password`（密文）→ 静态池一条 `{label:"共享账号", username, password, state:"admin", enabled:true}`。
- 旧 `provider==script`（`prepare_script`/`cleanup_script`/`script_config`）→ `dynamic_script` = 该脚本名。
- 旧 15 个 HTTP 字段 → 丢弃。
- 遗留 `default_parameters.user_admin/password_admin` → 一并并入池内 admin 条目。
- 配合彻底删旧文件，改历史迁移的 import 指向新包。
- down 迁移尽力而为（HTTP 字段无法完整还原，仅还原 shared/script 映射）。

## 9. 错误处理

- 池内无匹配 state 账号且未配 `dynamic_script` → `WebTestDataError`，经 `runs.py` 现有兜底转 **HTTP 422**，文案可执行：*"项目「X」未声明满足『锁定账号』的测试账号，请在账号池补充或配置 dynamic_script"*。
- **匹配优先级**：精确 state 命中 > `normal` 请求可回落到 `admin` 账号（保证老用例继续过）> 否则报错。
- `dynamic_script` 在脚本库不存在 / 执行抛错 → `WebTestDataError` 点名脚本 + 原因。
- 密码解密失败 → 明确的配置错。
- **结构性性质**：平台核心对 SUT 零出站 HTTP，"账号工厂接口不可连接 / ReadTimeout"这一整类错误从结构上不可能再出现。

## 10. 测试策略

- 新单测（`resolver` / `sources` / `binding`）：本地生成、池匹配（命中/未命中/disabled 过滤）、匹配优先级、脚本 prepare+cleanup、各错误文案、`sources` 无平台默认值。
- 需求推断测试（`infer/validate`）保留并按新结构微调。
- 删除 HTTP provider 老测试。
- **端到端自测**：平台自测项目重配为静态池（demo_admin）→ 验证 Web 登录用例在**无 `_calibrate_shared_accounts`** 下照常通过；如含破坏性用例，加可选"重置 demo_admin"workflow 脚本再验。

## 11. 后续（不在本子项目）

- 子项目 B：通用化接口契约来源（`functional_cases.py` 的 `allowed_ports`/localhost `/docs` 限制）。
- 子项目 C：验证码通用化 / 清理（`utils/captcha/service.py` 写死 forex 接口，且为死代码）。
