# PM 重设计 · M1 数据底座 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给项目管理重设计搭好数据层底座 —— 新建 5 张表（users / roles / user_roles / tasks / version_test_summaries）+ 修改 3 张表（requirements / project_versions / test_cases），不改动任何 UI 和业务逻辑，确保现有用例执行不受影响。

**Architecture:** 8 个 ORM 模型变更（4 新建 + 3 修改 + 1 export 总入口）→ 1 份 Alembic 增量 migration（revision `pm_000003`）→ migration 内 seed 6 个固定 role。每步用 import 检查 + alembic upgrade/downgrade roundtrip 当 "失败测试 → 实现 → 验证通过"，本仓库无传统单测体系。

**Tech Stack:** SQLAlchemy 1.x 风格 declarative_base（`database.base`）；JSONType 跨 PG/SQLite；Alembic（手写 revision id `pm_xxxxxx`，不用自动哈希）；Python 3.11；现有 Pydantic v2。

**前置依赖:** spec 已 approved，路径 `docs/superpowers/specs/2026-05-04-project-management-redesign-design.md`。

**关键约定（每个 task 都要遵守）:**
- 模型文件用 SQLAlchemy 1.x `Column(...)` 风格，不要用 2.x `mapped_column`（仓库统一）
- JSON 字段用 `from database.base import JSONType`，**不要**直接 `Column(JSON)`
- 状态枚举写常量 + `ALL_XXX` 集合（看 `database/models/project_version.py` 的 VERSION_STATUS_* 模式）
- 模型必须有 `to_dict()` 方法，返回 `{"created_at": self.created_at.isoformat() if self.created_at else None, ...}`
- 新模型必须在 `database/models/__init__.py` 导入（顺序敏感：被关系引用的模型先 import；export 到 `__all__`）
- 每 task 单独提交，commit message 用 `feat(pm-m1):` 前缀

---

## File Structure

| 路径 | 操作 | 职责 |
|---|---|---|
| `database/models/user.py` | 新建 | User 模型（最小字段：id / username / full_name / email / is_active）。无 password_hash —— 平台目前内网无 auth，先占位；M2+ 接入登录时再扩 |
| `database/models/role.py` | 新建 | Role + UserRole（user × role m2m），固定 6 个 role code |
| `database/models/task.py` | 新建 | Task（含 type / status / severity / assignee_dev / assignee_test / parent_task_id / metadata） |
| `database/models/version_test_summary.py` | 新建 | VersionTestSummary（一对一挂 ProjectVersion） |
| `database/models/requirement.py` | 修改 | 加 5 字段：version_id / system_status / business_status / assignee_pm_id / accepted_at + 新常量 |
| `database/models/project_version.py` | 修改 | 状态枚举加 `VERSION_STATUS_READY_TO_RELEASE` |
| `database/models/test_case.py` | 修改 | 加 `version_id` 字段（资产沉淀回流用） |
| `database/models/__init__.py` | 修改 | 导入新模型 + 导出新常量 |
| `database/migrations/versions/20260504_0001_pm_redesign_phase1.py` | 新建 | 一份大 migration：建 5 表 + 改 3 表 + seed 6 角色；revision `pm_000003`，down_revision `pm_000002` |

---

## Task 1 — User 模型

**Files:**
- Create: `database/models/user.py`
- Modify: `database/models/__init__.py`

- [ ] **Step 1: 验证 User 当前不可 import（"失败测试"）**

```bash
cd /Users/Apple/Documents/test_automation_platform
python -c "from database.models import User; print('ok')"
```
Expected: `ImportError: cannot import name 'User' from 'database.models'`

- [ ] **Step 2: 创建 `database/models/user.py`**

```python
"""User —— 平台用户。

设计：
  - 内网平台无 auth，本期先建最小字段，M2+ 接入登录时扩展 password_hash / last_login_at
  - 角色通过 user_roles 关联 roles 表（多对多），不在 users 表内冗余 role 字段
  - is_active 用于离职 / 停用场景：保留历史记录但不出现在分配下拉
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, func
from sqlalchemy.orm import relationship

from database.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    full_name = Column(String(128))
    email = Column(String(255), unique=True, index=True)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # 关系：roles 通过 user_roles 关联（在 role.py 里定义 backref）
    # tasks_as_dev / tasks_as_test 通过 task.py 里的 assignee_dev / assignee_test FK 反向

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "full_name": self.full_name,
            "email": self.email,
            "is_active": self.is_active,
            "role_codes": [r.code for r in (self.roles or [])],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
```

- [ ] **Step 3: 修改 `database/models/__init__.py`**

在第 20 行（`from .module import Module` 那行后面）加：
```python
from .user import User
```

并在 `__all__` 列表里 "ProjectVersion" 之后加 `"User"`（保持字母序也行）。

- [ ] **Step 4: 验证 import 通过**

