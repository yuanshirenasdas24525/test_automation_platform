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


def get_cases_from_db(params: Dict[str, Any], db):
    """
    查询用例并将 ID 转换为对应的项目名称和模块名称返回
    """

    project_id = params.get("project")
    module_id = params.get("module")
    case_id = params.get("case")

    if not project_id:
        raise ValueError("错误：必须提供项目 ID ('project')。")

    # 1. 核心 SQL 改造：使用 JOIN 获取名称
    # p.name 为项目名，m.name 为模块名
    sql_base = """
               SELECT p.name as project_name, \
                      t.id, \
                      m.name as module_name, \
                      t.name, \
                      t.description, \
                      t.skip, \
                      t.method, \
                      t.path, \
                      t.headers, \
                      t.data_type, \
                      t.params, \
                      t.file_path, \
                      t.extract_data, \
                      t.sql_query, \
                      t.assertion, \
                      t.wait_time
               FROM test_cases t
                        JOIN modules m ON t.module_id = m.id
                        JOIN projects p ON m.project_id = p.id
               WHERE p.id = :project_id \
               """

    query_params = {"project_id": project_id}

    # 2. 动态添加过滤条件 (ID 优先级：Case > Module > Project)
    if case_id:
        sql_base += " AND t.id = :case_id"
        query_params["case_id"] = case_id
    elif module_id:
        sql_base += " AND m.id = :module_id"
        query_params["module_id"] = module_id

    # 排序
    sql_base += " ORDER BY t.sort_order ASC"

    # 执行查询
    rows = db.query(sql_base, query_params)

    return [row for row in rows if not row.get("skip")]


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

