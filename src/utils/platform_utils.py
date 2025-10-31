# -*- coding:utf-8 -*-
# Create on
import shutil
import time
import re
import os
import json
import subprocess
from jsonpath import jsonpath
from typing import NamedTuple, Any, Dict, List, Union
from src.utils.logger import LOGGER, ERROR_LOGGER



class CommandResult(NamedTuple):
    """命令执行结果"""
    success: bool
    stdout: str
    stderr: str
    returncode: int


def check_string_format(s: str) -> bool:
    """
    检查字符串是否为有效的 IP:端口 格式

    Args:
        s: 要检查的字符串

    Returns:
        bool: 如果是有效的 IP:端口 格式返回 True，否则返回 False
    """
    if not s or not isinstance(s, str):
        return False

    # 使用正则表达式进行更精确的匹配
    pattern = r'^(\d{1,3}\.){3}\d{1,3}:\d{1,5}$'
    if not re.match(pattern, s):
        return False

    try:
        ip_part, port_part = s.split(':', 1)
        ip_parts = ip_part.split('.')
        port = int(port_part)

        # 检查IP地址各部分是否在有效范围内
        ip_valid = all(0 <= int(part) <= 255 for part in ip_parts)
        # 检查端口是否在有效范围内
        port_valid = 1 <= port <= 65535

        return ip_valid and port_valid

    except (ValueError, AttributeError):
        return False


def run_command_safely(command, shell=False, capture_output=True, text=True, timeout=None) -> CommandResult:
    """
    安全运行命令，返回执行结果

    Args:
        command: 命令字符串或命令列表
        shell: 是否使用shell执行
        capture_output: 是否捕获输出
        text: 是否以文本模式返回
        timeout: 命令执行超时时间（秒）

    Returns:
        CommandResult: 包含执行结果的对象
    """
    try:
        # 执行命令
        result = subprocess.run(
            command,
            shell=shell,
            capture_output=capture_output,
            text=text,
            timeout=timeout,
            check=False  # 不自动抛出异常，我们自己处理
        )

        # 返回标准化的结果
        return CommandResult(
            success=result.returncode == 0,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            returncode=result.returncode
        )

    except subprocess.TimeoutExpired as e:
        ERROR_LOGGER.error(f"命令执行超时 ({timeout}秒): {command}")
        return CommandResult(
            success=False,
            stdout=e.stdout.decode('utf-8') if e.stdout else "",
            stderr=e.stderr.decode('utf-8') if e.stderr else f"命令执行超时: {e}",
            returncode=-1
        )
    except Exception as e:
        ERROR_LOGGER.error(f"执行命令时发生错误: {e}, 命令: {command}")
        return CommandResult(
            success=False,
            stdout="",
            stderr=str(e),
            returncode=-1
        )


def create_directory(path):
    """创建目录，如果不存在的话"""
    if not os.path.isdir(path):
        os.makedirs(path)


def execution_time_decorator(func):
    def wrapper(*args, **kwargs):
        LOGGER.info(f'运行{func.__name__}，args：{args}；kwargs: {kwargs}')
        start_time = time.perf_counter()
        try:
            result = func(*args, **kwargs)
            end_time = time.perf_counter()
            execution_time = end_time - start_time
            LOGGER.error(f"函数,{func.__name__}耗时：{execution_time},秒；"
                         f"返回结果：{result}")
            return result
        except Exception as e:
            ERROR_LOGGER.error(f"{func.__name__}: {e}")
            raise

    return wrapper


def delete_old_directories(directory_path):
    """
    删除指定路径下除了 10 个最新的子目录之外的所有子目录。
    :param directory_path: 要处理的目录路径
    """
    if os.path.isdir(directory_path):
        subdirectories = [os.path.join(directory_path, filename)
                          for filename in os.listdir(directory_path)
                          if os.path.isdir(os.path.join(directory_path, filename))]
        subdirectories.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        directories_to_delete = subdirectories[10:]
        for directory_to_delete in directories_to_delete:
            try:
                shutil.rmtree(directory_to_delete)
            except Exception as e:
                ERROR_LOGGER.error(f'无法删除 {directory_to_delete}。原因: {e}')
    else:
        ERROR_LOGGER.error(f'指定的路径 {directory_path} 不存在或不是一个目录。')


