from pathlib import Path
import os
from config.settings import ProjectPaths
from src.utils.logger import LOGGER, ERROR_LOGGER

def ensure_report_dirs():
    """
    创建缓存/报告目录：{CACHE_FILE}/{NOW}/(report_data|report)
    """
    base = Path(ProjectPaths.CACHE_FILE) / ProjectPaths.NOW
    report_data = base / "report_data"
    report_dir = base / "report"
    report_data.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_data, report_dir


# 新增：根据 alluredir 路径校验/创建报告目录，失败则退回默认 ensure_report_dirs
def resolve_report_dirs(alluredir: str = None):
    """
    根据传入的 alluredir 路径，校验/创建报告目录；
    如果路径无效则退回默认 ensure_report_dirs()
    """
    if alluredir:
        if os.path.exists(alluredir):
            if not os.path.isdir(alluredir):
                LOGGER.warning(f"[WARN] --alluredir 指定的路径存在但不是目录: {alluredir}, 使用默认目录")
                return ensure_report_dirs()
            else:
                report_data_dir = alluredir
                report_dir = os.path.join(os.path.dirname(os.path.dirname(alluredir)), "report")
                return report_data_dir, report_dir
        else:
            try:
                os.makedirs(alluredir, exist_ok=True)
                report_data_dir = alluredir
                report_dir = os.path.join(os.path.dirname(os.path.dirname(alluredir)), "report")
                return report_data_dir, report_dir
            except Exception as e:
                ERROR_LOGGER.error(f"[ERROR] 无法创建 --alluredir 指定的目录 {alluredir}: {e}, 使用默认目录")
                return ensure_report_dirs()
    else:
        return ensure_report_dirs()