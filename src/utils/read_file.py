# -*- coding:utf-8 -*-
import json
import yaml
import pandas as pd
from pathlib import Path
import configparser
from typing import List, Dict, Any, Optional
from src.utils.logger import LOGGER, ERROR_LOGGER
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

# =========================================================
# 使用示例
# =========================================================
if __name__ == "__main__":

    reader = GenericCaseReader(ProjectPaths.ui_register_case, process_ui_row).read()
    for i in reader:
        print(i)