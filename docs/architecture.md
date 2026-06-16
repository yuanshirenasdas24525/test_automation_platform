# 自动化测试平台架构重构方案

> ⚠️ **本文档是历史设计稿（2025 年重构方案），实际落地结构请参考项目根 `AGENTS.md`。**
> 文中提到的 `src/` 前缀、`core/` 目录、`api_runner.py`/`mobile_runner.py` 等已在 v2 重构中废弃或重组。
>
> 基于对现有 `test_automation_platform` 代码的分析，给出完整的评估、目标架构、数据模型重构、App UI 接入方案、AI 能力规划、以及分阶段演进路线。

---

## 一、现状评估（Where We Are）

### 1.1 已经做对的东西（继承，不要动）

| 模块 | 文件 | 评价 |
|---|---|---|
| 项目/模块树状结构 | `models/project.py`、`models/module.py` | 设计合理，直接复用，多项目场景够用 |
| 报告通用化 | `models/test_step_report.py` | **已经预留了** `action / target / input_data / output_data / screenshot_path / page_info`，这是通用步骤报告，非常好，不用改 |
| 配置热更新 | `utils/reload_config.py` + `config_store` | `config_center.reload()` 的机制可以继承 |
| Mobile 核心分层 | `src/core/mobile/app_action.py` | Finder → ValueResolver → Executor → Assertion → Cache 五层，**已经是步骤化设计**，只需要把输入从 Excel 行改成 DB 行 |
| Celery 异步执行 | `celery_app.py` + `tasks/run_test_task.py` | 保留，只需要队列按 category 拆分 |
| Allure 报告体系 | `data_sync.py` 的 `sync_allure_to_db` | 保留，未来可以逐步用自建报告替代 |

### 1.2 卡住扩展的核心问题

**问题 1：`TestCase` 表是 API 专用形状（致命）**

```python
# src/database/models/test_case.py
class TestCase(Base):
    method = Column(String, nullable=False)        # ← HTTP method
    path = Column(String)                          # ← HTTP 路径
    headers / params / data_type / sql_query ...   # ← 全是 HTTP 字段
```

一条用例 = 一个 HTTP 请求。没有「多步骤」概念，App UI 的 `by/locator/action/value` 根本插不进来。**这是阻断 App 接入的根因。**

**问题 2：Runner 协议不统一，平台与 CLI 两条路**

- 平台调用：`main.py` → Celery → `pytest.main(tests/service_run_executor.py::test_{category}_runner)` → 但 `service_run_executor.py` 里只有 `test_api_runner`，**没有 `test_app_runner`**。
- CLI 调用：`src/runners/api_runner.py / mobile_runner.py / web_ui_runner.py` 是各自独立的 pytest 包装器，和平台没对接。
- 结果：App UI 目前能跑（通过 `tests/test_app_ui.py` 命令行启动），但**从平台发起跑不了**。

**问题 3：App 用例还在 Excel / YAML 文件里**

`tests/test_app_ui.py` 读的是 `ProjectPaths.ui_register_case` 这类文件路径，`process_ui_row` 解析的是 Excel 列。你说要"数据库存储步骤、平台即用即编排"，这一层完全缺失。

**问题 4：`TestCaseCreate` Pydantic 模型也是 API 形状**

前端表单和数据库绑死了，加 App 用例要改前端、改 Pydantic、改 SQLAlchemy、改 API 接口，牵一发动全身。

**问题 5：没有变量/环境/Hook 的体系**

- 目前变量是放在 `ParameterCache`（内存）和 `ExecutionContext`（单次执行）
- 没有"项目级环境变量"、"全局变量"、"前置/后置 Hook"的数据库建模
- 中等规模团队必需

**问题 6：没有 AI 层的任何预留**

- 没有 LLM 网关
- 没有文档导入机制
- 没有用例生成/评审服务

**问题 7：前端是原生 HTML+JS**

`client/index.html + script.js + style.css` —— 能跑，但要支持多测试类型、可视化步骤编排、报告图表、AI 交互，这个前端会撑不住。**会用 React 重写**。

---

## 二、目标架构（Where We're Going）

### 2.1 整体分层图