```bash
python -c "from database.models import User; print(User.__tablename__)"
```
Expected: `users`

- [ ] **Step 5: Commit**

```bash
git add database/models/user.py database/models/__init__.py
git commit -m "feat(pm-m1): add User model"
```

---

## Task 2 — Role + UserRole 模型

**Files:**
- Create: `database/models/role.py`
- Modify: `database/models/__init__.py`

- [ ] **Step 1: 验证 Role 当前不可 import**

```bash
python -c "from database.models import Role, UserRole; print('ok')"
```
Expected: `ImportError`

- [ ] **Step 2: 创建 `database/models/role.py`**

```python
"""Role + UserRole —— 用户角色（多对多）。

设计：
  - 6 个固定 role code：admin / dev / test / pm / ui / ops
  - role 表本身只 seed 一次（migration 里 INSERT），运行期一般不增删
  - user_roles 是简单 m2m 关联表，无业务字段
"""
from sqlalchemy import Column, Integer, String, Text, ForeignKey, Table
from sqlalchemy.orm import relationship

from database.base import Base


# 6 个固定 role code
ROLE_ADMIN = "admin"
ROLE_DEV = "dev"
ROLE_TEST = "test"
ROLE_PM = "pm"
ROLE_UI = "ui"
ROLE_OPS = "ops"

ALL_ROLE_CODES = {
    ROLE_ADMIN, ROLE_DEV, ROLE_TEST, ROLE_PM, ROLE_UI, ROLE_OPS,
}

# m2m 关联表
user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(50))
    description = Column(Text)

    users = relationship("User", secondary=user_roles, backref="roles")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "description": self.description,
        }
```

注：仓库习惯把 m2m 表小写命名作模块级变量（看 `project_version_modules` 在 `project_version.py`）。这里 `user_roles`（m2m Table）和概念上的 "UserRole" 同一个东西；在 `__init__.py` 里同时导出 `Role` 和 `user_roles`。

- [ ] **Step 3: 修改 `database/models/__init__.py`**

在 `from .user import User` 后面加：
```python
from .role import (
    Role,
    user_roles,
    ROLE_ADMIN,
    ROLE_DEV,
    ROLE_TEST,
    ROLE_PM,
    ROLE_UI,
    ROLE_OPS,
    ALL_ROLE_CODES,
)
```

`__all__` 加：`"Role", "user_roles", "ROLE_ADMIN", "ROLE_DEV", "ROLE_TEST", "ROLE_PM", "ROLE_UI", "ROLE_OPS", "ALL_ROLE_CODES",`

- [ ] **Step 4: 验证 import + 关系**

```bash
python -c "from database.models import Role, User, ROLE_ADMIN, ALL_ROLE_CODES; print(Role.__tablename__, ROLE_ADMIN, len(ALL_ROLE_CODES))"
```
Expected: `roles admin 6`

- [ ] **Step 5: Commit**

```bash
git add database/models/role.py database/models/__init__.py
git commit -m "feat(pm-m1): add Role + UserRole m2m"
```

---

## Task 3 — Task 模型

**Files:**
- Create: `database/models/task.py`
- Modify: `database/models/__init__.py`

- [ ] **Step 1: 验证 Task 当前不可 import**

```bash
python -c "from database.models import Task; print('ok')"
```
Expected: `ImportError`

- [ ] **Step 2: 创建 `database/models/task.py`**