def clear_log_files(log_dir):
    """
    清空指定文件夹中所有后缀为 .log 的日志文件内容。
    Parameters:
    log_dir (str): 日志文件所在的文件夹路径。
    """
    # 检查文件夹是否存在
    if not os.path.isdir(log_dir):
        ERROR_LOGGER.error(f"Error: {log_dir} is not a valid directory.")
        return
    # 遍历文件夹中的所有文件
    files_found = False  # 标记是否找到符合条件的文件
    for filename in os.listdir(log_dir):
        if filename.endswith('.log'):
            log_file = os.path.join(log_dir, filename)
            with open(log_file, 'w') as file:
                file.truncate(0)
            files_found = True

    # 如果没有找到符合条件的文件
    if not files_found:
        ERROR_LOGGER.error(f"No '.log' files found in {log_dir}.")


def delete_error_png_files(folder_path):
    """
    删除指定文件夹下所有后缀为 "error.png" 的文件。
    Parameters:
    folder_path (str): 目标文件夹路径。
    """
    # 检查文件夹是否存在
    if not os.path.isdir(folder_path):
        ERROR_LOGGER.error(f"Error: {folder_path} is not a valid directory.")
        return

    # 遍历文件夹中的所有文件
    files_deleted = False  # 标记是否删除了文件
    for filename in os.listdir(folder_path):
        if filename.endswith('error.png') or filename.endswith('fail.png'):
            file_path = os.path.join(folder_path, filename)
            os.remove(file_path)
            LOGGER.info(f"Deleted: {file_path}")
            files_deleted = True

    # 如果没有找到符合条件的文件
    if not files_deleted:
        ERROR_LOGGER.error(f"No 'error.png' files found in {folder_path}.")


def clear_directory(directory: str):
    """
    删除指定目录下的所有内容（文件和子文件夹），但保留目录本身。
    :param directory: 需要清理的目录路径
    """
    if not os.path.exists(directory):
        ERROR_LOGGER.info(f"目录不存在: {directory}")
        return

    if not os.path.isdir(directory):
        ERROR_LOGGER.info(f"不是目录: {directory}")
        return

    for filename in os.listdir(directory):
        file_path = os.path.join(directory, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.remove(file_path)  # 删除文件或符号链接
                LOGGER.info(f"已删除文件: {file_path}")
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)  # 删除整个子目录
                LOGGER.info(f"已删除目录: {file_path}")
        except Exception as e:
            ERROR_LOGGER.error(f"删除失败 {file_path}: {e}")

def rep_expr(text: str, extra_pool: Dict[str, Any]) -> str:
    """
    替换文本中的表达式变量，格式为${var}，从extra_pool中取值替换。
    """
    if not isinstance(text, str):
        ERROR_LOGGER.error(f"rep_expr需要一个字符串输入，得到 {type(text)}")
        return text

    pattern = re.compile(r'\$\{(.*?)}')
    def replacer(match):
        key = match.group(1)
        value = extra_pool.get(key, match.group(0))
        return str(value)
    try:
        result = pattern.sub(replacer, text)
        LOGGER.debug(f"rep_expr 输入: {text}, 输出: {result}")
        return result
    except Exception as e:
        ERROR_LOGGER.error(f"rep_expr错误: {e}")
        return text

def extractor(json_obj: Union[Dict, List], json_path: str) -> Any:
    """
    使用jsonpath表达式从json对象中提取数据。
    """
    try:
        matches = jsonpath(json_obj, json_path)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        return matches
    except Exception as e:
        ERROR_LOGGER.error(f"提取器错误: {e}| json_path: {json_path}")
        return json_path

def extract_code(text: str, pattern: str) -> Union[str, None]:
    """
    从文本中使用正则表达式提取验证码。
    """
    try:
        match = re.search(pattern, text)
        if match:
            code = match.group(1)
            LOGGER.debug(f"extract_code 模式: {pattern}, code: {code}")
            return code
        return None
    except Exception as e:
        ERROR_LOGGER.error(f"extract_code 错误: {e}")
        return None

def convert_json(data: str) -> Any:
    """
    将字符串转换为json对象，支持dict和list。
    """
    if not isinstance(data, str):
        ERROR_LOGGER.error(f"convert_json 需要一个字符串输入，得到 {type(data)}")
        return data
    try:
        obj = json.loads(data)
        LOGGER.debug(f"convert_json 输入: {data}, 输出: {obj}")
        return obj
    except Exception as e:
        ERROR_LOGGER.error(f"convert_json 错误: {e}")
        return data