```
┌─────────────────────────────────────────────────────────────────┐
│                      用户 (10-50人 / 多项目)                      │
└────────────────────────────────┬────────────────────────────────┘
                                 │
┌────────────────────────────────┴────────────────────────────────┐
│             接入层 (Frontend) — React 18 + Vite                  │
│   TypeScript + Ant Design 5 + Zustand + TanStack Query +        │
│   React Router v6 + Monaco Editor                                │
└────────────────────────────────┬────────────────────────────────┘
                                 │ REST + WebSocket(日志流)
┌────────────────────────────────┴────────────────────────────────┐
│                    平台服务层 (Platform)                         │
│  FastAPI                                                         │
│  ├─ auth/           权限、用户、项目成员                          │
│  ├─ project/        项目、模块、环境                              │
│  ├─ case/           用例 + 多步骤                                │
│  ├─ execution/      执行编排、报告聚合、WebSocket 日志流          │
│  ├─ scheduler/      定时任务                                      │
│  ├─ ai/             AI 能力编排（调 AI Gateway）                  │
│  └─ device/         设备池管理（App 专用）                        │
└─────┬─────────────────────┬─────────────────────┬───────────────┘
      │ Celery              │ HTTP                │ HTTP
┌─────┴──────┐   ┌─────────┴───────────┐    ┌───┴───────────┐
│  Runner     │   │   AI Gateway        │    │  Agent        │
│  (执行引擎) │   │   (LiteLLM 封装)    │    │  (App 专用)   │
├─────────────┤   ├─────────────────────┤    ├───────────────┤
│ APIRunner   │   │ /chat               │    │ 设备发现       │
│ AppRunner   │   │ /embedding          │    │ Appium 启停    │
│ WebRunner   │   │ /parse_doc          │    │ 截图/录屏上传  │
│ LoadRunner  │   │ /generate_case      │    │ 设备锁         │
│             │   │ /review_case        │    │                │
│             │   │ /analyze_failure    │    │                │
└─────┬───────┘   └─────────┬───────────┘    └───┬───────────┘
      │                     │                    │
┌─────┴─────────────────────┴────────────────────┴───────────────┐
│                  基础设施层 (Infra)                              │
│ PostgreSQL │ Redis │ MinIO/S3 │ Appium Server(池) │ 设备         │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 关键设计决策（已确认）

| 决策 | 选择 | 理由 |
|---|---|---|
| 平台 Web 框架 | 继续 FastAPI | 你已经用了，合理 |
| **数据库** | **PostgreSQL** ✅ | 原生 JSONB、GIN 索引、更丰富的 JSON 操作 |
| **ORM 迁移** | **Alembic** | SQLAlchemy 官方迁移工具 |
| 任务队列 | 继续 Celery + Redis | 已用；按 category 拆 queue（`api`/`app`/`web`/`ai`） |
| 用例存储 | DB 驱动 | TestCase + TestStep（新增） |
| Runner 形态 | 每类测试独立进程 + 统一协议 | 解耦、独立扩展、故障隔离 |
| AI 网关 | 独立服务 + LiteLLM 底座 | 统一 Provider、方便切换、计费统计 |
| **前端** | **React 18 + TypeScript + Vite + Ant Design 5** ✅ | 组件生态成熟、后台场景友好 |
| 对象存储 | MinIO（自建 S3） | 存截图、录屏、日志包 |
| 文档格式（首个 AI） | OpenAPI 3.0 (Swagger) | Swagger → API 用例优先做 |

---

## 三、数据模型重构（最关键的一步）

### 3.1 核心思路

**把 TestCase 变成"用例元信息壳"，把 HTTP/App/Web 动作都下沉到 TestStep**。一条用例可以：

- 只包含一个 `http_request` step（等价于你现在的 API 用例）
- 包含一串 `app_tap / app_input / app_swipe / assert` step（App UI 用例）
- 混合步骤：`http_request（登录拿token） → app_start_app → app_input（填token）`（这是你未来的杀手锏）

### 3.2 新增/修改的表（PostgreSQL 版）

#### 表 `test_cases` — 改造（保留字段作兼容）

```python
from sqlalchemy.dialects.postgresql import JSONB

class TestCase(Base):
    __tablename__ = "test_cases"
    id = Column(Integer, primary_key=True)
    module_id = Column(Integer, ForeignKey("modules.id"))

    # === 通用元信息 ===
    name = Column(String, nullable=False)
    description = Column(String)
    case_type = Column(String, default="api", index=True)  # api|app|web|mixed
    tags = Column(JSONB, default=list)                      # 支持标签筛选（GIN 索引）
    skip = Column(Boolean, default=False)
    priority = Column(Integer, default=2)                   # 0/1/2/3
    sort_order = Column(Integer, default=0)

    # === 执行控制 ===
    env_id = Column(Integer, ForeignKey("test_environments.id"), nullable=True)
    pre_hook = Column(JSONB)         # [{type:'sql'|'http'|'script', ...}]
    post_hook = Column(JSONB)
    variables = Column(JSONB)        # 用例级变量
    timeout = Column(Integer, default=60)
    retry = Column(Integer, default=0)

    # === 兼容字段（过渡期保留；半年后删除） ===
    method = Column(String, nullable=True)
    path = Column(String, nullable=True)
    headers = Column(Text, nullable=True)
    data_type = Column(String, nullable=True)
    params = Column(Text, nullable=True)
    file_path = Column(String, nullable=True)
    extract_data = Column(Text, nullable=True)
    sql_query = Column(Text, nullable=True)
    assertion = Column(Text, nullable=True)
    wait_time = Column(Integer, default=0)

    module = relationship("Module", back_populates="test_cases")
    steps = relationship("TestStep", back_populates="case",
                         cascade="all, delete-orphan",
                         order_by="TestStep.step_order")
