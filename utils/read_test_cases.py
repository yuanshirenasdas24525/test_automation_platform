# -*- coding:utf-8 -*-
import json
import yaml
import numpy as np
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any, Optional

from utils.logger import LOGGER
from config.settings import ProjectPaths

PROJECT = Path(ProjectPaths.BASE_DIR)


# =========================================================
# 公共工具函数
# =========================================================
def is_json(text):
    """判断字符串是否有效 JSON"""
    try:
        json.loads(text)
        return True
    except (json.JSONDecodeError, TypeError):
        return False


def is_path(text):
    """判断字符串是否是路径"""
    absolute_path = PROJECT / text
    absolute_path = absolute_path.resolve()
    try:
        return Path(absolute_path).exists()
    except Exception as e:
        return False


def process_json_files_in_path(relative_path):
    """处理指定路径下的 JSON 文件并合并数据"""
    try:
        absolute_path = PROJECT / relative_path
        absolute_path = absolute_path.resolve()

        if not absolute_path.exists() or not absolute_path.is_dir():
            return None

        files = list(absolute_path.glob("*.json"))
        if not files:
            return None

        data = []
        for file in files:
            try:
                with file.open('r', encoding='utf-8') as f:
                    data.extend(json.load(f))
            except Exception as e:
                LOGGER.error(f"读取 JSON 文件时出错 {file}: {e}")
        return data
    except Exception as e:
        LOGGER.error(f"错误处理路径 {relative_path}: {e}")
        return None


# =========================================================
# 通用读取类（支持逐行 yield）
# =========================================================
class GenericCaseReader:
    def __init__(self, file_path, row_processor=None):
        """
        :param file_path: 用例文件路径
        :param row_processor: 行处理函数，可选；接收参数 (row_list: list, row_index: int)
        """
        self.file_path = Path(file_path).resolve()
        self.row_processor = row_processor
        if not self.file_path.exists():
            raise FileNotFoundError(f"文件不存在: {self.file_path}")

    def read(self):
        """按文件类型读取，并逐行 yield"""
        suffix = self.file_path.suffix.lower()
        if suffix in (".yaml", ".yml"):
            yield from self._read_yaml()
        elif suffix == ".json":
            yield from self._read_json()
        elif suffix in (".xls", ".xlsx"):
            yield from self._read_excel()
        elif suffix == ".csv":
            yield from self._read_csv()
        else:
            raise ValueError(f"不支持的文件格式: {suffix}")

    def _read_yaml(self):
        with open(self.file_path, 'r', encoding='utf-8') as f:
            yield yaml.safe_load(f)

    def _read_json(self):
        with open(self.file_path, 'r', encoding='utf-8') as f:
            yield json.load(f)

    def _read_excel(self):
        df = pd.read_excel(self.file_path, dtype=str)
        df = df.replace('\n', '', regex=True).replace({np.nan: None, pd.NA: None})
        df = df.infer_objects(copy=False)
        for idx, row_dict in enumerate(df.to_dict('records'), start=2):
            if self.row_processor:
                processed = self.row_processor(row_dict, idx)
                if processed is None:
                    continue
                yield processed
            else:
                yield row_dict

    def _read_csv(self):
        df = pd.read_csv(self.file_path, header=None, dtype=str)
        df = df.replace('\n', '', regex=True).replace({np.nan: None, pd.NA: None})
        df = df.infer_objects(copy=False)
        for idx, row_dict in enumerate(df.to_dict('records'), start=2):
            if self.row_processor:
                processed = self.row_processor(row_dict, idx)
                if processed is None:
                    continue
                yield processed
            else:
                yield row_dict


# =========================================================
# 行处理器实现（针对 Excel/CSV 的一行 list）
# =========================================================
def process_api_row(row_dict: Dict[str, Any], idx: int) -> Optional[Dict[str, Any]]:
    try:
        # 第5列是 skip
        if row_dict.get("skip"):
            return None

        # 第10列是 data

        data_field = row_dict.get("data")
        if data_field and not is_json(data_field) and is_path(data_field):
            json_data = process_json_files_in_path(data_field)
            if json_data:
                # 这里可对 json_data 做特殊处理
                pass

        return row_dict
    except Exception as e:
        LOGGER.error(f"处理第 {idx} 行 API 用例出错: {e}")
        return None


def process_ui_row(row_list: List[Any], idx: int) -> Dict[str, Any]:
    """处理 UI 用例行（row_list 是一个 list）"""
    try:
        # 第5列是 skip
        skip_val = str(row_list[4]).strip().upper() if len(row_list) > 4 else ""
        if skip_val == "Y":
            return {}

        keys = [
            "case_module", "case_submodule", "case_name", "case_title", "skip", "by",
            "locator", "action", "value", "deposit", "retrieve", "expected",
            "sliding_location", "wait"
        ]

        return dict(zip(keys, row_list))
    except Exception as e:
        LOGGER.error(f"处理第 {idx} 行 UI 用例出错: {e}")
        return {}


