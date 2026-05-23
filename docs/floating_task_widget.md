# 全局浮动任务列表 & 执行记录页任务看板

> 最后更新：2025-05-21

---

## 功能概述

用户在平台上触发的所有异步任务（AI 生成测试用例、AI 生成测试分析、执行 API 自动化、执行 Web 自动化、执行 App 自动化），统一以浮动圆圈的形式展示进行中任务数。用户可自由拖动该圆圈，松手后自动吸附到屏幕边缘。同时，在执行记录页面增加"进行中的任务"卡片。

### 功能点

| # | 描述 |
|---|---|
| 1 | 页面右下角出现一个**浮动圆圈**，显示当前进行中任务的数量 |
| 2 | 圆圈可**自由拖动**，松手后**自动吸附**到屏幕左/右边缘 |
| 3 | **有任务时显示**，无任务时隐藏 |
| 4 | 点击圆圈弹出**任务列表**，列出所有进行中任务（类型、名称、状态、已运行时间），可点击跳转 |
| 5 | **执行记录页**顶部新增"进行中的任务"卡片，功能同浮动列表 |

### 哪些任务算"进行中"

| 任务类型 | 数据来源 | 判断条件 |
|---|---|---|
| AI 生成测试分析 | `ai_runs` 表 | `status = 'pending'` 或 `'running'` |
| AI 生成测试用例 | `ai_runs` 表 | `status = 'pending'` 或 `'running'` |
| 执行 API 自动化 | `test_reports` 表 | `status = 'running'` |
| 执行 Web 自动化 | `test_reports` 表 | `status = 'running'` |
| 执行 App 自动化 | `test_reports` 表 | `status = 'running'` |

---

## 扩展性设计：任务类型注册表（Task Registry）

为保证后续扩展其他需要时间执行的功能（如 RAG 索引、代码扫描、批量导入等）能零改动收录到任务看板中，引入 **任务类型注册表** 模式。

### 核心思路

```
                    ┌─────────────────────────────────┐
                    │     TaskRegistry (单例)          │
                    │  register(TaskTypeInfo)          │
                    │  get_all_in_progress(db, pid)    │
                    └──────────┬──────────────────────┘
                               │ 遍历所有已注册条目
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
        ai_requirement    test_run_api     rag_index
        _parse            (web/app)        (未来新增)

        各条目自带: query_fn 查自己的表 → 统一 UnifiedTask 格式
```

### 新任务接入步骤

```python
# 1. 在任务所在模块注册即可：
task_registry.register(TaskTypeInfo(
    key="rag_index",
    label="代码索引构建",
    category="system",
    icon="Database",
    query_fn=lambda db, pid, limit: (
        db.query(RagIndexJob)
        .filter(RagIndexJob.status.in_(["pending", "running"]))
        .limit(limit).all()
    ),
    detail_url_tpl="/config/rag?job_id={id}",
))
```

前端、API 路由、浮动圆圈逻辑全部 **零改动**。

### TaskTypeInfo 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `key` | str | 唯一标识 |
| `label` | str | 中文名 |
| `category` | str | 大类：`ai` / `execution` / `system` |
| `icon` | str | lucide 图标名 |
| `query_fn` | callable | 查询进行中任务的函数 `(db_session, project_id, limit) -> list[dict]` |
| `detail_url_tpl` | str | 跳转链接模板，支持 `{id}` 和 `{project_id}` 占位 |

---

## 后端 API

### `GET /api/tasks-overview/in-progress`

聚合查询所有"进行中"的异步任务。

**请求参数（可选）：**

| 参数 | 类型 | 说明 |
|---|---|---|
| `project_id` | int | 按项目过滤 |
| `limit` | int | 最大返回条数，默认 50 |

**响应结构：**

