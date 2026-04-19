"""v2 数据迁移：把老 TestCase 的 v1 字段拆成一条 http_request TestStep

背景
----
v1 时代，一条 TestCase 直接携带 API 字段（method/path/headers/...），
一条用例 = 一次 HTTP 请求。

v2 改造后：
  - TestCase 只负责"元信息"（name / case_type / env / hooks / variables / ...）
  - 真正的执行动作全部下沉到 TestStep（step_type=http_request 的 config JSON 里）

这个脚本把每条 v1 用例，按原样复制成**一条** TestStep(step_type='http_request')，
保证 v1 用例在 v2 runner 下的行为**与原来完全一致**。

⚠️ 重要约定
-----------
1. 这个脚本是**幂等**的：如果一条 case 已经有 steps，就跳过它，不会重复插入。
2. 脚本**不会删除**老字段（method/path/...），是为了万一出问题可以回滚回 v1 runner。
   老字段的最终清理由后续 schema 迁移（v3）负责，计划 v2 稳定运行 3 个月后执行。
3. 脚本**可以重复执行**：先跑 dry-run 看报告，确认无误再 `--commit`。

使用方法
--------
    # 默认 dry-run，打印会迁移几条、有哪些异常
    python -m src.database.migrations.data_migrations.v2_cases_to_steps

    # 真正执行（写库）
    python -m src.database.migrations.data_migrations.v2_cases_to_steps --commit

    # 限制数量用于分批灰度
    python -m src.database.migrations.data_migrations.v2_cases_to_steps --commit --limit 100

    # 只迁移某个项目
    python -m src.database.migrations.data_migrations.v2_cases_to_steps --commit --project-id 3

    # 切到另一个 DB section（默认 sqlite_local）
    python -m src.database.migrations.data_migrations.v2_cases_to_steps --commit --db-section postgres_local
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterable

# 把项目根塞进 sys.path（python -m 执行时已自动加，但直接 python 执行也兼容）
ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from src.database.db import DB
from src.database.models.module import Module
from src.database.models.test_case import TestCase
from src.database.models.test_step import TestStep, STEP_TYPE_HTTP_REQUEST

logger = logging.getLogger("v2_migration")


# ========================================================================
# 解析老字段的辅助函数
# ========================================================================
def _safe_json_loads(raw: Any) -> Any:
    """老库里 headers/params/extract_data/assertion 都是 TEXT，
    可能是 JSON 串、可能是空串、可能是 None，全部容错。"""
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        # 老数据有时是 Python 字面量（单引号），再兜一道
        try:
            import ast
            return ast.literal_eval(s)
        except (ValueError, SyntaxError):
            return s   # 都解析不了就保留原字符串，交给 Runner 去报错


def _build_http_config(case: TestCase) -> dict:
    """从老 case 字段里拼 step_type=http_request 的 config"""
    return {
        "method": (case.method or "GET").upper(),
        "path": case.path or "",
        "headers": _safe_json_loads(case.headers) or {},
        "data_type": case.data_type or "json",
        "params": _safe_json_loads(case.params) or {},
        "file_path": case.file_path or None,
        "sql_query": case.sql_query or None,   # 老表里 SQL 查询跟 API 混在一起
    }


def _build_extract_rules(case: TestCase) -> list[dict]:
    """把老的 extract_data（通常是 {var_name: jsonpath}）转成新的 [{name,from,jsonpath}] 列表"""
    raw = _safe_json_loads(case.extract_data)
    if not raw:
        return []
    rules: list[dict] = []
    if isinstance(raw, dict):
        # 老格式 A：{"token": "$.data.token", "uid": "$.data.user.id"}
        for var_name, expr in raw.items():
            rules.append({
                "name": str(var_name),
                "from": "response.body",
                "jsonpath": str(expr),
            })
    elif isinstance(raw, list):
        # 老格式 B：[{"name":"token","jsonpath":"$.data.token"}, ...]
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("key")
            expr = item.get("jsonpath") or item.get("path") or item.get("expr")
            if not name or not expr:
                continue
            rules.append({
                "name": str(name),
                "from": item.get("from") or "response.body",
                "jsonpath": str(expr),
            })
    return rules


def _build_assertions(case: TestCase) -> list[dict]:
    """老 assertion 通常是 {"status_code": 200, "contains": "ok", ...} 或 JSONPath 表达式，
    这里尽量把每一项都转成新的 [{type,target,expected}] 格式。
    转不动的走 type='raw'，Runner 会兜底按老逻辑判。"""
    raw = _safe_json_loads(case.assertion)
    if not raw:
        return []

    asserts: list[dict] = []

    if isinstance(raw, dict):
        for key, expected in raw.items():
            key_l = str(key).lower().strip()
            if key_l in ("status", "status_code", "code"):
                asserts.append({"type": "equal", "target": "status_code", "expected": expected})
            elif key_l in ("contains", "body_contains", "response_contains"):
                asserts.append({"type": "contains", "target": "body_text", "expected": expected})
            elif key_l.startswith("$") or "." in key_l:
                # JSONPath key
                asserts.append({"type": "jsonpath", "target": key, "expected": expected})
            else:
                # 兜底：把 key 当 jsonpath 试一下
                asserts.append({"type": "raw", "target": key, "expected": expected})
    elif isinstance(raw, list):
        # 老格式 B：已经是列表，逐条规整字段名
        for item in raw:
            if not isinstance(item, dict):
                continue
            asserts.append({
                "type": item.get("type") or "equal",
                "target": item.get("target") or item.get("key") or "",
                "expected": item.get("expected") if "expected" in item else item.get("value"),
            })
    else:
        # 字符串原样扔进去，让 Runner 做兼容
        asserts.append({"type": "raw", "target": "", "expected": raw})

    return asserts


# ========================================================================
# 主迁移逻辑
# ========================================================================
def iter_cases_to_migrate(
    session: Session,
    project_id: int | None,
    limit: int | None,
) -> Iterable[TestCase]:
    """筛选候选 case：
      - 有 method 字段（说明是老的 API 用例）
      - 且还没有任何 step（幂等）
    """
    q = session.query(TestCase).filter(TestCase.method.isnot(None))
    if project_id is not None:
        # TestCase -> Module -> project_id
        q = q.join(Module, Module.id == TestCase.module_id).filter(Module.project_id == project_id)
    if limit:
        q = q.limit(limit)

    for case in q.all():
        if case.steps:
            logger.debug("skip case#%s (already has %s steps)", case.id, len(case.steps))
            continue
        yield case


def migrate_case(session: Session, case: TestCase) -> TestStep:
    """把一条老 TestCase 转成一条 http_request TestStep。不 commit，调用方负责事务。"""
    step = TestStep(
        case_id=case.id,
        step_order=0,
        step_name=case.name or f"case#{case.id}",
        step_type=STEP_TYPE_HTTP_REQUEST,
        skip=bool(case.skip) if case.skip is not None else False,
        config=_build_http_config(case),
        extract=_build_extract_rules(case),
        assertion=_build_assertions(case),
        wait_before=case.wait_time or 0,
        timeout=60,
        retry=0,
        on_failure="stop",
    )
    session.add(step)

    # 顺便把 case_type 刷成 'api'（兼容早期 v2 升级脚本漏刷的情况）
    if not case.case_type:
        case.case_type = "api"

    return step


# ========================================================================
# CLI
# ========================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="把 v1 老用例的 API 字段拆成一条 http_request step"
    )
    parser.add_argument("--commit", action="store_true",
                        help="真的写库。默认是 dry-run，只打印会迁移几条。")
    parser.add_argument("--project-id", type=int, default=None,
                        help="只迁移某个 project_id 下的用例")
    parser.add_argument("--limit", type=int, default=None,
                        help="最多迁移 N 条（用于灰度 / 调试）")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="打印每条转换明细")
    parser.add_argument("--db-section", default=None,
                        help="config/object_conf.ini 里的 section 名，默认 sqlite_local")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    # 复用项目的 DB 工厂，避免再写一份连接字符串解析
    db_conf = None
    if args.db_section:
        from src.utils.read_conf import read_conf
        db_conf = read_conf.get_dict(args.db_section)
    db = DB(db_conf=db_conf)
    session: Session = db.session
    ok, err = 0, 0
    errors: list[tuple[int, str]] = []

    try:
        for case in iter_cases_to_migrate(session, args.project_id, args.limit):
            try:
                step = migrate_case(session, case)
                ok += 1
                logger.debug(
                    "case#%s -> step(type=%s, method=%s, path=%s)",
                    case.id, step.step_type,
                    step.config.get("method"), step.config.get("path"),
                )
            except Exception as exc:   # noqa: BLE001
                err += 1
                errors.append((case.id, str(exc)))
                logger.exception("case#%s 转换失败: %s", case.id, exc)

        if args.commit:
            session.commit()
            logger.info("✅ 已提交：成功 %s 条，失败 %s 条", ok, err)
        else:
            session.rollback()
            logger.info("🧪 dry-run：会迁移 %s 条，失败 %s 条（未写库，加 --commit 才真的写）",
                        ok, err)

        if errors:
            logger.warning("----- 失败列表 -----")
            for cid, msg in errors:
                logger.warning("  case#%s: %s", cid, msg)

        return 0 if err == 0 else 2

    except Exception:  # noqa: BLE001
        session.rollback()
        logger.exception("整体迁移被中断，已回滚")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