def _case_types_for_category(category: Optional[str]) -> Optional[List[str]]:
    """
    把"运行 X 全部用例"的 category 解析成实际要跑的 case_type 集合。

    规则（必须和前端 ProjectDetailPage 的 caseTypesFor() 对齐 —— 用户在 X Tab 里看到
    什么，"运行 X 全部" 就跑什么）：
        - "api"        → ["api", "mixed"]
        - "web"        → ["web", "mixed"]
        - "android"    → ["android", "mixed"]
        - "ios"        → ["ios", "mixed"]
        - "functional" → 上层 runs.py 已经拦掉，不应该走到这里
        - 空 / 不识别  → None（不过滤，老行为兜底）

    mixed 用例会在它涉及的每个栈 Tab 里都出现一次，所以也会被相应栈的"运行全部"
    选中。这意味着同一条 mixed 用例，如果用户先点"运行 API 全部"再点"运行 Web 全部"
    会被执行两次 —— 这是有意的（与 Tab 显示一致），用户需要的话可以先把 mixed
    用例 skip 掉再跑。
    """
    if not category:
        return None
    cat = str(category).strip().lower()
    if cat in ("api", "web", "android", "ios"):
        return [cat, "mixed"]
    return None


# v1 raw-SQL loader `get_cases_from_db` 已删 —— v2 唯一路径走
# get_cases_v2_from_db（带 steps / environment / case_type 过滤）。


# =========================================================
# v2：查询带 steps / environment 的完整用例字典
# =========================================================
def get_cases_v2_from_db(params: Dict[str, Any], db) -> List[Dict[str, Any]]:
    """查询 v2 形态的 case。返回每条 case 都是一个**自包含的字典**，
    里面嵌套了 steps、environment，可以直接 JSON 序列化后扔给 Celery / pytest。

    目的：避免 Celery 端还要再连数据库拉 steps，降低耦合、方便进程隔离。

    返回结构：
        [
            {
                "id": 1, "name": "...", "case_type": "api",
                "project_name": "...", "module_name": "...",
                "skip": False,
                "timeout": 60, "retry": 0,
                "variables": {...}, "pre_hook": [...], "post_hook": [...],
                "environment": {"id":3,"name":"dev","host":"...", "variables":{...}},
                "steps": [
                    {"id":101, "step_order":0, "step_type":"http_request",
                     "config": {...}, "extract":[...], "assertion":[...], ...},
                    ...
                ],
                # v1 兼容字段（没迁的老用例仍然保留，以便 CaseExecutor 合成 step）
                "method": "POST", "path": "/login", ...
            },
            ...
        ]
    """
    from sqlalchemy.orm import selectinload, joinedload
    from database.models.test_case import TestCase
    from database.models.module import Module
    from database.models.project import Project

    project_id = params.get("project")
    module_id = params.get("module")
    case_id = params.get("case")
    category = params.get("category")
    if not project_id:
        raise ValueError("错误：必须提供项目 ID ('project')。")

    # 我们 piggy-back 老的 db.sql 只能跑 raw SQL；这里用 ORM session
    session = getattr(db, "session", None) or db  # 兼容 SQLHandler / Session 两种入参

    q = (
        session.query(TestCase)
        .join(Module, Module.id == TestCase.module_id)
        .join(Project, Project.id == Module.project_id)
        .options(
            selectinload(TestCase.steps),
            joinedload(TestCase.environment),
        )
        .filter(Project.id == project_id)
    )
    if case_id:
        q = q.filter(TestCase.id == case_id)
    elif module_id:
        q = q.filter(Module.id == module_id)

    # case_type 过滤：用户在 X 栈点"运行全部"，只跑该栈 + mixed 的用例。
    # 没传 category 或非自动化栈 → 不过滤。
    # 用 case_id 单跑某条用例时也跳过 category 过滤（用户已明确指定）。
    # v1 兼容（NULL case_type 视作 api）已删，所有 case 必须带 case_type。
    types = _case_types_for_category(category)
    if types and not case_id:
        q = q.filter(TestCase.case_type.in_(types))

    q = q.order_by(TestCase.sort_order.asc())

    # 项目名 / 模块名用一次性子查询拿到，避免 N+1
    proj = session.query(Project).get(project_id)
    proj_name = proj.name if proj else None

    cases = q.all()
    result: List[Dict[str, Any]] = []
    for c in cases:
        if c.skip:
            continue
        steps = [s.to_dict() for s in sorted(
            (c.steps or []), key=lambda s: s.step_order or 0
        )]
        env = None
        if c.environment is not None:
            env = {
                "id": c.environment.id,
                "name": c.environment.name,
                "category": c.environment.category,
                "host": c.environment.host,
                # App 自动化：acquire_session_for_case 要用这个选设备池
                "device_pool": c.environment.device_pool,
                # Web / App 运行时 caps：headless / slow_mo / capabilities 等
                "browser_config": c.environment.browser_config,
                "variables": c.environment.variables,
                "secrets": None,   # secrets 不下发给 worker，以避免日志/序列化泄漏
            }
        result.append({
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "case_type": c.case_type or "api",
            "project_name": proj_name,
            "module_name": c.module.name if c.module else None,
            "skip": bool(c.skip),
            "tags": c.tags,
            "priority": c.priority,
            "timeout": c.timeout,
            "retry": c.retry,
            "variables": c.variables,
            "pre_hook": c.pre_hook,
            "post_hook": c.post_hook,
            "environment": env,
            "steps": steps,
            # v1 兼容字段 —— 没有 steps 的老用例靠这些字段被 CaseExecutor 合成 http_request
            "method": c.method,
            "path": c.path,
            "headers": c.headers,
            "data_type": c.data_type,
            "params": c.params,
            "file_path": c.file_path,
            "extract_data": c.extract_data,
            "sql_query": c.sql_query,
            "assertion": c.assertion,
            "wait_time": c.wait_time,
        })
    return result

