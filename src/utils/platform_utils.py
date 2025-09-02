# -*- coding:utf-8 -*-
# Create on
import os
import shutil
import time
from src.utils.logger import LOGGER, ERROR_LOGGER


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


def delete_all_files_in_dir(directory: str):
    """
    删除指定目录下的所有文件，但保留子目录和目录本身。
    :param directory: 需要清理的目录路径
    """
    if not os.path.exists(directory):
        ERROR_LOGGER.error(f"目录不存在: {directory}")
        return

    if not os.path.isdir(directory):
        ERROR_LOGGER.error(f"不是目录: {directory}")
        return

    for filename in os.listdir(directory):
        file_path = os.path.join(directory, filename)
        if os.path.isfile(file_path):
            try:
                os.remove(file_path)
                LOGGER.info(f"已删除文件: {file_path}")
            except Exception as e:
                ERROR_LOGGER.info(f"删除失败 {file_path}: {e}")