```

#### 表 `test_steps` — 新增（核心）

```python
class TestStep(Base):
    __tablename__ = "test_steps"
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("test_cases.id"), index=True)

    # === 基础 ===
    step_order = Column(Integer, default=0)
    step_name = Column(String, nullable=False)
    step_type = Column(String, nullable=False, index=True)
    # step_type 枚举：
    #   http_request, sql_query, script,
    #   app_tap, app_input, app_swipe, app_press, app_launch, app_close,
    #   app_wait, app_screenshot, app_back,
    #   web_goto, web_click, web_input, web_select, web_wait,
    #   assert, sleep
    skip = Column(Boolean, default=False)

    # === 核心载荷（PostgreSQL JSONB，可 GIN 索引） ===
    config = Column(JSONB, nullable=False)
    #  http_request: {method, path, headers, data_type, params, file_path}
    #  app_tap:      {by, locator, sliding_location, wait}
    #  app_input:    {by, locator, value, clear_first}

    extract = Column(JSONB)    # [{name, from:'response.body'|'text'|'attr', jsonpath:'...'}]
    assertion = Column(JSONB)  # [{type:'equal'|'contains'|'jsonpath', target, expected}]

    wait_before = Column(Float, default=0)
    timeout = Column(Integer, default=30)
    retry = Column(Integer, default=0)
    on_failure = Column(String, default="stop")  # stop | continue | retry

    case = relationship("TestCase", back_populates="steps")
```

**重点**：所有 step 类型共享 `extract / assertion / wait_before / timeout / retry / on_failure` —— 这就是"API 和 App 步骤能在同一条用例里混用"的基石。

#### 表 `test_environments` — 新增

```python
class TestEnvironment(Base):
    __tablename__ = "test_environments"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    name = Column(String, nullable=False)   # dev / staging / prod
    category = Column(String)               # api / app / web
    host = Column(String)                   # API base url
    device_pool = Column(String)            # App 设备池标签
    browser_config = Column(JSONB)          # Web 浏览器配置
    variables = Column(JSONB)               # 环境变量
    secrets = Column(JSONB)                 # 敏感信息（加密存储，pgcrypto）
```

#### 表 `test_variables` — 新增（可选，也可合并到 env）

```python
class TestVariable(Base):
    __tablename__ = "test_variables"
    id = Column(Integer, primary_key=True)
    scope = Column(String)   # global | project | env | case
    scope_id = Column(Integer)
    key = Column(String, nullable=False)
    value = Column(Text)
    secret = Column(Boolean, default=False)
```

#### 表 `devices` — App 专用

```python
class Device(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True)
    udid = Column(String, unique=True, nullable=False)
    platform = Column(String)      # Android / iOS
    platform_version = Column(String)
    device_name = Column(String)
    agent_host = Column(String)    # 这台设备挂在哪台 agent 机器上
    appium_port = Column(Integer)
    status = Column(String)        # idle / busy / offline
    last_heartbeat = Column(DateTime)
    pool = Column(String)          # 默认池 default；可按业务分池
    owner_execution_id = Column(Integer, nullable=True)  # 占用锁
```

#### 表 `executions` & `step_executions` — 改名重构

当前 `test_reports` / `test_step_reports` 建议改名为 `executions` / `step_executions`（不改名也行，语义上更准确）。

`test_step_reports` 已经非常通用，基本不用动。建议补充：

```python
# 在 TestStepReport 上补充
case_execution_id = Column(Integer, index=True)  # 关联单条用例的子执行
step_id = Column(Integer)                        # 关联 test_steps.id（便于回溯）
attachments = Column(JSONB)                      # [{name, url(minio)}] 截图/日志包
```

### 3.3 数据迁移脚本

**使用 Alembic**（PostgreSQL + SQLAlchemy 的正统迁移工具）：

```bash
# 初始化 alembic
alembic init database/migrations

# 生成第一个迁移
alembic revision --autogenerate -m "v2_add_test_steps_and_env"

# 应用迁移
alembic upgrade head
```

数据迁移脚本 `database/migrations/data_migrations/v2_cases_to_steps.py`：

```python
from src.database.db import DB
from src.database.models import TestCase, TestStep

def migrate():
    db = DB()
    cases = db.session.query(TestCase).all()
    for case in cases:
        if not case.method or case.steps:
            continue  # 已迁移或非 API 用例
        step = TestStep(
            case_id=case.id,
            step_order=0,
            step_name=case.name,
            step_type="http_request",
            config={
                "method": case.method,
                "path": case.path,
                "headers": case.headers,
                "data_type": case.data_type,
                "params": case.params,
                "file_path": case.file_path,
            },
            extract=parse_extract_data(case.extract_data),
            assertion=parse_assertion(case.assertion),
            wait_before=case.wait_time or 0,
        )
        db.session.add(step)
        case.case_type = "api"
    db.session.commit()
