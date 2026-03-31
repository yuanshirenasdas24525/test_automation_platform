# -*- coding:utf-8 -*-
import json
import yaml
import pandas as pd
from pathlib import Path
import configparser
from typing import List, Dict, Any, Optional
from src.utils.logger import LOGGER, ERROR_LOGGER
from config.settings import ProjectPaths
from src.utils.sql_handler import SQLHandlerFactory

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
                ERROR_LOGGER.error(f"读取 JSON 文件时出错 {file}: {e}")
        return data
    except Exception as e:
        ERROR_LOGGER.error(f"错误处理路径 {relative_path}: {e}")
        return None


class ReadConf:
    def __init__(self, file_path):
        self.config = configparser.ConfigParser()
        # 保持原始大小写
        self.config.optionxform = lambda option: option
        with open(file_path, 'r', encoding='utf-8') as fp:
            self.config.read_file(fp)

    def get_dict(self, section):
        return dict(self.config.items(section))

    def get_list(self, section, key):
        return self.config.get(section, key).split(",")

read_conf = ReadConf(ProjectPaths.OBJ_CONFIG)


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
        df = pd.read_excel(self.file_path, header=None, dtype=str)  # 一行一个 list
        df = df.replace('\n', '', regex=True).replace(pd.NA, None)
        for idx, row in enumerate(df.itertuples(index=False, name=None)):
            if idx == 0:  # 跳过第一行表头
                continue
            row_list = list(row)
            if self.row_processor:
                processed = self.row_processor(row_list, idx)
                if processed is None:
                    continue
                yield processed
            else:
                yield row_list

    def _read_csv(self):
        df = pd.read_csv(self.file_path, header=None, dtype=str)
        df = df.replace('\n', '', regex=True).replace(pd.NA, None)
        for idx, row in enumerate(df.itertuples(index=False, name=None)):
            if idx == 0:  # 跳过第一行表头
                continue
            row_list = list(row)
            if self.row_processor:
                processed = self.row_processor(row_list, idx)
                if processed is None:
                    continue
                yield processed
            else:
                yield row_list


# =========================================================
# 行处理器实现（针对 Excel/CSV 的一行 list）
# =========================================================
def process_api_row(row_list: List[Any], idx: int) -> Optional[List[Any]]:
    """处理 API 用例行（row_list 是一个 list）"""
    try:
        # 第5列是 skip
        skip_val = str(row_list[4]).strip().upper() if len(row_list) > 4 else ""
        if skip_val == "Y":
            return None

        # 第10列是 data
        if len(row_list) > 9:
            data_field = row_list[9]
            if data_field and not is_json(data_field) and is_path(data_field):
                json_data = process_json_files_in_path(data_field)
                if json_data:
                    # 这里可对 json_data 做特殊处理
                    pass

        return row_list
    except Exception as e:
        ERROR_LOGGER.error(f"处理第 {idx} 行 API 用例出错: {e}")
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
        ERROR_LOGGER.error(f"处理第 {idx} 行 UI 用例出错: {e}")
        return {}


def get_cases_from_db(params: Dict[str, Any], con_sqlite: Dict[str, Any]):
    """
    查询用例并将 ID 转换为对应的项目名称和模块名称返回
    """
    db = SQLHandlerFactory.create(con_sqlite)

    project_id = params.get("project")
    module_id = params.get("module")
    case_id = params.get("case")

    if not project_id:
        raise ValueError("错误：必须提供项目 ID ('project')。")

    # 1. 核心 SQL 改造：使用 JOIN 获取名称
    # p.name 为项目名，m.name 为模块名
    sql_base = """
               SELECT p.name as project_name, \
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
    rows = db.execute_query(sql_base, query_params)

    # 3. 格式改造：生成列表
    # 字段顺序严格对应你要求的：[项目名, 模块名, 用例名, 描述, skip, ...]
    fields_order = [
        "project_name", "module_name", "name", "description", "skip",
        "method", "path", "headers", "data_type", "params",
        "file_path", "extract_data", "sql_query", "assertion", "wait_time"
    ]

    result_list = []
    for row in rows:
        # row 已经是字典，直接按 key 取出值
        case_row = [row.get(f) for f in fields_order]
        result_list.append(case_row)

    return result_list

# =========================================================
# 使用示例
# =========================================================
if __name__ == "__main__":
    con_sqlite = read_conf.get_dict("sqlite_local")
    print(con_sqlite)

    # reader = GenericCaseReader(ProjectPaths.ui_register_case, process_ui_row).read()
    # for i in reader:
    #     print(i)
    params = {"project":17,"module":19,"case":67}
    try:
        final_data = get_cases_from_db(params, con_sqlite)
        print(f"\n最终返回列表总数: {len(final_data)}")
        for i in final_data:
            print(i)
    except Exception as e:
        print(f"发生错误: {e}")