```python
"""Task —— Requirement 下的执行单元（开发 / 测试 / UI 走查 / Bug）。

状态机（Task 线性，bug 解耦）：
  pending → dev_doing → dev_done → test_doing → passed → closed
                                                ↓ failed
                                             （新建 type=bug 的 Task，
                                               指向原 task.parent_task_id）

字段说明：
  - type: dev / test / ui_review / bug
    - bug 是独立 type，不在 dev/test 状态机内回退
  - parent_task_id: bug 指向原 task；非 bug 留空
  - severity: 仅 bug 用（P0/P1/P2/P3），其它 type 留空
  - assignee_dev_id / assignee_test_id: 一个 task 通常只有一方指派
    - dev type → 填 assignee_dev_id
    - test type → 填 assignee_test_id
    - bug type → 填 assignee_dev_id（指派给修复人）
  - related_case_id: bug 来源用例（测试在失败用例处建 bug 时自动带）
  - metadata: 重现步骤、环境快照、截图等附属信息（JSONB）
"""
from sqlalchemy import (
    Column, Integer, String, Text, Numeric, ForeignKey, DateTime, func,
)
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


# Task 类型
TASK_TYPE_DEV = "dev"
TASK_TYPE_TEST = "test"
TASK_TYPE_UI_REVIEW = "ui_review"
TASK_TYPE_BUG = "bug"
ALL_TASK_TYPES = {
    TASK_TYPE_DEV, TASK_TYPE_TEST, TASK_TYPE_UI_REVIEW, TASK_TYPE_BUG,
}

# Task 状态
TASK_STATUS_PENDING = "pending"
TASK_STATUS_DEV_DOING = "dev_doing"
TASK_STATUS_DEV_DONE = "dev_done"
TASK_STATUS_TEST_DOING = "test_doing"
TASK_STATUS_PASSED = "passed"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CLOSED = "closed"
ALL_TASK_STATUSES = {
    TASK_STATUS_PENDING,
    TASK_STATUS_DEV_DOING,
    TASK_STATUS_DEV_DONE,
    TASK_STATUS_TEST_DOING,
    TASK_STATUS_PASSED,
    TASK_STATUS_FAILED,
    TASK_STATUS_CLOSED,
}

# Bug 严重等级
BUG_SEVERITY_P0 = "P0"
BUG_SEVERITY_P1 = "P1"
BUG_SEVERITY_P2 = "P2"
BUG_SEVERITY_P3 = "P3"
ALL_BUG_SEVERITIES = {
    BUG_SEVERITY_P0, BUG_SEVERITY_P1, BUG_SEVERITY_P2, BUG_SEVERITY_P3,
}


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)

    # 归属
    requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # bug 指向原 task；非 bug 留空
    parent_task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True, index=True)

    # 内容
    title = Column(String(255), nullable=False)
    description = Column(Text)

    # 类型 / 状态 / 严重等级
    type = Column(String(20), nullable=False, index=True)
    status = Column(String(20), nullable=False, default=TASK_STATUS_PENDING, index=True)
    severity = Column(String(4), nullable=True)  # 仅 bug 用

    # 指派
    assignee_dev_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    assignee_test_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # bug 来源用例
    related_case_id = Column(Integer, ForeignKey("test_cases.id"), nullable=True)

    # 重现步骤、环境快照、截图等
    task_metadata = Column("metadata", JSONType, nullable=True)

    # 工时
    estimated_hours = Column(Numeric(5, 2), nullable=True)
    actual_hours = Column(Numeric(5, 2), nullable=True)

    # 时间戳
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    closed_at = Column(DateTime, nullable=True)

    # 关系
    requirement = relationship("Requirement")
    parent_task = relationship("Task", remote_side=[id])
    assignee_dev = relationship("User", foreign_keys=[assignee_dev_id])
    assignee_test = relationship("User", foreign_keys=[assignee_test_id])
    created_by = relationship("User", foreign_keys=[created_by_id])
    related_case = relationship("TestCase", foreign_keys=[related_case_id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "requirement_id": self.requirement_id,
            "parent_task_id": self.parent_task_id,
            "title": self.title,
            "description": self.description,
            "type": self.type,
            "status": self.status,
            "severity": self.severity,
            "assignee_dev_id": self.assignee_dev_id,
            "assignee_test_id": self.assignee_test_id,
            "created_by_id": self.created_by_id,
            "related_case_id": self.related_case_id,
            "metadata": self.task_metadata or {},
            "estimated_hours": float(self.estimated_hours) if self.estimated_hours is not None else None,
            "actual_hours": float(self.actual_hours) if self.actual_hours is not None else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }
```

注：列名 `metadata` 与 SQLAlchemy `Base.metadata` 同名会冲突，所以 ORM 属性命名为 `task_metadata`，DB 列名仍是 `metadata`（用 `Column("metadata", ...)` 显式指定）。`to_dict()` 里 key 用 `metadata` 对外保持一致。

- [ ] **Step 3: 修改 `database/models/__init__.py`**

在 Role 那段下面加：
```python
from .task import (
    Task,
    TASK_TYPE_DEV,
    TASK_TYPE_TEST,
    TASK_TYPE_UI_REVIEW,
    TASK_TYPE_BUG,
    ALL_TASK_TYPES,
    TASK_STATUS_PENDING,
    TASK_STATUS_DEV_DOING,
    TASK_STATUS_DEV_DONE,
    TASK_STATUS_TEST_DOING,
    TASK_STATUS_PASSED,
    TASK_STATUS_FAILED,
    TASK_STATUS_CLOSED,
    ALL_TASK_STATUSES,
    BUG_SEVERITY_P0,
    BUG_SEVERITY_P1,
    BUG_SEVERITY_P2,
    BUG_SEVERITY_P3,
    ALL_BUG_SEVERITIES,
)
```

`__all__` 加这 16 个名字。

- [ ] **Step 4: 验证 import**

```bash
python -c "from database.models import Task, ALL_TASK_TYPES, ALL_TASK_STATUSES; print(Task.__tablename__, len(ALL_TASK_TYPES), len(ALL_TASK_STATUSES))"
```
Expected: `tasks 4 7`

- [ ] **Step 5: Commit**

```bash
git add database/models/task.py database/models/__init__.py
git commit -m "feat(pm-m1): add Task model with state machine constants"
```

---

## Task 4 — VersionTestSummary 模型