```

---

## 四、Runner 协议与执行流程（统一抽象）

### 4.1 Runner 基类

新建 `src/runners/base.py`：

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class StepResult:
    status: str                  # passed | failed | broken | skipped
    duration: float              # 秒
    output: Any = None           # 输出数据（response / ui text / ...）
    extracted: Dict = field(default_factory=dict)
    assertions: List[Dict] = field(default_factory=list)
    error: Optional[str] = None
    screenshot: Optional[str] = None   # 本地路径或 MinIO URL
    logs: List[str] = field(default_factory=list)


class BaseRunner(ABC):
    """所有 Runner 的统一接口：API/App/Web 共用一套执行协议"""

    @abstractmethod
    def setup(self, env: dict, case_context: "ExecutionContext"):
        """用例级 setup：打开 session / 启动 App / 打开浏览器"""

    @abstractmethod
    def execute_step(self, step: dict, case_context: "ExecutionContext") -> StepResult:
        """执行单个步骤 —— 核心方法"""

    @abstractmethod
    def teardown(self, case_context: "ExecutionContext"):
        """用例级 teardown：关闭 session / 关闭 App / 关浏览器"""

    def supported_step_types(self) -> List[str]:
        return []
```

### 4.2 Runner 分派器

`src/runners/dispatcher.py`：

```python
from src.runners.api_runner import APIRunner
from src.runners.app_runner import AppRunner
from src.runners.web_runner import WebRunner

RUNNER_MAP = {
    "http_request": APIRunner,
    "sql_query":    APIRunner,
    "app_tap":      AppRunner,
    "app_input":    AppRunner,
    "app_swipe":    AppRunner,
    # ... 所有 app_* 都归 AppRunner
    "web_click":    WebRunner,
}

class CaseExecutor:
    """协调一条用例的多步骤执行，自动根据 step_type 派发到不同 Runner"""
    def __init__(self, case, env, context):
        self.case = case
        self.env = env
        self.context = context
        self._runners = {}

    def run(self):
        self._run_hooks(self.case.pre_hook)
        results = []
        for step in self.case.steps:
            runner = self._get_runner(step.step_type)
            result = runner.execute_step(step.to_dict(), self.context)
            results.append(result)
            if result.status == "failed" and step.on_failure == "stop":
                break
        self._run_hooks(self.case.post_hook)
        self._cleanup_runners()
        return results
```

### 4.3 APIRunner 改造（最小改动）

```python
# src/runners/api_runner.py
from src.runners.base import BaseRunner, StepResult
from src.core.api.factory import create_api_client

class APIRunner(BaseRunner):
    def setup(self, env, ctx):
        self._client = create_api_client(ctx.record_property)
        self._client.set_base_url(env.host)

    def execute_step(self, step, ctx):
        if step["step_type"] == "http_request":
            cfg = step["config"]
            resp = self._client.send(
                method=cfg["method"],
                path=cfg["path"],
                headers=cfg.get("headers"),
                data=cfg.get("params"),
                data_type=cfg.get("data_type"),
                files=cfg.get("file_path"),
            )
            extracted = self._client.handle_extract(resp, step["extract"], ctx)
            assertions = self._client.assert_result(resp, step["assertion"], ctx)
            return StepResult(
                status="passed" if all(a["passed"] for a in assertions) else "failed",
                duration=resp.elapsed.total_seconds(),
                output=resp.json_or_text(),
                extracted=extracted,
                assertions=assertions,
            )
```

### 4.4 AppRunner（新建，复用现有 AppAction）

```python
# src/runners/app_runner.py
from src.runners.base import BaseRunner, StepResult
from src.core.mobile.start_app import AppManager

STEP_TYPE_TO_ACTION = {
    "app_tap":    "click",
    "app_input":  "input",
    "app_swipe":  "swipe",
    "app_press":  "press",
    "app_wait":   "wait",
    "app_back":   "back",
}

class AppRunner(BaseRunner):
    def setup(self, env, ctx):
        # 从设备池申请一台设备
        self._app = AppManager(env.device_pool).acquire_device(
            execution_id=ctx.execution_id
        )

    def execute_step(self, step, ctx):
        cfg = step["config"]
        legacy_step = {
            "by":       cfg.get("by"),
            "locator":  cfg.get("locator"),
            "action":   STEP_TYPE_TO_ACTION[step["step_type"]],
            "value":    cfg.get("value"),
            "deposit":  step["extract"][0]["name"] if step["extract"] else None,
            "expected": step["assertion"][0]["expected"] if step["assertion"] else None,
            "sliding_location": cfg.get("sliding_location"),
            "wait": step.get("wait_before", 0),
        }
        try:
            result = self._app.app_steps(legacy_step)
            screenshot = self._app.take_screenshot()
            return StepResult(
                status="passed", duration=..., output=result,
                screenshot=screenshot,
            )
        except AssertionError as e:
            return StepResult(status="failed", error=str(e),
                              screenshot=self._app.take_screenshot())

    def teardown(self, ctx):
        self._app.release_device()
```

### 4.5 WebRunner（P5 阶段）

```python
# src/runners/web_runner.py
class WebRunner(BaseRunner):
    """Phase 5 再实现。用 Playwright 而不是 Selenium。"""
    pass
```

