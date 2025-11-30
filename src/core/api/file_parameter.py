# coding: utf-8
import os
import mimetypes
from config.settings import ProjectPaths
from src.utils.platform_utils import rep_expr
from src.utils.logger import LOGGER, ERROR_LOGGER

class FileParameter:
    """
    文件处理类，支持单文件、多文件、多类型 MIME 自动识别，
    返回 requests 可用的 files 结构。

    支持输入：
        - "path/to/file.png"
        - ["1.png", "2.xlsx"]
        - [{"key": "file1", "path": "a.png"}, {"key": "file2", "path": "b.jpg"}]

    返回格式：
        {
            "file": ("xxx.png", open(..., "rb"), "image/png"),
            "file2": ("b.jpg", open(..., "rb"), "image/jpeg")
        }
    """

    def __init__(self, root_dir=None, extra_pool=None):
        """
        root_dir: 图片文件根目录，可用于统一管理文件路径
        extra_pool: 额外参数池，用于替换路径中的动态参数
        """
        self.root_dir = root_dir or ProjectPaths.IMG_FILE
        self.extra_pool = extra_pool or {}

    # ---------------------------------------------------
    # 1. 路径处理
    # ---------------------------------------------------
    def resolve_path(self, path: str) -> str:
        path = rep_expr(path, self.extra_pool) if isinstance(path, str) else path or {}
        """自动处理相对路径/绝对路径"""
        if not path:
            ERROR_LOGGER.error("文件路径为空")

        # 已经是绝对路径
        if os.path.isabs(path):
            full_path = path
        else:
            # 相对路径：补全 root_dir
            full_path = os.path.join(self.root_dir, path)

        if not os.path.exists(full_path):
            ERROR_LOGGER.error(f"文件未找到: {full_path}")

        return full_path

    # ---------------------------------------------------
    # 2. 单文件处理
    # ---------------------------------------------------
    def process_single_file(self, path: str, key: str = "file"):
        file_path = self.resolve_path(path)
        LOGGER.info(f"处理单文件: {file_path}")

        # 自动识别 MIME 类型
        mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        file_name = os.path.basename(file_path)

        return key, (file_name, open(file_path, "rb"), mime_type)

    # ---------------------------------------------------
    # 3. 多文件处理
    # ---------------------------------------------------
    def process_file_list(self, file_list):
        """
        支持格式：
        1. ["a.png", "b.xlsx"]
        2. [{"key": "file1", "path": "a.png"}]
        3. "a.png"
        """
        files = {}

        if isinstance(file_list, str):
            key, file_value = self.process_single_file(file_list)
            files[key] = file_value
            return files

        if isinstance(file_list, list):
            for item in file_list:

                # case1: ["a.png", "b.xlsx"]
                if isinstance(item, str):
                    key, file_value = self.process_single_file(item)
                    files[key] = file_value

                # case2: [{"key": "file1", "path": "abc.png"}]
                elif isinstance(item, dict):
                    key = item.get("key", "file")
                    path = item.get("path")
                    key, file_value = self.process_single_file(path, key)
                    files[key] = file_value

                else:
                    ERROR_LOGGER.error(f"文件格式不合法: {item}")

            return files

        raise ValueError(f"file_list 必须是字符串或列表，但收到: {type(file_list)}")

    # ---------------------------------------------------
    # 4. 主入口
    # ---------------------------------------------------
    def get_files(self, file_list):
        """
        对外统一入口：
        file_list = None  → 返回 None
        file_list = "a.png" → 返回 {"file": (...)}
        file_list = ["a.png", "b.xlsx"] → 支持多个文件
        file_list = [{"key": "file1", "path": "a.png"}] → 自定义 key
        """
        file_list = [list_path.strip() for list_path in file_list.split(";")]
        if not file_list:
            ERROR_LOGGER.info(f"文件列表为空: {file_list}")
            return None

        return self.process_file_list(file_list)