**Files:**
- Create: `database/models/version_test_summary.py`
- Modify: `database/models/__init__.py`

- [ ] **Step 1: 验证 VersionTestSummary 当前不可 import**

```bash
python -c "from database.models import VersionTestSummary; print('ok')"
```
Expected: `ImportError`

- [ ] **Step 2: 创建 `database/models/version_test_summary.py`**

```python
"""VersionTestSummary —— 版本测试汇总（一对一挂 ProjectVersion）。

发版时由 version_summary_service.generate(version_id) 生成 / 更新。
之后版本归档页直接读这张表，避免每次重算。

字段语义：
  - first_pass_rate: 1 - bug_count / dev_task_count（无 bug 的开发任务比例）
  - test_coverage: 关联了用例的需求数 / 全部需求数
  - payload: 完整 JSONB 快照，便于发布后回溯（含每个 Req / Task / Bug 的明细 ID）
"""
from sqlalchemy import (
    Column, Integer, Numeric, ForeignKey, DateTime, func,
)
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


class VersionTestSummary(Base):
    __tablename__ = "version_test_summaries"

    id = Column(Integer, primary_key=True, index=True)
    version_id = Column(
        Integer,
        ForeignKey("project_versions.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # 计数
    total_requirements = Column(Integer, default=0, nullable=False)
    total_tasks = Column(Integer, default=0, nullable=False)
    total_test_cases = Column(Integer, default=0, nullable=False)

    # 用例执行结果
    passed = Column(Integer, default=0, nullable=False)
    failed = Column(Integer, default=0, nullable=False)
    blocked = Column(Integer, default=0, nullable=False)

    # Bug 统计
    total_bugs = Column(Integer, default=0, nullable=False)
    p0_bugs = Column(Integer, default=0, nullable=False)
    p1_bugs = Column(Integer, default=0, nullable=False)
    p2_bugs = Column(Integer, default=0, nullable=False)
    p3_bugs = Column(Integer, default=0, nullable=False)

    # 计算指标
    first_pass_rate = Column(Numeric(5, 4), nullable=True)
    avg_fix_time_hours = Column(Numeric(8, 2), nullable=True)
    test_coverage = Column(Numeric(5, 4), nullable=True)

    # 完整快照
    payload = Column(JSONType, nullable=True)

    generated_at = Column(DateTime, server_default=func.now(), nullable=False)

    version = relationship("ProjectVersion")

    def to_dict(self) -> dict:
        def _f(v):
            return float(v) if v is not None else None

        return {
            "id": self.id,
            "version_id": self.version_id,
            "total_requirements": self.total_requirements,
            "total_tasks": self.total_tasks,
            "total_test_cases": self.total_test_cases,
            "passed": self.passed,
            "failed": self.failed,
            "blocked": self.blocked,
            "total_bugs": self.total_bugs,
            "p0_bugs": self.p0_bugs,
            "p1_bugs": self.p1_bugs,
            "p2_bugs": self.p2_bugs,
            "p3_bugs": self.p3_bugs,
            "first_pass_rate": _f(self.first_pass_rate),
            "avg_fix_time_hours": _f(self.avg_fix_time_hours),
            "test_coverage": _f(self.test_coverage),
            "payload": self.payload or {},
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
        }
```

- [ ] **Step 3: 修改 `database/models/__init__.py`**

在 Task 段下面加：
```python
from .version_test_summary import VersionTestSummary
```

`__all__` 加：`"VersionTestSummary",`

- [ ] **Step 4: 验证 import**

```bash
python -c "from database.models import VersionTestSummary; print(VersionTestSummary.__tablename__)"
```
Expected: `version_test_summaries`

- [ ] **Step 5: Commit**

```bash
git add database/models/version_test_summary.py database/models/__init__.py
git commit -m "feat(pm-m1): add VersionTestSummary model"
```

---

## Task 5 — Requirement 模型加 5 字段 + 双 status 常量

**Files:**
- Modify: `database/models/requirement.py`
- Modify: `database/models/__init__.py`

- [ ] **Step 1: 验证新字段当前不存在（"失败测试"）**

```bash
python -c "from database.models import Requirement; r = Requirement(); print(hasattr(r, 'system_status'), hasattr(r, 'business_status'), hasattr(r, 'version_id'))"
```
Expected: `False False False`

- [ ] **Step 2: 在 `database/models/requirement.py` 第 25 行（`ALL_REQUIREMENT_STATUSES = {...}` 之后）加双 status 常量**