---

## 五、App UI 接入平台的完整方案

### 5.1 平台发起 App 执行的完整链路

```
用户点击 "运行用例"
  └─ POST /api/run_test {project_id, module_id?, case_id?, env_id?}
      └─ main.py#run_test
          ├─ 查出 cases 和 steps（从 DB）
          ├─ 创建 Execution 记录
          └─ Celery 派发到对应 queue
              ├─ case_type=api  → api_queue
              ├─ case_type=app  → app_queue   ← 新增
              ├─ case_type=web  → web_queue   ← 预留
              └─ case_type=mixed → mixed_queue
                   └─ tasks/run_case_task.py
                       └─ CaseExecutor(case, env, ctx).run()
                            ├─ APIRunner.execute_step(...)
                            ├─ AppRunner.execute_step(...)    ← 混合用例支持
                            └─ StepResult → DB (step_executions)
```

### 5.2 设备管理（分两阶段）

#### 阶段 A（起步期，1-2 周搞定）

- 一台测试机 Mac/Windows，插 4-6 台真机
- 机器上跑一个 **Agent 进程**（FastAPI）
- Agent 负责：
  - 启动多个 Appium Server（每个设备一个端口：4723, 4733, 4743...）
  - 上报设备心跳到平台（HTTP POST /api/devices/heartbeat，每 10s）
  - 提供"占用/释放设备"的 HTTP 接口
- 平台的 App Runner 不直接管设备，通过 Agent 申请

#### 阶段 B（扩展期，按需升级）

- 多台 Agent 机器
- 平台侧调度器：优先分配空闲设备、平台池切分（冒烟池、回归池）
- 录屏功能（scrcpy 或 iOS 的 xctestrunner）

### 5.3 Agent 骨架代码

`src/agent/main.py`（独立部署，不跟平台同进程）：

```python
from fastapi import FastAPI
import subprocess, requests, threading, time, os

app = FastAPI(title="Device Agent")
PLATFORM_URL = os.getenv("PLATFORM_URL", "http://platform:8000")
AGENT_HOST = os.getenv("AGENT_HOST")

class DevicePool:
    def __init__(self):
        self.devices = {}

    def scan_devices(self):
        # adb devices + idevice_id -l
        ...

    def start_appium(self, udid):
        port = self._next_port()
        proc = subprocess.Popen(
            ["appium", "-p", str(port), "--base-path", "/wd/hub"]
        )
        self.devices[udid] = {"port": port, "pid": proc.pid, "status": "idle"}
        return port

pool = DevicePool()

@app.post("/acquire")
def acquire(udid: str, execution_id: int):
    # 锁定设备、返回 Appium 地址
    ...

@app.post("/release")
def release(udid: str):
    ...

def heartbeat():
    while True:
        requests.post(f"{PLATFORM_URL}/api/devices/heartbeat", json={
            "agent_host": AGENT_HOST,
            "devices": pool.snapshot(),
        })
        time.sleep(10)

threading.Thread(target=heartbeat, daemon=True).start()
```

### 5.4 App 用例编辑器（React 前端）

前端提供两种编辑模式：

1. **表单模式**：每个 step 一个卡片，字段随 step_type 动态切换
   - `app_tap` → by / locator / 滑动查找
   - `app_input` → by / locator / value / 清空后输入
   - `assert` → 类型 / 目标 / 预期
2. **录制模式（阶段 B）**：用 Appium Inspector 连接设备，选中元素自动填表

---

## 六、AI 能力规划（独立 AI Gateway）

### 6.1 AI Gateway 结构

独立进程，FastAPI 服务，只对内网：

```
ai-gateway/
├─ main.py                  # FastAPI 入口
├─ llm/
│  ├─ client.py             # LiteLLM 封装
│  └─ router.py             # Provider 路由（默认/Fallback）
├─ prompts/                 # Jinja2 模板
│  ├─ parse_swagger.jinja
│  ├─ generate_api_case.jinja
│  ├─ generate_app_case.jinja
│  ├─ review_case.jinja
│  └─ analyze_failure.jinja
├─ services/
│  ├─ doc_parser.py
│  ├─ case_generator.py
│  ├─ case_reviewer.py
│  └─ failure_analyzer.py
└─ api/
   ├─ parse_doc.py
   ├─ generate_case.py
   ├─ review_case.py
   └─ analyze_failure.py
```

### 6.2 五个 AI 能力的优先级（已确认）

| 优先级 | 能力 | ROI | 难度 |
|---|---|---|---|
| ⭐⭐⭐⭐⭐ **P0** | **Swagger/OpenAPI → API 用例** | 极高 | 低 |
| ⭐⭐⭐⭐ P1 | AI 生成用例（基于需求文档） | 高 | 中 |
| ⭐⭐⭐⭐ P1 | AI 失败分析 | 高 | 低 |
| ⭐⭐⭐ P2 | AI 用例检查 | 中 | 中 |
| ⭐⭐⭐ P2 | AI 文档分析（PDF PRD → 知识库） | 中 | 中 |