```json
{
  "status": "success",
  "data": [
    {
      "type_key": "ai_requirement_parse",
      "type_label": "AI 需求分析",
      "category": "ai",
      "icon": "Brain",
      "id": 123,
      "name": "需求分析 — xxx需求",
      "status": "running",
      "project_id": 1,
      "project_name": "电商平台",
      "started_at": "2025-05-21T10:30:00",
      "detail_url": "/projects/1/requirements"
    }
  ]
}
```

---

## 前端组件设计

### 文件结构

```
FloatingTaskWidget/
├── index.tsx           # 主组件 + 全局轮询
├── FloatingCircle.tsx  # 浮动圆圈（拖拽 + 吸附 + 数量徽标）
└── TaskPopover.tsx     # 点击展开的任务列表
```

### FloatingCircle 行为

| 特性 | 描述 |
|---|---|
| 默认位置 | 右下角，距底部 80px，距右侧 16px |
| 尺寸 | 圆形 56×56px |
| 外观 | 主色填充圆 + 居中白色数字 + 外圈 loading 旋转环（有 running 态时） |
| 拖拽 | `onMouseDown` / `onPointerMove` / `onPointerUp`（同时支持 touch），无需第三方库 |
| 拖拽判定 | 位移 < 5px 视为点击，打开 Popover；≥ 5px 进入拖拽 |
| 吸附方向 | 松手后判断圆心 x 位置：屏幕中线左边 → 吸附左边缘；右边 → 吸附右边缘 |
| 吸附动画 | `transition: left/right 0.3s cubic-bezier(0.34, 1.56, 0.64, 1)` |
| 显示/隐藏 | `tasks.length > 0` 时 `opacity: 1`；否则 `opacity: 0, pointer-events: none` |
| z-index | `z-[9999]`，确保在所有对话框之上 |

### TaskPopover

- 弹出位置：自动判断（圆在左侧时弹窗在右，反之在左）
- 内容：任务列表，每条显示类型图标 + 名称 + 状态徽标 + 开始时间
- 最大高度 60vh，超出滚动

---

## 修改文件清单

| 层 | 文件 | 操作 |
|---|---|---|
| 后端 | `server/services/task_registry.py` | 新建 |
| 后端 | `server/api/tasks_overview.py` | 新建 |
| 后端 | `server/api/__init__.py` | 导出新路由 |
| 后端 | `server/main.py` | 注册新路由 |
| 后端 | `tasks/ai_tasks.py` | 注册 AI 类任务 |
| 后端 | `tasks/run_test_task.py` | 注册执行类任务 |
| 前端 | `frontend/src/components/FloatingTaskWidget/index.tsx` | 新建 |
| 前端 | `frontend/src/components/FloatingTaskWidget/FloatingCircle.tsx` | 新建 |
| 前端 | `frontend/src/components/FloatingTaskWidget/TaskPopover.tsx` | 新建 |
| 前端 | `frontend/src/components/AppLayout.tsx` | 集成 FloatingTaskWidget |
| 前端 | `frontend/src/pages/RunsPage.tsx` | 增加进行中任务区块 |
| 前端 | `frontend/src/lib/api.ts` | 新增 tasksOverviewApi |
| 前端 | `frontend/src/lib/query.ts` | 新增 query key |
| 前端 | `frontend/src/types/domain.ts` | 新增 InProgressTask 类型 |

### 边界情况

- **没有任务时**：浮动圆圈消失（`opacity: 0`），不占交互空间
- **网络断开**：轮询失败不弹 toast，安静重试
- **任务从 pending → running 切换**：圆自动刷新显示
- **任务完成/失败**：下次轮询时从列表中移除
- **多标签页**：每个标签页独立轮询
- **拖拽到边缘后刷新页面**：位置回到默认右下角（不持久化拖拽位置）
- **用户快速拖出屏幕外**：设定最小可见区域（至少 50% 圆可见），超出则回弹到最近吸附边
- **任务列表为空**：Popover 显示"当前没有进行中的任务"