```python
# system_status —— Task 状态聚合自动算
REQUIREMENT_SYSTEM_STATUS_APPROVED = "approved"
REQUIREMENT_SYSTEM_STATUS_DEVELOPING = "developing"
REQUIREMENT_SYSTEM_STATUS_TESTING = "testing"
REQUIREMENT_SYSTEM_STATUS_READY_TO_RELEASE = "ready_to_release"
ALL_REQUIREMENT_SYSTEM_STATUSES = {
    REQUIREMENT_SYSTEM_STATUS_APPROVED,
    REQUIREMENT_SYSTEM_STATUS_DEVELOPING,
    REQUIREMENT_SYSTEM_STATUS_TESTING,
    REQUIREMENT_SYSTEM_STATUS_READY_TO_RELEASE,
}

# business_status —— PM 维护，决定是否进入发布
REQUIREMENT_BUSINESS_STATUS_APPROVED = "approved"
REQUIREMENT_BUSINESS_STATUS_ACCEPTED = "accepted"
REQUIREMENT_BUSINESS_STATUS_RELEASED = "released"
ALL_REQUIREMENT_BUSINESS_STATUSES = {
    REQUIREMENT_BUSINESS_STATUS_APPROVED,
    REQUIREMENT_BUSINESS_STATUS_ACCEPTED,
    REQUIREMENT_BUSINESS_STATUS_RELEASED,
}
```

- [ ] **Step 3: 在 `database/models/requirement.py` 的 `Requirement` 类里、`sort_order = ...` 这行（约第 70 行）后面加 5 个字段**

```python
    # 版本归属（M1 加）
    version_id = Column(
        Integer, ForeignKey("project_versions.id"), nullable=True, index=True
    )

    # 自动算（Task 聚合）
    system_status = Column(String(20), nullable=True, index=True)

    # PM 维护（验收 gate）
    business_status = Column(String(20), nullable=True, index=True)

    # PM 指派人
    assignee_pm_id = Column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )

    # PM 一键 Accept 时间
    accepted_at = Column(DateTime, nullable=True)
```

- [ ] **Step 4: 在 `to_dict()` 返回字典里加这 5 个字段**

打开 `to_dict()`，在 `"sort_order": self.sort_order,` 后面、`"created_at"` 前面插入：
```python
            "version_id": self.version_id,
            "system_status": self.system_status,
            "business_status": self.business_status,
            "assignee_pm_id": self.assignee_pm_id,
            "accepted_at": self.accepted_at.isoformat() if self.accepted_at else None,
```

- [ ] **Step 5: 修改 `database/models/__init__.py` 的 requirement 导入段**

把现有的：
```python
from .requirement import (
    Requirement,
    REQUIREMENT_STATUS_DRAFT,
    ...
)
```
扩展为包含新常量：
```python
from .requirement import (
    Requirement,
    REQUIREMENT_STATUS_DRAFT,
    REQUIREMENT_STATUS_APPROVED,
    REQUIREMENT_STATUS_ARCHIVED,
    ALL_REQUIREMENT_STATUSES,
    REQUIREMENT_SOURCE_MANUAL,
    REQUIREMENT_SOURCE_AI,
    REQUIREMENT_SYSTEM_STATUS_APPROVED,
    REQUIREMENT_SYSTEM_STATUS_DEVELOPING,
    REQUIREMENT_SYSTEM_STATUS_TESTING,
    REQUIREMENT_SYSTEM_STATUS_READY_TO_RELEASE,
    ALL_REQUIREMENT_SYSTEM_STATUSES,
    REQUIREMENT_BUSINESS_STATUS_APPROVED,
    REQUIREMENT_BUSINESS_STATUS_ACCEPTED,
    REQUIREMENT_BUSINESS_STATUS_RELEASED,
    ALL_REQUIREMENT_BUSINESS_STATUSES,
)
```

`__all__` 同步加上 9 个新名字。

- [ ] **Step 6: 验证 import + 字段存在**

```bash
python -c "from database.models import Requirement, ALL_REQUIREMENT_SYSTEM_STATUSES, ALL_REQUIREMENT_BUSINESS_STATUSES; r = Requirement(); print(hasattr(r, 'system_status'), hasattr(r, 'business_status'), hasattr(r, 'version_id'), hasattr(r, 'assignee_pm_id'), hasattr(r, 'accepted_at'), len(ALL_REQUIREMENT_SYSTEM_STATUSES), len(ALL_REQUIREMENT_BUSINESS_STATUSES))"
```
Expected: `True True True True True 4 3`

- [ ] **Step 7: Commit**

```bash
git add database/models/requirement.py database/models/__init__.py
git commit -m "feat(pm-m1): add version/system_status/business_status to Requirement"
```

---

## Task 6 — ProjectVersion 加 ready_to_release + TestCase 加 version_id

**Files:**
- Modify: `database/models/project_version.py`
- Modify: `database/models/test_case.py`
- Modify: `database/models/__init__.py`

- [ ] **Step 1: 验证当前不存在**

```bash
python -c "from database.models import VERSION_STATUS_READY_TO_RELEASE; print('ok')"
```
Expected: `ImportError`

```bash
python -c "from database.models import TestCase; t = TestCase(); print(hasattr(t, 'version_id'))"
```
Expected: `False`

