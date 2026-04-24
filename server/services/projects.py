"""
Projects 相关的聚合查询。

老版 main.py 里 `get_projects` 存在经典 N+1：每个项目都再发两条 SQL 查
case_count 和 last_report。这里把它改成一次查询拿全部数据：
  - 用子查询 `case_counts_sq` 预先按 project_id 算好用例数（跨 Module join）。
  - 用子查询 `latest_reports_sq` 预先为每个 project 取 create_time 最大的那条 report。
  - 主查询用 outer join 一次性把 Project + 两个子结果拉回来。

如果后续还想查更多聚合（比如最近 7 天的通过率趋势），在这里扩就行。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, aliased

from database.models import Module, Project, TestCase, TestReport


def list_projects_with_stats(
    session: Session,
    type_filter: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    返回项目列表 + 聚合统计（用例数 / 通过率 / 上次执行状态 / 上次执行时间）。

    参数：
      - type_filter: 可选，按 `Project.type` ilike 过滤（"api" / "web" / "app"）。

    返回格式严格对齐老版 /api/projects/list 的 `data` 结构，前端零迁移。
    """
    # 子查询 1：每个项目的 case_count
    case_counts_sq = (
        session.query(
            Module.project_id.label("pid"),
            func.count(TestCase.id).label("case_count"),
        )
        .join(TestCase, TestCase.module_id == Module.id)
        .group_by(Module.project_id)
        .subquery()
    )

    # 子查询 2：每个项目"最新 report"的 id（用 max(create_time) 找）
    latest_time_sq = (
        session.query(
            TestReport.project_id.label("pid"),
            func.max(TestReport.create_time).label("last_time"),
        )
        .group_by(TestReport.project_id)
        .subquery()
    )

    # 别名一下 TestReport，从 latest_time_sq 回到具体 report 行
    LastReport = aliased(TestReport)

    query = (
        session.query(Project, case_counts_sq.c.case_count, LastReport)
        .outerjoin(case_counts_sq, case_counts_sq.c.pid == Project.id)
        .outerjoin(latest_time_sq, latest_time_sq.c.pid == Project.id)
        .outerjoin(
            LastReport,
            (LastReport.project_id == latest_time_sq.c.pid)
            & (LastReport.create_time == latest_time_sq.c.last_time),
        )
    )

    if type_filter:
        query = query.filter(Project.type.ilike(type_filter))

    query = query.order_by(Project.sort_order)

    results: list[dict[str, Any]] = []
    for proj, case_count, last_report in query.all():
        pass_rate = 0.0
        if last_report and last_report.total_count and last_report.total_count > 0:
            pass_rate = round(
                (last_report.pass_count / last_report.total_count) * 100, 1
            )

        last_status = last_report.status if last_report else "unknown"
        last_run_time = (
            last_report.create_time.strftime("%Y-%m-%d %H:%M")
            if last_report and last_report.create_time
            else "从未执行"
        )

        results.append(
            {
                "id": proj.id,
                "name": proj.name,
                "type": proj.type,
                "desc": proj.description,
                "case_count": int(case_count or 0),
                "pass_rate": pass_rate,
                "last_status": last_status,
                "last_run_time": last_run_time,
            }
        )

    return results


def serialize_project_basic(project: Project) -> dict[str, Any]:
    """给 create/update/get 接口复用的简单字典化。避免 FastAPI 把 ORM 对象
    上挂着的 relationship（modules 等）一起递归序列化出来。"""
    return {
        "id": project.id,
        "name": project.name,
        "type": project.type,
        "description": project.description,
    }


# 小工具：给外面的 router 用，不重复导入
def iter_ids(objs: Iterable[Any]) -> list[int]:
    return [o.id for o in objs]