### 6.3 Swagger → API 用例（P0）

80% 不需要 AI，20% 用 AI 补：

```python
# ai-gateway/services/doc_parser.py
def parse_openapi_to_steps(spec: dict) -> list[dict]:
    cases = []
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            step_config = {
                "method": method.upper(),
                "path": path,
                "headers": build_headers(op.get("parameters", [])),
                "params": build_example_body(op.get("requestBody")),
                "data_type": detect_content_type(op),
            }
            assertions = llm_suggest_assertions(op)
            cases.append({
                "case_name": op.get("summary") or f"{method} {path}",
                "tags": op.get("tags", []),
                "steps": [{
                    "step_type": "http_request",
                    "config": step_config,
                    "assertion": assertions,
                }]
            })
    return cases
```

### 6.4 平台侧 AI 交互设计（React 前端）

```
┌──────────────────────────────────────┐
│ 📁 项目  📂 模块  📄 用例              │
│  ┌────────────────────────────┐      │
│  │ 💡 AI 助手                  │      │
│  │ ├─ 导入 Swagger 生成用例    │      │
│  │ ├─ 从需求文档生成用例       │      │
│  │ ├─ 智能审查当前用例         │      │
│  │ ├─ 分析失败原因             │      │
│  │ └─ 与我对话（通用）          │      │
│  └────────────────────────────┘      │
└──────────────────────────────────────┘
```

---

## 七、目录结构重构方案

```
test_automation_platform/
├─ platform/                    # ← 平台主服务
│  ├─ main.py
│  ├─ api/                      # ← main.py 路由拆分
│  │  ├─ projects.py
│  │  ├─ modules.py
│  │  ├─ cases.py
│  │  ├─ steps.py               # ← 新增 step CRUD
│  │  ├─ executions.py
│  │  ├─ environments.py        # ← 新增
│  │  ├─ devices.py             # ← 新增
│  │  ├─ ai.py                  # ← 新增（转发到 AI Gateway）
│  │  ├─ auth.py                # ← 新增
│  │  └─ config.py
│  ├─ services/
│  │  ├─ execution_service.py
│  │  ├─ ai_proxy.py
│  │  └─ device_service.py
│  └─ websocket/
│     └─ log_stream.py
│
├─ runners/
│  ├─ base.py                   # BaseRunner 协议
│  ├─ dispatcher.py             # CaseExecutor
│  ├─ api_runner.py
│  ├─ app_runner.py
│  ├─ web_runner.py             # 预留
│  └─ load_runner.py
│
├─ core/                        # 底层能力（v2 已拆分至 runners/context/ + runners/app/ + utils/captcha/）
│  ├─ api/
│  ├─ mobile/
│  ├─ web/
│  ├─ context/
│  ├─ proxy_mock/
│  └─ captcha_solver/
│
├─ database/
│  ├─ base.py
│  ├─ db.py
│  ├─ models/
│  │  ├─ project.py
│  │  ├─ module.py
│  │  ├─ test_case.py
│  │  ├─ test_step.py           # ← 新增
│  │  ├─ test_environment.py    # ← 新增
│  │  ├─ test_variable.py       # ← 新增
│  │  ├─ device.py              # ← 新增
│  │  ├─ execution.py
│  │  └─ step_execution.py
│  ├─ schemas/                  # Pydantic schemas（补齐）
│  └─ migrations/               # Alembic
│     ├─ env.py
│     ├─ versions/
│     └─ data_migrations/
│        └─ v2_cases_to_steps.py
│
├─ ai-gateway/                  # 独立服务
│  ├─ main.py
│  ├─ llm/
│  ├─ prompts/
│  ├─ services/
│  └─ api/
│
├─ agent/                       # 设备 Agent
│  ├─ main.py
│  ├─ device_pool.py
│  └─ appium_supervisor.py
│
├─ tasks/                       # Celery tasks（按 category 拆）
│  ├─ api_task.py
│  ├─ app_task.py
│  ├─ web_task.py
│  └─ ai_task.py
│
├─ frontend/                    # ← React 18 + TS + Vite + AntD 5
│  ├─ src/
│  │  ├─ api/                   # axios + TanStack Query
│  │  ├─ components/
│  │  ├─ pages/
│  │  │  ├─ projects/
│  │  │  ├─ cases/
│  │  │  │  ├─ CaseList.tsx
│  │  │  │  ├─ CaseEditor.tsx       # ← 核心：动态步骤编辑器
│  │  │  │  └─ StepFormFactory.tsx  # 按 step_type 动态渲染表单
│  │  │  ├─ executions/
│  │  │  ├─ environments/
│  │  │  ├─ devices/
│  │  │  └─ ai/
│  │  ├─ stores/               # Zustand
│  │  ├─ router.tsx             # React Router v6
│  │  └─ main.tsx
│  ├─ package.json
│  ├─ vite.config.ts
│  └─ tsconfig.json
│
├─ client/                      # ← 老前端，保留做过渡 fallback
├─ config/
├─ tests/
├─ docker/
├─ docs/
├─ celery_app.py
└─ requirements.txt
```