- [ ] **Step 2: 修改 `database/models/project_version.py`**

在 `VERSION_STATUS_RELEASED = "released"` 这行后面、`VERSION_STATUS_ARCHIVED` 之前加：
```python
VERSION_STATUS_READY_TO_RELEASE = "ready_to_release"  # 全部 Req business_status=accepted，待发布
```

并在 `ALL_VERSION_STATUSES = {...}` 集合里加 `VERSION_STATUS_READY_TO_RELEASE,`。

- [ ] **Step 3: 修改 `database/models/test_case.py` 加 `version_id` 字段**

在 `priority = Column(Integer, default=2)` 这行（约第 56 行）后、`# ============ 执行控制（v2 新增）============` 注释前加：
```python

    # 版本归属（M1 加）—— 资产沉淀回流：迭代结束后该版本新增/修改的用例打这个标
    version_id = Column(Integer, ForeignKey("project_versions.id"), nullable=True, index=True)
```

如果 `TestCase` 类有 `to_dict()` 方法（先确认：`grep -n "def to_dict" database/models/test_case.py`），在它的返回字典里加 `"version_id": self.version_id,`。如果没有 `to_dict()` 就跳过这步。

- [ ] **Step 4: 修改 `database/models/__init__.py` —— ProjectVersion 导入段加新常量**

把现有：
```python
from .project_version import (
    ProjectVersion,
    VERSION_STATUS_PLANNING,
    VERSION_STATUS_DEVELOPING,
    VERSION_STATUS_TESTING,
    VERSION_STATUS_RELEASED,
    VERSION_STATUS_ARCHIVED,
    ALL_VERSION_STATUSES,
)
```
扩展为：
```python
from .project_version import (
    ProjectVersion,
    VERSION_STATUS_PLANNING,
    VERSION_STATUS_DEVELOPING,
    VERSION_STATUS_TESTING,
    VERSION_STATUS_READY_TO_RELEASE,
    VERSION_STATUS_RELEASED,
    VERSION_STATUS_ARCHIVED,
    ALL_VERSION_STATUSES,
)
```

`__all__` 加 `"VERSION_STATUS_READY_TO_RELEASE"`。

- [ ] **Step 5: 验证**

```bash
python -c "from database.models import VERSION_STATUS_READY_TO_RELEASE, ALL_VERSION_STATUSES, TestCase; t = TestCase(); print(VERSION_STATUS_READY_TO_RELEASE, len(ALL_VERSION_STATUSES), hasattr(t, 'version_id'))"
```
Expected: `ready_to_release 6 True`

- [ ] **Step 6: Commit**

```bash
git add database/models/project_version.py database/models/test_case.py database/models/__init__.py
git commit -m "feat(pm-m1): add ready_to_release status + TestCase.version_id"
```

---

## Task 7 — Alembic Migration（建 5 表 + 改 3 表 + seed 6 角色）

**Files:**
- Create: `database/migrations/versions/20260504_0001_pm_redesign_phase1.py`

- [ ] **Step 1: 验证当前 alembic head 是 `pm_000002`**

```bash
alembic heads
```
Expected: 输出包含 `pm_000002`

- [ ] **Step 2: 创建 migration 文件 `database/migrations/versions/20260504_0001_pm_redesign_phase1.py`**

```python
"""PM 重设计 Phase 1：users / roles / user_roles / tasks / version_test_summaries
   + requirements 5 字段 + project_versions 状态扩展 + test_cases.version_id

  - 5 张新表 + 3 张表的字段增量
  - Migration 末尾 INSERT 6 个固定 role（admin/dev/test/pm/ui/ops）

Revision ID: pm_000003
Revises: pm_000002
Create Date: 2026-05-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "pm_000003"
down_revision: Union[str, None] = "pm_000002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 6 个固定角色 seed
SEED_ROLES = [
    {"code": "admin", "name": "管理员",  "description": "全平台读写 + 成员管理"},
    {"code": "dev",   "name": "开发",    "description": "认领 dev 任务，修复 bug"},
    {"code": "test",  "name": "测试",    "description": "执行测试，建 bug，出报告"},
    {"code": "pm",    "name": "产品",    "description": "需求管理，PM 验收 gate"},
    {"code": "ui",    "name": "UI",     "description": "走查任务，设计稿资产"},
    {"code": "ops",   "name": "运维",    "description": "环境探活，发版部署"},
]


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("full_name", sa.String(length=128), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ------------------------------------------------------------------
    # 2. roles
    # ------------------------------------------------------------------
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.create_index("ix_roles_code", "roles", ["code"], unique=True)

    # ------------------------------------------------------------------
    # 3. user_roles (m2m)
    # ------------------------------------------------------------------
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    )

    # ------------------------------------------------------------------
    # 4. tasks
    # ------------------------------------------------------------------
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("requirement_id", sa.Integer(), sa.ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("severity", sa.String(length=4), nullable=True),
        sa.Column("assignee_dev_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("assignee_test_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("related_case_id", sa.Integer(), sa.ForeignKey("test_cases.id"), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("estimated_hours", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("actual_hours", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_tasks_requirement_id", "tasks", ["requirement_id"])
    op.create_index("ix_tasks_parent_task_id", "tasks", ["parent_task_id"])
    op.create_index("ix_tasks_assignee_dev_id", "tasks", ["assignee_dev_id"])
    op.create_index("ix_tasks_assignee_test_id", "tasks", ["assignee_test_id"])
    op.create_index("ix_tasks_type", "tasks", ["type"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_type_status", "tasks", ["type", "status"])

    # ------------------------------------------------------------------
    # 5. version_test_summaries
    # ------------------------------------------------------------------
    op.create_table(
        "version_test_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_id", sa.Integer(), sa.ForeignKey("project_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("total_requirements", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tasks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_test_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_bugs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("p0_bugs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("p1_bugs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("p2_bugs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("p3_bugs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_pass_rate", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("avg_fix_time_hours", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("test_coverage", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_vts_version_id", "version_test_summaries", ["version_id"], unique=True)

    # ------------------------------------------------------------------
    # 6. requirements 加 5 字段
    # ------------------------------------------------------------------
    op.add_column("requirements", sa.Column("version_id", sa.Integer(), sa.ForeignKey("project_versions.id"), nullable=True))
    op.add_column("requirements", sa.Column("system_status", sa.String(length=20), nullable=True))
    op.add_column("requirements", sa.Column("business_status", sa.String(length=20), nullable=True))
    op.add_column("requirements", sa.Column("assignee_pm_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("requirements", sa.Column("accepted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_req_version_id", "requirements", ["version_id"])
    op.create_index("ix_req_system_status", "requirements", ["system_status"])
    op.create_index("ix_req_business_status", "requirements", ["business_status"])
    op.create_index("ix_req_assignee_pm_id", "requirements", ["assignee_pm_id"])

    # ------------------------------------------------------------------
    # 7. test_cases 加 version_id
    # ------------------------------------------------------------------
    op.add_column("test_cases", sa.Column("version_id", sa.Integer(), sa.ForeignKey("project_versions.id"), nullable=True))
    op.create_index("ix_tc_version_id", "test_cases", ["version_id"])

    # ------------------------------------------------------------------
    # 8. seed 6 个固定 role
    # ------------------------------------------------------------------
    roles_table = sa.table(
        "roles",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
    )
    op.bulk_insert(roles_table, SEED_ROLES)


def downgrade() -> None:
    # 反序：先删字段，再删表
    op.drop_index("ix_tc_version_id", table_name="test_cases")
    op.drop_column("test_cases", "version_id")

    op.drop_index("ix_req_assignee_pm_id", table_name="requirements")
    op.drop_index("ix_req_business_status", table_name="requirements")
    op.drop_index("ix_req_system_status", table_name="requirements")
    op.drop_index("ix_req_version_id", table_name="requirements")
    op.drop_column("requirements", "accepted_at")
    op.drop_column("requirements", "assignee_pm_id")
    op.drop_column("requirements", "business_status")
    op.drop_column("requirements", "system_status")
    op.drop_column("requirements", "version_id")

    op.drop_index("ix_vts_version_id", table_name="version_test_summaries")
    op.drop_table("version_test_summaries")

    op.drop_index("ix_tasks_type_status", table_name="tasks")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_type", table_name="tasks")
    op.drop_index("ix_tasks_assignee_test_id", table_name="tasks")
    op.drop_index("ix_tasks_assignee_dev_id", table_name="tasks")
    op.drop_index("ix_tasks_parent_task_id", table_name="tasks")
    op.drop_index("ix_tasks_requirement_id", table_name="tasks")
    op.drop_table("tasks")

    op.drop_table("user_roles")

    op.drop_index("ix_roles_code", table_name="roles")
    op.drop_table("roles")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
```

注意：
- migration 用 `sa.JSON()` 而非 `JSONType`（migration 只能在 alembic 上下文里跑，不引 ORM 模块；ORM 层会映射回 JSONB on PG 自动）
- `users.is_active` 用 `server_default=sa.text("true")` 不用 `"1"`，PG 和 SQLite 都吃
- seed 用 `op.bulk_insert(...)` 而非 raw SQL，跨数据库友好

- [ ] **Step 3: 跑 upgrade**

```bash
alembic upgrade head
```
Expected: 输出 `Running upgrade pm_000002 -> pm_000003, ...`，无报错

- [ ] **Step 4: 验证表结构 + seed**