---

## 八、演进路线图（14 周）

```
Week 1-2  │██░░░░░░░░│ Phase 0+1: 目录骨架 + DB schema 升级(Alembic) + 迁移脚本
Week 3-4  │░██░░░░░░░│ Phase 2: Runner 协议 + APIRunner 重构 + 兼容性回归
Week 5-7  │░░░███░░░░│ Phase 3: App Runner + Agent + App 用例编辑器(React)
Week 8-9  │░░░░░██░░░│ Phase 4a: AI Gateway + LiteLLM + Swagger 转用例 ← P0
Week 10-13│░░░░░░░███│ Phase 4b: AI 生成用例 → AI 失败分析 → AI 审查
Week 14+  │░░░░░░░░░█│ Phase 5: Web Runner (Playwright) + AI 文档分析
```

前端 React 工程建议**从 Week 1 开始并行启动**，最先搭出来的是：
- 项目管理页（能直接跑通现有 API）
- 用例管理页（拆开成 CaseList + CaseEditor，CaseEditor 是关键）
- StepFormFactory 组件（根据 step_type 动态渲染不同表单）

---

## 九、React 前端工程建议

### 9.1 技术栈（已确认）

| 类别 | 选型 | 用途 |
|---|---|---|
| 核心框架 | React 18 + TypeScript | 基础 |
| 构建工具 | Vite 5 | 快 |
| 路由 | React Router v6 | 路由 |
| 状态管理 | Zustand | 轻量、不过度设计 |
| API 状态 | TanStack Query v5 | 缓存、加载、重试 |
| UI 库 | Ant Design 5 | 后台组件王牌 |
| 图表 | Recharts / ECharts | 报告仪表盘 |
| 编辑器 | Monaco Editor | JSON / SQL / 脚本 |
| 拖拽 | @dnd-kit/core | 步骤排序 |
| 测试 | Vitest + React Testing Library | 单测 |

### 9.2 关键组件：StepFormFactory

用例编辑器的核心是"根据 step_type 动态渲染表单"：

```tsx
// frontend/src/pages/cases/StepFormFactory.tsx
import { Form, Input, Select } from 'antd';
import type { StepType } from '@/types/step';

const FORM_MAP: Record<StepType, React.FC<{step: Step}>> = {
  http_request: HttpRequestForm,
  app_tap: AppTapForm,
  app_input: AppInputForm,
  app_swipe: AppSwipeForm,
  assert: AssertForm,
  // ...
};

export function StepFormFactory({ step, onChange }: Props) {
  const FormComponent = FORM_MAP[step.step_type];
  return <FormComponent step={step} onChange={onChange} />;
}
```

每个 step_type 对应一个专用表单组件，字段跟 `TestStep.config` JSON schema 对齐。

---

## 十、马上可以动手做的 3 件事（Quick Wins）

### 10.1 今天可以做：建 `test_steps` 表（PostgreSQL）

```sql
-- 使用 JSONB + GIN 索引
CREATE TABLE test_steps (
    id SERIAL PRIMARY KEY,
    case_id INT NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
    step_order INT DEFAULT 0,
    step_name VARCHAR(255) NOT NULL,
    step_type VARCHAR(50) NOT NULL,
    skip BOOLEAN DEFAULT FALSE,
    config JSONB NOT NULL,
    extract JSONB,
    assertion JSONB,
    wait_before FLOAT DEFAULT 0,
    timeout INT DEFAULT 30,
    retry INT DEFAULT 0,
    on_failure VARCHAR(20) DEFAULT 'stop'
);

CREATE INDEX idx_steps_case ON test_steps(case_id);
CREATE INDEX idx_steps_type ON test_steps(step_type);
CREATE INDEX idx_steps_config_gin ON test_steps USING GIN (config);

-- TestCase 新增字段
ALTER TABLE test_cases ADD COLUMN case_type VARCHAR(20) DEFAULT 'api';
ALTER TABLE test_cases ADD COLUMN tags JSONB;
ALTER TABLE test_cases ADD COLUMN env_id INT;
ALTER TABLE test_cases ADD COLUMN pre_hook JSONB;
ALTER TABLE test_cases ADD COLUMN post_hook JSONB;
ALTER TABLE test_cases ADD COLUMN variables JSONB;
ALTER TABLE test_cases ADD COLUMN timeout INT DEFAULT 60;
ALTER TABLE test_cases ADD COLUMN retry INT DEFAULT 0;
ALTER TABLE test_cases ADD COLUMN priority INT DEFAULT 2;
ALTER TABLE test_cases ALTER COLUMN method DROP NOT NULL;
ALTER TABLE test_cases ALTER COLUMN data_type DROP NOT NULL;
ALTER TABLE test_cases ALTER COLUMN assertion DROP NOT NULL;

CREATE INDEX idx_cases_tags_gin ON test_cases USING GIN (tags);
```

**推荐用 Alembic 自动生成**，见 §3.3。

### 10.2 这周可以做：`BaseRunner` + `CaseExecutor`

30-50 行代码，先跑通"旧 API 用例经过新 CaseExecutor 也能跑"。

### 10.3 下周可以做：AppRunner 骨架 + Hello World

1. `src/runners/app_runner.py` 按 §4.4 骨架写
2. `service_run_executor.py` 加 `test_app_runner`：
   ```python
   def test_app_runner(self, case, record_property):
       from src.runners.dispatcher import CaseExecutor
       CaseExecutor(case, env=None,
                    context=ExecutionContext(record_property)).run()
   ```
3. 手工插一条 App 用例到 DB（case_type='app'，一个 `app_tap` step）
4. 从平台点运行，观察通链路

---

## 十一、已确认的技术栈选型

1. **数据库：PostgreSQL** ✅
   - JSON 字段使用原生 `JSONB` 类型（比 MySQL JSON 支持更好，支持 GIN 索引）
   - `config` / `extract` / `assertion` / `variables` / `tags` 等字段都用 JSONB
   - 迁移工具用 **Alembic**
   - 敏感信息可用 `pgcrypto` 加密

2. **前端：React 重写** ✅
   - 技术栈：React 18 + TypeScript + Vite + **Ant Design 5** + Zustand + React Router v6 + TanStack Query + Monaco Editor
   - 新建 `frontend/` 目录，老 `client/` 先保留做 fallback
   - Vite 打包后由 FastAPI 的 StaticFiles 挂载

3. **AI 能力顺序：Swagger 转用例优先** ✅
   - Phase 4a（Week 8-9）：AI Gateway 骨架 + LiteLLM + Swagger/OpenAPI 转用例
   - Phase 4b（Week 10-13）：AI 生成用例 → AI 失败分析 → AI 审查 → AI 文档分析

---

## 附录 A：一条"混合用例"示例

展示 API + App 组合威力。用例：**"用 API 创建用户 → App 登录验证"**

```json
{
  "name": "新用户注册后 App 登录",
  "case_type": "mixed",
  "env_id": 3,
  "steps": [
    {
      "step_order": 0,
      "step_name": "API 创建用户",
      "step_type": "http_request",
      "config": {
        "method": "POST",
        "path": "/api/users",
        "data_type": "application/json",
        "params": {"username":"test_${timestamp}","pwd":"123456"}
      },
      "extract": [{"name":"new_user_id","from":"response.body","jsonpath":"$.data.id"}],
      "assertion": [{"type":"jsonpath","target":"$.code","expected":0}]
    },
    {
      "step_order": 1,
      "step_name": "启动 App",
      "step_type": "app_launch",
      "config": {"appPackage":"com.example.app"}
    },
    {
      "step_order": 2,
      "step_name": "输入用户名",
      "step_type": "app_input",
      "config": {
        "by":"id","locator":"et_username",
        "value":"test_${timestamp}"
      }
    },
    {
      "step_order": 3,
      "step_name": "点击登录",
      "step_type": "app_tap",
      "config": {"by":"id","locator":"btn_login"}
    },
    {
      "step_order": 4,
      "step_name": "断言登录成功",
      "step_type": "assert",
      "config": {"by":"id","locator":"tv_home_title"},
      "assertion": [{"type":"text_equal","expected":"首页"}]
    }
  ]
}
```

---

## 附录 B：推荐的技术依赖清单

### Python 后端（补充到 `requirements.txt`）

```
# DB 迁移（强烈推荐）
alembic~=1.13

# AI & LLM
litellm~=1.50
openai~=1.50
tiktoken

# 文档解析
openapi-schema-pydantic
pypdf
markdownify

# Web UI（Phase 5）
playwright~=1.40

# 对象存储
minio~=7.2

# 实时推送
websockets~=12.0
```

### React 前端 `frontend/package.json`

```json
{
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.22.0",
    "antd": "^5.14.0",
    "@ant-design/icons": "^5.3.0",
    "@ant-design/pro-components": "^2.6.0",
    "zustand": "^4.5.0",
    "@tanstack/react-query": "^5.17.0",
    "axios": "^1.6.0",
    "@monaco-editor/react": "^4.6.0",
    "@dnd-kit/core": "^6.1.0",
    "@dnd-kit/sortable": "^8.0.0",
    "recharts": "^2.10.0",
    "dayjs": "^1.11.0"
  },
  "devDependencies": {
    "vite": "^5.0.0",
    "@vitejs/plugin-react": "^4.2.0",
    "typescript": "^5.3.0",
    "@types/react": "^18.2.0",
    "@types/react-dom": "^18.2.0",
    "vitest": "^1.2.0",
    "@testing-library/react": "^14.1.0"
  }
}
```

---

**本方案的设计原则**：最小改动接入新能力、兼容老用例、所有新能力都建立在"通用 Step"这一底座上。先把 `test_steps` 表加上，你就会发现后面所有扩展都顺了。