```bash
python -c "
from database.db import DB
from database.models import User, Role, Task, VersionTestSummary, ALL_ROLE_CODES
db = DB()
roles = db.session.query(Role).all()
print('roles count:', len(roles))
print('role codes:', sorted(r.code for r in roles))
print('expected:  ', sorted(ALL_ROLE_CODES))
db.close()
"
```
Expected: 
```
roles count: 6
role codes: ['admin', 'dev', 'ops', 'pm', 'test', 'ui']
expected:   ['admin', 'dev', 'ops', 'pm', 'test', 'ui']
```

- [ ] **Step 5: downgrade 一次验证可回退**

```bash
alembic downgrade -1
```
Expected: 输出 `Running downgrade pm_000003 -> pm_000002, ...`，无报错

- [ ] **Step 6: 验证表确实被删**

```bash
python -c "
from database.db import DB
from sqlalchemy import inspect
db = DB()
insp = inspect(db.session.bind)
tables = set(insp.get_table_names())
removed_should_be_gone = {'users', 'roles', 'user_roles', 'tasks', 'version_test_summaries'}
print('still present (should be empty):', tables & removed_should_be_gone)
# 检查 requirements 没有 version_id 字段
cols = {c['name'] for c in insp.get_columns('requirements')}
print('requirements has version_id (should be False):', 'version_id' in cols)
db.close()
"
```
Expected: 
```
still present (should be empty): set()
requirements has version_id (should be False): False
```

- [ ] **Step 7: 重新 upgrade 一次（恢复到目标状态）**

```bash
alembic upgrade head
```
Expected: 再次成功，无报错。

- [ ] **Step 8: 再次验证 6 个 role seed 仍在**

```bash
python -c "
from database.db import DB
from database.models import Role
db = DB()
print(sorted(r.code for r in db.session.query(Role).all()))
db.close()
"
```
Expected: `['admin', 'dev', 'ops', 'pm', 'test', 'ui']`

- [ ] **Step 9: Commit**

```bash
git add database/migrations/versions/20260504_0001_pm_redesign_phase1.py
git commit -m "feat(pm-m1): alembic pm_000003 — 5 new tables + 3 table changes + seed roles"
```

---

## Task 8 — 回归验证：现有用例执行不受 schema 变更影响

**Files:** 无新建 / 无修改，仅验证。

- [ ] **Step 1: 找一个已有 v2 用例，跑一次端到端**

```bash
# 列出现有 case，挑一个有 steps 的
python -c "
from database.db import DB
from database.models import TestCase
db = DB()
cases = db.session.query(TestCase).limit(5).all()
for c in cases:
    print(c.id, c.case_type, c.name)
db.close()
"
```

挑一个 `case_type=api` 且有 steps 的 id（记为 `<CASE_ID>`）。

- [ ] **Step 2: 通过 API 触发执行**

```bash
# 启动 server（如果没起）
# CELERY_TASK_ALWAYS_EAGER=1 python server/main.py &
# 然后在另一个 terminal：
curl -s -X POST http://127.0.0.1:54351/api/run_test \
  -H 'Content-Type: application/json' \
  -d '{"case_ids": [<CASE_ID>], "env_id": null}'
```

或者直接用 EAGER 模式同步跑：
```bash
CELERY_TASK_ALWAYS_EAGER=1 python -c "
from tasks.run_test_task import run_test_task
result = run_test_task.delay(<CASE_ID_LIST>, None)
print(result.get())
"
```

Expected: 任务执行完毕，TestReport 落库，状态非 `running`。

- [ ] **Step 3: 验证报告状态**

```bash
python -c "
from database.db import DB
from database.models import TestReport
db = DB()
latest = db.session.query(TestReport).order_by(TestReport.id.desc()).first()
print('latest report:', latest.id, latest.status)
db.close()
"
```
Expected: status 是 `passed` / `failed` / `error` 之一，不是 `running`。

- [ ] **Step 4: 不需要 commit（仅验证）**

如果上述任意步失败 —— **停止**，回查 migration 是否破坏现有外键 / 字段。M1 的"零业务影响"承诺是核心，发现回归立刻 alembic downgrade 并 fix。

---

## M1 完成判定 ✅

- [ ] 4 个新模型全部可 import 且 `to_dict()` 正常
- [ ] requirements / project_versions / test_cases 字段添加完毕、新常量导出
- [ ] alembic upgrade → downgrade → upgrade 三次循环通过
- [ ] 6 个 role 在 roles 表里
- [ ] 现有 v2 用例端到端跑过、报告正常落库
- [ ] 8 个 commit 全部入 git

---

## 后续 Milestone（不在本计划内）

- **M2 · API 层** —— /api/users + /api/roles + /api/tasks + /api/version-summaries + task_service（system_status 自动算）+ from-test-failure 快捷端点
- **M3 · 工作台前端** —— 6 个 workspace 页面 + TaskList + CreateBugModal
- **M4 · 资产沉淀 + 报告** —— VersionSummary 页面 + 用例库回流 + 版本归档

每个 milestone 单独写一份 plan，参照本文件结构（导航 task → 失败检查 → 实现代码块 → 验证 → commit）。
